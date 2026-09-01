"""DDPA Observability — drift alerting, pattern discovery, dashboard.

Usage:
    python scripts/run_observability.py --runs-dir runs/ --contract e1_endoscopia
"""
from observability.manifest_loader import load_manifests, filter_by_contract
from observability.drift_alerter import build_drift_report, render_drift_markdown
from observability.pattern_discovery import build_discovery_report, render_discovery_markdown
from observability.dashboard import build_dashboard_data, render_dashboard_html

__all__ = [
    'load_manifests', 'filter_by_contract',
    'build_drift_report', 'render_drift_markdown',
    'build_discovery_report', 'render_discovery_markdown',
    'build_dashboard_data', 'render_dashboard_html',
]
