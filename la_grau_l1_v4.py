"""
L1 detector v4 — la_grau (Los Angeles classification of erosive esophagitis).

Baseado em vocabulary_la_grau.md (75 laudos lidos manualmente).

Filosofia v4-B:
- Não inferir, só localizar.
- Devolve evidência (span + offset + section + pattern_id), não só a letra.
- 'historical', 'ambiguous', 'anti_pattern_collision' são saídas legítimas.
- Indicação é descartada antes do parsing.
- Não compete com a IA: serve para circuit-breaker e cross-check, não substitui.

Refatorado em 2026-04-11 para consumir `framework_core`. Toda a infraestrutura
genérica (normalize, segment, evidence helpers, historicidade, anti-patterns
universais) vem do core e é compartilhada com bbps e demais detectores L1 v4.
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
    has_pattern_in_window,
    HISTORICITY_MARKERS,
    COMMON_SCALE_ANTI_PATTERNS,
)


# ---------- patterns lexicais específicos de la_grau ----------

# "los angeles" tolerante: cobre typos reais (ANGLES, ANGESLES, ÂNGELES)
PAT_LA_TERM = r'l[o]s?\s*[a]n+g[el]{1,4}s?'

# grau A/B/C/D
PAT_GRAU_LETRA = r'\bgrau\s*([abcd])\b'

# explicit: "grau A de los angeles" / "los angeles grau A"
PAT_LA_EXPLICIT = re.compile(
    rf'(?:{PAT_GRAU_LETRA}\s*(?:de|do|da|na|-|,|:|\(|\)|\s)*\s*{PAT_LA_TERM}'
    rf'|{PAT_LA_TERM}\s*[-,:]?\s*(?:grau\s*)?([abcd])\b)'
)

# Termos de esofagite erosiva (para LA_implicit)
ESOFAGITE_EROSIVA_TERMS = [
    'esofagite erosiva', 'esofagite de refluxo', 'esofagite por refluxo',
    'esofagite-de refluxo', 'esofagite refluxo', 'doenca do refluxo',
    'esofagite de refluxo erosiva', 'esofagite erosiva-grau',
]
PAT_ESOFAGITE_EROSIVA = re.compile(
    r'(' + '|'.join(re.escape(t) for t in ESOFAGITE_EROSIVA_TERMS) + r')'
)

PAT_ESOFAGITE_BROAD = re.compile(r'\besofagite\b')

# Negação de esofagite erosiva
NEG_ESOFAGITE_PATTERNS = [
    r'esofagite\s+nao[-\s]?erosiva',
    r'sem\s+esofagite\s+erosiva',
    r'sem\s+sinais\s+de\s+esofagite',
    r'ausencia\s+de\s+esofagite',
    r'ausencia\s+de\s+sinais\s+de\s+esofagite',
    r'nao\s+ha\s+sinais\s+de\s+esofagite',
    r'nao\s+evidenciado\s+esofagite',
    r'nao\s+evidencia\s+esofagite',
    r'esofagite\s+leve\s+nao\s+erosiva',
    r'sinais\s+(?:leves|discretos)\s+de\s+esofagite\s+nao\s+erosiva',
    r'esofago\s+(?:de\s+)?aspecto\s+normal',
    r'mucosa\s+esofagica\s+(?:de\s+aspecto\s+)?(?:normal|preservada|habitual)',
    r'esofago\s+sem\s+alteracoes',
    r'esofago\s+normal',
]
PAT_NEG_ESOFAGITE = re.compile('(' + '|'.join(NEG_ESOFAGITE_PATTERNS) + ')')

# Multi-letra (grau A/B, grau B-C)
PAT_MULTI = re.compile(r'\bgrau\s*([abcd])\s*[/-]\s*([abcd])\b')


# ---------- detecção ----------

@dataclass
class LAResult:
    """Output específico de la_grau (mantém `letter` como campo nomeado)."""
    letter: Optional[str]              # 'A'|'B'|'C'|'D'|'sem_esofagite'|None
    status: str                         # pattern_id
    section: Optional[str]
    span_start: int = -1
    span_end: int = -1
    evidence: str = ''


def _scan_section(content: str, section_name: str, base_offset: int) -> Optional[LAResult]:
    """Procura LA dentro de uma única seção. Devolve o primeiro hit válido."""
    # 1. Negação literal: 'sem_esofagite' (avaliada por último, mas pré-computada)
    m_neg = PAT_NEG_ESOFAGITE.search(content)

    # 2. Multi-letra ambígua
    m_multi = PAT_MULTI.search(content)
    if m_multi:
        if has_pattern_in_window(content, m_multi.start(), m_multi.end(), HISTORICITY_MARKERS):
            return LAResult(None, 'LA_historical', section_name,
                            base_offset + m_multi.start(), base_offset + m_multi.end(),
                            evidence_around(content, m_multi.start(), m_multi.end()))
        return LAResult(None, 'LA_ambiguous', section_name,
                        base_offset + m_multi.start(), base_offset + m_multi.end(),
                        evidence_around(content, m_multi.start(), m_multi.end()))

    # 3. LA explícito (com termo "los angeles")
    for m in PAT_LA_EXPLICIT.finditer(content):
        if has_pattern_in_window(content, m.start(), m.end(), COMMON_SCALE_ANTI_PATTERNS):
            continue
        if has_pattern_in_window(content, m.start(), m.end(), HISTORICITY_MARKERS):
            return LAResult(None, 'LA_historical', section_name,
                            base_offset + m.start(), base_offset + m.end(),
                            evidence_around(content, m.start(), m.end()))
        letter = (m.group(1) or m.group(2) or '').upper()
        if letter in {'A', 'B', 'C', 'D'}:
            return LAResult(letter, 'LA_explicit', section_name,
                            base_offset + m.start(), base_offset + m.end(),
                            evidence_around(content, m.start(), m.end()))

    # 4. LA implícito: "grau X" próximo a esofagite (com 'erosiva')
    # 4b. LA implícito bare: "esofagite grau X" sem "erosiva"/"refluxo" e sem "los angeles"
    #     Fundamentado no corpus E2 (128k laudos, 2026-04-15): dos 25 casos nessa forma,
    #     todos eram históricos — já cobertos por HISTORICITY_MARKERS. Esofagite
    #     eosinofílica/cáustica não usa graus A-D. Certainty='medium' via status distinto.
    bare_fallback = None
    if PAT_ESOFAGITE_BROAD.search(content):
        for m in re.finditer(PAT_GRAU_LETRA, content):
            if has_pattern_in_window(content, m.start(), m.end(), COMMON_SCALE_ANTI_PATTERNS):
                continue
            a = max(0, m.start() - 100)
            b = min(len(content), m.end() + 100)
            window = content[a:b]
            if not PAT_ESOFAGITE_BROAD.search(window):
                continue
            # historicidade tem precedência
            if has_pattern_in_window(content, m.start(), m.end(), HISTORICITY_MARKERS):
                return LAResult(None, 'LA_historical', section_name,
                                base_offset + m.start(), base_offset + m.end(),
                                evidence_around(content, m.start(), m.end()))
            # implícito com "erosiva" / "refluxo" → LA_implicit (alta confiança)
            if PAT_ESOFAGITE_EROSIVA.search(window):
                letter = m.group(1).upper()
                return LAResult(letter, 'LA_implicit', section_name,
                                base_offset + m.start(), base_offset + m.end(),
                                evidence_around(content, m.start(), m.end()))
            # implícito bare (sem "erosiva") → candidato fallback, cede para outras regras
            if bare_fallback is None:
                bare_fallback = LAResult(m.group(1).upper(), 'LA_implicit_bare', section_name,
                                         base_offset + m.start(), base_offset + m.end(),
                                         evidence_around(content, m.start(), m.end()))

    # 5. Negação só vale se nenhum positivo foi encontrado
    if m_neg:
        return LAResult('sem_esofagite', 'LA_negative', section_name,
                        base_offset + m_neg.start(), base_offset + m_neg.end(),
                        evidence_around(content, m_neg.start(), m_neg.end()))

    # 6. Bare fallback (esofagite grau X sem 'erosiva' e sem negação)
    if bare_fallback is not None:
        return bare_fallback

    return None


SECTION_PRIORITY = ['conclusao', 'esofago', 'descricao', 'corpo']  # indicacao NUNCA


def detect_la_grau(text: str) -> LAResult:
    norm = normalize(text)
    if not norm:
        return LAResult(None, 'empty', None)

    seg = segment(norm, ENDOSCOPY_SECTIONS)

    for sec_name in SECTION_PRIORITY:
        for block in seg.get(sec_name):
            r = _scan_section(block.content, sec_name, base_offset=block.start)
            if r is not None:
                return r

    return LAResult(None, 'no_match', None)


# ---------- sanity check / smoke test ----------

if __name__ == '__main__':
    samples = [
        ('Esofagite erosiva grau A de Los Angeles', 'A', 'LA_explicit'),
        ('GRAU B DE LOS ANGELES', 'B', 'LA_explicit'),
        ('esofagite (grau b de los angeles) hernia hiatal', 'B', 'LA_explicit'),
        ('esofagite erosiva grau A', 'A', 'LA_implicit'),
        ('ESOFAGITE EROSIVA-GRAU B', 'B', 'LA_implicit'),
        ('esofagite nao erosiva', 'sem_esofagite', 'LA_negative'),
        ('sem esofagite erosiva', 'sem_esofagite', 'LA_negative'),
        ('controle de esofagite grau B previa', None, 'LA_historical'),
        ('esofagite grau A/B', None, 'LA_ambiguous'),
        ('ulcera forrest IIa', None, 'no_match'),
        ('GRAU B DE LOS ANGLES', 'B', 'LA_explicit'),  # typo
        ('GRAU C DE LOS ANGESLES', 'C', 'LA_explicit'),  # typo
        ('CONCLUSAO: Esofagite erosiva grau A de Los Angeles', 'A', 'LA_explicit'),
        ('INDICACAO: controle de esofagite grau B. CONCLUSAO: esofago normal', 'sem_esofagite', 'LA_negative'),
    ]
    print('Smoke tests (la_grau v4 sobre framework_core):')
    ok = 0
    for text, exp_letter, exp_status in samples:
        r = detect_la_grau(text)
        match = (r.letter == exp_letter and r.status == exp_status)
        marker = 'OK ' if match else 'FAIL'
        if match:
            ok += 1
        print(f'  [{marker}] expected=({exp_letter},{exp_status}) got=({r.letter},{r.status}) :: {text[:60]}')
    print(f'\n{ok}/{len(samples)} passing')
