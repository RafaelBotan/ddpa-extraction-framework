"""Auto-adjudicator de patches (tarefa #93).

Verifica que `auto_adjudicate_items`:
  - aplica apenas em normalization_disagreement com action segura
  - respeita min_count
  - não toca items com resolution pré-existente
  - marca todos os demais como 'escalate'
"""
from __future__ import annotations

from patches import auto_adjudicate_items  # type: ignore[import-not-found]


def _mk(**overrides):
    base = {
        'item_id': 'gate2_disagree_var_xx',
        'item_type': 'normalization_disagreement',
        'variable': 'var',
        'count': 15,
        'description': 'd',
        'suggested_patch': {
            'action': 'add_vocabulary_alias_or_reject',
            'variable': 'var',
            'pair': "L1='sim' vs IA='nao'",
        },
        'resolution': None,
    }
    base.update(overrides)
    return base


def test_high_count_disagreement_auto_applied():
    items = [_mk(count=20)]
    stats = auto_adjudicate_items(items, min_count=10)
    assert items[0]['resolution'] == 'apply'
    assert items[0]['auto_adjudicated'] is True
    assert stats['auto_applied'] == ['gate2_disagree_var_xx']


def test_low_count_disagreement_escalated():
    items = [_mk(count=3)]
    auto_adjudicate_items(items, min_count=10)
    assert items[0]['resolution'] == 'escalate'
    assert 'auto_adjudicated' not in items[0]


def test_escalated_case_never_auto_applied():
    items = [_mk(item_type='escalated_case', count=100)]
    auto_adjudicate_items(items, min_count=10)
    assert items[0]['resolution'] == 'escalate'


def test_unknown_action_escalated():
    items = [_mk(suggested_patch={'action': 'drop_variable', 'variable': 'var'})]
    auto_adjudicate_items(items, min_count=10)
    assert items[0]['resolution'] == 'escalate'


def test_existing_resolution_untouched():
    items = [_mk(resolution='reject')]
    stats = auto_adjudicate_items(items, min_count=10)
    assert items[0]['resolution'] == 'reject'
    assert stats['untouched'] == ['gate2_disagree_var_xx']


def test_mixed_batch_partition():
    items = [
        _mk(item_id='a', count=50),                             # auto-apply
        _mk(item_id='b', count=5),                              # escalate (count)
        _mk(item_id='c', item_type='escalated_case', count=50), # escalate (type)
        _mk(item_id='d', count=50, resolution='apply'),          # untouched
    ]
    stats = auto_adjudicate_items(items, min_count=10)
    assert stats['auto_applied'] == ['a']
    assert set(stats['escalated']) == {'b', 'c'}
    assert stats['untouched'] == ['d']


def test_canonical_inversion_blocked():
    """BUG crítico descoberto 2026-04-14: polipo_presente 'nao'↔'sim' foi
    sugerido como alias pelo Gate #2 com count>10. Se aplicado, inverteria
    presença/ausência de pólipo no corpus inteiro."""
    items = [
        _mk(item_id='inv', count=53, suggested_patch={
            'action': 'add_vocabulary_alias_or_reject',
            'variable': 'polipo_presente',
            'pair': "L1='sim' vs IA='nao'",
        }),
    ]
    ontologies = {'polipo_presente': ['sim', 'nao']}
    stats = auto_adjudicate_items(items, min_count=10, ontologies=ontologies)
    assert items[0]['resolution'] == 'escalate'
    assert 'canonical_value_inversion' in stats['escalated_reasons']['inv']


def test_vocabulary_alias_still_works_when_ia_off_ontology():
    """Caso legítimo: IA emite valor fora da ontology enum; L1 emite canônico.
    Isto é o uso correto de alias — deve ser auto-aplicado."""
    items = [
        _mk(item_id='ok', count=50, suggested_patch={
            'action': 'add_vocabulary_alias_or_reject',
            'variable': 'intervalo_recomendado',
            'pair': "L1='sem_intervalo' vs IA='nao_recomendado'",
        }),
    ]
    ontologies = {
        'intervalo_recomendado': ['1_ano', '3_anos', '5_anos', 'sem_intervalo'],
    }
    auto_adjudicate_items(items, min_count=10, ontologies=ontologies)
    assert items[0]['resolution'] == 'apply'


def test_no_ontology_falls_back_to_permissive():
    """Sem ontology passada, mantém comportamento v1 (apenas count+action guard)."""
    items = [_mk(count=50)]
    auto_adjudicate_items(items, min_count=10)  # sem ontologies
    assert items[0]['resolution'] == 'apply'


