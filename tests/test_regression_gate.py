"""Tests for the v2.2 regression gate.

The first test is the one that matters: the gate must REPROVE a planted
regression. A guard that has never been seen failing is not a guard.

Fact established while writing these tests (proposal_executor.execute_overlay_regex):
the overlay runs the proposed regex over the WHOLE raw text with re.finditer, takes
the MAX for numeric ontologies and hard-codes section_compliant=True at the
single-text level. A proposed rule that matches an educational comment therefore
codes the comment's number. The invariance case below is the first automated
check that exercises this path.
"""
from __future__ import annotations

import json

import pytest

from policy_registry import Policy, OntologySpec
from sandbox.patch_card import PatchCard, ProposedChange
from sandbox.regression import (
    RegressionCase,
    barrier_regression_gate,
    canonical_value,
    load_regression_set,
    status_family,
)


def _policy():
    return Policy(
        variable='polipo_tamanho_max_mm',
        taxonomy=['MEAS-N'],
        ontology=OntologySpec(type='float', values=[], range=(0.1, 100.0)),
        allows_implicit_negative=False,
        default_if_silent=None,
        section_scope=['descricao', 'conclusao'],
    )


def _card(regex: str):
    return PatchCard(
        card_id='test_regression_card',
        variable='polipo_tamanho_max_mm',
        source_cluster={'ia_value': '8.0', 'count': 10},
        proposed_change=ProposedChange(
            change_type='new_regex',
            description='test overlay',
            regex_skeleton=regex,
            capture_map={'group_1': 'value_mm'},
            target_scope=['descricao'],
            precedence_hint=None,
            near_miss_patterns=[],
            risk_level='medium',
        ),
        evidence_discovery=[],
        evidence_eval=[],
        hard_negatives=[],
        site_distribution={'A': 10},
        impact_estimate=10,
        plausibility_tag='plausible',
        support_tag='coherent_cluster',
        sandbox_result=None,
        human_decision=None,
    )


def _abstaining_detector(text: str):
    """Baseline that never emits a value, so the proposal's overlay decides."""
    return None


# Overlay that fires on 'polipo ... N mm' across sentence boundaries — it will code
# the biopsy-forceps size as the polyp size. This is the planted regression.
GREEDY = r'p[oó]lipo.*?(\d+)\s*mm'
# Overlay confined to one sentence ('.' and ';' stop it).
STRICT = r'p[oó]lipo[^.;]*?\bde\s+(\d+)\s*mm'

CASES = [
    RegressionCase(id='R1', kind='synthetic_canary',
                   text='DESCRICAO: polipo sessil de 8mm em colon direito.', expected_value=8.0),
    RegressionCase(id='R2', kind='synthetic_canary',
                   text='DESCRICAO: formacao polipoide diminuta.', expected_value=None),
    # hard negative: the 10 mm belongs to the forceps, in the NEXT sentence
    RegressionCase(id='R3', kind='synthetic_known_bug',
                   text='DESCRICAO: polipo diminuto ressecado. Pinca de 10 mm utilizada.',
                   expected_value=None, note='measure belongs to the instrument'),
]


class TestGateFailsOnPlantedRegression:
    def test_greedy_rule_is_rejected(self):
        result = barrier_regression_gate(_card(GREEDY), CASES, _abstaining_detector, _policy())
        assert result.passed is False, 'a rule that codes the forceps size as polyp size must be rejected'
        assert result.barrier == 'regression_gate'
        assert 'REJECTED_REGRESSION' in result.detail
        assert 'R3' in result.detail
        assert result.score == pytest.approx(2 / 3)

    def test_empty_set_fails_closed(self):
        result = barrier_regression_gate(_card(STRICT), [], _abstaining_detector, _policy())
        assert result.passed is False
        assert 'fail closed' in result.detail


class TestGatePassesOnConformingRule:
    def test_strict_rule_conforms(self):
        result = barrier_regression_gate(_card(STRICT), CASES, _abstaining_detector, _policy())
        assert result.passed is True, result.detail
        assert result.score == 1.0

    def test_improvement_abstention_to_correct_value_passes(self):
        # baseline abstains on R1; the proposal supplies the correct 8 mm -> not a regression
        result = barrier_regression_gate(_card(STRICT), CASES[:1], _abstaining_detector, _policy())
        assert result.passed is True


