"""Tests for dashboard — fill rate trends and gate compression visualization."""
from __future__ import annotations
import json
import pytest
from pathlib import Path
from observability.dashboard import (
    extract_timeline_data,
    build_dashboard_data,
    render_dashboard_html,
)


def _make_var(name: str, fill_rate: float, agreement_surface: float = 0.95) -> dict:
    return {
        'name': name,
        'n_total': 1000,
        'n_filled': int(1000 * fill_rate),
        'fill_rate': fill_rate,
        'agreement_surface': agreement_surface,
    }


def _make_manifest(contract_id: str, started_at: str,
                   variables: list[dict], gate2_count: int = 0) -> dict:
    return {
        'contract_id': contract_id,
        'started_at': started_at,
        'duration_s': 10.0,
        'variables': variables,
        'gates': {'gate2_count': gate2_count},
    }


class TestExtractTimelineData:
    def test_extracts_fill_rates_over_time(self):
        manifests = [
            _make_manifest('e1', '2026-04-13T00:00:00+00:00',
                           [_make_var('bbps', 0.70)], gate2_count=10),
            _make_manifest('e1', '2026-04-14T00:00:00+00:00',
                           [_make_var('bbps', 0.75)], gate2_count=7),
            _make_manifest('e1', '2026-04-15T00:00:00+00:00',
                           [_make_var('bbps', 0.80)], gate2_count=3),
        ]
        timeline = extract_timeline_data(manifests)
        assert len(timeline['runs']) == 3
        assert timeline['variables']['bbps']['fill_rates'] == [0.70, 0.75, 0.80]
        assert timeline['gate2_counts'] == [10, 7, 3]

    def test_handles_variable_appearing_midway(self):
        manifests = [
            _make_manifest('e1', '2026-04-13T00:00:00+00:00',
                           [_make_var('bbps', 0.70)]),
            _make_manifest('e1', '2026-04-14T00:00:00+00:00',
                           [_make_var('bbps', 0.75), _make_var('polipo', 0.90)]),
        ]
        timeline = extract_timeline_data(manifests)
        assert timeline['variables']['bbps']['fill_rates'] == [0.70, 0.75]
        assert timeline['variables']['polipo']['fill_rates'] == [None, 0.90]


class TestBuildDashboardData:
    def test_builds_data_for_contract(self):
        manifests = [
            _make_manifest('e1', f'2026-04-{13+i}T00:00:00+00:00',
                           [_make_var('bbps', 0.70 + i * 0.05)], gate2_count=10 - i)
            for i in range(3)
        ]
        data = build_dashboard_data(manifests, contract_id='e1')
        assert data['contract_id'] == 'e1'
        assert len(data['timeline']['runs']) == 3


class TestRenderDashboardHtml:
    def test_produces_valid_html(self):
        manifests = [
            _make_manifest('e1', f'2026-04-{13+i}T00:00:00+00:00',
                           [_make_var('bbps', 0.70 + i * 0.05)], gate2_count=10 - i)
            for i in range(3)
        ]
        data = build_dashboard_data(manifests, contract_id='e1')
        html = render_dashboard_html(data)
        assert '<html' in html
        assert 'bbps' in html
        assert 'Fill Rate' in html

    def test_writes_to_file(self, tmp_path):
        manifests = [
            _make_manifest('e1', '2026-04-13T00:00:00+00:00',
                           [_make_var('bbps', 0.70)], gate2_count=5),
        ]
        data = build_dashboard_data(manifests, contract_id='e1')
        out_path = tmp_path / 'dashboard.html'
        html = render_dashboard_html(data)
        out_path.write_text(html, encoding='utf-8')
        assert out_path.exists()
        assert out_path.stat().st_size > 100
