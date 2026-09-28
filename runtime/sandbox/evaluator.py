"""DDPA Sandbox — evaluator v2.2 (gate + four blocking barriers + two telemetries).

Runs checks sequentially with fail-fast semantics:

  Gate      B0: family_viability            — blocks cards whose family is too fragmented
  Barrier   B1: canary_pass                 — contract canaries must keep their expected value
  Barrier   B2: target_hit                  — recall gate
  Barrier   B3: near_miss_safety            — precision guard
  Barrier   B4: regression_gate             — v2.2: versioned held-aside set re-executed from raw
                                              text; value AND state compared; CAN REJECT
  Telemetry T1: site_skew_warning           — source-distribution warning (never rejects)
  Telemetry T2: corpus_wide_impact_forecast — projected change count (never rejects)

History: until v2.1 the two telemetries were listed as "barriers 4 and 5" although both
returned passed=True unconditionally; v2.2 renames them (old names kept as deprecated
aliases), adds the regression gate and stops emitting SKEW_WARNING as a final status
(it lives only in warning_flags). Entry point: evaluate_card()
"""
from __future__ import annotations

import warnings
from typing import Any, Callable, Optional

import pandas as pd

from policy_registry import Policy
from sandbox.patch_card import (
    BarrierResult,
    PatchCard,
    SandboxResult,
)
from sandbox.proposal_executor import execute_proposal_batch, execute_proposal_single
from sandbox.regression import barrier_regression_gate


# ============================================================================
# Barrier 0: Family viability gate (v2.1 per GPT-5 verdict 2026-04-17)
# ============================================================================

FAMILY_COVERAGE_MIN = 0.10  # 10% — below this, family is too fragmented to be informative


def barrier_family_viability(
    card: PatchCard,
    min_coverage: float = FAMILY_COVERAGE_MIN,
) -> BarrierResult:
    """Reject cards whose source family covers <10% of its original cluster.

    Measures whether the classify_text_to_family assignment kept enough of the
    cluster together to make downstream B2/B3 metrics informative. Cards with
    coverage below threshold are sliced into microfatias — any regex that
    matches that subset will look "good" for the wrong reason.

    Returns:
        passed=True if coverage ≥ min_coverage OR metric unavailable
                (v1 proposer / pre-classifier cards).
        passed=False otherwise — triggers LOW_FAMILY_COVERAGE status.
    """
    coverage = card.source_cluster.get('family_coverage_rate')
    if coverage is None:
        return BarrierResult(
            barrier='family_viability',
            passed=True,
            score=None,
            detail='No family_coverage_rate — skipped (pre-classifier card)',
        )

    detail_obj = card.source_cluster.get('family_coverage_detail', {})
    claimed = detail_obj.get('claimed_here', '?')
    total = detail_obj.get('source_cluster_total', '?')

    if coverage < min_coverage:
        return BarrierResult(
            barrier='family_viability',
            passed=False,
            score=float(coverage),
            detail=(
                f'LOW_FAMILY_COVERAGE: {coverage:.1%} '
                f'(claimed {claimed} / {total}, threshold={min_coverage:.0%})'
            ),
        )

    return BarrierResult(
        barrier='family_viability',
        passed=True,
        score=float(coverage),
        detail=f'coverage={coverage:.1%} (claimed {claimed} / {total})',
    )


# ============================================================================
# Barrier 1: Canary pass
# ============================================================================

