"""Gate #2 consensus-first (tarefa #92).

Verifica:
  - apply_cascade promove `abstained_by_consensus` para resolution terminal.
  - build_gate2 NÃO emite items para linhas resolvidas via cascade.
"""
from __future__ import annotations

import pandas as pd

from cascade import (  # type: ignore[import-not-found]
    CrossVerdict, ConsensusVerifier, apply_cascade,
)
from gates import build_gate2  # type: ignore[import-not-found]


class _Policy:
    class _Ont:
        def __init__(self, vals, t='enum'):
            self.values = vals
            self.type = t

    def __init__(self, vals, t='enum'):
        self.ontology = self._Ont(vals, t)


class _FakeV:
    def __init__(self, name, verdicts_by_id):
        self.name = name
        self.model = name
        self._v = verdicts_by_id
        self._id = None

    def set_current_id(self, i):
        self._id = str(i)

    def verify(self, *a, **k):
        return self._v[self._id]


def _mk_df():
    return pd.DataFrame({
        'id_registro': [1, 2, 3],
        'var__value': ['sim', 'nao', 'sim'],
        'var__status': ['match', 'no_match', 'match'],
        'var__resolution': ['escalated_case', 'escalated_case', 'accepted_exact'],
        'var__normalized': ['sim', 'nao', 'sim'],
        'var__ia': ['nao', 'sim', 'sim'],
    })


def test_apply_cascade_promotes_abstained_by_consensus():
    df = _mk_df()
    ids = df['id_registro']
    texts = pd.Series(['', '', ''])
    policy = _Policy(['sim', 'nao'])

    # Família A abstém, Família B abstém => consensus abstain
    va = _FakeV('A', {'1': CrossVerdict(False, 'A', reason='stub_report'),
                      '2': CrossVerdict(False, 'A', reason='abstain')})
    vb = _FakeV('B', {'1': CrossVerdict(False, 'B', reason='stub_report'),
                      '2': CrossVerdict(False, 'B', reason='abstain')})
    consensus = ConsensusVerifier([va, vb])

    stats = apply_cascade(
        df, 'var', policy, texts, ids, consensus,
        only_resolutions=('escalated_case',),
    )

    # ambos escalated viram abstained_by_consensus
    assert df.loc[0, 'var__resolution'] == 'abstained_by_consensus'
    assert df.loc[1, 'var__resolution'] == 'abstained_by_consensus'
    # linha já accepted_exact não é tocada
    assert df.loc[2, 'var__resolution'] == 'accepted_exact'
    assert stats['n_invoked'] == 2


def test_apply_cascade_promotes_consensus_agree():
    df = _mk_df()
    ids = df['id_registro']
    texts = pd.Series(['texto sim', '', ''])
    policy = _Policy(['sim', 'nao'])

    va = _FakeV('A', {'1': CrossVerdict(True, 'A', 'sim', 'e', 'high'),
                      '2': CrossVerdict(False, 'A', reason='abstain')})
    vb = _FakeV('B', {'1': CrossVerdict(True, 'B', 'sim', 'e', 'high'),
                      '2': CrossVerdict(False, 'B', reason='abstain')})
    consensus = ConsensusVerifier([va, vb])

    apply_cascade(df, 'var', policy, texts, ids, consensus,
                  only_resolutions=('escalated_case',))

    assert df.loc[0, 'var__resolution'] == 'accepted_cross_verified'
    assert df.loc[0, 'var__normalized'] == 'sim'
    assert df.loc[1, 'var__resolution'] == 'abstained_by_consensus'


def test_build_gate2_skips_cascade_accepted_disagreements():
    # Monta df com: 3 linhas com L1 != IA, uma já tem cascade_accepted=True.
    df = pd.DataFrame({
        'id_registro': [1, 2, 3],
        'var__value': ['sim', 'sim', 'sim'],
        'var__status': ['match', 'match', 'match'],
        'var__resolution': ['accepted_cross_verified', 'accepted_exact', 'accepted_exact'],
        'var__normalized': ['sim', 'sim', 'sim'],
        'var__ia': ['nao', 'nao', 'nao'],
        'var__cascade_accepted': [True, False, False],
    })
    items = build_gate2({'var': df})
    # Só as 2 linhas não resolvidas por cascade devem contar para disagreement item
    disagreement_items = [it for it in items if it.item_type == 'normalization_disagreement']
    assert len(disagreement_items) == 1
    assert disagreement_items[0].count == 2


def test_build_gate2_skips_abstained_by_consensus():
    df = pd.DataFrame({
        'id_registro': [1, 2],
        'var__value': ['sim', 'sim'],
        'var__status': ['match', 'match'],
        'var__resolution': ['abstained_by_consensus', 'accepted_exact'],
        'var__normalized': ['sim', 'sim'],
        'var__ia': ['nao', 'nao'],
        'var__cascade_accepted': [False, False],
    })
    items = build_gate2({'var': df})
    disagreement_items = [it for it in items if it.item_type == 'normalization_disagreement']
    assert len(disagreement_items) == 1
    assert disagreement_items[0].count == 1
