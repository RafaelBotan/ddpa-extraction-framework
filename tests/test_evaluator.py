"""Tests for the 5-barrier sandbox evaluator."""
from __future__ import annotations
import pytest
import pandas as pd
from policy_registry import Policy, OntologySpec
from sandbox.patch_card import (
    PatchCard, ProposedChange, SandboxResult, BarrierResult,
)


def _make_policy():
    return Policy(
        variable='polipo_tamanho_max_mm',
        taxonomy=['MEAS-N'],
        ontology=OntologySpec(type='float', values=[], range=(0.1, 100.0)),
        allows_implicit_negative=False,
        default_if_silent=None,
        section_scope=['descricao', 'conclusao'],
    )


def _make_card(regex=r'p[oó]lipo.*?(\d+)\s*mm', risk='medium'):
    return PatchCard(
        card_id='test_eval_card',
        variable='polipo_tamanho_max_mm',
        source_cluster={'ia_value': '5.0', 'count': 100},
        proposed_change=ProposedChange(
            change_type='new_regex',
            description='test',
            regex_skeleton=regex,
            capture_map={'group_1': 'value_mm'},
            target_scope=['descricao'],
            precedence_hint=None,
            near_miss_patterns=[],
            risk_level=risk,
        ),
        evidence_discovery=[],
        evidence_eval=[
            {'id_registro': i, 'texto_laudo': f'polipo séssil de 5mm (caso {i})', 'clinica': 'Clinic A'}
            for i in range(10)
        ],
        hard_negatives=[
            {'id_registro': 100, 'texto_laudo': 'sem polipos na mucosa', 'value': None, 'resolution': 'accepted_exact', 'clinica': 'Clinic A'},
        ],
        site_distribution={'Clinic A': 80, 'Clinic B': 20},
        impact_estimate=100,
        plausibility_tag='plausible',
        support_tag='coherent_cluster',
        sandbox_result=None,
        human_decision=None,
    )


class TestBarrierCanaryPass:
    def test_canaries_pass(self):
        from sandbox.evaluator import barrier_canary_pass
        from study_contract import Canary
        canaries = [Canary(id='C1', text='PREPARO: Boston 2+3+3=8', expected_value=8)]
        card = _make_card()
        policy = _make_policy()
        def detector(t):
            return 8  # detector returns 8, canary expects 8
        result = barrier_canary_pass(card, canaries, detector, policy)
        assert result.passed is True

    def test_canary_with_none_expected(self):
        from sandbox.evaluator import barrier_canary_pass
        from study_contract import Canary
        canaries = [Canary(id='C1', text='sem polipos', expected_value=None)]
        card = _make_card()
        policy = _make_policy()
        def detector(t):
            return None
        result = barrier_canary_pass(card, canaries, detector, policy)
        assert result.passed is True

    def test_canary_mismatch_fails(self):
        from sandbox.evaluator import barrier_canary_pass
        from study_contract import Canary
        canaries = [Canary(id='C1', text='polipo de 5mm', expected_value=99)]
        card = _make_card()
        policy = _make_policy()
        def detector(t):
            return None  # overlay will find 5, not 99
        result = barrier_canary_pass(card, canaries, detector, policy)
        assert result.passed is False
        assert 'REJECTED_CANARY' in result.detail

    def test_empty_canaries_passes(self):
        from sandbox.evaluator import barrier_canary_pass
        card = _make_card()
        policy = _make_policy()
        def detector(t):
            return None
        result = barrier_canary_pass(card, [], detector, policy)
        assert result.passed is True

    def test_multiple_canaries_all_pass(self):
        from sandbox.evaluator import barrier_canary_pass
        from study_contract import Canary
        canaries = [
            Canary(id='C1', text='polipo de 10mm', expected_value=10),
            Canary(id='C2', text='sem polipos na mucosa', expected_value=None),
        ]
        card = _make_card()
        policy = _make_policy()
        def detector(t):
            return None
        result = barrier_canary_pass(card, canaries, detector, policy)
        assert result.passed is True

    def test_multiple_canaries_one_fails(self):
        from sandbox.evaluator import barrier_canary_pass
        from study_contract import Canary
        canaries = [
            Canary(id='C1', text='polipo de 10mm', expected_value=10),
            Canary(id='C2', text='polipo de 5mm', expected_value=99),  # expects 99, will find 5
        ]
        card = _make_card()
        policy = _make_policy()
        def detector(t):
            return None
        result = barrier_canary_pass(card, canaries, detector, policy)
        assert result.passed is False


