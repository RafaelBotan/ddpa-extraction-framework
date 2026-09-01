"""Minimal DDPA dashboard — fill rate trends and gate compression as static HTML."""
from __future__ import annotations
import html as html_lib


def extract_timeline_data(manifests: list[dict]) -> dict:
    """Extract time-series data from a list of manifests (same contract, sorted by date).
    Returns:
        {
            'runs': [{'started_at': str, 'duration_s': float}, ...],
            'variables': {var_name: {'fill_rates': [float|None], 'agreement_surfaces': [float|None]}},
            'gate2_counts': [int, ...],
        }
    """
    all_var_names: list[str] = []
    seen = set()
    for m in manifests:
        for v in m.get('variables', []):
            if v['name'] not in seen:
                all_var_names.append(v['name'])
                seen.add(v['name'])
    runs = []
    gate2_counts = []
    variables: dict[str, dict] = {
        name: {'fill_rates': [], 'agreement_surfaces': []}
        for name in all_var_names
    }
    for m in manifests:
        runs.append({
            'started_at': m.get('started_at', ''),
            'duration_s': m.get('duration_s', 0),
        })
        gate2_counts.append(m.get('gates', {}).get('gate2_count', 0))
        var_map = {v['name']: v for v in m.get('variables', [])}
        for var_name in all_var_names:
            v = var_map.get(var_name)
            if v:
                variables[var_name]['fill_rates'].append(v.get('fill_rate'))
                variables[var_name]['agreement_surfaces'].append(v.get('agreement_surface'))
            else:
                variables[var_name]['fill_rates'].append(None)
                variables[var_name]['agreement_surfaces'].append(None)
    return {'runs': runs, 'variables': variables, 'gate2_counts': gate2_counts}


def build_dashboard_data(manifests: list[dict], contract_id: str) -> dict:
    """Build dashboard data for a specific contract."""
    filtered = [m for m in manifests if m.get('contract_id', '').startswith(contract_id)]
    filtered.sort(key=lambda m: m.get('started_at', ''))
    return {
        'contract_id': contract_id,
        'n_runs': len(filtered),
        'timeline': extract_timeline_data(filtered),
    }


