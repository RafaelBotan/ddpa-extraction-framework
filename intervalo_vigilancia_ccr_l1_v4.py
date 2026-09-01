"""
intervalo_vigilancia_ccr_l1_v4 — Target derivado para vigilância CCR.

Wraps `intervalo_rec_l1_v4.detect_intervalo_recomendado` + exclusão
negativa de indicações NÃO-vigilância na janela da evidência.

Motivação (DDPA double-check 2026-04-14, GPT):
    `followup_interval_any` (raw) mistura 4 intents:
      - Vigilância CCR (target E1): "controle em 3 anos", "rastreamento em 5 anos"
      - Terapêutico: "nova sessao APC em 4 semanas", "plasma argonio"
      - Remake: "mais precocemente devido a preparo ruim"
      - Diagnóstico: "complementação com colonoscopia virtual"

    Só vigilância CCR é o target clínico. Este detector é EXCLUSÃO NEGATIVA
    de não-vigilância: chama o raw, e se a janela de evidência contiver
    marcadores terapêuticos/remake/diagnósticos, retorna None com status
    `excluded_*`. Caso contrário passa o raw adiante.

    Não é classificador positivo de vigilância — é a versão barata do
    split. Se a qualidade não bastar, trocar por indicacao_categoria_l1.

Status novos (além do raw):
    excluded_therapeutic_session — APC, mucosectomia, nova sessão, dilatação
    excluded_bad_prep_remake — repetir por preparo ruim
    excluded_diagnostic_completion — complementação / prosseguir investigação
"""
from __future__ import annotations
import re
from dataclasses import replace

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from framework_core import normalize, EvidenceRecord
from intervalo_rec_l1_v4 import detect_intervalo_recomendado


DETECTOR_VERSION = 'intervalo_vigilancia_ccr_l1_v4@1.0.0'
WINDOW_CHARS = 200


# ============================================================================
# Patterns — exclusão por intent
# ============================================================================

# Terapêutico: sessão futura de ablação/ressecção/tratamento.
PAT_THERAPEUTIC = re.compile(
    r'\b(?:'
    r'plasma\s+(?:de\s+)?argonio'
    r'|argonio\b'
    r'|\bapc\b'
    r'|eletrocoagulac[aa]o'
    r'|mucosectomia'
    r'|dissec[cc][aa]o'
    r'|ligadura\s+elast'
    r'|bandagem'
    r'|hemostasia'
    r'|nova\s+sess[aa]o'
    r'|sess[aa]o\s+de\s+(?:apc|argonio|eletrocoag|mucosectomia|ligadura|dilata)'
    r'|tratamento\s+endoscopic'
    r'|dilatac[aa]o'
    r'|escleroterapia'
    r'|injec[cc][aa]o\s+(?:de\s+)?(?:adrenalina|etanol)'
    r'|cauterizac[aa]o'
    r')\b',
    re.IGNORECASE
)

# Remake: repetir por preparo ruim / exame incompleto (não é vigilância).
# Exige co-ocorrência de sinal de má-qualidade + recomendação de repetir.
PAT_BAD_PREP = re.compile(
    r'\b(?:'
    r'preparo\s+(?:ruim|inadequado|deficiente|regular|ma\s+qualidade|insatisfatorio)'
    r'|ma\s+qualidade\s+do\s+preparo'
    r'|melhor\s+preparo'
    r'|preparo\s+adequado'
    r'|condic[oo]es\s+(?:regulares|ruins|inadequadas)\s+do?\s+preparo'
    r'|intervalo\s+(?:menor|reduzido)\s+que\s+o\s+preconizad'
    r'|intervalo\s+(?:menor|reduzido)'
    r'|mais\s+precocemente'
    r'|oportunamente\s+com\s+melhor'
    r'|com\s+melhor\s+preparo'
    r')\b',
    re.IGNORECASE
)

PAT_INCOMPLETE_EXAM = re.compile(
    r'\b(?:'
    r'exame\s+incompleto'
    r'|nao\s+atingi[dr]\s+(?:o\s+)?ceco'
    r'|ileo\s+terminal\s+nao\s+(?:atingi|alcanca)'
    r'|colonoscopia\s+incompleta'
    r'|nao\s+foi\s+possivel\s+(?:completar|atingir)'
    r')\b',
    re.IGNORECASE
)

