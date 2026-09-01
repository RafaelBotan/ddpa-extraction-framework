"""Detector fake para testes de integração.

Gera os cinco estados terminais de forma determinística a partir do texto:
    'LITERAL sim'         → literal, accepted_exact
    'ALIAS lst'           → ontology_alias, accepted_semantic
    'BOUND 5-8mm'         → bound, accepted_semantic
    'SILENT'              → no_match, scope_complete=True → accepted_exact(default)
    'OUTOFENUM talvez'    → literal, mas valor fora da ontology → escalated_case
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

DETECTOR_VERSION = 'fake_detector@1.0.0'


@dataclass
class FakeResult:
    value: Optional[str]
    status: str
    section: Optional[str] = None
    evidence: str = ''
    span_start: int = -1
    span_end: int = -1
    certainty: str = 'high'
    evidence_class: Optional[str] = None
    scope_meta: Optional[dict] = field(default=None)


def _scope(scope_complete: bool) -> dict:
    return {
        'detector_version': DETECTOR_VERSION,
        'scope_complete': scope_complete,
        'sections_scanned': ['conclusao'] if scope_complete else [],
        'chars_scanned': 42 if scope_complete else 0,
        'pattern_families_run': ['fake_lex'],
        'hits_total': 0 if not scope_complete else 1,
        'explicit_negative_hits': 0,
        'historical_hits': 0,
    }


def detect(text: str) -> FakeResult:
    t = text or ''

    if 'LITERAL sim' in t:
        s = t.index('LITERAL sim')
        return FakeResult(
            value='sim', status='literal_match', section='conclusao',
            evidence='LITERAL sim', span_start=s, span_end=s + len('LITERAL sim'),
            certainty='high', evidence_class='literal',
            scope_meta=_scope(True),
        )

    if 'ALIAS lst' in t:
        s = t.index('ALIAS lst')
        return FakeResult(
            value='sim', status='polipo_synonym', section='conclusao',
            evidence='ALIAS lst', span_start=s, span_end=s + len('ALIAS lst'),
            certainty='medium', evidence_class='ontology_alias',
            scope_meta=_scope(True),
        )

    if 'BOUND' in t:
        s = t.index('BOUND')
        return FakeResult(
            value='sim', status='measure_interval', section='descricao',
            evidence='BOUND 5-8mm', span_start=s, span_end=s + len('BOUND 5-8mm'),
            certainty='medium', evidence_class='bound',
            scope_meta=_scope(True),
        )

    if 'OUTOFENUM' in t:
        s = t.index('OUTOFENUM')
        return FakeResult(
            value='talvez', status='literal_match', section='conclusao',
            evidence='OUTOFENUM talvez', span_start=s, span_end=s + len('OUTOFENUM talvez'),
            certainty='high', evidence_class='literal',
            scope_meta=_scope(True),
        )

    # default: silêncio auditável (scope_complete=True → accepted_exact via default)
    return FakeResult(
        value=None, status='no_match', section=None,
        evidence='', span_start=-1, span_end=-1,
        certainty='high', evidence_class='silence_default',
        scope_meta=_scope(True),
    )
