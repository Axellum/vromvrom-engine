"""
tests/unit/test_dreamcoder_parallele.py — Drainage parallèle du backlog (#T387).

Trois garde-fous portent le parallélisme, et chacun corrige un défaut qui ne se
voyait pas tant que le drainage était séquentiel :

1. Un CLONE par slot — deux `git checkout` concurrents dans un même dépôt se
   marchent dessus, et le `git add -A` de l'un ramasse le travail de l'autre.
2. Une RÉSERVATION atomique — `get_next_task()` ne fait qu'un SELECT ; trois
   travailleurs y lisent la même ligne 'pending' et jouent la même tâche.
3. Un PROVIDER distinct par tâche en vol — sinon un même quota gratuit est
   consommé trois fois plus vite et rend ses 429 trois fois plus tôt.

S'y ajoute la sonde de santé : « la clé est présente » ne veut pas dire « le
provider peut inférer » (cerebras-free rend 402, claude-cli-abo rend « Credit
balance is too low » comme s'il s'agissait d'une réponse normale).
"""

import asyncio
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.dreamer_agent import _ENGINE_ROOT, _slots_dreamcoder
from core.runtime_db import get_db_path, override_db_path
from core.sante_providers import (
    MODELE_PAR_PROVIDER,
    _texte_de_reponse,
    providers_sains,
    reponse_valide,
    sonder_providers,
    sonder_un,
)

# ── 1. Isolation par clone ───────────────────────────────────────────────────

def test_slots_multiples():
    slots = _slots_dreamcoder({"dreamcoder_repo_paths": ["/a", "/b", "/c"]})
    assert len(slots) == 3


def test_un_clone_ne_peut_pas_servir_deux_slots():
    """Le même chemin deux fois, c'est deux tâches dans le même index Git."""
    slots = _slots_dreamcoder({"dreamcoder_repo_paths": ["/a", "/a", "/b"]})
    assert len(slots) == 2


def test_repli_sur_le_slot_unique_historique():
    """Sans `repo_paths`, le comportement d'avant le parallélisme est conservé."""
    assert _slots_dreamcoder({"dreamcoder_repo_path": "/solo"})[0].endswith("solo")
    assert _slots_dreamcoder({}) == [_ENGINE_ROOT]
    assert _slots_dreamcoder(None) == [_ENGINE_ROOT]


def test_une_chaine_seule_est_acceptee():
    """Tolérance de config : une chaîne au lieu d'une liste ne doit pas exploser."""
    assert len(_slots_dreamcoder({"dreamcoder_repo_paths": "/unique"})) == 1


# ── 2. Réservation atomique de la tâche ──────────────────────────────────────

@pytest.fixture
def db_ephemere(tmp_path):
    ancien = get_db_path()
    override_db_path(str(tmp_path / "backlog_test.db"))
    yield
    override_db_path(ancien)


@pytest.fixture(autouse=True)
def _verrous_neufs_par_test():
    """
    `_db_write_lock` est un asyncio.Lock de module : il se lie à la première
    boucle qui l'utilise, alors que pytest-asyncio en donne une neuve par test.
    """
    import core.backlog_db as backlog_db

    ancien = backlog_db._db_write_lock
    backlog_db._db_write_lock = asyncio.Lock()
    yield
    backlog_db._db_write_lock = ancien


@pytest.mark.asyncio
async def test_deux_travailleurs_ne_prennent_jamais_la_meme_tache(db_ephemere):
    """
    Le cœur du parallélisme. Avec `get_next_task()` (SELECT seul), les deux
    appels rendent la même ligne : deux clones jouent la même tâche pour un
    seul résultat conservé.
    """
    from core.backlog_db import add_task, init_backlog_db, reserver_prochaine_tache

    await init_backlog_db()
    await add_task("Tâche A", "description A", 1, None)
    await add_task("Tâche B", "description B", 2, None)

    premiere, seconde = await asyncio.gather(
        reserver_prochaine_tache(), reserver_prochaine_tache())

    assert premiere is not None and seconde is not None
    assert premiere["id"] != seconde["id"], "deux travailleurs ont pris la même tâche"
    assert {premiere["title"], seconde["title"]} == {"Tâche A", "Tâche B"}


@pytest.mark.asyncio
async def test_la_reservation_sort_la_tache_du_vivier(db_ephemere):
    """Une tâche réservée passe 'running' : le travailleur suivant ne la revoit pas."""
    from core.backlog_db import add_task, init_backlog_db, reserver_prochaine_tache

    await init_backlog_db()
    await add_task("Seule tâche", "d", 1, None)

    premiere = await reserver_prochaine_tache()
    seconde = await reserver_prochaine_tache()

    assert premiere["status"] == "pending"      # état AVANT réservation
    assert seconde is None                       # plus rien à prendre


@pytest.mark.asyncio
async def test_backlog_vide_rend_none(db_ephemere):
    from core.backlog_db import init_backlog_db, reserver_prochaine_tache

    await init_backlog_db()
    assert await reserver_prochaine_tache() is None


