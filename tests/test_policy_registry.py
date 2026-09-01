"""
Tests do policy_registry — coração do runtime.

Cobre o resolution contract (6 estados terminais) + Mode C v1.1:
    accepted_exact / accepted_semantic
    abstained_unsupported / abstained_source_incomplete
    escalated_policy / escalated_case

Pytest organizado por método, não por feature, para que falha aponte
direto pra função quebrada.
"""
from __future__ import annotations

import pytest

from policy_registry import (  # type: ignore[import-not-found]
    OntologySpec, Policy, PolicyRegistry,
    apply_policy, apply_mode_c, verify_evidence,
    _build_silence_evidence_meta, validate_contract,
)


# ============================================================================
# Fixtures de policies
# ============================================================================

@pytest.fixture
def pol_polipo() -> Policy:
    """LEX-S binária, silêncio = nao."""
    return Policy(
        variable='polipo_presente',
        taxonomy=['LEX-S', 'NEG'],
        ontology=OntologySpec(type='enum', values=['sim', 'nao']),
        allows_implicit_negative=True,
        default_if_silent='nao',
        abstention_statuses=['ambiguous_history'],
        vocabulary_aliases={'lst': 'sim'},
    )


@pytest.fixture
def pol_bbps() -> Policy:
    """NUM-C, silêncio NÃO conta como default."""
    return Policy(
        variable='bbps',
        taxonomy=['NUM-C', 'ORD-E'],
        ontology=OntologySpec(type='integer', range=(0, 9)),
        allows_implicit_negative=False,
        default_if_silent=None,
    )


@pytest.fixture
def pol_intervalo() -> Policy:
    """TEMP enum com default_if_silent='nao_recomendado' E
    allows_implicit_negative=True (silêncio mapeia para default)."""
    return Policy(
        variable='intervalo_recomendado',
        taxonomy=['TEMP', 'INF'],
        ontology=OntologySpec(type='enum', values=['3_anos', '5_anos', 'nao_recomendado']),
        allows_implicit_negative=True,
        default_if_silent='nao_recomendado',
    )


# ============================================================================
# apply_policy — matriz dos 6 estados terminais
# ============================================================================

class TestApplyPolicy:

    def test_accepted_exact_enum_match(self, pol_polipo):
        d = apply_policy('sim', 'literal_match', pol_polipo)
        assert d.resolution == 'accepted_exact'
        assert d.normalized_value == 'sim'

    def test_accepted_exact_numeric_match_in_range(self, pol_bbps):
        d = apply_policy(7, 'soma_3seg', pol_bbps)
        assert d.resolution == 'accepted_exact'
        assert d.normalized_value == 7.0

    def test_accepted_exact_silence_with_implicit_negative(self, pol_polipo):
        d = apply_policy(None, None, pol_polipo)
        assert d.resolution == 'accepted_exact'
        assert d.normalized_value == 'nao'
        assert 'silence' in d.reason

    def test_accepted_semantic_via_alias(self, pol_polipo):
        d = apply_policy('lst', 'polipo_synonym', pol_polipo)
        assert d.resolution == 'accepted_semantic'
        assert d.normalized_value == 'sim'

    def test_abstained_unsupported_by_status(self, pol_polipo):
        d = apply_policy('sim', 'ambiguous_history', pol_polipo)
        assert d.resolution == 'abstained_unsupported'
        assert d.normalized_value is None

    def test_abstained_source_incomplete_no_default(self, pol_bbps):
        # silêncio sem default = abstained
        d = apply_policy(None, None, pol_bbps)
        assert d.resolution == 'abstained_source_incomplete'

    def test_abstained_source_incomplete_default_but_no_implicit_negative(self):
        pol = Policy(
            variable='x',
            taxonomy=['ENUM'],
            ontology=OntologySpec(type='enum', values=['a', 'b']),
            allows_implicit_negative=False,
            default_if_silent='b',
        )
        d = apply_policy(None, None, pol)
        assert d.resolution == 'abstained_source_incomplete'
        assert 'no_implicit_negative' in d.reason

    def test_escalated_case_value_outside_enum(self, pol_polipo):
        d = apply_policy('talvez', 'fuzzy_match', pol_polipo)
        assert d.resolution == 'escalated_case'

    def test_escalated_case_numeric_out_of_range(self, pol_bbps):
        d = apply_policy(15, 'bbps_total', pol_bbps)
        assert d.resolution == 'escalated_case'
        assert 'out-of-range' in d.reason

    def test_escalated_case_non_numeric_for_integer(self, pol_bbps):
        d = apply_policy('três', 'qualitative', pol_bbps)
        assert d.resolution == 'escalated_case'
        assert 'nao numerico' in d.reason


