"""
L1 detector v4 — bbps (preparo_boston_total).

Filosofia v4-B (NUM-C):
- Exigir âncora 'boston' ou 'bbps' antes de aceitar qualquer dígito (anti-pattern defense).
- Section priority: preparo > inicio > descricao. NUNCA conclusao/indicacao.
- Soma de 3 segmentos é prioritária (deterministic + permite validação aritmética).
- Quando soma != total declarado → flag arith_inconsistent (não escolher unilateralmente).
- Evidence store completo: (value, status, section, span, evidence, computed_sum, declared_total, arith_flag).

Refatorado em 2026-04-11 para consumir `framework_core`. normalize/segment vêm
do core; apenas patterns BBPS-específicos vivem aqui.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional

from framework_core import (
    normalize,
    segment,
    ENDOSCOPY_SECTIONS,
    evidence_around,
)


DETECTOR_VERSION = 'bbps_l1_v4@1.1.0'


def _build_scope_meta(sections_scanned, chars_scanned, pattern_families_run,
                     hits_total, scope_complete=None):
    if scope_complete is None:
        scope_complete = chars_scanned > 0 and len(sections_scanned) > 0
    return {
        'detector_version': DETECTOR_VERSION,
        'scope_complete': bool(scope_complete),
        'sections_scanned': list(sections_scanned),
        'chars_scanned': int(chars_scanned),
        'pattern_families_run': list(pattern_families_run),
        'hits_total': int(hits_total),
        'explicit_negative_hits': 0,
        'historical_hits': 0,
    }


# ---------- patterns BBPS-específicos ----------

PAT_BOSTON_ANCHOR = re.compile(r'\b(?:bbps|boston)\b')
# Aceita "o" como typo para 0 no contexto Boston (achado real: "boston = o")
PAT_SOMA_3SEG = re.compile(r'\b([0-3])\s*\+\s*([0-3])\s*\+\s*([0-3])\b')
PAT_SOBRE_9 = re.compile(r'\b(\d)\s*/\s*9\b')
PAT_TOTAL_NEAR_BOSTON = re.compile(
    r'\bboston\b[^.]{0,30}?[\s:=\(\-]\s*0?(\d|10|o)\b'
)
PAT_BBPS_TOTAL = re.compile(r'\bbbps\b[\s:=\-]*\s*0?(\d|10|o)\b')
PAT_LABELED_SEG_ABBREV = re.compile(
    r'\bcd\s*:?\s*([0-3o])\b[^.]{0,30}?\bct\s*:?\s*([0-3o])\b[^.]{0,30}?\bce\s*:?\s*([0-3o])\b'
)
# Forma com nomes completos: "colon direito X + colon transverso Y + colon esquerdo Z"
PAT_LABELED_SEG_FULL = re.compile(
    r'colon\s+direito\s*:?\s*([0-3o])\b[^.]{0,40}?'
    r'colon\s+transverso\s*:?\s*([0-3o])\b[^.]{0,40}?'
    r'colon\s+esquerdo\s*:?\s*([0-3o])\b'
)


def _digit(g: str) -> int:
    """Converte 'o' (typo do médico) em 0; senão int normal."""
    return 0 if g == 'o' else int(g)


# ---------- detecção ----------

@dataclass
class BBPSResult:
    value: Optional[int]                  # total final
    status: str                            # pattern_id
    section: Optional[str]                 # 'preparo' / 'descricao' / 'inicio' / None
    computed_sum: Optional[int] = None     # soma dos 3 segmentos
    declared_total: Optional[int] = None   # total escrito
    arith_flag: bool = False               # True se soma != total
    span_start: int = -1
    span_end: int = -1
    evidence: str = ''
    scope_meta: Optional[dict] = None
    evidence_class: Optional[str] = None


def _scan_section(content: str, section_name: str, base_offset: int) -> Optional[BBPSResult]:
    """Procura BBPS dentro de uma seção. Exige âncora 'boston' ou 'bbps' EXCETO
    quando o conteúdo é da seção [preparo] — ali, estar na seção já é âncora
    contextual e dispensa o termo literal."""
    is_preparo = (section_name == 'preparo')
    if not is_preparo and not PAT_BOSTON_ANCHOR.search(content):
        return None

    # 1. Soma 3 segmentos (mais robusta)
    m = PAT_SOMA_3SEG.search(content)
    if m and not is_preparo:
        # Fora do preparo: verifica que o match está em janela ±60 chars de 'boston'/'bbps'
        a = max(0, m.start() - 60)
        b = min(len(content), m.end() + 60)
        if not PAT_BOSTON_ANCHOR.search(content[a:b]):
            m = None
    if m:
        s1, s2, s3 = int(m.group(1)), int(m.group(2)), int(m.group(3))
        soma = s1 + s2 + s3
        # tentar achar total declarado próximo (=N ou /9)
        tail = content[m.end():m.end() + 30]
        declared = None
        m_eq = re.match(r'\s*=\s*0?(\d|10)\b', tail)
        if m_eq:
            declared = int(m_eq.group(1))
        else:
            m_sl = re.match(r'\s*=?\s*0?(\d|10)\s*/\s*9\b', tail)
            if m_sl:
                declared = int(m_sl.group(1))
        arith_flag = (declared is not None and declared != soma)
        return BBPSResult(
            value=soma,
            status='bbps_soma_3seg' + ('_arith_inc' if arith_flag else ''),
            section=section_name,
            computed_sum=soma,
            declared_total=declared,
            arith_flag=arith_flag,
            span_start=base_offset + m.start(),
            span_end=base_offset + m.end(),
            evidence=evidence_around(content, m.start(), m.end()),
        )

    # 2. CD/CT/CE labels (abreviado OU nome completo "colon direito/transverso/esquerdo")
    m = PAT_LABELED_SEG_ABBREV.search(content) or PAT_LABELED_SEG_FULL.search(content)
    if m:
        s1, s2, s3 = _digit(m.group(1)), _digit(m.group(2)), _digit(m.group(3))
        soma = s1 + s2 + s3
        return BBPSResult(
            value=soma, status='bbps_labeled_seg', section=section_name,
            computed_sum=soma, declared_total=None, arith_flag=False,
            span_start=base_offset + m.start(), span_end=base_offset + m.end(),
            evidence=evidence_around(content, m.start(), m.end()),
        )

    # 3. N/9 com âncora boston próxima
    for m in PAT_SOBRE_9.finditer(content):
        a = max(0, m.start() - 60)
        b = min(len(content), m.end() + 60)
        if PAT_BOSTON_ANCHOR.search(content[a:b]):
            v = int(m.group(1))
            return BBPSResult(
                value=v, status='bbps_sobre_9', section=section_name,
                computed_sum=None, declared_total=v, arith_flag=False,
                span_start=base_offset + m.start(), span_end=base_offset + m.end(),
                evidence=evidence_around(content, m.start(), m.end()),
            )

    # 4. Total simples próximo de 'boston'
    m = PAT_TOTAL_NEAR_BOSTON.search(content)
    if m:
        v = _digit(m.group(1)) if m.group(1) != '10' else 10
        if 0 <= v <= 9:
            return BBPSResult(
                value=v, status='bbps_total_near', section=section_name,
                computed_sum=None, declared_total=v, arith_flag=False,
                span_start=base_offset + m.start(), span_end=base_offset + m.end(),
                evidence=evidence_around(content, m.start(), m.end()),
            )

    # 5. BBPS total
    m = PAT_BBPS_TOTAL.search(content)
    if m:
        v = _digit(m.group(1)) if m.group(1) != '10' else 10
        if 0 <= v <= 9:
            return BBPSResult(
                value=v, status='bbps_total', section=section_name,
                computed_sum=None, declared_total=v, arith_flag=False,
                span_start=base_offset + m.start(), span_end=base_offset + m.end(),
                evidence=evidence_around(content, m.start(), m.end()),
            )

    return None


SECTION_PRIORITY = ['preparo', 'sedacao', 'inicio', 'descricao', 'corpo']


def detect_bbps(text: str) -> BBPSResult:
    norm = normalize(text)
    if not norm:
        return BBPSResult(
            None, 'empty', None,
            evidence_class='silence_default',
            scope_meta=_build_scope_meta(
                sections_scanned=[], chars_scanned=0,
                pattern_families_run=[], hits_total=0, scope_complete=False,
            ),
        )

    seg = segment(norm, ENDOSCOPY_SECTIONS)
    sections_scanned = [s for s in SECTION_PRIORITY if seg.get(s)]
    chars_scanned = sum(len(b.content) for s in sections_scanned for b in seg.get(s))

    families = ['boston_anchor', 'soma_3seg', 'labeled_seg', 'sobre_9',
                'total_near_boston', 'bbps_total']

    for sec_name in SECTION_PRIORITY:
        for block in seg.get(sec_name):
            r = _scan_section(block.content, sec_name, base_offset=block.start)
            if r is not None:
                r.evidence_class = 'literal'
                r.scope_meta = _build_scope_meta(
                    sections_scanned=sections_scanned, chars_scanned=chars_scanned,
                    pattern_families_run=families, hits_total=1, scope_complete=True,
                )
                return r

    return BBPSResult(
        None, 'no_match', None,
        evidence_class='silence_default',
        scope_meta=_build_scope_meta(
            sections_scanned=sections_scanned, chars_scanned=chars_scanned,
            pattern_families_run=families, hits_total=0, scope_complete=True,
        ),
    )


# ---------- smoke ----------

if __name__ == '__main__':
    cases = [
        ('[PREPARO] Adequado. BOSTON 9.', 9, 'bbps_total_near'),
        ('[PREPARO] Adequado (Boston = 9).', 9, 'bbps_total_near'),
        ('[PREPARO] do colon: adequado (Escala de Boston: 3+3+3=9)', 9, 'bbps_soma_3seg'),
        ('[PREPARO] regular (Escala de Boston: 2+2+2=6/9)', 6, 'bbps_soma_3seg'),
        ('[PREPARO] BOM (BOSTON 3+3+3=9)', 9, 'bbps_soma_3seg'),
        ('[PREPARO] Adequado BOSTON 9 (3+3+3)', 9, 'bbps_soma_3seg'),  # soma é prioritária
        ('[PREPARO] regular (BOSTON = CD 0 + CT 1 + CE 1 = 2)', 2, 'bbps_labeled_seg'),
        ('Adequado (Boston = 5/9)', 5, 'bbps_sobre_9'),
        ('boston 09', 9, 'bbps_total_near'),
        ('[PREPARO] Adequado. boston 9.', 9, 'bbps_total_near'),
        # arith inconsistent
        ('[PREPARO] regular (escala de boston: 1+2+3=5/9)', 6, 'bbps_soma_3seg_arith_inc'),
        # anti patterns — sem âncora boston
        ('Polipo de 5 mm em colon ascendente', None, 'no_match'),
        ('Paris 0-Is, NICE 2', None, 'no_match'),
        # conclusion mention should NOT count
        ('[CONCLUSAO] Boston 9. [PREPARO] adequado.', None, 'no_match'),
        # typo "o" para 0
        ('[PREPARO] boston = o', 0, 'bbps_total_near'),
        ('[PREPARO] inadequado - boston o', 0, 'bbps_total_near'),
        # nomes completos de segmentos
        ('[PREPARO] insuficiente (escala de boston: colon direito 0 + colon transverso 1 + colon esquerdo 2 = 3)', 3, 'bbps_labeled_seg'),
        ('[PREPARO] irregular (escala de boston: colon direito 1 + colon transverso 2 + colon esquerdo 2 = 5)', 5, 'bbps_labeled_seg'),
        # com typo "o" no nome completo
        ('[PREPARO] (escala de boston: colon direito o + colon transverso 1 + colon esquerdo 2 = 3)', 3, 'bbps_labeled_seg'),
    ]
    print('Smoke tests (bbps v4 sobre framework_core):')
    ok = 0
    for txt, exp_v, exp_s in cases:
        r = detect_bbps(txt)
        match = (r.value == exp_v and r.status == exp_s)
        marker = 'OK ' if match else 'FAIL'
        if match:
            ok += 1
        print(f'  [{marker}] expected=({exp_v},{exp_s}) got=({r.value},{r.status}) :: {txt[:60]}')
    print(f'\n{ok}/{len(cases)} passing')
