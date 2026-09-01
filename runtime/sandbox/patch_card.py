"""DDPA Sandbox — PatchCard dataclasses and generator for proposal validation pipeline."""
from __future__ import annotations
import hashlib
import random
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProposedChange:
    change_type: str              # new_regex | new_alias | scope_expansion | normalization_fix
    description: str              # human-readable hypothesis
    regex_skeleton: str | None    # suggested regex (not final)
    capture_map: dict | None      # {"group_1": "value_mm"}
    target_scope: list[str]       # allowed sections
    precedence_hint: str | None   # where in detector priority
    near_miss_patterns: list[str] # patterns that must NOT match
    risk_level: str               # low | medium | high


@dataclass
class BarrierResult:
    barrier: str          # canary_pass | target_hit | near_miss_safety | corpus_skew | corpus_wide_shadow
    passed: bool
    score: float | None
    detail: str


@dataclass
class SandboxResult:
    status: str                            # APPROVED | REJECTED_CANARY | LOW_TARGET_HIT | FALSE_POSITIVE_RISK | NEEDS_REFINEMENT | LOW_FAMILY_COVERAGE
    barriers: list[BarrierResult]
    impact_report: dict
    generated_canaries: list[dict]
    generated_hard_negatives: list[dict]
    warning_flags: list[str] = field(default_factory=list)  # LOCAL_PATTERN_WARNING | LOW_FAMILY_COVERAGE


@dataclass
class ExecutionResult:
    baseline_value: Any
    baseline_status: str
    proposed_value: Any
    proposed_status: str
    changed: bool
    overlay_matched: bool
    section_compliant: bool
    ontology_compliant: bool


@dataclass
class PatchCard:
    card_id: str
    variable: str
    source_cluster: dict
    proposed_change: ProposedChange
    evidence_discovery: list[dict]
    evidence_eval: list[dict]
    hard_negatives: list[dict]
    site_distribution: dict
    impact_estimate: int
    plausibility_tag: str
    support_tag: str
    sandbox_result: SandboxResult | None
    human_decision: str | None
    overlaps_with: list[str] = field(default_factory=list)


@dataclass
class DecisionRecord:
    decision_id: str
    source_type: str        # gate2 | patch_card
    change_domain: str      # policy | detector
    card_id: str
    human_decision: str     # approve | reject | defer
    applied_via: str        # patches_py | manual_merge
    timestamp: str
    new_canaries: list[dict] = field(default_factory=list)
    new_hard_negatives: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# PatchCard generator helpers
# ---------------------------------------------------------------------------

def _deterministic_id(variable: str, ia_value: str, seed: int) -> str:
    """Return 'pc_' + first 12 hex chars of SHA256(variable:ia_value:seed)."""
    raw = f'{variable}:{ia_value}:{seed}'
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    return f'pc_{digest[:12]}'


def _split_discovery_eval(
    miss_rows: list[dict],
    ratio: float,
    seed: int,
) -> tuple[list[dict], list[dict]]:
    """Deterministic shuffle then split at ratio boundary."""
    shuffled = list(miss_rows)
    rng = random.Random(seed)
    rng.shuffle(shuffled)
    split_at = int(len(shuffled) * ratio)
    return shuffled[:split_at], shuffled[split_at:]


def _build_hard_negatives(
    corpus_df: 'pd.DataFrame',
    var_name: str,
    cluster_evidence_windows: list[str],
    max_negatives: int = 20,
    seed: int = 42,
) -> list[dict]:
    """
    Find cases where L1 correctly extracted (accepted_exact or accepted_semantic)
    AND text shares tokens with cluster evidence windows.
    Score by token overlap, return top N.
    """
    resolution_col = f'{var_name}__resolution'
    text_col = 'texto_laudo'

    if resolution_col not in corpus_df.columns or text_col not in corpus_df.columns:
        return []

    accepted_mask = corpus_df[resolution_col].isin(['accepted_exact', 'accepted_semantic'])
    candidates = corpus_df[accepted_mask].copy()

    if candidates.empty:
        return []

    # Build token set from all evidence windows
    evidence_tokens: set[str] = set()
    for window in cluster_evidence_windows:
        evidence_tokens.update(re.findall(r'\w+', window.lower()))

    def _token_overlap(text: str) -> int:
        if not isinstance(text, str):
            return 0
        tokens = set(re.findall(r'\w+', text.lower()))
        return len(tokens & evidence_tokens)

    candidates = candidates.copy()
    candidates['_overlap'] = candidates[text_col].apply(_token_overlap)
    candidates = candidates[candidates['_overlap'] > 0]

    if candidates.empty:
        return []

    candidates = candidates.sort_values('_overlap', ascending=False)
    top = candidates.head(max_negatives)

    result = []
    for _, row in top.iterrows():
        entry: dict[str, Any] = {
            'id_registro': row.get('id_registro'),
            'texto_laudo': row.get(text_col),
            'resolution': row.get(resolution_col),
        }
        value_col = f'{var_name}__value'
        if value_col in row.index:
            entry['value'] = row[value_col]
        ia_col = f'{var_name}__ia'
        if ia_col in row.index:
            entry['ia_value'] = row[ia_col]
        if 'clinica' in row.index:
            entry['clinica'] = row['clinica']
        result.append(entry)

    return result


