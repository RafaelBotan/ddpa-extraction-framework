#!/usr/bin/env python3
"""
L1 Deterministic Detector — Mama IHQ (Immunohistochemistry)
Framework v4 / DDPA cross-domain pilot #2

Variables covered (10 extracted + 2 derived = 12 total):
  Extracted:
  - re_status       (BIN)     — estrogen receptor: positivo/negativo
  - re_pct          (NUM)     — estrogen receptor percentage (0-100)
  - re_allred       (NUM)     — estrogen receptor Allred score (0-8, L1-only)
  - rp_status       (BIN)     — progesterone receptor: positivo/negativo
  - rp_pct          (NUM)     — progesterone receptor percentage (0-100)
  - rp_allred       (NUM)     — progesterone receptor Allred score (0-8, L1-only)
  - her2_score      (CAT-ORD) — HER2 IHC score: 0/0+/1+/2+/3+
  - her2_status     (CAT)     — HER2 interpretation: positivo/negativo/equivoco
  - ki67_pct        (NUM)     — Ki-67 proliferation index (percentage)
  - tipo_hist_ihq   (CAT)     — histological type mentioned in IHQ conclusion
  Derived:
  - her2_status     (CAT)     — derived from her2_score when not stated
  - subtipo_molecular (CAT)   — molecular subtype: luminal_a/luminal_b_her2neg/
                                 luminal_b_her2pos/her2_enriched/triplo_neg

Design principles (inherited from mama AP playbook v3.4):
  1. Check NEGATION before positive
  2. Evidence-first: return matched text span
  3. Conservative witness: if unsure, return None
  4. Dual-format: antibody TABLE + CONCLUSION narrative
  5. Table values take priority over conclusion (more specific)

Two report formats:
  Format A (tabular): "RE (6F11): Positivo" / "HER2 / c-erb B-2 (SP3): Escore 3+"
  Format B (conclusion): "RECEPTOR DE ESTROGENO-POSITIVO" / "ESCORE 0 PARA HER2"
"""

import re
import csv
import json
import sys
import os
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Normalization (shared with mama AP)
# ---------------------------------------------------------------------------
ACCENT_MAP = str.maketrans(
    "áàãâéèêíìîóòõôúùûçÁÀÃÂÉÈÊÍÌÎÓÒÕÔÚÙÛÇ",
    "aaaaeeeiiioooouuucAAAAEEEIIIOOOOUUUC"
)

def normalize(text: str) -> str:
    text = text.lower().translate(ACCENT_MAP)
    text = text.replace('\u00b4', '').replace('\u00b8', '').replace('\u02c6', '')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# ===========================================================================
# INVASIVE COMPONENT PRIORITY — shared by RE/RP detectors
# ===========================================================================

def _find_invasive_component_status(text_norm: str, marker_re: str) -> Tuple[Optional[str], Optional[str]]:
    """When a table line differentiates invasive vs in-situ components,
    extract the value for the INVASIVE component.

    Patterns:
    - "RP (16): Negativo no componente invasivo e Positivo no componente in situ"
      → negativo
    - "RE (6F11): Positivo em 90% (negativo no componente intraductal)"
      → positivo (the first value = invasive; parenthetical = in situ note)
    - "RE (6F11): Negativo (no componente microinvasivo) Positivo em 10% (do CLIS)"
      → negativo (microinvasivo)

    Returns (positivo/negativo/None, evidence) or (None, None) if no component
    differentiation found.
    """
    # Pattern: "Marker: Negativo/Positivo no componente invasivo/microinvasivo"
    m = re.search(marker_re + r':\s*(positivo|negativo)\s+(?:no\s+)?componente\s+(?:invasivo|microinvasiv)', text_norm)
    if m:
        return m.group(1), m.group(0).strip()[:80]

    # Pattern: "Marker: Negativo (no componente microinvasivo) Positivo..."
    m = re.search(marker_re + r':\s*(positivo|negativo)\s+\((?:no\s+)?componente\s+(?:invasivo|microinvasiv)', text_norm)
    if m:
        return m.group(1), m.group(0).strip()[:80]

    return None, None


def _find_invasive_component_pct(text_norm: str, marker_re: str) -> Tuple[Optional[int], Optional[str]]:
    """Extract percentage for the INVASIVE component when both are present.

    Patterns:
    - "Negativo no componente invasivo e Positivo no in situ em 70%" → 0 (invasive = negativo)
    - "Positivo em 90% (negativo no componente intraductal)" → 90 (first value = invasive)

    Returns (int/None, evidence) or (None, None) if no differentiation.
    """
    # "Negativo no componente invasivo" → 0%
    m = re.search(marker_re + r':\s*negativo\s+(?:no\s+)?componente\s+(?:invasivo|microinvasiv)', text_norm)
    if m:
        return 0, m.group(0).strip()[:80]

    # "Positivo em X% ... no componente invasivo"
    m = re.search(marker_re + r':\s*positivo\s+em\s+(\d+)\s*%.{0,40}componente\s+(?:invasivo|microinvasiv)', text_norm)
    if m:
        return int(m.group(1)), m.group(0).strip()[:80]

    return None, None


# ===========================================================================
# RE_STATUS — Estrogen receptor
# ===========================================================================

