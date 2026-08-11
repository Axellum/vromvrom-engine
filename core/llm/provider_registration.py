"""
core/llm/provider_registration.py — Enregistrement des providers depuis leurs plugins
(#T231, étape 1).

Porte l'invariant qui supprime la divergence catalogue↔gateway (design §5) :

    1. découverte  → liste de ProviderPlugin
    2. build()     → transport instancié, OU exception → on écarte et on journalise
    3. INSERT      → providers + models, UNIQUEMENT pour ceux de l'étape 2

Autrement dit : **pas de transport, pas de ligne en base**. L'état « au catalogue mais
injoignable » (badge `non câblé` de `ihm-v2/src/views/LLMRegistry.tsx`) devient non
représentable pour les providers passés par ce chemin.

Étape 1 : la fonction existe, est testée, et n'est appelée par aucun chemin de
production. C'est volontaire — `core/llm_gateway.py` et `seed_models_db.py` restent
inchangés tant que les 18 providers ne sont pas convertis, sans quoi on créerait
exactement la bascule à moitié faite que le design met en garde de ne pas faire (§6).

Étape 2 : les providers du seed sont convertis (`core/llm/builtin_providers.py`) et
`enregistrer_provider_plugins_once()` est appelé au démarrage par `core/factory.py`.
L'écriture est en **upsert seul** : rien n'est jamais supprimé du catalogue ici.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from core.llm.provider_plugin import (
    CredentialsManquantes,
    ProviderPlugin,
    ProviderPluginError,
)

logger = logging.getLogger(__name__)


@dataclass
class RapportEnregistrement:
    """Résultat d'une passe d'enregistrement.

    `transports` est indexé par `"<provider_id>:<model_id>"` : le gateway range une
    instance par modèle (`llm_gateway.py:232-237`), pas une par provider.
    """

    transports: dict[str, Any] = field(default_factory=dict)
    providers_enregistres: list[str] = field(default_factory=list)
    modeles_enregistres: list[str] = field(default_factory=list)
    ecartes: dict[str, str] = field(default_factory=dict)  # provider_id → raison

    @property
    def ok(self) -> bool:
        """Vrai si au moins un provider a été enregistré et aucun n'a échoué anormalement."""
        return bool(self.providers_enregistres) and not self.ecartes


