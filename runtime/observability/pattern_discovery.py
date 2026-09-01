"""Residual pattern discovery — find what L1 misses, scope leaks, silence suspects."""
from __future__ import annotations
import re
from collections import Counter
import pandas as pd


def find_l1_misses_ia_hits(df: pd.DataFrame, var_name: str) -> pd.DataFrame:
    """Find rows where L1 produced null but IA produced a value."""
    vcol = f'{var_name}__value'
    iacol = f'{var_name}__ia'
    if iacol not in df.columns:
        return df.iloc[0:0]
    mask = df[vcol].isna() & df[iacol].notna()
    return df[mask].copy()


def extract_evidence_windows(
    texto: str,
    search_value: str,
    window_chars: int = 50,
) -> list[str]:
    """Extract text windows around occurrences of search_value in texto."""
    if not texto or not search_value:
        return []
    texto_lower = texto.lower()
    search_lower = str(search_value).lower()
    windows = []
    start = 0
    while True:
        idx = texto_lower.find(search_lower, start)
        if idx == -1:
            break
        win_start = max(0, idx - window_chars)
        win_end = min(len(texto), idx + len(search_lower) + window_chars)
        windows.append(texto[win_start:win_end])
        start = idx + 1
    return windows


def _build_grounding_pattern(ia_value_str: str, unit: str | None) -> re.Pattern | None:
    """Build a regex to detect whether ia_value is textually grounded.

    If unit is provided, match the integer value near the unit token:
        "5mm", "5 mm", "5.0mm", "5,0 mm" all count as grounded for ia_value=5.
    If unit is None, no generic grounding can be derived — return None (caller
    should treat grounding_rate as unknown for this variable).
    """
    try:
        num = float(ia_value_str)
    except (TypeError, ValueError):
        return None
    int_val = int(num) if num.is_integer() else None
    if unit is None:
        return None
    unit_esc = re.escape(str(unit))
    if int_val is not None:
        pattern = rf'\b{int_val}\s*(?:[.,]\s*\d+)?\s*{unit_esc}\b'
    else:
        # non-integer value: match as literal with optional zero-padding
        val_esc = re.escape(ia_value_str.rstrip('0').rstrip('.'))
        pattern = rf'\b{val_esc}\s*(?:[.,]\s*\d+)?\s*{unit_esc}\b'
    return re.compile(pattern, flags=re.IGNORECASE)


def cluster_by_ia_value(
    misses_df: pd.DataFrame,
    var_name: str,
    max_samples: int = 5,
    policy=None,
) -> dict[str, dict]:
    """Group L1 misses by IA value, with counts, evidence samples, and optional
    grounding_rate (fraction of rows where ia_value is textually detectable).

    If policy is provided and its ontology has a unit, grounding is computed
    for every row in the cluster and a representative evidence window is
    extracted from the first grounded row. Otherwise evidence_windows falls
    back to the legacy literal-search behavior and grounding_rate is None.
    """
    iacol = f'{var_name}__ia'
    unit = None
    if policy is not None:
        unit = getattr(policy.ontology, 'unit', None)
    clusters = {}
    for ia_val, group in misses_df.groupby(iacol):
        ia_str = str(ia_val)
        pattern = _build_grounding_pattern(ia_str, unit) if unit else None

        grounded_count = 0
        evidence_windows: list[str] = []
        for _, row in group.iterrows():
            texto = str(row.get('texto_laudo', '') or '')
            if pattern is not None:
                m = pattern.search(texto)
                if m:
                    grounded_count += 1
                    if len(evidence_windows) < max_samples:
                        start = max(0, m.start() - 50)
                        end = min(len(texto), m.end() + 50)
                        evidence_windows.append(texto[start:end])
            elif ia_str in texto.lower():
                # Legacy fallback: literal substring search
                for w in extract_evidence_windows(texto, ia_str):
                    if len(evidence_windows) < max_samples:
                        evidence_windows.append(w)

        samples = group.head(max_samples)
        total = len(group)
        grounding_rate = (grounded_count / total) if (pattern is not None and total > 0) else None

        clusters[ia_str] = {
            'count': total,
            'evidence_samples': [
                str(row.get('texto_laudo', ''))[:200]
                for _, row in samples.iterrows()
            ],
            'evidence_windows': evidence_windows[:max_samples],
            'grounding_rate': grounding_rate,
            'grounded_count': grounded_count if pattern is not None else None,
        }
    return clusters