def _propose_regex_skeleton(
    ia_value: str,
    evidence_windows: list[str],
    var_name: str,
) -> str | None:
    """
    Heuristic v1 regex proposal based on variable naming convention.
    - _mm or tamanho in name  -> measurement pattern
    - bbps / score in name   -> score pattern
    - fallback               -> escaped literal ia_value
    """
    name_lower = var_name.lower()

    if name_lower.endswith('_mm') or 'tamanho' in name_lower:
        return r'(\d+(?:[.,]\d+)?)\s*mm'

    if any(kw in name_lower for kw in ('bbps', 'score', 'escore')):
        return r'(\d+)\s*(?:pontos?|score|escore)?'

    # generic fallback: escaped literal
    return re.escape(ia_value)


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_patch_cards(
    filtered_clusters: list[dict],
    corpus_df: 'pd.DataFrame',
    var_name: str,
    seed: int = 42,
    discovery_eval_ratio: float = 0.7,
    max_hard_negatives: int = 20,
) -> list[PatchCard]:
    """
    Generate PatchCards from plausibility-filtered clusters.

    Parameters
    ----------
    filtered_clusters:
        Output of plausibility.filter_clusters() — each dict has keys:
        ia_value, variable, count, evidence_samples, evidence_windows,
        plausibility_tag, support_tag.
    corpus_df:
        Full corpus DataFrame with columns texto_laudo, {var}__value,
        {var}__ia, {var}__resolution, clinica, id_registro.
    var_name:
        Variable name (e.g. 'polipo_tamanho_max_mm').
    seed:
        RNG seed for deterministic splits and IDs.
    discovery_eval_ratio:
        Fraction of miss rows assigned to discovery set (default 0.7).
    max_hard_negatives:
        Maximum hard-negative examples per card.

    Returns
    -------
    list[PatchCard]
        One PatchCard per input cluster; overlaps_with populated at end.
    """
    value_col = f'{var_name}__value'
    ia_col = f'{var_name}__ia'

    cards: list[PatchCard] = []

    for cluster in filtered_clusters:
        ia_value = str(cluster['ia_value'])

        # 1. Find miss rows: value is null AND ia matches cluster ia_value
        if value_col in corpus_df.columns and ia_col in corpus_df.columns:
            value_null = corpus_df[value_col].isna()
            # ia column may be numeric or string; compare as string
            ia_match = corpus_df[ia_col].astype(str) == ia_value
            miss_mask = value_null & ia_match
            miss_frame = corpus_df[miss_mask]
        else:
            miss_frame = corpus_df.iloc[0:0]  # empty

        miss_rows: list[dict] = miss_frame.to_dict(orient='records')

        # 2. Split into discovery / eval
        discovery, eval_set = _split_discovery_eval(miss_rows, discovery_eval_ratio, seed)

        # 3. Compute site distribution from miss rows
        if not miss_frame.empty and 'clinica' in miss_frame.columns:
            vc = miss_frame['clinica'].value_counts()
            site_distribution: dict[str, int] = {k: int(v) for k, v in vc.items()}
        else:
            site_distribution = {}

        # 4. Build hard negatives
        hard_negatives = _build_hard_negatives(
            corpus_df,
            var_name,
            cluster.get('evidence_windows', []),
            max_negatives=max_hard_negatives,
            seed=seed,
        )

        # 5. Propose regex skeleton
        regex_skel = _propose_regex_skeleton(
            ia_value,
            cluster.get('evidence_windows', []),
            var_name,
        )

        # 6. Build ProposedChange
        _ew = cluster.get('evidence_windows') or ['']
        description = (
            f"Auto-proposed regex for {var_name} cluster ia_value={ia_value}; "
            f"covers patterns like: {_ew[0]!r}"
        )
        proposed_change = ProposedChange(
            change_type='new_regex',
            description=description,
            regex_skeleton=regex_skel,
            capture_map={'group_1': 'value'},
            target_scope=['descricao', 'conclusao'],
            precedence_hint=None,
            near_miss_patterns=[],
            risk_level='medium',
        )

        # 7. Build PatchCard
        card_id = _deterministic_id(var_name, ia_value, seed)
        card = PatchCard(
            card_id=card_id,
            variable=var_name,
            source_cluster=cluster,
            proposed_change=proposed_change,
            evidence_discovery=discovery,
            evidence_eval=eval_set,
            hard_negatives=hard_negatives,
            site_distribution=site_distribution,
            impact_estimate=len(miss_rows),
            plausibility_tag=cluster.get('plausibility_tag', ''),
            support_tag=cluster.get('support_tag', ''),
            sandbox_result=None,
            human_decision=None,
            overlaps_with=[],
        )
        cards.append(card)

    # 8. Detect overlaps: cards sharing the same regex_skeleton
    skeleton_to_cards: dict[str | None, list[int]] = {}
    for idx, card in enumerate(cards):
        skel = card.proposed_change.regex_skeleton
        skeleton_to_cards.setdefault(skel, []).append(idx)

    for skel, indices in skeleton_to_cards.items():
        if skel is not None and len(indices) > 1:
            for idx in indices:
                others = [cards[j].card_id for j in indices if j != idx]
                cards[idx].overlaps_with.extend(others)

    return cards
