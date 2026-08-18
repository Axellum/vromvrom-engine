"""
core/model_health_probe.py — Sonde de vivacité des modèles du catalogue (#T333).

Pourquoi : le 12/08, **35 % des tentatives de cascade** partaient dans le vide
(51 tentatives, 18 échecs, 6 modèles disjonctés) parce que des modèles du
catalogue ne répondaient plus — clé d'abonnement refusée, modèle retiré du
gratuit. Personne ne le savait : le défaut n'était visible qu'en lisant le
journal. Coût réel pour l'utilisateur : la même demande est passée de 39 s à
104 s, sans le moindre changement de code sur son chemin.

Ce que cette sonde N'invente PAS : le test de vivacité (`ping`, une vraie
mini-génération) et l'interrupteur (`set_model_status`) existaient déjà, mais
rien ne les reliait. Elle est l'ordonnanceur manquant, pas un nouvel outil.

⚠️ Le test DOIT être une inférence réelle. Un simple `GET /v1/models` ne suffit
pas : mesuré le 12/08 sur DashScope, la même clé rend **HTTP 200 sur la liste des
modèles** et **HTTP 401 sur une complétion**. Une sonde qui se contenterait de
lister aurait déclaré tout le monde en bonne santé.

Cycle complet, avec le droit de modifier le routage de production — d'où des
garde-fous qui priment sur la réactivité :

  1. jamais toucher un modèle désactivé PAR UN HUMAIN (cf. `desactive_par_sonde`) ;
  2. rien désactiver quand la majorité du cycle échoue (c'est le réseau, pas les
     modèles) ;
  3. ne jamais descendre sous un plancher de modèles actifs ;
  4. concurrence bornée et prompt minuscule — la sonde consomme de vrais tokens.

Ce que cette sonde s'est vu ajouter (mesuré en prod : 31/76 modèles éteints,
112 cycles consécutifs de re-confirmation de la même panne, ~3 s par test) :

- **Backoff sur ce qui est déjà éteint.** Un modèle éteint par la sonde n'est
  plus re-testé à chaque cycle horaire : l'intervalle de re-test croît avec le
  nombre d'échecs consécutifs (`intervalle_backoff`), plafonné à une journée.
  Une panne actionnable (crédit épuisé, workspace non trusté, binaire absent,
  clé refusée) ne se répare pas par la répétition : on la re-confirme de moins
  en moins souvent, sans jamais cesser de la re-tester — la réactivation reste
  automatique le jour où Axel répare la cause.
- **Classement de la cause.** `classer_erreur` distingue « actionnable par un
  humain » d'« inconnue / transitoire » à partir du texte de la dernière erreur.
  Prudence : on ne classe comme actionnable que ce qu'on reconnaît de façon
  fiable ; dans le doute, c'est transitoire (un modèle sain rangé par erreur
  dans « actionnable » serait mis au placard pour la journée).
- **Visible une fois, pas cent.** Une cause actionnable produit UN message
  clair et actionnable (quoi faire, sur quelle machine) quand elle est détectée
  ou quand elle change — jamais à chaque cycle (colonne
  `cause_actionnable_signalee`).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)

# Intervalle entre deux cycles. Une heure par défaut : une clé morte doit se voir
# le jour même, pas dans la minute — et chaque cycle coûte de vrais tokens.
INTERVALLE_DEFAUT_S = 3600
# Un modèle actif doit rater ce nombre de cycles CONSÉCUTIFS avant d'être éteint.
# 3 h de panne avérée : assez pour ignorer une coupure passagère, assez peu pour
# ne pas laisser saigner la latence toute la journée.
SEUIL_ECHECS = 3
# Au-delà de cette proportion d'échecs sur un cycle, on ne désactive RIEN : quand
# tout tombe en même temps, la cause est locale (réseau, DNS, coupure FAI).
SEUIL_PANNE_GLOBALE = 0.8
# Plancher de modèles actifs — un catalogue vide rendrait le moteur muet.
MIN_MODELES_ACTIFS = 5
# Appels simultanés : borné pour ne pas faire une rafale sur les APIs.
CONCURRENCE = 4
TIMEOUT_PING_S = 20.0
# Plafond du backoff de re-test d'un modèle déjà éteint par la sonde : une
# journée. Au-delà, on re-testé au plus une fois par jour — assez pour
# réactiver le jour même où la cause disparaît, sans re-confirmer la même panne
# 24 fois par jour.
PLAFOND_BACKOFF_S = 24 * 3600
# Colonne de suivi d'alerte : mémorise la catégorie actionnable déjà signalée
# pour un modèle, pour ne pas répéter le même message à chaque cycle.
_COLONNE_ALERTE = "cause_actionnable_signalee"

# Motifs actionnables reconnus de façon fiable, mesurés en prod le 17/08.
# Chaque entrée : (catégorie, tuple de fragments à chercher, message humain).
# Règle de prudence : on n'ajoute ici QUE ce qu'on a vu échouer en réel, pas
# une taxonomie théorique — un faux positif mettrait un modèle sain au placard.
_PATTERNS_ACTIONNABLES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "credit",
        # HTTP 400 Anthropic (reproduit en direct) : solde trop bas.
        ("credit balance is too low", "insufficient_quota", "out of quota",
         "insufficient balance", "no credit", "solde insuffisant"),
        "Recharge le crédit du compte (Plans & Billing sur api.anthropic.com) — "
        "aucune nouvelle tentative ne réparera ça.",
    ),
    (
        "trust",
        # Claude CLI : workspace non trusté, à accepter une fois en interactif.
        ("has not been trusted", "hastrustdialogaccepted", "trust dialog"),
        "Accepte le dialogue de confiance de Claude Code une fois en interactif "
        "sur la machine concernée (ou pose hasTrustDialogAccepted: true dans sa "
        "configuration).",
    ),
    (
        "binaire",
        # Binaire CLI absent (Gemini *-cli) : erreur système de fichier.
        ("no such file or directory", "[errno 2]", "not recognized as an "
         "internal or external command"),
        "Installe ou corrige le chemin du binaire CLI manquant sur la machine "
        "qui héberge le provider.",
    ),
    (
        "cle",
        # Clé d'API refusée : 401/403 sur l'authentification.
        ("invalid_api_key", "authentication_error", "unauthorized", "401",
         "403", "permission denied"),
        "Vérifie / remplace la clé d'API refusée (401/403) auprès du fournisseur.",
    ),
)


def _maintenant() -> float:
    """Horloge de la sonde, isolée pour être injectable dans les tests."""
    return time.time()


def _entier_env(nom: str, defaut: int) -> int:
    try:
        return int(os.environ.get(nom, defaut))
    except (TypeError, ValueError):
        return defaut


def intervalle_sonde() -> int:
    return _entier_env("MODEL_HEALTH_INTERVAL_SECONDS", INTERVALLE_DEFAUT_S)


def sonde_activee() -> bool:
    """Interrupteur général : `MODEL_HEALTH_PROBE=false` neutralise la sonde."""
    return os.environ.get("MODEL_HEALTH_PROBE", "true").strip().lower() not in (
        "0", "false", "no", "non", "off",
    )


def peut_desactiver() -> bool:
    """
    Autonomie d'action. `MODEL_HEALTH_AUTO_DISABLE=false` réduit la sonde à un
    rôle d'observation : elle mesure et journalise, sans toucher au routage.
    """
    return os.environ.get("MODEL_HEALTH_AUTO_DISABLE", "true").strip().lower() not in (
        "0", "false", "no", "non", "off",
    )


# ── Backoff & classement de la cause ────────────────────────────────────────

def intervalle_backoff(echecs_consecutifs: int) -> float:
    """
    Intervalle de re-test d'un modèle DÉJÀ éteint par la sonde, en secondes.

    Progression géométrique (doublement) depuis l'intervalle horaire de base,
    plafonnée à une journée :
      échecs 3 → 1 h, 4 → 2 h, 5 → 4 h, 6 → 8 h, 7 → 16 h, 8+ → 24 h.

    Justification : une panne actionnable (crédit, trust, binaire, clé) ne se
    répare pas par la répétition — on la re-confirme de moins en moins souvent.
    Le doublement atteint le plafond en ~5 re-tests (~1 à 2 jours), ce qui borne
    la consommation de tokens (mesurée : ~3 s + vrais tokens par test) tout en
    restant réactif : une panne passagère qui guérit seule est rattrapée dans
    l'heure ou les deux qui suivent. Le plafond d'une journée garantit qu'un
    modèle réparé par Axel est re-testé et réactivé le jour même, sans
    intervention manuelle.
    """
    if echecs_consecutifs <= SEUIL_ECHECS:
        return float(INTERVALLE_DEFAUT_S)
    exposant = echecs_consecutifs - SEUIL_ECHECS
    return min(float(INTERVALLE_DEFAUT_S) * (2 ** exposant), float(PLAFOND_BACKOFF_S))


def _categorie_actionnable(erreur: str) -> str | None:
    """Catégorie actionnable reconnue (credit/trust/binaire/cle), sinon None."""
    bas = erreur.lower()
    for categorie, motifs, _message in _PATTERNS_ACTIONNABLES:
        if any(motif in bas for motif in motifs):
            return categorie
    return None


def classer_erreur(erreur: str | None) -> str:
    """
    Classe la dernière erreur en « actionnable » (seul un humain peut réparer) ou
    « transitoire » (inconnue, réseau, à re-tester).

    Règle de prudence : on ne classe comme actionnable que ce qu'on reconnaît de
    façon fiable. Dans le doute, c'est transitoire — un modèle sain rangé par
    erreur dans « actionnable » serait mis au placard pour la journée alors qu'il
    guérit peut-être tout seul.
    """
    if not erreur:
        return "transitoire"
    return "actionnable" if _categorie_actionnable(erreur) else "transitoire"


def _message_actionnable(model_id: str, categorie: str, erreur: str) -> str:
    """Message unique et actionnable pour une cause reconnue."""
    for cat, _motifs, texte in _PATTERNS_ACTIONNABLES:
        if cat == categorie:
            return (
                f"[SANTÉ MODÈLES] {model_id} en panne ACTIONNABLE ({categorie}) : "
                f"{texte} Dernière erreur : {erreur}"
            )
    return f"[SANTÉ MODÈLES] {model_id} en panne actionnable : {erreur}"


# ── État persistant ─────────────────────────────────────────────────────────

def _assurer_colonne_alerte() -> None:
    """
    Migration douce : ajoute la colonne de suivi d'alerte si elle manque.

    Idempotente, sans toucher aux données existantes ni au schéma des autres
    tables. Appelée en tête de cycle : la base de prod a été créée avant cette
    colonne, elle doit la gagner sans recréer la table.
    """
    from core.runtime_db import get_connection
    conn = get_connection()
    try:
        colonnes = {
            r[1] for r in conn.execute("PRAGMA table_info(model_health)")
        }
        if _COLONNE_ALERTE not in colonnes:
            conn.execute(
                f"ALTER TABLE model_health ADD COLUMN {_COLONNE_ALERTE} TEXT"
            )
            conn.commit()
    finally:
        conn.close()


def lire_sante(model_id: str) -> dict:
    """État connu d'un modèle ; valeurs neutres s'il n'a jamais été testé."""
    from core.runtime_db import get_connection
    conn = get_connection()
    # Lecture tolérante : sur une base créée avant l'ajout de la colonne d'alerte
    # (et pas encore migrée), on retombe sur le SELECT historique et on renvoie
    # la valeur neutre pour la colonne manquante.
    try:
        # Nom de colonne constant du module (jamais d'entrée utilisateur).
        sql = ("SELECT model_id, dernier_test, dernier_succes, echecs_consecutifs, "  # noqa: S608
               "succes_consecutifs, desactive_par_sonde, derniere_erreur, latence_ms, "
               f"{_COLONNE_ALERTE} FROM model_health WHERE model_id = ?")
        ligne = conn.execute(sql, (model_id,)).fetchone()
        avec_alerte = True
    except Exception:
        ligne = conn.execute(
            "SELECT model_id, dernier_test, dernier_succes, echecs_consecutifs, "
            "succes_consecutifs, desactive_par_sonde, derniere_erreur, latence_ms "
            "FROM model_health WHERE model_id = ?",
            (model_id,),
        ).fetchone()
        avec_alerte = False
    if ligne is None:
        return {
            "model_id": model_id, "dernier_test": None, "dernier_succes": None,
            "echecs_consecutifs": 0, "succes_consecutifs": 0,
            "desactive_par_sonde": 0, "derniere_erreur": None, "latence_ms": None,
            _COLONNE_ALERTE: None,
        }
    if hasattr(ligne, "keys"):
        d = dict(ligne)
        if not avec_alerte:
            d[_COLONNE_ALERTE] = None
        return d
    return {
        "model_id": ligne[0], "dernier_test": ligne[1], "dernier_succes": ligne[2],
        "echecs_consecutifs": ligne[3], "succes_consecutifs": ligne[4],
        "desactive_par_sonde": ligne[5], "derniere_erreur": ligne[6],
        "latence_ms": ligne[7],
        _COLONNE_ALERTE: ligne[8] if avec_alerte else None,
    }


