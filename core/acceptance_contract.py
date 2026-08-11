"""
core/acceptance_contract.py — Contrat d'acceptation vérifiable (#T253).

Aujourd'hui la boucle de revue (`core/review_loop.py`) s'arrête sur un
`quality_score` produit par un LLM reviewer : un jugement, pas une preuve. Un
modèle complaisant valide du code qui ne compile pas, un modèle sévère fait
boucler une tâche déjà correcte — et dans les deux cas rien n'est reproductible.

Idée reprise de l'« exécution pilotée par contrat » d'OpenFox (le README v2.0
parle de critères d'acceptation servant de contrat immuable, vérifiés
itérativement jusqu'à satisfaction) : le Planner exprime la réussite en
critères **mécaniquement vérifiables**, et c'est leur exécution — pas l'avis
d'un modèle — qui décide si le travail est fini.

Trois types, volontairement peu nombreux :
- `commande`        : la commande sort en code 0 (ex. `pytest tests/unit -q`) ;
- `fichier_contient`: le fichier existe et contient le motif attendu ;
- `fichier_existe`  : le fichier existe.

Sécurité — deux garde-fous, parce que ces critères sont écrits par un LLM et
exécutés **sans qu'un agent ne décide** :
1. seules des commandes de VÉRIFICATION sont exécutées (liste de préfixes
   ci-dessous) ; tout le reste est rapporté « non vérifiable », jamais lancé,
   et jamais compté comme satisfait ;
2. exécution sans shell (`shlex.split`, `shell=False`) : ni pipe, ni
   redirection, ni chaînage — un critère ne peut pas devenir un script.

Les chemins passent par le même résolveur d'espace de travail que les outils
d'édition (`tools.system._resolve_within_workspace`) : une seule politique de
frontière, pas deux. La lecture, elle, est faite en direct et NON via
`read_file`, dont le cache sémantique peut résumer le contenu — un `contient`
sur un texte résumé donnerait un faux négatif silencieux.
"""

import logging
import ntpath
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Clé sous laquelle le Planner transporte son contrat dans les metadata du StateUpdate.
CLE_CONTRAT = "contrat_acceptation"

TYPES_CONNUS = ("commande", "fichier_contient", "fichier_existe")

# Délai maximum d'une commande de vérification (une suite de tests peut être longue).
TIMEOUT_COMMANDE_S = 180

# Commandes de vérification autorisées, par préfixe de tokens. Tout ce qui
# n'est pas ici n'est pas exécuté — on ne "durcit" pas la liste au fil de l'eau
# sans y réfléchir : chaque ajout doit rester une commande qui CONSTATE un état,
# jamais une commande qui le modifie.
PREFIXES_AUTORISES: tuple[tuple[str, ...], ...] = (
    ("pytest",),
    ("python", "-m", "pytest"),
    ("python3", "-m", "pytest"),
    ("python", "-m", "py_compile"),
    ("python3", "-m", "py_compile"),
    ("python", "-m", "compileall"),
    ("python3", "-m", "compileall"),
    ("ruff", "check"),
    ("ruff", "format", "--check"),
    ("mypy",),
    ("esphome", "config"),
    ("node", "--check"),
    ("tsc",),
    ("npx", "tsc"),
    ("npm", "run"),
    ("npm", "test"),
    ("git", "status"),
    ("git", "diff"),
)


@dataclass(frozen=True)
class Critere:
    """Un critère d'acceptation, normalisé."""

    type: str
    valeur: str
    attendu: str | None = None
    description: str = ""

    def libelle(self) -> str:
        if self.description:
            return self.description
        if self.type == "commande":
            return f"la commande `{self.valeur}` réussit"
        if self.type == "fichier_contient":
            return f"`{self.valeur}` contient « {self.attendu} »"
        return f"`{self.valeur}` existe"


@dataclass(frozen=True)
class ResultatCritere:
    """Verdict d'un critère. `satisfait=None` = non vérifiable (ni vrai ni faux)."""

    critere: Critere
    satisfait: bool | None
    detail: str


