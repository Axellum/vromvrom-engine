"""
api/routes/mcp_vue.py — Vue d'ensemble MCP : ce que le moteur EXPOSE et ce qu'il CONSOMME.

    GET /api/mcp/overview

Deux sens à ne pas confondre :

  - EXPOSÉ  : le moteur est lui-même un serveur MCP (« Tab5 Engine », `mcp_server.py`,
              transport stdio). Les IDE et Claude Code s'y branchent pour appeler ses outils.
  - CONSOMMÉ: le moteur pilote d'autres serveurs MCP (fichiers, mémoire, GitHub…) déclarés
              dans `mcp_config.json` et activés par `mcp_activation_rules.json`
              (voir `core/mcp_bridge.py`).

Cette vue est STATIQUE et sans effet de bord : elle ne lance aucun processus MCP et ne sort
pas du process. Les outils exposés sont lus par analyse syntaxique (module `ast`) des
fichiers d'outils — on n'importe pas le serveur (l'import ouvrirait des clients LLM et
des bases) — et le résultat est mis en cache tant que les fichiers ne changent pas.

Vérité d'abord : l'état « actif » vient de la CONFIGURATION et des RÈGLES, pas d'un test de
démarrage ; la section `notes` le dit. Aucun secret n'est exposé : on ne lit jamais `env`
(le bloc des variables d'un serveur), et la cible d'un serveur est passée au masqueur de
secrets du moteur.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from core.log_redaction import masquer_secrets

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp", tags=["MCP"])

_DEPOT = Path(__file__).resolve().parents[2]
_FICHIER_SERVEUR = _DEPOT / "mcp_server.py"
_FICHIER_REGLES = _DEPOT / "mcp_activation_rules.json"
_FICHIER_SERVEUR_WORKSPACE = _DEPOT / "workspace_mcp.py"

# Le pont ne lance jamais ce serveur : c'est le moteur lui-même (anti-récursion).
_SERVEURS_AUTO_EXCLUS = ("tab5-engine", "tab5_engine")

_LONGUEUR_MAX_DESCRIPTION = 240
_MOTS_CLES_MAX = 12

# ═══════════════════════════════════════════════════════════════════════════
# Côté EXPOSÉ : analyse statique des outils (avec cache)
# ═══════════════════════════════════════════════════════════════════════════

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"signature": None, "valeur": None}


def _est_decorateur_outil(decorateur: ast.expr) -> bool:
    """`@mcp.tool()` ou `@mcp.tool` (FastMCP)."""
    cible = decorateur.func if isinstance(decorateur, ast.Call) else decorateur
    return (
        isinstance(cible, ast.Attribute)
        and cible.attr == "tool"
        and isinstance(cible.value, ast.Name)
        and cible.value.id == "mcp"
    )


def _premiere_phrase(docstring: str | None) -> str:
    """Premier paragraphe du docstring, sur une ligne, tronqué."""
    if not docstring:
        return ""
    paragraphe = docstring.strip().split("\n\n", 1)[0]
    texte = " ".join(paragraphe.split())
    if len(texte) > _LONGUEUR_MAX_DESCRIPTION:
        texte = texte[: _LONGUEUR_MAX_DESCRIPTION - 1].rstrip() + "…"
    return texte


def _outils_d_un_fichier(chemin: Path) -> list[dict]:
    arbre = ast.parse(chemin.read_text(encoding="utf-8"), filename=str(chemin))
    outils: list[dict] = []
    for noeud in arbre.body:
        if isinstance(noeud, ast.FunctionDef | ast.AsyncFunctionDef) and any(
            _est_decorateur_outil(d) for d in noeud.decorator_list
        ):
            outils.append({"nom": noeud.name, "description": _premiere_phrase(ast.get_docstring(noeud))})
    return outils


def _modules_d_outils() -> list[Path]:
    """
    Fichiers d'outils du serveur, déduits des `import core.mcp_tools.X` de `mcp_server.py`
    (une seule source de vérité : l'ajout d'un module d'outils dans le point d'entrée est
    vu ici sans retouche).
    """
    arbre = ast.parse(_FICHIER_SERVEUR.read_text(encoding="utf-8"), filename=str(_FICHIER_SERVEUR))
    modules: list[Path] = []
    for noeud in ast.walk(arbre):
        noms: list[str] = []
        if isinstance(noeud, ast.Import):
            noms = [a.name for a in noeud.names]
        elif isinstance(noeud, ast.ImportFrom) and noeud.module:
            noms = [noeud.module]
        for nom in noms:
            if nom.startswith("core.mcp_tools."):
                chemin = _DEPOT.joinpath(*nom.split(".")).with_suffix(".py")
                if chemin.is_file() and chemin not in modules:
                    modules.append(chemin)
    return modules


def _signature_fichiers(fichiers: list[Path]) -> tuple:
    sig = []
    for f in fichiers:
        st = f.stat()
        sig.append((str(f), st.st_mtime_ns, st.st_size))
    return tuple(sig)


def _outils_exposes() -> list[dict]:
    """Outils du serveur « Tab5 Engine », triés par fichier puis par ordre de déclaration."""
    fichiers = [_FICHIER_SERVEUR, *_modules_d_outils()]
    signature = _signature_fichiers(fichiers)
    with _cache_lock:
        if _cache["signature"] == signature:
            return _cache["valeur"]
    outils: list[dict] = []
    for f in fichiers[1:]:
        outils.extend(_outils_d_un_fichier(f))
    with _cache_lock:
        _cache["signature"] = signature
        _cache["valeur"] = outils
    return outils


def _nombre_outils_workspace() -> int | None:
    """Combien d'outils expose le second serveur du dépôt (Google Workspace) ; None si illisible."""
    try:
        return len(_outils_d_un_fichier(_FICHIER_SERVEUR_WORKSPACE))
    except Exception as exc:
        logger.debug("[MCP-VUE] workspace_mcp illisible : %s", exc)
        return None