# ============================================================================
# verify_evidence — 4 modos do hybrid verifier (offset-first, GPT-5 #2)
# ============================================================================

TEXT = 'achados: pólipo séssil de 8mm em cólon ascendente. conclusão: sugere-se nova colonoscopia em 3 anos.'


class TestVerifyEvidence:

    def test_silence_legitimate(self):
        v = verify_evidence(None, None, None, TEXT)
        assert v.verified is True
        assert v.support == 'none'
        assert v.reason == 'silence'

    def test_value_without_evidence(self):
        v = verify_evidence('sim', None, 'high', TEXT)
        assert v.verified is False
        assert v.reason == 'value_without_evidence'

    def test_offset_exact_strong_when_high_certainty(self):
        # 'pólipo séssil' está em [9:22]
        s = TEXT.index('pólipo séssil')
        e = s + len('pólipo séssil')
        v = verify_evidence('sim', 'pólipo séssil', 'high', TEXT,
                            span_start=s, span_end=e)
        assert v.verified is True
        assert v.support == 'strong'
        assert v.reason == 'offset_exact'

    def test_offset_exact_weak_when_medium_certainty(self):
        s = TEXT.index('pólipo séssil')
        e = s + len('pólipo séssil')
        v = verify_evidence('sim', 'pólipo séssil', 'medium', TEXT,
                            span_start=s, span_end=e)
        assert v.verified is True
        assert v.support == 'weak'

    def test_offset_normalized_handles_whitespace(self):
        # evidence com whitespace extra deve casar via normalização
        s = TEXT.index('pólipo séssil')
        e = s + len('pólipo séssil')
        v = verify_evidence('sim', '  pólipo  séssil  ', 'high', TEXT,
                            span_start=s, span_end=e)
        assert v.verified is True
        assert v.reason in ('offset_exact', 'offset_normalized')

    def test_legacy_exact_substring_never_strong(self):
        # sem offsets -> cai no fallback substring; nunca strong
        v = verify_evidence('sim', 'pólipo séssil', 'high', TEXT)
        assert v.verified is True
        assert v.support == 'weak'
        assert v.reason == 'legacy_exact_substring'

    def test_legacy_partial_substring_weak_candidate(self):
        # evidence só casa em prefix de 20 chars -> weak_candidate, NOT verified
        partial = 'pólipo séssil de 8mm em supinação esquerda'  # >20 chars, prefix existe
        v = verify_evidence('sim', partial, 'high', TEXT)
        assert v.verified is False
        assert v.support == 'weak_candidate'
        assert v.reason == 'legacy_partial_substring'

    def test_evidence_not_in_text(self):
        v = verify_evidence('sim', 'adenocarcinoma', 'high', TEXT)
        assert v.verified is False
        assert v.support == 'none'
        assert v.reason == 'evidence_not_in_text'


# ============================================================================
# apply_mode_c — silence audit (v1.1 #3) + evidence_class mapping (v1.1 #1)
# ============================================================================

SCOPE_COMPLETE = {
    'detector_version': 'polipo_l1_v4@1.1.0',
    'scope_complete': True,
    'sections_scanned': ['conclusao', 'descricao'],
    'chars_scanned': 412,
    'pattern_families_run': ['polipo_lex', 'negation'],
    'hits_total': 0,
    'explicit_negative_hits': 0,
    'historical_hits': 0,
}

SCOPE_INCOMPLETE = {**SCOPE_COMPLETE, 'scope_complete': False, 'chars_scanned': 0,
                    'sections_scanned': []}


