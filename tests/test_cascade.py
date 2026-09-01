"""Tests do cascade v2 — GPT5Verifier cache + ConsensusVerifier.

Não fazemos chamadas reais à API aqui. GPT5Verifier é testado via:
  - cache hit (pre-populado)
  - gate de ambiente desligado (ALLOW_L4_API ausente)
ConsensusVerifier é testado via stubs que herdam da interface.
"""
from __future__ import annotations

import json

import pytest

from cascade import (  # type: ignore[import-not-found]
    CrossVerdict, NullVerifier, StubVerifier, GPT5Verifier, ConsensusVerifier,
    GPT5_PROMPT_TEMPLATE,
)


# ============================================================================
# GPT5_PROMPT_TEMPLATE — não pode vazar candidate; tem regras-chave
# ============================================================================

class TestPromptTemplate:

    def test_prompt_does_not_leak_candidate(self):
        # Substituir placeholders só com os aceitos (sem 'candidate').
        p = GPT5_PROMPT_TEMPLATE.format(
            variable='polipo_presente',
            ontology=['sim', 'nao'],
            text='laudo qualquer',
        )
        assert '{candidate}' not in p
        assert 'Valor proposto por outro modelo' not in p

    def test_prompt_has_section_hierarchy_rule(self):
        p = GPT5_PROMPT_TEMPLATE.format(
            variable='x', ontology=['a'], text='t',
        )
        assert 'HIERARQUIA DE SEÇÕES' in p
        assert 'INDICAÇÃO' in p

    def test_prompt_has_stub_rule(self):
        p = GPT5_PROMPT_TEMPLATE.format(
            variable='x', ontology=['a'], text='t',
        )
        assert 'STUB INSTITUCIONAL' in p or 'stub_report' in p

    def test_prompt_forces_literal_evidence(self):
        p = GPT5_PROMPT_TEMPLATE.format(
            variable='x', ontology=['a'], text='t',
        )
        assert 'substring LITERAL' in p or 'substring literal' in p.lower()


# ============================================================================
# GPT5Verifier — gate, cache e budget sem chamar API
# ============================================================================

class TestGPT5VerifierGating:

    def test_disabled_when_env_not_set(self, tmp_path, monkeypatch):
        monkeypatch.delenv('ALLOW_L4_API', raising=False)
        monkeypatch.delenv('OPENAI_API_KEY', raising=False)
        v = GPT5Verifier(cache_path=tmp_path / 'cache.jsonl')
        v.set_current_id('foo')
        verdict = v.verify('texto', 'polipo_presente', 'sim', ['sim', 'nao'], 'enum')
        assert verdict.accepted is False
        assert 'disabled' in verdict.reason

    def test_cache_hit_bypasses_api(self, tmp_path, monkeypatch):
        # Sem env, sem API — mas cache hit retorna verdict real.
        monkeypatch.delenv('ALLOW_L4_API', raising=False)
        cache = tmp_path / 'cache.jsonl'
        cache.write_text(json.dumps({
            'id_key': '42', 'variable': 'polipo_presente', 'model': 'gpt-4.1-mini',
            'verdict': {
                'accepted': True, 'model': 'gpt-4.1-mini',
                'value': 'sim', 'evidence': 'pólipo séssil',
                'confidence': 'high', 'reason': 'accepted:literal',
            },
        }) + '\n', encoding='utf-8')
        v = GPT5Verifier(cache_path=cache)
        v.set_current_id('42')
        verdict = v.verify('texto com pólipo séssil', 'polipo_presente',
                           'sim', ['sim', 'nao'], 'enum')
        assert verdict.accepted is True
        assert verdict.value == 'sim'
        assert 'cache_hit' in verdict.reason

    def test_budget_exhausted(self, tmp_path, monkeypatch):
        monkeypatch.setenv('ALLOW_L4_API', '1')
        monkeypatch.setenv('OPENAI_API_KEY', 'fake')
        v = GPT5Verifier(max_calls=0, cache_path=tmp_path / 'cache.jsonl')
        v.set_current_id('x')
        verdict = v.verify('texto', 'polipo_presente', 'sim', ['sim', 'nao'], 'enum')
        assert verdict.accepted is False
        assert verdict.reason == 'budget_exhausted'


# ============================================================================
# ConsensusVerifier — 3 cenários de regra
# ============================================================================

class _FakeVerifier:
    """Stub in-memory com verdict fixo por id."""
    def __init__(self, name, verdicts_by_id):
        self.name = name
        self.model = name
        self._v = verdicts_by_id
        self._id = None

    def set_current_id(self, i):
        self._id = str(i)

    def verify(self, text, variable, cand, ont, ont_type):
        return self._v[self._id]


class TestConsensusVerifier:

    def test_all_agree_same_value_accepted(self):
        va = _FakeVerifier('A', {'1': CrossVerdict(True, 'A', 'sim', 'pólipo', 'high')})
        vb = _FakeVerifier('B', {'1': CrossVerdict(True, 'B', 'sim', 'pólipo', 'high')})
        c = ConsensusVerifier([va, vb])
        c.set_current_id('1')
        r = c.verify('texto', 'polipo_presente', None, ['sim', 'nao'], 'enum')
        assert r.accepted is True
        assert r.value == 'sim'
        assert 'consensus_agree' in r.reason

    def test_all_abstain_returns_abstained_by_consensus(self):
        va = _FakeVerifier('A', {'1': CrossVerdict(False, 'A', reason='stub_report')})
        vb = _FakeVerifier('B', {'1': CrossVerdict(False, 'B', reason='abstain')})
        c = ConsensusVerifier([va, vb])
        c.set_current_id('1')
        r = c.verify('t', 'x', None, ['a'], 'enum')
        assert r.accepted is False
        assert r.reason == 'abstained_by_consensus'

    def test_split_verdict_stays_escalated(self):
        va = _FakeVerifier('A', {'1': CrossVerdict(True, 'A', 'sim', 'e1', 'high')})
        vb = _FakeVerifier('B', {'1': CrossVerdict(True, 'B', 'nao', 'e2', 'high')})
        c = ConsensusVerifier([va, vb])
        c.set_current_id('1')
        r = c.verify('t', 'x', None, ['sim', 'nao'], 'enum')
        assert r.accepted is False
        assert r.reason.startswith('split:')

    def test_one_accept_one_abstain_is_split(self):
        va = _FakeVerifier('A', {'1': CrossVerdict(True, 'A', 'sim', 'e', 'high')})
        vb = _FakeVerifier('B', {'1': CrossVerdict(False, 'B', reason='stub_report')})
        c = ConsensusVerifier([va, vb])
        c.set_current_id('1')
        r = c.verify('t', 'x', None, ['sim', 'nao'], 'enum')
        assert r.accepted is False
        assert r.reason.startswith('split:')

    def test_propagates_set_current_id_to_all(self):
        ids_seen = {'A': None, 'B': None}

        class _T:
            def __init__(self, n):
                self.name = n
                self.model = n

            def set_current_id(self, i):
                ids_seen[self.name] = i

            def verify(self, *a, **k):
                return CrossVerdict(False, self.name, reason='r')

        c = ConsensusVerifier([_T('A'), _T('B')])
        c.set_current_id('42')
        assert ids_seen == {'A': '42', 'B': '42'}
