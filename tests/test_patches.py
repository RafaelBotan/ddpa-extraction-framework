"""Tests do declarative patcher v1 — 3 ações + idempotência + backup."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from patches import apply_patches, _parse_pair_key  # type: ignore[import-not-found]


# ============================================================================
# Helpers
# ============================================================================

def _write_registry(path: Path, policies: dict) -> None:
    data = {
        'registry': {'id': 'test', 'version': '1', 'domain': 'test', 'language': 'pt-BR'},
        'policies': policies,
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding='utf-8')


def _write_gate(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + '\n')


def _read_registry(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding='utf-8'))


# ============================================================================
# _parse_pair_key
# ============================================================================

def test_parse_pair_key_quoted_strings():
    l1, ia = _parse_pair_key("L1='nao' vs IA='sim'")
    assert l1 == 'nao' and ia == 'sim'


def test_parse_pair_key_double_quoted():
    l1, ia = _parse_pair_key('L1="3_anos" vs IA="3a"')
    assert l1 == '3_anos' and ia == '3a'


def test_parse_pair_key_returns_empty_on_garbage():
    l1, ia = _parse_pair_key('not a pair')
    assert l1 == '' and ia == ''


# ============================================================================
# add_vocabulary_alias_or_reject
# ============================================================================

def test_add_vocabulary_alias_writes_to_registry(tmp_path):
    reg = tmp_path / 'reg.yaml'
    _write_registry(reg, {
        'intervalo_recomendado': {
            'output_ontology': {'type': 'enum', 'values': ['3_anos', '5_anos']},
        },
    })
    gate = tmp_path / 'gates' / 'gate2.jsonl'
    _write_gate(gate, [{
        'item_id': 'g2_001',
        'variable': 'intervalo_recomendado',
        'resolution': 'apply',
        'suggested_patch': {
            'action': 'add_vocabulary_alias_or_reject',
            'variable': 'intervalo_recomendado',
            'pair': "L1='3_anos' vs IA='3a'",
        },
    }])

    res = apply_patches(gate, reg)
    assert len(res['applied']) == 1

    out = _read_registry(reg)
    aliases = out['policies']['intervalo_recomendado']['vocabulary_aliases']
    assert aliases == {'3a': '3_anos'}


def test_alias_idempotent_skipped_on_duplicate(tmp_path):
    reg = tmp_path / 'reg.yaml'
    _write_registry(reg, {
        'intervalo_recomendado': {
            'output_ontology': {'type': 'enum', 'values': ['3_anos']},
            'vocabulary_aliases': {'3a': '3_anos'},
        },
    })
    gate = tmp_path / 'gates' / 'gate2.jsonl'
    _write_gate(gate, [{
        'item_id': 'g2_002',
        'variable': 'intervalo_recomendado',
        'resolution': 'apply',
        'suggested_patch': {
            'action': 'add_vocabulary_alias_or_reject',
            'variable': 'intervalo_recomendado',
            'pair': "L1='3_anos' vs IA='3a'",
        },
    }])
    res = apply_patches(gate, reg)
    assert len(res['applied']) == 0
    assert len(res['skipped']) == 1


# ============================================================================
# add_abstention_status_or_alias
# ============================================================================

def test_add_abstention_status(tmp_path):
    reg = tmp_path / 'reg.yaml'
    _write_registry(reg, {
        'polipo_presente': {
            'output_ontology': {'type': 'enum', 'values': ['sim', 'nao']},
        },
    })
    gate = tmp_path / 'gates' / 'gate2.jsonl'
    _write_gate(gate, [{
        'item_id': 'g2_003',
        'variable': 'polipo_presente',
        'resolution': 'apply',
        'suggested_patch': {
            'action': 'add_abstention_status_or_alias',
            'variable': 'polipo_presente',
            'status': 'ambiguous_history',
        },
    }])
    res = apply_patches(gate, reg)
    assert len(res['applied']) == 1
    out = _read_registry(reg)
    assert 'ambiguous_history' in out['policies']['polipo_presente']['abstention_statuses']


# ============================================================================
# add_ontology_value
# ============================================================================

def test_add_ontology_value(tmp_path):
    reg = tmp_path / 'reg.yaml'
    _write_registry(reg, {
        'intervalo_recomendado': {
            'output_ontology': {'type': 'enum', 'values': ['3_anos', '5_anos']},
        },
    })
    gate = tmp_path / 'gates' / 'gate2.jsonl'
    _write_gate(gate, [{
        'item_id': 'g2_004',
        'variable': 'intervalo_recomendado',
        'resolution': 'apply',
        'suggested_patch': {
            'action': 'add_ontology_value',
            'variable': 'intervalo_recomendado',
            'value': '7_anos',
        },
    }])
    res = apply_patches(gate, reg)
    assert len(res['applied']) == 1
    out = _read_registry(reg)
    assert '7_anos' in out['policies']['intervalo_recomendado']['output_ontology']['values']


# ============================================================================
# Filtros e segurança
# ============================================================================

def test_resolution_reject_is_skipped(tmp_path):
    reg = tmp_path / 'reg.yaml'
    _write_registry(reg, {
        'polipo_presente': {'output_ontology': {'type': 'enum', 'values': ['sim', 'nao']}},
    })
    gate = tmp_path / 'gates' / 'gate2.jsonl'
    _write_gate(gate, [{
        'item_id': 'g2_005',
        'variable': 'polipo_presente',
        'resolution': 'reject',
        'suggested_patch': {'action': 'add_abstention_status_or_alias',
                            'variable': 'polipo_presente', 'status': 'foo'},
    }])
    res = apply_patches(gate, reg)
    assert len(res['applied']) == 0
    assert len(res['skipped']) == 1


def test_unknown_variable_logs_error(tmp_path):
    reg = tmp_path / 'reg.yaml'
    _write_registry(reg, {'polipo_presente': {'output_ontology':
                                              {'type': 'enum', 'values': ['sim']}}})
    gate = tmp_path / 'gates' / 'gate2.jsonl'
    _write_gate(gate, [{
        'item_id': 'g2_006',
        'variable': 'nonexistent_var',
        'resolution': 'apply',
        'suggested_patch': {'action': 'add_abstention_status_or_alias',
                            'variable': 'nonexistent_var', 'status': 'foo'},
    }])
    res = apply_patches(gate, reg)
    assert len(res['errors']) == 1
    assert 'nonexistent_var' in res['errors'][0]['reason']


def test_dry_run_does_not_modify_registry(tmp_path):
    reg = tmp_path / 'reg.yaml'
    _write_registry(reg, {
        'polipo_presente': {'output_ontology': {'type': 'enum', 'values': ['sim', 'nao']}},
    })
    original = reg.read_text(encoding='utf-8')
    gate = tmp_path / 'gates' / 'gate2.jsonl'
    _write_gate(gate, [{
        'item_id': 'g2_007',
        'variable': 'polipo_presente',
        'resolution': 'apply',
        'suggested_patch': {'action': 'add_abstention_status_or_alias',
                            'variable': 'polipo_presente', 'status': 'foo'},
    }])
    res = apply_patches(gate, reg, dry_run=True)
    assert len(res['applied']) == 1
    assert reg.read_text(encoding='utf-8') == original


def test_real_apply_creates_backup(tmp_path):
    reg = tmp_path / 'reg.yaml'
    _write_registry(reg, {
        'polipo_presente': {'output_ontology': {'type': 'enum', 'values': ['sim', 'nao']}},
    })
    gate = tmp_path / 'gates' / 'gate2.jsonl'
    _write_gate(gate, [{
        'item_id': 'g2_008',
        'variable': 'polipo_presente',
        'resolution': 'apply',
        'suggested_patch': {'action': 'add_abstention_status_or_alias',
                            'variable': 'polipo_presente', 'status': 'foo'},
    }])
    apply_patches(gate, reg)
    bak = reg.with_suffix(reg.suffix + '.bak')
    assert bak.exists()


def test_unsupported_action_skipped_not_applied(tmp_path):
    reg = tmp_path / 'reg.yaml'
    _write_registry(reg, {
        'polipo_presente': {'output_ontology': {'type': 'enum', 'values': ['sim']}},
    })
    gate = tmp_path / 'gates' / 'gate2.jsonl'
    _write_gate(gate, [{
        'item_id': 'g2_009',
        'variable': 'polipo_presente',
        'resolution': 'apply',
        'suggested_patch': {'action': 'add_logical_dependency',  # v1 não suporta
                            'variable': 'polipo_presente'},
    }])
    res = apply_patches(gate, reg)
    assert len(res['applied']) == 0
    assert len(res['skipped']) == 1
    assert 'não suportada' in res['skipped'][0]['reason']