def register_provider_plugins(
    plugins: tuple[ProviderPlugin, ...] | list[ProviderPlugin],
    credentials: dict[str, str],
    *,
    dry_run: bool = False,
    upsert_provider: Any = None,
    upsert_model: Any = None,
) -> RapportEnregistrement:
    """Construit puis enregistre les providers dont le transport a pu être bâti.

    Args:
        plugins: plugins à traiter (typiquement `BUILTIN_PROVIDER_PLUGINS`).
        credentials: dict de type environnement (souvent `os.environ`). Passé
            explicitement pour que l'enregistrement soit testable sans variable globale.
        dry_run: construit les transports mais n'écrit rien en base. Sert à répondre
            « que se passerait-il ? » sans toucher au catalogue.
        upsert_provider / upsert_model: injection des écritures, pour les tests. Par
            défaut, celles de `core.models_db`. L'import est différé pour ne pas ouvrir
            de connexion SQLite au simple import de ce module.

    Returns:
        RapportEnregistrement — transports construits, lignes écrites, providers écartés.
    """
    if upsert_provider is None or upsert_model is None:
        from core import models_db

        upsert_provider = upsert_provider or models_db.upsert_provider
        upsert_model = upsert_model or models_db.upsert_model

    rapport = RapportEnregistrement()

    for plugin in plugins:
        descripteur = plugin.descriptor
        pid = descripteur.id

        # ── Étape 2 : construire AVANT d'écrire quoi que ce soit ──────────────
        transports_du_provider: dict[str, Any] = {}
        try:
            for spec in descripteur.models:
                transports_du_provider[f"{pid}:{spec.id}"] = plugin.build(
                    credentials, model=spec.id
                )
        except CredentialsManquantes as exc:
            # Cas normal : provider non configuré sur cette machine. On écarte sans
            # bruit d'erreur — c'est précisément ce qu'on veut à la place d'une ligne
            # de catalogue injoignable.
            logger.info("[ProviderRegistration] '%s' écarté : %s", pid, exc)
            rapport.ecartes[pid] = str(exc)
            continue
        except ProviderPluginError as exc:
            logger.error("[ProviderRegistration] '%s' écarté (transport invalide) : %s", pid, exc)
            rapport.ecartes[pid] = str(exc)
            continue
        except Exception as exc:  # défensif : un plugin tiers peut lever n'importe quoi
            logger.error("[ProviderRegistration] '%s' écarté (erreur inattendue) : %s", pid, exc)
            rapport.ecartes[pid] = f"{type(exc).__name__}: {exc}"
            continue

        if dry_run:
            rapport.transports.update(transports_du_provider)
            logger.info("[ProviderRegistration] '%s' constructible (dry-run, rien écrit).", pid)
            continue

        # ── Étape 3 : écrire, maintenant qu'on sait que le transport existe ───
        if not upsert_provider(pid, **descripteur.en_colonnes()):
            rapport.ecartes[pid] = "échec de l'écriture du provider en base"
            logger.error("[ProviderRegistration] '%s' : upsert_provider a échoué.", pid)
            continue

        rapport.providers_enregistres.append(pid)
        rapport.transports.update(transports_du_provider)

        # `discover_models` rend la liste déclarée par défaut ; un provider capable
        # d'interroger son API (GET /v1/models) la surcharge sans changer ce flux.
        premier_transport = next(iter(transports_du_provider.values()), None)
        for spec in plugin.discover_models(premier_transport):
            if upsert_model(spec.id, **spec.en_colonnes(pid)):
                rapport.modeles_enregistres.append(spec.id)
            else:
                logger.warning(
                    "[ProviderRegistration] '%s' : upsert_model('%s') a échoué.", pid, spec.id
                )

    logger.info(
        "[ProviderRegistration] %d provider(s) enregistré(s), %d modèle(s), %d écarté(s).",
        len(rapport.providers_enregistres),
        len(rapport.modeles_enregistres),
        len(rapport.ecartes),
    )
    return rapport


_ENREGISTREMENT_FAIT = False


def enregistrer_provider_plugins_once(credentials: dict[str, str] | None = None) -> RapportEnregistrement | None:
    """[#T231 étape 2] Enregistrement idempotent des plugins internes, au démarrage.

    Appelé par `core/factory.create_engine()` (partagé par gui_server, main et
    mcp_server) : le catalogue reçoit les providers/modèles dont le transport a pu
    être construit — c'est-à-dire dont les identifiants sont présents sur CET hôte.
    C'est ce qui résout #T242 : le catalogue suit les capacités réelles de l'hôte à
    chaque démarrage, sans re-seed manuel (sur le Deck, `dashscope` arrive enfin au
    catalogue puisque la clé y est depuis #T244).

    Désactivable via `MOTEUR_PROVIDER_PLUGINS=0` (repli sans redéploiement).
    Un échec ne doit JAMAIS empêcher le démarrage : l'ancien chemin (gateway +
    seed) reste pleinement opérationnel.
    """
    global _ENREGISTREMENT_FAIT
    if _ENREGISTREMENT_FAIT:
        return None
    _ENREGISTREMENT_FAIT = True

    if os.getenv("MOTEUR_PROVIDER_PLUGINS", "1").strip().lower() in ("0", "false", "off", "no"):
        logger.info("[ProviderRegistration] Désactivé par MOTEUR_PROVIDER_PLUGINS — catalogue inchangé.")
        return None

    try:
        from core.llm.builtin_providers import BUILTIN_PROVIDER_PLUGINS

        return register_provider_plugins(
            BUILTIN_PROVIDER_PLUGINS,
            credentials if credentials is not None else dict(os.environ),
        )
    except Exception:
        logger.exception("[ProviderRegistration] Échec de l'enregistrement des plugins — l'ancien chemin reste actif.")
        return None
