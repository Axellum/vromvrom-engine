"""
Module : core.budget_guard.py
Version : V12 (Antigravity Engine)
Description : Gardien de budget et de quotas pour les agents du tab5-engine.
              Gère le basculement dynamique (cascade) entre les différents providers LLM
              en fonction de la disponibilité locale, des quotas gratuits et du budget quotidien.
              Optimisé pour Windows, ultra-fiable et thread-safe via db_lock_context.
"""

import asyncio
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp

from core.backlog_db import db_read_lock_context, db_write_lock_context

# Imports requis du tab5-engine
from core.runtime_db import get_connection, get_db_path

# Configuration du logging
logger = logging.getLogger("Antigravity.BudgetGuard")

# Cache module-level (PAS un attribut d'instance) : BudgetGuard() est
# ré-instancié à chaque appel un peu partout (dreamer_agent.py, tests...),
# donc un cache par instance ne servirait à rien. Évite de rappeler l'API
# de solde DeepSeek à chaque décision de cascade (potentiellement plusieurs
# fois par seconde).
_deepseek_balance_cache: dict[str, Any] = {"value": None, "checked_at": 0.0}
_DEEPSEEK_BALANCE_CACHE_TTL = 600.0  # 10 min


async def _get_cached_deepseek_balance_usd() -> float | None:
    """Solde réel DeepSeek (core.provider_balances), rafraîchi au plus toutes les 10 min."""
    now = time.time()
    if now - _deepseek_balance_cache["checked_at"] < _DEEPSEEK_BALANCE_CACHE_TTL:
        return _deepseek_balance_cache["value"]
    from core.provider_balances import fetch_deepseek_balance_usd
    balance = await fetch_deepseek_balance_usd()
    _deepseek_balance_cache["value"] = balance
    _deepseek_balance_cache["checked_at"] = now
    return balance


# Cooldown module-level après échec d'un provider (ex: CLI Gemini/Claude non
# trustée dans l'environnement) — évite qu'une boucle de drainage (plusieurs
# tâches par cycle) retente en boucle un provider cassé sur chaque tâche du
# même cycle, ce qui brûlerait le temps du cycle sur des échecs répétés au
# lieu d'avancer sur le backlog via les providers suivants de la cascade.
_provider_cooldown: dict[str, float] = {}
_PROVIDER_COOLDOWN_SECONDS = 1800.0  # 30 min


# [#T395] Signatures d'un échec IMPUTABLE AU PROVIDER. Tout le reste — code qui
# ne compile pas, branche vide, contrat d'acceptation non tenu — est un échec de
# la TÂCHE : le provider a répondu, et le punir revient à se priver d'un
# fournisseur sain pour 30 minutes.
_SIGNATURES_PANNE_PROVIDER = (
    "timeout", "timed out", "read timeout", "connect",
    "connection", "unreachable", "network", "ssl",
    "429", "rate limit", "quota", "too many requests",
    "401", "403", "402", "payment required", "authentication", "unauthorized",
    "500", "502", "503", "504", "bad gateway", "service unavailable",
    "model_archived", "model not found", "no such model",
)


def echec_imputable_au_provider(erreur: str) -> bool:
    """
    [#T395] L'échec vient-il du fournisseur, ou du travail produit ?

    Mesuré le 18/08 : trois tâches échouent en 1,4 s sur des contrôles de
    QUALITÉ (« la branche est identique à master »), leurs trois providers
    partent en cooldown de 30 min, et le cycle s'arrête aussitôt sur « aucun
    provider libre » — quatre tâches jamais tentées. Les providers avaient
    pourtant parfaitement répondu.

    Par défaut on ne pénalise PAS : depuis la sonde de santé (#T387), un
    provider réellement mort est déjà écarté en tête de cycle. Le cooldown
    n'est plus la première ligne de défense, et un cooldown injustifié coûte
    bien plus cher qu'un provider re-essayé une fois de trop.
    """
    # `str(erreur)` explicite : l'appelant passe parfois l'exception elle-même et
    # non son message — `(erreur or "")` rendait alors une chaîne vide, donc
    # « pas une panne provider », et le cooldown ne se déclenchait jamais.
    bas = str(erreur).lower()
    return any(signature in bas for signature in _SIGNATURES_PANNE_PROVIDER)