@pytest.mark.asyncio
async def test_la_priorite_est_respectee_en_parallele(db_ephemere):
    """La P1 doit partir en premier, même quand plusieurs slots piochent."""
    from core.backlog_db import add_task, init_backlog_db, reserver_prochaine_tache

    await init_backlog_db()
    await add_task("Basse", "d", 3, None)
    await add_task("Haute", "d", 1, None)

    assert (await reserver_prochaine_tache())["title"] == "Haute"


@pytest.mark.asyncio
async def test_reserver_exclut_les_taches_mises_de_cote(db_ephemere):
    """
    Une tâche bloquée par l'historique (réservée puis libérée faute de
    provider) ne doit pas être reprise par la pioche du même drainage :
    sans l'exclusion, le travailleur la ré-réserverait sans fin.
    """
    from core.backlog_db import add_task, init_backlog_db, reserver_prochaine_tache

    await init_backlog_db()
    id_bloquee = await add_task("Bloquée", "d", 1, None)  # P1 : partirait en premier
    await add_task("Jouable", "d", 2, None)

    tache = await reserver_prochaine_tache(exclure_ids={id_bloquee})

    assert tache["title"] == "Jouable"
    # Il ne reste que la bloquée : elle est exclue → rien à prendre.
    assert await reserver_prochaine_tache(exclure_ids={id_bloquee}) is None


@pytest.mark.asyncio
async def test_liberer_tache_remet_en_attente_sans_bruler_de_retries(db_ephemere):
    """Une tâche libérée revient 'pending' avec ses compteurs intacts."""
    from core.backlog_db import (
        add_task,
        get_all_tasks,
        init_backlog_db,
        liberer_tache,
        reserver_prochaine_tache,
    )

    await init_backlog_db()
    id_tache = await add_task("Réservée puis libérée", "d", 1, None)

    await reserver_prochaine_tache()
    assert await liberer_tache(id_tache) is True

    (tache,) = await get_all_tasks()
    assert tache["status"] == "pending"
    assert tache["retries"] == 0


# ── 3. Un provider distinct par tâche en vol ─────────────────────────────────

async def _garde_sur_base_neuve():
    """
    BudgetGuard sur une base réelle mais vide — le schéma est créé par
    `initialize()`. Neutraliser cette méthode laisserait `billing_history`
    absente : le worktree de test n'a pas de bases seedées.
    """
    from core.budget_guard import BudgetGuard

    bg = BudgetGuard()
    bg.db_path = get_db_path()
    await bg.initialize()
    return bg


@pytest.mark.asyncio
async def test_budget_guard_ecarte_les_providers_non_sains(db_ephemere):
    """
    Un provider absent de `sains` n'est jamais élu, même clé présente. C'est
    exactement le cas de cerebras-free : clé valide, HTTP 402 à l'appel.
    """
    bg = await _garde_sur_base_neuve()
    with patch.dict("os.environ", {"CEREBRAS_API_KEY": "x", "MISTRAL_API_KEY": "x"}), \
         patch.object(bg, "_check_gemini_cli_availability", return_value=False), \
         patch.object(bg, "_check_claude_cli_availability", return_value=False):
        choisi = await bg.get_available_provider(sains={"mistral-free"})

    assert choisi == "mistral-free"


@pytest.mark.asyncio
async def test_budget_guard_ecarte_les_providers_deja_en_vol(db_ephemere):
    """Le provider tenu par un autre slot n'est pas re-servi au suivant."""
    bg = await _garde_sur_base_neuve()
    with patch.dict("os.environ", {"MISTRAL_API_KEY": "x", "COHERE_API_KEY": "x"}), \
         patch.object(bg, "_check_gemini_cli_availability", return_value=False), \
         patch.object(bg, "_check_claude_cli_availability", return_value=False):
        choisi = await bg.get_available_provider(
            sains={"mistral-free", "cohere-free"}, exclus={"mistral-free"})

    assert choisi == "cohere-free"


# ── 4. La sonde de santé ─────────────────────────────────────────────────────

def test_reponse_en_dict_est_lue():
    """`open-mistral-nemo` rend le message brut, pas une chaîne."""
    assert _texte_de_reponse({"role": "assistant", "content": "PRET", "tool_calls": None}) == "PRET"
    assert _texte_de_reponse("PRET") == "PRET"
    assert _texte_de_reponse(None) == ""


def test_accent_ne_tue_pas_un_provider():
    assert reponse_valide("PRÊT") is True
    assert reponse_valide("prêt") is True


def test_message_d_erreur_rendu_comme_une_reponse_est_rejete():
    """
    Le cas claude-cli-abo : l'appel réussit, rend un texte, et ce texte est une
    erreur. Un contrôle qui se fierait au code de retour le dirait sain.
    """
    assert reponse_valide("Credit balance is too low") is False
    assert reponse_valide("") is False
    assert reponse_valide({"role": "assistant", "content": "", "tool_calls": None}) is False


@pytest.mark.asyncio
async def test_provider_absent_du_catalogue():
    gateway = MagicMock()
    gateway.providers = {}
    res = await sonder_un(gateway, "mistral-free")
    assert res["sain"] is False
    assert "absent du catalogue" in res["motif"]


