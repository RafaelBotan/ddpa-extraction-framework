"""Tests for pattern_discovery — residual pattern analysis."""
from __future__ import annotations
import pandas as pd
import pytest
from observability.pattern_discovery import (
    find_l1_misses_ia_hits,
    extract_evidence_windows,
    cluster_by_ia_value,
    detect_scope_leaks,
    detect_silence_suspects,
    build_discovery_report,
)


def _make_df(rows: list[dict], var_name: str = 'her2_score') -> pd.DataFrame:
    """Build a DataFrame mimicking a DDPA variable output CSV."""
    records = []
    for r in rows:
        rec = {
            'id_registro': r.get('id', 1),
            'texto_laudo': r.get('texto', 'laudo vazio'),
            f'{var_name}__value': r.get('l1_value'),
            f'{var_name}__status': r.get('l1_status', 'no_match'),
            f'{var_name}__section': r.get('section'),
            f'{var_name}__evidence': r.get('evidence', ''),
            f'{var_name}__ia': r.get('ia_value'),
            f'{var_name}__resolution': r.get('resolution', 'abstained_source_incomplete'),
        }
        records.append(rec)
    return pd.DataFrame(records)


class TestFindL1MissesIaHits:
    def test_finds_rows_where_l1_null_ia_filled(self):
        df = _make_df([
            {'id': 1, 'l1_value': None, 'ia_value': '3+', 'texto': 'her2 score 3+'},
            {'id': 2, 'l1_value': '2+', 'ia_value': '2+', 'texto': 'her2 score 2+'},
            {'id': 3, 'l1_value': None, 'ia_value': '3+', 'texto': 'her2 escore 3+'},
            {'id': 4, 'l1_value': None, 'ia_value': None, 'texto': 'sem informacao'},
        ])
        misses = find_l1_misses_ia_hits(df, 'her2_score')
        assert len(misses) == 2
        assert set(misses['id_registro']) == {1, 3}

    def test_returns_empty_when_no_misses(self):
        df = _make_df([
            {'id': 1, 'l1_value': '2+', 'ia_value': '2+'},
        ])
        misses = find_l1_misses_ia_hits(df, 'her2_score')
        assert len(misses) == 0


class TestClusterByIaValue:
    def test_groups_by_ia_value(self):
        df = _make_df([
            {'id': 1, 'l1_value': None, 'ia_value': '3+', 'texto': 'her2 3+'},
            {'id': 2, 'l1_value': None, 'ia_value': '3+', 'texto': 'her2 score 3+'},
            {'id': 3, 'l1_value': None, 'ia_value': '1+', 'texto': 'her2 1+'},
        ])
        misses = find_l1_misses_ia_hits(df, 'her2_score')
        clusters = cluster_by_ia_value(misses, 'her2_score')
        assert '3+' in clusters
        assert clusters['3+']['count'] == 2
        assert '1+' in clusters
        assert clusters['1+']['count'] == 1

    def test_includes_evidence_samples(self):
        df = _make_df([
            {'id': i, 'l1_value': None, 'ia_value': '3+',
             'texto': f'laudo {i} her2 score 3+ positivo'}
            for i in range(15)
        ])
        misses = find_l1_misses_ia_hits(df, 'her2_score')
        clusters = cluster_by_ia_value(misses, 'her2_score')
        assert len(clusters['3+']['evidence_samples']) <= 5


class TestExtractEvidenceWindows:
    def test_extracts_window_around_ia_value(self):
        texto = 'o resultado de imunohistoquimica mostra her2 score 3+ em membrana completa'
        windows = extract_evidence_windows(texto, '3+', window_chars=30)
        assert len(windows) >= 1
        assert '3+' in windows[0]

    def test_returns_empty_when_no_match(self):
        texto = 'laudo sem informacao relevante'
        windows = extract_evidence_windows(texto, '3+', window_chars=30)
        assert windows == []


class TestDetectScopeLeaks:
    def test_detects_extraction_from_wrong_section(self):
        df = _make_df([
            {'id': 1, 'l1_value': '3+', 'section': 'conclusao', 'resolution': 'accepted_exact'},
            {'id': 2, 'l1_value': '2+', 'section': 'historico_clinico', 'resolution': 'accepted_exact'},
            {'id': 3, 'l1_value': '1+', 'section': 'conclusao', 'resolution': 'accepted_exact'},
        ])
        expected_sections = ['conclusao', 'resultado']
        leaks = detect_scope_leaks(df, 'her2_score', expected_sections)
        assert len(leaks) == 1
        assert leaks.iloc[0]['her2_score__section'] == 'historico_clinico'

    def test_no_leaks_when_all_in_scope(self):
        df = _make_df([
            {'id': 1, 'l1_value': '3+', 'section': 'conclusao'},
        ])
        leaks = detect_scope_leaks(df, 'her2_score', ['conclusao'])
        assert len(leaks) == 0


class TestDetectSilenceSuspects:
    def test_detects_high_abstention_rate(self):
        df = _make_df([
            {'id': i, 'l1_value': None, 'ia_value': None,
             'resolution': 'abstained_source_incomplete',
             'texto': f'laudo {i} com dados de receptor'}
            for i in range(80)
        ] + [
            {'id': i + 80, 'l1_value': '3+', 'ia_value': '3+',
             'resolution': 'accepted_exact', 'texto': f'laudo {i}'}
            for i in range(20)
        ])
        suspects = detect_silence_suspects(df, 'her2_score')
        assert suspects['abstention_rate'] == pytest.approx(0.80, abs=0.01)
        assert suspects['is_suspect']

    def test_low_abstention_not_suspect(self):
        df = _make_df([
            {'id': i, 'l1_value': None, 'resolution': 'abstained_source_incomplete',
             'texto': f'laudo {i}'}
            for i in range(10)
        ] + [
            {'id': i + 10, 'l1_value': '3+', 'resolution': 'accepted_exact',
             'texto': f'laudo {i}'}
            for i in range(90)
        ])
        suspects = detect_silence_suspects(df, 'her2_score')
        assert not suspects['is_suspect']


class TestBuildDiscoveryReport:
    def test_produces_complete_report(self):
        df = _make_df([
            {'id': 1, 'l1_value': None, 'ia_value': '3+',
             'texto': 'her2 score 3+ positivo', 'section': 'conclusao'},
            {'id': 2, 'l1_value': None, 'ia_value': '3+',
             'texto': 'her2 escore 3+ membrana', 'section': 'conclusao'},
            {'id': 3, 'l1_value': '2+', 'ia_value': '2+',
             'texto': 'her2 score 2+', 'section': 'conclusao',
             'resolution': 'accepted_exact'},
        ])
        report = build_discovery_report(df, 'her2_score', expected_sections=['conclusao'])
        assert 'l1_miss_clusters' in report
        assert 'scope_leaks' in report
        assert 'silence_suspects' in report
        assert report['total_records'] == 3
        assert report['l1_miss_count'] == 2