# [#T398] Signatures d'une PANNE D'INFRASTRUCTURE : la tâche n'a atteint AUCUN
# modèle. Ce n'est ni la faute du provider élu, ni celle du travail demandé —
# c'est la maison qui est éteinte (clés absentes du .env, réseau coupé, tous
# les disjoncteurs ouverts).
# Ecrites SANS accent : la comparaison les retire des deux cotes (voir plus
# bas). Le piege est connu -- #T383 : des marqueurs ecrits sans accent ne
# matchaient aucune des formulations accentuees reellement produites.
_SIGNATURES_PANNE_INFRA = (
    "tous les providers",
    "circuits ouverts",
    "aucun provider",
    "aucun fournisseur",
    "no provider available",
    "aucun modele disponible",
)


def _sans_accents(texte: str) -> str:
    """Minuscules et accents retires, pour comparer des messages d'erreur."""
    import unicodedata
    # `str(texte)` explicite : l'appelant passe parfois l'exception elle-même.
    decompose = unicodedata.normalize("NFD", str(texte).lower())
    return "".join(c for c in decompose if not unicodedata.combining(c))


def panne_infrastructure(erreur: str) -> bool:
    """
    [#T398] La tâche a-t-elle échoué faute d'infrastructure, sans jamais
    atteindre un modèle ?

    Mesuré le 18/08 : le .env de production perd ses clés à 19h09, les quatre
    providers sains de 19h00 tombent tous, et cinq tâches du backlog passent en
    `failed` à 19h15 avec **0 token consommé** — punies pour une panne qui
    n'est pas la leur. Trois pannes de ce type suffisent à les faire passer en
    `abandoned` sans qu'un seul modèle les ait jamais lues.

    Symétrique de `echec_imputable_au_provider` (#T395) : celui-là protège le
    PROVIDER d'un échec de la tâche, celui-ci protège la TÂCHE d'un échec de
    l'infrastructure.
    """
    # `str(erreur)` explicite : l'appelant passe parfois l'exception elle-même et
    # non son message. `_sans_accents` met déjà en minuscules.
    bas = _sans_accents(str(erreur))
    return any(signature in bas for signature in _SIGNATURES_PANNE_INFRA)


def _fenetres_quota_gratuit() -> dict:
    """Metrique, fenetre et cle de plafond de chaque palier gratuit compte."""
    return {
        # Google plafonne les REQUETES par jour (#T404), pas les tokens.
        "gemini-free": ("requetes", 86400.0, "gemini_free_requests_per_day"),
        "deepseek-free": ("requetes", 86400.0, "deepseek_free_requests_per_day"),
    }


def mark_provider_failed(provider: str) -> None:
    """
    Place un provider en cooldown après un échec d'exécution (appelé par
    agents/dreamer_agent.py sur un pipeline_err). get_available_provider()
    ne le re-proposera pas avant _PROVIDER_COOLDOWN_SECONDS.
    """
    _provider_cooldown[provider] = time.time()
    logger.warning(f"[BudgetGuard] Provider '{provider}' mis en cooldown ({_PROVIDER_COOLDOWN_SECONDS/60:.0f} min) après échec.")


def provider_en_cooldown(provider: str) -> bool:
    """
    [#T403] Le cooldown, lisible par les APPELANTS.

    `mark_provider_failed` posait un cooldown que seule `get_available_provider`
    consultait. Or depuis le routage par historique (#T393), c'est
    `_choisir_provider_tache` qui elit le provider dans le cas nominal, et il ne
    passe par la cascade de cout qu'en secours : le cooldown etait donc
    contourne dans le chemin le plus frequent.

    Mesure le 18/08 a 22h05 en production : la tache #43 prend un 429 sur
    `gemini-free`, le provider part en cooldown -- et la tache #44 recoit
    `gemini-free` a la seconde suivante. Le cycle est mort sur « tous les
    providers ont echoue » avec quatre providers sains sous la main.
    """
    return _in_cooldown(provider)


def _in_cooldown(provider: str) -> bool:
    ts = _provider_cooldown.get(provider, 0.0)
    return (time.time() - ts) < _PROVIDER_COOLDOWN_SECONDS


