"""Tests for manifest_loader — loading and validating run manifests."""
from __future__ import annotations
import json
import pytest
from pathlib import Path
from observability.manifest_loader import load_manifests, filter_by_contract


def _write_manifest(tmp_path: Path, run_name: str, manifest: dict) -> Path:
    run_dir = tmp_path / run_name
    run_dir.mkdir(parents=True)
    path = run_dir / 'run_manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    return path


def _make_manifest(contract_id: str, started_at: str, variables: list[dict]) -> dict:
    return {
        'manifest_version': '1.0.0',
        'contract_id': contract_id,
        'contract_version': '1.0.0',
        'started_at': started_at,
        'completed_at': started_at,
        'duration_s': 10.0,
        'hashes': {},
        'variables': variables,
        'gates': {'gate2_count': 0},
    }


def _make_var(name: str, n_total: int = 100, fill_rate: float = 0.8,
              agreement_surface: float = 0.95,
              status_distribution: dict | None = None,
              resolution_distribution: dict | None = None) -> dict:
    n_filled = int(n_total * fill_rate)
    return {
        'name': name,
        'n_total': n_total,
        'n_filled': n_filled,
        'fill_rate': fill_rate,
        'status_distribution': status_distribution or {'pattern_1': n_filled, 'no_match': n_total - n_filled},
        'ia_fill': n_filled + 5,
        'n_both_filled': n_filled,
        'n_agree_both': int(n_filled * agreement_surface),
        'agreement_surface': agreement_surface,
        'resolution_distribution': resolution_distribution or {'accepted_exact': n_filled},
    }


class TestLoadManifests:
    def test_loads_all_manifests_sorted_by_date(self, tmp_path):
        m1 = _make_manifest('e1', '2026-04-14T00:00:00+00:00', [_make_var('bbps')])
        m2 = _make_manifest('e1', '2026-04-15T00:00:00+00:00', [_make_var('bbps')])
        _write_manifest(tmp_path, 'run_old', m1)
        _write_manifest(tmp_path, 'run_new', m2)
        result = load_manifests(tmp_path)
        assert len(result) == 2
        assert result[0]['started_at'] <= result[1]['started_at']

    def test_skips_invalid_json(self, tmp_path):
        run_dir = tmp_path / 'bad_run'
        run_dir.mkdir()
        (run_dir / 'run_manifest.json').write_text('NOT JSON', encoding='utf-8')
        _write_manifest(tmp_path, 'good_run', _make_manifest('e1', '2026-04-14T00:00:00+00:00', []))
        result = load_manifests(tmp_path)
        assert len(result) == 1

    def test_empty_directory(self, tmp_path):
        result = load_manifests(tmp_path)
        assert result == []

    def test_attaches_run_dir_to_manifest(self, tmp_path):
        _write_manifest(tmp_path, 'run_a', _make_manifest('e1', '2026-04-14T00:00:00+00:00', []))
        result = load_manifests(tmp_path)
        assert '_run_dir' in result[0]
        assert Path(result[0]['_run_dir']).name == 'run_a'


class TestFilterByContract:
    def test_filters_by_contract_id(self, tmp_path):
        m1 = _make_manifest('e1', '2026-04-14T00:00:00+00:00', [_make_var('bbps')])
        m2 = _make_manifest('mama_ihq', '2026-04-15T00:00:00+00:00', [_make_var('re_status')])
        _write_manifest(tmp_path, 'run_e1', m1)
        _write_manifest(tmp_path, 'run_mama', m2)
        all_manifests = load_manifests(tmp_path)
        filtered = filter_by_contract(all_manifests, 'e1')
        assert len(filtered) == 1
        assert filtered[0]['contract_id'] == 'e1'

    def test_filters_by_prefix(self, tmp_path):
        m1 = _make_manifest('e1_endoscopia_full', '2026-04-14T00:00:00+00:00', [_make_var('bbps')])
        m2 = _make_manifest('e1_endoscopia_scale3k', '2026-04-15T00:00:00+00:00', [_make_var('bbps')])
        m3 = _make_manifest('mama_ihq', '2026-04-16T00:00:00+00:00', [_make_var('re_status')])
        _write_manifest(tmp_path, 'run_1', m1)
        _write_manifest(tmp_path, 'run_2', m2)
        _write_manifest(tmp_path, 'run_3', m3)
        all_manifests = load_manifests(tmp_path)
        filtered = filter_by_contract(all_manifests, 'e1_endoscopia', prefix=True)
        assert len(filtered) == 2