@dataclass
class RapportContrat:
    """Verdict d'ensemble du contrat."""

    resultats: list[ResultatCritere] = field(default_factory=list)

    @property
    def verifiables(self) -> list[ResultatCritere]:
        return [r for r in self.resultats if r.satisfait is not None]

    @property
    def echecs(self) -> list[ResultatCritere]:
        return [r for r in self.resultats if r.satisfait is False]

    @property
    def non_verifiables(self) -> list[ResultatCritere]:
        return [r for r in self.resultats if r.satisfait is None]

    @property
    def determinable(self) -> bool:
        """
        Vrai si le contrat peut trancher. Un contrat vide, ou dont AUCUN critère
        n'est vérifiable, ne doit pas décider à la place de la revue LLM : dans
        ce cas l'appelant garde son comportement d'avant.
        """
        return bool(self.verifiables)

    @property
    def satisfait(self) -> bool:
        return self.determinable and not self.echecs

    def resume(self) -> str:
        """Compte rendu injectable dans le contexte de correction d'un agent."""
        lignes = []
        for r in self.resultats:
            marque = {True: "✅", False: "❌", None: "⚠️"}[r.satisfait]
            lignes.append(f"{marque} {r.critere.libelle()} — {r.detail}")
        entete = (
            f"CONTRAT D'ACCEPTATION : {len(self.echecs)} critère(s) en échec sur "
            f"{len(self.verifiables)} vérifiable(s)"
        )
        if self.non_verifiables:
            entete += f" ({len(self.non_verifiables)} non vérifiable(s), non comptés)"
        return entete + "\n" + "\n".join(lignes)


def normaliser_criteres(bruts) -> list[Critere]:
    """
    Convertit la sortie JSON du Planner en critères exploitables.

    Tolérant par nécessité (c'est un LLM qui écrit) mais jamais silencieux :
    une entrée inutilisable est journalisée et écartée, pas devinée.
    """
    if not bruts:
        return []
    if isinstance(bruts, dict):
        bruts = [bruts]
    if not isinstance(bruts, list):
        logger.warning(f"[CONTRAT] Critères ignorés : type inattendu {type(bruts).__name__}")
        return []

    criteres = []
    for brut in bruts:
        if not isinstance(brut, dict):
            logger.warning(f"[CONTRAT] Critère ignoré (pas un objet) : {brut!r}")
            continue
        type_critere = str(brut.get("type") or "").strip().lower()
        valeur = str(brut.get("valeur") or "").strip()
        if not valeur:
            logger.warning(f"[CONTRAT] Critère ignoré (valeur vide) : {brut!r}")
            continue
        if type_critere not in TYPES_CONNUS:
            # Type inconnu : gardé comme non vérifiable plutôt que jeté, pour
            # qu'il apparaisse dans le rapport au lieu de disparaître.
            logger.warning(f"[CONTRAT] Type de critère inconnu : {type_critere!r}")
        attendu = brut.get("attendu")
        criteres.append(Critere(
            type=type_critere,
            valeur=valeur,
            attendu=str(attendu) if attendu is not None else None,
            description=str(brut.get("description") or "").strip(),
        ))
    return criteres


def _commande_style_windows(commande: str) -> bool:
    """Vrai si la commande commence par un chemin absolu Windows (`C:\\...`).

    Nécessaire hors Windows aussi : la CI Linux doit reconnaître les binaires
    Windows dans la liste blanche sans que `shlex` POSIX mange les antislashs.
    """
    tete = commande.strip().lstrip("\"'")
    return len(tete) >= 3 and tete[0].isalpha() and tete[1] == ":" and tete[2] == "\\"


def _decouper(commande: str) -> list[str] | None:
    """
    Découpe une commande en arguments, `None` si elle est indécoupable.

    Sous Windows, `shlex.split` en mode POSIX traite l'antislash comme un
    caractère d'échappement : `C:\\Python\\python.exe` devient `C:Pythonpython.exe`,
    donc un chemin absolu ne serait ni reconnu par la liste blanche ni exécutable.
    On passe donc en mode non-POSIX là-bas (et pour toute commande au style
    Windows, même sous Linux), en retirant nous-mêmes les guillemets que ce mode
    conserve. La validation et l'exécution utilisent le MÊME découpage : on ne
    veut pas autoriser une commande et en lancer une autre.
    """
    try:
        if os.name == "nt" or _commande_style_windows(commande):
            tokens = [t.strip("\"'") for t in shlex.split(commande, posix=False)]
        else:
            tokens = shlex.split(commande)
    except ValueError:
        return None
    tokens = [t for t in tokens if t]
    return tokens or None