# [#T248] Interrupteur d'hôte : les providers CLI (abonnements Antigravity/Claude)
# n'ont de sens que sur une machine où le binaire existe et est trusté (le PC).
# Sur le Deck, l'absence est structurelle — les proposer au round-robin ne peut
# qu'échouer et consumer des cooldowns. MOTEUR_DISABLE_CLI_PROVIDERS=1 dans le
# .env de l'hôte les exclut explicitement de la cascade.
def _cli_providers_disabled() -> bool:
    return os.getenv("MOTEUR_DISABLE_CLI_PROVIDERS", "").strip().lower() in ("1", "true", "yes", "on")


def _binary_present(names: tuple[str, ...], windows_appdata_candidates: tuple[str, ...] = ()) -> bool:
    """
    [#T248] Détection cross-plateforme d'un binaire CLI : chemins absolus
    Windows (AppData) seulement sous Windows, sinon recherche PATH générique.
    Les chemins AppData n'existent jamais sous Linux — les tester ailleurs
    qu'en Windows n'ajoute que du bruit.
    """
    import shutil
    if sys.platform == "win32" and any(os.path.exists(p) for p in windows_appdata_candidates):
        return True
    return any(shutil.which(name) for name in names)


# Index de rotation round-robin module-level (BudgetGuard() est réinstancié
# à chaque appel, cf. commentaire sur _deepseek_balance_cache) — fait tourner
# les API cloud gratuites disponibles plutôt que de toujours privilégier la
# même (retour Axel 07/07 : Ollama gagnait systématiquement, empêchant
# Gemini/Cerebras/Cohere/Mistral d'être jamais réellement exercés).
_free_api_rotation_index = 0