def render_dashboard_html(data: dict) -> str:
    """Render dashboard data as a self-contained static HTML file with inline SVG charts."""
    contract = html_lib.escape(data['contract_id'])
    timeline = data['timeline']
    runs = timeline['runs']
    n_runs = len(runs)

    if n_runs == 0:
        return f'<html><body><h1>DDPA Dashboard — {contract}</h1><p>No runs found.</p></body></html>'

    run_labels = [r['started_at'][:10] for r in runs]
    sections = []

    # Fill Rate chart
    sections.append('<h2>Fill Rate Trends</h2>')
    sections.append('<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">')
    sections.append('<tr><th>Variable</th><th>Current</th><th>Trend</th></tr>')
    for var_name, var_data in timeline['variables'].items():
        rates = var_data['fill_rates']
        current = next((r for r in reversed(rates) if r is not None), None)
        current_str = f'{current:.1%}' if current is not None else '—'
        sparkline = _svg_sparkline(rates, width=200, height=30)
        sections.append(
            f'<tr><td><code>{html_lib.escape(var_name)}</code></td>'
            f'<td>{current_str}</td><td>{sparkline}</td></tr>'
        )
    sections.append('</table>')

    # Gate #2 bar chart
    sections.append('<h2>Gate #2 Items Over Time</h2>')
    gate_counts = timeline['gate2_counts']
    sections.append(_svg_bar_chart(run_labels, gate_counts, width=max(400, n_runs * 50), height=120))

    # Agreement surface table
    sections.append('<h2>Agreement Surface</h2>')
    sections.append('<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">')
    sections.append('<tr><th>Variable</th><th>Current</th><th>Trend</th></tr>')
    for var_name, var_data in timeline['variables'].items():
        agreements = var_data['agreement_surfaces']
        current = next((a for a in reversed(agreements) if a is not None), None)
        current_str = f'{current:.1%}' if current is not None else '—'
        sparkline = _svg_sparkline(agreements, width=200, height=30)
        sections.append(
            f'<tr><td><code>{html_lib.escape(var_name)}</code></td>'
            f'<td>{current_str}</td><td>{sparkline}</td></tr>'
        )
    sections.append('</table>')

    # Run details table
    sections.append('<h2>Runs</h2>')
    sections.append('<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">')
    sections.append('<tr><th>#</th><th>Date</th><th>Duration</th><th>Gate #2</th></tr>')
    for i, run in enumerate(runs):
        dur = f'{run["duration_s"]:.1f}s'
        g2 = gate_counts[i] if i < len(gate_counts) else '—'
        sections.append(f'<tr><td>{i+1}</td><td>{run_labels[i]}</td><td>{dur}</td><td>{g2}</td></tr>')
    sections.append('</table>')

    body = '\n'.join(sections)
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>DDPA Dashboard — {contract}</title>
<style>
  body {{ font-family: monospace; margin: 2em; background: #fafafa; }}
  h1 {{ color: #333; }}
  h2 {{ color: #555; margin-top: 2em; }}
  table {{ margin: 1em 0; }}
  th {{ background: #eee; text-align: left; }}
  code {{ background: #f0f0f0; padding: 2px 4px; }}
</style>
</head>
<body>
<h1>DDPA Dashboard — <code>{contract}</code></h1>
<p>{n_runs} runs</p>
{body}
<footer><p style="color:#999">Generated by DDPA Observability</p></footer>
</body>
</html>"""


def _svg_sparkline(values: list[float | None], width: int = 200, height: int = 30) -> str:
    """Render a simple SVG sparkline for a list of values."""
    valid = [(i, v) for i, v in enumerate(values) if v is not None]
    if not valid:
        return '<span style="color:#999">—</span>'
    n = len(values)
    min_v = min(v for _, v in valid)
    max_v = max(v for _, v in valid)
    v_range = max_v - min_v if max_v != min_v else 1.0
    points = []
    for i, v in valid:
        x = (i / max(n - 1, 1)) * (width - 4) + 2
        y = height - 2 - ((v - min_v) / v_range) * (height - 4)
        points.append(f'{x:.1f},{y:.1f}')
    polyline = ' '.join(points)
    last_x, last_y = points[-1].split(',')
    return (
        f'<svg width="{width}" height="{height}" style="vertical-align:middle">'
        f'<polyline points="{polyline}" fill="none" stroke="#4a90d9" stroke-width="1.5"/>'
        f'<circle cx="{last_x}" cy="{last_y}" r="2.5" fill="#4a90d9"/>'
        f'</svg>'
    )


def _svg_bar_chart(labels: list[str], values: list[int], width: int = 400, height: int = 120) -> str:
    """Render a simple SVG bar chart."""
    n = len(values)
    if n == 0:
        return '<span>No data</span>'
    max_v = max(values) if values else 1
    if max_v == 0:
        max_v = 1
    bar_w = max(10, (width - 40) // n - 4)
    padding = 20
    bars = []
    for i, v in enumerate(values):
        x = padding + i * (bar_w + 4)
        bar_h = (v / max_v) * (height - 30)
        y = height - 20 - bar_h
        color = '#4a90d9' if i < n - 1 else '#d94a4a'
        bars.append(
            f'<rect x="{x}" y="{y:.0f}" width="{bar_w}" height="{bar_h:.0f}" fill="{color}" />'
            f'<text x="{x + bar_w/2:.0f}" y="{y - 3:.0f}" text-anchor="middle" '
            f'font-size="10" fill="#333">{v}</text>'
        )
    return (
        f'<svg width="{max(width, n * (bar_w + 4) + 40)}" height="{height}">'
        + ''.join(bars)
        + '</svg>'
    )