class TestInvarianceIsConsistencyNotCorrectness:
    BASE = 'DESCRICAO: polipo sessil de 8mm em colon direito.'

    def test_invariant_perturbation_passes(self):
        perturbed = self.BASE + '\n\nCOMENTARIOS: texto educativo sobre classificacao, sem medidas.'
        pair = RegressionCase(id='INV1', kind='metamorphic_invariance', text=perturbed, reference_text=self.BASE)
        result = barrier_regression_gate(_card(STRICT), [pair], _abstaining_detector, _policy())
        assert result.passed is True, result.detail

    def test_boilerplate_with_a_measure_is_caught(self):
        """The overlay scans the whole text and takes the max: an educational comment
        with '47 mm' flips the answer from 8 to 47. The gate must catch it."""
        perturbed = self.BASE + '\n\nCOMENTARIOS: referencia educativa, polipos a partir de 47 mm sao grandes.'
        pair = RegressionCase(id='INV2', kind='metamorphic_invariance', text=perturbed, reference_text=self.BASE)
        result = barrier_regression_gate(_card(STRICT), [pair], _abstaining_detector, _policy())
        assert result.passed is False
        assert 'INV2' in result.detail and '47' in result.detail

    def test_wrong_but_consistent_rule_passes_invariance(self):
        """A rule that is WRONG (codes the forceps size) but stable under a harmless
        perturbation passes the invariance check — which is why invariance measures
        consistency, never correctness."""
        wrong_base = 'DESCRICAO: polipo ressecado com pinca de 10 mm.'      # truth: None
        perturbed = wrong_base + '\n\nOBSERVACAO: exame sem intercorrencias.'
        pair = RegressionCase(id='INV3', kind='metamorphic_invariance', text=perturbed, reference_text=wrong_base)
        result = barrier_regression_gate(_card(STRICT), [pair], _abstaining_detector, _policy())
        assert result.passed is True
        # the same rule fails a truth-bearing case on that text
        truth = RegressionCase(id='T1', kind='synthetic_known_bug', text=wrong_base, expected_value=None)
        assert barrier_regression_gate(_card(STRICT), [truth], _abstaining_detector, _policy()).passed is False

    def test_invariance_requires_reference_text(self):
        with pytest.raises(ValueError):
            RegressionCase(id='X', kind='metamorphic_invariance', text='abc')


class TestAttributionPreExistingVsIntroduced:
    """An overlay only acts where the baseline abstains. A case the frozen detector
    already gets wrong, and the card does not touch, is a pre-existing defect: it is
    reported but must not block the card — otherwise every card is blocked by
    defects it cannot reach, and the trap's two numbers become meaningless."""

    @staticmethod
    def _wrong_baseline(text: str):
        # the frozen detector codes the forceps size (10) on the R3 text; abstains elsewhere
        return 10.0 if 'pinca de 10 mm' in text.casefold() else None

    def test_untouched_baseline_defect_is_reported_not_blocking(self):
        result = barrier_regression_gate(_card(STRICT), CASES, self._wrong_baseline, _policy())
        assert result.passed is True, result.detail
        assert 'pre-existing' in result.detail and 'R3' in result.detail
        assert '1 pre-existing' in result.detail

    def test_card_that_introduces_the_divergence_is_blocked(self):
        # same set, abstaining baseline: now the 10 comes from the card itself
        result = barrier_regression_gate(_card(GREEDY), CASES, _abstaining_detector, _policy())
        assert result.passed is False
        assert '1 regressed' in result.detail and '0 pre-existing' in result.detail


import pandas as pd
from study_contract import Canary
from sandbox.evaluator import (
    evaluate_card,
    site_skew_warning,
    corpus_wide_impact_forecast,
    barrier_corpus_skew,
    barrier_corpus_wide_shadow,
)


def _full_card(regex: str):
    """A card that clears B0-B3 in this harness, so the regression gate is reached."""
    card = _card(regex)
    card.evidence_eval = [
        {'id_registro': i, 'texto_laudo': f'DESCRICAO: polipo sessil de 8mm (caso {i})', 'clinica': 'A'}
        for i in range(10)
    ]
    card.site_distribution = {'A': 5, 'B': 5}
    return card


CANARIES = [Canary(id='C1', text='sem polipos', expected_value=None)]
CORPUS = pd.DataFrame({
    'id_registro': range(10),
    'texto_laudo': ['sem alteracoes na mucosa'] * 10,
    'clinica': ['A'] * 5 + ['B'] * 5,
    'polipo_tamanho_max_mm__value': [None] * 10,
    'polipo_tamanho_max_mm__resolution': ['abstained_unsupported'] * 10,
})


