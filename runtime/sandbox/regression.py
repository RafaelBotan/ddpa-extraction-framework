"""DDPA Sandbox — regression gate (v2.2).

A blocking barrier that re-executes a proposed detector change on a versioned,
held-aside regression set and compares the PROPOSED output against the EXPECTED
output of each case. It never uses the baseline as the truth: a proposal that
turns an abstention into the correct value passes; a proposal that emits a value
where the expected is an abstention (hard negative, boilerplate, external
control) fails; a proposal that changes a correct value fails.

Attribution (three buckets per case)
------------------------------------
conform        proposed output == expected.
pre_existing   proposed output != expected BUT proposed output == baseline output:
               the frozen detector already gets this case wrong and the proposal
               did not touch it. Not attributable to the card; reported, never
               counted as a regression (otherwise every card would be blocked by
               defects it cannot reach — an overlay only acts where the baseline
               abstains).
regressed      proposed output != expected AND != baseline: the card introduced
               the divergence. Any regressed case blocks the card.

Case kinds
----------
synthetic_canary / synthetic_known_bug / adjudicated_dev / metamorphic_expected_change
    ``expected_value`` (and optionally ``expected_status``) are the truth, by
    construction or by review.
metamorphic_invariance
    ``reference_text`` is the untransformed report. The expected output is NOT
    stored: it is computed at gate time as the proposal's own output on the
    reference text, and the check is proposal(text) == proposal(reference_text).
    This is a CONSISTENCY oracle (metamorphic conformity), not a correctness
    oracle: it detects instability under perturbations that must not change the
    answer, and says nothing about whether that answer is right.

The detector function applies its own normalisation and sectioning (L0), so the
gate exercises the pipeline from raw text, not from pre-cut windows.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from sandbox.patch_card import BarrierResult, PatchCard
from sandbox.proposal_executor import execute_proposal_single

BARRIER_NAME = 'regression_gate'
STATUS_ON_FAIL = 'REJECTED_REGRESSION'

VALID_KINDS = (
    'synthetic_canary',
    'synthetic_known_bug',
    'adjudicated_dev',
    'metamorphic_expected_change',
    'metamorphic_invariance',
)


@dataclass
class RegressionCase:
    id: str
    kind: str
    text: str
    expected_value: Any = None
    expected_status: str | None = None
    reference_text: str | None = None
    source: str = ''
    note: str = ''

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            raise ValueError(f'{self.id}: unknown regression kind {self.kind!r}')
        if self.kind == 'metamorphic_invariance' and not self.reference_text:
            raise ValueError(f'{self.id}: metamorphic_invariance requires reference_text')
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError(f'{self.id}: empty text')


def load_regression_set(path: str | Path) -> tuple[list[RegressionCase], str]:
    """Load a JSONL regression set. Returns (cases, sha256 of the file bytes).

    Fails closed: a malformed line, a duplicated id or an unknown kind raises.
    """
    p = Path(path)
    raw = p.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    cases: list[RegressionCase] = []
    seen: set[str] = set()
    for n, line in enumerate(raw.decode('utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f'{p.name}:{n}: invalid JSON ({exc})') from exc
        case = RegressionCase(**{k: obj.get(k) for k in RegressionCase.__dataclass_fields__ if k in obj})
        if case.id in seen:
            raise ValueError(f'{p.name}:{n}: duplicated case id {case.id!r}')
        seen.add(case.id)
        cases.append(case)
    if not cases:
        raise ValueError(f'{p.name}: regression set is empty')
    return cases, digest


def canonical_value(value: Any) -> Any:
    """Canonical form for comparison: None stays None; numerics compare as float
    (so 8, 8.0 and '8' agree); everything else as a stripped, case-folded string."""
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).casefold()
    try:
        return round(float(str(value).replace(',', '.')), 4)
    except (TypeError, ValueError):
        return str(value).strip().casefold()


def status_family(status: Any) -> str | None:
    """Collapse resolution/detector statuses into families the gate can compare."""
    if status is None:
        return None
    s = str(status).casefold()
    if s.startswith('accept'):
        return 'accepted'
    if s.startswith('abstain'):
        return 'abstained'
    if s.startswith('escalat'):
        return 'escalated'
    return s


@dataclass
class _Run:
    baseline_value: Any
    baseline_status: Any
    proposed_value: Any
    proposed_status: Any


def _run(text: str, card: PatchCard, detector_func: Callable[[str], Any], policy) -> _Run:
    res = execute_proposal_single(text, card, detector_func, policy)
    return _Run(res.baseline_value, res.baseline_status, res.proposed_value, res.proposed_status)


def _same(a_v: Any, a_s: Any, b_v: Any, b_s: Any, check_status: bool) -> bool:
    if canonical_value(a_v) != canonical_value(b_v):
        return False
    return (not check_status) or (status_family(a_s) == status_family(b_s))


def barrier_regression_gate(
    card: PatchCard,
    cases: Iterable[RegressionCase],
    detector_func: Callable[[str], Any],
    policy,
    set_sha256: str | None = None,
) -> BarrierResult:
    """Blocking barrier. ``passed`` is False as soon as one case is *regressed*
    (divergence introduced by the card). Pre-existing baseline defects are
    reported in ``detail`` but never block.
    """
    cases = list(cases)
    if not cases:
        return BarrierResult(
            barrier=BARRIER_NAME,
            passed=False,
            score=None,
            detail='REJECTED_REGRESSION: no regression set loaded — fail closed',
        )

    regressed: list[str] = []
    pre_existing: list[str] = []
    conform = 0
    for case in cases:
        got = _run(case.text, card, detector_func, policy)
        if case.kind == 'metamorphic_invariance':
            ref = _run(case.reference_text, card, detector_func, policy)
            exp_v, exp_s, check_status = ref.proposed_value, ref.proposed_status, True
        else:
            exp_v, exp_s = case.expected_value, case.expected_status
            check_status = case.expected_status is not None

        if _same(got.proposed_value, got.proposed_status, exp_v, exp_s, check_status):
            conform += 1
            continue
        untouched = _same(got.proposed_value, got.proposed_status,
                          got.baseline_value, got.baseline_status, check_status)
        label = (f'{case.id}[{case.kind}]: expected={exp_v!r}/{status_family(exp_s)} '
                 f'got={got.proposed_value!r}/{status_family(got.proposed_status)}')
        if untouched:
            pre_existing.append(label)
        else:
            regressed.append(label)

    n = len(cases)
    score = 1.0 - len(regressed) / n
    suffix = f' (set sha256 {set_sha256[:12]})' if set_sha256 else ''
    summary = f'{conform} conform, {len(pre_existing)} pre-existing baseline defect(s), {len(regressed)} regressed of {n}'
    if regressed:
        return BarrierResult(
            barrier=BARRIER_NAME,
            passed=False,
            score=score,
            detail=(f'{STATUS_ON_FAIL}: {summary}{suffix} — '
                    + '; '.join(regressed[:20]) + (' …' if len(regressed) > 20 else '')),
        )
    detail = f'No regression introduced: {summary}{suffix}'
    if pre_existing:
        detail += ' — pre-existing: ' + '; '.join(pre_existing[:10]) + (' …' if len(pre_existing) > 10 else '')
    if conform == 0:
        detail += ' — WARNING: no case conformed; the set gives no attributable evidence for this card'
    return BarrierResult(barrier=BARRIER_NAME, passed=True, score=score, detail=detail)