class BudgetGuard:
    """
    Gestionnaire de budget et de quotas pour les appels LLM.
    Assure la transition transparente entre les providers locaux (LM Studio),
    les paliers gratuits (Gemini, DeepSeek) et les paliers payants (Claude Haiku)
    tout en respectant strictement les limites financières définies.
    """

    def __init__(self) -> None:
        # [P1-2.4] Chemin dérivé de la racine du moteur (et non un chemin Windows
        # codé en dur `e:\...` qui n'existe pas sur le Deck en prod → la config
        # budget_guard n'était jamais chargée).
        self.config_path = Path(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
        )
        self.db_path = get_db_path()
        self.config = self._load_config()
        self._initialized = False

    def _load_config(self) -> dict[str, Any]:
        """
        Charge la configuration depuis le fichier config.json.
        Retourne des valeurs par défaut robustes en cas d'absence ou d'erreur.
        """
        defaults = {
            "gemini_free_tokens_per_hour": 1_000_000,
            # [#T404] Le plafond que Google applique VRAIMENT au palier gratuit.
            # Releve dans le corps du 403 rendu en production le 18/08 a 22h20 :
            #   "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
            #   "quotaValue": "20"
            # 20 REQUETES PAR JOUR ET PAR MODELE -- pas un million de tokens par
            # heure. Une seule tache DAG en consomme plusieurs dizaines : le
            # compteur en tokens/heure restait a zero pendant que le vrai quota
            # etait deja epuise, et la rotation continuait d'elire gemini-free.
            "gemini_free_requests_per_day": 20,
            "deepseek_free_requests_per_day": 200,
            "daily_budget_usd": 0.50,
            "auditor_weekly_budget_usd": 1.00,
            "total_daily_budget_usd": 1.00
        }

        if not self.config_path.exists():
            logger.warning(f"Fichier de configuration introuvable à {self.config_path}. Utilisation des valeurs par défaut.")
            return defaults

        try:
            with open(self.config_path, encoding="utf-8") as f:
                data = json.load(f)
                bg_config = data.get("budget_guard", {})

                return {
                    "gemini_free_tokens_per_hour": bg_config.get("gemini_free_tokens_per_hour", defaults["gemini_free_tokens_per_hour"]),
                    "gemini_free_requests_per_day": bg_config.get("gemini_free_requests_per_day", defaults["gemini_free_requests_per_day"]),
                    "deepseek_free_requests_per_day": bg_config.get("deepseek_free_requests_per_day", defaults["deepseek_free_requests_per_day"]),
                    "daily_budget_usd": bg_config.get("daily_budget_usd", defaults["daily_budget_usd"]),
                    "total_daily_budget_usd": bg_config.get("total_daily_budget_usd", defaults["total_daily_budget_usd"]),
                    "auditor_weekly_budget_usd": bg_config.get("auditor_weekly_budget_usd", defaults["auditor_weekly_budget_usd"])
                }
        except Exception as e:
            logger.error(f"Erreur lors de la lecture de la configuration : {e}. Utilisation des valeurs par défaut.")
            return defaults

    async def initialize(self) -> None:
        """
        Initialise le composant et effectue la migration de la base de données à chaud si nécessaire.
        Garantit la présence de toutes les colonnes requises pour la V12.
        """
        if self._initialized:
            return

        logger.info("Initialisation du BudgetGuard et vérification du schéma de la base de données...")

        # #T64 : le schéma de billing_history (y compris model/tokens_used/cost_usd/window_type)
        # et sa migration additive sont désormais centralisés dans runtime_db._init_schema.
        # get_connection() crée/migre le schéma automatiquement → plus de définition dupliquée ici.
        async with db_write_lock_context():
            def ensure_schema():
                conn = get_connection()
                conn.close()

            await asyncio.to_thread(ensure_schema)

        self._initialized = True
        logger.info("BudgetGuard initialisé avec succès.")

    async def _check_lmstudio_availability(self) -> bool:
        """
        Vérifie si l'instance locale de LM Studio est active et répond.
        """
        url = "http://localhost:1234/v1/models"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=1.0) as response:
                    return response.status == 200
        except Exception:
            return False

    async def _check_ollama_availability(self) -> bool:
        """
        Vérifie si le démon Ollama local est actif et répond.
        Endpoint natif `/api/tags` (cohérent avec ollama_local = 127.0.0.1:11434
        dans openai_compat_provider.py). Préféré à LM Studio car il tourne aussi
        sur le Steam Deck et héberge directement notre fine-tune domotique.
        """
        url = "http://127.0.0.1:11434/api/tags"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=1.0) as response:
                    return response.status == 200
        except Exception:
            return False

    def _check_gemini_cli_availability(self) -> bool:
        """
        Présence du binaire headless `agy` (Antigravity CLI, abo Google).

        L'ancien test visait `antigravity-ide.cmd` : c'est le lanceur d'IDE,
        pas une complétion. GeminiCLIProvider parle à `agy` (session Google,
        pas GEMINI_API_KEY).
        """
        from core.llm.providers.gemini import chemin_agy
        return chemin_agy() is not None

    def _check_claude_cli_availability(self) -> bool:
        """
        Vérifie la présence locale du binaire `claude` (utilisé par
        ClaudeCLIProvider, core/llm/providers/deepseek.py) — même limite que
        _check_gemini_cli_availability() : présence du binaire seulement, pas
        du dialogue de confiance du workspace.
        """
        return _binary_present(("claude", "claude.cmd"))

    async def quota_gratuit_epuise(self, provider: str) -> bool:
        """
        [#T409] Le quota d'un palier gratuit est-il deja crevé ?

        #T404 a corrige l'unite du plafond Gemini (20 requetes/jour, pas un
        million de tokens/heure) — mais uniquement DANS `get_available_provider`,
        c'est-a-dire dans la cascade de cout. Or depuis #T393 c'est
        `_choisir_provider_tache` (routage par historique) qui elit le provider
        dans le cas nominal, et la cascade n'est plus que le secours.

        Mesure le 19/08 a 22h18 : les deux taches du cycle recoivent
        `gemini-free`, quota du jour deja consomme. Exactement le meme oubli que
        #T403 pour le cooldown : un plafond pose sur un chemin que le flux
        n'emprunte plus.

        Rend False pour tout provider sans plafond compte ici (cohere, mistral,
        CLI, local) : leur limite est constatee a l'usage, pas modelisee.
        """
        fenetres = _fenetres_quota_gratuit()
        if provider not in fenetres:
            return False
        if not self._initialized:
            await self.initialize()

        _metrique, fenetre_s, cle_plafond = fenetres[provider]
        plafond = self.config.get(cle_plafond)
        if not plafond:
            return False
        depuis = time.time() - fenetre_s

        def _compter() -> int:
            conn = sqlite3.connect(self.db_path)
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT COUNT(*) FROM billing_history "
                    "WHERE provider = ? AND timestamp > ?",
                    (provider, depuis),
                )
                return int(cur.fetchone()[0])
            finally:
                conn.close()

        try:
            async with db_read_lock_context():
                utilise = await asyncio.to_thread(_compter)
        except Exception as err:  # base absente/verrouillee : on ne bloque pas
            logger.debug(f"[BudgetGuard] Lecture du quota '{provider}' impossible : {err}")
            return False
        return utilise >= plafond

    async def get_available_provider(self, sains: set[str] | None = None,
                                     exclus: set[str] | None = None) -> str | None:
        """
        Détermine le provider d'EXÉCUTION (executor) optimal.

        23/08 Axel : le local des workflows = AJEAN seulement (Ollama/LM Studio
        restent au registre, hors élection). AJEAN vivant → on le prend avant
        le cloud, sinon les clés gratuites empêchent à jamais d'exercer le llama
        (constat des nuits DreamCoder : ajean_pc jamais élu). Ollama n'est plus
        dans cette cascade : il gagnait trop (07/07) puis s'est éteint (18/08).

        1. AJEAN (`ajean_pc` LAN, puis `ajean_deck` loopback) s'il est éligible.
        2. Rotation round-robin parmi les API cloud gratuites/abonnements
           (gemini-cli-abo, claude-cli-abo, cohere-free, mistral-free,
           gemini-free). D-15 : cerebras-free n'en fait plus partie (PayGo).
        3. deepseek-free (quota + solde réel), puis anthropic-claude-haiku
           (payant, budget dédié) — coupés si total_daily_budget_usd atteint.
        4. None si tout est épuisé.

        Note : ceci reste une cascade de coût, pas un choix de modèle "adapté
        à la tâche" (ça, c'est core/provider_scorer.py::ProviderScorer, utilisé
        par le moteur interactif via LLMGateway.get_provider_for_tier). Pour
        DreamCoder, le branchement est fait : la tâche est réservée d'abord,
        puis le provider est élu par taux de réussite historique sur son
        domaine (`classer_providers_pour_tache`) ; cette cascade-ci ne sert
        plus que de secours quand aucun candidat n'a d'historique.

        [#T387] Deux filtres optionnels, tous deux nés du drainage parallèle :

        :param sains: providers ayant réellement répondu à la sonde de santé
            (`core/sante_providers.py`). Sans ce filtre, la sélection se contente
            de « la clé est présente » — et trois providers étaient élus puis
            perdaient leur tâche : cerebras-free (HTTP 402, palier gratuit
            disparu — D-15), claude-cli-abo
            (« Credit balance is too low » rendu comme une réponse normale) et
            gemini-cli-abo (binaire absent en prod). None = pas de filtrage.
        :param exclus: providers déjà attribués à un autre slot du cycle. Trois
            tâches concurrentes sur le même provider videraient son quota trois
            fois plus vite et déclencheraient ses 429.
        """
        def _utilisable(nom: str) -> bool:
            if exclus and nom in exclus:
                return False
            return sains is None or nom in sains

        if not self._initialized:
            await self.initialize()

        # 0. AJEAN — seul local des workflows. Sous sonde (`sains` fourni),
        # pas de TCP : DreamCoder a déjà exercé une complétion. Hors sonde,
        # GET /v1/models (1 s) — pas une complétion (piège DashScope).
        # 06/10 : seulement les hôtes AJEAN laissés par le réglage (providers_sondes).
        from core.llm.ajean_adapter import ajean_http_ok, providers_workflow_actifs
        for local_id in providers_workflow_actifs():
            if not _utilisable(local_id) or _in_cooldown(local_id):
                continue
            if sains is not None:
                logger.info("[BudgetGuard] Executor local AJEAN : %s (sonde OK).", local_id)
                return local_id
            if await ajean_http_ok(local_id):
                logger.info("[BudgetGuard] Executor local AJEAN : %s (HTTP 200).", local_id)
                return local_id

        now = time.time()
        one_hour_ago = now - 3600.0
        twenty_four_hours_ago = now - 86400.0

        async with db_read_lock_context():
            def query_db_status() -> tuple[int, int, int, float]:
                conn = sqlite3.connect(self.db_path)
                try:
                    cursor = conn.cursor()

                    # Gemini : Somme des tokens utilisés dans la dernière heure
                    cursor.execute("""
                        SELECT COALESCE(SUM(tokens_used), 0)
                        FROM billing_history
                        WHERE provider = 'gemini-free' AND timestamp > ?
                    """, (one_hour_ago,))
                    gemini_tokens = int(cursor.fetchone()[0])

                    # [#T404] Gemini : nombre de REQUETES sur 24 h — c'est ce
                    # que Google plafonne (20/jour/modèle au palier gratuit).
                    cursor.execute("""
                        SELECT COUNT(*)
                        FROM billing_history
                        WHERE provider = 'gemini-free' AND timestamp > ?
                    """, (twenty_four_hours_ago,))
                    gemini_requests = int(cursor.fetchone()[0])

                    # DeepSeek : Nombre de requêtes dans les dernières 24 heures
                    cursor.execute("""
                        SELECT COUNT(*)
                        FROM billing_history
                        WHERE provider = 'deepseek-free' AND timestamp > ?
                    """, (twenty_four_hours_ago,))
                    deepseek_requests = int(cursor.fetchone()[0])

                    # Global/Payant : Somme des coûts USD dans les dernières 24 heures
                    # (toutes sources confondues — sert de plafond quotidien COMBINÉ,
                    # pas seulement pour Claude Haiku, cf. total_daily_budget_usd).
                    cursor.execute("""
                        SELECT COALESCE(SUM(cost_usd), 0.0)
                        FROM billing_history
                        WHERE timestamp > ?
                    """, (twenty_four_hours_ago,))
                    global_cost = float(cursor.fetchone()[0])

                    return gemini_tokens, gemini_requests, deepseek_requests, global_cost
                finally:
                    conn.close()

            gemini_tokens, gemini_requests, deepseek_requests, global_cost = await asyncio.to_thread(query_db_status)

        # 1. Rotation round-robin parmi les API cloud gratuites/abonnements
        # réellement disponibles (clé présente / binaire trouvé / pas en
        # cooldown / quota non dépassé pour gemini-free).
        # [#T248] Les providers CLI sont exclus quand l'hôte les désactive
        # (MOTEUR_DISABLE_CLI_PROVIDERS) — profil Deck sans CLI.
        cli_disabled = _cli_providers_disabled()
        if cli_disabled:
            logger.debug("[BudgetGuard] Providers CLI désactivés par MOTEUR_DISABLE_CLI_PROVIDERS — exclus du round-robin.")
        cloud_candidates: list[str] = []
        if not cli_disabled and not _in_cooldown("gemini-cli-abo") and self._check_gemini_cli_availability():
            cloud_candidates.append("gemini-cli-abo")
        if not cli_disabled and not _in_cooldown("claude-cli-abo") and self._check_claude_cli_availability():
            cloud_candidates.append("claude-cli-abo")
        # D-15 Axel 23/08 : Cerebras est PayGo Team — plus dans la rotation
        # gratuite (l'id `cerebras-free` reste pour la sonde Tab).
        # D-8 Axel 13/08 : Dashscope hors rotation (clé 401 + CGU backend).
        if not _in_cooldown("cohere-free") and os.getenv("COHERE_API_KEY"):
            cloud_candidates.append("cohere-free")
        if not _in_cooldown("mistral-free") and os.getenv("MISTRAL_API_KEY"):
            cloud_candidates.append("mistral-free")
        # [#T404] Deux plafonds, le plus contraignant gagne. Celui qui compte
        # vraiment est le nombre de REQUETES sur 24 h : mesure le 18/08, le
        # compteur de tokens/heure affichait zero pendant que Google rendait
        # deja « RESOURCE_EXHAUSTED / GenerateRequestsPerDayPerProjectPerModel-
        # FreeTier, quotaValue 20 ». La rotation elisait donc un provider mort.
        gemini_dispo = (
            gemini_tokens < self.config["gemini_free_tokens_per_hour"]
            and gemini_requests < self.config["gemini_free_requests_per_day"]
        )
        if not _in_cooldown("gemini-free") and gemini_dispo:
            cloud_candidates.append("gemini-free")
        elif not _in_cooldown("gemini-free"):
            logger.info(
                f"[BudgetGuard] gemini-free écarté : quota gratuit épuisé "
                f"({gemini_requests}/{self.config['gemini_free_requests_per_day']} requêtes sur 24 h, "
                f"{gemini_tokens}/{self.config['gemini_free_tokens_per_hour']} tokens sur 1 h)."
            )

        # [#T387] Ne garder que ce qui répond vraiment, et pas ce qu'un autre
        # slot utilise déjà.
        cloud_candidates = [c for c in cloud_candidates if _utilisable(c)]

        if cloud_candidates:
            global _free_api_rotation_index
            chosen = cloud_candidates[_free_api_rotation_index % len(cloud_candidates)]
            _free_api_rotation_index += 1
            return chosen

        # 2. Plus de filet Ollama/LM Studio : Axel 23/08, local = AJEAN
        # uniquement. Les checks `_check_ollama/_check_lmstudio` restent pour
        # d'éventuels diagnostics, ils n'élisent plus.

        total_cap_reached = global_cost >= self.config["total_daily_budget_usd"]
        if total_cap_reached:
            logger.warning(
                f"[BudgetGuard] Plafond quotidien combiné atteint (${global_cost:.4f}/"
                f"{self.config['total_daily_budget_usd']} USD) — DeepSeek/Claude-Haiku coupés, "
                "gratuit/abonnement uniquement pour le reste de la journée."
            )
            return None

        # 3. DeepSeek Free — quota de requêtes ET solde réel (API officielle
        # DeepSeek, cache 10 min). Le comptage de requêtes seul ne dit rien si
        # le compte est simplement à sec ; on vérifie les deux.
        if _utilisable("deepseek-free") and deepseek_requests < self.config["deepseek_free_requests_per_day"]:
            balance = await _get_cached_deepseek_balance_usd()
            if balance is None or balance > 0.01:
                return "deepseek-free"
            logger.warning(
                f"[BudgetGuard] DeepSeek : quota requêtes OK mais solde réel quasi nul (${balance:.4f}) — provider sauté."
            )

        # 4. Anthropic Claude Haiku (payant sous contrôle de budget dédié)
        if _utilisable("anthropic-claude-haiku") and global_cost < self.config["daily_budget_usd"]:
            return "anthropic-claude-haiku"

        # 5. Hors budget / Quotas épuisés
        logger.warning("Alerte critique : Tous les quotas et budgets LLM sont épuisés !")
        return None

    async def record_usage(self, provider: str, tokens: int, cost: float, model: str,
                            window_type: str = "daily", sync_source: str = "dreamcoder") -> None:
        """
        Enregistre la consommation d'un appel LLM dans l'historique de facturation.
        Assure la rétrocompatibilité avec l'ancien schéma de données.

        :param sync_source: Origine de l'appel (défaut 'dreamcoder' pour préserver le
            comportement existant). L'auditeur autonome (core/auditor_agent.py) passe
            'auditor' pour que ses dépenses soient comptabilisées séparément via
            get_scoped_spend_usd(), sans impacter le budget quotidien global de DreamCoder.
        """
        if not self._initialized:
            await self.initialize()

        timestamp = time.time()

        async with db_write_lock_context():
            def insert_usage():
                conn = sqlite3.connect(self.db_path)
                try:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO billing_history (
                            timestamp, provider, metric, value, currency, sync_source,
                            model, tokens_used, cost_usd, window_type
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        timestamp,
                        provider,
                        "tokens_and_cost",
                        cost,
                        "USD",
                        sync_source,
                        model,
                        tokens,
                        cost,
                        window_type
                    ))
                    conn.commit()
                    logger.info(f"Consommation enregistrée pour {provider} ({model}) : {tokens} tokens, {cost:.6f} USD.")
                except Exception as e:
                    conn.rollback()
                    logger.error(f"Erreur lors de l'enregistrement de la consommation : {e}")
                    raise e
                finally:
                    conn.close()

            await asyncio.to_thread(insert_usage)

    async def get_scoped_spend_usd(self, sync_source: str, since_seconds: float) -> float:
        """
        Coût cumulé (USD) attribué à une source d'appel donnée (ex: 'auditor')
        sur une fenêtre glissante — utilisé pour le plafond hebdomadaire de
        l'auditeur autonome, indépendant du budget quotidien global de DreamCoder.

        :param sync_source: Origine à filtrer (colonne billing_history.sync_source).
        :param since_seconds: Taille de la fenêtre glissante en secondes (ex: 7*86400).
        :return: Somme des coûts USD sur la fenêtre, 0.0 si aucune dépense.
        """
        if not self._initialized:
            await self.initialize()

        cutoff = time.time() - since_seconds

        async with db_read_lock_context():
            def query_scoped_spend() -> float:
                conn = sqlite3.connect(self.db_path)
                try:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT COALESCE(SUM(cost_usd), 0.0)
                        FROM billing_history
                        WHERE sync_source = ? AND timestamp > ?
                    """, (sync_source, cutoff))
                    return float(cursor.fetchone()[0])
                finally:
                    conn.close()

            return await asyncio.to_thread(query_scoped_spend)

    async def get_quota_summary(self) -> dict[str, Any]:
        """
        Retourne un état complet de l'utilisation des quotas et budgets pour l'IHM de supervision.
        """
        if not self._initialized:
            await self.initialize()

        ollama_online = await self._check_ollama_availability()
        lmstudio_online = await self._check_lmstudio_availability()
        now = time.time()
        one_hour_ago = now - 3600.0
        twenty_four_hours_ago = now - 86400.0

        async with db_read_lock_context():
            def fetch_summary_data() -> tuple[int, int, int, float]:
                conn = sqlite3.connect(self.db_path)
                try:
                    cursor = conn.cursor()

                    cursor.execute("""
                        SELECT COALESCE(SUM(tokens_used), 0) 
                        FROM billing_history 
                        WHERE provider = 'gemini-free' AND timestamp > ?
                    """, (one_hour_ago,))
                    gemini_used = int(cursor.fetchone()[0])

                    # [#T404] Le compteur qui décide vraiment : requêtes/24 h.
                    cursor.execute("""
                        SELECT COUNT(*)
                        FROM billing_history
                        WHERE provider = 'gemini-free' AND timestamp > ?
                    """, (twenty_four_hours_ago,))
                    gemini_req = int(cursor.fetchone()[0])

                    cursor.execute("""
                        SELECT COUNT(*) 
                        FROM billing_history 
                        WHERE provider = 'deepseek-free' AND timestamp > ?
                    """, (twenty_four_hours_ago,))
                    deepseek_used = int(cursor.fetchone()[0])

                    cursor.execute("""
                        SELECT COALESCE(SUM(cost_usd), 0.0) 
                        FROM billing_history 
                        WHERE timestamp > ?
                    """, (twenty_four_hours_ago,))
                    cost_used = float(cursor.fetchone()[0])

                    return gemini_used, gemini_req, deepseek_used, cost_used
                finally:
                    conn.close()

            gemini_used, gemini_req, deepseek_used, cost_used = await asyncio.to_thread(fetch_summary_data)

        gemini_limit = self.config["gemini_free_tokens_per_hour"]
        gemini_req_limit = self.config["gemini_free_requests_per_day"]
        deepseek_limit = self.config["deepseek_free_requests_per_day"]
        budget_limit = self.config["daily_budget_usd"]

        return {
            "timestamp": now,
            "providers": {
                "ollama": {
                    "available": ollama_online,
                    "metric": "disponibilité locale",
                    "used": 1 if ollama_online else 0,
                    "limit": 1,
                    "unit": "status"
                },
                "lmstudio": {
                    "available": lmstudio_online,
                    "metric": "disponibilité locale",
                    "used": 1 if lmstudio_online else 0,
                    "limit": 1,
                    "unit": "status"
                },
                "gemini-free": {
                    # [#T404] La métrique affichée est celle qui bloque
                    # réellement : Google plafonne les REQUÊTES par jour.
                    "available": (gemini_used < gemini_limit
                                  and gemini_req < gemini_req_limit),
                    "metric": "requêtes_24h",
                    "used": gemini_req,
                    "limit": gemini_req_limit,
                    "unit": "requêtes",
                    "tokens_1h": gemini_used,
                    "tokens_1h_limit": gemini_limit,
                },
                "deepseek-free": {
                    "available": deepseek_used < deepseek_limit,
                    "metric": "requêtes_24h",
                    "used": deepseek_used,
                    "limit": deepseek_limit,
                    "unit": "requêtes"
                },
                "anthropic-claude-haiku": {
                    "available": cost_used < budget_limit,
                    "metric": "budget_global_24h",
                    "used": round(cost_used, 4),
                    "limit": budget_limit,
                    "unit": "USD"
                }
            }
        }