def commande_autorisee(commande: str) -> bool:
    """Vrai si la commande correspond à un préfixe de vérification autorisé."""
    tokens = _decouper(commande)
    if not tokens:
        return False
    tokens = list(tokens)
    # `python.exe` / chemins absolus : on compare sur le nom de base.
    # ntpath : même sous Linux, un chemin `C:\...\python.exe` doit donner `python`
    # (posixpath.basename laisserait le chemin entier intact).
    tokens[0] = ntpath.basename(tokens[0]).lower()
    if tokens[0].endswith(".exe"):
        tokens[0] = tokens[0][:-4]
    return any(
        len(tokens) >= len(prefixe) and tuple(tokens[:len(prefixe)]) == prefixe
        for prefixe in PREFIXES_AUTORISES
    )


def _verifier_commande(critere: Critere, racine: str | None) -> ResultatCritere:
    if not commande_autorisee(critere.valeur):
        return ResultatCritere(
            critere, None,
            "commande hors de la liste de vérification autorisée — non exécutée",
        )
    tokens = _decouper(critere.valeur)
    if not tokens:
        return ResultatCritere(critere, None, "commande indécoupable")
    try:
        proc = subprocess.run(
            tokens,
            cwd=racine or _racine_moteur(),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_COMMANDE_S,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return ResultatCritere(critere, False, f"dépassement du délai ({TIMEOUT_COMMANDE_S} s)")
    except FileNotFoundError:
        return ResultatCritere(critere, None, "exécutable introuvable sur cet hôte")
    except Exception as err:
        return ResultatCritere(critere, None, f"exécution impossible : {err}")

    if proc.returncode == 0:
        return ResultatCritere(critere, True, "code de retour 0")
    sortie = ((proc.stderr or "") + (proc.stdout or "")).strip().replace("\n", " ")
    return ResultatCritere(critere, False, f"code {proc.returncode} — {sortie[:400]}")


def _resoudre(chemin: str) -> tuple[str | None, str | None]:
    """Résout un chemin avec la politique d'espace de travail des outils d'édition."""
    try:
        from tools.system import _resolve_within_workspace
        return _resolve_within_workspace(chemin)
    except Exception as err:  # module indisponible : on ne devine pas de frontière
        return None, f"résolution de chemin impossible : {err}"


def _verifier_fichier(critere: Critere) -> ResultatCritere:
    resolu, erreur = _resoudre(critere.valeur)
    if erreur or not resolu:
        return ResultatCritere(critere, None, erreur or "chemin non résolu")

    if not os.path.isfile(resolu):
        return ResultatCritere(critere, False, "fichier absent")
    if critere.type == "fichier_existe":
        return ResultatCritere(critere, True, "fichier présent")

    if not critere.attendu:
        return ResultatCritere(critere, None, "aucun motif attendu fourni")
    try:
        # Lecture directe et non via read_file : son cache sémantique peut
        # résumer le contenu, ce qui produirait un faux négatif silencieux.
        with open(resolu, encoding="utf-8", errors="replace") as f:
            contenu = f.read()
    except Exception as err:
        return ResultatCritere(critere, None, f"lecture impossible : {err}")

    if critere.attendu in contenu:
        return ResultatCritere(critere, True, "motif trouvé")
    try:
        if re.search(critere.attendu, contenu):
            return ResultatCritere(critere, True, "motif trouvé (expression régulière)")
    except re.error:
        pass
    return ResultatCritere(critere, False, "motif absent du fichier")


def _racine_moteur() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def verifier_contrat(criteres, racine: str | None = None) -> RapportContrat:
    """
    Exécute tous les critères et retourne le verdict d'ensemble.

    Ne lève jamais : un critère qui explose devient « non vérifiable », parce
    qu'une exception ici bloquerait la boucle de revue au lieu de l'informer.
    """
    if criteres and isinstance(criteres[0], dict):
        criteres = normaliser_criteres(criteres)

    rapport = RapportContrat()
    for critere in criteres or []:
        if critere.type == "commande":
            rapport.resultats.append(_verifier_commande(critere, racine))
        elif critere.type in ("fichier_contient", "fichier_existe"):
            rapport.resultats.append(_verifier_fichier(critere))
        else:
            rapport.resultats.append(ResultatCritere(
                critere, None, f"type de critère inconnu : {critere.type!r}"
            ))

    if rapport.resultats:
        logger.info(
            f"[CONTRAT] {len(rapport.verifiables)} critère(s) vérifiable(s), "
            f"{len(rapport.echecs)} échec(s), {len(rapport.non_verifiables)} non vérifiable(s) "
            f"→ satisfait={rapport.satisfait}"
        )
    return rapport
