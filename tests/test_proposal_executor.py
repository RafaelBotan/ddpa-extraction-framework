"""Tests for proposal executor — regex overlay simulation."""
from __future__ import annotations
import pytest
import pandas as pd
from policy_registry import Policy, OntologySpec
from sandbox.patch_card import ProposedChange, PatchCard, ExecutionResult


def _make_policy(ont_type='float', ont_range=(0.1, 100.0), ont_values=None):
    return Policy(
        variable='polipo_tamanho_max_mm',
        taxonomy=['MEAS-N'],
        ontology=OntologySpec(type=ont_type, values=ont_values or [], range=ont_range),
        allows_implicit_negative=False,
        default_if_silent=None,
        section_scope=['descricao', 'conclusao'],
    )


def _make_regex_change():
    return ProposedChange(
        change_type='new_regex',
        description='capture polipo de Xmm',
        regex_skeleton=r'p[oó]lipo\s+(?:s[eé]ssil\s+)?de\s+(\d+)\s*mm',
        capture_map={'group_1': 'value_mm'},
        target_scope=['descricao', 'conclusao'],
        precedence_hint=None,
        near_miss_patterns=[],
        risk_level='medium',
    )


def _make_card(change, variable='polipo_tamanho_max_mm'):
    return PatchCard(
        card_id='test_card',
        variable=variable,
        source_cluster={'ia_value': '5.0', 'count': 100},
        proposed_change=change,
        evidence_discovery=[],
        evidence_eval=[],
        hard_negatives=[],
        site_distribution={},
        impact_estimate=100,
        plausibility_tag='plausible',
        support_tag='coherent_cluster',
        sandbox_result=None,
        human_decision=None,
    )


class TestRegexOverlay:
    def test_overlay_matches_when_baseline_is_null(self):
        from sandbox.proposal_executor import execute_overlay_regex
        text = 'Descricao: polipo séssil de 5 mm no ceco'
        change = _make_regex_change()
        policy = _make_policy()
        result = execute_overlay_regex(text, change, policy, baseline_value=None)
        assert result is not None
        assert result.proposed_value == 5.0
        assert result.overlay_matched is True
        assert result.ontology_compliant is True

    def test_overlay_skipped_when_baseline_has_value(self):
        from sandbox.proposal_executor import execute_overlay_regex
        text = 'Descricao: polipo séssil de 5 mm no ceco'
        change = _make_regex_change()
        policy = _make_policy()
        result = execute_overlay_regex(text, change, policy, baseline_value=5.0)
        assert result is None

    def test_overlay_no_match(self):
        from sandbox.proposal_executor import execute_overlay_regex
        text = 'Sem alteracoes na mucosa colonica'
        change = _make_regex_change()
        policy = _make_policy()
        result = execute_overlay_regex(text, change, policy, baseline_value=None)
        assert result is None

    def test_overlay_extracts_max_when_multiple_matches(self):
        from sandbox.proposal_executor import execute_overlay_regex
        text = 'polipo de 3 mm no ceco e polipo de 8 mm no sigmoide'
        change = _make_regex_change()
        policy = _make_policy()
        result = execute_overlay_regex(text, change, policy, baseline_value=None)
        assert result is not None
        assert result.proposed_value == 8.0

    def test_overlay_rejects_out_of_range(self):
        from sandbox.proposal_executor import execute_overlay_regex
        text = 'polipo de 200 mm'
        change = _make_regex_change()
        policy = _make_policy()
        result = execute_overlay_regex(text, change, policy, baseline_value=None)
        assert result is not None
        assert result.ontology_compliant is False