class TestEvaluateCardV22:
    def test_regressing_card_is_rejected_by_the_gate(self):
        out = evaluate_card(_full_card(GREEDY), CANARIES, _policy(), CORPUS, _abstaining_detector,
                            regression_cases=CASES, regression_set_sha256='ab' * 32)
        sr = out.sandbox_result
        assert sr.status == 'REJECTED_REGRESSION'
        assert sr.barriers[-1].barrier == 'regression_gate' and sr.barriers[-1].kind == 'blocking'
        assert 'R3' in sr.barriers[-1].detail

    def test_conforming_card_is_approved_with_seven_entries(self):
        out = evaluate_card(_full_card(STRICT), CANARIES, _policy(), CORPUS, _abstaining_detector,
                            regression_cases=CASES)
        sr = out.sandbox_result
        assert sr.status == 'APPROVED'
        kinds = [b.kind for b in sr.barriers]
        assert len(sr.barriers) == 7 and kinds.count('blocking') == 5 and kinds.count('telemetry') == 2
        assert 'NO_REGRESSION_SET' not in sr.warning_flags

    def test_missing_set_is_flagged_never_silently_passed(self):
        out = evaluate_card(_full_card(STRICT), CANARIES, _policy(), CORPUS, _abstaining_detector)
        sr = out.sandbox_result
        assert sr.status == 'APPROVED'
        assert 'NO_REGRESSION_SET' in sr.warning_flags
        gate = next(b for b in sr.barriers if b.barrier == 'regression_gate')
        assert gate.detail.startswith('SKIPPED')

    def test_skew_never_becomes_the_status(self):
        card = _full_card(STRICT)
        card.site_distribution = {'A': 100}
        out = evaluate_card(card, CANARIES, _policy(), CORPUS, _abstaining_detector, regression_cases=CASES)
        sr = out.sandbox_result
        assert sr.status == 'APPROVED'          # never SKEW_WARNING in v2.2
        assert 'LOCAL_PATTERN_WARNING' in sr.warning_flags


class TestTelemetryNeverBlocks:
    def test_site_skew_warning_is_telemetry_even_at_full_skew(self):
        card = _full_card(STRICT)
        card.site_distribution = {'A': 100}
        r = site_skew_warning(card)
        assert r.passed is True and r.kind == 'telemetry' and 'WARNING' in r.detail

    def test_impact_forecast_is_telemetry(self):
        r = corpus_wide_impact_forecast(_full_card(STRICT), _abstaining_detector, _policy(), CORPUS)
        assert r.passed is True and r.kind == 'telemetry'

    def test_deprecated_aliases_warn_and_delegate(self):
        card = _full_card(STRICT)
        with pytest.warns(DeprecationWarning):
            r = barrier_corpus_skew(card)
        assert r.barrier == 'site_skew_warning' and r.kind == 'telemetry'
        with pytest.warns(DeprecationWarning):
            r2 = barrier_corpus_wide_shadow(card, _abstaining_detector, _policy(), CORPUS)
        assert r2.barrier == 'corpus_wide_impact_forecast' and r2.kind == 'telemetry'


class TestHelpers:
    def test_canonical_value(self):
        assert canonical_value(8) == canonical_value('8.0') == canonical_value('8,0')
        assert canonical_value(None) is None
        assert canonical_value('0+') == '0+'
        assert canonical_value(' Positivo ') == 'positivo'

    def test_status_family(self):
        assert status_family('accepted_exact') == 'accepted'
        assert status_family('abstained_unsupported') == 'abstained'
        assert status_family('escalated_case') == 'escalated'
        assert status_family(None) is None

    def test_load_regression_set_fails_closed(self, tmp_path):
        p = tmp_path / 'set.jsonl'
        p.write_text(json.dumps({'id': 'a', 'kind': 'synthetic_canary', 'text': 'x', 'expected_value': 1}) + '\n'
                     + json.dumps({'id': 'a', 'kind': 'synthetic_canary', 'text': 'y', 'expected_value': 2}) + '\n',
                     encoding='utf-8')
        with pytest.raises(ValueError, match='duplicated'):
            load_regression_set(p)
        p.write_text(json.dumps({'id': 'a', 'kind': 'nonsense', 'text': 'x'}) + '\n', encoding='utf-8')
        with pytest.raises(ValueError, match='unknown regression kind'):
            load_regression_set(p)
        p.write_text(json.dumps({'id': 'a', 'kind': 'synthetic_canary', 'text': 'x', 'expected_value': 1}) + '\n',
                     encoding='utf-8')
        cases, sha = load_regression_set(p)
        assert len(cases) == 1 and len(sha) == 64