def barrier_canary_pass(
    card: PatchCard,
    canaries: list,
    detector_func: Callable[[str], Any],
    policy: Policy,
) -> BarrierResult:
    """Verify all canaries produce the expected value after overlay.

    Comparison rules:
    - Both None → pass
    - Both numeric and equal → pass
    - Both string and equal → pass
    - Any mismatch → FAIL
    """
    if not canaries:
        return BarrierResult(
            barrier='canary_pass',
            passed=True,
            score=1.0,
            detail='No canaries defined — skipped',
        )

    failures: list[str] = []
    for canary in canaries:
        result = execute_proposal_single(canary.text, card, detector_func, policy)
        actual = result.proposed_value
        expected = canary.expected_value

        passed = _values_equal(actual, expected)
        if not passed:
            failures.append(
                f'Canary {canary.id!r}: expected={expected!r}, got={actual!r}'
            )

    if failures:
        return BarrierResult(
            barrier='canary_pass',
            passed=False,
            score=0.0,
            detail='REJECTED_CANARY: ' + '; '.join(failures),
        )

    return BarrierResult(
        barrier='canary_pass',
        passed=True,
        score=1.0,
        detail=f'All {len(canaries)} canaries passed',
    )


def _values_equal(actual: Any, expected: Any) -> bool:
    """Compare values with type-aware rules."""
    if actual is None and expected is None:
        return True
    if actual is None or expected is None:
        return False
    # Both numeric
    try:
        return float(actual) == float(expected)
    except (TypeError, ValueError):
        pass
    # Both string
    return str(actual) == str(expected)


# ============================================================================
# Barrier 2: Target hit rate
# ============================================================================

def barrier_target_hit(
    card: PatchCard,
    detector_func: Callable[[str], Any],
    policy: Policy,
) -> BarrierResult:
    """Measure recall on the evaluation holdout set.

    A hit is: result.changed AND result.ontology_compliant.

    Thresholds:
    - Standard (medium/low): ≥80% passes; 60-79% → NEEDS_REFINEMENT; <60% → LOW_TARGET_HIT
    - High risk: ≥90% passes; otherwise same failure bands
    """
    eval_set = card.evidence_eval
    if not eval_set:
        return BarrierResult(
            barrier='target_hit',
            passed=False,
            score=0.0,
            detail='No eval evidence — cannot assess target hit rate',
        )

    risk_level = card.proposed_change.risk_level
    threshold_pass = 0.90 if risk_level == 'high' else 0.80

    hits = 0
    for example in eval_set:
        text = str(example.get('texto_laudo', ''))
        result = execute_proposal_single(text, card, detector_func, policy)
        if result.changed and result.ontology_compliant:
            hits += 1

    score = hits / len(eval_set)

    if score >= threshold_pass:
        return BarrierResult(
            barrier='target_hit',
            passed=True,
            score=score,
            detail=(
                f'Hit rate {score:.1%} >= threshold {threshold_pass:.0%} '
                f'(risk={risk_level}, n={len(eval_set)})'
            ),
        )

    if score >= 0.60:
        status_tag = 'NEEDS_REFINEMENT'
    else:
        status_tag = 'LOW_TARGET_HIT'

    return BarrierResult(
        barrier='target_hit',
        passed=False,
        score=score,
        detail=(
            f'{status_tag}: hit rate {score:.1%} < threshold {threshold_pass:.0%} '
            f'(risk={risk_level}, n={len(eval_set)})'
        ),
    )


# ============================================================================
# Barrier 3: Near-miss safety
# ============================================================================