def test_canonical_binary_allowlist_blocks_even_without_ontology():
    """Camada (b): se variável está em canonical_binary_vars, bloqueia
    inversão mesmo sem ontology declarada no registry."""
    items = [
        _mk(item_id='inv2', count=80, suggested_patch={
            'action': 'add_vocabulary_alias_or_reject',
            'variable': 'polipo_presente',
            'pair': "L1='sim' vs IA='nao'",
        }),
    ]
    stats = auto_adjudicate_items(items, min_count=10)  # sem ontologies
    assert items[0]['resolution'] == 'escalate'
    assert 'canonical_binary_inversion' in stats['escalated_reasons']['inv2']


def test_canonical_binary_allowlist_custom_set():
    """Allowlist customizável — variável fora do set default não bloqueia."""
    items = [
        _mk(item_id='ok2', count=80, suggested_patch={
            'action': 'add_vocabulary_alias_or_reject',
            'variable': 'qualquer_outra_var',
            'pair': "L1='sim' vs IA='nao'",
        }),
    ]
    auto_adjudicate_items(items, min_count=10, canonical_binary_vars=frozenset())
    assert items[0]['resolution'] == 'apply'


# ============================================================================
# Skew guard (tarefa #95, DDPA must-close #2 do double-check GPT)
# ============================================================================

def _safe_pair():
    """Par alias legítimo (não dispara anti-inversão)."""
    return "L1='sem_intervalo' vs IA='nao_recomendado'"


def test_skew_single_clinica_dominates_escalated():
    """Padrão enviesado: 90% dos casos vêm de 1 clínica → escalar."""
    items = [_mk(
        item_id='skew1', count=50, variable='followup_interval_any',
        suggested_patch={
            'action': 'add_vocabulary_alias_or_reject',
            'variable': 'followup_interval_any',
            'pair': _safe_pair(),
        },
        distribution={'clinica': {'CL_A': 45, 'CL_B': 3, 'CL_C': 2}},
    )]
    stats = auto_adjudicate_items(items, min_count=10)
    assert items[0]['resolution'] == 'escalate'
    reason = stats['escalated_reasons']['skew1']
    assert 'skew' in reason
    assert 'clinica=CL_A' in reason


def test_skew_balanced_distribution_applied():
    """Distribuição equilibrada: nenhum bucket ≥70% → aplica normalmente."""
    items = [_mk(
        item_id='bal', count=50, variable='followup_interval_any',
        suggested_patch={
            'action': 'add_vocabulary_alias_or_reject',
            'variable': 'followup_interval_any',
            'pair': _safe_pair(),
        },
        distribution={
            'clinica': {'CL_A': 18, 'CL_B': 17, 'CL_C': 15},
            'era': {'2023': 25, '2024': 25},
        },
    )]
    auto_adjudicate_items(items, min_count=10)
    assert items[0]['resolution'] == 'apply'


def test_skew_below_min_count_still_applied():
    """count baixo para skew (<skew_min_count) não dispara skew guard.
    O count já passou no min_count geral (10); skew_min_count é threshold
    adicional para evitar falso skew em N pequeno."""
    items = [_mk(
        item_id='low', count=11, variable='followup_interval_any',
        suggested_patch={
            'action': 'add_vocabulary_alias_or_reject',
            'variable': 'followup_interval_any',
            'pair': _safe_pair(),
        },
        distribution={'clinica': {'CL_A': 10, 'CL_B': 1}},  # 91% share
    )]
    # skew_min_count=20 > count=11, então skew guard não atua
    auto_adjudicate_items(items, min_count=10, skew_min_count=20)
    assert items[0]['resolution'] == 'apply'


def test_skew_empty_distribution_applied():
    """Distribuição vazia (sem strata detectados): skew guard não atua."""
    items = [_mk(
        item_id='nodist', count=50, variable='followup_interval_any',
        suggested_patch={
            'action': 'add_vocabulary_alias_or_reject',
            'variable': 'followup_interval_any',
            'pair': _safe_pair(),
        },
        distribution={},
    )]
    auto_adjudicate_items(items, min_count=10)
    assert items[0]['resolution'] == 'apply'


