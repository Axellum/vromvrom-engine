"""
core/sauvegarde.py — Sauvegarde à chaud des bases du moteur, vérifiable et restaurable.

Constat (audit de la prod du 01/11/2026) : aucune sauvegarde automatique des bases
ne fonctionnait. `sqlite_sync.service` plantait en boucle depuis le démarrage du
Deck (419 834 redémarrages : paramiko absent, source inexistante, cible sur un
disque interdit) et les dernières copies manuelles de `memory.db` et de
`moteur_runtime.db` dataient de 75 jours — alors que le Dreamer réécrit
`memory.db` chaque heure.

Ce module remplace ça par quelque chose de simple et de contrôlable :

- **Copie à chaud COHÉRENTE** des bases SQLite par `sqlite3.Connection.backup()`
  (l'API de sauvegarde en ligne de SQLite). Jamais une copie de fichier brute :
  sur une base en mode WAL, copier le `.db` seul perd les pages encore dans le
  `-wal` et peut produire une base incohérente.
- **Un dossier horodaté par sauvegarde** (`AAAAMMJJ_hhmmss/`) avec un
  `manifeste.json` : date, commit du moteur, et pour chaque fichier sa taille, son
  empreinte sha256 et le résultat de `PRAGMA integrity_check` sur la COPIE.
- **Jamais de secret** : `.env`, jetons, clés et certificats ne sont ni copiés ni
  listés comme sauvegardés (voir `est_fichier_secret`). Une sauvegarde qui contient
  les clés d'API serait un second endroit à protéger — et à fuir.
- **Rétention** (`MOTEUR_SAUVEGARDE_GARDER`, défaut 14) appliquée APRÈS une
  sauvegarde réussie et saine, jamais avant : une sauvegarde en échec ne doit pas
  faire disparaître les anciennes.
- **Un seul écrivain** : verrou `filelock` non bloquant. Une seconde demande
  pendant qu'une sauvegarde tourne est refusée (HTTP 409 côté API), pas mise en file.

Configuration (voir `.env.example`) :
    MOTEUR_SAUVEGARDE_DIR     dossier de destination (défaut : <dépôt>/sauvegardes/)
    MOTEUR_SAUVEGARDE_GARDER  nombre de sauvegardes conservées (défaut : 14)

CLI :
    python -m core.sauvegarde                  crée une sauvegarde
    python -m core.sauvegarde --lister         liste les sauvegardes
    python -m core.sauvegarde --verifier <id>  recontrôle sha256 + intégrité d'une copie

Codes de sortie : 0 = tout est sain ; 1 = anomalie constatée (intégrité ou empreinte) ;
2 = impossible de faire ce qui est demandé (sauvegarde en cours, disque plein…).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from filelock import FileLock, Timeout

logger = logging.getLogger("core.sauvegarde")

# Racine du dépôt : parent du package `core`. Résolue depuis __file__ et jamais
# depuis le répertoire courant (un timer systemd n'a pas le même CWD qu'un shell).
_RACINE_DEFAUT = Path(__file__).resolve().parents[1]

# ── Ce qui est sauvegardé ──────────────────────────────────────────────────
# Bases SQLite vivantes (vérifié dans le code : core/runtime_db.py, memory/memory_db.py,
# core/models_db.py). `session_history.db`, `routing_metrics.db` et `checkpoints.db` ont
# été fondues dans `moteur_runtime.db` : ils ne sont plus des bases vivantes.
BASES_SQLITE: tuple[str, ...] = ("moteur_runtime.db", "memory.db", "models_registry.db")
# Fichiers de configuration versionnés avec le code mais modifiés à chaud par l'IHM.
FICHIERS_CONFIG: tuple[str, ...] = (
    "config.json",
    "ha_commands.json",
    "mcp_activation_rules.json",
    "agents_workflows.json",
)
# Dossiers copiés tels quels (prompts éditables depuis l'IHM).
DOSSIERS_COPIES: tuple[str, ...] = ("prompts/agents",)

RETENTION_DEFAUT = 14
VERSION_MANIFESTE = 1
NOM_MANIFESTE = "manifeste.json"
NOM_VERROU = ".sauvegarde.lock"

# Identifiant d'une sauvegarde : horodatage local, suffixe `_N` si deux sauvegardes
# tombent dans la même seconde. Ce motif sert aussi de garde-fou : on ne supprime et
# on ne lit JAMAIS un dossier dont le nom ne le respecte pas (pas de traversée de chemin).
_MOTIF_ID = re.compile(r"^\d{8}_\d{6}(?:_\d+)?$")
_MOTIF_TMP = re.compile(r"^\.\d{8}_\d{6}(?:_\d+)?\.tmp$")

# Un fichier dont le NOM ressemble à un secret n'est jamais copié.
_FRAGMENTS_SECRETS = (
    "token", "secret", "credential", "password", "passwd", "id_rsa", "id_ed25519",
    "ha_logger_key", "apikey", "api_key", "private_key",
)
_SUFFIXES_SECRETS = (".pem", ".key", ".p12", ".pfx", ".jks", ".kdbx")


class ErreurSauvegarde(Exception):
    """Base des erreurs de sauvegarde (message lisible, destiné à l'exploitant)."""


class SauvegardeEnCours(ErreurSauvegarde):
    """Une autre sauvegarde tient déjà le verrou."""


class SauvegardeImpossible(ErreurSauvegarde):
    """La sauvegarde ne peut pas être faite (dossier inaccessible, disque plein…)."""


# ── Configuration ──────────────────────────────────────────────────────────

def racine_depot() -> Path:
    """Racine du dépôt à sauvegarder (remplaçable dans les tests)."""
    return _RACINE_DEFAUT


def dossier_sauvegardes(racine: Path | None = None) -> Path:
    """Dossier de destination : `MOTEUR_SAUVEGARDE_DIR`, sinon `<dépôt>/sauvegardes/`."""
    valeur = os.environ.get("MOTEUR_SAUVEGARDE_DIR", "").strip()
    if valeur:
        return Path(valeur).expanduser()
    return (racine or racine_depot()) / "sauvegardes"


def retention() -> int:
    """Nombre de sauvegardes conservées ; une valeur invalide retombe sur le défaut."""
    brut = os.environ.get("MOTEUR_SAUVEGARDE_GARDER", "").strip()
    if not brut:
        return RETENTION_DEFAUT
    try:
        valeur = int(brut)
    except ValueError:
        logger.warning("[SAUVEGARDE] MOTEUR_SAUVEGARDE_GARDER invalide (%r) : défaut %d.",
                       brut, RETENTION_DEFAUT)
        return RETENTION_DEFAUT
    if valeur < 1:
        # 0 ou négatif effacerait la sauvegarde qu'on vient de faire.
        logger.warning("[SAUVEGARDE] MOTEUR_SAUVEGARDE_GARDER=%d < 1 : défaut %d.",
                       valeur, RETENTION_DEFAUT)
        return RETENTION_DEFAUT
    return valeur


def est_fichier_secret(nom: str) -> bool:
    """Vrai si le nom de fichier désigne un secret (`.env*`, jeton, clé, certificat)."""
    bas = Path(nom).name.lower()
    if bas == ".env" or bas.startswith(".env.") or bas.endswith(".env"):
        return True
    if bas.endswith(_SUFFIXES_SECRETS):
        return True
    return any(fragment in bas for fragment in _FRAGMENTS_SECRETS)


# ── Outils bas niveau ──────────────────────────────────────────────────────

def _sha256(chemin: Path) -> str:
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloc)
    return h.hexdigest()