def barrier_near_miss_safety(
    card: PatchCard,
    detector_func: Callable[[str], Any],
    policy: Policy,
    corpus_df: pd.DataFrame,
    random_n: int = 200,
    seed: int = 42,
) -> BarrierResult:
    """Precision guard: ensure the proposal does not produce false positives.

    Two layers:
    1. Hard negatives from card.hard_negatives
    2. Random sample from corpus where resolution is accepted_exact/accepted_semantic
       (stratified by clinica if available and has >1 unique value)

    Any changed result in either layer → FAIL.
    """
    false_positives: list[str] = []

    # --- Layer 1: Hard negatives ---
    for neg in card.hard_negatives:
        text = str(neg.get('texto_laudo', ''))
        result = execute_proposal_single(text, card, detector_func, policy)
        if result.changed:
            false_positives.append(
                f'Hard negative id={neg.get("id_registro")!r} triggered overlay'
            )

    # --- Layer 2: Random sample from corpus ---
    if not corpus_df.empty:
        var = card.variable
        resolution_col = f'{var}__resolution'

        if resolution_col in corpus_df.columns:
            correct = corpus_df[
                corpus_df[resolution_col].isin(['accepted_exact', 'accepted_semantic'])
            ]
        else:
            correct = corpus_df

        if not correct.empty:
            sample = _stratified_sample(correct, n=random_n, seed=seed)

            for _, row in sample.iterrows():
                text = str(row.get('texto_laudo', '')) if pd.notna(row.get('texto_laudo')) else ''
                result = execute_proposal_single(text, card, detector_func, policy)
                if result.changed:
                    false_positives.append(
                        f'Corpus sample id={row.get("id_registro")!r} triggered overlay'
                    )

    if false_positives:
        sample_msgs = false_positives[:5]
        detail = 'FALSE_POSITIVE_RISK: ' + '; '.join(sample_msgs)
        if len(false_positives) > 5:
            detail += f' ... and {len(false_positives) - 5} more'
        return BarrierResult(
            barrier='near_miss_safety',
            passed=False,
            score=0.0,
            detail=detail,
        )

    return BarrierResult(
        barrier='near_miss_safety',
        passed=True,
        score=1.0,
        detail='No false positives detected in hard negatives or corpus sample',
    )


