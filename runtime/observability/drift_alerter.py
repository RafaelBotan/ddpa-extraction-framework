"""Fill rate and agreement drift detection between consecutive DDPA runs."""
from __future__ import annotations
import enum


class AlertLevel(enum.Enum):
    GREEN = 'green'
    YELLOW = 'yellow'
    RED = 'red'


def classify_alert(
    delta: float,
    yellow_threshold: float = 0.03,
    red_threshold: float = 0.15,
) -> AlertLevel:
    """Classify a drift delta into an alert level.
    Only negative deltas (degradation) trigger alerts.
    Thresholds are absolute values in percentage points (0.03 = 3pp).
    """
    if delta is None:
        return AlertLevel.GREEN
    magnitude = abs(min(delta, 0.0))
    if magnitude >= red_threshold:
        return AlertLevel.RED
    if magnitude >= yellow_threshold:
        return AlertLevel.YELLOW
    return AlertLevel.GREEN


def compute_drift(old_var: dict, new_var: dict) -> dict:
    """Compute drift metrics between two variable summaries from consecutive runs."""
    fill_delta = new_var['fill_rate'] - old_var['fill_rate']
    old_agreement = old_var.get('agreement_surface')
    new_agreement = new_var.get('agreement_surface')
    if old_agreement is not None and new_agreement is not None:
        agreement_delta = new_agreement - old_agreement
    else:
        agreement_delta = None
    return {
        'variable': new_var['name'],
        'old_fill_rate': old_var['fill_rate'],
        'new_fill_rate': new_var['fill_rate'],
        'fill_rate_delta': round(fill_delta, 4),
        'old_agreement': old_agreement,
        'new_agreement': new_agreement,
        'agreement_surface_delta': round(agreement_delta, 4) if agreement_delta is not None else None,
    }


def detect_new_statuses(old_dist: dict, new_dist: dict) -> dict:
    """Find status keys in new_dist that don't exist in old_dist."""
    old_keys = set(old_dist.keys())
    return {k: v for k, v in new_dist.items() if k not in old_keys}


def build_drift_report(manifests: list[dict]) -> dict:
    """Build a drift report comparing the last two runs in the list.
    Args:
        manifests: list of manifest dicts, sorted by started_at ascending.
                   Should be pre-filtered to the same contract.
    Returns:
        Dict with 'old_run', 'new_run', 'comparisons' (list of per-variable drifts).
    """
    if len(manifests) < 2:
        return {
            'old_run': manifests[0]['started_at'] if manifests else None,
            'new_run': None,
            'comparisons': [],
        }
    old_m = manifests[-2]
    new_m = manifests[-1]
    old_vars = {v['name']: v for v in old_m.get('variables', [])}
    new_vars = {v['name']: v for v in new_m.get('variables', [])}
    comparisons = []
    for var_name, new_var in new_vars.items():
        if var_name in old_vars:
            old_var = old_vars[var_name]
            drift = compute_drift(old_var, new_var)
            drift['new_statuses'] = detect_new_statuses(
                old_var.get('status_distribution', {}),
                new_var.get('status_distribution', {}),
            )
            drift['new_resolutions'] = detect_new_statuses(
                old_var.get('resolution_distribution', {}),
                new_var.get('resolution_distribution', {}),
            )
            fill_alert = classify_alert(drift['fill_rate_delta'])
            agreement_alert = classify_alert(drift['agreement_surface_delta'])
            has_new_status = len(drift['new_statuses']) > 0
            status_alert = AlertLevel.YELLOW if has_new_status else AlertLevel.GREEN
            levels = [fill_alert, agreement_alert, status_alert]
            priority = {AlertLevel.RED: 2, AlertLevel.YELLOW: 1, AlertLevel.GREEN: 0}
            drift['alert_level'] = max(levels, key=lambda x: priority[x])
            drift['is_new_variable'] = False
            comparisons.append(drift)
        else:
            comparisons.append({
                'variable': var_name,
                'is_new_variable': True,
                'new_fill_rate': new_var['fill_rate'],
                'alert_level': AlertLevel.GREEN,
                'fill_rate_delta': None,
                'agreement_surface_delta': None,
                'new_statuses': {},
                'new_resolutions': {},
            })
    return {
        'old_run': old_m['started_at'],
        'new_run': new_m['started_at'],
        'old_gate2_count': old_m.get('gates', {}).get('gate2_count', 0),
        'new_gate2_count': new_m.get('gates', {}).get('gate2_count', 0),
        'comparisons': comparisons,
    }


def render_drift_markdown(report: dict) -> str:
    """Render a drift report as Markdown with alert indicators."""
    lines = [
        '# Drift Report',
        '',
        f'**Comparing:** `{report["old_run"]}` -> `{report["new_run"]}`',
        '',
    ]
    if not report['comparisons']:
        lines.append('*No comparisons available (need at least 2 runs).*')
        return '\n'.join(lines)
    gate2_old = report.get('old_gate2_count', '?')
    gate2_new = report.get('new_gate2_count', '?')
    lines.append(f'**Gate #2 items:** {gate2_old} -> {gate2_new}')
    lines.append('')
    emoji = {AlertLevel.GREEN: '[OK]', AlertLevel.YELLOW: '[WARN]', AlertLevel.RED: '[ALERT]'}
    lines.append('| Variable | Fill Rate | Delta | Agreement | Delta | New Statuses | Alert |')
    lines.append('|---|---|---|---|---|---|---|')
    for comp in report['comparisons']:
        if comp.get('is_new_variable'):
            lines.append(
                f'| {comp["variable"]} | {comp["new_fill_rate"]:.1%} | *new* | — | — | — | NEW |'
            )
            continue
        fr_old = comp.get('old_fill_rate', 0)
        fr_new = comp.get('new_fill_rate', 0)
        fr_delta = comp.get('fill_rate_delta', 0)
        ag_old = comp.get('old_agreement')
        ag_new = comp.get('new_agreement')
        ag_delta = comp.get('agreement_surface_delta')
        new_st = ', '.join(comp.get('new_statuses', {}).keys()) or '—'
        alert = emoji.get(comp['alert_level'], '?')
        ag_old_str = f'{ag_old:.1%}' if ag_old is not None else '—'
        ag_new_str = f'{ag_new:.1%}' if ag_new is not None else '—'
        ag_delta_str = f'{ag_delta:+.1%}' if ag_delta is not None else '—'
        lines.append(
            f'| {comp["variable"]} | {fr_old:.1%}->{fr_new:.1%} | {fr_delta:+.1%} '
            f'| {ag_old_str}->{ag_new_str} | {ag_delta_str} | {new_st} | {alert} |'
        )
    return '\n'.join(lines)
