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

# [Incident du 17/08] Clé de l'état initial constaté des critères AU MOMENT où
# le plan est posé. Un critère déjà vrai AVANT que le lot ne commence n'est pas
# une preuve de travail : « vérifier qu'un fichier préexistant est accessible »
# est satisfait quoi que fasse le lot. Le porteur du constat peut être absent
# (plans antérieurs à ce mécanisme) — par défaut, un critère reste alors
# considéré comme une preuve possible : le doute ne doit jamais rendre le
# jugement plus permissif, seulement plus strict.
CLE_ETAT_INITIAL = "etat_initial_criteres"

# [Incident du 17/08] Clé des substitutions DÉCLARÉES par un plan correctif
# (un critère du plancher devenu impossible y est remplacé, de façon tracée,
# par un autre). Jamais de remplacement silencieux : toute substitution passe
# par une déclaration explicite, journalisée à l'évaluation.
CLE_SUBSTITUTIONS = "substitutions_acceptation"

TYPES_CONNUS = ("commande", "fichier_contient", "fichier_existe")

# Types de critères dont on peut constater l'état initial à moindre coût :
# les critères fichier. Les critères `commande` sont exclus du constat : une
# commande de vérification peut être longue (suite de tests) et la lancer une
# fois de plus avant chaque lot n'est pas acceptable.
TYPES_CONSTATABLES = ("fichier_contient", "fichier_existe")

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
    """Un critère d'acceptation, normalisé.

    `satisfait_d_avance` est posé à l'ÉVALUATION (jamais par le Planner) quand
    le constat d'état initial montre que le critère était déjà vrai avant le
    début du travail : il reste évalué et visible dans le rapport, mais il ne
    compte ni comme preuve ni comme verdict.
    """

    type: str
    valeur: str
    attendu: str | None = None
    description: str = ""
    satisfait_d_avance: bool = False

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
    def resultats_satisfaits_d_avance(self) -> list[ResultatCritere]:
        """Résultats dont le critère était déjà vrai avant le début du lot."""
        return [r for r in self.resultats if r.critere.satisfait_d_avance]

    @property
    def echecs_satisfaits_d_avance(self) -> list[ResultatCritere]:
        """
        Échecs parmi les critères satisfaits d'avance. Ils ne servent PAS de
        preuve (comme tout critère satisfait d'avance), mais ils BLOQUENT le
        verdict : un critère vrai avant le lot qui échoue maintenant est une
        régression, et elle doit rester visible dans le rapport.
        """
        return [r for r in self.echecs if r.critere.satisfait_d_avance]

    @property
    def verifiables(self) -> list[ResultatCritere]:
        """
        Résultats tranchés, HORS critères satisfaits d'avance : ceux-là sont
        vrais avant même que le lot ne commence et ne prouvent donc rien sur
        le travail accompli. Incident du 17/08 : un contrat correctif composé
        uniquement de critères satisfaits d'avance (des fichiers préexistants)
        avait absous un DAG en erreur.
        """
        return [
            r for r in self.resultats
            if r.satisfait is not None and not r.critere.satisfait_d_avance
        ]

    @property
    def echecs(self) -> list[ResultatCritere]:
        return [r for r in self.resultats if r.satisfait is False]

    @property
    def non_verifiables(self) -> list[ResultatCritere]:
        return [r for r in self.resultats if r.satisfait is None]

    @property
    def determinable(self) -> bool:
        """
        Vrai si le contrat peut trancher. Un contrat vide, dont AUCUN critère
        n'est vérifiable, ou dont les seuls critères vérifiables étaient
        satisfaits d'avance, ne doit pas décider à la place de la revue LLM :
        dans ces cas l'appelant garde son comportement d'avant. Conséquences
        directes : un contrat entièrement satisfait d'avance ne peut plus
        absoudre un DAG en erreur (rien n'y prouve le travail du lot) ; et un
        critère satisfait d'avance qui ÉCHOUE maintenant reste un échec —
        c'est une régression, et le doute doit toujours aller vers le strict.
        """
        return bool(self.verifiables)

    @property
    def satisfait(self) -> bool:
        """
        Le contrat est satisfait si au moins un critère PROUVE le travail
        (vérifiable et non satisfait d'avance) et qu'AUCUN critère n'échoue —
        y compris parmi les critères satisfaits d'avance : un critère vrai
        avant le lot qui échoue maintenant est une régression, pas une
        anecdote. Un critère satisfait d'avance ne peut donc JAMAIS servir de
        preuve, mais son échec peut toujours faire tomber le verdict.
        """
        return self.determinable and not self.echecs

    def resume(self) -> str:
        """Compte rendu injectable dans le contexte de correction d'un agent."""
        lignes = []
        for r in self.resultats:
            marque = {True: "✅", False: "❌", None: "⚠️"}[r.satisfait]
            if r.critere.satisfait_d_avance:
                marque += " (satisfait d'avance — exclu du verdict)"
            lignes.append(f"{marque} {r.critere.libelle()} — {r.detail}")
        entete = (
            f"CONTRAT D'ACCEPTATION : {len(self.echecs)} critère(s) en échec sur "
            f"{len(self.verifiables)} vérifiable(s)"
        )
        if self.resultats_satisfaits_d_avance:
            entete += f" ({len(self.resultats_satisfaits_d_avance)} satisfait(s) d'avance, non comptés)"
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
            # `satisfait_d_avance` n'est JAMAIS lu depuis l'entrée : c'est un
            # constat d'évaluation calculé depuis l'état initial, pas un champ
            # que le Planner (ou quiconque) peut s'attribuer à lui-même.
        ))
    return criteres