def _stratified_sample(
    df: pd.DataFrame,
    n: int,
    seed: int = 42,
) -> pd.DataFrame:
    """Sample up to n rows, stratified by clinica if available."""
    if 'clinica' in df.columns and df['clinica'].nunique() > 1:
        n_clinics = df['clinica'].nunique()
        per_clinic = max(1, n // n_clinics)
        sample = df.groupby('clinica', group_keys=False).apply(
            lambda g: g.sample(n=min(len(g), per_clinic), random_state=seed),
            include_groups=False,
        ).head(n)
    else:
        sample = df.sample(n=min(n, len(df)), random_state=seed)
    return sample


# ============================================================================
# Barrier 4: Corpus skew
# ============================================================================

def site_skew_warning(
    card: PatchCard,
    skew_threshold: float = 0.8,
) -> BarrierResult:
    """TELEMETRY (v2.2; formerly 'barrier 4'): site distribution skew - never rejects, emits warning.

    If the dominant site has >skew_threshold of total records, emit WARNING.
    """
    dist = card.site_distribution
    if not dist:
        return BarrierResult(
            barrier='site_skew_warning',
            kind='telemetry',
            passed=True,
            score=None,
            detail='No site distribution data available',
        )

    total = sum(dist.values())
    if total == 0:
        return BarrierResult(
            barrier='site_skew_warning',
            kind='telemetry',
            passed=True,
            score=None,
            detail='Site distribution total is zero',
        )

    max_site = max(dist, key=lambda k: dist[k])
    max_frac = dist[max_site] / total

    if max_frac > skew_threshold:
        return BarrierResult(
            barrier='site_skew_warning',
            kind='telemetry',
            passed=True,
            score=max_frac,
            detail=(
                f'WARNING: site skew detected — {max_site!r} has {max_frac:.1%} '
                f'of {total} records (threshold={skew_threshold:.0%})'
            ),
        )

    return BarrierResult(
        barrier='site_skew_warning',
        kind='telemetry',
        passed=True,
        score=max_frac,
        detail=f'Site distribution balanced — max site {max_site!r} at {max_frac:.1%}',
    )


# ============================================================================
# Barrier 5: Corpus-wide shadow run
# ============================================================================

def corpus_wide_impact_forecast(
    card: PatchCard,
    detector_func: Callable[[str], Any],
    policy: Policy,
    corpus_df: pd.DataFrame,
) -> BarrierResult:
    """TELEMETRY (v2.2; formerly 'barrier 5'): corpus-wide impact forecast - never rejects.
    It projects how many rows would change; it is NOT a regression test (that is barrier 4).

    Computes:
    - cases_changed: how many rows would gain a value
    - fill_rate_delta: (baseline_filled + cases_changed) / total - baseline_filled / total
    """
    if corpus_df.empty:
        return BarrierResult(
            barrier='corpus_wide_impact_forecast',
            kind='telemetry',
            passed=True,
            score=None,
            detail='Empty corpus — shadow run skipped',
        )

    result_df = execute_proposal_batch(
        corpus_df, card, detector_func, policy, card.variable
    )

    total = len(result_df)
    cases_changed = int(result_df['changed'].sum())

    # Baseline fill = rows that already had a value before proposal
    baseline_filled = int((result_df['baseline_value'].notna()).sum())

    if total > 0:
        baseline_rate = baseline_filled / total
        proposed_rate = (baseline_filled + cases_changed) / total
        fill_rate_delta = proposed_rate - baseline_rate
    else:
        fill_rate_delta = 0.0
        baseline_rate = 0.0
        proposed_rate = 0.0

    return BarrierResult(
        barrier='corpus_wide_impact_forecast',
        kind='telemetry',
        passed=True,
        score=fill_rate_delta,
        detail=(
            f'Shadow: {cases_changed}/{total} cases would change; '
            f'fill_rate {baseline_rate:.1%} → {proposed_rate:.1%} '
            f'(delta={fill_rate_delta:+.1%})'
        ),
    )


# ============================================================================
# Main orchestrator
# ============================================================================

# ---------------------------------------------------------------------------
# Deprecated aliases (v2.2). The old names suggested these could reject a card;
# they never could. Kept so v1.x scripts and reports keep importing.
# ---------------------------------------------------------------------------

def barrier_corpus_skew(*args, **kwargs) -> BarrierResult:
    warnings.warn('barrier_corpus_skew is telemetry and was renamed site_skew_warning (v2.2)',
                  DeprecationWarning, stacklevel=2)
    return site_skew_warning(*args, **kwargs)


def barrier_corpus_wide_shadow(*args, **kwargs) -> BarrierResult:
    warnings.warn('barrier_corpus_wide_shadow is telemetry and was renamed corpus_wide_impact_forecast (v2.2)',
                  DeprecationWarning, stacklevel=2)
    return corpus_wide_impact_forecast(*args, **kwargs)


def evaluate_card(
    card: PatchCard,
    canaries: list,
    policy: Policy,
    corpus_df: pd.DataFrame,
    detector_func: Callable[[str], Any],
    random_negative_n: int = 200,
    regression_cases: Optional[list] = None,
    regression_set_sha256: Optional[str] = None,
) -> PatchCard:
    """Run gate, four blocking barriers and two telemetries; set card.sandbox_result.

    Fail-fast rules (v2.2):
    - B0 fails -> status=LOW_FAMILY_COVERAGE
    - B1 fails -> status=REJECTED_CANARY
    - B2 fails -> status=NEEDS_REFINEMENT (60-79%) or LOW_TARGET_HIT (<60%)
    - B3 fails -> status=FALSE_POSITIVE_RISK
    - B4 (regression gate) fails -> status=REJECTED_REGRESSION
    - telemetries never change the status; final status is APPROVED, with
      LOCAL_PATTERN_WARNING / NO_REGRESSION_SET in warning_flags when applicable.

    ``regression_cases`` is the loaded held-aside set (see sandbox.regression). When it is
    None the gate is SKIPPED and flagged NO_REGRESSION_SET - that is not a pass; production
    runs (scripts/run_sandbox.py) refuse to evaluate without a set.
    High-risk cards use random_negative_n=500 for barrier 3.
    """
    risk_level = card.proposed_change.risk_level
    neg_n = 500 if risk_level == 'high' else random_negative_n

    barriers_run: list[BarrierResult] = []

    # --- Barrier 0: Family viability (v2.1) ---
    b0 = barrier_family_viability(card)
    barriers_run.append(b0)
    if not b0.passed:
        card.sandbox_result = SandboxResult(
            status='LOW_FAMILY_COVERAGE',
            barriers=barriers_run,
            impact_report={},
            generated_canaries=[],
            generated_hard_negatives=[],
            warning_flags=['LOW_FAMILY_COVERAGE'],
        )
        return card

    # --- Barrier 1: Canary pass ---
    b1 = barrier_canary_pass(card, canaries, detector_func, policy)
    barriers_run.append(b1)
    if not b1.passed:
        card.sandbox_result = SandboxResult(
            status='REJECTED_CANARY',
            barriers=barriers_run,
            impact_report={},
            generated_canaries=[],
            generated_hard_negatives=[],
        )
        return card

    # --- Barrier 2: Target hit ---
    b2 = barrier_target_hit(card, detector_func, policy)
    barriers_run.append(b2)
    if not b2.passed:
        if b2.score is not None and b2.score >= 0.60:
            status = 'NEEDS_REFINEMENT'
        else:
            status = 'LOW_TARGET_HIT'
        card.sandbox_result = SandboxResult(
            status=status,
            barriers=barriers_run,
            impact_report={},
            generated_canaries=[],
            generated_hard_negatives=[],
        )
        return card

    # --- Barrier 3: Near-miss safety ---
    b3 = barrier_near_miss_safety(card, detector_func, policy, corpus_df, random_n=neg_n)
    barriers_run.append(b3)
    if not b3.passed:
        card.sandbox_result = SandboxResult(
            status='FALSE_POSITIVE_RISK',
            barriers=barriers_run,
            impact_report={},
            generated_canaries=[],
            generated_hard_negatives=[],
        )
        return card

    # --- Barrier 4 (v2.2): regression gate - blocking ---
    skipped_regression = regression_cases is None
    if skipped_regression:
        b4 = BarrierResult(
            barrier='regression_gate', passed=True, score=None, kind='blocking',
            detail='SKIPPED: no regression set supplied - not a pass; production runs must supply one',
        )
    else:
        b4 = barrier_regression_gate(card, regression_cases, detector_func, policy,
                                     set_sha256=regression_set_sha256)
    barriers_run.append(b4)
    if not b4.passed:
        card.sandbox_result = SandboxResult(
            status='REJECTED_REGRESSION',
            barriers=barriers_run,
            impact_report={},
            generated_canaries=[],
            generated_hard_negatives=[],
        )
        return card

    # --- Telemetry T1: site skew (never rejects) ---
    t_skew = site_skew_warning(card)
    barriers_run.append(t_skew)

    # --- Telemetry T2: corpus-wide impact forecast (never rejects) ---
    t_impact = corpus_wide_impact_forecast(card, detector_func, policy, corpus_df)
    barriers_run.append(t_impact)

    # Determine final status and warning flags (v2.1 per GPT-5 verdict 2026-04-17:
    # SKEW_WARNING conflates two things. Separate decision_status from warning_flags.)
    warning_flags: list[str] = []

    local_pattern = (
        t_skew.detail is not None and
        ('WARNING' in t_skew.detail or 'skew' in t_skew.detail.lower()) and
        t_skew.score is not None and t_skew.score > 0.8
    )
    if local_pattern:
        warning_flags.append('LOCAL_PATTERN_WARNING')
    if skipped_regression:
        warning_flags.append('NO_REGRESSION_SET')

    # v2.2: telemetry never decides the status. SKEW_WARNING is no longer a final
    # status; the signal lives in warning_flags (LOCAL_PATTERN_WARNING).
    final_status = 'APPROVED'

    impact_report: dict = {}
    if t_impact.score is not None:
        impact_report['fill_rate_delta'] = t_impact.score
    if t_impact.detail:
        impact_report['shadow_detail'] = t_impact.detail

    card.sandbox_result = SandboxResult(
        status=final_status,
        barriers=barriers_run,
        impact_report=impact_report,
        generated_canaries=[],
        generated_hard_negatives=[],
        warning_flags=warning_flags,
    )
    return card