# Colonnes autorisées à l'écriture (liste blanche, cf. `_ecrire_sante`).
_COLONNES_SANTE = frozenset({
    "dernier_test", "dernier_succes", "echecs_consecutifs",
    "succes_consecutifs", "desactive_par_sonde", "derniere_erreur", "latence_ms",
    _COLONNE_ALERTE,
})


def _ecrire_sante(model_id: str, **champs) -> None:
    """
    Met à jour l'état d'un modèle. Les VALEURS sont paramétrées ; les NOMS de
    colonnes sont validés contre une liste blanche — un nom interpolé dans du SQL
    reste un nom interpolé dans du SQL, même quand tous les appelants sont
    internes aujourd'hui.
    """
    inconnues = set(champs) - _COLONNES_SANTE
    if inconnues:
        raise ValueError(f"Colonnes de santé inconnues : {sorted(inconnues)}")

    from core.runtime_db import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT INTO model_health (model_id) VALUES (?) "
        "ON CONFLICT(model_id) DO NOTHING",
        (model_id,),
    )
    if champs:
        sets = ", ".join(f"{cle} = ?" for cle in champs)  # noqa: S608 — noms en liste blanche
        conn.execute(
            f"UPDATE model_health SET {sets} WHERE model_id = ?",  # noqa: S608
            (*champs.values(), model_id),
        )
    conn.commit()