def cle_critere(critere) -> tuple:
    """
    Identité d'un critère pour la déduplication de l'union des contrats.

    La description n'en fait PAS partie : deux plans peuvent formuler la même
    exigence différemment, et c'est l'exigence (type + cible + motif) qui
    compte. En revanche `attendu` en fait partie : changer le motif attendu
    d'un `fichier_contient` change l'exigence elle-même — ce n'est pas un
    doublon, c'est une modification qui doit rester visible.
    """
    return (critere.type, critere.valeur, critere.attendu)


def union_contrats(listes_criteres: list[list]) -> list[dict]:
    """
    [Incident du 17/08] Union des contrats de l'historique.

    Un plan correctif peut ENRICHIR le contrat, jamais le remplacer ni
    l'affaiblir : chaque critère déjà posé est conservé. La déduplication
    s'appuie sur `cle_critere` ; la PREMIÈRE occurrence dans l'ordre
    chronologique gagne, pour préserver la formulation du contrat approuvé et
    son état initial constaté.

    L'ordre de la liste retournée place les critères du contrat le PLUS
    RÉCENT en tête, puis ceux des contrats plus anciens — l'ordre ne change
    rien au verdict (tous sont évalués), mais conserve la forme d'avant pour
    le premier élément et présente d'abord l'état visé par la dernière
    correction. Le PLANCHER, lui, ne se reconnaît pas à l'ordre de cette
    liste : c'est la première entrée de contrat de l'historique.

    Accepte des listes de dicts bruts (format des metadata), en ordre
    chronologique ; retourne des dicts au même format.
    """
    union: dict[tuple, tuple[int, dict]] = {}
    for position, liste in enumerate(listes_criteres):
        for brut in liste or []:
            normalises = normaliser_criteres(brut)
            if not normalises:
                continue
            cle = cle_critere(normalises[0])
            # setdefault : la première occurrence chronologique gagne, pour
            # préserver la formulation du contrat approuvé.
            union.setdefault(cle, (position, dict(brut)))
    # Contrat le plus récent d'abord ; au sein d'un même contrat, l'ordre
    # chronologique d'origine est conservé.
    return [brut for _position, brut in sorted(union.values(), key=lambda p: -p[0])]