# Diagnóstico/complementar: não é vigilância CCR.
PAT_DIAGNOSTIC = re.compile(
    r'\b(?:'
    r'complementac[aa]o'
    r'|complementar\s+(?:com|a)'
    r'|prosseguir\s+(?:a\s+)?investigac[aa]o'
    r'|colonoscopia\s+virtual'
    r'|colono\s+virtual'
    r'|tomografia\s+(?:computadoriza|de\s+)'
    r'|capsula\s+endoscopica'
    r'|entero(?:ressonancia|tomografia|scopia)'
    r'|investigac[aa]o\s+do\s+(?:intestino\s+)?delgado'
    r'|investigac[aa]o\s+complementar'
    r')\b',
    re.IGNORECASE
)


# ============================================================================
# Core detector
# ============================================================================

def detect_intervalo_vigilancia_ccr(text_raw: str) -> EvidenceRecord:
    """
    Target: intervalo de vigilância CCR.

    Algoritmo:
      1. Chama raw detector (`detect_intervalo_recomendado`)
      2. Se raw.value é None ou status in {no_match} → propaga (silêncio)
      3. Se raw.span_* disponível → janela ±WINDOW_CHARS em torno do hit
         Caso contrário → scan texto inteiro normalizado (fallback)
      4. Matches de PAT_THERAPEUTIC → excluded_therapeutic_session
      5. Matches de PAT_INCOMPLETE_EXAM ou PAT_BAD_PREP → excluded_bad_prep_remake
      6. Matches de PAT_DIAGNOSTIC → excluded_diagnostic_completion
      7. Senão → propaga raw com meta.intent='vigilancia'

    Preserva certainty, evidence_class, scope_meta do raw quando passa adiante.
    """
    raw = detect_intervalo_recomendado(text_raw)

    # Silêncio / sem valor: propaga sem tocar
    if raw.value is None:
        meta = dict(raw.meta or {})
        meta['target_detector'] = DETECTOR_VERSION
        meta['target_intent'] = 'propagated_silence'
        return replace(raw, meta=meta)

    text_norm = normalize(text_raw)
    if raw.span_start is not None and raw.span_start >= 0 and raw.span_end and raw.span_end > raw.span_start:
        ws = max(0, raw.span_start - WINDOW_CHARS)
        we = min(len(text_norm), raw.span_end + WINDOW_CHARS)
        window = text_norm[ws:we]
    else:
        window = text_norm

    excluded_status = None
    excluded_match = None

    # Ordem de precedência: mais específico → menos específico.
    # PAT_DIAGNOSTIC primeiro: "complementação com colono virtual" é sinal
    # diagnóstico forte mesmo quando co-ocorre com "exame incompleto".
    m = PAT_THERAPEUTIC.search(window)
    if m:
        excluded_status = 'excluded_therapeutic_session'
        excluded_match = m.group(0)
    else:
        m = PAT_DIAGNOSTIC.search(window)
        if m:
            excluded_status = 'excluded_diagnostic_completion'
            excluded_match = m.group(0)
        else:
            m = PAT_INCOMPLETE_EXAM.search(window) or PAT_BAD_PREP.search(window)
            if m:
                excluded_status = 'excluded_bad_prep_remake'
                excluded_match = m.group(0)

    meta = dict(raw.meta or {})
    meta['target_detector'] = DETECTOR_VERSION
    meta['raw_value'] = raw.value
    meta['raw_status'] = raw.status

    if excluded_status:
        meta['target_intent'] = 'excluded'
        meta['exclusion_match'] = excluded_match
        return EvidenceRecord(
            value=None,
            status=excluded_status,
            section=raw.section,
            span_start=raw.span_start,
            span_end=raw.span_end,
            evidence=raw.evidence,
            certainty='high',
            meta=meta,
            scope_meta=raw.scope_meta,
            evidence_class='policy_exclusion',
        )

    meta['target_intent'] = 'vigilancia'
    return replace(raw, meta=meta)


if __name__ == '__main__':
    cases = [
        ('vigilance — controle 3 anos',
         'CONCLUSAO: exame normal. recomenda-se novo controle colonoscopico em 3 anos.'),
        ('therapeutic — APC',
         'CONCLUSAO: angiodisplasias no ceco. sugere-se nova sessao de argonio em 4 semanas.'),
        ('bad prep remake',
         'CONCLUSAO: preparo ruim. sugere-se repetir exame mais precocemente com melhor preparo.'),
        ('diagnostic completion',
         'CONCLUSAO: exame incompleto. sugere-se complementacao com colonoscopia virtual.'),
        ('silence',
         'CONCLUSAO: exame normal.'),
    ]
    for name, txt in cases:
        r = detect_intervalo_vigilancia_ccr(txt)
        print(f'[{name}]')
        print(f'  value={r.value!r}  status={r.status!r}  intent={(r.meta or {}).get("target_intent")!r}')
        print()