def modeles_eteints_par_la_sonde() -> list[str]:
    """Modèles que la sonde a elle-même désactivés — les seuls qu'elle peut rallumer."""
    from core.runtime_db import get_connection
    conn = get_connection()
    lignes = conn.execute(
        "SELECT model_id FROM model_health WHERE desactive_par_sonde = 1"
    ).fetchall()
    return [ligne[0] for ligne in lignes]


# ── Test unitaire d'un modèle ───────────────────────────────────────────────

async def tester_modele(model_id: str, gateway=None) -> tuple[bool | None, str, float]:
    """
    Mini-génération réelle. Retourne (vivant, message d'erreur, latence ms).

    `vivant` vaut `None` si le modèle n'est pas câblé au gateway (#T186) : ce
    n'est ni une panne ni une preuve de santé — il est exclu des compteurs de
    cycle (plancher, panne globale). Sinon True/False.

    Même chemin que `POST /api/models/{id}/ping` (#T158) : c'est volontaire, un
    second test « équivalent » finirait par diverger de celui que l'IHM montre.
    """
    from core.llm_gateway import LLMGateway

    gateway = gateway or LLMGateway()
    debut = time.perf_counter()
    try:
        provider = gateway.get_provider(model_id)
    except ValueError:
        # Modèle au catalogue mais non câblé (#T186) : ce n'est pas une panne,
        # et l'éteindre n'apporterait rien — il n'est de toute façon jamais choisi.
        return None, "non_cable", 0.0
    try:
        await asyncio.wait_for(
            provider.generate_async(
                system_prompt="Réponds uniquement : pong",
                user_prompt="ping",
            ),
            timeout=TIMEOUT_PING_S,
        )
        return True, "", (time.perf_counter() - debut) * 1000
    except Exception as exc:
        return False, str(exc)[:300], (time.perf_counter() - debut) * 1000