def constater_etat_initial(criteres_bruts) -> dict[int, bool]:
    """
    [Incident du 17/08] Constate l'état des critères fichier AU MOMENT où le
    plan est posé, AVANT tout travail du lot.

    Retourne `{index du critère: vrai si déjà satisfait}`. Un critère absent
    du constat (type non constatable, résolution impossible…) n'est PAS
    considéré satisfait d'avance : le doute doit rendre le jugement plus
    strict, jamais plus permissif.

    Appelée par le Planner à la pose du plan et par le service de reprise à la
    restauration du contrat approuvé — dans les deux cas à un instant qui
    précède l'exécution, ce qui fait du constat une photographie d'avant-travail.

    Accepte des dicts bruts ou des `Critere` déjà normalisés (le Planner vient
    de normaliser : on ne re-normalise pas deux fois, les index doivent
    correspondre terme à terme à la liste évaluée plus tard).
    """
    normalises = (
        criteres_bruts
        if criteres_bruts and isinstance(criteres_bruts[0], Critere)
        else normaliser_criteres(criteres_bruts)
    )
    etats: dict[int, bool] = {}
    for index, critere in enumerate(normalises):
        if critere.type not in TYPES_CONSTATABLES:
            continue
        resultat = _verifier_fichier(critere)
        etats[index] = resultat.satisfait is True
    return etats


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


def _marquer_si_satisfait_d_avance(
    index: int, critere: Critere, resultat: ResultatCritere,
    etats_initiaux: dict[int, bool] | None,
) -> ResultatCritere:
    """
    Si le constat d'état initial dit que ce critère était déjà vrai avant le
    lot, retourne un résultat portant un critère marqué `satisfait_d_avance`
    (le verdict réel et le détail sont conservés pour le rapport et le journal).
    """
    if etats_initiaux and etats_initiaux.get(index) and not critere.satisfait_d_avance:
        marque = Critere(
            type=critere.type, valeur=critere.valeur, attendu=critere.attendu,
            description=critere.description, satisfait_d_avance=True,
        )
        return ResultatCritere(
            marque, resultat.satisfait,
            f"{resultat.detail} — constaté satisfait AVANT le début du travail",
        )
    return resultat


def verifier_contrat(
    criteres,
    racine: str | None = None,
    etats_initiaux: dict[int, bool] | None = None,
) -> RapportContrat:
    """
    Exécute tous les critères et retourne le verdict d'ensemble.

    `etats_initiaux` (facultatif) : constat `{index: déjà satisfait}` posé à
    la soumission du plan. Les critères déjà vrais à cet instant sont marqués
    « satisfaits d'avance » : évalués et visibles, mais exclus du verdict —
    un critère vrai avant le travail n'est pas une preuve de travail.

    Ne lève jamais : un critère qui explose devient « non vérifiable », parce
    qu'une exception ici bloquerait la boucle de revue au lieu de l'informer.
    """
    if criteres and isinstance(criteres[0], dict):
        criteres = normaliser_criteres(criteres)

    rapport = RapportContrat()
    for index, critere in enumerate(criteres or []):
        if critere.type == "commande":
            resultat = _verifier_commande(critere, racine)
        elif critere.type in ("fichier_contient", "fichier_existe"):
            resultat = _verifier_fichier(critere)
        else:
            resultat = ResultatCritere(
                critere, None, f"type de critère inconnu : {critere.type!r}"
            )
        rapport.resultats.append(
            _marquer_si_satisfait_d_avance(index, critere, resultat, etats_initiaux)
        )

    if rapport.resultats:
        exclus = len(rapport.resultats_satisfaits_d_avance)
        note = f", {exclus} satisfait(s) d'avance (exclu(s) du verdict)" if exclus else ""
        if rapport.echecs_satisfaits_d_avance:
            # Un critère vrai avant le lot qui échoue maintenant : régression.
            # Elle bloque le verdict — le doute va toujours vers le strict.
            note += f", dont {len(rapport.echecs_satisfaits_d_avance)} en RÉGRESSION (bloquant(s))"
        logger.info(
            f"[CONTRAT] {len(rapport.verifiables)} critère(s) vérifiable(s), "
            f"{len(rapport.echecs)} échec(s), {len(rapport.non_verifiables)} non vérifiable(s)"
            f"{note} → satisfait={rapport.satisfait}"
        )
    return rapport