class TestExecuteProposalSingle:
    def test_regex_proposal_on_miss(self):
        from sandbox.proposal_executor import execute_proposal_single
        text = 'polipo séssil de 5 mm no ceco'
        card = _make_card(_make_regex_change())
        policy = _make_policy()
        def fake_detector(t):
            return None
        result = execute_proposal_single(text, card, fake_detector, policy)
        assert result.changed is True
        assert result.baseline_value is None
        assert result.proposed_value == 5.0

    def test_regex_proposal_on_existing_match(self):
        from sandbox.proposal_executor import execute_proposal_single
        text = 'polipo séssil de 5 mm no ceco'
        card = _make_card(_make_regex_change())
        policy = _make_policy()
        def fake_detector(t):
            return 5.0
        result = execute_proposal_single(text, card, fake_detector, policy)
        assert result.changed is False
        assert result.baseline_value == 5.0

    def test_detector_returns_dict(self):
        from sandbox.proposal_executor import execute_proposal_single
        text = 'sem polipos'
        card = _make_card(_make_regex_change())
        policy = _make_policy()
        def fake_detector(t):
            return {'value': None, 'status': 'no_match'}
        result = execute_proposal_single(text, card, fake_detector, policy)
        assert result.baseline_value is None
        assert result.baseline_status == 'no_match'

    def test_detector_returns_object_with_attrs(self):
        from sandbox.proposal_executor import execute_proposal_single

        class FakeResult:
            value = 7.0
            status = 'measure_explicit'

        text = 'polipo de 7mm'
        card = _make_card(_make_regex_change())
        policy = _make_policy()
        def fake_detector(t):
            return FakeResult()
        result = execute_proposal_single(text, card, fake_detector, policy)
        assert result.baseline_value == 7.0
        assert result.baseline_status == 'measure_explicit'
        assert result.changed is False

    def test_scope_expansion_returns_unchanged(self):
        from sandbox.proposal_executor import execute_proposal_single
        change = ProposedChange(
            change_type='scope_expansion',
            description='expand to intro section',
            regex_skeleton=None,
            capture_map=None,
            target_scope=['intro'],
            precedence_hint=None,
            near_miss_patterns=[],
            risk_level='low',
        )
        card = _make_card(change)
        policy = _make_policy()
        def fake_detector(t):
            return 5.0
        result = execute_proposal_single('polipo de 5mm', card, fake_detector, policy)
        assert result.changed is False
        assert result.baseline_value == 5.0

    def test_normalization_fix_returns_unchanged(self):
        from sandbox.proposal_executor import execute_proposal_single
        change = ProposedChange(
            change_type='normalization_fix',
            description='fix unit normalization',
            regex_skeleton=None,
            capture_map=None,
            target_scope=[],
            precedence_hint=None,
            near_miss_patterns=[],
            risk_level='low',
        )
        card = _make_card(change)
        policy = _make_policy()
        def fake_detector(t):
            return None
        result = execute_proposal_single('sem polipos', card, fake_detector, policy)
        assert result.changed is False
        assert result.baseline_value is None


class TestExecuteProposalBatch:
    def test_batch_returns_dataframe(self):
        from sandbox.proposal_executor import execute_proposal_batch
        df = pd.DataFrame({
            'id_registro': [1, 2, 3],
            'texto_laudo': [
                'polipo de 5 mm no ceco',
                'sem polipos',
                'polipo de 3mm no reto',
            ],
            'polipo_tamanho_max_mm__value': [None, None, 3.0],
            'polipo_tamanho_max_mm__status': ['no_match', 'no_match', 'measure_explicit'],
            'polipo_tamanho_max_mm__resolution': ['abstained_unsupported', 'abstained_unsupported', 'accepted_exact'],
            'clinica': ['Clinic A', 'Clinic B', 'Clinic A'],
        })
        card = _make_card(_make_regex_change())
        policy = _make_policy()
        def fake_detector(t):
            return None
        result_df = execute_proposal_batch(df, card, fake_detector, policy, 'polipo_tamanho_max_mm')
        assert 'proposed_value' in result_df.columns
        assert 'changed' in result_df.columns
        assert len(result_df) == 3

    def test_batch_column_completeness(self):
        from sandbox.proposal_executor import execute_proposal_batch
        df = pd.DataFrame({
            'id_registro': [1],
            'texto_laudo': ['polipo de 5 mm no ceco'],
            'clinica': ['Clinic A'],
        })
        card = _make_card(_make_regex_change())
        policy = _make_policy()
        def fake_detector(t):
            return None
        result_df = execute_proposal_batch(df, card, fake_detector, policy, 'polipo_tamanho_max_mm')
        expected_cols = {
            'id_registro', 'clinica',
            'baseline_value', 'baseline_status',
            'proposed_value', 'proposed_status',
            'changed', 'overlay_matched',
            'section_compliant', 'ontology_compliant',
        }
        assert expected_cols.issubset(set(result_df.columns))

    def test_batch_changed_count(self):
        from sandbox.proposal_executor import execute_proposal_batch
        df = pd.DataFrame({
            'id_registro': [1, 2, 3],
            'texto_laudo': [
                'polipo de 5 mm no ceco',   # should match
                'sem polipos',               # no match
                'polipo de 12 mm no reto',   # should match
            ],
            'clinica': ['A', 'B', 'A'],
        })
        card = _make_card(_make_regex_change())
        policy = _make_policy()
        def fake_detector(t):
            return None
        result_df = execute_proposal_batch(df, card, fake_detector, policy, 'polipo_tamanho_max_mm')
        assert result_df['changed'].sum() == 2

    def test_batch_preserves_id_clinica(self):
        from sandbox.proposal_executor import execute_proposal_batch
        df = pd.DataFrame({
            'id_registro': [101, 202],
            'texto_laudo': ['polipo de 5mm', 'sem polipos'],
            'clinica': ['Clinic A', 'Clinic B'],
        })
        card = _make_card(_make_regex_change())
        policy = _make_policy()
        def fake_detector(t):
            return None
        result_df = execute_proposal_batch(df, card, fake_detector, policy, 'polipo_tamanho_max_mm')
        assert list(result_df['id_registro']) == [101, 202]
        assert list(result_df['clinica']) == ['Clinic A', 'Clinic B']