class TestApplyModeC_Silence:
    """Silêncio é coverage claim, não bypass. Três ramos."""

    def test_silence_audited_complete_accepts_default(self, pol_polipo):
        d = apply_mode_c(None, 'no_match', None, None, TEXT, pol_polipo,
                         scope_meta=SCOPE_COMPLETE)
        assert d.resolution == 'accepted_exact'
        assert d.normalized_value == 'nao'
        assert d.evidence_meta is not None
        assert d.evidence_meta['audit_status'] == 'audited_scope_complete'
        assert d.evidence_meta['evidence_kind'] == 'silence_default'

    def test_silence_scope_incomplete_abstains(self, pol_polipo):
        d = apply_mode_c(None, 'no_match', None, None, TEXT, pol_polipo,
                         scope_meta=SCOPE_INCOMPLETE)
        assert d.resolution == 'abstained_source_incomplete'
        assert d.evidence_meta['audit_status'] == 'scope_incomplete'

    def test_silence_legacy_no_scope_meta_is_unaudited(self, pol_polipo):
        # backward compat: detector legacy sem retrofit ainda funciona, mas
        # marca unaudited_legacy_detector para visibilidade
        d = apply_mode_c(None, 'no_match', None, None, TEXT, pol_polipo,
                         scope_meta=None)
        assert d.resolution == 'accepted_exact'
        assert d.normalized_value == 'nao'
        assert d.evidence_meta['audit_status'] == 'unaudited_legacy_detector'

    def test_silence_routing_keyed_on_status_not_value(self, pol_polipo):
        # value=None mas status != 'no_match' -> NÃO entra no ramo silence,
        # cai em verify_evidence (que retorna verified=True silêncio legítimo)
        d = apply_mode_c(None, 'literal_negation', None, None, TEXT,
                         pol_polipo, scope_meta=SCOPE_COMPLETE)
        # caiu no caminho normal: apply_policy(None, ...) com policy aceitando
        # implicit negative -> accepted_exact com default
        assert d.resolution == 'accepted_exact'
        assert d.normalized_value == 'nao'

    def test_silence_default_intervalo_audited(self, pol_intervalo):
        # Caso real do retrofit do intervalo_rec: 'CONCLUSÃO: exame normal' →
        # status='no_match' → audit complete → accepted_exact(nao_recomendado)
        scope = {**SCOPE_COMPLETE, 'detector_version': 'intervalo_rec_l1_v4@1.1.0'}
        d = apply_mode_c(None, 'no_match', None, None,
                         'conclusao: exame normal.',
                         pol_intervalo, scope_meta=scope)
        assert d.resolution == 'accepted_exact'
        assert d.normalized_value == 'nao_recomendado'


class TestApplyModeC_EvidenceClass:
    """v1.1 #1: evidence_class determina accepted_exact vs semantic, não certainty."""

    def test_literal_high_certainty_accepted_exact(self, pol_polipo):
        s = TEXT.index('pólipo séssil')
        e = s + len('pólipo séssil')
        d = apply_mode_c('sim', 'literal_match', 'pólipo séssil',
                         'high', TEXT, pol_polipo,
                         span_start=s, span_end=e,
                         evidence_class='literal')
        assert d.resolution == 'accepted_exact'
        assert 'literal' in d.reason

    def test_lexical_alias_still_accepted_exact(self, pol_polipo):
        # lexical_alias = sinônimo lexical de mesmo conceito (pólipo↔polypoid)
        s = TEXT.index('pólipo séssil')
        e = s + len('pólipo séssil')
        d = apply_mode_c('sim', 'literal_match', 'pólipo séssil',
                         'high', TEXT, pol_polipo,
                         span_start=s, span_end=e,
                         evidence_class='lexical_alias')
        assert d.resolution == 'accepted_exact'

    def test_ontology_alias_demotes_to_semantic(self, pol_polipo):
        # ontology_alias = inferência (LST→pólipo), valor diferente do texto
        s = TEXT.index('pólipo séssil')
        e = s + len('pólipo séssil')
        d = apply_mode_c('sim', 'polipo_synonym', 'pólipo séssil',
                         'high', TEXT, pol_polipo,
                         span_start=s, span_end=e,
                         evidence_class='ontology_alias')
        assert d.resolution == 'accepted_semantic'

    def test_bound_evidence_class_demotes_to_semantic(self, pol_polipo):
        # bound = medida com intervalo (5-8mm), impreciso
        s = TEXT.index('pólipo séssil')
        e = s + len('pólipo séssil')
        d = apply_mode_c('sim', 'measure_interval', 'pólipo séssil',
                         'high', TEXT, pol_polipo,
                         span_start=s, span_end=e,
                         evidence_class='bound')
        assert d.resolution == 'accepted_semantic'

    def test_low_certainty_with_unknown_class_escalates(self, pol_polipo):
        # GPT-5 #1: classe não reconhecida (nem EXACT nem SEMANTIC nem silence)
        # + certainty='low' → escalated_case. Classes conhecidas vencem antes.
        s = TEXT.index('pólipo séssil')
        e = s + len('pólipo séssil')
        d = apply_mode_c('sim', 'fuzzy', 'pólipo séssil',
                         'low', TEXT, pol_polipo,
                         span_start=s, span_end=e,
                         evidence_class='unknown_heuristic')
        assert d.resolution == 'escalated_case'
        assert 'certainty=low' in d.reason

    def test_evidence_failed_escalates_case(self, pol_polipo):
        # evidence não está no texto -> escalated_case (alucinação suspeita)
        d = apply_mode_c('sim', 'literal_match', 'adenocarcinoma',
                         'high', TEXT, pol_polipo,
                         evidence_class='literal')
        assert d.resolution == 'escalated_case'
        assert 'evidence_failed' in d.reason

    def test_legacy_weak_demotes_to_semantic_when_no_class(self, pol_polipo):
        # backward compat: sem evidence_class, fallback ao mapping legacy
        # legacy_exact_substring -> weak -> demote para semantic
        d = apply_mode_c('sim', 'literal_match', 'pólipo séssil',
                         'medium', TEXT, pol_polipo,
                         evidence_class=None)
        assert d.resolution == 'accepted_semantic'