def _sdk_mcp_present() -> bool:
    """Le paquet `mcp` (FastMCP) est-il installé ? Sans l'importer entièrement."""
    try:
        import importlib.util
        return importlib.util.find_spec("mcp.server.fastmcp") is not None
    except Exception:
        return False


def _section_expose() -> dict:
    if not _FICHIER_SERVEUR.is_file():
        return {"disponible": False, "point_d_entree": "mcp_server.py (introuvable)", "outils": []}
    outils = _outils_exposes()
    sdk = _sdk_mcp_present()
    return {
        "disponible": bool(outils) and sdk,
        "point_d_entree": "python mcp_server.py (transport stdio)",
        "outils": outils,
        # Champs additifs
        "serveur": "Tab5 Engine",
        "nombre_outils": len(outils),
        "source": "analyse statique des fichiers d'outils (aucun serveur lancé)",
        "sdk_mcp_installe": sdk,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Côté CONSOMMÉ : mcp_config.json + règles d'activation
# ═══════════════════════════════════════════════════════════════════════════

_RE_IDENTIFIANTS_URL = re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s@]+@")
_RE_FLAG_SECRET = re.compile(r"(token|key|secret|passw|pwd|auth|bearer)", re.IGNORECASE)


def _chemins_config() -> list[str]:
    """Mêmes emplacements, dans le même ordre, que `MCPBridge.start` (premier trouvé gagne)."""
    return [
        os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-ide", "mcp_config.json"),
        str(_DEPOT / "mcp_config.json"),
        "/config/moteur-master/mcp_config.json",
    ]


def _chemin_lisible(chemin: str) -> str:
    """Chemin sans le nom d'utilisateur de la machine (`~/…`)."""
    home = os.path.expanduser("~")
    return ("~" + chemin[len(home):]) if chemin.startswith(home) else chemin


