"""Tests for drift_alerter — fill rate and agreement drift detection."""
from __future__ import annotations
import pytest
from observability.drift_alerter import (
    compute_drift,
    classify_alert,
    detect_new_statuses,
    build_drift_report,
    AlertLevel,
)


def _make_var(name: str, fill_rate: float = 0.8,
              agreement_surface: float = 0.95,
              n_total: int = 1000,
              status_distribution: dict | None = None,
              resolution_distribution: dict | None = None) -> dict:
    n_filled = int(n_total * fill_rate)
    return {
        'name': name,
        'n_total': n_total,
        'n_filled': n_filled,
        'fill_rate': fill_rate,
        'agreement_surface': agreement_surface,
        'status_distribution': status_distribution or {'pattern_1': n_filled, 'no_match': n_total - n_filled},
        'resolution_distribution': resolution_distribution or {'accepted_exact': n_filled},
    }


def _make_manifest(contract_id: str, started_at: str, variables: list[dict]) -> dict:
    return {
        'contract_id': contract_id,
        'started_at': started_at,
        'variables': variables,
        'gates': {'gate2_count': 0},
    }


class TestComputeDrift:
    def test_fill_rate_delta(self):
        old_var = _make_var('bbps', fill_rate=0.80)
        new_var = _make_var('bbps', fill_rate=0.72)
        drift = compute_drift(old_var, new_var)
        assert drift['fill_rate_delta'] == pytest.approx(-0.08, abs=0.001)

    def test_agreement_delta(self):
        old_var = _make_var('bbps', agreement_surface=0.95)
        new_var = _make_var('bbps', agreement_surface=0.90)
        drift = compute_drift(old_var, new_var)
        assert drift['agreement_surface_delta'] == pytest.approx(-0.05, abs=0.001)

    def test_no_agreement_in_old(self):
        old_var = _make_var('bbps')
        del old_var['agreement_surface']
        new_var = _make_var('bbps')
        drift = compute_drift(old_var, new_var)
        assert drift['agreement_surface_delta'] is None

    def test_gate2_count_delta(self):
        m_old = _make_manifest('e1', '2026-04-14T00:00:00+00:00', [_make_var('bbps')])
        m_new = _make_manifest('e1', '2026-04-15T00:00:00+00:00', [_make_var('bbps')])
        m_old['gates']['gate2_count'] = 5
        m_new['gates']['gate2_count'] = 12
        assert m_new['gates']['gate2_count'] - m_old['gates']['gate2_count'] == 7


class TestClassifyAlert:
    def test_green_no_drift(self):
        assert classify_alert(0.01) == AlertLevel.GREEN

    def test_yellow_moderate_drift(self):
        assert classify_alert(-0.04) == AlertLevel.YELLOW

    def test_red_severe_drift(self):
        assert classify_alert(-0.16) == AlertLevel.RED

    def test_positive_drift_is_green(self):
        assert classify_alert(0.10) == AlertLevel.GREEN

    def test_custom_thresholds(self):
        assert classify_alert(-0.02, yellow_threshold=0.01, red_threshold=0.05) == AlertLevel.YELLOW


class TestDetectNewStatuses:
    def test_detects_new_status(self):
        old_dist = {'pattern_1': 80, 'no_match': 20}
        new_dist = {'pattern_1': 75, 'no_match': 20, 'new_pattern': 5}
        new_statuses = detect_new_statuses(old_dist, new_dist)
        assert new_statuses == {'new_pattern': 5}

    def test_no_new_status(self):
        old_dist = {'pattern_1': 80, 'no_match': 20}
        new_dist = {'pattern_1': 85, 'no_match': 15}
        assert detect_new_statuses(old_dist, new_dist) == {}


class TestBuildDriftReport:
    def test_produces_report_for_two_runs(self):
        m_old = _make_manifest('e1', '2026-04-14T00:00:00+00:00', [_make_var('bbps', fill_rate=0.80)])
        m_new = _make_manifest('e1', '2026-04-15T00:00:00+00:00', [_make_var('bbps', fill_rate=0.72)])
        report = build_drift_report([m_old, m_new])
        assert len(report['comparisons']) == 1
        comp = report['comparisons'][0]
        assert comp['variable'] == 'bbps'
        assert comp['alert_level'] == AlertLevel.YELLOW

    def test_no_report_for_single_run(self):
        m = _make_manifest('e1', '2026-04-14T00:00:00+00:00', [_make_var('bbps')])
        report = build_drift_report([m])
        assert report['comparisons'] == []

    def test_handles_variable_added_in_new_run(self):
        m_old = _make_manifest('e1', '2026-04-14T00:00:00+00:00', [_make_var('bbps')])
        m_new = _make_manifest('e1', '2026-04-15T00:00:00+00:00', [
            _make_var('bbps'), _make_var('polipo_presente'),
        ])
        report = build_drift_report([m_old, m_new])
        var_names = [c['variable'] for c in report['comparisons']]
        assert 'bbps' in var_names
        assert any(c.get('is_new_variable') for c in report['comparisons'])

    def test_detects_new_status_in_report(self):
        old_var = _make_var('bbps', status_distribution={'pat_1': 80, 'no_match': 20})
        new_var = _make_var('bbps', status_distribution={'pat_1': 70, 'no_match': 20, 'pat_new': 10})
        m_old = _make_manifest('e1', '2026-04-14T00:00:00+00:00', [old_var])
        m_new = _make_manifest('e1', '2026-04-15T00:00:00+00:00', [new_var])
        report = build_drift_report([m_old, m_new])
        comp = report['comparisons'][0]
        assert 'pat_new' in comp['new_statuses']