def detect_re_status(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect estrogen receptor status. Returns (positivo/negativo/None, evidence).

    Two sources (table takes priority):
    - Table: "RE (6F11): Positivo/Negativo"
    - Conclusion: "RECEPTOR DE ESTROGENO-POSITIVO/NEGATIVO"

    POLICY: When invasive and in-situ components are differentiated,
    extract the INVASIVE component only.
    """
    # INVASIVE COMPONENT PRIORITY: check first
    inv_val, inv_ev = _find_invasive_component_status(text_norm, r're\s+\([^)]+\)\s*')
    if inv_val is not None:
        return inv_val, inv_ev

    # Table format: "RE (clone): [fraco/forte] Positivo/Negativo [in X% ...]"
    # Multi-word clones: (pgr 636), (pgr-636), (sp1 sp2)
    m = re.search(r're\s+\([^)]+\)\s*:\s*(?:fraco\s+|forte\s+)?(positivo|negativo)', text_norm)
    if m:
        return m.group(1), m.group(0).strip()[:60]

    # Table with extra space or positivo details (includes "fraco/forte positivo")
    m = re.search(r're\s+\([^)]+\)\s*:\s+(?:fraco\s+|forte\s+)?positivo', text_norm)
    if m:
        return 'positivo', m.group(0).strip()[:60]

    m = re.search(r're\s+\([^)]+\)\s*:\s+negativo', text_norm)
    if m:
        return 'negativo', m.group(0).strip()[:60]

    # Table format with full name: "receptor de estrogenio (EP1): Positivo/Negativo"
    # Handles multi-word clones: (pgr 636), (pgr-636), clone without parens: ep1:
    m = re.search(r'receptor\s+(?:de\s+)?estrogen\w*\s+\([^)]+\)\s*:?\s*(?:fraco\s+)?(positiv\w*|negativ\w*)', text_norm)
    if not m:
        # Clone without parens: "receptor de estrogenio ep1: positiva"
        m = re.search(r'receptor\s+(?:de\s+)?estrogen\w*\s+\w+\s*:\s*(?:fraco\s+)?(positiv\w*|negativ\w*)', text_norm)
    if m:
        val = 'positivo' if m.group(1).startswith('positiv') else 'negativo'
        return val, m.group(0).strip()[:60]

    # Conclusion format: "RECEPTOR DE ESTROGENO-POSITIVO/NEGATIVO/POSITIVA/NEGATIVA"
    # Also handles: "RECEPTOR DE ESTROGÊNIO-FRACO POSITIVO" → positivo
    m = re.search(r'receptor\s+(?:de\s+)?estrogen\w*[\s-]*(fraco\s+)?(positiv[oa]|negativ[oa])', text_norm)
    if m:
        val = 'positivo' if m.group(2).startswith('positiv') else 'negativo'
        return val, m.group(0).strip()[:60]

    # "POSITIVO/NEGATIVO/POSITIVIDADE/NEGATIVIDADE PARA RECEPTORES DE ESTROGENO"
    m = re.search(r'(positividade|negatividade|positiv[oa]|negativ[oa])\s+para\s+receptor(?:es)?\s+(?:de\s+)?estrogen', text_norm)
    if m:
        val = 'positivo' if m.group(1).startswith('positiv') else 'negativo'
        return val, m.group(0).strip()[:60]

    # "EXPRESSÃO POSITIVA/NEGATIVA DO RE" (rare)
    m = re.search(r'express.o\s+(positiva|negativa)\s+(?:do|de)\s+re\b', text_norm)
    if m:
        val = 'positivo' if m.group(1) == 'positiva' else 'negativo'
        return val, m.group(0).strip()[:60]

    # Simple "RE: Positivo/Negativo" without clone (lab format C)
    m = re.search(r'\bre\s*:\s*(?:fraco\s+)?(positiv\w*|negativ\w*)', text_norm)
    if m:
        val = 'positivo' if m.group(1).startswith('positiv') else 'negativo'
        return val, m.group(0).strip()[:60]

    # "triplo negativo" / "triplo-negativo" in conclusion → RE negativo
    m = re.search(r'triplo[\s-]negativ', text_norm)
    if m:
        return 'negativo', m.group(0).strip()[:60]

    return None, None


# ===========================================================================
# RE_PCT — Estrogen receptor percentage
# ===========================================================================

def _detect_receptor_pct(text_norm: str, prefix_re: str) -> Tuple[Optional[int], Optional[str]]:
    """Generic receptor percentage detector. prefix_re matches RE or RP table line.

    Returns (int percentage or None, evidence).

    POLICY: When invasive and in-situ components are differentiated,
    extract the INVASIVE component percentage only.

    Patterns handled:
    - Table: "RE (6F11): Positivo em 90% das células" → 90
    - Table range: "RE (6F11): Positivo em 30% e 50%" → midpoint 40
    - Table negativo: "RE (6F11): Negativo" → 0
    - Table with </>: "RE (6F11): Positivo em <20%" → 20 (conservative boundary)
    - Inconclusivo: → None
    """
    # INVASIVE COMPONENT PRIORITY: check first
    inv_val, inv_ev = _find_invasive_component_pct(text_norm, prefix_re)
    if inv_val is not None:
        return inv_val, inv_ev

    # Common qualifier pattern: handles masculine/feminine, fraco/a/forte before or after,
    # and optional intensity clause in parens: "(forte intensidade)", "(moderada intensidade)"
    _pos_qual = r'(?:frac[oa]\s+|forte\s+)?positiv[oa](?:\s+(?:frac[oa]|forte|focal))?(?:\s+\([^)]*intensidade[^)]*\))?'

    # Filler between positivo and N%: "em", "nas celulas ...", "em raras celulas ...(cerca de"
    _pct_filler = r'(?:(?:em|nas?)\s+(?:[^%]{0,30}?(?:cerca\s+de\s+)?)?)?'

    # Table range: "Positivo em X% e/a Y%" or "Positivo em X a Y%"
    m = re.search(prefix_re + r':?\s*' + _pos_qual + r'\s+' + _pct_filler + r'(\d+)\s*%?\s*(?:a|e)\s*(\d+)\s*%', text_norm)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        mid = (lo + hi) // 2
        return mid, m.group(0).strip()[:80]

    # Table single: "Positivo [forte] [em] X%"
    m = re.search(prefix_re + r':?\s*' + _pos_qual + r'\s+' + _pct_filler + r'(\d+)\s*%', text_norm)
    if m:
        return int(m.group(1)), m.group(0).strip()[:80]

    # Table: "Positivo (N%" — percentage in parens without "em"
    m = re.search(prefix_re + r':?\s*' + _pos_qual + r'\s*\((?:positiv\w*\s+em\s+)?(\d+)\s*%', text_norm)
    if m:
        return int(m.group(1)), m.group(0).strip()[:80]

    # Table with < or >: "Positivo em <20%" or ">90%"
    m = re.search(prefix_re + r':?\s*' + _pos_qual + r'\s+em\s*[<>]\s*(\d+)\s*%', text_norm)
    if m:
        return int(m.group(1)), m.group(0).strip()[:80]

    # Table with "menos de" / "mais de": "Positivo em menos de 1%" / "mais de 90%"
    m = re.search(prefix_re + r':?\s*' + _pos_qual + r'\s+em\s+menos\s+de\s+(\d+)\s*%', text_norm)
    if m:
        return int(m.group(1)), m.group(0).strip()[:80]

    m = re.search(prefix_re + r':?\s*' + _pos_qual + r'\s+em\s+mais\s+(?:de|que)\s+(\d+)\s*%', text_norm)
    if m:
        return int(m.group(1)), m.group(0).strip()[:80]

    # Table: "Negativo/a" → 0%
    m = re.search(prefix_re + r':?\s*negativ[oa]', text_norm)
    if m:
        return 0, m.group(0).strip()[:60]

    # Inconclusivo → None
    m = re.search(prefix_re + r':?\s*inconclusivo', text_norm)
    if m:
        return None, m.group(0).strip()[:60]

    return None, None


def detect_re_pct(text_norm: str) -> Tuple[Optional[int], Optional[str]]:
    """Detect estrogen receptor percentage. Returns (int/None, evidence)."""
    # Try short form first (RE (clone):), then full name, then simple (RE:)
    for prefix in [r're\s+\([^)]+\)\s*',
                   r'receptor\s+(?:de\s+)?estrogen\w*\s+\([^)]+\)\s*',
                   r'\bre\s*']:
        val, ev = _detect_receptor_pct(text_norm, prefix)
        if val is not None:
            return val, ev
    return None, None


# ===========================================================================
# RE_ALLRED — Estrogen receptor Allred score (L1-only)
# ===========================================================================

def detect_re_allred(text_norm: str) -> Tuple[Optional[int], Optional[str]]:
    """Detect RE Allred score. Returns (int 0-8 or None, evidence)."""
    # "RE (6F11): Positivo em 80% das células (Escore de Allred: 6)"
    # Also: "Escore de Allred:8)", "RE: positivo em 80% allred: 6"
    for pat in [r're\s+\([^)]+\)\s*:.{0,80}allred\s*:\s*(\d+)',
                r'\bre\s*:.{0,80}allred\s*:\s*(\d+)']:
        m = re.search(pat, text_norm)
        if m:
            score = int(m.group(1))
            if 0 <= score <= 8:
                return score, m.group(0).strip()[:80]
    return None, None


# ===========================================================================
# RP_STATUS — Progesterone receptor
# ===========================================================================

def detect_rp_status(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect progesterone receptor status. Returns (positivo/negativo/None, evidence).

    POLICY: When invasive and in-situ components are differentiated,
    extract the INVASIVE component only.
    """
    # INVASIVE COMPONENT PRIORITY: check first
    inv_val, inv_ev = _find_invasive_component_status(text_norm, r'rp\s+\([^)]+\)\s*')
    if inv_val is not None:
        return inv_val, inv_ev

    # Table format: "RP (clone): [fraco/forte] Positivo/Negativo"
    # Multi-word clones: (pgr 636), (pgr-636), (sp1 sp2)
    m = re.search(r'rp\s+\([^)]+\)\s*:\s*(?:fraco\s+|forte\s+)?(positivo|negativo)', text_norm)
    if m:
        return m.group(1), m.group(0).strip()[:60]

    # Table with extra space or positivo details (includes "fraco/forte positivo")
    m = re.search(r'rp\s+\([^)]+\)\s*:\s+(?:fraco\s+|forte\s+)?positivo', text_norm)
    if m:
        return 'positivo', m.group(0).strip()[:60]

    m = re.search(r'rp\s+\([^)]+\)\s*:\s+negativo', text_norm)
    if m:
        return 'negativo', m.group(0).strip()[:60]

    # Table format with full name: "receptor de progesterona (16): Positivo/Negativo"
    # Handles multi-word clones: (pgr 636), (pgr-636), clone without parens
    m = re.search(r'receptor\s+(?:de\s+)?progesteron\w*\s+\([^)]+\)\s*:?\s*(?:fraco\s+)?(positiv\w*|negativ\w*)', text_norm)
    if not m:
        # Clone without parens: "receptor de progesterona 16: positiva"
        m = re.search(r'receptor\s+(?:de\s+)?progesteron\w*\s+\w+\s*:\s*(?:fraco\s+)?(positiv\w*|negativ\w*)', text_norm)
    if m:
        val = 'positivo' if m.group(1).startswith('positiv') else 'negativo'
        return val, m.group(0).strip()[:60]

    # Conclusion: "RECEPTOR DE PROGESTERONA-POSITIVO/NEGATIVO/POSITIVA/NEGATIVA"
    m = re.search(r'receptor\s+(?:de\s+)?progesteron\w*[\s-]*(fraco\s+)?(positiv[oa]|negativ[oa])', text_norm)
    if m:
        val = 'positivo' if m.group(2).startswith('positiv') else 'negativo'
        return val, m.group(0).strip()[:60]

    # "NEGATIVO/POSITIVO/POSITIVIDADE/NEGATIVIDADE PARA RECEPTORES DE PROGESTERONA"
    m = re.search(r'(positividade|negatividade|positiv[oa]|negativ[oa])\s+para\s+receptor(?:es)?\s+(?:de\s+)?progesteron', text_norm)
    if m:
        val = 'positivo' if m.group(1).startswith('positiv') else 'negativo'
        return val, m.group(0).strip()[:60]

    # "EXPRESSÃO POSITIVA/NEGATIVA DO RP"
    m = re.search(r'express.o\s+(positiva|negativa)\s+(?:do|de)\s+rp\b', text_norm)
    if m:
        val = 'positivo' if m.group(1) == 'positiva' else 'negativo'
        return val, m.group(0).strip()[:60]

    # Compound: "positividade/negatividade para receptores de estrogênio e progesterona"
    m = re.search(r'(positividade|negatividade|positiv[oa]|negativ[oa])\s+para\s+receptor(?:es)?\s+(?:de\s+)?(?:estrogen\w*|hormona\w*)\s+e\s+progesteron', text_norm)
    if m:
        val = 'positivo' if m.group(1).startswith('positiv') else 'negativo'
        return val, m.group(0).strip()[:80]

    # Simple "RP: Positivo/Negativo" without clone (lab format C)
    m = re.search(r'\brp\s*:\s*(?:fraco\s+)?(positiv\w*|negativ\w*)', text_norm)
    if m:
        val = 'positivo' if m.group(1).startswith('positiv') else 'negativo'
        return val, m.group(0).strip()[:60]

    # "triplo negativo" / "triplo-negativo" in conclusion → RP negativo
    m = re.search(r'triplo[\s-]negativ', text_norm)
    if m:
        return 'negativo', m.group(0).strip()[:60]

    return None, None


# ===========================================================================
# RP_PCT — Progesterone receptor percentage
# ===========================================================================

def detect_rp_pct(text_norm: str) -> Tuple[Optional[int], Optional[str]]:
    """Detect progesterone receptor percentage. Returns (int/None, evidence)."""
    for prefix in [r'rp\s+\([^)]+\)\s*',
                   r'receptor\s+(?:de\s+)?progesteron\w*\s+\([^)]+\)\s*',
                   r'\brp\s*']:
        val, ev = _detect_receptor_pct(text_norm, prefix)
        if val is not None:
            return val, ev
    return None, None


# ===========================================================================
# RP_ALLRED — Progesterone receptor Allred score (L1-only)
# ===========================================================================

def detect_rp_allred(text_norm: str) -> Tuple[Optional[int], Optional[str]]:
    """Detect RP Allred score. Returns (int 0-8 or None, evidence)."""
    for pat in [r'rp\s+\([^)]+\)\s*:.{0,80}allred\s*:\s*(\d+)',
                r'\brp\s*:.{0,80}allred\s*:\s*(\d+)']:
        m = re.search(pat, text_norm)
        if m:
            score = int(m.group(1))
            if 0 <= score <= 8:
                return score, m.group(0).strip()[:80]
    return None, None


# ===========================================================================
# HER2_SCORE — HER2 IHC score
# ===========================================================================

def _is_reference_context(text_norm: str, match_start: int) -> bool:
    """Check if a match position is in reference/orientation text, not patient result.

    Strategy: Reference sections in IHQ mama reports follow a pattern:
    "COMENTÁRIOS: (1) Quantificação do RE, RP e HER-2 conforme orientações..."
    "(2) Em casos de resultados duvidosos (Escore 2+) para o Produto do Oncogene..."

    Key insight: 'COMENTÁRIOS:' alone is NOT a reliable marker — some reports
    use it for patient-specific observations. The reliable marker is
    '(1) quantificac' or '(1) conforme' which always starts the ASCO/CAP
    reference boilerplate.

    Also catches inline reference context like 'resultados duvidosos' and
    'oncogene' near the match.
    """
    # Find the start of ASCO/CAP reference boilerplate
    # These are highly specific markers that only appear in reference text
    ref_section_markers = [
        r'\(1\)\s*quantificac',
        r'\(1\)\s*conforme',
        r'orienta..es\s+da\s+asco',
    ]
    ref_start = len(text_norm)  # default: no reference section found
    for marker in ref_section_markers:
        m = re.search(marker, text_norm)
        if m and m.start() < ref_start:
            ref_start = m.start()

    # If match is after reference section start, it's reference
    if match_start >= ref_start:
        return True

    # Also check immediate context (within 50 chars before the match)
    # for inline reference keywords
    window_start = max(0, match_start - 50)
    window = text_norm[window_start:match_start]
    inline_ref_keywords = ['resultados duvidosos', 'oncogene', 'recomendado']
    return any(kw in window for kw in inline_ref_keywords)


def detect_her2_score(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect HER2 IHC score. Returns (0/0+/1+/2+/3+/None, evidence).

    Sources:
    - Table: "HER2 / c-erb B-2 (SP3): Escore 3+ (Positivo)"
    - Table variant: "HER-2 (SP3): positivo 3+"
    - Table with quotes: 'Escore "0" (Negativo)'
    - Table inverted: "Escore +3 (Positivo)"
    - Conclusion: "ESCORE 0 PARA EXPRESSÃO PROTÉICA DE HER2"
    - Conclusion: "SUPEREXPRESSÃO ... HER2 (ESCORE 3+)"
    - Conclusion: "NEGATIVA (ESCORE 0) PARA HER2"
    - Conclusion: "ESCORE 2+ (DUVIDOSO) PARA HER2"
    - Conclusion: "EXPRESSÃO DUVIDOSA DOS PRODUTOS DO HER2" → 2+

    GUARD: Reference/orientation text (ASCO/CAP criteria, footnotes) contains
    "Escore 2+" and "Escore 3+" that must NOT be captured as patient result.
    Table patterns are safe (highly specific). Conclusion patterns use
    _is_reference_context() to reject matches near reference keywords.
    """
    # ---- TIER 1: Table patterns (safe — highly specific, no ref guard needed) ----

    # Table format: "HER2 / c-erb B-2 (clone): Escore N+ (status)"
    # Also handles quotes around score: Escore "0"
    m = re.search(r'her2?\s*/?\s*c-erb\s*b-?\s*2\s*\([^)]+\)\s*:\s*escore\s+"?(\d\+?)"?', text_norm)
    if m:
        score = m.group(1)
        return score, m.group(0).strip()[:80]

    # Table inverted: "Escore +3 (Positivo)" or "Escore + 3" (+ before number)
    m = re.search(r'her2?\s*/?\s*c-erb\s*b-?\s*2\s*\([^)]+\)\s*:\s*escore\s+\+\s*(\d)', text_norm)
    if m:
        score = m.group(1) + '+'
        return score, m.group(0).strip()[:80]

    # Table: "HER-2 (SP3): Escore N+" (without c-erb prefix)
    m = re.search(r'her[\s-]?2\s*\([^)]+\)\s*:?\s*escore\s+"?(\d\+?)"?', text_norm)
    if m:
        return m.group(1), m.group(0).strip()[:80]

    # Table: "HER-2 (SP3): Escore +N (status)" — inverted with space
    m = re.search(r'her[\s-]?2\s*\([^)]+\)\s*:?\s*escore\s+\+\s*(\d)', text_norm)
    if m:
        score = m.group(1) + '+'
        return score, m.group(0).strip()[:80]

    # Table variant: "HER-2 (SP3): positivo 3+" or "HER-2 (clone): negativo 0"
    m = re.search(r'her[\s-]?2\s*\([^)]+\)\s*:?\s*(?:positivo|negativo)\s+(\d\+?)', text_norm)
    if m:
        return m.group(1), m.group(0).strip()[:80]

    # Table: "HER-2 (SP3): N+ (status)" or "N+ status" — score BEFORE status
    m = re.search(r'her[\s-]?2\s*\([^)]+\)\s*:?\s*(\d\+?)\s+\(?(?:positiv|negativ)', text_norm)
    if m:
        return m.group(1), m.group(0).strip()[:80]

    # Table with score in parens: "HER-2 (SP3): status (escore N+ ...)"
    # Allows extra text inside parens: "segundo ASCO/CAP", quotes around score
    m = re.search(r'her[\s-]?2\s*\([^)]+\)\s*:?\s*(?:positiv|negativ)\w*\s+\(escore\s+["\']*(\d\+?)["\']*[^)]*\)', text_norm)
    if m:
        return m.group(1), m.group(0).strip()[:80]

    # Table: "HER-2 (SP3): (0+) Negativo" — score in parens BEFORE status
    m = re.search(r'her[\s-]?2\s*\([^)]+\)\s*:\s*\((\d\+?)\)\s*(?:positiv|negativ)', text_norm)
    if m:
        return m.group(1), m.group(0).strip()[:80]

    # Table: "HER-2 (SP3): duvidoso (2+)" — interpretation word before score
    m = re.search(r'her[\s-]?2\s*\([^)]+\)\s*:?\s*duvidoso?\s*\((\d\+?)\)', text_norm)
    if m:
        return m.group(1), m.group(0).strip()[:80]

    # Simple "HER-2: N+ (status)" or "HER-2: status (score)" without clone
    m = re.search(r'\bher[\s-]?2\s*:\s*(\d\+?)\s*\((?:positiv|negativ)', text_norm)
    if m and not _is_reference_context(text_norm, m.start()):
        return m.group(1), m.group(0).strip()[:80]
    m = re.search(r'\bher[\s-]?2\s*:\s*(?:positiv\w*|negativ\w*)\s*\((\d\+?)\)', text_norm)
    if m and not _is_reference_context(text_norm, m.start()):
        return m.group(1), m.group(0).strip()[:80]

    # ---- TIER 1.5: c-erbB-2 (clone) alone, OCR 'O', status+(digit) without escore word ----
    # Added 2026-04-17 by Dr. Rafael's directive "100% dos dados extraiveis testados".
    # Empirical audit of 7698 abstained her2_score found 85 laudos with real HER2 result
    # missed due to: (a) 'c-erbB-2 (clone): ...' without 'HER2/' prefix, (b) OCR letter 'O'
    # instead of digit 0, (c) qualitative 'Negativo (0)' without 'escore' keyword. Reference
    # guard applied since missing 'HER2' prefix raises false-positive risk from footer text.

    # c-erbB-2 (clone): qualitativo (escore N+) — c-erb alone, with explicit "escore" word
    m = re.search(r'c-?erb\s*b\s*-?\s*2\s*\([^)]+\)\s*:?\s*(?:positiv|negativ)\w*\s*\(escore\s*["\']*(\d\+?)["\']*[^)]*\)', text_norm)
    if m and not _is_reference_context(text_norm, m.start()):
        return m.group(1), m.group(0).strip()[:80]

    # (HER-2|c-erbB-2) (clone): qualitativo (N) — status then bare digit in parens
    m = re.search(r'(?:her[\s-]?2|c-?erb\s*b\s*-?\s*2)\s*\([^)]+\)\s*:?\s*(?:positiv|negativ)\w*\s*\((\d\+?)\)', text_norm)
    if m and not _is_reference_context(text_norm, m.start()):
        return m.group(1), m.group(0).strip()[:80]

    # Tier 1 tabular com OCR 'O' no lugar de 0: "Escore O" / 'Escore "O"' (normalize() lowercases)
    m = re.search(r'(?:her2?\s*/?\s*c-erb\s*b-?\s*2|her[\s-]?2|c-?erb\s*b\s*-?\s*2)\s*\([^)]+\)\s*:\s*escore\s*"?o"?(?!\w)', text_norm)
    if m and not _is_reference_context(text_norm, m.start()):
        return '0', m.group(0).strip()[:80]

    # ---- TIER 2: Conclusion patterns (need reference guard) ----

    # Conclusion: "SUPEREXPRESSÃO PROTÉICA DE HER2 (ESCORE 3+)"
    m = re.search(r'her2\s*\(\s*escore\s+(\d\+?)\s*\)', text_norm)
    if m and not _is_reference_context(text_norm, m.start()):
        return m.group(1), m.group(0).strip()[:80]

    # Conclusion: "ESCORE N+ PARA EXPRESSÃO PROTÉICA DE HER2"
    m = re.search(r'escore\s+"?(\d\+?)"?\s+(?:para\s+)?express.o\s+prot.ica\s+(?:de\s+|do\s+)?her', text_norm)
    if m and not _is_reference_context(text_norm, m.start()):
        return m.group(1), m.group(0).strip()[:80]

    # Conclusion: "ESCORE N+ (...) PARA HER2" or "ESCORE N+ PARA ... HER2"
    m = re.search(r'escore\s+"?(\d\+?)"?\s*(?:\([^)]*\)\s*)?para\s+.{0,30}her2', text_norm)
    if m and not _is_reference_context(text_norm, m.start()):
        return m.group(1), m.group(0).strip()[:80]

    # Conclusion: "NEGATIVA (ESCORE 0) PARA HER2" or "(ESCORE 0) PARA HER2"
    m = re.search(r'\(escore\s+(\d\+?)\)\s*(?:para\s+)?(?:express.o\s+)?(?:prot.ica\s+)?(?:de\s+|do\s+)?her2', text_norm)
    if m and not _is_reference_context(text_norm, m.start()):
        return m.group(1), m.group(0).strip()[:80]

    # Conclusion: "EXPRESSÃO DUVIDOSA DOS PRODUTOS DO HER2" → implies 2+
    m = re.search(r'express.o\s+duvidosa\s+(?:dos\s+produtos\s+(?:do\s+)?)?her2', text_norm)
    if m and not _is_reference_context(text_norm, m.start()):
        return '2+', m.group(0).strip()[:80]

    return None, None


# ===========================================================================
# HER2_STATUS — HER2 interpretation
# ===========================================================================

def derive_her2_status(her2_score: Optional[str], text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Derive HER2 status from score or explicit text.

    Rules (ASCO/CAP):
    - 3+ = positivo
    - 2+ = equivoco (needs ISH/FISH)
    - 0, 0+, 1+ = negativo
    """
    # If we have a score, derive from it
    # Handle bare digits (without +): "3" = "3+", "2" = "2+"
    if her2_score is not None:
        if her2_score in ('3+', '3'):
            return 'positivo', f'score {her2_score}'
        elif her2_score in ('2+', '2'):
            return 'equivoco', f'score {her2_score}'
        else:  # 0, 0+, 1+, 1
            return 'negativo', f'score {her2_score}'

    # No score → check explicit text in conclusion

    # "SUPEREXPRESSÃO PROTÉICA DE HER2" → positivo (no score but overexpression)
    m = re.search(r'superexpress.o\s+prot.ica\s+(?:de\s+|do\s+)?her2', text_norm)
    if m and not _is_reference_context(text_norm, m.start()):
        return 'positivo', m.group(0).strip()[:80]

    # "EXPRESSÃO POSITIVA/NEGATIVA DOS PRODUTOS DO HER2"
    # Also handles concordance error: "NEGATIVO" (masc) instead of "NEGATIVA" (fem)
    m = re.search(r'express.o\s+(positiv[oa]|negativ[oa])\s+(?:dos\s+produtos\s+(?:do\s+)?)?her2', text_norm)
    if m and not _is_reference_context(text_norm, m.start()):
        val = 'positivo' if m.group(1).startswith('positiv') else 'negativo'
        return val, m.group(0).strip()[:80]

    # "COM EXPRESSÃO NEGATIVA/POSITIVA ... HER2"
    m = re.search(r'(?:com\s+)?express.o\s+(negativ[oa]|positiv[oa])\s+.{0,20}her2', text_norm)
    if m and not _is_reference_context(text_norm, m.start()):
        val = 'positivo' if m.group(1).startswith('positiv') else 'negativo'
        return val, m.group(0).strip()[:80]

    # Table: "HER-2 (clone): negativo" or "HER-2 (clone): positivo" (no score)
    m = re.search(r'her[\s-]?2\s*\([^)]+\)\s*:\s*(positivo|negativo)', text_norm)
    if m:
        return m.group(1), m.group(0).strip()[:60]

    # "expressão duvidosa do/para HER2" → equivoco
    m = re.search(r'express.o\s+duvidosa\s+(?:do|de|para)\s+her', text_norm)
    if m and not _is_reference_context(text_norm, m.start()):
        return 'equivoco', m.group(0).strip()[:60]

    # "superexpressão de HER2" / "sem superexpressão de HER2"
    m_neg = re.search(r'(?:nao|sem|ausencia).{0,10}(?:super|sobre)express.o.{0,20}her', text_norm)
    m_pos = re.search(r'(?:super|sobre)express.o.{0,20}her', text_norm)
    if m_neg and not _is_reference_context(text_norm, m_neg.start()):
        return 'negativo', m_neg.group(0).strip()[:60]
    elif m_pos and not _is_reference_context(text_norm, m_pos.start()):
        return 'positivo', m_pos.group(0).strip()[:60]

    # Simple "HER-2: negativo/positivo" without clone
    m = re.search(r'\bher[\s-]?2\s*:\s*(positiv\w*|negativ\w*)', text_norm)
    if m and not _is_reference_context(text_norm, m.start()):
        val = 'positivo' if m.group(1).startswith('positiv') else 'negativo'
        return val, m.group(0).strip()[:60]

    # "negativo/negatividade para HER2"
    m = re.search(r'(positividade|negatividade|positiv[oa]|negativ[oa])\s+(?:para|do)\s+her', text_norm)
    if m and not _is_reference_context(text_norm, m.start()):
        val = 'positivo' if m.group(1).startswith('positiv') else 'negativo'
        return val, m.group(0).strip()[:60]

    # "triplo negativo" → HER2 negativo
    m = re.search(r'triplo[\s-]negativ', text_norm)
    if m:
        return 'negativo', m.group(0).strip()[:60]

    return None, None


# ===========================================================================
# KI67_PCT — Ki-67 proliferation index
# ===========================================================================

def detect_ki67_pct(text_norm: str) -> Tuple[Optional[int], Optional[str]]:
    """Detect Ki-67 proliferation index (percentage). Returns (int/None, evidence).

    Sources:
    - Table: "Ki-67 / MIB-1 (clone): Positivo em X% das células"
    - Table: "Ki-67 / MIB-1 (clone): Positivo em X a Y% das células" → use midpoint
    - Conclusion: "ÍNDICE DE PROLIFERAÇÃO CELULAR DE X%"
    """
    # Table: "Ki-67 / MIB-1 (clone): Positivo em X% [a Y%]"
    m = re.search(r'ki[\s-]?67\s*/?\s*mib[\s-]?1\s*\([^)]+\)\s*:\s*positivo\s+em\s+(\d+)\s*%?\s*(?:a|e)\s*(\d+)\s*%', text_norm)
    if m:
        # Range: use midpoint
        lo = int(m.group(1))
        hi = int(m.group(2))
        mid = (lo + hi) // 2
        return mid, m.group(0).strip()[:80]

    m = re.search(r'ki[\s-]?67\s*/?\s*mib[\s-]?1\s*\([^)]+\)\s*:\s*positivo\s+em\s+(\d+)\s*%', text_norm)
    if m:
        return int(m.group(1)), m.group(0).strip()[:80]

    # Broader: "ki-67 ... positivo/positiva [em] [de] X [a/e/-] Y%"
    m = re.search(r'ki[\s-]?67.{0,40}positiv[oa]\s+(?:em\s+)?(?:de\s+)?(\d+)\s*%?\s*(?:a|e|-)\s*(\d+)\s*%', text_norm)
    if m:
        lo = int(m.group(1))
        hi = int(m.group(2))
        mid = (lo + hi) // 2
        return mid, m.group(0).strip()[:80]

    m = re.search(r'ki[\s-]?67.{0,40}positiv[oa]\s+(?:em\s+)?(\d+)\s*%', text_norm)
    if m:
        return int(m.group(1)), m.group(0).strip()[:80]

    # Fallback for long clone descriptions: "ki-67 (antigeno...) (clone): Positivo [em] N%"
    # Uses [^:] to avoid crossing antibody row boundaries
    m = re.search(r'ki[\s-]?67[^:]{0,55}:\s*positiv[oa]\s+(?:em\s+)?(\d+)\s*%', text_norm)
    if m:
        return int(m.group(1)), m.group(0).strip()[:80]

    # Conclusion: "ÍNDICE DE PROLIFERAÇÃO CELULAR [ao Ki67] DE [ate/cerca de] X [a Y]%"
    m = re.search(r'(?:indice\s+de\s+)?proliferac.o\s+celular\s+(?:ao\s+ki[\s-]?67\s+)?de\s+(?:(?:ate|cerca\s+de)\s+)?(\d+)\s*(?:a\s+(\d+)\s*)?%', text_norm)
    if m:
        lo = int(m.group(1))
        hi = m.group(2)
        if hi:
            mid = (lo + int(hi)) // 2
            return mid, m.group(0).strip()[:80]
        return lo, m.group(0).strip()[:80]

    # Variant: "proliferação celular de [ate/cerca de] X a Y% ao ki67"
    m = re.search(r'proliferac.o\s+celular\s+de\s+(?:(?:ate|cerca\s+de)\s+)?(\d+)\s*(?:a\s+(\d+)\s*)?%\s*ao\s+ki', text_norm)
    if m:
        lo = int(m.group(1))
        hi = m.group(2)
        if hi:
            mid = (lo + int(hi)) // 2
            return mid, m.group(0).strip()[:80]
        return lo, m.group(0).strip()[:80]

    # Standalone: "ao ki-67 de [ate/cerca de] X [a Y]%" — without "proliferação celular"
    # Covers: "indice proliferativo ao ki-67 de ate 10%"
    m = re.search(r'ao\s+ki[\s-]?67\s+de\s+(?:(?:ate|cerca\s+de)\s+)?(\d+)\s*(?:a\s+(\d+)\s*)?%', text_norm)
    if m:
        lo = int(m.group(1))
        hi = m.group(2)
        if hi:
            mid = (lo + int(hi)) // 2
            return mid, m.group(0).strip()[:80]
        return lo, m.group(0).strip()[:80]

    # "Positivo/a em mais de X%" → X (lower bound; ki67_is_lower_bound flag set by caller)
    m = re.search(r'ki[\s-]?67.{0,40}positiv[oa]\s+em\s+mais\s+(?:de|que)\s+(\d+)\s*%', text_norm)
    if m:
        return int(m.group(1)), m.group(0).strip()[:80]

    # "Positivo/a em menos [de] X%" → X (upper boundary)
    m = re.search(r'ki[\s-]?67.{0,40}positiv[oa]\s+em\s+menos\s+(?:de\s+)?(\d+)\s*%', text_norm)
    if m:
        return int(m.group(1)), m.group(0).strip()[:80]

    # "Positivo/a [em] [de] ate X%" → X (upper boundary)
    m = re.search(r'ki[\s-]?67.{0,40}positiv[oa]\s+(?:em\s+)?(?:(?:com\s+areas\s+)?de\s+)?ate\s+(\d+)\s*%', text_norm)
    if m:
        return int(m.group(1)), m.group(0).strip()[:80]

    # "Positivo/a [em] cerca de X% [a Y%]" → X or midpoint
    m = re.search(r'ki[\s-]?67.{0,40}positiv[oa][\s,]+(?:em\s+)?cerca\s+de\s+(\d+)\s*%?\s*(?:a\s+(\d+)\s*%)?', text_norm)
    if not m:
        # Long-clone fallback for "cerca de" (colon-safe window)
        m = re.search(r'ki[\s-]?67[^:]{0,55}:\s*(?:reacao\s+)?positiv[oa]\s+(?:em\s+)?cerca\s+de\s+(\d+)\s*%?\s*(?:a\s+(\d+)\s*%)?', text_norm)
    if m:
        lo = int(m.group(1))
        hi = m.group(2)
        if hi:
            mid = (lo + int(hi)) // 2
            return mid, m.group(0).strip()[:80]
        return lo, m.group(0).strip()[:80]

    # "Positivo entre X e Y%" → midpoint
    m = re.search(r'ki[\s-]?67.{0,40}positiv[oa]\s+entre\s+(\d+)\s*%?\s*(?:e|a)\s*(\d+)\s*%', text_norm)
    if m:
        lo = int(m.group(1))
        hi = int(m.group(2))
        mid = (lo + hi) // 2
        return mid, m.group(0).strip()[:80]

    # "Positivo em < X%" → X (upper boundary)
    m = re.search(r'ki[\s-]?67.{0,40}positiv[oa]\s+em\s+<\s*(\d+)\s*%', text_norm)
    if m:
        return int(m.group(1)), m.group(0).strip()[:80]

    # "ki67... variando de X [a Y]%" → midpoint or single
    m = re.search(r'ki[\s-]?67.{0,60}variando\s+de\s+(\d+)\s*(?:%?\s*a\s*(\d+)\s*)?%', text_norm)
    if m:
        lo = int(m.group(1))
        hi = m.group(2)
        if hi:
            mid = (lo + int(hi)) // 2
            return mid, m.group(0).strip()[:80]
        return lo, m.group(0).strip()[:80]

    # "ki67 (N%)" — parenthesized percentage
    m = re.search(r'ki[\s-]?67\s*\((\d+)\s*%\)', text_norm)
    if m:
        return int(m.group(1)), m.group(0).strip()[:40]

    # "ki67 de N%" — simple with "de"
    m = re.search(r'(?:,\s*e?\s*)?ki[\s-]?67\s+de\s+(\d+)\s*%', text_norm)
    if m:
        return int(m.group(1)), m.group(0).strip()[:40]

    # "KI67: X%" or "KI67 (clone): X%" (rare simple format)
    m = re.search(r'ki[\s-]?67\s*(?:\([^)]*\)\s*)?:\s*(\d+)\s*%', text_norm)
    if m:
        return int(m.group(1)), m.group(0).strip()[:40]

    # "ki67 (clone): positivo em N das celulas" — missing % sign (OCR/formatting)
    m = re.search(r'ki[\s-]?67[^:]{0,55}:\s*positiv[oa]\s+em\s+(\d+)\s+das?\s+celulas', text_norm)
    if m:
        val = int(m.group(1))
        if val <= 100:  # guard against OCR artifacts like "605"
            return val, m.group(0).strip()[:80]

    # "N% ao ki-67" or "N a M% ao ki-67" — percentage precedes marker
    m = re.search(r'(\d+)\s*(?:%?\s*a\s+(\d+)\s*)?%\s+ao\s+ki[\s-]?67', text_norm)
    if m:
        lo = int(m.group(1))
        hi = m.group(2)
        if hi:
            mid = (lo + int(hi)) // 2
            return mid, m.group(0).strip()[:80]
        return lo, m.group(0).strip()[:80]

    return None, None


# ===========================================================================
# TIPO_HIST_IHQ — Histological type from IHQ conclusion
# ===========================================================================

def detect_tipo_hist_ihq(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect histological type from IHQ conclusion/material."""

    # Lobular invasive (CLI) — check before ductal
    # Handles: "carcinoma lobular invasivo", "carcinoma mamario invasivo lobular",
    # "carcinoma lobular pleomorfico infiltrante", "carcinoma lobular de mama"
    m = re.search(
        r'carcinoma\s+(?:mamario\s+)?(?:invasiv\w*\s+)?lobular'
        r'(?:\s+(?:pleomorf\w*\s+)?(?:invasivo|infiltrante))?'
        r'(?:\s+de\s+mama)?',
        text_norm)
    if m and ('lobular' in m.group(0)):
        txt = m.group(0)
        # Accept if any invasive indicator OR "de mama" (implies invasive in IHQ context)
        if 'invasiv' in txt or 'infiltrant' in txt or 'de mama' in txt:
            return 'CLI', txt.strip()[:60]

    # Lobular in situ (CLIS) — distinct from CLI
    m = re.search(r'carcinoma\s+(?:mamario\s+)?lobular\s*["\']?in\s*situ["\']?', text_norm)
    if m:
        return 'CLIS', m.group(0).strip()[:60]

    # Micropapilar
    m = re.search(r'(?:carcinoma\s+)?micropapilar', text_norm)
    if m and re.search(r'carcinoma|neoplasia', text_norm):
        return 'micropapilar', m.group(0).strip()[:60]

    # Mucinoso / coloide (synonym)
    m = re.search(r'carcinoma\s+(?:mucinos[oa]|coloid[ea])', text_norm)
    if m:
        return 'mucinoso', m.group(0).strip()[:60]

    # CDIS / ductal in situ / intraductal / intraducto
    insitu = re.search(
        r'carcinoma\s+ductal\s*["\']*!?in\s*situ["\']*'
        r'|cdis'
        r'|\bductal\s+["\']*in\s+situ["\']*'
        r'|carcinoma\s+intraduct(?:al|o)',
        text_norm)
    # Invasive guard: detect "invasivo/infiltrante/microinvasivo" but NOT
    # when negated ("nao-invasiva", "nao invasivo", "sem invasao")
    has_invasive_raw = re.search(r'invasiv|infiltrant|microinvasiv', text_norm)
    has_invasive = False
    if has_invasive_raw:
        # Check ALL invasive mentions — if ALL are negated, not truly invasive
        for im in re.finditer(r'(?:invasiv|infiltrant|microinvasiv)\w*', text_norm):
            pre = text_norm[max(0, im.start()-15):im.start()]
            if not re.search(r'(?:nao[\s-]?|sem\s+|ausencia\s+de\s+)', pre):
                has_invasive = True
                break
    if insitu and not has_invasive:
        return 'CDIS', insitu.group(0).strip()[:60]

    # CDI_SOE (ductal invasive / NST / tipo não especial / microinvasivo)
    cdi_pats = [
        r'carcinoma\s+(?:mamario\s+)?ductal\s+(?:invasivo|infiltrante)',
        r'carcinoma\s+(?:mamario\s+)?(?:invasivo|infiltrante)\s+(?:da\s+mama|em\s+mama|de\s+mama|sem\s+tipo)',
        r'carcinoma\s+(?:mamario\s+)?invasivo[^,]*tipo[\s-]+nao[\s-]+especial',
        r'tipo[\s-]+nao[\s-]+especial',
        r'carcinoma\s+(?:mamario\s+)?(?:ductal\s+)?microinvasiv',
        # "carcinoma mamario invasivo" (generic — after specific subtypes)
        r'carcinoma\s+mamario\s+invasiv',
        # "carcinoma de ductos mamarios"
        r'carcinoma\s+de\s+ductos\s+mamari',
        # "carcinoma mamario ductal"
        r'carcinoma\s+mamario\s+ductal',
        # "carcinoma ductal" alone (without in situ) — in IHQ context = CDI
        # Match with following word or comma (e.g., "carcinoma ductal, alto grau")
        r'(?:adeno)?carcinoma\s+ductal(?:\s+(?:de|em|metastatico|pouco|moderadamente|bem)|,)',
    ]
    for pat in cdi_pats:
        m = re.search(pat, text_norm)
        if m:
            return 'CDI_SOE', m.group(0).strip()[:60]

    # Generic carcinoma with invasive
    if has_invasive and re.search(r'carcinoma', text_norm):
        m = re.search(r'carcinoma\s+(?:invasiv|infiltrant)', text_norm)
        if m:
            return 'CDI_SOE', m.group(0).strip()[:60]

    # CDIS/CLIS + invasive component → CDI_SOE (the invasive takes priority)
    if insitu and has_invasive:
        return 'CDI_SOE', 'in situ + invasive component'

    # Papilar (check AFTER CDI_SOE to avoid matching subordinate "carcinoma papilar")
    m = re.search(r'carcinoma\s+(?:invasiv\w*\s+)?papilar', text_norm)
    if m:
        return 'papilar', m.group(0).strip()[:60]

    return None, None


# ===========================================================================
# SUBTIPO_MOLECULAR — Derived molecular subtype
# ===========================================================================

def derive_subtipo_molecular(
    re_status: Optional[str],
    rp_status: Optional[str],
    her2_status: Optional[str],
    ki67_pct: Optional[int],
) -> Optional[str]:
    """Derive molecular subtype from IHQ markers.

    St. Gallen 2013 consensus:
    - Luminal A: RE+ and/or RP+, HER2-, Ki67 low (<20%)
    - Luminal B HER2-: RE+ and/or RP+, HER2-, Ki67 high (>=20%)
    - Luminal B HER2+: RE+ and/or RP+, HER2+
    - HER2-enriched: RE-, RP-, HER2+
    - Triple-negative: RE-, RP-, HER2-
    """
    if re_status is None and rp_status is None:
        return None

    hr_pos = (re_status == 'positivo' or rp_status == 'positivo')
    hr_neg = (re_status == 'negativo' and rp_status == 'negativo')
    her2_pos = (her2_status == 'positivo')
    her2_neg = (her2_status == 'negativo')

    if hr_pos and her2_pos:
        return 'luminal_b_her2pos'

    if hr_pos and her2_neg:
        if ki67_pct is not None:
            if ki67_pct < 20:
                return 'luminal_a'
            else:
                return 'luminal_b_her2neg'
        # Ki67 unknown → can't distinguish luminal_a vs luminal_b
        return 'luminal'  # generic luminal, needs Ki67

    if hr_neg and her2_pos:
        return 'her2_enriched'

    if hr_neg and her2_neg:
        return 'triplo_negativo'

    # HER2 equivoco or missing → can't classify without HER2 resolution
    # (Decision 3, R10: equivoco needs ISH; missing = no data)
    return None


# ===========================================================================
# SMOKE TESTS
# ===========================================================================

def run_smoke_tests():
    failures = 0
    total = 0

    def check(name, got, expected):
        nonlocal failures, total
        total += 1
        if got != expected:
            print(f"  FAIL {name}: got {got!r}, expected {expected!r}")
            failures += 1

    # --- RE ---
    t = normalize("RE  (6F11): Positivo")
    v, _ = detect_re_status(t)
    check("re_table_pos", v, "positivo")

    t = normalize("RE  (6F11): Negativo")
    v, _ = detect_re_status(t)
    check("re_table_neg", v, "negativo")

    t = normalize("RECEPTOR DE ESTROGÊNIO-POSITIVO, RECEPTOR DE PROGESTERONA-POSITIVO")
    v, _ = detect_re_status(t)
    check("re_concl_pos", v, "positivo")

    t = normalize("RECEPTOR DE ESTROGÊNIO-NEGATIVO, RECEPTOR DE PROGESTERONA-NEGATIVO")
    v, _ = detect_re_status(t)
    check("re_concl_neg", v, "negativo")

    t = normalize("RECEPTOR DE ESTROGÊNIO-FRACO POSITIVO, RECEPTOR DE PROGESTERONA-NEGATIVO")
    v, _ = detect_re_status(t)
    check("re_concl_fraco_pos", v, "positivo")

    t = normalize("FIBROADENOMA SEM ATIPIA")
    v, _ = detect_re_status(t)
    check("re_absent", v, None)

    # --- RE multi-word clone / missing colon ---
    t = normalize("RE (PGR 636): Positivo em 90% das células")
    v, _ = detect_re_status(t)
    check("re_multiword_clone_pos", v, "positivo")

    t = normalize("RE (PGR-636): Negativo")
    v, _ = detect_re_status(t)
    check("re_multiword_clone_neg", v, "negativo")

    t = normalize("Receptor de Estrogenio (PGR 636): Positivo em 80%")
    v, _ = detect_re_status(t)
    check("re_fullname_multiword_clone", v, "positivo")

    t = normalize("Receptor de Estrogenio (EP1) Positivo em 80%")
    v, _ = detect_re_status(t)
    check("re_fullname_no_colon", v, "positivo")

    t = normalize("Receptor de Estrogenio EP1: Positiva em 80%")
    v, _ = detect_re_status(t)
    check("re_clone_without_parens", v, "positivo")

    t = normalize("RE (PGR 636): Positivo em 90% das células")
    v, _ = detect_re_pct(t)
    check("re_pct_multiword_clone", v, 90)

    # --- RE fraco/forte positivo ---
    t = normalize("RE (6F11) : fraco positivo focal")
    v, _ = detect_re_status(t)
    check("re_fraco_positivo_short", v, "positivo")

    t = normalize("RE (6F11) : forte positivo")
    v, _ = detect_re_status(t)
    check("re_forte_positivo_short", v, "positivo")

    t = normalize("RE (6F11) : fraco positivo em 20% das celulas")
    v, _ = detect_re_status(t)
    check("re_fraco_positivo_pct", v, "positivo")

    # --- RE invasive component priority ---
    t = normalize("RP (16): Negativo no componente invasivo e Positivo no componente \"in situ\" em 70% das células")
    v, _ = detect_rp_status(t)
    check("rp_invasive_neg", v, "negativo")

    t = normalize("RE  (6F11): Positivo em 90% das células (Escore de Allred: 7) (negativo no componente intraductal)")
    v, _ = detect_re_status(t)
    check("re_invasive_pos_intraductal_note", v, "positivo")  # first value is invasive

    t = normalize("RE (6F11): Negativo (no componente microinvasivo) Positivo em 10% das células (do CLIS)")
    v, _ = detect_re_status(t)
    check("re_invasive_neg_microinv", v, "negativo")

    # --- RE_PCT invasive component ---
    t = normalize("RP (16): Negativo no componente invasivo e Positivo no componente \"in situ\" em 70% das células")
    v, _ = detect_rp_pct(t)
    check("rp_pct_invasive_0", v, 0)

    # --- RE_PCT ---
    t = normalize("RE  (6F11): Positivo em 90% das células (Escore de Allred: 6)")
    v, _ = detect_re_pct(t)
    check("re_pct_90", v, 90)

    t = normalize("RE  (6F11): Positivo em  5% das células (Escore de Allred: 3)")
    v, _ = detect_re_pct(t)
    check("re_pct_5", v, 5)

    t = normalize("RE  (6F11): Positivo em 1% das células")
    v, _ = detect_re_pct(t)
    check("re_pct_1", v, 1)

    t = normalize("RE  (6F11): Positivo em 100% das células (Escore de Allred: 8)")
    v, _ = detect_re_pct(t)
    check("re_pct_100", v, 100)

    t = normalize("RE  (6F11): Negativo")
    v, _ = detect_re_pct(t)
    check("re_pct_neg_0", v, 0)

    t = normalize("RE  (6F11): Negativo (controle interno positivo) (Escore de Allred: 0)")
    v, _ = detect_re_pct(t)
    check("re_pct_neg_allred0", v, 0)

    t = normalize("RECEPTOR DE ESTROGÊNIO-POSITIVO")
    v, _ = detect_re_pct(t)
    check("re_pct_concl_none", v, None)  # conclusion has no %, return None

    # iter 5: "positivo X%" without "em"
    t = normalize("RE  (6F11): Positivo 80% das células (Escore de Allred: 6)")
    v, _ = detect_re_pct(t)
    check("re_pct_no_em_80", v, 80)

    # iter 5: "positivo forte em X%"
    t = normalize("RE (EP1): Positivo forte em 20% das células")
    v, _ = detect_re_pct(t)
    check("re_pct_forte_20", v, 20)

    # iter 5: "menos de X%"
    t = normalize("RP (16): Positivo em menos de 1% das células")
    v, _ = detect_rp_pct(t)
    check("rp_pct_menos_de_1", v, 1)

    t = normalize("RE (6F11): Positivo em menos de 5% das células")
    v, _ = detect_re_pct(t)
    check("re_pct_menos_de_5", v, 5)

    # iter 5: "mais de X%" for RE/RP
    t = normalize("RE (6F11): Positivo em mais de 90% das células")
    v, _ = detect_re_pct(t)
    check("re_pct_mais_de_90", v, 90)

    # iter 5: "receptor de estrogenio (clone): positivo"
    t = normalize("Receptor de Estrogenio (EP1): Positivo forte em 20% dos ductos")
    v, _ = detect_re_status(t)
    check("re_status_receptor_clone_pos", v, "positivo")
    v, _ = detect_re_pct(t)
    check("re_pct_receptor_clone_20", v, 20)

    t = normalize("Receptor de Estrogenio (EP1): Negativo")
    v, _ = detect_re_status(t)
    check("re_status_receptor_clone_neg", v, "negativo")

    # iter 5: "receptor de progesterona (clone): positivo"
    t = normalize("Receptor de Progesterona (16): Positivo em 30%")
    v, _ = detect_rp_status(t)
    check("rp_status_receptor_clone_pos", v, "positivo")
    v, _ = detect_rp_pct(t)
    check("rp_pct_receptor_clone_30", v, 30)

    # iter 6: simple "RE: Positivo" format (no clone)
    t = normalize("RE: Positivo em 90%; Escore Allred: 8")
    v, _ = detect_re_status(t)
    check("re_simple_colon_pos", v, "positivo")
    v, _ = detect_re_pct(t)
    check("re_pct_simple_colon_90", v, 90)
    v, _ = detect_re_allred(t)
    check("re_allred_simple_colon_8", v, 8)

    t = normalize("RE: Negativo")
    v, _ = detect_re_status(t)
    check("re_simple_colon_neg", v, "negativo")

    t = normalize("RE: Positivo forte em 90% das células neoplásicas")
    v, _ = detect_re_status(t)
    check("re_simple_colon_forte_pos", v, "positivo")
    v, _ = detect_re_pct(t)
    check("re_pct_simple_colon_forte_90", v, 90)

    t = normalize("RP: Positivo em 80% Allred: 8")
    v, _ = detect_rp_status(t)
    check("rp_simple_colon_pos", v, "positivo")
    v, _ = detect_rp_pct(t)
    check("rp_pct_simple_colon_80", v, 80)
    v, _ = detect_rp_allred(t)
    check("rp_allred_simple_colon_8", v, 8)

    t = normalize("RP: Negativo")
    v, _ = detect_rp_status(t)
    check("rp_simple_colon_neg", v, "negativo")

    # iter 6: positividade/negatividade para receptor de estrogeno
    t = normalize("apresentando positividade para receptores de estrogênio e progesterona")
    v, _ = detect_re_status(t)
    check("re_positividade_para", v, "positivo")
    v, _ = detect_rp_status(t)
    check("rp_positividade_para", v, "positivo")

    t = normalize("negatividade para receptor de estrogênio, negatividade para receptor de progesterona")
    v, _ = detect_re_status(t)
    check("re_negatividade_para", v, "negativo")
    v, _ = detect_rp_status(t)
    check("rp_negatividade_para", v, "negativo")

    # iter 6: triplo negativo
    t = normalize("carcinoma invasivo da mama, triplo negativo para RE, RP e HER-2")
    v, _ = detect_re_status(t)
    check("re_triplo_neg", v, "negativo")
    v, _ = detect_rp_status(t)
    check("rp_triplo_neg", v, "negativo")

    t = normalize("triplo-negativos para RE; RP e HER-2")
    v, _ = detect_re_status(t)
    check("re_triplo_neg_hyphen", v, "negativo")

    # --- RE_PCT feminine + intensity qualifier ---
    t = normalize("Receptor de Estrogenio (EP1): Positiva forte em 80%")
    v, _ = detect_re_pct(t)
    check("re_pct_positiva_forte_80", v, 80)

    t = normalize("RE (6F11): fraco positivo em 20% das celulas")
    v, _ = detect_re_pct(t)
    check("re_pct_fraco_positivo_20", v, 20)

    t = normalize("Receptor de Estrogenio (EP1): Positivo (moderada intensidade) em 30%")
    v, _ = detect_re_pct(t)
    check("re_pct_moderada_intensidade_30", v, 30)

    t = normalize("RE (6F11): positivo (positivo em 100% das celulas)")
    v, _ = detect_re_pct(t)
    check("re_pct_double_positivo_100", v, 100)

    t = normalize("Receptor de Estrogenio (EP1): Positiva fraca em raras celulas (cerca de 5%)")
    v, _ = detect_re_pct(t)
    check("re_pct_positiva_fraca_5", v, 5)

    t = normalize("RE (6F11): Positivo nas celulas neoplasicas (em 90% das celulas)")
    v, _ = detect_re_pct(t)
    check("re_pct_nas_celulas_90", v, 90)

    # --- RE_ALLRED ---
    t = normalize("RE  (6F11): Positivo em 90% das células (Escore de Allred: 6)")
    v, _ = detect_re_allred(t)
    check("re_allred_6", v, 6)

    t = normalize("RE  (6F11): Positivo em 100% das células (Escore de Allred:8)")
    v, _ = detect_re_allred(t)
    check("re_allred_8_nospace", v, 8)

    t = normalize("RE  (6F11): Negativo (controle interno positivo) (Escore de Allred: 0)")
    v, _ = detect_re_allred(t)
    check("re_allred_0", v, 0)

    t = normalize("RE  (6F11): Positivo em 80% das células")
    v, _ = detect_re_allred(t)
    check("re_allred_absent", v, None)

    # --- RP ---
    t = normalize("RP (16): Positivo")
    v, _ = detect_rp_status(t)
    check("rp_table_pos", v, "positivo")

    t = normalize("RP (16): Negativo")
    v, _ = detect_rp_status(t)
    check("rp_table_neg", v, "negativo")

    t = normalize("RECEPTOR DE PROGESTERONA-POSITIVO E APRESENTANDO ESCORE 0")
    v, _ = detect_rp_status(t)
    check("rp_concl_pos", v, "positivo")

    t = normalize("RECEPTOR DE PROGESTERONA-NEGATIVO E COM EXPRESSÃO NEGATIVA")
    v, _ = detect_rp_status(t)
    check("rp_concl_neg", v, "negativo")

    t = normalize("RECEPTOR DE PROGESTERONA-NEGATIVA E COM EXPRESSÃO NEGATIVA")
    v, _ = detect_rp_status(t)
    check("rp_concl_negativa_fem", v, "negativo")

    t = normalize("NEGATIVO PARA RECEPTORES DE PROGESTERONA E ESCORE 2+")
    v, _ = detect_rp_status(t)
    check("rp_neg_para_receptores", v, "negativo")

    t = normalize("NEGATIVO PARA RECEPTOR DE PROGESTERONA E NEGATIVA (ESCORE 0) PARA HER2")
    v, _ = detect_rp_status(t)
    check("rp_neg_para_receptor_sing", v, "negativo")

    # --- RP multi-word clone / missing colon ---
    t = normalize("RP (PGR 636): Positivo em 30%")
    v, _ = detect_rp_status(t)
    check("rp_multiword_clone_pos", v, "positivo")

    t = normalize("RP (PGR-636): Negativo")
    v, _ = detect_rp_status(t)
    check("rp_multiword_clone_neg", v, "negativo")

    t = normalize("Receptor de Progesterona (PGR 636): Positivo em 50%")
    v, _ = detect_rp_status(t)
    check("rp_fullname_multiword_clone", v, "positivo")

    t = normalize("Receptor de Progesterona (16) Positivo em 50%")
    v, _ = detect_rp_status(t)
    check("rp_fullname_no_colon", v, "positivo")

    t = normalize("Receptor de Progesterona 16: Positiva em 50%")
    v, _ = detect_rp_status(t)
    check("rp_clone_without_parens", v, "positivo")

    t = normalize("RP (PGR 636): Positivo em 30% das células")
    v, _ = detect_rp_pct(t)
    check("rp_pct_multiword_clone", v, 30)

    # --- RP_PCT ---
    t = normalize("RP (16): Positivo em 30% das células (Escore de Allred: 5)")
    v, _ = detect_rp_pct(t)
    check("rp_pct_30", v, 30)

    t = normalize("RP (16): Positivo em  1% das células (Escore de Allred: 3)")
    v, _ = detect_rp_pct(t)
    check("rp_pct_1", v, 1)

    t = normalize("RP (16): Positivo em 100% das células (Escore de Allred: 8)")
    v, _ = detect_rp_pct(t)
    check("rp_pct_100", v, 100)

    t = normalize("RP (16): Negativo")
    v, _ = detect_rp_pct(t)
    check("rp_pct_neg_0", v, 0)

    t = normalize("RECEPTOR DE PROGESTERONA-POSITIVO")
    v, _ = detect_rp_pct(t)
    check("rp_pct_concl_none", v, None)

    # --- RP_ALLRED ---
    t = normalize("RP (16): Positivo em 30% das células (Escore de Allred: 5)")
    v, _ = detect_rp_allred(t)
    check("rp_allred_5", v, 5)

    t = normalize("RP (16): Negativo")
    v, _ = detect_rp_allred(t)
    check("rp_allred_absent", v, None)

    # --- HER2 score ---
    t = normalize("HER2 / c-erb B-2 (SP3): Escore 3+ (Positivo)")
    v, _ = detect_her2_score(t)
    check("her2_table_3plus", v, "3+")

    t = normalize("HER2 / c-erb B-2 (SP3): Escore 0 (Negativo)")
    v, _ = detect_her2_score(t)
    check("her2_table_0", v, "0")

    t = normalize("HER2 / c-erb B-2 (SP3): Escore 2+ (Duvidoso)")
    v, _ = detect_her2_score(t)
    check("her2_table_2plus", v, "2+")

    t = normalize("HER2 / c-erb B-2 (SP3): Escore 0+ (Negativo)")
    v, _ = detect_her2_score(t)
    check("her2_table_0plus", v, "0+")

    t = normalize("APRESENTANDO ESCORE 3+ PARA EXPRESSÃO PROTÉICA DE HER2")
    v, _ = detect_her2_score(t)
    check("her2_concl_3plus", v, "3+")

    t = normalize("APRESENTANDO ESCORE 0 PARA EXPRESSÃO PROTÉICA DE HER2")
    v, _ = detect_her2_score(t)
    check("her2_concl_0", v, "0")

    t = normalize("COM EXPRESSÃO NEGATIVA DOS PRODUTOS DO HER2")
    v, _ = detect_her2_score(t)
    check("her2_concl_neg_no_score", v, None)  # no score in this format

    # New HER2 patterns (iter 2)
    t = normalize("HER2 / c-erb B-2 (SP3): Escore +3 (Positivo)")
    v, _ = detect_her2_score(t)
    check("her2_table_inverted_3plus", v, "3+")

    t = normalize('HER2 / c-erb B-2 (SP3): Escore "0" (Negativo)')
    v, _ = detect_her2_score(t)
    check("her2_table_quoted_0", v, "0")

    t = normalize('HER2 / c-erb B-2 (SP3): Escore "0"   (Negativo)')
    v, _ = detect_her2_score(t)
    check("her2_table_quoted_0_spaces", v, "0")

    t = normalize("HER-2 (SP3): \tpositivo 3+")
    v, _ = detect_her2_score(t)
    check("her2_table_variant_3plus", v, "3+")

    t = normalize("COM SUPEREXPRESSÃO PROTÉICA DE HER2 (ESCORE 3+)")
    v, _ = detect_her2_score(t)
    check("her2_concl_paren_3plus", v, "3+")

    t = normalize("NEGATIVA (ESCORE 0) PARA HER2")
    v, _ = detect_her2_score(t)
    check("her2_concl_neg_paren_0", v, "0")

    t = normalize("ESCORE 2+ (DUVIDOSO) PARA HER2")
    v, _ = detect_her2_score(t)
    check("her2_concl_duvidoso_2plus", v, "2+")

    t = normalize("COM EXPRESSÃO DUVIDOSA DOS PRODUTOS DO HER2")
    v, _ = detect_her2_score(t)
    check("her2_concl_duvidosa_implies_2plus", v, "2+")

    # Reference section contamination guard — should NOT match reference text
    t = normalize("(2) Em casos de resultados duvidosos (Escore 2+) para o Produto do Oncogene c-erbB-2(HER2) é recomendado o Teste")
    v, _ = detect_her2_score(t)
    check("her2_reference_guard_blocks", v, None)  # reference guard should block this

    t = normalize("(1) Quantificação do RE, RP e HER-2 conforme orientações da ASCO/CAP")
    v, _ = detect_her2_score(t)
    check("her2_reference_asco_cap", v, None)  # no score here anyway

    # iter 5: HER-2 (clone) without c-erb prefix
    t = normalize("[A1] HER-2 (SP3): Escore 0 (Negativo)")
    v, _ = detect_her2_score(t)
    check("her2_no_cerb_escore0", v, "0")

    # iter 5: "positivo (escore 3+)" — score inside parens
    t = normalize("[A1] HER-2 (SP3): positivo (escore 3+)")
    v, _ = detect_her2_score(t)
    check("her2_pos_escore_parens_3plus", v, "3+")

    t = normalize("HER-2 (SP3): negativo (escore 0)")
    v, _ = detect_her2_score(t)
    check("her2_neg_escore_parens_0", v, "0")

    # iter 5: no colon
    t = normalize("HER-2 (SP3) positivo (escore 3+)")
    v, _ = detect_her2_score(t)
    check("her2_no_colon_pos_3plus", v, "3+")

    t = normalize("HER-2 (SP3) negativo (escore 0)")
    v, _ = detect_her2_score(t)
    check("her2_no_colon_neg_0", v, "0")

    # iter 6: HER-2 (clone): (0+) Negativo — score in parens before status
    t = normalize("HER-2 (SP3): (0+) Negativo")
    v, _ = detect_her2_score(t)
    check("her2_paren_score_before_status", v, "0+")

    # iter 6: simple "HER-2: N+ (status)" without clone
    t = normalize("HER-2: 0+ (Negativo)")
    v, _ = detect_her2_score(t)
    check("her2_simple_colon_0plus", v, "0+")

    t = normalize("HER-2: 3+ (Positivo)")
    v, _ = detect_her2_score(t)
    check("her2_simple_colon_3plus", v, "3+")

    # iter 6: "HER-2: negativo (0+)"
    t = normalize("HER-2: Negativo (0+)")
    v, _ = detect_her2_score(t)
    check("her2_simple_status_score_parens", v, "0+")

    # iter 7: HER-2 (clone): duvidoso (2+)
    t = normalize("HER-2 (SP3): duvidoso (2+)")
    v, _ = detect_her2_score(t)
    check("her2_duvidoso_2plus", v, "2+")

    t = normalize("HER-2 (SP3): \tduvidoso (2+)")
    v, _ = detect_her2_score(t)
    check("her2_duvidoso_tab_2plus", v, "2+")

    # iter 8: HER2 score with extra text in parens — "negativo (escore 1+ segundo ASCO/CAP)"
    t = normalize("HER-2 (SP3): Negativo (escore 1+ segundo critérios ASCO/CAP)")
    v, _ = detect_her2_score(t)
    check("her2_escore_extended_1plus", v, "1+")

    t = normalize("HER-2 (SP3): Negativo (escore 0 segundo ASCO/CAP)")
    v, _ = detect_her2_score(t)
    check("her2_escore_extended_0", v, "0")

    t = normalize("HER-2 (SP3) Positivo (escore 3+ conforme ASCO/CAP)")
    v, _ = detect_her2_score(t)
    check("her2_escore_extended_3plus_no_colon", v, "3+")

    # iter 8: HER2 score with quotes — (escore "0") / (escore ''0'')
    t = normalize('HER-2 (SP3): Negativo (escore "0")')
    v, _ = detect_her2_score(t)
    check("her2_escore_quoted_0", v, "0")

    t = normalize("HER-2 (SP3): Negativo (escore ''0'')")
    v, _ = detect_her2_score(t)
    check("her2_escore_doublequoted_0", v, "0")

    # iter 8: HER2 N+ before status — "3+ (positivo)" / "0+ negativo"
    t = normalize("HER-2 (SP3): 3+ (Positivo)")
    v, _ = detect_her2_score(t)
    check("her2_score_before_status_3plus", v, "3+")

    t = normalize("HER-2 (SP3): 0+ (Negativo)")
    v, _ = detect_her2_score(t)
    check("her2_score_before_status_0plus", v, "0+")

    t = normalize("HER-2 (SP3): 0+ Negativo")
    v, _ = detect_her2_score(t)
    check("her2_score_before_status_no_paren", v, "0+")

    # iter 8: HER2 "escore + N" inverted with space
    t = normalize("HER2 / c-erb B-2 (SP3): Escore + 1 (Negativo)")
    v, _ = detect_her2_score(t)
    check("her2_escore_plus_space_1", v, "1+")

    t = normalize("HER-2 (SP3): Escore + 3 (Positivo)")
    v, _ = detect_her2_score(t)
    check("her2_escore_plus_space_3", v, "3+")

    # iter 5: Ki67 "menos de X%"
    t = normalize("Ki-67 / MIB-1 (MM1): Positivo em menos de 5% das células")
    v, _ = detect_ki67_pct(t)
    check("ki67_menos_de_5", v, 5)

    t = normalize("Ki-67 / MIB-1 (MM1): Positivo em menos de 10% das células neoplásicas")
    v, _ = detect_ki67_pct(t)
    check("ki67_menos_de_10", v, 10)

    t = normalize("Ki-67 / MIB-1 (MM1): Positivo em menos de 1% das células")
    v, _ = detect_ki67_pct(t)
    check("ki67_menos_de_1", v, 1)

    # --- HER2 status ---
    v, _ = derive_her2_status('3+', normalize("escore 3+"))
    check("her2_status_3plus", v, "positivo")

    v, _ = derive_her2_status('2+', normalize("escore 2+"))
    check("her2_status_2plus", v, "equivoco")

    v, _ = derive_her2_status('0', normalize("escore 0"))
    check("her2_status_0", v, "negativo")

    v, _ = derive_her2_status('1+', normalize("escore 1+"))
    check("her2_status_1plus", v, "negativo")

    v, _ = derive_her2_status(None, normalize("COM EXPRESSÃO NEGATIVA DOS PRODUTOS DO HER2"))
    check("her2_status_from_text_neg", v, "negativo")

    v, _ = derive_her2_status(None, normalize("COM EXPRESSÃO POSITIVA DOS PRODUTOS DO HER2"))
    check("her2_status_from_text_pos", v, "positivo")

    # iter 6: expressão duvidosa → equivoco
    v, _ = derive_her2_status(None, normalize("neoplasia demonstrou expressão duvidosa do HER2"))
    check("her2_status_expressao_duvidosa", v, "equivoco")

    # iter 6: superexpressão → positivo
    v, _ = derive_her2_status(None, normalize("observamos superexpressão de HER2"))
    check("her2_status_superexpressao_pos", v, "positivo")

    # iter 6: sem superexpressão → negativo
    v, _ = derive_her2_status(None, normalize("sem superexpressão de HER2"))
    check("her2_status_sem_superexp_neg", v, "negativo")

    # iter 6: simple HER-2: negativo
    v, _ = derive_her2_status(None, normalize("HER-2: Negativo"))
    check("her2_status_simple_colon_neg", v, "negativo")

    # iter 6: triplo negativo → HER2 negativo
    v, _ = derive_her2_status(None, normalize("carcinoma mamário triplo negativo"))
    check("her2_status_triplo_neg", v, "negativo")

    v, _ = derive_her2_status(None, normalize("triplo-negativos para RE, RP e HER-2"))
    check("her2_status_triplo_neg_hyphen", v, "negativo")

    # --- Ki67 ---
    t = normalize("Ki-67 / MIB-1 (MM1): Positivo em 30% das células neoplásicas")
    v, _ = detect_ki67_pct(t)
    check("ki67_table_30", v, 30)

    t = normalize("Ki-67 / MIB-1 (MM1): Positivo em 20 a 30% das células neoplásicas")
    v, _ = detect_ki67_pct(t)
    check("ki67_table_range_25", v, 25)

    t = normalize("Ki-67 / MIB-1 (SP6): Positivo em 80 a 90% das células neoplásicas")
    v, _ = detect_ki67_pct(t)
    check("ki67_table_range_85", v, 85)

    t = normalize("Ki-67 / MIB-1 (MM1): Positivo em 5% das células neoplásicas")
    v, _ = detect_ki67_pct(t)
    check("ki67_table_5", v, 5)

    t = normalize("APRESENTANDO ELEVADO ÍNDICE DE PROLIFERAÇÃO CELULAR")
    v, _ = detect_ki67_pct(t)
    check("ki67_qualitative_null", v, None)  # qualitative → no number

    t = normalize("ÍNDICE DE PROLIFERAÇÃO CELULAR DE 40%")
    v, _ = detect_ki67_pct(t)
    check("ki67_concl_40", v, 40)

    t = normalize("FIBROADENOMA SEM ATIPIA")
    v, _ = detect_ki67_pct(t)
    check("ki67_absent", v, None)

    # --- Ki67 with range using "e" instead of "a"
    t = normalize("Ki-67 / MIB-1 (SP6): Positivo em 30% e 50% das células neoplásicas")
    v, _ = detect_ki67_pct(t)
    check("ki67_table_range_e_40", v, 40)

    # --- Ki67 range "30% à 40%"
    t = normalize("Ki-67 / MIB-1 (MM1): Positivo em 30% à 40% das células neoplásicas")
    v, _ = detect_ki67_pct(t)
    check("ki67_table_range_accent_35", v, 35)

    # R10 Decision 6: "mais de X%" → X (not X+5)
    t = normalize("Ki-67 / MIB-1 (MM1): Positivo em mais de 50% das células neoplásicas")
    v, ev = detect_ki67_pct(t)
    check("ki67_mais_de_50_returns_50", v, 50)  # was 55 pre-R10
    check("ki67_mais_de_50_is_lower_bound", bool(re.search(r'mais\s+de', ev)), True)

    t = normalize("Ki-67 / MIB-1 (MM1): Positivo em mais de 30% das células")
    v, _ = detect_ki67_pct(t)
    check("ki67_mais_de_30_returns_30", v, 30)  # was 35 pre-R10

    # iter 6: "proliferação celular ao Ki67 de X a Y%"
    t = normalize("apresentando índice de proliferação celular ao Ki67 de 40 a 50%")
    v, _ = detect_ki67_pct(t)
    check("ki67_prolif_ao_ki67_range_45", v, 45)

    t = normalize("índice de proliferação celular ao Ki67 de 40%")
    v, _ = detect_ki67_pct(t)
    check("ki67_prolif_ao_ki67_40", v, 40)

    # iter 6: "proliferação celular de X a Y% ao ki67"
    t = normalize("índice de proliferação celular de 60 a 70% ao Ki67")
    v, _ = detect_ki67_pct(t)
    check("ki67_prolif_range_ao_ki67_65", v, 65)

    # iter 6: feminine "positiva em X%"
    t = normalize("Ki-67 (MIB-1): Positiva em 30% das células")
    v, _ = detect_ki67_pct(t)
    check("ki67_positiva_fem_30", v, 30)

    # iter 6: dash range "X-Y%"
    t = normalize("Ki-67 (MIB-1): Positivo em 2-3% das células")
    v, _ = detect_ki67_pct(t)
    check("ki67_dash_range_2", v, 2)  # midpoint of 2-3

    t = normalize("Ki-67 (MIB-1): Positiva em 5-8% das células")
    v, _ = detect_ki67_pct(t)
    check("ki67_dash_range_fem_6", v, 6)  # midpoint of 5-8

    # iter 6: "positivo em de X a Y%"
    t = normalize("Ki-67 / MIB-1 (MM1): Positivo em de 5 a 30% das células neoplásicas")
    v, _ = detect_ki67_pct(t)
    check("ki67_em_de_range_17", v, 17)  # midpoint of 5-30

    # iter 8: "positivo N%" without "em"
    t = normalize("Ki-67 (MIB1): Positivo 60% das células")
    v, _ = detect_ki67_pct(t)
    check("ki67_positivo_no_em_60", v, 60)

    t = normalize("Ki-67 (antigeno de proliferação celular) (SP6): Positivo 5%")
    v, _ = detect_ki67_pct(t)
    check("ki67_positivo_no_em_long_clone_5", v, 5)

    # iter 8: "positivo N% a N%" range without "em"
    t = normalize("Ki67 (SP6): Positivo 80% a 90% das células neoplásicas")
    v, _ = detect_ki67_pct(t)
    check("ki67_positivo_no_em_range_85", v, 85)  # midpoint

    # iter 8: "positivo em ate N%"
    t = normalize("Ki-67 (MIB-1): Positiva em até 20% das células")
    v, ev = detect_ki67_pct(t)
    check("ki67_ate_20", v, 20)

    t = normalize("Ki67 (MIB-1): Positivo em até 5% das células")
    v, _ = detect_ki67_pct(t)
    check("ki67_ate_5", v, 5)

    t = normalize("Ki67 (MIB-1): Positivo com areas de até 20% das células estromais")
    v, _ = detect_ki67_pct(t)
    check("ki67_ate_com_areas_20", v, 20)

    # iter 8: "positivo em cerca de N%"
    t = normalize("Ki-67 / MIB-1 (MM1): Positivo em cerca de 5% das células neoplásicas")
    v, _ = detect_ki67_pct(t)
    check("ki67_cerca_de_5", v, 5)

    t = normalize("Ki 67 (MIB -1) Positivo cerca de 30% das células")
    v, _ = detect_ki67_pct(t)
    check("ki67_cerca_de_no_em_30", v, 30)

    # iter 8: "cerca de N% a N%" range
    t = normalize("Ki-67 (MIB-1): reação positiva em cerca de 3% a 5% das células")
    v, _ = detect_ki67_pct(t)
    check("ki67_cerca_de_range_4", v, 4)  # midpoint 3-5

    # iter 8: "positivo, cerca de N%"
    t = normalize("Ki-67 (MIB-1): Positivo, cerca de 80%")
    v, _ = detect_ki67_pct(t)
    check("ki67_cerca_de_comma_80", v, 80)

    # iter 8: "positivo entre N e N%"
    t = normalize("Ki-67 (MIB-1): Positivo entre 1 e 5% das células")
    v, _ = detect_ki67_pct(t)
    check("ki67_entre_3", v, 3)  # midpoint 1-5

    # iter 8: "positivo em < N%"
    t = normalize("Ki-67 / MIB-1 (MM1): Positivo em < 5% das células neoplásicas")
    v, _ = detect_ki67_pct(t)
    check("ki67_lt_5", v, 5)

    # iter 8: "menos N%" without "de"
    t = normalize("Ki-67 / MIB-1 (MM1): Positivo em menos 10% das células neoplásicas")
    v, _ = detect_ki67_pct(t)
    check("ki67_menos_no_de_10", v, 10)

    t = normalize("Ki-67 / MIB-1 (MM1): Positivo em menos 1% das células neoplásicas")
    v, _ = detect_ki67_pct(t)
    check("ki67_menos_no_de_1", v, 1)

    # iter 8: conclusion "proliferação celular ao ki-67 de ate N%"
    t = normalize("índice de proliferação celular ao Ki-67 de até 10%")
    v, _ = detect_ki67_pct(t)
    check("ki67_prolif_ao_ki67_ate_10", v, 10)

    t = normalize("índice de proliferação celular ao Ki-67 de até 30%")
    v, _ = detect_ki67_pct(t)
    check("ki67_prolif_ao_ki67_ate_30", v, 30)

    # iter 8: conclusion "proliferação celular ao ki-67 de cerca de N%"
    t = normalize("índice de proliferação celular ao Ki-67 de cerca de 25%")
    v, _ = detect_ki67_pct(t)
    check("ki67_prolif_ao_ki67_cerca_25", v, 25)

    # iter 8: standalone "ao ki-67 de ate N%" — without "proliferação celular"
    t = normalize("índice proliferativo ao Ki-67 de até 10%")
    v, _ = detect_ki67_pct(t)
    check("ki67_proliferativo_ao_ki67_ate_10", v, 10)

    t = normalize("indice proliferativo ao Ki-67 de até 60%")
    v, _ = detect_ki67_pct(t)
    check("ki67_proliferativo_ao_ki67_ate_60", v, 60)

    # iter 8: long clone "reação positiva em cerca de"
    t = normalize("Ki-67 (marcador de proliferação celular) (MIB-1): reação positiva em cerca de 3% a 5% das células")
    v, _ = detect_ki67_pct(t)
    check("ki67_long_clone_cerca_range_4", v, 4)  # midpoint 3-5

    # iter 8: "ki67, variando de N a N%"
    t = normalize("expressão de Ki67, variando de 10 a 40% das células")
    v, _ = detect_ki67_pct(t)
    check("ki67_variando_range_25", v, 25)  # midpoint 10-40

    t = normalize("Ki-67 tem padrão heterogêneo nesta amostra, variando de 40 a 80%")
    v, _ = detect_ki67_pct(t)
    check("ki67_variando_range_60", v, 60)  # midpoint 40-80

    # iter 8: "ki67 (N%)" parenthesized
    t = normalize("3+); Ki67 (30%)")
    v, _ = detect_ki67_pct(t)
    check("ki67_paren_30", v, 30)

    # iter 8: "ki67 de N%"
    t = normalize(", e Ki67 de 10%.")
    v, _ = detect_ki67_pct(t)
    check("ki67_de_10", v, 10)

    # iter 8: "ki67 (clone): N%" — clone in between
    t = normalize("Ki67 (MM1): 20% das células neoplásicas")
    v, _ = detect_ki67_pct(t)
    check("ki67_clone_colon_no_positivo_20", v, 20)

    # iter 8: reference table should NOT match
    t = normalize("Ki-67<14% luminal b RE + e/ou RP +, HER2- e Ki67 > ou = 14%")
    v, _ = detect_ki67_pct(t)
    check("ki67_reference_table_null", v, None)

    # iter 8: "positivo em N das celulas" — missing % sign
    t = normalize("Ki-67 (MIB-1): Positivo em 30 das células neoplásicas")
    v, _ = detect_ki67_pct(t)
    check("ki67_missing_pct_sign_30", v, 30)

    # iter 8: "N% ao ki-67" — percentage precedes marker
    t = normalize("índice de proliferação celular de 20 a 30% ao Ki-67")
    v, _ = detect_ki67_pct(t)
    check("ki67_pct_ao_ki67_range_25", v, 25)  # midpoint 20-30

    # iter 8: "N% ao ki-67" — single value
    t = normalize("proliferação celular de 40% ao Ki-67")
    v, _ = detect_ki67_pct(t)
    check("ki67_pct_ao_ki67_single_40", v, 40)

    # --- tipo_hist_ihq ---
    t = normalize("CARCINOMA DUCTAL INVASIVO DE MAMA DIREITA, GRAU II")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_cdi", v, "CDI_SOE")

    t = normalize("CARCINOMA LOBULAR INVASIVO ÀS 4H DE MAMA ESQUERDA")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_cli", v, "CLI")

    t = normalize("CARCINOMA DUCTAL IN SITU (CDIS) DE MAMA ESQUERDA")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_cdis", v, "CDIS")

    t = normalize("CARCINOMA INVASIVO DA MAMA, DO TIPO NÃO ESPECIAL")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_nst", v, "CDI_SOE")

    t = normalize("CARCINOMA MAMÁRIO INVASIVO SEM TIPO ESPECIAL")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_sem_tipo", v, "CDI_SOE")

    t = normalize("CARCINOMA MICROINVASIVO DE MAMA")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_microinv", v, "CDI_SOE")

    # Bug fix iter 5: "ductal microinvasivo" (ductal between carcinoma and microinvasivo)
    t = normalize("CARCINOMA DUCTAL MICROINVASIVO COM COMPONENTE 'IN SITU' ASSOCIADO")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_ductal_microinv", v, "CDI_SOE")

    t = normalize("CARCINOMA DUCTAL MICROINVASIVO DE MAMA ESQUERDA, COM COMPONENTE DE CARCINOMA INTRADUCTO (CDIS)")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_ductal_microinv2", v, "CDI_SOE")

    # Bug fix iter 5: "TIPO NÃO-ESPECIAL" (hyphen)
    t = normalize("CARCINOMA MAMÁRIO INVASIVO DE TIPO NÃO-ESPECIAL, RESIDUAL PÓS-QUIMIOTERAPIA")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_nst_hyphen", v, "CDI_SOE")

    # Bug fix iter 5: CLIS (lobular in situ)
    t = normalize("CARCINOMA LOBULAR 'IN SITU', RECEPTOR DE ESTRÓGENO-POSITIVO")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_clis", v, "CLIS")

    t = normalize('CARCINOMA LOBULAR "IN SITU" DE MAMA DIREITA')
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_clis_dquote", v, "CLIS")

    # Iter 7: carcinoma intraductal/intraducto → CDIS
    t = normalize("CARCINOMA INTRADUCTO DE MAMA DIREITA, ALTO GRAU COM NECROSE, RECEPTOR DE ESTRÓGENO-NEGATIVO")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_intraducto", v, "CDIS")

    t = normalize("CARCINOMA INTRADUCTAL (DUCTAL 'INSITU'), TIPO CRIBRIFORME, DE GRAU NUCLEAR INTERMEDIÁRIO")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_intraductal", v, "CDIS")

    # Iter 7: carcinoma mamario invasivo → CDI_SOE
    t = normalize("CARCINOMA MAMARIO INVASIVO, TIPO MISTO (DUCTAL - 80% E CRIBRIFORME), GRAU 1 DE NOTTINGHAM")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_mamario_inv", v, "CDI_SOE")

    t = normalize("CARCINOMA MAMARIO INVASIVO BEM DIFERENCIADO DE GRAU 1 MBR, PODENDO CORRESPONDER AO CARCINOMA PAPILAR")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_mamario_inv2", v, "CDI_SOE")

    # Iter 7: carcinoma mamario invasivo lobular → CLI (not CDI_SOE)
    t = normalize("CARCINOMA MAMARIO INVASIVO LOBULAR INVASIVO, VARIANTE PLEOMÓRFICO DE MAMA DIREITA")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_mamario_inv_lobular", v, "CLI")

    # Iter 7: carcinoma de ductos mamários → CDI_SOE
    t = normalize("CARCINOMA DE DUCTOS MAMÁRIOS DE MAMA ESQUERDA GRAU NUCLEAR INTERMEDIÁRIO, RECEPTOR DE ESTRÓGENO-POSITIVO")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_ductos_mamarios", v, "CDI_SOE")

    # Iter 7: carcinoma mamario ductal → CDI_SOE
    t = normalize("CARCINOMA MAMÁRIO DUCTAL METASTÁTICO PARA ENCÉFALO")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_mamario_ductal", v, "CDI_SOE")

    # Iter 7: carcinoma coloide → mucinoso
    t = normalize("CARCINOMA COLÓIDE (MUCINOSO), COMPATÍVEL COM GRAU 2 DE NOTTINGHAM, RECEPTOR DE ESTRÓGENO-POSITIVO")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_coloide", v, "mucinoso")

    # Iter 7: carcinoma ductal alone (without in situ/invasivo) → CDI_SOE
    t = normalize("CARCINOMA DUCTAL METASTÁTICO PARA LINFONODO AXILAR, RECEPTOR DE ESTRÓGENO-POSITIVO")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_ductal_meta", v, "CDI_SOE")

    t = normalize("CARCINOMA DUCTAL DE MAMA METASTÁTICO PARA LINFONODO AXILAR DIREITO")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_ductal_de_mama", v, "CDI_SOE")

    # Iter 7b: carcinoma lobular pleomorfico infiltrante → CLI
    t = normalize("CARCINOMA LOBULAR PLEOMÓRFICO INFILTRANTE DE MAMA ESQUERDA, GRAU III")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_lobular_pleo_inf", v, "CLI")

    # Iter 7b: carcinoma lobular de mama → CLI (implies invasive in IHQ context)
    t = normalize("CARCINOMA LOBULAR DE MAMA COMO SÍTIO PRIMÁRIO MAIS PROVÁVEL")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_lobular_de_mama", v, "CLI")

    # Iter 7b: carcinoma papilar invasivo → papilar
    t = normalize("CARCINOMA PAPILAR INVASIVO EM MAMA DIREITA, RECEPTOR DE ESTRÓGENO-POSITIVO")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_papilar_inv", v, "papilar")

    # Iter 7b: carcinoma papilar em mama → papilar
    t = normalize("CARCINOMA PAPILAR EM MAMA ESQUERDA, RECEPTOR DE ESTRÓGENO-POSITIVO")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_papilar_mama", v, "papilar")

    # Iter 7b: carcinoma ductal, alto grau → CDI_SOE (comma after ductal)
    t = normalize("CARCINOMA DUCTAL, ALTO GRAU NUCLEAR, EM MAMA ESQUERDA, RECEPTOR DE ESTRÓGENO-NEGATIVO")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_ductal_comma", v, "CDI_SOE")

    # Iter 7b: CDIS with double single quotes
    t = normalize("CARCINOMA DUCTAL ''IN SITU'' DE ALTO GRAU NUCLEAR, RECEPTOR DE ESTRÓGENO-POSITIVO")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_cdis_double_quote", v, "CDIS")

    # Iter 7b: CDIS with negated invasive in comment — should still be CDIS
    t = normalize("CARCINOMA DUCTAL 'IN SITU' DE MAMA. COMENTÁRIO: A CONDIÇÃO NÃO-INVASIVA FOI CONFIRMADA")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_cdis_nao_invasiva", v, "CDIS")

    # Iter 7b: CDIS with microinvasive → NOT CDIS (has real invasive component)
    t = normalize("CARCINOMA DUCTAL 'IN SITU' COM COMPONENTE MICROINVASIVO ASSOCIADO")
    v, _ = detect_tipo_hist_ihq(t)
    check("tipo_ihq_cdis_with_microinv", v, "CDI_SOE")

    # --- subtipo_molecular ---
    check("sub_luminal_a", derive_subtipo_molecular('positivo', 'positivo', 'negativo', 10), 'luminal_a')
    check("sub_luminal_b_her2neg", derive_subtipo_molecular('positivo', 'negativo', 'negativo', 30), 'luminal_b_her2neg')
    check("sub_luminal_b_her2pos", derive_subtipo_molecular('positivo', 'positivo', 'positivo', 20), 'luminal_b_her2pos')
    check("sub_her2_enriched", derive_subtipo_molecular('negativo', 'negativo', 'positivo', 60), 'her2_enriched')
    check("sub_triplo_neg", derive_subtipo_molecular('negativo', 'negativo', 'negativo', 80), 'triplo_negativo')
    check("sub_luminal_no_ki67", derive_subtipo_molecular('positivo', 'positivo', 'negativo', None), 'luminal')
    check("sub_none", derive_subtipo_molecular(None, None, None, None), None)
    check("sub_hr_neg_no_her2", derive_subtipo_molecular('negativo', 'negativo', None, None), None)
    # R10 Decision 3: HER2 equivoco → None (not 'luminal')
    check("sub_hr_pos_her2_equivoco", derive_subtipo_molecular('positivo', 'positivo', 'equivoco', 20), None)
    check("sub_hr_pos_her2_missing", derive_subtipo_molecular('positivo', 'negativo', None, 15), None)
    check("sub_hr_neg_her2_equivoco", derive_subtipo_molecular('negativo', 'negativo', 'equivoco', 50), None)

    # --- Standalone wrappers ---
    t = normalize("HER2 / c-erb B-2 (SP3): Escore 3+ (Positivo)")
    v, _ = detect_her2_status_standalone(t)
    check("her2_status_standalone_3plus", v, "positivo")

    t = normalize("HER2 / c-erb B-2 (SP3): Escore 2+ (Equivoco)")
    v, _ = detect_her2_status_standalone(t)
    check("her2_status_standalone_2plus", v, "equivoco")

    t = normalize("COM EXPRESSAO NEGATIVA DOS PRODUTOS DO HER2")
    v, _ = detect_her2_status_standalone(t)
    check("her2_status_standalone_neg_text", v, "negativo")

    t = normalize("RE (6F11): Positivo em 90% das celulas\nRP (16): Positivo em 60%\nHER2 / c-erb B-2 (SP3): Escore 0 (Negativo)\nKi-67 / MIB-1 (MM1): Positivo em 5% das celulas neoplasicas.")
    v, _ = detect_subtipo_molecular_standalone(t)
    check("subtipo_standalone_lumA", v, "luminal_a")

    t = normalize("CARCINOMA DUCTAL INFILTRANTE, TRIPLO-NEGATIVO")
    v, _ = detect_subtipo_molecular_standalone(t)
    check("subtipo_standalone_tn", v, "triplo_negativo")

    t = normalize("RE (6F11): Positivo em 80%\nRP (16): Positivo em 40%\nHER2 / c-erb B-2 (SP3): Escore 3+ (Positivo)\nKi-67 / MIB-1 (MM1): Positivo em 35%")
    v, _ = detect_subtipo_molecular_standalone(t)
    check("subtipo_standalone_lumB_her2pos", v, "luminal_b_her2pos")

    print(f"\n  Smoke tests: {total - failures} passed, {failures} failed\n")
    return failures


# ===========================================================================
# STANDALONE WRAPPERS — for DDPA runner (interface: tuple)
# ===========================================================================

def detect_her2_status_standalone(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Wrapper for runner: detects HER2 status from text alone.
    Internally calls detect_her2_score then derive_her2_status.
    """
    score, _ = detect_her2_score(text_norm)
    return derive_her2_status(score, text_norm)


def detect_subtipo_molecular_standalone(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Wrapper for runner: derives molecular subtype from text alone.
    Re-extracts RE, RP, HER2, Ki-67 internally. Includes CDIS guard.
    """
    re_val, _ = detect_re_status(text_norm)
    rp_val, _ = detect_rp_status(text_norm)
    her2_score_val, _ = detect_her2_score(text_norm)
    her2_status_val, _ = derive_her2_status(her2_score_val, text_norm)
    ki67_val, _ = detect_ki67_pct(text_norm)
    tipo_val, _ = detect_tipo_hist_ihq(text_norm)

    subtipo = derive_subtipo_molecular(re_val, rp_val, her2_status_val, ki67_val)

    if subtipo and tipo_val in ('CDIS', 'CLIS'):
        has_invasive = bool(re.search(
            r'(?:carcinoma|neoplasia).{0,40}(?:invasiv|infiltrant)',
            text_norm))
        if not has_invasive:
            subtipo = None

    evidence = f're={re_val} rp={rp_val} her2={her2_status_val} ki67={ki67_val}'
    return subtipo, evidence


# ===========================================================================
# RUNNER — Compare L1 vs LLM
# ===========================================================================

def run_comparison(holdout_csv: str, llm_json: str, output_csv: str = None):
    """Run L1 on holdout and compare with LLM."""
    import json, csv

    with open(holdout_csv, 'r', encoding='utf-8') as f:
        texts = {}
        for row in csv.DictReader(f):
            texts[row['numExame']] = row

    with open(llm_json, 'r', encoding='utf-8') as f:
        llm_list = json.load(f)
    llm = {r['numExame']: r for r in llm_list}

    results = []
    vars_to_compare = ['re_status', 'rp_status', 'her2_score', 'her2_status',
                        'ki67_pct', 'tipo_hist_ihq', 'subtipo_molecular']

    stats = {v: {'agree': 0, 'disagree': 0, 'l1_only': 0, 'llm_only': 0,
                 'both_na': 0, 'total': 0, 'disagree_examples': []}
             for v in vars_to_compare}

    for numExame, rec in texts.items():
        if rec['estrato'] == 'nao_mama':
            continue  # skip non-breast

        text_norm = normalize(rec['conclusao'])
        llm_rec = llm.get(numExame, {})

        # Extract
        re_val, re_ev = detect_re_status(text_norm)
        re_pct_val, re_pct_ev = detect_re_pct(text_norm)
        re_allred_val, re_allred_ev = detect_re_allred(text_norm)
        rp_val, rp_ev = detect_rp_status(text_norm)
        rp_pct_val, rp_pct_ev = detect_rp_pct(text_norm)
        rp_allred_val, rp_allred_ev = detect_rp_allred(text_norm)
        her2_score_val, her2_score_ev = detect_her2_score(text_norm)
        her2_status_val, her2_status_ev = derive_her2_status(her2_score_val, text_norm)
        ki67_val, ki67_ev = detect_ki67_pct(text_norm)
        tipo_val, tipo_ev = detect_tipo_hist_ihq(text_norm)
        subtipo_val = derive_subtipo_molecular(re_val, rp_val, her2_status_val, ki67_val)

        # CDIS/CLIS guard (Decision 5, R10): when tipo_hist is pure in-situ
        # (no invasive component), null out subtipo_molecular.
        # RE/RP/HER2/Ki67 are kept — they're clinically relevant for DCIS.
        # But St. Gallen subtipo doesn't apply to in-situ disease.
        is_cdis_only = False
        if tipo_val in ('CDIS', 'CLIS'):
            has_invasive = bool(re.search(
                r'(?:carcinoma|neoplasia).{0,40}(?:invasiv|infiltrant)',
                text_norm))
            if not has_invasive:
                is_cdis_only = True
                subtipo_val = None

        # Normalize her2_score: 0+ → 0 for comparison with LLM
        her2_score_cmp = her2_score_val
        if her2_score_cmp == '0+':
            her2_score_cmp = '0'  # LLM doesn't distinguish 0 vs 0+

        # --- Schema flags (Decision 4,5,6 R10) ---
        # erlow_flag: RE 1-10% or "fraco positivo"
        erlow_flag = False
        if re_val == 'positivo':
            if re_pct_val is not None and 1 <= re_pct_val <= 10:
                erlow_flag = True
            elif re_ev and 'fraco' in re_ev:
                erlow_flag = True

        # ki67_is_lower_bound: approximate/boundary indicator in evidence
        # Covers: "mais de", "menos de/que", "ate", "< N", "cerca de"
        ki67_is_lower_bound = bool(
            ki67_ev and re.search(r'(?:mais|menos)\s+(?:de|que)|\bate\s+\d|<\s*\d|cerca\s+de\s+\d', ki67_ev))

        # her2_resolved: HER2 status is definitively positive or negative
        her2_resolved = her2_status_val in ('positivo', 'negativo')

        row = {
            'numExame': numExame,
            'estrato': rec['estrato'],
            'l1_re_status': re_val or '',
            'l1_re_evidence': re_ev or '',
            'l1_re_pct': str(re_pct_val) if re_pct_val is not None else '',
            'l1_re_pct_evidence': re_pct_ev or '',
            'l1_re_allred': str(re_allred_val) if re_allred_val is not None else '',
            'l1_re_allred_evidence': re_allred_ev or '',
            'l1_rp_status': rp_val or '',
            'l1_rp_evidence': rp_ev or '',
            'l1_rp_pct': str(rp_pct_val) if rp_pct_val is not None else '',
            'l1_rp_pct_evidence': rp_pct_ev or '',
            'l1_rp_allred': str(rp_allred_val) if rp_allred_val is not None else '',
            'l1_rp_allred_evidence': rp_allred_ev or '',
            'l1_her2_score': her2_score_val or '',
            'l1_her2_score_evidence': her2_score_ev or '',
            'l1_her2_status': her2_status_val or '',
            'l1_her2_status_evidence': her2_status_ev or '',
            'l1_ki67_pct': str(ki67_val) if ki67_val is not None else '',
            'l1_ki67_evidence': ki67_ev or '',
            'l1_tipo_hist_ihq': tipo_val or '',
            'l1_tipo_hist_ihq_evidence': tipo_ev or '',
            'l1_subtipo_molecular': subtipo_val or '',
            # Schema flags
            'l1_erlow_flag': erlow_flag,
            'l1_is_cdis_only': is_cdis_only,
            'l1_ki67_is_lower_bound': ki67_is_lower_bound,
            'l1_her2_resolved': her2_resolved,
        }
        results.append(row)

        # Compare with LLM
        l1_vals = {
            're_status': re_val or '',
            'rp_status': rp_val or '',
            'her2_score': her2_score_cmp or '',
            'her2_status': her2_status_val or '',
            'ki67_pct': str(ki67_val) if ki67_val is not None else '',
            'tipo_hist_ihq': tipo_val or '',
            'subtipo_molecular': subtipo_val or '',
        }

        # LLM field mapping (different key names in LLM JSON)
        llm_field_map = {
            're_status': 're_status',
            'rp_status': 'rp_status',
            'her2_score': 'her2_score_raw',
            'her2_status': 'her2_status',
            'ki67_pct': 'ki67_pct_num',
            'tipo_hist_ihq': 'tipo_hist_ihq',
            'subtipo_molecular': 'subtipo_molecular',
        }

        for var in vars_to_compare:
            l1v = l1_vals[var].lower().strip()
            llm_key = llm_field_map[var]
            llm_v = str(llm_rec.get(llm_key, '') or '').lower().strip()
            llm_v = llm_v.translate(ACCENT_MAP)
            if llm_v in ('none', 'null', ''): llm_v = ''

            # Normalize HER2 score comparison
            if var == 'her2_score':
                # LLM might return "3+" or just "3"
                if l1v and not l1v.endswith('+') and l1v != '0':
                    l1v = l1v + '+'
                if llm_v and not llm_v.endswith('+') and llm_v != '0':
                    llm_v = llm_v + '+'

            # Ki67: compare as integers
            if var == 'ki67_pct' and l1v and llm_v:
                try:
                    l1_num = int(float(l1v))
                    llm_num = int(float(llm_v))
                    if abs(l1_num - llm_num) <= 5:  # tolerance for range midpoint
                        l1v = llm_v = str(l1_num)
                except ValueError:
                    pass

            stats[var]['total'] += 1

            if l1v == '' and llm_v == '':
                stats[var]['both_na'] += 1
                stats[var]['agree'] += 1
            elif l1v == '' and llm_v != '':
                stats[var]['llm_only'] += 1
            elif l1v != '' and llm_v == '':
                stats[var]['l1_only'] += 1
            elif l1v == llm_v:
                stats[var]['agree'] += 1
            else:
                stats[var]['disagree'] += 1
                if len(stats[var]['disagree_examples']) < 8:
                    stats[var]['disagree_examples'].append(
                        (numExame, rec['estrato'], l1v, llm_v))

    # Save results
    if output_csv and results:
        fieldnames = list(results[0].keys())
        with open(output_csv, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        print(f"Results saved to {output_csv}")

    # Print concordance report
    print("=" * 80)
    print("L1 vs LLM CONCORDANCE REPORT — Mama IHQ")
    print("=" * 80)

    for var in vars_to_compare:
        s = stats[var]
        total = s['total']
        if total == 0:
            continue
        agree_pct = 100 * s['agree'] / total
        print(f"\n--- {var} ---")
        print(f"  Agree (incl both NA): {s['agree']}/{total} = {agree_pct:.1f}%")
        print(f"    Both NA: {s['both_na']}")
        print(f"    Agree on value: {s['agree'] - s['both_na']}")
        print(f"  L1-only (L1 has, LLM missing): {s['l1_only']}")
        print(f"  LLM-only (LLM has, L1 missing): {s['llm_only']}")
        print(f"  Disagree (both have, different): {s['disagree']}")
        if s['disagree_examples']:
            print(f"  Disagreement examples:")
            for nid, est, l1v, llm_v in s['disagree_examples']:
                print(f"    [{nid}] {est}: L1={l1v} vs LLM={llm_v}")

    # L1-only variables (no LLM comparison)
    l1_only_vars = ['re_pct', 're_allred', 'rp_pct', 'rp_allred']
    print(f"\n{'=' * 80}")
    print("L1-ONLY VARIABLES (no LLM comparison)")
    print("=" * 80)

    for var in l1_only_vars:
        filled = sum(1 for r in results if r.get(f'l1_{var}', '') != '')
        total = len(results)
        fill_pct = 100 * filled / total if total > 0 else 0
        print(f"\n--- {var} ---")
        print(f"  Filled: {filled}/{total} = {fill_pct:.1f}%")
        if var.endswith('_pct') and filled > 0:
            vals = [int(r[f'l1_{var}']) for r in results if r.get(f'l1_{var}', '') != '']
            print(f"  Range: {min(vals)}-{max(vals)}, Median: {sorted(vals)[len(vals)//2]}")
        elif var.endswith('_allred') and filled > 0:
            vals = [int(r[f'l1_{var}']) for r in results if r.get(f'l1_{var}', '') != '']
            print(f"  Range: {min(vals)}-{max(vals)}, Distribution: {dict(sorted(((v, vals.count(v)) for v in set(vals))))}")


# ===========================================================================
# MAIN
# ===========================================================================

if __name__ == '__main__':
    if '--smoke' in sys.argv or len(sys.argv) == 1:
        print("=== SMOKE TESTS ===\n")
        failures = run_smoke_tests()

        if '--smoke' not in sys.argv or '--run' in sys.argv:
            print("=== RUNNING ON HOLDOUT CORPUS ===\n")
            holdout_csv = r'Y:\IDOC-Patologia\04_artigos\artigo1_validacao_ia\holdout\holdout_mama_ihq.csv'
            llm_json = r'Y:\IDOC-Patologia\04_artigos\artigo1_validacao_ia\holdout\llm_mama_ihq.json'
            output_csv = r'Y:\DDPA\framework_extracao_v4\04_piloto\mama_ihq_l1_holdout.csv'
            run_comparison(holdout_csv, llm_json, output_csv)