def _integrite_sqlite(chemin: Path) -> str:
    """`PRAGMA integrity_check` sur un fichier : 'ok' ou 'ko' (jamais d'exception)."""
    try:
        conn = sqlite3.connect(f"{chemin.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
        try:
            lignes = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("[SAUVEGARDE] integrity_check impossible sur %s : %s", chemin.name, exc)
        return "ko"
    return "ok" if lignes == [("ok",)] else "ko"


def _copier_base_sqlite(source: Path, destination: Path) -> None:
    """
    Copie à chaud cohérente par l'API de sauvegarde en ligne de SQLite.

    La source est ouverte en lecture seule : la sauvegarde n'écrit jamais dans la
    base vivante. `backup()` copie en une seule passe (pages=-1) : si le moteur
    écrit en même temps, SQLite garantit un instantané cohérent. La copie est
    ensuite remise en mode `DELETE` pour ne produire qu'UN fichier autonome (pas de
    `-wal` / `-shm` à restaurer avec elle).
    """
    try:
        source_conn = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    except sqlite3.OperationalError:
        # Certaines bases WAL refusent l'ouverture en lecture seule (-shm absent
        # et dossier non inscriptible) : repli sur une ouverture normale. backup()
        # ne fait que lire, la sémantique reste la même.
        source_conn = sqlite3.connect(str(source), timeout=30)
    try:
        dest_conn = sqlite3.connect(str(destination))
        try:
            source_conn.backup(dest_conn)
            dest_conn.execute("PRAGMA journal_mode=DELETE")
            dest_conn.commit()
        finally:
            dest_conn.close()
    finally:
        source_conn.close()


def _fichiers_dossier(dossier: Path) -> list[Path]:
    """Fichiers d'un dossier copié (récursif), hors cache Python (les secrets sont filtrés plus loin)."""
    return [
        chemin for chemin in sorted(dossier.rglob("*"))
        if chemin.is_file() and "__pycache__" not in chemin.parts
    ]


def _commit_moteur() -> str | None:
    """SHA court du commit du moteur, ou None s'il est indéterminable (jamais inventé)."""
    try:
        from core.version_info import get_short_commit
        return get_short_commit() or None
    except Exception as exc:  # pragma: no cover - défensif, ne doit jamais bloquer une sauvegarde
        logger.debug("[SAUVEGARDE] commit indéterminable : %s", exc)
        return None


def _nouvel_id(dossier: Path, maintenant: datetime) -> str:
    base = maintenant.strftime("%Y%m%d_%H%M%S")
    candidat, n = base, 1
    while (dossier / candidat).exists():
        n += 1
        candidat = f"{base}_{n}"
    return candidat


def _purger_temporaires(dossier: Path) -> None:
    """Supprime les dossiers `.<id>.tmp` laissés par un arrêt brutal.

    Appelé SOUS verrou : aucun autre écrivain n'existe, ces dossiers sont donc
    forcément des restes de copies interrompues (jamais une sauvegarde valide,
    qui n'est renommée qu'une fois complète).
    """
    for enfant in dossier.iterdir():
        if enfant.is_dir() and _MOTIF_TMP.match(enfant.name):
            logger.warning("[SAUVEGARDE] Reste d'une copie interrompue supprimé : %s", enfant.name)
            shutil.rmtree(enfant, ignore_errors=True)


def _ids_valides(dossier: Path) -> list[str]:
    """Ids de sauvegardes (dossiers au bon nom contenant un manifeste), du plus récent au plus ancien."""
    if not dossier.is_dir():
        return []
    ids = [
        e.name for e in dossier.iterdir()
        if e.is_dir() and _MOTIF_ID.match(e.name) and (e / NOM_MANIFESTE).is_file()
    ]
    return sorted(ids, reverse=True)


def _appliquer_retention(dossier: Path, garder: int) -> list[str]:
    """Supprime les sauvegardes au-delà de `garder` ; retourne les ids supprimés."""
    supprimes = []
    for ancien in _ids_valides(dossier)[garder:]:
        shutil.rmtree(dossier / ancien, ignore_errors=True)
        supprimes.append(ancien)
        logger.info("[SAUVEGARDE] Rétention : %s supprimée (on garde %d).", ancien, garder)
    return supprimes


# ── Création ───────────────────────────────────────────────────────────────

def creer_sauvegarde(
    *,
    racine: Path | None = None,
    dossier: Path | None = None,
    garder: int | None = None,
) -> dict:
    """
    Crée une sauvegarde complète et retourne son manifeste.

    Lève `SauvegardeEnCours` si une autre sauvegarde tient le verrou et
    `SauvegardeImpossible` si le dossier est inaccessible ou le disque trop plein.
    Une copie dont `integrity_check` n'est pas « ok » n'est PAS une erreur levée :
    elle est conservée, marquée `integrite: "ko"` dans le manifeste, et la
    rétention est alors suspendue (on ne remplace pas de bonnes copies par une
    mauvaise).
    """
    racine = Path(racine) if racine else racine_depot()
    dossier = Path(dossier) if dossier else dossier_sauvegardes(racine)
    garder = garder if garder is not None else retention()

    try:
        dossier.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SauvegardeImpossible(f"Dossier de sauvegarde inaccessible ({dossier}) : {exc}") from exc

    verrou = FileLock(str(dossier / NOM_VERROU), timeout=0)
    try:
        verrou.acquire()
    except Timeout as exc:
        raise SauvegardeEnCours("Une sauvegarde est déjà en cours.") from exc
    except OSError as exc:
        raise SauvegardeImpossible(f"Verrou de sauvegarde inutilisable ({dossier}) : {exc}") from exc

    try:
        return _creer_sous_verrou(racine, dossier, garder)
    finally:
        verrou.release()


def _creer_sous_verrou(racine: Path, dossier: Path, garder: int) -> dict:
    debut = time.perf_counter()
    _purger_temporaires(dossier)

    # ── Inventaire de ce qu'on va copier ──
    a_copier: list[tuple[Path, str, bool]] = []   # (source, nom dans la sauvegarde, est_sqlite)
    absents: list[str] = []
    exclus: list[str] = []

    def _ajouter(source: Path, nom: str, sqlite: bool) -> None:
        if est_fichier_secret(nom):
            exclus.append(nom)
            return
        a_copier.append((source, nom, sqlite))

    for nom in BASES_SQLITE:
        source = racine / nom
        if source.is_file():
            _ajouter(source, nom, True)
        else:
            absents.append(nom)
    for nom in FICHIERS_CONFIG:
        source = racine / nom
        if source.is_file():
            _ajouter(source, nom, False)
        else:
            absents.append(nom)
    for rel in DOSSIERS_COPIES:
        source_dossier = racine / rel
        if not source_dossier.is_dir():
            absents.append(rel + "/")
            continue
        for fichier in _fichiers_dossier(source_dossier):
            _ajouter(fichier, fichier.relative_to(racine).as_posix(), False)

    # ── Place disque : on refuse plutôt que de remplir le disque du moteur ──
    necessaire = sum(s.stat().st_size for s, _n, _q in a_copier)
    libre = shutil.disk_usage(dossier).free
    if libre < necessaire * 1.3 + 16 * 1024 * 1024:
        raise SauvegardeImpossible(
            f"Espace disque insuffisant dans {dossier} : {libre // (1024 * 1024)} Mo libres, "
            f"{necessaire // (1024 * 1024)} Mo à copier (marge de sécurité comprise)."
        )

    maintenant = datetime.now().astimezone()
    id_sauvegarde = _nouvel_id(dossier, maintenant)
    temporaire = dossier / f".{id_sauvegarde}.tmp"
    temporaire.mkdir()

    try:
        fichiers: list[dict] = []
        for source, nom, sqlite in a_copier:
            cible = temporaire / nom
            cible.parent.mkdir(parents=True, exist_ok=True)
            entree: dict = {"nom": nom}
            try:
                if sqlite:
                    _copier_base_sqlite(source, cible)
                    entree["integrite"] = _integrite_sqlite(cible)
                else:
                    shutil.copy2(source, cible)
                    entree["integrite"] = None
                entree["taille_octets"] = cible.stat().st_size
                entree["sha256"] = _sha256(cible)
            except (sqlite3.Error, OSError) as exc:
                # Un fichier qu'on n'a pas pu copier ne disparaît pas en silence :
                # il figure au manifeste, marqué en échec, avec la raison.
                logger.error("[SAUVEGARDE] Copie de %s en échec : %s", nom, exc)
                cible.unlink(missing_ok=True)   # pas de copie partielle qui ressemble à une vraie
                entree.update(integrite="ko", taille_octets=0, sha256=None, erreur=str(exc)[:300])
            fichiers.append(entree)

        manifeste = {
            "version_manifeste": VERSION_MANIFESTE,
            "id": id_sauvegarde,
            "cree_le": maintenant.isoformat(timespec="seconds"),
            "commit": _commit_moteur(),
            "duree_s": round(time.perf_counter() - debut, 2),
            "taille_octets": sum(f["taille_octets"] for f in fichiers),
            "fichiers": fichiers,
            "absents": absents,
            "exclus_secrets": exclus,
        }
        with open(temporaire / NOM_MANIFESTE, "w", encoding="utf-8") as f:
            json.dump(manifeste, f, ensure_ascii=False, indent=2)

        # Le dossier n'apparaît sous son vrai nom qu'une fois complet.
        os.replace(temporaire, dossier / id_sauvegarde)
    except BaseException:
        shutil.rmtree(temporaire, ignore_errors=True)
        raise

    sain = all(f["integrite"] != "ko" for f in fichiers)
    if sain:
        manifeste["supprimees_par_retention"] = _appliquer_retention(dossier, garder)
    else:
        logger.error(
            "[SAUVEGARDE] %s contient une copie en échec : rétention suspendue, anciennes copies conservées.",
            id_sauvegarde,
        )
        manifeste["supprimees_par_retention"] = []
    logger.info("[SAUVEGARDE] %s créée : %d fichier(s), %d octets, %.1f s.",
                id_sauvegarde, len(fichiers), manifeste["taille_octets"], manifeste["duree_s"])
    return manifeste


# ── Lecture ────────────────────────────────────────────────────────────────

def _lire_manifeste(dossier: Path, id_sauvegarde: str) -> dict | None:
    try:
        with open(dossier / id_sauvegarde / NOM_MANIFESTE, encoding="utf-8") as f:
            donnees = json.load(f)
        return donnees if isinstance(donnees, dict) else None
    except (OSError, ValueError):
        return None


def entree_api(manifeste: dict) -> dict:
    """Forme publique d'une sauvegarde (contrat de GET /api/sauvegardes) — sans empreintes."""
    return {
        "id": manifeste.get("id"),
        "cree_le": manifeste.get("cree_le"),
        "taille_octets": int(manifeste.get("taille_octets") or 0),
        "fichiers": [
            {
                "nom": f.get("nom"),
                "taille_octets": int(f.get("taille_octets") or 0),
                "integrite": f.get("integrite"),
            }
            for f in manifeste.get("fichiers", [])
        ],
        "commit": manifeste.get("commit"),
    }


def lister_sauvegardes(dossier: Path | None = None) -> list[dict]:
    """Sauvegardes présentes, de la plus récente à la plus ancienne (forme publique).

    Un dossier au bon nom mais au manifeste illisible est listé quand même (il
    occupe du disque et l'exploitant doit le voir) : `cree_le` vient alors de
    l'horodatage du dossier et `valide` vaut False.
    """
    dossier = Path(dossier) if dossier else dossier_sauvegardes()
    if not dossier.is_dir():
        return []
    resultat = []
    ids = sorted(
        (e.name for e in dossier.iterdir() if e.is_dir() and _MOTIF_ID.match(e.name)),
        reverse=True,
    )
    for id_sauvegarde in ids:
        manifeste = _lire_manifeste(dossier, id_sauvegarde)
        if manifeste is None:
            resultat.append({
                "id": id_sauvegarde,
                "cree_le": datetime.fromtimestamp((dossier / id_sauvegarde).stat().st_mtime)
                .astimezone().isoformat(timespec="seconds"),
                "taille_octets": 0, "fichiers": [], "commit": None, "valide": False,
            })
            continue
        manifeste.setdefault("id", id_sauvegarde)
        entree = entree_api(manifeste)
        entree["valide"] = True
        resultat.append(entree)
    return resultat


def etat_sauvegardes(dossier: Path | None = None) -> dict:
    """Corps de GET /api/sauvegardes."""
    dossier = Path(dossier) if dossier else dossier_sauvegardes()
    sauvegardes = lister_sauvegardes(dossier)
    valides = [s for s in sauvegardes if s.get("valide")]
    return {
        "dossier": str(dossier),
        "retention": retention(),
        "sauvegardes": sauvegardes,
        "derniere": valides[0]["cree_le"] if valides else None,
    }


def verifier_sauvegarde(id_sauvegarde: str, dossier: Path | None = None) -> dict:
    """
    Recontrôle une sauvegarde : empreinte sha256 de chaque fichier ET
    `integrity_check` des bases, comparés au manifeste.

    Retourne `{"id", "ok": bool, "fichiers": [{"nom", "sha256_ok", "integrite", "erreur"}]}`.
    Lève `ErreurSauvegarde` si l'id est invalide ou inconnu.
    """
    if not _MOTIF_ID.match(id_sauvegarde or ""):
        raise ErreurSauvegarde(f"Identifiant de sauvegarde invalide : {id_sauvegarde!r}.")
    dossier = Path(dossier) if dossier else dossier_sauvegardes()
    manifeste = _lire_manifeste(dossier, id_sauvegarde)
    if manifeste is None:
        raise ErreurSauvegarde(f"Sauvegarde {id_sauvegarde} introuvable ou manifeste illisible.")

    rapport = []
    tout_ok = True
    for entree in manifeste.get("fichiers", []):
        nom = entree.get("nom", "")
        chemin = dossier / id_sauvegarde / nom
        ligne: dict = {"nom": nom, "sha256_ok": None, "integrite": None, "erreur": None}
        if entree.get("integrite") == "ko" and not entree.get("sha256"):
            ligne["erreur"] = entree.get("erreur") or "copie en échec à la création"
            tout_ok = False
        elif not chemin.is_file():
            ligne["erreur"] = "fichier absent de la sauvegarde"
            tout_ok = False
        else:
            ligne["sha256_ok"] = _sha256(chemin) == entree.get("sha256")
            if nom.endswith(".db"):
                ligne["integrite"] = _integrite_sqlite(chemin)
            if ligne["sha256_ok"] is False:
                ligne["erreur"] = "empreinte sha256 différente du manifeste"
            if ligne["sha256_ok"] is False or ligne["integrite"] == "ko":
                tout_ok = False
        rapport.append(ligne)
    return {"id": id_sauvegarde, "ok": tout_ok, "fichiers": rapport}


# ── Ligne de commande ──────────────────────────────────────────────────────

def _octets(n: int) -> str:
    for unite in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unite == "Go":
            return f"{n:.0f} {unite}" if unite == "o" else f"{n:.1f} {unite}"
        n /= 1024
    return f"{n} o"  # pragma: no cover


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(
        prog="python -m core.sauvegarde",
        description="Sauvegarde à chaud des bases du moteur (sans argument : crée une sauvegarde).",
    )
    groupe = parseur.add_mutually_exclusive_group()
    groupe.add_argument("--lister", action="store_true", help="liste les sauvegardes présentes")
    groupe.add_argument("--verifier", metavar="ID", help="recontrôle sha256 + intégrité d'une sauvegarde")
    args = parseur.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    # Le .env du moteur peut définir MOTEUR_SAUVEGARDE_DIR : le charger (sans
    # écraser l'environnement) pour que la CLI et le serveur visent le même dossier.
    try:
        from core.env_bootstrap import bootstrap_env
        bootstrap_env()
    except Exception as exc:  # pragma: no cover - défensif
        logger.warning("[SAUVEGARDE] .env non chargé : %s", exc)

    try:
        if args.lister:
            etat = etat_sauvegardes()
            print(f"Dossier : {etat['dossier']}  (rétention : {etat['retention']})")
            if not etat["sauvegardes"]:
                print("Aucune sauvegarde.")
                return 0
            for s in etat["sauvegardes"]:
                ko = [f["nom"] for f in s["fichiers"] if f["integrite"] == "ko"]
                etat_txt = "INVALIDE (manifeste illisible)" if not s.get("valide") else (
                    "intégrité KO : " + ", ".join(ko) if ko else "ok"
                )
                print(f"{s['id']}  {s['cree_le']}  {_octets(s['taille_octets']):>9}  "
                      f"commit={s['commit'] or 'inconnu'}  {len(s['fichiers'])} fichier(s)  {etat_txt}")
            return 0

        if args.verifier:
            rapport = verifier_sauvegarde(args.verifier)
            for f in rapport["fichiers"]:
                marque = "ok" if not f["erreur"] and f["integrite"] != "ko" else "ECHEC"
                detail = f" — {f['erreur']}" if f["erreur"] else ""
                integ = f"  integrity_check={f['integrite']}" if f["integrite"] else ""
                print(f"[{marque}] {f['nom']}{integ}{detail}")
            print("Sauvegarde saine." if rapport["ok"] else "Sauvegarde ALTÉRÉE.")
            return 0 if rapport["ok"] else 1

        manifeste = creer_sauvegarde()
    except ErreurSauvegarde as exc:
        print(f"Échec : {exc}", file=sys.stderr)
        return 2

    ko = [f["nom"] for f in manifeste["fichiers"] if f["integrite"] == "ko"]
    print(f"Sauvegarde {manifeste['id']} : {len(manifeste['fichiers'])} fichier(s), "
          f"{_octets(manifeste['taille_octets'])}, commit {manifeste['commit'] or 'inconnu'}.")
    if manifeste["absents"]:
        print("Absents (non sauvegardés) : " + ", ".join(manifeste["absents"]))
    if ko:
        print("ANOMALIE — copie en échec ou intégrité KO : " + ", ".join(ko), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