# ============================================================================
# _build_silence_evidence_meta — schema canônico v1.1
# ============================================================================

class TestSilenceEvidenceMeta:

    def test_default_schema_when_scope_meta_missing(self, pol_polipo):
        meta = _build_silence_evidence_meta(None, pol_polipo, 'no_match')
        assert meta['evidence_kind'] == 'silence_default'
        assert meta['policy_id'] == 'polipo_presente.silence.v1'
        assert meta['scope_complete'] is False
        assert meta['detector_version'] == 'unknown'
        assert meta['status_from_detector'] == 'no_match'

    def test_propagates_detector_scope_meta(self, pol_polipo):
        meta = _build_silence_evidence_meta(SCOPE_COMPLETE, pol_polipo, 'no_match')
        assert meta['scope_complete'] is True
        assert meta['detector_version'] == 'polipo_l1_v4@1.1.0'
        assert meta['chars_scanned'] == 412
        assert 'conclusao' in meta['sections_scanned']

    def test_unknown_keys_in_scope_meta_are_dropped(self, pol_polipo):
        scope = {**SCOPE_COMPLETE, 'random_extra_field': 'foo'}
        meta = _build_silence_evidence_meta(scope, pol_polipo, 'no_match')
        assert 'random_extra_field' not in meta


# ============================================================================
# validate_contract — guards estruturais do registry
# ============================================================================

class _StubVar:
    def __init__(self, name, criticality=5):
        self.name = name
        self.criticality = criticality
        self.silence_policy = None


class _StubContract:
    def __init__(self, variables):
        self.variables = variables


def _mk_registry(policies: dict) -> PolicyRegistry:
    return PolicyRegistry(
        id='test', version='1', domain='test', language='pt-BR',
        created='2026-04-14', policies=policies, raw_yaml='',
    )


class TestValidateContract:
    """Guards estruturais que bloqueiam run antes de chegar no auto-adjudicator."""

    def test_enum_policy_without_values_is_error(self, pol_bbps):
        """enum sem values deixaria a guard anti-inversão cega. Falha cedo."""
        bad = Policy(
            variable='polipo_presente',
            taxonomy=['LEX-S'],
            ontology=OntologySpec(type='enum', values=[]),  # ← buraco
            allows_implicit_negative=True,
            default_if_silent='nao',
        )
        reg = _mk_registry({'polipo_presente': bad})
        contract = _StubContract([_StubVar('polipo_presente', 8)])
        res = validate_contract(contract, reg)
        assert any('SEM values' in e for e in res['errors'])
        assert 'polipo_presente' not in res['ok']

    def test_enum_policy_with_values_passes(self, pol_polipo):
        reg = _mk_registry({'polipo_presente': pol_polipo})
        contract = _StubContract([_StubVar('polipo_presente', 8)])
        res = validate_contract(contract, reg)
        assert not res['errors']
        assert 'polipo_presente' in res['ok']

    def test_integer_policy_without_values_is_not_error(self, pol_bbps):
        """type=integer não precisa values; só enum dispara o guard."""
        reg = _mk_registry({'bbps': pol_bbps})
        contract = _StubContract([_StubVar('bbps', 8)])
        res = validate_contract(contract, reg)
        assert not res['errors']
        assert 'bbps' in res['ok']