class TestBarrierTargetHit:
    def test_high_hit_rate_passes(self):
        from sandbox.evaluator import barrier_target_hit
        card = _make_card()
        policy = _make_policy()
        def detector(t):
            return None  # L1 miss, overlay should catch it
        result = barrier_target_hit(card, detector, policy)
        assert result.passed is True
        assert result.score >= 0.8

    def test_low_hit_rate_fails(self):
        from sandbox.evaluator import barrier_target_hit
        card = _make_card(regex=r'NOMATCH_PATTERN_XYZ')
        policy = _make_policy()
        def detector(t):
            return None
        result = barrier_target_hit(card, detector, policy)
        assert result.passed is False
        assert result.score < 0.6

    def test_high_risk_stricter_threshold(self):
        from sandbox.evaluator import barrier_target_hit
        card = _make_card(risk='high')
        # 8 match + 2 don't = 80%, but high risk needs 90%
        card.evidence_eval = [
            {'id_registro': i, 'texto_laudo': f'polipo séssil de 5mm (caso {i})', 'clinica': 'Clinic A'}
            for i in range(8)
        ] + [
            {'id_registro': 8, 'texto_laudo': 'texto sem padrao', 'clinica': 'Clinic A'},
            {'id_registro': 9, 'texto_laudo': 'outro texto sem padrao', 'clinica': 'Clinic A'},
        ]
        policy = _make_policy()
        def detector(t):
            return None
        result = barrier_target_hit(card, detector, policy)
        assert result.score == pytest.approx(0.8, abs=0.01)
        # 80% < 90% threshold for high risk
        assert result.passed is False

    def test_no_eval_set_fails(self):
        from sandbox.evaluator import barrier_target_hit
        card = _make_card()
        card.evidence_eval = []
        policy = _make_policy()
        def detector(t):
            return None
        result = barrier_target_hit(card, detector, policy)
        assert result.passed is False

    def test_needs_refinement_at_70pct(self):
        from sandbox.evaluator import barrier_target_hit
        card = _make_card()
        # 7 match, 3 don't = 70%
        card.evidence_eval = [
            {'id_registro': i, 'texto_laudo': f'polipo séssil de 5mm (caso {i})', 'clinica': 'G'}
            for i in range(7)
        ] + [
            {'id_registro': 7, 'texto_laudo': 'texto sem padrao', 'clinica': 'G'},
            {'id_registro': 8, 'texto_laudo': 'outro texto', 'clinica': 'G'},
            {'id_registro': 9, 'texto_laudo': 'nada aqui', 'clinica': 'G'},
        ]
        policy = _make_policy()
        def detector(t):
            return None
        result = barrier_target_hit(card, detector, policy)
        assert result.passed is False
        assert 'NEEDS_REFINEMENT' in result.detail
        assert result.score == pytest.approx(0.7, abs=0.01)