def _masquer_args(args: list[Any]) -> list[str]:
    """Arguments d'un serveur, secrets masqués : valeur d'un `--token`, `--api-key=…`, etc."""
    sortie: list[str] = []
    masquer_suivant = False
    for a in args:
        texte = str(a)
        if masquer_suivant:
            sortie.append("***")
            masquer_suivant = False
            continue
        if texte.startswith("-") and _RE_FLAG_SECRET.search(texte):
            if "=" in texte:
                texte = texte.split("=", 1)[0] + "=***"
            else:
                masquer_suivant = True
        sortie.append(texte)
    return sortie


def _cible(conf: dict) -> tuple[str, str]:
    """(type, cible lisible sans secret) d'un serveur de mcp_config.json."""
    url = conf.get("url")
    if isinstance(url, str) and url:
        type_ = str(conf.get("type") or "http").lower()
        # Ni identifiants ni paramètres d'URL (`?key=`) : seulement hôte + chemin.
        sans_requete = re.sub(r"^(\w+://)[^/@]*@", r"\1", url.split("?", 1)[0])
        return type_, masquer_secrets(sans_requete)
    commande = str(conf.get("command") or "")
    args = conf.get("args") if isinstance(conf.get("args"), list) else []
    texte = " ".join([commande, *_masquer_args(args)]).strip()
    # Une chaîne de connexion en argument (`mysql://utilisateur:motdepasse@hôte/base`) porte
    # ses identifiants dans l'URL : on les remplace, que le motif du masqueur les reconnaisse ou non.
    texte = _RE_IDENTIFIANTS_URL.sub(r"\1***@", texte)
    return "stdio", masquer_secrets(_chemin_lisible(texte)) if texte else ""


def _lire_json(chemin: str) -> Any:
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


