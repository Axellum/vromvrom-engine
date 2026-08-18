"""test_vocal_audit_couverture.py — #T369.

Verdict sur « 73 % des latences vocales ne sont pas enregistrées ».

Ce test verrouille ce qui peut l'être SANS la base de production (elle est sur
le Steam Deck) :

1. Le comportement du timer `VocalAuditTimer` (déjà couvert par
   test_latence_vocale.py, re-vérifié ici pour la couverture) :
   - lu À L'INTÉRIEUR du bloc `with` → valeur strictement positive ;
   - lu APRÈS le bloc → total figé (deux lectures identiques) ;
   - jamais démarré (instancié hors `with`) → 0.0, jamais une durée inventée.

2. La COUVERTURE des chemins d'écriture dans `vocal_audit_log` : chaque appel
   à `log_vocal_response(..., latency_ms=...)` dans `api/routes/agents.py` et
   `api/routes/streaming.py` doit être précédé d'un `with VocalAuditTimer()`
   et lire `timer.elapsed_ms`. Un chemin qui écrirait une réponse vocale SANS
   timer (ou en lisant une valeur non mesurée) est le cas le plus grave : il
   enregistrerait `latency_ms = 0` même après #T270.

   L'analyse est STATIQUE (lecture du code source) : elle ne dépend ni du
   réseau ni de la base. Elle échoue si un futur chemin ajoute un
   `log_vocal_response` sans timer, ou lit une latence qui n'est pas
   `timer.elapsed_ms`.
"""
import re
import time
from pathlib import Path

import pytest

from core.vocal_audit import VocalAuditTimer

# Racine du dépôt : remonte depuis tests/unit/ jusqu'à la racine du worktree.
_RACINE = Path(__file__).resolve().parents[2]

# Fichiers qui écrivent dans vocal_audit_log via log_vocal_response.
_FICHIERS_ECRITURE = [
    _RACINE / "api" / "routes" / "agents.py",
    _RACINE / "api" / "routes" / "streaming.py",
]


class TestComportementTimer:
    """Les trois invariants du timer, verrouillés pour la couverture."""

    def test_lu_dans_le_bloc_donne_une_valeur_positive(self):
        """C'est LE point de #T270 : lire pendant le bloc rend une vraie mesure."""
        with VocalAuditTimer() as timer:
            time.sleep(0.02)
            mesure = timer.elapsed_ms

        assert mesure > 0, "la latence lue dans le bloc ne doit plus être nulle"

    def test_lu_apres_le_bloc_reste_figee(self):
        """Sortir du bloc fige le total : deux lectures donnent la même valeur."""
        with VocalAuditTimer() as timer:
            time.sleep(0.01)

        premiere = timer.elapsed_ms
        time.sleep(0.02)
        assert timer.elapsed_ms == premiere, "la mesure doit être figée après __exit__"

    def test_timer_jamais_demarre_rend_zero(self):
        """Instancié hors d'un `with` : 0.0, et surtout pas une durée inventée."""
        assert VocalAuditTimer().elapsed_ms == 0.0


class TestCouvertureCheminsEcriture:
    """Aucun chemin d'écriture dans vocal_audit_log ne doit être sans timer.

    Un `log_vocal_response` qui ne lit pas `timer.elapsed_ms` (ou qui n'a pas
    de timer du tout) enregistrerait `latency_ms = 0` même après #T270 : c'est
    le cas le plus grave, celui qu'il faut interdire par construction.
    """

    @pytest.mark.parametrize("fichier", _FICHIERS_ECRITURE)
    def test_chaque_log_vocal_response_a_un_timer(self, fichier):
        """Chaque appel log_vocal_response est précédé d'un with VocalAuditTimer()."""
        source = fichier.read_text(encoding="utf-8")

        # Découper le fichier en blocs de code (indentation 0) pour associer
        # chaque appel à son contexte.
        blocs = _decouper_blocs(source)

        appels = [b for b in blocs if "log_vocal_response(" in b]
        assert appels, f"{fichier.name} doit contenir au moins un log_vocal_response"

        for bloc in appels:
            # Le bloc doit contenir un `with VocalAuditTimer() as timer:`.
            assert re.search(
                r"with\s+VocalAuditTimer\(\)\s+as\s+timer\s*:",
                bloc,
            ), (
                f"{fichier.name} : un log_vocal_response n'est pas dans un "
                "`with VocalAuditTimer() as timer:` — il enregistrerait "
                "latency_ms = 0 même après #T270."
            )
            # Et la latence lue doit être `timer.elapsed_ms`.
            assert "latency_ms=timer.elapsed_ms" in bloc, (
                f"{fichier.name} : un log_vocal_response ne lit pas "
                "`timer.elapsed_ms` — la latence ne serait pas mesurée."
            )

    def test_les_routing_types_attendus_sont_emiss(self):
        """Les routing_type du trafic vocal réel sont bien écrits par les deux fichiers.

        `discussion_chat` (chemin bufferisé, agents.py) et
        `discussion_chat_stream` (chemin streamé, streaming.py) sont deux
        routing_type distincts, écrits par deux fichiers distincts.
        """
        agents = (_RACINE / "api" / "routes" / "agents.py").read_text(encoding="utf-8")
        streaming = (_RACINE / "api" / "routes" / "streaming.py").read_text(encoding="utf-8")

        # Chemin bufferisé : agents.py écrit le routing_type du host_result,
        # qui vaut "discussion_chat" pour le chat (voir core/vocal_host.py).
        assert "discussion_chat" in agents
        # Chemin streamé : streaming.py écrit explicitement "discussion_chat_stream".
        assert 'routing_type="discussion_chat_stream"' in streaming

    def test_aucun_chemin_sans_timer_dans_les_fichiers(self):
        """Aucun `VocalAuditTimer()` instancié hors d'un `with` dans les fichiers."""
        for fichier in _FICHIERS_ECRITURE:
            source = fichier.read_text(encoding="utf-8")
            # Chaque occurrence de VocalAuditTimer() doit être précédée de `with `.
            for m in re.finditer(r"VocalAuditTimer\(\)", source):
                debut = max(0, m.start() - 10)
                contexte = source[debut:m.start()]
                assert "with " in contexte, (
                    f"{fichier.name} : VocalAuditTimer() instancié hors d'un `with` "
                    "— ce timer ne serait jamais démarré et rendrait 0.0."
                )


def _decouper_blocs(source: str) -> list[str]:
    """Découpe un fichier Python en blocs de code au niveau d'indentation 0.

    Chaque bloc commence à une ligne d'indentation 0 et s'étend jusqu'à la
    ligne d'indentation 0 suivante. Cela permet d'associer un appel
    `log_vocal_response` au `with VocalAuditTimer()` qui l'englobe.
    """
    lignes = source.splitlines()
    blocs: list[str] = []
    debut = 0
    for i, ligne in enumerate(lignes):
        if i > 0 and ligne.strip() and not ligne.startswith((" ", "\t")):
            blocs.append("\n".join(lignes[debut:i]))
            debut = i
    blocs.append("\n".join(lignes[debut:]))
    return blocs