@pytest.mark.asyncio
async def test_provider_inconnu():
    res = await sonder_un(MagicMock(), "provider-imaginaire")
    assert res["sain"] is False
    assert res["motif"] == "provider inconnu"


@pytest.mark.asyncio
async def test_sonde_absorbe_les_erreurs_reseau():
    """Une exception de transport écarte le provider, elle ne remonte pas."""
    instance = MagicMock()
    instance.generate_async = AsyncMock(side_effect=RuntimeError("402 Payment Required"))
    gateway = MagicMock()
    gateway.providers = {MODELE_PAR_PROVIDER["cerebras-free"]: instance}

    res = await sonder_un(gateway, "cerebras-free")
    assert res["sain"] is False
    assert "402" in res["motif"]


@pytest.mark.asyncio
async def test_sonde_coupe_un_provider_qui_ne_repond_pas():
    async def _interminable(*a, **kw):
        await asyncio.sleep(30)

    instance = MagicMock()
    instance.generate_async = _interminable
    gateway = MagicMock()
    gateway.providers = {MODELE_PAR_PROVIDER["gemini-free"]: instance}

    res = await sonder_un(gateway, "gemini-free", timeout_s=0.05)
    assert res["sain"] is False
    assert "timeout" in res["motif"]


@pytest.mark.asyncio
async def test_sonde_globale_trie_le_sain_du_casse():
    bon = MagicMock()
    bon.generate_async = AsyncMock(return_value="PRET")
    sans_credit = MagicMock()
    sans_credit.generate_async = AsyncMock(return_value="Credit balance is too low")
    casse = MagicMock()
    casse.generate_async = AsyncMock(side_effect=RuntimeError("HTTP 402"))

    gateway = MagicMock()
    gateway.providers = {
        MODELE_PAR_PROVIDER["mistral-free"]: bon,
        MODELE_PAR_PROVIDER["claude-cli-abo"]: sans_credit,
        MODELE_PAR_PROVIDER["cerebras-free"]: casse,
    }

    detail = await sonder_providers(
        ["mistral-free", "claude-cli-abo", "cerebras-free"], gateway=gateway)

    assert providers_sains(detail) == {"mistral-free"}
    assert "Credit balance" in detail["claude-cli-abo"]["motif"]


@pytest.mark.asyncio
async def test_sonde_sans_candidat():
    assert await sonder_providers([], gateway=MagicMock()) == {}


def test_la_table_des_modeles_est_partagee():
    """
    La sonde et l'exécution doivent viser le MÊME modèle : sonder un modèle et
    en jouer un autre validerait une chose pour en lancer une autre.
    """
    import agents.dreamer_agent as da

    source = (da.__file__ and open(da.__file__, encoding="utf-8").read()) or ""
    assert "executor_model_map = {" not in source, "copie locale de la table réapparue"
    assert "MODELE_PAR_PROVIDER" in source


# ── [#T392] Un slot repart toujours de sa base ───────────────────────────────

@pytest.mark.asyncio
async def test_un_slot_reste_sur_une_branche_de_tache_est_ramene(tmp_path):
    """
    Un slot resté sur une `task/*` d'un cycle précédent — cycle tué, ou clone
    inspecté à la main — faisait partir toutes les tâches suivantes de ce
    travail-là. Mesuré : « la branche task/17_… est identique à task/13_… ».
    """
    from agents.dreamer_agent import BASE_SLOT, _preparer_slot

    depot = tmp_path / "slot"
    depot.mkdir()

    def git(*args):
        return subprocess.run(["git"] + list(args), cwd=str(depot),
                              capture_output=True, text=True)

    git("init", "-b", BASE_SLOT)
    git("config", "user.email", "t@local")
    git("config", "user.name", "T")
    (depot / "base.txt").write_text("base", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "base")
    # Le slot est laissé sur une branche de tâche, comme après un cycle interrompu.
    git("checkout", "-b", "task/13_1787052642")

    base = await _preparer_slot(str(depot), rafraichir=False)

    assert base == BASE_SLOT
    courante = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert courante == BASE_SLOT


@pytest.mark.asyncio
async def test_le_travail_non_merge_n_est_pas_detruit(tmp_path):
    """Quitter une branche ne l'efface pas : son commit reste récupérable."""
    from agents.dreamer_agent import _preparer_slot

    depot = tmp_path / "slot"
    depot.mkdir()

    def git(*args):
        return subprocess.run(["git"] + list(args), cwd=str(depot),
                              capture_output=True, text=True)

    git("init", "-b", "master")
    git("config", "user.email", "t@local")
    git("config", "user.name", "T")
    (depot / "base.txt").write_text("base", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "base")
    git("checkout", "-b", "task/99_1787000000")
    (depot / "travail.txt").write_text("livrable", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "travail de l'agent")

    await _preparer_slot(str(depot), rafraichir=False)

    branches = git("branch", "--format=%(refname:short)").stdout
    assert "task/99_1787000000" in branches
    contenu = git("show", "task/99_1787000000:travail.txt").stdout
    assert "livrable" in contenu