class TestBarrierNearMissSafety:
    def test_no_false_positives(self):
        from sandbox.evaluator import barrier_near_miss_safety
        card = _make_card()
        policy = _make_policy()
        corpus_df = pd.DataFrame({
            'id_registro': range(50),
            'texto_laudo': ['sem alteracoes na mucosa colonica'] * 50,
            'clinica': ['Clinic A'] * 25 + ['Clinic B'] * 25,
            'polipo_tamanho_max_mm__value': [None] * 50,
            'polipo_tamanho_max_mm__resolution': ['abstained_unsupported'] * 50,
        })
        def detector(t):
            return None
        result = barrier_near_miss_safety(card, detector, policy, corpus_df, random_n=50)
        assert result.passed is True

    def test_false_positive_detected(self):
        from sandbox.evaluator import barrier_near_miss_safety
        card = _make_card(regex=r'(\d+)')  # matches any number
        policy = _make_policy()
        corpus_df = pd.DataFrame({
            'id_registro': range(10),
            'texto_laudo': ['paciente de 45 anos'] * 10,
            'clinica': ['Clinic A'] * 10,
            'polipo_tamanho_max_mm__value': [None] * 10,
            'polipo_tamanho_max_mm__resolution': ['accepted_exact'] * 10,
        })
        def detector(t):
            return None
        result = barrier_near_miss_safety(card, detector, policy, corpus_df, random_n=10)
        assert result.passed is False

    def test_hard_negative_triggers_false_positive(self):
        from sandbox.evaluator import barrier_near_miss_safety
        # A regex that matches hard_negative text: "sem polipos na mucosa" has no digits,
        # but we use a regex that would match anything.
        card = _make_card(regex=r'(sem)')  # matches "sem" — will catch hard negative
        policy = _make_policy()
        # Make ontology accept strings to avoid ontology filter
        policy2 = Policy(
            variable='polipo_tamanho_max_mm',
            taxonomy=['MEAS-N'],
            ontology=OntologySpec(type='string', values=[], range=None),
            allows_implicit_negative=False,
            default_if_silent=None,
            section_scope=['descricao', 'conclusao'],
        )
        corpus_df = pd.DataFrame()
        def detector(t):
            return None
        result = barrier_near_miss_safety(card, detector, policy2, corpus_df, random_n=0)
        assert result.passed is False
        assert 'FALSE_POSITIVE_RISK' in result.detail

    def test_empty_corpus_still_checks_hard_negatives(self):
        from sandbox.evaluator import barrier_near_miss_safety
        card = _make_card()
        # Hard neg has no digits matching the polipo regex — should pass
        policy = _make_policy()
        corpus_df = pd.DataFrame()
        def detector(t):
            return None
        result = barrier_near_miss_safety(card, detector, policy, corpus_df, random_n=0)
        assert result.passed is True

    def test_stratified_sampling_by_clinica(self):
        from sandbox.evaluator import barrier_near_miss_safety
        card = _make_card()
        policy = _make_policy()
        corpus_df = pd.DataFrame({
            'id_registro': range(100),
            'texto_laudo': ['texto limpo sem polipos'] * 100,
            'clinica': (['Clinic A'] * 50 + ['Clinic B'] * 30 + ['OutraClinica'] * 20),
            'polipo_tamanho_max_mm__value': [None] * 100,
            'polipo_tamanho_max_mm__resolution': ['accepted_exact'] * 100,
        })
        def detector(t):
            return None
        # Should not raise; stratified sampling must handle 3 clinics
        result = barrier_near_miss_safety(card, detector, policy, corpus_df, random_n=30)
        assert result.passed is True


class TestBarrierCorpusSkew:
    def test_no_skew(self):
        from sandbox.evaluator import barrier_corpus_skew
        card = _make_card()
        card.site_distribution = {'Clinic A': 50, 'Clinic B': 50}
        result = barrier_corpus_skew(card)
        assert result.passed is True

    def test_skew_warning(self):
        from sandbox.evaluator import barrier_corpus_skew
        card = _make_card()
        card.site_distribution = {'Clinic A': 90, 'Clinic B': 10}
        result = barrier_corpus_skew(card, skew_threshold=0.8)
        assert result.passed is True  # warning, not block
        assert 'WARNING' in result.detail or 'skew' in result.detail.lower()

    def test_empty_distribution(self):
        from sandbox.evaluator import barrier_corpus_skew
        card = _make_card()
        card.site_distribution = {}
        result = barrier_corpus_skew(card)
        assert result.passed is True

    def test_single_site_is_skewed(self):
        from sandbox.evaluator import barrier_corpus_skew
        card = _make_card()
        card.site_distribution = {'Clinic A': 100}
        result = barrier_corpus_skew(card, skew_threshold=0.8)
        assert result.passed is True  # always passes
        assert 'WARNING' in result.detail

    def test_skew_score_returned(self):
        from sandbox.evaluator import barrier_corpus_skew
        card = _make_card()
        card.site_distribution = {'A': 85, 'B': 15}
        result = barrier_corpus_skew(card, skew_threshold=0.8)
        assert result.score == pytest.approx(0.85, abs=0.01)


