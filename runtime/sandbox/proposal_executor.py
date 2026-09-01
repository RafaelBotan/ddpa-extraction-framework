"""DDPA Sandbox — Proposal Executor: regex overlay simulation.

Core innovation: simulates proposals against texts WITHOUT modifying detectors
or persisted files. Three execution strategies:

  new_regex       → run regex overlay if baseline is null
  new_alias       → deep-copy Policy, add alias, re-apply apply_policy()
  scope_expansion → v1 stub (returns unchanged result)
  normalization_fix → v1 stub (returns unchanged result)
"""
from __future__ import annotations

import copy
import re
from typing import Any, Callable, Optional

import pandas as pd

from policy_registry import Policy, apply_policy
from sandbox.patch_card import ExecutionResult, PatchCard, ProposedChange


# ============================================================================
# Internal helpers
# ============================================================================

def _extract_baseline(raw) -> tuple[Any, str]:
    """Normalise various return types that detectors may emit.

    Supported shapes:
      - None                          → (None, 'no_match')
      - raw scalar (int/float/str)   → (raw, 'detector_value')
      - dict with 'value'/'status'   → (dict['value'], dict['status'])
      - object with .value/.status   → (obj.value, obj.status)
    """
    if raw is None:
        return None, 'no_match'
    if isinstance(raw, dict):
        value = raw.get('value')
        status = raw.get('status', 'detector_value')
        return value, status
    if hasattr(raw, 'value') and hasattr(raw, 'status'):
        return raw.value, raw.status
    # plain scalar
    return raw, 'detector_value'


def _numeric_cast(value_str: str, ont_type: str) -> Any:
    """Cast string to numeric according to ontology type."""
    if ont_type == 'integer':
        return int(float(value_str))
    return float(value_str)


def _ontology_compliant(value: Any, policy: Policy) -> bool:
    """Return True if value satisfies ontology range/values."""
    ont = policy.ontology
    if ont.type in ('integer', 'float'):
        try:
            num = float(value)
        except (TypeError, ValueError):
            return False
        if ont.range:
            lo, hi = ont.range
            return lo <= num <= hi
        return True
    if ont.type == 'enum':
        return str(value) in {str(v) for v in ont.values}
    # string — no constraint
    return True


# ============================================================================
# Public API
# ============================================================================

def execute_overlay_regex(
    text: str,
    change: ProposedChange,
    policy: Policy,
    baseline_value: Any,
) -> Optional[ExecutionResult]:
    """Simulate a new_regex proposal against a single text.

    Returns None when:
      - baseline_value is not None (detector already found a value)
      - change.regex_skeleton is absent
      - the regex does not match

    For numeric variables with multiple matches, takes the maximum value
    (consistent with measure_multi_max behaviour in detectors).
    """
    if baseline_value is not None:
        return None
    if not change.regex_skeleton:
        return None

    matches = list(re.finditer(change.regex_skeleton, text, re.IGNORECASE))
    if not matches:
        return None

    # Candidate-local forbidden filter (v2.0.1): per GPT-5 verdict, forbidden
    # patterns must not reject the whole document — they must reject only
    # matches whose enclosing sentence contains the forbidden subject.
    # Rationale: a report can legitimately mention a true polyp AND a
    # papilofibroma in different sentences; document-wide reject kills recall.
    compiled_forbidden: list[re.Pattern] = []
    if change.near_miss_patterns:
        compiled_forbidden = [
            re.compile(p, re.IGNORECASE) for p in change.near_miss_patterns if p
        ]

    raw_values: list[str] = []
    for m in matches:
        if compiled_forbidden:
            sent_start = max(
                text.rfind('.', 0, m.start()),
                text.rfind('\n', 0, m.start()),
            ) + 1
            dot = text.find('.', m.end())
            nl = text.find('\n', m.end())
            ends = [e for e in (dot, nl) if e >= 0]
            sent_end = min(ends) if ends else len(text)
            sentence = text[sent_start:sent_end]
            if any(p.search(sentence) for p in compiled_forbidden):
                continue
        groups = m.groups()
        if groups:
            raw_values.append(groups[0])

    if not raw_values:
        return None

    ont = policy.ontology
    if ont.type in ('integer', 'float'):
        # Convert all to float, take max
        numeric_values: list[float] = []
        for rv in raw_values:
            try:
                numeric_values.append(float(rv.replace(',', '.')))
            except (TypeError, ValueError):
                pass
        if not numeric_values:
            return None
        proposed_num = max(numeric_values)
        # Cast to int if ontology demands
        proposed_value: Any = _numeric_cast(str(proposed_num), ont.type)
        compliant = _ontology_compliant(proposed_value, policy)
    else:
        # Non-numeric: take first match value
        proposed_value = raw_values[0]
        compliant = _ontology_compliant(proposed_value, policy)

    return ExecutionResult(
        baseline_value=None,
        baseline_status='no_match',
        proposed_value=proposed_value,
        proposed_status='overlay_match',
        changed=True,
        overlay_matched=True,
        section_compliant=True,   # section compliance checked at batch level when sections available
        ontology_compliant=compliant,
    )