# ── Cycle complet ───────────────────────────────────────────────────────────

def _retest_du_a_backoff(model_id: str) -> bool:
    """
    Vrai si le moment de re-tester un modèle ÉTEINT par la sonde est venu.

    Compare le temps écoulé depuis le dernier test à l'intervalle de backoff
    dicté par ses échecs consécutifs. Un modèle jamais testé est testé.
    """
    etat = lire_sante(model_id)
    dernier_test = etat["dernier_test"]
    if dernier_test is None:
        return True
    echecs = etat["echecs_consecutifs"] or 0
    return (_maintenant() - dernier_test) >= intervalle_backoff(echecs)


async def executer_cycle(gateway=None) -> dict:
    """
    Un passage : teste les modèles actifs + ceux que la sonde a éteints, puis
    applique les transitions autorisées. Retourne un compte rendu.
    """
    from core.models_db import get_active_models, set_model_status

    # Migration douce de la colonne d'alerte (base de prod créée avant elle).
    _assurer_colonne_alerte()

    # `get_active_models()` renvoie la clé SQL `id` (PK), pas `model_id`.
    actifs = [m["id"] for m in get_active_models() if m.get("id")]
    eteints_par_sonde = modeles_eteints_par_la_sonde()
    # Les modèles éteints par la sonde restent testés : c'est ce qui permet de
    # les rallumer quand l'abonnement est renouvelé ou l'incident terminé.
    a_tester = list(dict.fromkeys(actifs + eteints_par_sonde))

    # Backoff : un modèle déjà éteint par la sonde n'est PAS re-testé à chaque
    # cycle horaire — l'intervalle de re-test croît avec ses échecs consécutifs
    # (`intervalle_backoff`), plafonné à une journée. Les modèles ACTIFS, eux,
    # restent testés à chaque cycle : c'est eux qu'il faut éteindre vite.
    reportes_backoff = 0
    filtre = []
    for model_id in a_tester:
        if model_id in actifs:
            filtre.append(model_id)
            continue
        if _retest_du_a_backoff(model_id):
            filtre.append(model_id)
        else:
            reportes_backoff += 1
    a_tester = filtre

    if not a_tester:
        return {"testes": 0, "vivants": 0, "morts": 0, "ignores_non_cables": 0,
                "reportes_backoff": reportes_backoff,
                "desactives": [], "reactives": []}

    verrou = asyncio.Semaphore(CONCURRENCE)

    async def _tester(model_id: str):
        async with verrou:
            return model_id, await tester_modele(model_id, gateway)

    resultats = await asyncio.gather(
        *(_tester(m) for m in a_tester), return_exceptions=True
    )

    vivants, morts, ignores = [], [], []
    for resultat in resultats:
        if isinstance(resultat, BaseException):
            continue
        model_id, (ok, erreur, latence) = resultat
        maintenant = _maintenant()
        etat = lire_sante(model_id)
        if ok is None:
            # Non câblé : hors statistiques (sinon ils « sauvent » le plancher
            # et masquent une panne de tous les providers réellement joignables).
            ignores.append(model_id)
            continue
        if ok:
            vivants.append(model_id)
            _ecrire_sante(
                model_id, dernier_test=maintenant, dernier_succes=maintenant,
                echecs_consecutifs=0,
                succes_consecutifs=(etat["succes_consecutifs"] or 0) + 1,
                derniere_erreur=None, latence_ms=latence,
                # Succès = la cause actionnable éventuelle est réparée : on
                # efface le marqueur pour qu'une prochaine panne actionnable
                # (même cause) soit re-signalée, et non avalée.
                **{_COLONNE_ALERTE: None},
            )
        else:
            morts.append(model_id)
            _ecrire_sante(
                model_id, dernier_test=maintenant,
                echecs_consecutifs=(etat["echecs_consecutifs"] or 0) + 1,
                succes_consecutifs=0, derniere_erreur=erreur, latence_ms=latence,
            )
            # Alerte-une-fois : une cause actionnable produit UN message clair
            # quand elle apparaît ou quand elle change, pas à chaque cycle.
            categorie = _categorie_actionnable(erreur or "")
            signalee = etat.get(_COLONNE_ALERTE)
            if categorie and categorie != signalee:
                logger.warning(_message_actionnable(model_id, categorie, erreur))
                _ecrire_sante(model_id, **{_COLONNE_ALERTE: categorie})

    compte_rendu = {
        "testes": len(vivants) + len(morts),
        "vivants": len(vivants),
        "morts": len(morts),
        "ignores_non_cables": len(ignores),
        "reportes_backoff": reportes_backoff,
        "desactives": [],
        "reactives": [],
        "gele": False,
    }

    # ── Garde-fou n°2 : panne globale ──
    # Quand presque tout échoue, la cause est ici, pas chez les fournisseurs.
    # Éteindre le catalogue entier sur une coupure de box serait le pire résultat
    # possible pour un mécanisme censé protéger la production.
    if compte_rendu["testes"] and (
        len(morts) / compte_rendu["testes"] >= SEUIL_PANNE_GLOBALE
    ):
        logger.error(
            "[SANTÉ MODÈLES] %d/%d modèles en échec : panne locale probable — "
            "AUCUNE désactivation. Les compteurs restent enregistrés.",
            len(morts), compte_rendu["testes"],
        )
        compte_rendu["gele"] = True
        return compte_rendu

    if not peut_desactiver():
        if morts:
            logger.warning(
                "[SANTÉ MODÈLES] %d modèle(s) muet(s) : %s — action désactivée "
                "(MODEL_HEALTH_AUTO_DISABLE=false), rien n'a été modifié.",
                len(morts), ", ".join(sorted(morts)[:8]),
            )
        return compte_rendu

    # ── Extinction des modèles durablement muets ──
    # Plancher = modèles actifs CÂBLÉS uniquement (les non câblés n'alimentent
    # jamais la cascade).
    ignores_set = set(ignores)
    actifs_cables = [m for m in actifs if m not in ignores_set]
    actifs_restants = len(actifs_cables)
    for model_id in morts:
        if model_id not in actifs:
            continue  # déjà éteint par la sonde, rien à faire
        etat = lire_sante(model_id)
        if (etat["echecs_consecutifs"] or 0) < SEUIL_ECHECS:
            continue
        # ── Garde-fou n°3 : plancher ──
        if actifs_restants <= MIN_MODELES_ACTIFS:
            logger.error(
                "[SANTÉ MODÈLES] Plancher de %d modèles actifs atteint : %s reste "
                "ALLUMÉ malgré %d échecs. Un catalogue vide rendrait le moteur muet.",
                MIN_MODELES_ACTIFS, model_id, etat["echecs_consecutifs"],
            )
            break
        if set_model_status(model_id, "inactive", revendique_par_sonde=True):
            actifs_restants -= 1
            compte_rendu["desactives"].append(model_id)
            logger.warning(
                "[SANTÉ MODÈLES] %s DÉSACTIVÉ après %d échecs consécutifs. "
                "Dernière erreur : %s",
                model_id, etat["echecs_consecutifs"], etat["derniere_erreur"],
            )

    # ── Rallumage : uniquement ce que la sonde a éteint ──
    # Garde-fou n°1. Un modèle désactivé à la main n'apparaît pas ici, donc la
    # sonde ne peut pas défaire une décision humaine — et un toggle IHM efface
    # `desactive_par_sonde` (voir `set_model_status`).
    for model_id in vivants:
        if model_id not in eteints_par_sonde:
            continue
        if set_model_status(model_id, "active", revendique_par_sonde=True):
            compte_rendu["reactives"].append(model_id)
            logger.info(
                "[SANTÉ MODÈLES] %s RÉACTIVÉ : il répond de nouveau.", model_id
            )

    return compte_rendu