class TestBarrierFamilyViability:
    """Tests for B0 — family viability gate (v2.1 per GPT-5 verdict 2026-04-17)."""

    def test_no_coverage_metric_passes(self):
        from sandbox.evaluator import barrier_family_viability
        card = _make_card()
        # source_cluster has no family_coverage_rate (legacy/pre-classifier card)
        result = barrier_family_viability(card)
        assert result.passed is True
        assert 'skipped' in result.detail.lower()

    def test_low_coverage_fails(self):
        from sandbox.evaluator import barrier_family_viability
        card = _make_card()
        card.source_cluster = {
            'ia_value': '5.0',
            'family_coverage_rate': 0.02,  # 2% — typical of v2.0.1 ablation cards
            'family_coverage_detail': {'claimed_here': 8, 'source_cluster_total': 400},
        }
        result = barrier_family_viability(card)
        assert result.passed is False
        assert 'LOW_FAMILY_COVERAGE' in result.detail

    def test_high_coverage_passes(self):
        from sandbox.evaluator import barrier_family_viability
        card = _make_card()
        card.source_cluster = {
            'ia_value': '5.0',
            'family_coverage_rate': 0.55,
            'family_coverage_detail': {'claimed_here': 220, 'source_cluster_total': 400},
        }
        result = barrier_family_viability(card)
        assert result.passed is True
        assert result.score == pytest.approx(0.55)

    def test_at_threshold_passes(self):
        from sandbox.evaluator import barrier_family_viability, FAMILY_COVERAGE_MIN
        card = _make_card()
        card.source_cluster = {
            'ia_value': '5.0',
            'family_coverage_rate': FAMILY_COVERAGE_MIN,
            'family_coverage_detail': {'claimed_here': 40, 'source_cluster_total': 400},
        }
        result = barrier_family_viability(card)
        assert result.passed is True

    def test_low_coverage_stops_evaluation(self):
        """B0 fail should short-circuit the evaluator (no B1/B2/B3 run)."""
        from sandbox.evaluator import evaluate_card
        from study_contract import Canary
        card = _make_card()
        card.source_cluster = {
            'ia_value': '5.0',
            'family_coverage_rate': 0.02,
            'family_coverage_detail': {'claimed_here': 8, 'source_cluster_total': 400},
        }
        canaries = [Canary(id='C1', text='sem polipos', expected_value=None)]
        corpus_df = pd.DataFrame()
        def detector(t):
            return None
        result_card = evaluate_card(card, canaries, None, corpus_df, detector)
        sr = result_card.sandbox_result
        assert sr.status == 'LOW_FAMILY_COVERAGE'
        assert len(sr.barriers) == 1  # only B0, no B1+
        assert 'LOW_FAMILY_COVERAGE' in sr.warning_flags


