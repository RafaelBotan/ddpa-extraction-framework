"""
intervalo_rec_l1_v4 — L1 determinístico para intervalo_recomendado (Cat 7b + Cat 8a).

Piloto #6 do framework DDPA.
Categorias: Cat 7b (intervalo temporal) + Cat 8a (recomendação/procedimento).
Variável: intervalo_recomendado — intervalo de follow-up recomendado na conclusão.

Valores possíveis:
  - Específicos: "7_dias", "15_dias", "30_dias", "3_meses", "6_meses",
                  "1_ano", "2_anos", "3_anos", "5_anos", "10_anos"
  - Vagos: "precoce" (precocemente/oportunamente/intervalo menor),
            "posterior" (exame posterior, sem data),
            "imediato" (novo exame imediato/urgente)
  - Sem intervalo: "sem_intervalo" (recomenda repetir mas não diz quando)
  - Não recomenda: "nao_recomendado" (sem menção de repetir/controle)

Seções-fonte: [CONCLUSAO] + [OBS/NOTA] somente.
Regra: NUNCA extrair de [INDICAÇÃO] ou [DESCRIÇÃO].
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from framework_core import (
    normalize, segment, ENDOSCOPY_SECTIONS, SectionConfig,
    EvidenceRecord, evidence_around,
)


DETECTOR_VERSION = 'intervalo_rec_l1_v4@1.1.0'

_PATTERN_FAMILIES = (
    'rec_verbs',
    'interval_numeric',
    'interval_range',
    'interval_written',
    'interval_periodic',
    'interval_standalone',
    'qualifier_precoce',
    'qualifier_posterior',
    'qualifier_imediato',
)


def _build_scope_meta(
    sections_scanned, chars_scanned, pattern_families_run,
    hits_total, explicit_negative_hits=0, historical_hits=0,
    scope_complete=None,
):
    if scope_complete is None:
        scope_complete = chars_scanned > 0 and len(sections_scanned) > 0
    return {
        'detector_version': DETECTOR_VERSION,
        'scope_complete': bool(scope_complete),
        'sections_scanned': list(sections_scanned),
        'chars_scanned': int(chars_scanned),
        'pattern_families_run': list(pattern_families_run),
        'hits_total': int(hits_total),
        'explicit_negative_hits': int(explicit_negative_hits),
        'historical_hits': int(historical_hits),
    }


# ============================================================================
# Patterns
# ============================================================================

# Recommendation verbs — signals that a repeat is being suggested
PAT_REC_VERBS = re.compile(
    r'\b(?:'
    r'repeti[rcc]|reprogramar|programar\s+nov[oa]|novo\s+exame'
    r'|sugir[oa]?|sugere-?se|sugest[aa]o'
    r'|recomend[ao](?:-se)?|recomenda-se'
    r'|controle\s+(?:colonosc|endosc|em\s+\d)'
    r'|colonoscopia\s+de\s+controle'
    r'|realizar\s+nov[ao]'
    r'|nova\s+colonoscopia'
    r'|exame\s+(?:de\s+)?controle'
    r'|programar\s+(?:ressec\w*|polipectomia|mucosectomia|biopsia|exame|colonoscopia|endoscopia)'
    r'|revis[aa]o\s+(?:endosc|colonosc|em\s+\d)'
    r'|marcar\s+nov[oa]'
    r'|marcar\s+(?:exame|colonoscopia|endoscopia)'
    r'|dever[ae]?\s+repeti[rcc]'
    r'|oriento?\s+repeti[rcc]'
    r')\b',
    re.IGNORECASE
)

# Specific interval: "em N ano(s)/mes(es)/dia(s)/semana(s)"
# Also handles "dentro de N", "após N", "no prazo de N"
PAT_INTERVAL_NUMERIC = re.compile(
    r'(?:em|dentro\s+de|apos|no\s+prazo\s+de|daqui\s+a?|com)'
    r'(?:\s+(?:no\s+maximo|ate|no\s+minimo|aproximadamente))?\s+'
    r'(0?\d{1,2})\s*'
    r'(ano|anos|mes|meses|dia|dias|semana|semanas|h(?:oras?)?)\b',
    re.IGNORECASE
)

# Range interval: "3-4 semanas", "3 a 4 semanas" — take MAX
PAT_INTERVAL_RANGE = re.compile(
    r'(?:em|dentro\s+de|apos|com)'
    r'(?:\s+(?:no\s+maximo|ate|aproximadamente))?\s+'
    r'(\d{1,2})\s*[-a]\s*(\d{1,2})\s*'
    r'(ano|anos|mes|meses|dia|dias|semana|semanas)',
    re.IGNORECASE
)

# Periodic words: "anual", "semestral", "trimestral", "bimestral", "mensal", "semanal"
PERIODIC_WORDS = {
    'anual': ('1', 'ano'), 'anuais': ('1', 'ano'), 'anualmente': ('1', 'ano'),
    'semestral': ('6', 'meses'), 'semestralmente': ('6', 'meses'),
    'trimestral': ('3', 'meses'), 'trimestralmente': ('3', 'meses'),
    'bimestral': ('2', 'meses'), 'bimestralmente': ('2', 'meses'),
    'mensal': ('1', 'mes'), 'mensalmente': ('1', 'mes'),
    'semanal': ('1', 'semana'), 'semanalmente': ('1', 'semana'),
    'quinzenal': ('15', 'dias'), 'quinzenalmente': ('15', 'dias'),
}
PAT_PERIODIC = re.compile(
    r'\b(' + '|'.join(PERIODIC_WORDS.keys()) + r')\b',
    re.IGNORECASE
)

# Written-out numbers
WRITTEN_NUMBERS = {
    'um': '1', 'uma': '1', 'dois': '2', 'duas': '2', 'tres': '3',
    'quatro': '4', 'cinco': '5', 'seis': '6', 'sete': '7',
    'oito': '8', 'nove': '9', 'dez': '10',
}

PAT_INTERVAL_WRITTEN = re.compile(
    r'(?:em|dentro\s+de|apos|no\s+prazo\s+de|com)'
    r'(?:\s+(?:no\s+maximo|ate|no\s+minimo|aproximadamente))?\s+'
    r'(' + '|'.join(WRITTEN_NUMBERS.keys()) + r')\s+'
    r'(ano|anos|mes|meses|dia|dias|semana|semanas)',
    re.IGNORECASE
)

# Standalone interval without preposition (e.g., "1 ano" near rec verb)
PAT_INTERVAL_STANDALONE = re.compile(
    r'\b(0?\d{1,2})\s*(ano|anos|mes|meses|dia|dias|semana|semanas)\b',
    re.IGNORECASE
)

# Vague "precoce" patterns
PAT_PRECOCE = re.compile(
    r'\b(?:'
    r'precocemente|oportunamente'
    r'|intervalo\s+menor'
    r'|mais\s+precoce'
    r'|mais\s+cedo'
    r'|brevemente'
    r'|breve\s+prazo'
    r'|menor\s+(?:intervalo|tempo|prazo)'
    r'|curto\s+prazo'
    r')\b',
    re.IGNORECASE
)

# "Posterior" / undated
PAT_POSTERIOR = re.compile(
    r'\b(?:'
    r'exame\s+posterior'
    r'|momento\s+posterior'
    r'|posteriormente'
    r'|data\s+oportuna'
    r'|a\s+posterio?ri'
    r'|a\s+criterio\s+clinico'
    r')\b',
    re.IGNORECASE
)

# Immediate / urgent
PAT_IMEDIATO = re.compile(
    r'\b(?:'
    r'imediat[oa]?(?:mente)?'
    r'|imediat[oa]'
    r'|urgente?(?:mente)?'
    r'|urgencia'
    r'|o\s+mais\s+rapido'
    r'|o\s+mais\s+breve'
    r'|com\s+urgencia'
    r')\b',
    re.IGNORECASE
)

# OBS/NOTA section markers (appear after CONCLUSAO in many reports)
PAT_OBS_NOTA = re.compile(
    r'\b(?:obs|nota|observacao|observacoes)\s*[:\d]',
    re.IGNORECASE
)


# ============================================================================
# Normalization of interval to canonical form
# ============================================================================

def _normalize_interval(n: str, unit: str) -> str:
    """Convert (number, unit) to canonical interval string."""
    n_int = int(n)
    unit_lower = unit.lower()

    if unit_lower.startswith('ano'):
        if n_int == 1:
            return '1_ano'
        return f'{n_int}_anos'
    elif unit_lower.startswith('mes'):
        if n_int == 1:
            return '1_mes'
        return f'{n_int}_meses'
    elif unit_lower.startswith('dia'):
        if n_int == 1:
            return '1_dia'
        return f'{n_int}_dias'
    elif unit_lower.startswith('semana'):
        # Convert weeks to days
        days = n_int * 7
        return f'{days}_dias'
    elif unit_lower.startswith('h'):
        # Convert hours to days (round up)
        days = max(1, (n_int + 23) // 24)
        return f'{days}_dias'
    return f'{n_int}_{unit_lower}'


# ============================================================================
# Core detector
# ============================================================================

def detect_intervalo_recomendado(text_raw: str) -> EvidenceRecord:
    """
    Detect recommended follow-up interval in colonoscopy/EDA report.

    Strategy:
    1. Normalize and segment text
    2. Search CONCLUSAO + OBS/NOTA sections only
    3. Look for recommendation verbs first
    4. If found, look for specific interval near the verb
    5. If no specific interval, classify as precoce/posterior/imediato/sem_intervalo
    6. If no recommendation verb, return nao_recomendado
    """
    text_norm = normalize(text_raw)

    if not text_norm or len(text_norm) < 20:
        # Silêncio por texto insuficiente: scope incompleto.
        return EvidenceRecord(
            value=None, status='no_match',
            evidence=text_norm[:100], certainty='low',
            evidence_class='silence_default',
            scope_meta=_build_scope_meta(
                sections_scanned=[], chars_scanned=len(text_norm or ''),
                pattern_families_run=[], hits_total=0,
                scope_complete=False,
            ),
        )

    # Segment text
    seg = segment(text_norm, ENDOSCOPY_SECTIONS)

    # Build search text from CONCLUSAO + full text after conclusao (captures OBS/NOTA)
    search_blocks = []

    # Primary: conclusao
    for block in seg.get('conclusao'):
        search_blocks.append(block)

    # Also search in text AFTER conclusao (OBS/NOTA often come after)
    # Find last conclusao position
    conclusao_blocks = seg.get('conclusao')
    if conclusao_blocks:
        last_concl_end = max(b.end for b in conclusao_blocks)
        # Everything after conclusao is potential OBS/NOTA
        remaining = text_norm[last_concl_end:]
        if remaining.strip():
            from framework_core import Block
            search_blocks.append(Block('pos_conclusao', last_concl_end, len(text_norm), remaining))

    # Fallback: if no conclusao, search full text
    if not search_blocks:
        from framework_core import Block
        search_blocks.append(Block('corpo', 0, len(text_norm), text_norm))

    # Combine all search text
    search_text = ' '.join(b.content for b in search_blocks)
    search_offset = search_blocks[0].start if search_blocks else 0
    sections_scanned = [b.section for b in search_blocks]
    chars_scanned = len(search_text)

    # Step 1: Find recommendation verbs
    rec_matches = list(PAT_REC_VERBS.finditer(search_text))

    if not rec_matches:
        # No recommendation verb — check fallback patterns.
        interval_match = PAT_INTERVAL_NUMERIC.search(search_text)
        if interval_match:
            n, unit = interval_match.group(1), interval_match.group(2)
            value = _normalize_interval(n, unit)
            return EvidenceRecord(
                value=value, status='interval_standalone',
                section='conclusao',
                span_start=search_offset + interval_match.start(),
                span_end=search_offset + interval_match.end(),
                evidence=evidence_around(text_norm, search_offset + interval_match.start(),
                                        search_offset + interval_match.end(), 60),
                certainty='medium',
                evidence_class='literal',
                scope_meta=_build_scope_meta(
                    sections_scanned=sections_scanned, chars_scanned=chars_scanned,
                    pattern_families_run=['rec_verbs', 'interval_numeric'],
                    hits_total=1, scope_complete=True,
                ),
            )

        written_match = PAT_INTERVAL_WRITTEN.search(search_text)
        if written_match:
            n = WRITTEN_NUMBERS.get(written_match.group(1).lower(), '1')
            unit = written_match.group(2)
            value = _normalize_interval(n, unit)
            return EvidenceRecord(
                value=value, status='interval_written',
                section='conclusao',
                span_start=search_offset + written_match.start(),
                span_end=search_offset + written_match.end(),
                evidence=evidence_around(text_norm, search_offset + written_match.start(),
                                        search_offset + written_match.end(), 60),
                certainty='medium',
                evidence_class='literal',
                scope_meta=_build_scope_meta(
                    sections_scanned=sections_scanned, chars_scanned=chars_scanned,
                    pattern_families_run=['rec_verbs', 'interval_numeric', 'interval_written'],
                    hits_total=1, scope_complete=True,
                ),
            )

        # NOTE: periodic words (anual, semestral) and range intervals NOT searched standalone
        # — too many false positives (e.g. "infliximabe mensal"). Only near rec verb.

        # Silêncio auditado: escaneamos todo o scope e não achamos verbo/interval.
        # value=None + status=no_match → policy resolve via default_if_silent.
        return EvidenceRecord(
            value=None, status='no_match',
            evidence=search_text[:150], certainty='high',
            evidence_class='silence_default',
            scope_meta=_build_scope_meta(
                sections_scanned=sections_scanned, chars_scanned=chars_scanned,
                pattern_families_run=['rec_verbs', 'interval_numeric', 'interval_written'],
                hits_total=0, scope_complete=True,
            ),
        )

    # Step 2: We have recommendation — now look for specific interval
    # Search within 200 chars after each rec verb
    best_interval = None
    best_rec_match = None
    step2_families = ['rec_verbs', 'interval_numeric', 'interval_range',
                      'interval_written', 'interval_periodic', 'interval_standalone']

    def _interval_scope():
        return _build_scope_meta(
            sections_scanned=sections_scanned, chars_scanned=chars_scanned,
            pattern_families_run=step2_families,
            hits_total=len(rec_matches), scope_complete=True,
        )

    for rec_m in rec_matches:
        window_start = rec_m.start()
        window_end = min(len(search_text), rec_m.end() + 200)
        window = search_text[window_start:window_end]

        # Try numeric interval
        int_m = PAT_INTERVAL_NUMERIC.search(window)
        if int_m:
            n, unit = int_m.group(1), int_m.group(2)
            value = _normalize_interval(n, unit)
            abs_start = search_offset + window_start + int_m.start()
            abs_end = search_offset + window_start + int_m.end()
            best_interval = EvidenceRecord(
                value=value, status='interval_with_rec',
                section='conclusao',
                span_start=abs_start, span_end=abs_end,
                evidence=evidence_around(text_norm, abs_start, abs_end, 60),
                certainty='high',
                evidence_class='literal',
                scope_meta=_interval_scope(),
            )
            break

        # Try range interval ("3-4 semanas", "3 a 4 meses")
        rng_m = PAT_INTERVAL_RANGE.search(window)
        if rng_m:
            n1, n2, unit = int(rng_m.group(1)), int(rng_m.group(2)), rng_m.group(3)
            n_max = str(max(n1, n2))
            value = _normalize_interval(n_max, unit)
            abs_start = search_offset + window_start + rng_m.start()
            abs_end = search_offset + window_start + rng_m.end()
            best_interval = EvidenceRecord(
                value=value, status='interval_range_with_rec',
                section='conclusao',
                span_start=abs_start, span_end=abs_end,
                evidence=evidence_around(text_norm, abs_start, abs_end, 60),
                certainty='high',
                evidence_class='bound',
                scope_meta=_interval_scope(),
            )
            break

        # Try written number interval
        wrt_m = PAT_INTERVAL_WRITTEN.search(window)
        if wrt_m:
            n = WRITTEN_NUMBERS.get(wrt_m.group(1).lower(), '1')
            unit = wrt_m.group(2)
            value = _normalize_interval(n, unit)
            abs_start = search_offset + window_start + wrt_m.start()
            abs_end = search_offset + window_start + wrt_m.end()
            best_interval = EvidenceRecord(
                value=value, status='interval_written_with_rec',
                section='conclusao',
                span_start=abs_start, span_end=abs_end,
                evidence=evidence_around(text_norm, abs_start, abs_end, 60),
                certainty='high',
                evidence_class='lexical_alias',
                scope_meta=_interval_scope(),
            )
            break

        # Try periodic words ("anual", "semestral", etc.)
        per_m = PAT_PERIODIC.search(window)
        if per_m:
            word = per_m.group(1).lower()
            n, unit = PERIODIC_WORDS[word]
            value = _normalize_interval(n, unit)
            abs_start = search_offset + window_start + per_m.start()
            abs_end = search_offset + window_start + per_m.end()
            best_interval = EvidenceRecord(
                value=value, status='interval_periodic_with_rec',
                section='conclusao',
                span_start=abs_start, span_end=abs_end,
                evidence=evidence_around(text_norm, abs_start, abs_end, 60),
                certainty='high',
                evidence_class='ontology_alias',
                scope_meta=_interval_scope(),
            )
            break

        # Try standalone interval (just "1 ano" near rec verb)
        std_m = PAT_INTERVAL_STANDALONE.search(window)
        if std_m and not best_interval:
            n, unit = std_m.group(1), std_m.group(2)
            # Avoid matching things like "Paris 0-IIa" or scores
            try:
                n_int = int(n)
                if n_int > 0 and n_int <= 20:
                    value = _normalize_interval(n, unit)
                    abs_start = search_offset + window_start + std_m.start()
                    abs_end = search_offset + window_start + std_m.end()
                    best_interval = EvidenceRecord(
                        value=value, status='interval_standalone_near_rec',
                        section='conclusao',
                        span_start=abs_start, span_end=abs_end,
                        evidence=evidence_around(text_norm, abs_start, abs_end, 60),
                        certainty='medium',
                        evidence_class='literal',
                        scope_meta=_interval_scope(),
                    )
            except ValueError:
                pass

        if not best_rec_match:
            best_rec_match = rec_m

    if best_interval:
        return best_interval

    # Step 3: Recommendation found but no specific interval — classify vague type
    rec_m = best_rec_match or rec_matches[0]
    rec_context = search_text[rec_m.start():min(len(search_text), rec_m.end() + 150)]
    abs_start = search_offset + rec_m.start()
    abs_end = search_offset + rec_m.end()

    step3_families = step2_families + ['qualifier_imediato', 'qualifier_precoce', 'qualifier_posterior']

    def _qualifier_scope():
        return _build_scope_meta(
            sections_scanned=sections_scanned, chars_scanned=chars_scanned,
            pattern_families_run=step3_families,
            hits_total=len(rec_matches), scope_complete=True,
        )

    # Check for "imediato"
    if PAT_IMEDIATO.search(rec_context) or PAT_IMEDIATO.search(search_text):
        return EvidenceRecord(
            value='imediato', status='rec_imediato',
            section='conclusao',
            span_start=abs_start, span_end=abs_end,
            evidence=evidence_around(text_norm, abs_start, abs_end, 80),
            certainty='medium',
            evidence_class='literal',
            scope_meta=_qualifier_scope(),
        )

    # Check for "precoce"
    if PAT_PRECOCE.search(rec_context) or PAT_PRECOCE.search(search_text):
        return EvidenceRecord(
            value='precoce', status='rec_precoce',
            section='conclusao',
            span_start=abs_start, span_end=abs_end,
            evidence=evidence_around(text_norm, abs_start, abs_end, 80),
            certainty='medium',
            evidence_class='lexical_alias',
            scope_meta=_qualifier_scope(),
        )

    # Check for "posterior"
    if PAT_POSTERIOR.search(rec_context) or PAT_POSTERIOR.search(search_text):
        return EvidenceRecord(
            value='posterior', status='rec_posterior',
            section='conclusao',
            span_start=abs_start, span_end=abs_end,
            evidence=evidence_around(text_norm, abs_start, abs_end, 80),
            certainty='medium',
            evidence_class='lexical_alias',
            scope_meta=_qualifier_scope(),
        )

    # Has recommendation but no interval qualifier
    return EvidenceRecord(
        value='sem_intervalo', status='rec_no_interval',
        section='conclusao',
        span_start=abs_start, span_end=abs_end,
        evidence=evidence_around(text_norm, abs_start, abs_end, 80),
        certainty='medium',
        evidence_class='qualitative',
        scope_meta=_qualifier_scope(),
    )


# ============================================================================
# Smoke tests
# ============================================================================

SMOKE_TESTS = [
    # --- Specific intervals ---
    # 1. "em 1 ano"
    ('[CONCLUSAO] POLIPOS COLONICOS. OBS: Sugiro novo exame em 1 ano.',
     '1_ano', 'interval'),
    # 2. "dentro de 1 ano"
    ('[CONCLUSAO] EXAME NORMAL. Recomenda-se repetir dentro de 1 ano.',
     '1_ano', 'interval'),
    # 3. "em 6 meses"
    ('[CONCLUSAO] POLIPO RETAL. Sugere-se controle em 6 meses.',
     '6_meses', 'interval'),
    # 4. "em 3 anos"
    ('[CONCLUSAO] POLIPO COLONICO. Polipectomia. Repetir em 3 anos.',
     '3_anos', 'interval'),
    # 5. "em 5 anos"
    ('[CONCLUSAO] EXAME NORMAL. Controle em 5 anos.',
     '5_anos', 'interval'),
    # 6. "em 30 dias"
    ('[CONCLUSAO] PREPARO INADEQUADO. Programar novo exame em 30 dias.',
     '30_dias', 'interval'),
    # 7. "em 3 meses"
    ('[CONCLUSAO] REALIZAR NOVA COLONOSCOPIA COM PREPARO OTIMIZADO EM 3 MESES.',
     '3_meses', 'interval'),
    # 8. "em 15 dias"
    ('[CONCLUSAO] POLIPECTOMIA. Controle em 15 dias.',
     '15_dias', 'interval'),
    # 9. "em 2 anos"
    ('[CONCLUSAO] DIVERTICULOSE. Sugere-se novo exame dentro de 2 anos.',
     '2_anos', 'interval'),
    # 10. "em 10 anos"
    ('[CONCLUSAO] COLONOSCOPIA NORMAL. Repetir em 10 anos.',
     '10_anos', 'interval'),
    # 11. Written number "um ano"
    ('[CONCLUSAO] EXAME NORMAL. Repetir em um ano com preparo adequado.',
     '1_ano', 'interval'),
    # 12. "em 2 semanas" -> 14 dias
    ('[CONCLUSAO] BIOPSIA REALIZADA. Sugiro controle em 2 semanas.',
     '14_dias', 'interval'),

    # --- Vague / qualitative intervals ---
    # 13. "precocemente"
    ('[CONCLUSAO] PREPARO RUIM. Recomenda-se repetir precocemente.',
     'precoce', 'rec_precoce'),
    # 14. "oportunamente"
    ('[CONCLUSAO] PREPARO INADEQUADO. Sugere-se repetir oportunamente.',
     'precoce', 'rec_precoce'),
    # 15. "intervalo menor"
    ('[CONCLUSAO] POLIPOS. Sugiro intervalo menor que o preconizado.',
     'precoce', 'rec_precoce'),
    # 16. "exame posterior"
    ('[CONCLUSAO] LST NAO RESSECADA. Programar resseccao em exame posterior.',
     'posterior', 'rec_posterior'),
    # 17. "a criterio clinico"
    ('[CONCLUSAO] PREPARO RUIM. Repetir a criterio clinico.',
     'posterior', 'rec_posterior'),
    # 18. "imediato"
    ('[CONCLUSAO] SANGRAMENTO ATIVO. PROGRAMAR NOVO EXAME IMEDIATO.',
     'imediato', 'rec_imediato'),

    # --- Has recommendation but no interval ---
    # 19. "repetir em melhores condições"
    ('[CONCLUSAO] PREPARO INADEQUADO. PROGRAMAR NOVO EXAME EM MELHORES CONDICOES DE PREPARO.',
     'sem_intervalo', 'rec_no_interval'),
    # 20. "novo exame para polipectomia"
    ('[CONCLUSAO] POLIPO COLICO. PROGRAMAR NOVO EXAME PARA POLIPECTOMIA.',
     'sem_intervalo', 'rec_no_interval'),
    # 21. "repetir com melhor preparo"
    ('[CONCLUSAO] EXAME INCOMPLETO POR PREPARO RUIM. Repetir com melhor preparo.',
     'sem_intervalo', 'rec_no_interval'),

    # --- No recommendation (silêncio auditado; policy resolve via default_if_silent) ---
    # 22. Normal exam, no mention of repeat
    ('[CONCLUSAO] ILEOCOLONOSCOPIA NORMAL.',
     None, 'no_match'),
    # 23. Findings but no recommendation
    ('[CONCLUSAO] DOENCA DIVERTICULAR DO COLON ESQUERDO SEM COMPLICACAO.',
     None, 'no_match'),
    # 24. Polyp with polypectomy, no follow-up mentioned
    ('[CONCLUSAO] POLIPO SESSIL EM SIGMOIDE - POLIPECTOMIA.',
     None, 'no_match'),

    # --- OBS/NOTA section ---
    # 25. Interval in OBS after conclusao
    ('[CONCLUSAO] POLIPOS COLONICOS. obs: sugiro novo exame em 1 ano.',
     '1_ano', 'interval'),
    # 26. Nota section
    ('[CONCLUSAO] EXAME NORMAL. nota: recomenda-se repetir em 6 meses devido preparo.',
     '6_meses', 'interval'),

    # --- Edge cases ---
    # 27. "Melhores condições" should NOT extract interval from "em melhores"
    ('[CONCLUSAO] REPETIR EXAME EM MELHORES CONDICOES DE PREPARO PARA AVALIACAO COMPLETA.',
     'sem_intervalo', 'rec_no_interval'),
    # 28. Multiple recommendations — use first specific interval
    ('[CONCLUSAO] POLIPO COLONICO - POLIPECTOMIA. Sugiro controle em 1 ano. '
     'PREPARO INADEQUADO - repetir em 6 meses.',
     '1_ano', 'interval'),
    # 29. "em no máximo um ano"
    ('[CONCLUSAO] PREPARO INADEQUADO. Sugiro repetir estudo em no maximo um ano.',
     '1_ano', 'interval'),
    # 30. "em até um ano"
    ('[CONCLUSAO] POLIPOS COLONICOS. Sugere-se controle em ate um ano.',
     '1_ano', 'interval'),
    # 31. "em no máximo 6 meses"
    ('[CONCLUSAO] ESOFAGITE. Recomenda-se repetir em no maximo 6 meses.',
     '6_meses', 'interval'),
    # 32. "em aproximadamente 3 meses"
    ('[CONCLUSAO] POLIPO RETAL. Sugiro novo exame em aproximadamente 3 meses.',
     '3_meses', 'interval'),
    # 33. "controle com seis meses" (preposition "com")
    ('[CONCLUSAO] TUMORACAO EXTRA MUCOSA EM CECO. Sugiro controle colonoscopico com seis meses.',
     '6_meses', 'interval'),
    # 34. "controle anual" (periodic word)
    ('[CONCLUSAO] POLIPOS COLONICOS. OBS: sugere-se controle anual.',
     '1_ano', 'interval'),
    # 35. "em 48h" (hours)
    ('[CONCLUSAO] ESTENOSE ANASTOMOSE. Realizar nova sessao em 48h com preparo adequado.',
     '2_dias', 'interval'),
    # 36. "3-4 semanas" (range → max)
    ('[CONCLUSAO] HEMOSTASIA. Sugiro nova avaliacao em 3-4 semanas.',
     '28_dias', 'interval'),
    # 37. "semestralmente"
    ('[CONCLUSAO] POLIPOS COLONICOS. Recomenda-se controle semestral.',
     '6_meses', 'interval'),
    # 38. "em 72 horas"
    ('[CONCLUSAO] SANGRAMENTO. Programar novo exame em 72 horas.',
     '3_dias', 'interval'),
    # 39. "controle com 3 meses" (preposition "com" + numeric)
    ('[CONCLUSAO] POLIPECTOMIA. Sugiro controle com 3 meses.',
     '3_meses', 'interval'),
]


def run_smoke_tests():
    """Run all smoke tests and report results."""
    passed = 0
    failed = 0
    for i, (text, expected_value, expected_status_prefix) in enumerate(SMOKE_TESTS, 1):
        result = detect_intervalo_recomendado(text)
        value_ok = result.value == expected_value
        status_ok = result.status.startswith(expected_status_prefix) if expected_status_prefix else True

        if value_ok and status_ok:
            passed += 1
        else:
            failed += 1
            print(f'FAIL #{i}: expected={expected_value}/{expected_status_prefix}, '
                  f'got={result.value}/{result.status}')
            print(f'  evidence: {result.evidence[:120]}')
            print()

    print(f'\n{passed}/{passed+failed} smoke tests passed.')
    return failed == 0


if __name__ == '__main__':
    run_smoke_tests()