async def boucle_sonde(interval_seconds: int | None = None) -> None:
    """
    Boucle de fond, même motif que `quota_refresh_loop` (#T269) : pas de nouvel
    ordonnanceur, `asyncio.create_task` depuis gui_server.

    Une erreur de cycle est journalisée et la boucle continue — un fournisseur
    injoignable est la situation NORMALE que cette sonde observe, elle ne doit
    jamais faire tomber le serveur.
    """
    if not sonde_activee():
        logger.info("[SANTÉ MODÈLES] Sonde désactivée (MODEL_HEALTH_PROBE=false).")
        return
    interval = intervalle_sonde() if interval_seconds is None else interval_seconds
    logger.info(
        "[SANTÉ MODÈLES] Sonde démarrée (intervalle=%ds, seuil=%d échecs, "
        "action_auto=%s).", interval, SEUIL_ECHECS, peut_desactiver(),
    )
    while True:
        await asyncio.sleep(interval)
        try:
            cr = await executer_cycle()
            logger.info(
                "[SANTÉ MODÈLES] Cycle : %d testés, %d vivants, %d muets, "
                "%d désactivés, %d réactivés%s",
                cr["testes"], cr["vivants"], cr["morts"],
                len(cr["desactives"]), len(cr["reactives"]),
                " (GELÉ : panne globale)" if cr.get("gele") else "",
            )
        except Exception as exc:
            logger.warning("[SANTÉ MODÈLES] Erreur de cycle (la boucle continue) : %s", exc)