def detect_scope_leaks(
    df: pd.DataFrame,
    var_name: str,
    expected_sections: list[str],
) -> pd.DataFrame:
    """Find rows where L1 extracted from a section not in expected_sections."""
    scol = f'{var_name}__section'
    vcol = f'{var_name}__value'
    if scol not in df.columns:
        return df.iloc[0:0]
    has_value = df[vcol].notna()
    has_section = df[scol].notna()
    in_scope = df[scol].isin(expected_sections)
    return df[has_value & has_section & ~in_scope].copy()


def detect_silence_suspects(
    df: pd.DataFrame,
    var_name: str,
    suspect_threshold: float = 0.50,
) -> dict:
    """Detect if the abstention/silence rate is suspiciously high."""
    rcol = f'{var_name}__resolution'
    n_total = len(df)
    if n_total == 0:
        return {'abstention_rate': 0.0, 'is_suspect': False, 'n_abstained': 0, 'n_total': 0}
    abstention_mask = df[rcol].astype(str).str.startswith('abstained')
    n_abstained = int(abstention_mask.sum())
    rate = n_abstained / n_total
    return {
        'abstention_rate': round(rate, 4),
        'is_suspect': rate >= suspect_threshold,
        'n_abstained': n_abstained,
        'n_total': n_total,
    }


def build_discovery_report(
    df: pd.DataFrame,
    var_name: str,
    expected_sections: list[str] | None = None,
) -> dict:
    """Build a complete pattern discovery report for one variable."""
    misses = find_l1_misses_ia_hits(df, var_name)
    clusters = cluster_by_ia_value(misses, var_name) if len(misses) > 0 else {}
    scope_leaks_df = (
        detect_scope_leaks(df, var_name, expected_sections)
        if expected_sections else pd.DataFrame()
    )
    silence = detect_silence_suspects(df, var_name)
    return {
        'variable': var_name,
        'total_records': len(df),
        'l1_miss_count': len(misses),
        'l1_miss_rate': round(len(misses) / len(df), 4) if len(df) else 0.0,
        'l1_miss_clusters': clusters,
        'scope_leaks': {
            'count': len(scope_leaks_df),
            'sections_found': (
                scope_leaks_df[f'{var_name}__section'].value_counts().to_dict()
                if len(scope_leaks_df) > 0 else {}
            ),
        },
        'silence_suspects': silence,
    }


def render_discovery_markdown(report: dict) -> str:
    """Render a pattern discovery report as Markdown."""
    var = report['variable']
    lines = [
        f'# Pattern Discovery Report — `{var}`',
        '',
        f'**Total records:** {report["total_records"]}',
        f'**L1 misses (IA hit):** {report["l1_miss_count"]} ({report["l1_miss_rate"]:.1%})',
        '',
    ]
    clusters = report['l1_miss_clusters']
    if clusters:
        lines.append('## L1 Miss Clusters (IA found, L1 did not)')
        lines.append('')
        lines.append('| IA Value | Count | Evidence Windows |')
        lines.append('|---|---|---|')
        for ia_val, info in sorted(clusters.items(), key=lambda x: -x[1]['count']):
            windows = '; '.join(f'`{w[:60]}`' for w in info['evidence_windows'][:3]) or '—'
            lines.append(f'| {ia_val} | {info["count"]} | {windows} |')
        lines.append('')
    else:
        lines.append('*No L1 misses with IA hits found.*')
        lines.append('')
    leaks = report['scope_leaks']
    if leaks['count'] > 0:
        lines.append('## Scope Leaks')
        lines.append('')
        lines.append(f'**{leaks["count"]} extractions from unexpected sections:**')
        for section, count in leaks['sections_found'].items():
            lines.append(f'- `{section}`: {count} cases')
        lines.append('')
    silence = report['silence_suspects']
    if silence['is_suspect']:
        lines.append('## Silence / Abstention Concern')
        lines.append('')
        lines.append(
            f'**Abstention rate: {silence["abstention_rate"]:.1%}** '
            f'({silence["n_abstained"]}/{silence["n_total"]})'
        )
        lines.append('This may indicate the section scope is too narrow or the silence policy is incorrect.')
        lines.append('')
    return '\n'.join(lines)
