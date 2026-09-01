"""Plausibility filter — remove impossible IA values and assess cluster quality."""
from __future__ import annotations
import re
from policy_registry import Policy


def classify_plausibility(ia_value_str: str, policy: Policy) -> str:
    """Classify a single IA value as plausible, impossible, or unverified."""
    ont = policy.ontology

    if ont.type in ('integer', 'float'):
        try:
            num = float(ia_value_str)
        except (TypeError, ValueError):
            return 'impossible'
        if ont.range:
            lo, hi = ont.range
            if not (lo <= num <= hi):
                return 'impossible'
            return 'plausible'
        return 'unverified'

    if ont.type == 'enum':
        if not ont.values:
            return 'unverified'
        if ia_value_str in {str(v) for v in ont.values}:
            return 'plausible'
        return 'impossible'

    return 'unverified'


def _extract_pattern_tokens(text: str) -> set[str]:
    """Extract rough pattern tokens from an evidence window for similarity."""
    tokens = set(re.findall(r'[a-záàâãéêíóôõúç]{3,}', text.lower()))
    return tokens


def classify_support(cluster: dict, min_count: int = 10) -> str:
    """Classify cluster quality as coherent_cluster, noisy_cluster, or low_support."""
    if cluster['count'] < min_count:
        return 'low_support'

    windows = cluster.get('evidence_windows', [])
    if len(windows) < 2:
        return 'coherent_cluster'

    # Group windows into families by Jaccard similarity
    token_sets = [_extract_pattern_tokens(w) for w in windows]
    families: list[set[str]] = []
    for ts in token_sets:
        if not ts:
            continue
        merged = False
        for fam in families:
            intersection = len(ts & fam)
            union = len(ts | fam)
            if union > 0 and intersection / union >= 0.3:
                fam.update(ts)
                merged = True
                break
        if not merged:
            families.append(set(ts))

    if len(families) > 5:
        return 'noisy_cluster'
    return 'coherent_cluster'


def classify_grounding(cluster: dict, threshold_low: float = 0.3, threshold_high: float = 0.7) -> str:
    """Classify cluster by grounding_rate (fraction of cases with textual evidence).

    Returns:
      'well_grounded' if rate >= threshold_high
      'weakly_grounded' if threshold_low <= rate < threshold_high
      'ungrounded' if rate < threshold_low
      'unknown' if rate is None (no unit/pattern available for this variable)
    """
    rate = cluster.get('grounding_rate')
    if rate is None:
        return 'unknown'
    if rate >= threshold_high:
        return 'well_grounded'
    if rate >= threshold_low:
        return 'weakly_grounded'
    return 'ungrounded'


def filter_clusters_all(
    clusters: dict[str, dict],
    policy: Policy,
    var_name: str,
    min_count: int = 10,
) -> list[dict]:
    """Tag all clusters with plausibility, support, and grounding tags."""
    result = []
    for ia_val, info in clusters.items():
        tagged = {
            'ia_value': ia_val,
            'variable': var_name,
            'count': info['count'],
            'evidence_samples': info.get('evidence_samples', []),
            'evidence_windows': info.get('evidence_windows', []),
            'grounding_rate': info.get('grounding_rate'),
            'plausibility_tag': classify_plausibility(ia_val, policy),
            'support_tag': classify_support(info, min_count),
            'grounding_tag': classify_grounding(info),
        }
        result.append(tagged)
    return result


def filter_clusters(
    clusters: dict[str, dict],
    policy: Policy,
    var_name: str,
    min_count: int = 10,
    reject_ungrounded: bool = True,
) -> list[dict]:
    """Tag and filter clusters. Returns only plausible + supported + grounded clusters.

    When reject_ungrounded is True (default) and grounding_rate is known,
    ungrounded clusters are dropped. Clusters with grounding_rate=None
    (unknown — no unit in ontology) are not rejected here.
    """
    all_tagged = filter_clusters_all(clusters, policy, var_name, min_count)
    kept = []
    for c in all_tagged:
        if c['plausibility_tag'] != 'plausible':
            continue
        if c['support_tag'] == 'low_support':
            continue
        if reject_ungrounded and c['grounding_tag'] == 'ungrounded':
            continue
        kept.append(c)
    return kept
