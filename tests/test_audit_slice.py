"""Audit slice sampler (task #96, DDPA must-close #3).

Verifica que `build_audit_slice`:
  - só amostra resolutions silenciosos (accepted_*/abstained_by_consensus)
  - estratifica por (resolution × stratum) e respeita N por stratum
  - é determinístico dado o seed
  - escreve manifest com contagens corretas
  - funciona quando uma ou mais resoluções estão ausentes
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

_HERE = Path(__file__).resolve().parent
_PILOTO = _HERE.parent
if str(_PILOTO) not in sys.path:
    sys.path.insert(0, str(_PILOTO))
if str(_PILOTO / 'runtime') not in sys.path:
    sys.path.insert(0, str(_PILOTO / 'runtime'))

from audit_slice import build_audit_slice, SILENT_RESOLUTIONS  # type: ignore


def _make_var_csv(dir_: Path, var: str, rows: list[dict]) -> Path:
    """Cria CSV per-var no formato do runner."""
    df = pd.DataFrame(rows)
    path = dir_ / f'{var}.csv'
    df.to_csv(path, index=False)
    return path


def _mk_row(
    id_registro: str,
    resolution: str,
    value: str = 'sim',
    clinica: str = 'CL_A',
    evidence: str = 'formação polipóide',
    status: str = 'ok',
) -> dict:
    var = 'polipo_presente'
    return {
        'id_registro': id_registro,
        'clinica': clinica,
        f'{var}__value': value,
        f'{var}__status': status,
        f'{var}__section': 'conclusao',
        f'{var}__evidence': evidence,
        f'{var}__certainty': 'high',
        f'{var}__resolution': resolution,
        f'{var}__ia': value,
    }


def test_samples_only_silent_resolutions(tmp_path):
    rows = [
        _mk_row(f'r{i}', 'accepted_exact') for i in range(20)
    ] + [
        _mk_row(f'e{i}', 'escalated_case') for i in range(10)
    ] + [
        _mk_row(f'd{i}', 'normalization_disagreement') for i in range(5)
    ]
    _make_var_csv(tmp_path, 'polipo_presente', rows)

    res = build_audit_slice(tmp_path, per_stratum=5, seed=42)
    with (tmp_path / 'audit' / 'silent_consensus_sample.jsonl').open() as f:
        samples = [json.loads(l) for l in f if l.strip()]

    assert all(s['resolution'] in SILENT_RESOLUTIONS for s in samples)
    assert len({s['id_registro'] for s in samples if s['id_registro'].startswith('r')}) > 0
    assert not any(s['id_registro'].startswith('e') for s in samples)
    assert not any(s['id_registro'].startswith('d') for s in samples)


def test_stratifies_by_resolution_and_clinica(tmp_path):
    rows = []
    # 3 clínicas × 2 resoluções silenciosas × 20 casos cada
    for clinica in ('CL_A', 'CL_B', 'CL_C'):
        for res in ('accepted_exact', 'accepted_semantic'):
            for i in range(20):
                rows.append(_mk_row(f'{clinica}_{res}_{i}', res, clinica=clinica))
    _make_var_csv(tmp_path, 'polipo_presente', rows)

    build_audit_slice(tmp_path, per_stratum=5, seed=42)
    with (tmp_path / 'audit' / 'silent_consensus_sample.jsonl').open() as f:
        samples = [json.loads(l) for l in f if l.strip()]

    # 6 strata (3 clínicas × 2 resoluções) × 5 = 30
    assert len(samples) == 30
    from collections import Counter
    per_stratum = Counter((s['resolution'], s['strata']['clinica']) for s in samples)
    assert all(n == 5 for n in per_stratum.values())
    assert len(per_stratum) == 6


def test_respects_per_stratum_when_pool_smaller(tmp_path):
    """Stratum com menos linhas que per_stratum retorna tudo, sem erro."""
    rows = [
        _mk_row(f'r{i}', 'accepted_exact', clinica='CL_A') for i in range(3)
    ]
    _make_var_csv(tmp_path, 'polipo_presente', rows)

    build_audit_slice(tmp_path, per_stratum=10, seed=42)
    with (tmp_path / 'audit' / 'silent_consensus_sample.jsonl').open() as f:
        samples = [json.loads(l) for l in f if l.strip()]
    assert len(samples) == 3


def test_deterministic_with_seed(tmp_path):
    rows = [_mk_row(f'r{i}', 'accepted_exact') for i in range(30)]
    _make_var_csv(tmp_path, 'polipo_presente', rows)

    build_audit_slice(tmp_path, per_stratum=5, seed=42)
    ids_1 = [
        json.loads(l)['id_registro']
        for l in (tmp_path / 'audit' / 'silent_consensus_sample.jsonl').read_text().splitlines()
        if l.strip()
    ]
    # nova chamada, mesmo seed → mesma amostra
    build_audit_slice(tmp_path, per_stratum=5, seed=42)
    ids_2 = [
        json.loads(l)['id_registro']
        for l in (tmp_path / 'audit' / 'silent_consensus_sample.jsonl').read_text().splitlines()
        if l.strip()
    ]
    assert ids_1 == ids_2


def test_manifest_counts_match(tmp_path):
    rows = [_mk_row(f'a{i}', 'accepted_exact') for i in range(15)]
    rows += [_mk_row(f'b{i}', 'accepted_semantic') for i in range(8)]
    rows += [_mk_row(f'c{i}', 'escalated_case') for i in range(5)]  # ignored
    _make_var_csv(tmp_path, 'polipo_presente', rows)

    res = build_audit_slice(tmp_path, per_stratum=10, seed=42)
    manifest = json.loads((tmp_path / 'audit' / 'audit_manifest.json').read_text())

    assert manifest['total_population'] == 23  # 15 + 8 silenciosos
    per = manifest['per_variable']['polipo_presente']
    assert per['population'] == 23
    assert per['by_resolution']['accepted_exact'] == 15
    assert per['by_resolution']['accepted_semantic'] == 8
    assert per['sampled'] == res['total_sampled']


def test_multiple_variables(tmp_path):
    """Processa todas as CSVs *.csv no run_dir."""
    _make_var_csv(tmp_path, 'polipo_presente',
                  [_mk_row(f'p{i}', 'accepted_exact') for i in range(12)])
    # Segunda variável
    rows_b = []
    for i in range(12):
        rows_b.append({
            'id_registro': f'b{i}',
            'clinica': 'CL_A',
            'bbps__value': 8,
            'bbps__status': 'ok',
            'bbps__section': 'preparo',
            'bbps__evidence': 'BBPS=8',
            'bbps__certainty': 'high',
            'bbps__resolution': 'accepted_exact',
            'bbps__ia': 8,
        })
    _make_var_csv(tmp_path, 'bbps', rows_b)

    res = build_audit_slice(tmp_path, per_stratum=5, seed=42)
    assert 'polipo_presente' in res['per_variable']
    assert 'bbps' in res['per_variable']


def test_custom_resolutions(tmp_path):
    """--resolutions permite auditar apenas 1 categoria."""
    rows = [_mk_row(f'a{i}', 'accepted_exact') for i in range(10)]
    rows += [_mk_row(f's{i}', 'accepted_semantic') for i in range(10)]
    _make_var_csv(tmp_path, 'polipo_presente', rows)

    build_audit_slice(tmp_path, per_stratum=5, seed=42,
                      resolutions=('accepted_semantic',))
    with (tmp_path / 'audit' / 'silent_consensus_sample.jsonl').open() as f:
        samples = [json.loads(l) for l in f if l.strip()]
    assert all(s['resolution'] == 'accepted_semantic' for s in samples)


def test_empty_run_produces_empty_sample(tmp_path):
    """Run sem resolutions silenciosas produz arquivo vazio e manifest zerado."""
    rows = [_mk_row(f'e{i}', 'escalated_case') for i in range(5)]
    _make_var_csv(tmp_path, 'polipo_presente', rows)

    res = build_audit_slice(tmp_path, per_stratum=10, seed=42)
    assert res['total_sampled'] == 0
    content = (tmp_path / 'audit' / 'silent_consensus_sample.jsonl').read_text()
    assert content.strip() == ''
    manifest = json.loads((tmp_path / 'audit' / 'audit_manifest.json').read_text())
    assert manifest['total_sampled'] == 0