def test_skew_threshold_configurable():
    """Threshold custom: 60% dispara em distribuição 65/35."""
    items = [_mk(
        item_id='cfg', count=50, variable='followup_interval_any',
        suggested_patch={
            'action': 'add_vocabulary_alias_or_reject',
            'variable': 'followup_interval_any',
            'pair': _safe_pair(),
        },
        distribution={'clinica': {'CL_A': 33, 'CL_B': 17}},  # 66% share
    )]
    auto_adjudicate_items(items, min_count=10, skew_threshold=0.60)
    assert items[0]['resolution'] == 'escalate'


def test_skew_any_stratum_triggers():
    """Clínica equilibrada mas template dominante: ainda escala."""
    items = [_mk(
        item_id='tpl', count=50, variable='followup_interval_any',
        suggested_patch={
            'action': 'add_vocabulary_alias_or_reject',
            'variable': 'followup_interval_any',
            'pair': _safe_pair(),
        },
        distribution={
            'clinica': {'CL_A': 17, 'CL_B': 17, 'CL_C': 16},  # balanceado
            'template': {'TPL_X': 42, 'TPL_Y': 8},  # 84% share
        },
    )]
    stats = auto_adjudicate_items(items, min_count=10)
    assert items[0]['resolution'] == 'escalate'
    assert 'template=TPL_X' in stats['escalated_reasons']['tpl']


# ============================================================================
# Numeric guard (Test E Camada 1 achado 2026-04-15)
# ============================================================================

def test_numeric_variable_meas_n_escalated():
    """BUG descoberto em Test E Camada 1: auto-adjudicator estava aceitando
    vocab_alias entre valores numéricos (ex: polipo_tamanho '10.0'→'5.0'),
    o que é clinicamente errado. Guard deve escalar."""
    items = [_mk(
        item_id='num_meas', count=20, variable='polipo_tamanho_max_mm',
        suggested_patch={
            'action': 'add_vocabulary_alias_or_reject',
            'variable': 'polipo_tamanho_max_mm',
            'pair': 'L1=5.0 vs IA=10.0',
        },
    )]
    var_taxonomies = {'polipo_tamanho_max_mm': {'MEAS-N'}}
    stats = auto_adjudicate_items(items, min_count=10, var_taxonomies=var_taxonomies)
    assert items[0]['resolution'] == 'escalate'
    assert 'numeric_variable' in stats['escalated_reasons']['num_meas']


def test_numeric_variable_num_c_escalated():
    """NUM-C (BBPS total) também deve escalar vocab_alias."""
    items = [_mk(
        item_id='num_c', count=15, variable='bbps',
        suggested_patch={
            'action': 'add_vocabulary_alias_or_reject',
            'variable': 'bbps',
            'pair': 'L1=8.0 vs IA=9.0',
        },
    )]
    var_taxonomies = {'bbps': {'NUM-C'}}
    stats = auto_adjudicate_items(items, min_count=10, var_taxonomies=var_taxonomies)
    assert items[0]['resolution'] == 'escalate'
    assert 'numeric_variable' in stats['escalated_reasons']['num_c']


def test_numeric_guard_ord_e_escalated():
    """ORD-E (Bethesda I-VI, LA A-D) também escala — ordinais não são sinônimos."""
    items = [_mk(
        item_id='ord', count=25, variable='bethesda',
        suggested_patch={
            'action': 'add_vocabulary_alias_or_reject',
            'variable': 'bethesda',
            'pair': "L1='III' vs IA='IV'",
        },
    )]
    var_taxonomies = {'bethesda': {'ORD-E'}}
    stats = auto_adjudicate_items(items, min_count=10, var_taxonomies=var_taxonomies)
    assert items[0]['resolution'] == 'escalate'
    assert 'numeric_variable' in stats['escalated_reasons']['ord']


def test_textual_variable_still_applies():
    """Variável textual (TXT-L) sem taxonomia numérica: guard não atua."""
    items = [_mk(
        item_id='txt', count=30, variable='followup_interval_any',
        suggested_patch={
            'action': 'add_vocabulary_alias_or_reject',
            'variable': 'followup_interval_any',
            'pair': _safe_pair(),
        },
    )]
    var_taxonomies = {'followup_interval_any': {'TXT-L', 'CONT-I'}}
    auto_adjudicate_items(items, min_count=10, var_taxonomies=var_taxonomies)
    assert items[0]['resolution'] == 'apply'


def test_no_taxonomy_falls_back_to_permissive():
    """Sem var_taxonomies fornecido, comportamento v1 preservado."""
    items = [_mk(item_id='plain', count=50)]
    auto_adjudicate_items(items, min_count=10)  # sem var_taxonomies
    assert items[0]['resolution'] == 'apply'