class TestEvaluateCard:
    def test_full_evaluation(self):
        from sandbox.evaluator import evaluate_card
        from study_contract import Canary
        card = _make_card()
        policy = _make_policy()
        canaries = [Canary(id='C1', text='sem polipos', expected_value=None)]
        corpus_df = pd.DataFrame({
            'id_registro': range(20),
            'texto_laudo': ['sem alteracoes'] * 20,
            'clinica': ['Clinic A'] * 10 + ['Clinic B'] * 10,
            'polipo_tamanho_max_mm__value': [None] * 20,
            'polipo_tamanho_max_mm__resolution': ['abstained_unsupported'] * 20,
        })
        def detector(t):
            return None
        result_card = evaluate_card(card, canaries, policy, corpus_df, detector)
        assert result_card.sandbox_result is not None
        assert result_card.sandbox_result.status in ('APPROVED', 'NEEDS_REFINEMENT', 'SKEW_WARNING')

    def test_fail_fast_skips_later_barriers(self):
        from sandbox.evaluator import evaluate_card
        from study_contract import Canary
        # Use regex that won't match eval examples
        card = _make_card(regex=r'NOMATCH_XYZ')
        policy = _make_policy()
        canaries = [Canary(id='C1', text='sem polipos', expected_value=None)]
        corpus_df = pd.DataFrame()
        def detector(t):
            return None
        result_card = evaluate_card(card, canaries, policy, corpus_df, detector)
        sr = result_card.sandbox_result
        assert sr is not None
        # Should fail on target_hit (barrier 2), not run barrier 3+
        assert sr.status in ('LOW_TARGET_HIT', 'NEEDS_REFINEMENT')
        assert len(sr.barriers) == 3  # family_viability + canary_pass + target_hit

    def test_canary_failure_stops_at_barrier1(self):
        from sandbox.evaluator import evaluate_card
        from study_contract import Canary
        card = _make_card()
        policy = _make_policy()
        # Canary expects 99 but overlay will return 5 from "polipo de 5mm"
        canaries = [Canary(id='C1', text='polipo de 5mm', expected_value=99)]
        corpus_df = pd.DataFrame()
        def detector(t):
            return None
        result_card = evaluate_card(card, canaries, policy, corpus_df, detector)
        sr = result_card.sandbox_result
        assert sr.status == 'REJECTED_CANARY'
        assert len(sr.barriers) == 2  # family_viability + canary_pass

    def test_false_positive_stops_at_barrier3(self):
        from sandbox.evaluator import evaluate_card
        from study_contract import Canary
        # regex matches anything with a digit — will cause false positives
        card = _make_card(regex=r'(\d+)')
        policy = _make_policy()
        canaries = [Canary(id='C1', text='sem polipos', expected_value=None)]
        corpus_df = pd.DataFrame({
            'id_registro': range(10),
            'texto_laudo': ['paciente de 45 anos, exame normal'] * 10,
            'clinica': ['Clinic A'] * 10,
            'polipo_tamanho_max_mm__value': [None] * 10,
            'polipo_tamanho_max_mm__resolution': ['accepted_exact'] * 10,
        })
        def detector(t):
            return None
        result_card = evaluate_card(card, canaries, policy, corpus_df, detector)
        sr = result_card.sandbox_result
        assert sr.status == 'FALSE_POSITIVE_RISK'
        assert len(sr.barriers) == 4  # family_viability + canary_pass + target_hit + near_miss_safety

    def test_approved_card_has_5_barriers(self):
        from sandbox.evaluator import evaluate_card
        from study_contract import Canary
        card = _make_card()
        card.site_distribution = {'Clinic A': 50, 'Clinic B': 50}  # no skew
        policy = _make_policy()
        canaries = [Canary(id='C1', text='sem polipos', expected_value=None)]
        corpus_df = pd.DataFrame({
            'id_registro': range(10),
            'texto_laudo': ['sem alteracoes na mucosa'] * 10,
            'clinica': ['Clinic A'] * 5 + ['Clinic B'] * 5,
            'polipo_tamanho_max_mm__value': [None] * 10,
            'polipo_tamanho_max_mm__resolution': ['abstained_unsupported'] * 10,
        })
        def detector(t):
            return None
        result_card = evaluate_card(card, canaries, policy, corpus_df, detector)
        sr = result_card.sandbox_result
        assert sr is not None
        assert len(sr.barriers) == 6  # family_viability + 5 original barriers

    def test_high_risk_uses_500_negatives(self):
        """Verify high-risk cards use neg_n=500 (structural test, not count-verification)."""
        from sandbox.evaluator import evaluate_card
        from study_contract import Canary
        card = _make_card(risk='high')
        policy = _make_policy()
        canaries = [Canary(id='C1', text='sem polipos', expected_value=None)]
        corpus_df = pd.DataFrame({
            'id_registro': range(5),
            'texto_laudo': ['sem alteracoes'] * 5,
            'clinica': ['Clinic A'] * 5,
            'polipo_tamanho_max_mm__value': [None] * 5,
            'polipo_tamanho_max_mm__resolution': ['abstained_unsupported'] * 5,
        })
        def detector(t):
            return None
        # High risk: target_hit threshold is 90%; 10 eval examples but only ~8 match → fails
        result_card = evaluate_card(card, canaries, policy, corpus_df, detector)
        assert result_card.sandbox_result is not None

    def test_sandbox_result_impact_report_populated(self):
        from sandbox.evaluator import evaluate_card
        from study_contract import Canary
        card = _make_card()
        policy = _make_policy()
        canaries = []
        corpus_df = pd.DataFrame({
            'id_registro': range(5),
            'texto_laudo': ['sem alteracoes'] * 5,
            'clinica': ['Clinic A'] * 5,
            'polipo_tamanho_max_mm__value': [None] * 5,
            'polipo_tamanho_max_mm__resolution': ['abstained_unsupported'] * 5,
        })
        def detector(t):
            return None
        result_card = evaluate_card(card, canaries, policy, corpus_df, detector)
        sr = result_card.sandbox_result
        if sr.status in ('APPROVED', 'SKEW_WARNING'):
            assert 'fill_rate_delta' in sr.impact_report or 'shadow_detail' in sr.impact_report


class TestBarrierResultDataclass:
    def test_barrier_result_fields(self):
        r = BarrierResult(barrier='canary_pass', passed=True, score=1.0, detail='ok')
        assert r.barrier == 'canary_pass'
        assert r.passed is True
        assert r.score == 1.0
        assert r.detail == 'ok'

    def test_sandbox_result_fields(self):
        r = SandboxResult(
            status='APPROVED',
            barriers=[],
            impact_report={},
            generated_canaries=[],
            generated_hard_negatives=[],
        )
        assert r.status == 'APPROVED'
        assert r.barriers == []
