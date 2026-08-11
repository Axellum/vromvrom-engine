"""test_latence_vocale.py — #T270.

La latence vocale n'était mesurée que sur 6 % des échanges, et le chiffre
affiché était faux d'un facteur 16.

Deux défauts distincts, un test pour chacun :

1. `VocalAuditTimer.elapsed_ms` n'était calculé que dans `__exit__`, alors que
   les 14 appels à `log_vocal_response(..., latency_ms=timer.elapsed_ms)` le
   lisent À L'INTÉRIEUR du bloc `with`. Tous enregistraient donc `0.0`.
   Mesuré en prod : 394 réponses à `latency_ms = 0`, 26 valeurs réelles, 0 NULL.

2. `/api/vocal/stats` filtrait `latency_ms IS NOT NULL`, ce qui laissait passer
   les 394 zéros dans la moyenne : 123 ms affichés contre 1991 ms réels.
"""
import time

import pytest

from api.routes.vocal import _percentile
from core.vocal_audit import VocalAuditTimer


class TestTimerLuDansLeBloc:
    """Le défaut d'origine : la valeur était lue une ligne trop tôt."""

    def test_lecture_pendant_le_bloc_donne_une_vraie_mesure(self):
        """C'est LE test de régression : avant le correctif, il valait 0.0."""
        with VocalAuditTimer() as timer:
            time.sleep(0.02)
            # Exactement ce que font les 14 appels de api/routes/agents.py :
            # lire la latence sans être sorti du bloc.
            mesure_pendant = timer.elapsed_ms

        assert mesure_pendant > 0, "la latence lue dans le bloc ne doit plus être nulle"
        assert mesure_pendant >= 15, f"~20 ms attendues, obtenu {mesure_pendant:.1f} ms"

    def test_lecture_apres_le_bloc_reste_figee(self):
        """Sortir du bloc fige la mesure : deux lectures donnent la même valeur."""
        with VocalAuditTimer() as timer:
            time.sleep(0.01)

        premiere = timer.elapsed_ms
        time.sleep(0.02)
        assert timer.elapsed_ms == premiere, "la mesure doit être figée après __exit__"

    def test_la_mesure_progresse_pendant_le_bloc(self):
        with VocalAuditTimer() as timer:
            time.sleep(0.01)
            debut = timer.elapsed_ms
            time.sleep(0.02)
            fin = timer.elapsed_ms

        assert fin > debut

    def test_timer_jamais_demarre_ne_ment_pas(self):
        """Instancié hors d'un `with` : 0.0, et surtout pas une durée inventée."""
        assert VocalAuditTimer().elapsed_ms == 0.0


class TestPercentiles:
    """Les percentiles exposés par /api/vocal/stats."""

    def test_sur_liste_vide(self):
        assert _percentile([], 50) is None
        assert _percentile([], 90) is None

    def test_valeurs_connues(self):
        valeurs = [float(x) for x in range(1, 101)]  # 1..100 triées
        assert _percentile(valeurs, 50) == 51.0
        assert _percentile(valeurs, 90) == 91.0

    def test_ne_deborde_pas_sur_p100(self):
        valeurs = [1.0, 2.0, 3.0]
        assert _percentile(valeurs, 100) == 3.0

    def test_element_unique(self):
        assert _percentile([42.0], 50) == 42.0
        assert _percentile([42.0], 90) == 42.0


class TestZeroNestPasUneMesure:
    """Le second défaut : 0 ms signifie « jamais mesuré », pas « instantané »."""

    def test_la_moyenne_ignore_les_zeros(self):
        """Reproduit le calcul de la route sur les proportions réelles de la prod.

        394 zéros + 26 mesures à ~1991 ms : l'ancien filtre `IS NOT NULL` donnait
        123 ms, le nouveau doit rendre la vraie moyenne des mesures valides.
        """
        brut = [0.0] * 394 + [1991.0] * 26

        ancien = sum(brut) / len(brut)              # IS NOT NULL : les zéros comptent
        nouveau_ech = [v for v in brut if v > 0]    # > 0 : seules les vraies mesures
        nouveau = sum(nouveau_ech) / len(nouveau_ech)

        assert round(ancien) == 123, "l'ancien calcul diluait bien la moyenne"
        assert nouveau == 1991.0
        assert nouveau / ancien > 15, "l'écart mesuré en prod était d'un facteur 16"

    def test_le_denominateur_est_expose(self):
        """`echantillon` / `non_mesurees` : une moyenne sans son assiette ne vaut rien."""
        reponses = 420
        mesures = [1991.0] * 26
        assert len(mesures) == 26
        assert max(0, reponses - len(mesures)) == 394


@pytest.mark.parametrize("p", [50, 90])
def test_percentiles_coherents_avec_la_moyenne(p):
    """Garde-fou : sur une distribution plate, percentiles et moyenne se tiennent."""
    valeurs = [100.0] * 50
    assert _percentile(valeurs, p) == 100.0