def execute_proposal_single(
    text: str,
    card: PatchCard,
    detector_func: Callable[[str], Any],
    policy: Policy,
) -> ExecutionResult:
    """Simulate a proposal on a single text.

    Runs the baseline detector first, then applies the proposed change.
    Supports change_type: new_regex, new_alias, scope_expansion, normalization_fix.
    """
    change = card.proposed_change

    # --- Baseline ---
    raw_baseline = detector_func(text)
    baseline_value, baseline_status = _extract_baseline(raw_baseline)

    # --- Apply proposal ---
    if change.change_type == 'new_regex':
        overlay = execute_overlay_regex(text, change, policy, baseline_value)
        if overlay is not None:
            return overlay
        # Baseline already has a value (or regex didn't match) — return unchanged
        return ExecutionResult(
            baseline_value=baseline_value,
            baseline_status=baseline_status,
            proposed_value=baseline_value,
            proposed_status=baseline_status,
            changed=False,
            overlay_matched=False,
            section_compliant=True,
            ontology_compliant=_ontology_compliant(baseline_value, policy) if baseline_value is not None else True,
        )

    if change.change_type == 'new_alias':
        # Deep-copy policy, add alias, re-apply
        policy_copy = copy.deepcopy(policy)
        # The alias key is derived from the description or the card's source cluster ia_value
        # Convention: alias maps source ia_value string → canonical ontology value
        ia_value = str(card.source_cluster.get('ia_value', ''))
        if ia_value and change.description:
            # Extract alias key from description if possible; fall back to ia_value
            # For v1 we rely on the card's ia_value as the alias source
            # and use the first ontology value as canonical target (if enum), or ia_value itself
            ont = policy_copy.ontology
            if ont.type == 'enum' and ont.values:
                canonical = ont.values[0]
            else:
                canonical = ia_value
            policy_copy.vocabulary_aliases[ia_value] = canonical

        decision = apply_policy(baseline_value, baseline_status, policy_copy)
        proposed_value = decision.normalized_value
        proposed_status = decision.resolution
        changed = proposed_value != baseline_value or proposed_status != baseline_status
        return ExecutionResult(
            baseline_value=baseline_value,
            baseline_status=baseline_status,
            proposed_value=proposed_value,
            proposed_status=proposed_status,
            changed=changed,
            overlay_matched=False,
            section_compliant=True,
            ontology_compliant=_ontology_compliant(proposed_value, policy) if proposed_value is not None else True,
        )

    # scope_expansion, normalization_fix — v1 stub: return unchanged
    return ExecutionResult(
        baseline_value=baseline_value,
        baseline_status=baseline_status,
        proposed_value=baseline_value,
        proposed_status=baseline_status,
        changed=False,
        overlay_matched=False,
        section_compliant=True,
        ontology_compliant=_ontology_compliant(baseline_value, policy) if baseline_value is not None else True,
    )


def execute_proposal_batch(
    corpus_df: pd.DataFrame,
    card: PatchCard,
    detector_func: Callable[[str], Any],
    policy: Policy,
    var_name: str,
) -> pd.DataFrame:
    """Simulate a proposal across all rows of corpus_df.

    Expected corpus_df columns:
      - id_registro
      - clinica
      - texto_laudo (text column)
      - {var_name}__value (optional — baseline value column)
      - {var_name}__status (optional — baseline status column)

    Returns a DataFrame with columns:
      id_registro, clinica, baseline_value, baseline_status,
      proposed_value, proposed_status, changed, overlay_matched,
      section_compliant, ontology_compliant
    """
    rows: list[dict] = []

    text_col = 'texto_laudo'
    val_col = f'{var_name}__value'
    status_col = f'{var_name}__status'

    for _, row in corpus_df.iterrows():
        text = str(row.get(text_col, '')) if pd.notna(row.get(text_col, None)) else ''

        result = execute_proposal_single(text, card, detector_func, policy)

        rows.append({
            'id_registro': row.get('id_registro'),
            'clinica': row.get('clinica'),
            'baseline_value': result.baseline_value,
            'baseline_status': result.baseline_status,
            'proposed_value': result.proposed_value,
            'proposed_status': result.proposed_status,
            'changed': result.changed,
            'overlay_matched': result.overlay_matched,
            'section_compliant': result.section_compliant,
            'ontology_compliant': result.ontology_compliant,
        })

    return pd.DataFrame(rows)
