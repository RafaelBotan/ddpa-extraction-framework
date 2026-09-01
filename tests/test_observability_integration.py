"""Integration test — observability modules on real run manifests."""
from __future__ import annotations
import pytest
from pathlib import Path
from observability.manifest_loader import load_manifests, filter_by_contract
from observability.drift_alerter import build_drift_report, render_drift_markdown
from observability.dashboard import build_dashboard_data, render_dashboard_html

RUNS_DIR = Path(__file__).resolve().parent.parent / 'runs'


@pytest.mark.skipif(not RUNS_DIR.exists(), reason='No runs directory found')
class TestObservabilityIntegration:
    def test_loads_real_manifests(self):
        manifests = load_manifests(RUNS_DIR)
        assert len(manifests) > 0, 'Expected at least one run manifest'
        m = manifests[0]
        assert 'contract_id' in m
        assert 'variables' in m
        assert '_run_dir' in m

    def test_drift_report_on_e1(self):
        manifests = load_manifests(RUNS_DIR)
        e1 = filter_by_contract(manifests, 'e1_endoscopia', prefix=True)
        if len(e1) < 2:
            pytest.skip('Need at least 2 e1_endoscopia runs for drift')
        report = build_drift_report(e1)
        assert report['comparisons']
        md = render_drift_markdown(report)
        assert 'Drift Report' in md

    def test_dashboard_on_e1(self):
        manifests = load_manifests(RUNS_DIR)
        e1 = filter_by_contract(manifests, 'e1_endoscopia', prefix=True)
        if not e1:
            pytest.skip('No e1_endoscopia runs found')
        data = build_dashboard_data(e1, 'e1_endoscopia')
        html = render_dashboard_html(data)
        assert '<html' in html
        assert data['n_runs'] == len(e1)