def _section_consomme() -> tuple[list[dict], list[str], dict]:
    """(serveurs consommés, notes, état de la source). Ne lance rien."""
    notes: list[str] = []
    chemins = _chemins_config()
    trouve = next((c for c in chemins if os.path.exists(c)), None)
    source = {"fichier": None, "etat": "absent", "cherches": [_chemin_lisible(c) for c in chemins]}

    # ── Règles d'activation ──
    regles: dict = {}
    if _FICHIER_REGLES.is_file():
        try:
            lu = _lire_json(str(_FICHIER_REGLES))
            regles = lu if isinstance(lu, dict) else {}
        except Exception as exc:
            notes.append(f"mcp_activation_rules.json illisible ({masquer_secrets(str(exc))[:100]}) : "
                         "comme le pont, cette vue considère qu'aucun serveur n'est activé.")
    else:
        notes.append("mcp_activation_rules.json est absent : le pont ne démarre aucun serveur.")
    defaut = [str(s) for s in (regles.get("default") or [])]
    liste_regles = [r for r in (regles.get("rules") or []) if isinstance(r, dict)]

    if not trouve:
        notes.insert(0, "Aucun mcp_config.json trouvé (emplacements cherchés : "
                        + " ; ".join(source["cherches"]) + ") : le pont MCP est désactivé, "
                        "le moteur ne consomme aucun serveur MCP sur cette machine.")
        return [], notes, source

    source["fichier"] = _chemin_lisible(trouve)
    try:
        config = _lire_json(trouve)
        serveurs = config.get("mcpServers") if isinstance(config, dict) else None
        if not isinstance(serveurs, dict):
            raise ValueError("clé « mcpServers » absente ou invalide")
    except Exception as exc:
        source["etat"] = "illisible"
        notes.insert(0, f"{source['fichier']} est illisible ({masquer_secrets(str(exc))[:100]}) : "
                        "le pont ne démarre aucun serveur tant qu'il n'est pas corrigé.")
        return [], notes, source
    source["etat"] = "lu"
    notes.insert(0, f"Serveurs lus dans {source['fichier']} (premier fichier trouvé, comme le fait le pont).")

    consomme: list[dict] = []
    for nom, conf in serveurs.items():
        conf = conf if isinstance(conf, dict) else {}
        type_, cible = _cible(conf)
        phrases: list[str] = []
        actif: bool | None

        if nom in _SERVEURS_AUTO_EXCLUS:
            actif = False
            phrases.append("Jamais lancé par le pont : c'est le moteur lui-même (évite qu'il s'appelle en boucle).")
        elif not conf.get("command") and not conf.get("url"):
            actif = False
            phrases.append("Ni commande ni adresse dans la config : ignoré par le pont.")
        else:
            par_defaut = nom in defaut
            par_mots = []
            for r in liste_regles:
                if nom in [str(a) for a in (r.get("activate") or [])]:
                    mots = [str(k) for k in (r.get("keywords") or [])]
                    suite = "…" if len(mots) > _MOTS_CLES_MAX else ""
                    par_mots.append("Lancé aussi si la demande contient l'un de ces mots : "
                                    + ", ".join(mots[:_MOTS_CLES_MAX]) + suite + ".")
            if par_defaut:
                actif = True
                phrases.append("Lancé à chaque demande (liste « default » de mcp_activation_rules.json).")
            elif par_mots:
                actif = None
                phrases.append("Dépend de la demande : lancé seulement si un mot-clé correspond.")
            else:
                actif = False
                phrases.append("Aucune règle ne l'active : jamais lancé, même s'il est configuré.")
            phrases.extend(par_mots)

        consomme.append({"nom": nom, "type": type_, "actif": actif, "cible": cible, "regles": phrases})

    # ── Cohérence règles ⇄ config ──
    cites = {str(s) for s in defaut}
    for r in liste_regles:
        cites.update(str(a) for a in (r.get("activate") or []))
    absents = sorted(c for c in cites if c not in serveurs and c not in _SERVEURS_AUTO_EXCLUS)
    if absents:
        notes.append("Les règles d'activation citent des serveurs absents de la config (ils ne peuvent "
                     "pas démarrer) : " + ", ".join(absents) + ".")
    return consomme, notes, source


def _notes_generales() -> list[str]:
    notes = [
        "« actif » vient de la configuration et des règles, pas d'un test de démarrage : aucun serveur "
        "n'est lancé pour produire cette vue. true = lancé à chaque demande, null = selon les mots de la "
        "demande, false = jamais.",
        "Le pont démarre les serveurs « actifs » à chaque demande du pipeline, puis les arrête : chacun "
        "coûte un lancement de processus (8 s au plus avant abandon).",
        "Cette vue ne concerne que le moteur. Les serveurs MCP de Claude Code (fichier ~/.claude.json) "
        "sont un autre réglage, sans lien avec mcp_config.json.",
    ]
    n = _nombre_outils_workspace()
    if n:
        notes.append(f"Le dépôt contient un second serveur MCP, Google Workspace (workspace_mcp.py, {n} outils : "
                     "agenda, Gmail, Drive…). Il se branche à part et n'est pas listé dans les outils exposés ci-dessus.")
    return notes


@router.get("/overview")
async def mcp_overview() -> dict:
    """Ce que le moteur expose en MCP et ce qu'il consomme (voir le module)."""
    try:
        expose = await run_in_threadpool(_section_expose)
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        # Source de vérité illisible : pas de 200 qui prétendrait « aucun outil ».
        raise HTTPException(
            status_code=503,
            detail=f"Outils MCP exposés illisibles (analyse de mcp_server.py impossible) : {masquer_secrets(str(exc))[:200]}",
        ) from exc
    consomme, notes_source, source = await run_in_threadpool(_section_consomme)
    return {
        "expose": expose,
        "consomme": consomme,
        "notes": [*notes_source, *_notes_generales()],
        "consomme_source": source,
    }
