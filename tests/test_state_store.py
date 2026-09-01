"""Tests do state_store — checkpoint JSONL + resume idempotente."""
from __future__ import annotations

import json

from state_store import (  # type: ignore[import-not-found]
    checkpoint_path, append_row, load_processed, finalize, iter_rows,
)


def test_load_processed_empty_when_file_missing(tmp_path):
    p = checkpoint_path(tmp_path, 'bbps')
    assert load_processed(p, 'id_registro') == {}


def test_append_and_load_roundtrip(tmp_path):
    p = checkpoint_path(tmp_path, 'bbps')
    append_row(p, {'id_registro': 1, 'value': 7})
    append_row(p, {'id_registro': 2, 'value': 8})
    rows = load_processed(p, 'id_registro')
    assert set(rows.keys()) == {1, 2}
    assert rows[1]['value'] == 7


def test_dedup_last_occurrence_wins(tmp_path):
    """Crash entre append e flush pode duplicar id; reader fica com a última."""
    p = checkpoint_path(tmp_path, 'bbps')
    append_row(p, {'id_registro': 1, 'value': 7})
    append_row(p, {'id_registro': 1, 'value': 9})  # retry após crash
    rows = load_processed(p, 'id_registro')
    assert len(rows) == 1
    assert rows[1]['value'] == 9


def test_iter_rows_skips_blank_and_malformed(tmp_path):
    p = checkpoint_path(tmp_path, 'bbps')
    p.write_text('\n'.join([
        json.dumps({'id_registro': 1}),
        '',                          # blank
        'not-json-{{',               # malformed
        json.dumps({'id_registro': 2}),
    ]) + '\n', encoding='utf-8')
    ids = [r['id_registro'] for r in iter_rows(p)]
    assert ids == [1, 2]


def test_finalize_writes_csv_and_removes_jsonl(tmp_path):
    p = checkpoint_path(tmp_path, 'bbps')
    append_row(p, {'id_registro': 1, 'value': 7})
    append_row(p, {'id_registro': 2, 'value': 8})
    csv = tmp_path / 'bbps.csv'
    n = finalize(p, csv, 'id_registro')
    assert n == 2
    assert csv.exists()
    assert not p.exists()
    body = csv.read_text(encoding='utf-8')
    assert 'id_registro' in body
    assert '7' in body and '8' in body


def test_resume_skips_processed_ids(tmp_path):
    """Simula 1ª run que parou no id=2; 2ª run deve pular 1 e 2."""
    p = checkpoint_path(tmp_path, 'bbps')
    append_row(p, {'id_registro': 1, 'value': 'a'})
    append_row(p, {'id_registro': 2, 'value': 'b'})

    processed = load_processed(p, 'id_registro')
    todo_ids = [3, 4, 5]
    new = [i for i in todo_ids if i not in processed]
    assert new == [3, 4, 5]
    # nada mudou em 1, 2
    again = load_processed(p, 'id_registro')
    assert again[1]['value'] == 'a'


def test_unicode_safe_roundtrip(tmp_path):
    p = checkpoint_path(tmp_path, 'polipo')
    append_row(p, {'id_registro': 1, 'evidence': 'pólipo séssil em cólon ascendente'})
    rows = load_processed(p, 'id_registro')
    assert rows[1]['evidence'] == 'pólipo séssil em cólon ascendente'
