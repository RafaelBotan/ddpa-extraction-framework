"""Tests do wrapper intervalo_vigilancia_ccr_l1_v4 (DDPA target derivado)."""
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PILOTO = os.path.dirname(_HERE)
if _PILOTO not in sys.path:
    sys.path.insert(0, _PILOTO)

from intervalo_vigilancia_ccr_l1_v4 import detect_intervalo_vigilancia_ccr


class TestVigilanceAccepted:
    def test_3_anos_rastreio(self):
        r = detect_intervalo_vigilancia_ccr(
            'CONCLUSAO: recomenda-se novo controle colonoscopico em 3 anos para rastreio.'
        )
        assert r.value == '3_anos'
        assert r.meta['target_intent'] == 'vigilancia'

    def test_1_ano_controle(self):
        r = detect_intervalo_vigilancia_ccr(
            'CONCLUSAO: polipectomia. obs: sugerimos controle em 1 ano.'
        )
        assert r.value == '1_ano'
        assert r.meta['target_intent'] == 'vigilancia'


class TestTherapeuticExcluded:
    def test_apc_argonio(self):
        r = detect_intervalo_vigilancia_ccr(
            'CONCLUSAO: angiodisplasias no ceco. sugere-se nova sessao de argonio em 4 semanas.'
        )
        assert r.value is None
        assert r.status == 'excluded_therapeutic_session'
        assert r.evidence_class == 'policy_exclusion'
        # exclusion_match pode ser 'nova sessao' OU 'argonio' — qualquer marker terapêutico basta
        assert r.meta['exclusion_match'] is not None

    def test_mucosectomia(self):
        r = detect_intervalo_vigilancia_ccr(
            'CONCLUSAO: lesao em ceco. programar mucosectomia em 30 dias.'
        )
        assert r.value is None
        assert r.status == 'excluded_therapeutic_session'

    def test_dilatacao(self):
        r = detect_intervalo_vigilancia_ccr(
            'CONCLUSAO: estenose. sugere-se nova dilatacao em 3 meses.'
        )
        assert r.value is None
        assert r.status == 'excluded_therapeutic_session'


class TestRemakeExcluded:
    def test_preparo_ruim(self):
        r = detect_intervalo_vigilancia_ccr(
            'CONCLUSAO: preparo ruim. sugere-se repetir exame em 3 meses com melhor preparo.'
        )
        assert r.value is None
        assert r.status == 'excluded_bad_prep_remake'

    def test_precocemente(self):
        r = detect_intervalo_vigilancia_ccr(
            'CONCLUSAO: mucosa visualizada sem alteracoes. recomenda-se que a colonoscopia de controle '
            'seja realizada mais precocemente com melhor preparo em 1 ano.'
        )
        assert r.value is None
        assert r.status == 'excluded_bad_prep_remake'


class TestDiagnosticExcluded:
    def test_colono_virtual_complementacao(self):
        r = detect_intervalo_vigilancia_ccr(
            'CONCLUSAO: exame incompleto. sugere-se complementacao com colonoscopia virtual em 30 dias.'
        )
        assert r.value is None
        assert r.status == 'excluded_diagnostic_completion'

    def test_prosseguir_investigacao(self):
        r = detect_intervalo_vigilancia_ccr(
            'CONCLUSAO: sugere-se prosseguir investigacao do intestino delgado em 30 dias.'
        )
        assert r.value is None
        assert r.status == 'excluded_diagnostic_completion'


class TestSilencePropagated:
    def test_normal_exam(self):
        r = detect_intervalo_vigilancia_ccr('CONCLUSAO: exame normal.')
        assert r.value is None
        assert r.status == 'no_match'
        assert r.meta['target_intent'] == 'propagated_silence'

    def test_empty(self):
        r = detect_intervalo_vigilancia_ccr('')
        assert r.value is None
        assert r.meta['target_intent'] == 'propagated_silence'


class TestPrecedence:
    """Quando múltiplos exclusion markers co-ocorrem, precedência: therapeutic > diagnostic > remake."""

    def test_therapeutic_beats_preparo(self):
        r = detect_intervalo_vigilancia_ccr(
            'CONCLUSAO: preparo ruim, porem foi feita polipectomia. '
            'sugere-se nova sessao de argonio em 4 semanas para completar tratamento.'
        )
        assert r.status == 'excluded_therapeutic_session'

    def test_diagnostic_beats_incomplete(self):
        r = detect_intervalo_vigilancia_ccr(
            'CONCLUSAO: exame incompleto. sugere-se complementacao com colonoscopia virtual em 30 dias.'
        )
        assert r.status == 'excluded_diagnostic_completion'


class TestMetaPreservation:
    def test_vigilance_propagates_raw_meta(self):
        r = detect_intervalo_vigilancia_ccr(
            'CONCLUSAO: recomenda-se controle em 5 anos.'
        )
        assert r.meta['target_intent'] == 'vigilancia'
        assert r.meta['target_detector'].startswith('intervalo_vigilancia_ccr_l1_v4@')
        assert r.meta['raw_value'] == '5_anos'

    def test_exclusion_stores_raw_value(self):
        r = detect_intervalo_vigilancia_ccr(
            'CONCLUSAO: sugere-se nova sessao de argonio em 4 semanas.'
        )
        assert r.meta['raw_value'] is not None
        assert r.meta['exclusion_match'] is not None
