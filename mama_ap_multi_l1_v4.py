#!/usr/bin/env python3
"""
L1 Deterministic Detector — Mama AP (Multi-variable)
Framework v4 / DDPA cross-domain pilot

Variables covered (27 total, 19 with LLM comparison + 8 L1-only):
  LLM-compared:
  - tam_cm          (MEAS-N)   — tumor size
  - comp_insitu     (BIN)      — in situ component
  - procedimento    (CAT)      — surgical procedure
  - tipo_hist       (CAT)      — histological type
  - margens         (CAT)      — surgical margins
  - pT / pN         (CAT-ord)  — pathological staging
  - ln_exam/ln_pos  (NUM)      — lymph node counts
  - grau_nott       (CAT-ord)  — Nottingham grade
  - esc_tubular/nuclear/mitotico (NUM-C) — Nottingham components
  - inv_linfov/inv_perin (BIN) — vascular/perineural invasion
  - ref_ihq         (BIN)      — IHQ referral
  - behavior_class  (CAT)      — derived: invasive/in_situ/benigno
  - invasive_malignancy (BIN)  — derived
  - malignancy_any  (BIN)      — derived
  L1-only:
  - necrose_tumoral (BIN)      — tumor necrosis
  - multifocalidade (BIN)      — multifocality/multicentricity
  - microcalcificacoes (BIN)   — microcalcifications
  - pele            (BIN)      — skin involvement
  - mamilo          (BIN)      — nipple involvement
  - efeito_terapeutico (CAT)   — neoadjuvant response
  - infiltrado_linfocitico (CAT) — tumor-infiltrating lymphocytes
  - extensao_extranodal (BIN)  — extranodal extension

Design principles:
  1. Check NEGATION before positive (fix regex negation-blind bug)
  2. Evidence-first: return matched text span
  3. Section-aware: use conclusao only
  4. Conservative witness: if unsure, return None (not a guess)
  5. DCIS guard (Bug #28): null Nottingham components in pure in-situ reports
"""

import re
import csv
import json
import sys
import os
from dataclasses import dataclass, field
from typing import Optional, Tuple, List, Dict

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
ACCENT_MAP = str.maketrans(
    "áàãâéèêíìîóòõôúùûçÁÀÃÂÉÈÊÍÌÎÓÒÕÔÚÙÛÇ",
    "aaaaeeeiiioooouuucAAAAEEEIIIOOOOUUUC"
)

def normalize(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace, strip stray diacritics."""
    text = text.lower().translate(ACCENT_MAP)
    # Remove standalone diacritics/modifiers (U+00B4 acute, U+00B8 cedilla, etc.)
    text = text.replace('\u00b4', '').replace('\u00b8', '').replace('\u02c6', '')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# ===========================================================================
# TAM_CM — Tumor size extraction
# ===========================================================================

TAM_LABELS = [
    # (regex for label, capture group for value follows)
    r'maior\s+extens.o\s+(?:linear\s+)?d[aoe]\s+neoplasia[^:]*:\s*',
    r'tamanho\s+d[aoe]\s+(?:tumor|neoplasia\s*(?:invasiv[ao])?|lesao)\s*:\s*',
    r'maior\s+dimens.o\s+(?:linear\s+)?d[aoe]\s+neoplasia[^:]*:\s*',
    # Simpler "TAMANHO:" without qualifier (common in structured reports)
    r'tamanho\s*:\s*',
    # "FOCALIDADE E TAMANHO DO TUMOR:" pattern
    r'focalidade\s+e\s+tamanho\s+d[aoe]\s+tumor\s*:\s*',
]

# Measurement pattern: handles "1,5 X 1,3 X 1,0 CM" or "8,0 mm" or "2 mm" or "3,2 milímetros"
MEAS_PAT = r'(\d+(?:[,\.]\d+)?\s*(?:x\s*\d+(?:[,\.]\d+)?\s*)*(?:cm|mm|milimetros?))'

def _meas_to_mm(meas_str: str) -> Optional[float]:
    """Convert a measurement string like '3,3 x 2,5 cm' to max dimension in mm."""
    meas = meas_str.lower().strip()
    is_mm = 'mm' in meas or 'milimetro' in meas
    # Extract all numeric parts
    nums = re.findall(r'(\d+(?:[,\.]\d+)?)', meas)
    if not nums:
        return None
    vals = [float(n.replace(',', '.')) for n in nums]
    max_val = max(vals)
    if not is_mm:
        max_val *= 10  # cm → mm
    return max_val


def detect_tam_cm(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract tumor size. Returns (value, evidence).

    Priority: labeled patterns ('TAMANHO DA NEOPLASIA INVASIVA:', etc.) FIRST.
    Only falls back to 'medindo' when no labeled match exists.
    Among matches of same priority, returns the LARGEST
    (clinically correct for staging — uses largest invasive focus).

    Bug #26: 'medindo 3,0 x 2,5 x 2,5 cm' in axillary addendum was overriding
    labeled 'TAMANHO DA NEOPLASIA INVASIVA: 12 mm'. Fix: labeled > medindo.
    """
    labeled_candidates = []   # list of (value_str, evidence_str, size_mm)
    medindo_candidates = []   # fallback only

    def _add(lst: list, val: str, ev: str):
        mm = _meas_to_mm(val)
        if mm is not None:
            lst.append((val, ev, mm))

    # Try labeled patterns (most reliable — pathologist's structured field)
    for label_pat in TAM_LABELS:
        # Allow optional filler between label and measurement ("ÁREA DE", etc.)
        pat = label_pat + r'(?:area\s+de\s+)?' + MEAS_PAT
        for m in re.finditer(pat, text_norm, re.I):
            _add(labeled_candidates, m.group(1).strip(), m.group(0).strip())

    # Try "mede/medindo" with measurement (incl "medindo o maior foco [neoplásico]")
    for m in re.finditer(r'(?:mede|medindo)\s+(?:o\s+maior\s+(?:foco\s+(?:neoplasico\s+)?)?)?'
                         + MEAS_PAT, text_norm, re.I):
        _add(medindo_candidates, m.group(1).strip(), m.group(0).strip())

    # Return largest from labeled first; only fall back to medindo
    candidates = labeled_candidates if labeled_candidates else medindo_candidates
    if candidates:
        candidates.sort(key=lambda x: x[2], reverse=True)
        return candidates[0][0], candidates[0][1]

    # Fallback: "= X mm" after another measurement (e.g., "1,3 cm = 13,0 mm")
    m = re.search(r'=\s*' + MEAS_PAT, text_norm, re.I)
    if m:
        # Check context — should be near size keyword
        start = max(0, m.start() - 80)
        context = text_norm[start:m.start()]
        if re.search(r'tamanho|dimens|neoplasia|tumor', context):
            return m.group(1).strip(), text_norm[start:m.end()].strip()

    return None, None


# ===========================================================================
# COMP_INSITU — In situ component
# ===========================================================================

INSITU_NEG = [
    # Structured field negation: "COMPONENTE IN SITU: NÃO" / "AUSENTE"
    r'componente\s*"?in\s*situ"?\s*:\s*(?:nao|ausente)',
    r'componente\s*"?in\s*situ"?\s*:\s*nao\s+(?:observad|evidenciad|identificad)',
    r'sem\s+componente\s*"?in\s*situ"?',
    r'componente\s*"?in\s*situ"?\s*(?:nao|ausente)',
    # Lobular in situ negation
    r'componente\s+lobular\s*"?in\s*situ"?\s*:\s*(?:nao|ausente)',
    # Intraducto/intraductal negation (synonym for in situ)
    r'componente\s+intraduct\w*\s*:\s*(?:nao|ausente)',
    r'componente\s+intraduct\w*\s*:\s*nao\s+(?:observad|evidenciad|identificad)',
    r'sem\s+componente\s+intraduct',
    # "CARCINOMA INTRADUCTO: NÃO EVIDENCIADO" (structured field)
    r'carcinoma\s+intraduct\w*\s*:?\s*nao\s+(?:evidenciad|observad|identificad)',
    # "CARCINOMA DUCTAL IN SITU NÃO EVIDENCIADO" (inline negation)
    r'carcinoma\s+ductal\s*"?in\s*situ"?\s+nao\s+(?:evidenciad|observad|identificad)',
    # CDIS/in situ with (CDIS) explicitly negated
    r'carcinoma\s+ductal\s*"?in\s*situ"?\s*\(cdis\)\s*:?\s*nao\s+(?:evidenciad|observad|identificad)',
    r'ausencia\s+de\s+(?:carcinoma\s+(?:ductal\s*)?in\s*situ|cdis|clis)',
    r'nao\s+(?:se\s+)?(?:evidenci|observ|identific)\w+\s+(?:carcinoma\s+(?:ductal\s*)?in\s*situ|cdis)',
]

INSITU_POS = [
    r'componente\s*"?in\s*situ"?\s*:\s*presente',
    r'componente\s*"?in\s*situ"?\s*:\s*(?!nao|ausente)\S',
    r'componente\s*"?in\s*situ"?\s*presente',
    # "componente in situ associado/extenso" without colon
    r'componente\s*"?in\s*situ"?\s*(?:\([^)]*\)\s*)?associad',
    r'componente\s*"?in\s*situ"?\s*(?:\([^)]*\)\s*)?extenso',
    # "componente de carcinoma ductal in situ"
    r'componente\s+(?:de\s+)?carcinoma\s+ductal\s*"?in\s*situ"?',
    # "componente lobular in situ: presente"
    r'componente\s+lobular\s*"?in\s*situ"?\s*:\s*presente',
    # "componente intraducto/intraductal" with colon + positive value
    r'componente\s+intraduct\w*\s*:\s*(?:presente|extenso|focal|sim|associad)',
    # "componente intraducto/intraductal" with positive qualifier (no colon)
    r'componente\s+intraduct\w*\s+(?:associad|present|extenso|focal)',
    # CDIS/CLIS as component
    r'representado\s+por\s+(?:cdis|clis|carcinoma\s+ductal\s+in\s*situ)',
    # "com carcinoma intraducto associado"
    r'(?:com|e)\s+carcinoma\s+intraduct\w*\s*(?:\(cdis\))?\s*associad',
    # "presença de carcinoma intraducto"
    r'presenca\s+de\s+carcinoma\s+(?:ductal\s*)?in\s*situ',
    r'presenca\s+de\s+carcinoma\s+intraduct',
    r'presenca\s+de\s+cdis',
]

# Primary in situ diagnosis (the tumor IS in situ, not a component)
INSITU_PRIMARY = [
    r'carcinoma\s+ductal\s*"?in\s*situ"?\s+(?:de\s+mama|em\s+mama|\(cdis\))',
    r'carcinoma\s+ductal\s*"?in\s*situ"?\s*\(cdis\)',
    r'carcinoma\s+ductal\s*"?in\s*situ"?,?\s+(?:de\s+)?(?:padrao|grau|alto|baixo|intermedi)',
    r'carcinoma\s+lobular\s*"?in\s*situ"?',
    r'carcinoma\s*"?in\s*situ"?\s+(?:de|em)\s+mama',
    r'carcinoma\s+intraductal',
    r'carcinoma\s+intraducto\b',
]

def detect_comp_insitu(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect in situ component. Returns (sim/nao/None, evidence).

    Design: collect ALL negation and positive spans. Positive overrides negation
    when they're in different sections (handles multi-biopsy reports).
    A positive match that overlaps with a negation span is discarded.
    """
    # Collect negation spans
    neg_spans = []
    for pat in INSITU_NEG:
        for m in re.finditer(pat, text_norm):
            neg_spans.append((m.start(), m.end(), m.group(0).strip()))

    def overlaps_negation(start: int, end: int) -> bool:
        for ns, ne, _ in neg_spans:
            if ns <= start <= ne or start <= ns <= end:
                return True
        return False

    # Check positive patterns (INSITU_POS + INSITU_PRIMARY)
    all_pos = INSITU_POS + INSITU_PRIMARY
    for pat in all_pos:
        m = re.search(pat, text_norm)
        if m and not overlaps_negation(m.start(), m.end()):
            return 'sim', m.group(0).strip()

    # CDIS/CLIS abbreviation anywhere
    for m in re.finditer(r'\bcdis\b|\bclis\b', text_norm):
        if overlaps_negation(m.start(), m.end()):
            continue
        # Check local negation context (within 30 chars before)
        ctx_start = max(0, m.start() - 30)
        ctx = text_norm[ctx_start:m.start()]
        if re.search(r'sem\b|nao\b|ausent', ctx):
            if not neg_spans:
                neg_spans.append((ctx_start, m.end(), text_norm[ctx_start:m.end()].strip()))
            continue
        return 'sim', m.group(0).strip()

    # If only negation found
    if neg_spans:
        return 'nao', neg_spans[0][2]

    return None, None


# ===========================================================================
# PROCEDIMENTO — Surgical procedure
# ===========================================================================

PROC_PATTERNS = [
    # Order matters: more specific first
    (r'mastectomia\s+radical', 'mastectomia_radical'),
    (r'mastectomia\s+simples', 'mastectomia'),
    (r'mastectomia\s+(?:total|subcutanea)', 'mastectomia'),
    (r'adenomastectomia', 'mastectomia'),
    (r'mastectomia', 'mastectomia'),
    (r'quadrantectomia', 'quadrantectomia'),
    (r'setorectomia', 'excisional'),
    (r'resseccao\s+segmentar', 'excisional'),
    (r'tumorectomia', 'excisional'),
    (r'nodulectomia', 'excisional'),
    (r'biopsia\s+excisional', 'excisional'),
    (r'excisional', 'excisional'),
    (r'mamotomia', 'mamotomia'),
    (r'biopsia\s+(?:por\s+)?agulha', 'biopsia_agulha'),
    (r'biopsia\s+incisional', 'excisional'),
    (r'biopsia\s+core|core\s+biopsy', 'biopsia_agulha'),
    (r'ampliacao\s+de\s+marg', 'excisional'),
    (r'excisao\s+d[aoe]', 'excisional'),
    (r'produto\s+de\s+mamotomia', 'mamotomia'),
]

# Additional patterns in material field
PROC_MATERIAL_PATTERNS = [
    (r'mastectomia\s+radical', 'mastectomia_radical'),
    (r'mastectomia', 'mastectomia'),
    (r'quadrantectomia', 'quadrantectomia'),
    (r'setorectomia', 'excisional'),
    (r'mamotomia', 'mamotomia'),
]

def detect_procedimento(text_norm: str, material_norm: str = '') -> Tuple[Optional[str], Optional[str]]:
    """Detect surgical procedure. Checks conclusao first, then material."""
    # Check in text (conclusao) first — labeled patterns
    labeled = re.search(r'procedimento\s*(?:cirurgico|realizado)?\s*:\s*(.{5,60})', text_norm)
    if labeled:
        snippet = labeled.group(1)
        for pat, label in PROC_PATTERNS:
            if re.search(pat, snippet):
                return label, labeled.group(0).strip()[:80]

    # Then general patterns in text — prefer EARLIEST match position
    # (procedure info is typically at beginning of report, not in amendment notes)
    best_pos = len(text_norm) + 1
    best_label = None
    best_ev = None
    for pat, label in PROC_PATTERNS:
        m = re.search(pat, text_norm)
        if m and m.start() < best_pos:
            best_pos = m.start()
            best_label = label
            ctx_start = max(0, m.start() - 20)
            best_ev = text_norm[ctx_start:m.end() + 10].strip()[:80]
    if best_label:
        return best_label, best_ev

    # Check material field
    for pat, label in PROC_MATERIAL_PATTERNS:
        m = re.search(pat, material_norm)
        if m:
            return label, f'[material] {m.group(0).strip()}'

    # Fallback: "material de biópsia" without specific procedure → biopsia_agulha
    if re.search(r'material\s+de\s+biopsia', text_norm) or \
       re.search(r'material\s+de\s+biopsia', material_norm):
        return 'biopsia_agulha', 'material de biopsia (inferred)'

    return None, None


# ===========================================================================
# TIPO_HIST — Histological type
# ===========================================================================

def detect_tipo_hist(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect histological type. Returns (type, evidence)."""

    # --- Malignant subtypes (specific before catch-all) ---

    # Metaplastic (check early — distinctive)
    if re.search(r'carcinoma\s+metaplasico', text_norm):
        m = re.search(r'carcinoma\s+metaplasico', text_norm)
        return 'metaplasico', m.group(0).strip()

    # Mixed type (lobular + ductal in same report — check before individual types)
    has_lobular_inv = re.search(r'carcinoma\s+(?:mamario\s+)?lobular\s+(?:invasivo|infiltrante)', text_norm)
    has_ductal_inv = re.search(r'carcinoma\s+(?:mamario\s+)?(?:ductal\s+)?(?:invasivo|infiltrante)\s+(?:da\s+mama|em\s+mama|de\s+mama|do\s+tipo\s+nao)', text_norm) or \
                     re.search(r'tipo\s+nao\s+especial', text_norm)
    if has_lobular_inv and has_ductal_inv:
        return 'misto', f'{has_lobular_inv.group(0).strip()} + {has_ductal_inv.group(0).strip()}'[:80]

    # Lobular invasive
    if has_lobular_inv:
        return 'CLI', has_lobular_inv.group(0).strip()

    # Papilar encapsulado invasivo (before micropapilar check)
    if re.search(r'carcinoma\s+papilar\s+(?:encapsulado\s+)?invasiv', text_norm):
        m = re.search(r'carcinoma\s+papilar\s+(?:encapsulado\s+)?invasiv', text_norm)
        return 'papilar', m.group(0).strip()

    # Micropapilar invasive
    if re.search(r'(?:carcinoma\s+)?micropapilar\s+invasiv', text_norm):
        m = re.search(r'(?:carcinoma\s+)?micropapilar\s+invasiv', text_norm)
        return 'micropapilar', m.group(0).strip()
    if re.search(r'tipo\s+(?:carcinoma\s+)?micropapilar', text_norm):
        m = re.search(r'tipo\s+(?:carcinoma\s+)?micropapilar', text_norm)
        return 'micropapilar', m.group(0).strip()
    # "padrão micropapilar" / "componente micropapilar" (carcinoma with micropapilar pattern)
    m = re.search(r'(?:padrao|componente)\s+micropapilar', text_norm)
    if m and re.search(r'carcinoma|neoplasia', text_norm):
        return 'micropapilar', m.group(0).strip()

    # Mucinoso — require primary pattern, not secondary ("áreas de padrão mucinoso")
    m = re.search(r'carcinoma\s+mucinos[oa]', text_norm)
    if m:
        return 'mucinoso', m.group(0).strip()
    m = re.search(r'diferenciacao\s+mucinos[oa]', text_norm)
    if m:
        # But NOT "áreas de padrão mucinoso" / "com diferenciação mucinosa parcial"
        ctx_start = max(0, m.start() - 40)
        ctx = text_norm[ctx_start:m.start()]
        if not re.search(r'areas\s+de|parcial', ctx):
            return 'mucinoso', m.group(0).strip()
    m = re.search(r'tipo\s+mucinos[oa]', text_norm)
    if m:
        return 'mucinoso', m.group(0).strip()

    # Misto (ductal + lobular pattern)
    if re.search(r'padrao\s+misto\s+(?:tipo\s+)?ductal\s+e\s+lobular', text_norm):
        m = re.search(r'padrao\s+misto\s+(?:tipo\s+)?ductal\s+e\s+lobular', text_norm)
        return 'misto', m.group(0).strip()
    if re.search(r'carcinoma\s+(?:invasivo|mamario).{0,30}misto', text_norm):
        m = re.search(r'carcinoma\s+(?:invasivo|mamario).{0,30}misto', text_norm)
        return 'misto', m.group(0).strip()

    # Tubular (but NOT "diferenciação tubular" which is a Nottingham score component)
    m = re.search(r'carcinoma\s+tubular', text_norm)
    if m:
        return 'tubular', m.group(0).strip()

    # Medular
    if re.search(r'carcinoma\s+medular', text_norm):
        m = re.search(r'carcinoma\s+medular', text_norm)
        return 'medular', m.group(0).strip()

    # Microinvasivo (specific — before CDI_SOE catch-all)
    if re.search(r'carcinoma\s+(?:ductal\s+)?microinvasiv', text_norm):
        m = re.search(r'carcinoma\s+(?:ductal\s+)?microinvasiv', text_norm)
        return 'CDI_SOE', m.group(0).strip()  # microinvasive is CDI_SOE clinically
    if re.search(r'carcinoma\s+microinvasiv', text_norm):
        m = re.search(r'carcinoma\s+microinvasiv', text_norm)
        return 'CDI_SOE', m.group(0).strip()

    # CDI SOE: ductal invasive / infiltrating ductal / NST / "tipo não especial"
    cdi_patterns = [
        r'carcinoma\s+ductal\s+(?:invasivo|infiltrante)',
        r'carcinoma\s+(?:mamario\s+)?(?:invasivo|infiltrante)\s+(?:da\s+mama|em\s+mama|de\s+mama)',
        r'carcinoma\s+(?:mamario\s+)?invasivo[^,]*tipo\s+nao\s+especial',
        r'tipo\s+nao\s+especial',
        r'carcinoma\s+mamario\s+(?:ductal\s+)?invasiv',
        r'carcinoma\s+mamario\s+ductal\s+infiltrant',
        r'cdi[^s]',  # CDI but not CDIS
    ]
    for pat in cdi_patterns:
        m = re.search(pat, text_norm)
        if m:
            return 'CDI_SOE', m.group(0).strip()

    # In situ only (no invasive component) — including CDIS and "carcinoma intraductal"
    # Check for invasive/microinvasive mentions, excluding negated context
    # "ausencia de carcinoma invasivo" = NOT invasive
    _inv_matches = list(re.finditer(r'invasiv|infiltrant|microinvasiv|microinvasao', text_norm))
    has_invasive = False
    for _im in _inv_matches:
        _ctx = text_norm[max(0, _im.start() - 40):_im.start()]
        if not re.search(r'ausencia\s+de|sem\s+|nao\s+(?:ha|foram?\s+(?:identificad|evidenciad|observad))', _ctx):
            has_invasive = True
            break
    insitu_pats = [
        r'carcinoma\s+ductal\s*"?in\s*situ"?',
        r'\bcdis\b',
        r'carcinoma\s+intraductal',
        r'carcinoma\s+intraducto\b',
        r'carcinoma\s*"?in\s*situ"?',
    ]
    for pat in insitu_pats:
        if re.search(pat, text_norm):
            if not has_invasive:
                m = re.search(pat, text_norm)
                return 'CDIS', m.group(0).strip()
            # Has invasive/microinvasive component — classify as CDI_SOE
            m = re.search(r'carcinoma\s+(?:invasiv|infiltrant)', text_norm)
            if m:
                return 'CDI_SOE', m.group(0).strip()
            # Microinvasion without explicit "carcinoma invasivo" (e.g., "CDIS com microinvasão")
            return 'CDI_SOE', f'{re.search(pat, text_norm).group(0).strip()} + microinvasion'

    # Lobular in situ only
    if re.search(r'carcinoma\s+lobular\s*"?in\s*situ"?|clis', text_norm):
        if not has_invasive:
            return 'CLIS', 'carcinoma lobular in situ'

    # Carcinoma metastático
    if re.search(r'carcinoma\s+(?:de\s+ductos\s+mamarios\s+)?metastatico', text_norm):
        m = re.search(r'carcinoma.*metastatico', text_norm)
        return 'CDI_SOE', m.group(0).strip()[:60]

    # Generic carcinoma (last resort for malignant) — but check for invasive
    if re.search(r'carcinoma\s+(?:invasiv|infiltrant)', text_norm):
        m = re.search(r'carcinoma\s+(?:invasiv|infiltrant)', text_norm)
        return 'CDI_SOE', m.group(0).strip()

    # Adenocarcinoma (generic — not breast-specific)
    if re.search(r'adenocarcinoma', text_norm):
        return 'outro_maligno', 'adenocarcinoma'

    # --- Benign ---

    # Tumor phyllodes / filodes (check before fibroadenoma)
    if re.search(r'tumor\s+(?:phyllodes|filodes)', text_norm):
        m = re.search(r'tumor\s+(?:phyllodes|filodes).{0,30}', text_norm)
        return 'outro_benigno', m.group(0).strip()[:60]

    # Lipoma (check before fibrocystic)
    if re.search(r'lipoma', text_norm):
        m = re.search(r'lipoma\b.{0,30}', text_norm)
        return 'outro_benigno', m.group(0).strip()[:60]

    # Papiloma intraductal (specific diagnosis — before fibroadenoma and fibrocystic)
    m = re.search(r'papiloma\s+intraduct', text_norm)
    if m:
        return 'outro_benigno', m.group(0).strip()
    m = re.search(r'proliferacao\s+(?:intraductal\s+)?papilar', text_norm)
    if m:
        return 'outro_benigno', m.group(0).strip()

    # Fibroadenoma — use \b to avoid matching "fibroadenomatóide"
    if re.search(r'fibroadenoma\b', text_norm):
        return 'fibroadenoma', 'fibroadenoma'

    # Fibrocystic — explicit keyword
    fibro_pats = [r'fibrocistic', r'altera..es?\s+fibrocistic', r'doenca\s+fibrocistic']
    for pat in fibro_pats:
        if re.search(pat, text_norm):
            return 'fibrocistico', pat

    # Fibrocystic — inferred from combination of typical findings
    # (cistos + adenose/hiperplasia/ectasia/metaplasia = fibrocystic pattern)
    # Moved BEFORE individual benign patterns to avoid false outro_benigno
    fibro_features = sum([
        1 if re.search(r'\bcistos?\b|\bmicrocistos?\b', text_norm) else 0,
        1 if re.search(r'adenose', text_norm) else 0,
        1 if re.search(r'hiperplasia\s+(?:ductal|lobular|fibroadenomatoide)', text_norm) else 0,
        1 if re.search(r'ectasia\s+ductal', text_norm) else 0,
        1 if re.search(r'metaplasia\s+apocrina', text_norm) else 0,
        1 if re.search(r'fibrose\s+(?:estromal|reacional|intersticial|interlobular)', text_norm) else 0,
        1 if re.search(r'alterac.o\s+(?:de\s+)?celulas\s+colunares', text_norm) else 0,
        1 if re.search(r'alterac.es?\s+involutiv', text_norm) else 0,
    ])
    if fibro_features >= 2:
        return 'fibrocistico', f'fibrocystic pattern ({fibro_features} features)'

    # Other benign patterns (individual — only if not fibrocystic)
    benign_pats = [
        (r'necrose\s+adiposa', 'outro_benigno'),
        (r'adenose', 'outro_benigno'),
        (r'hiperplasia', 'outro_benigno'),
        (r'ectasia\s+ductal', 'outro_benigno'),
        (r'tecido\s+mamario\s+(?:sem|com|apresentando)\s+', 'outro_benigno'),
        (r'metaplasia\s+apocrina', 'outro_benigno'),
        (r'cistos?\b', 'outro_benigno'),
        (r'fibrose\s+estromal', 'outro_benigno'),
        (r'ducto[s]?\s+dilatad', 'outro_benigno'),
        (r'alterac.es?\s+involutiv', 'outro_benigno'),
    ]
    for pat, label in benign_pats:
        if re.search(pat, text_norm):
            m = re.search(pat, text_norm)
            return label, m.group(0).strip()

    return None, None


# ===========================================================================
# MARGENS — Surgical margins
# ===========================================================================

def detect_margens(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect surgical margin status. Returns (livres/comprometidas/exiguas/None, evidence).

    Design: WORST-CASE-WINS. If ANY margin is comprometida, overall is comprometidas.
    If any is exigua (none comprometida), overall is exiguas. Else livres.
    """
    worst = None  # None < 'livres' < 'exiguas' < 'comprometidas'
    evidence = ''
    SEVERITY = {'livres': 1, 'exiguas': 2, 'comprometidas': 3}

    def update_worst(candidate, ev):
        nonlocal worst, evidence
        if worst is None or SEVERITY.get(candidate, 0) > SEVERITY.get(worst, 0):
            worst = candidate
            evidence = ev

    # Check for tangenciadas anywhere (strong signal for exíguas)
    m = re.search(r'marg.{0,40}tangenciad.{0,40}', text_norm)
    if m:
        update_worst('exiguas', m.group(0).strip()[:100])

    # Explicit margin section: "MARGENS CIRÚRGICAS: ..."
    margin_section = re.search(
        r'marg(?:ens?|em)\s+cirurgica[s]?\s*(?:\([^)]*\))?\s*:\s*(.{5,120})', text_norm)
    if margin_section:
        snippet = margin_section.group(1)
        boundary = re.search(r'(?:linfon|mamilo|pele|areola|\d+\)|\d+\.\d+\))', snippet)
        if boundary:
            snippet = snippet[:boundary.start()]
        ev = margin_section.group(0).strip()[:100]
        if re.search(r'comprometid|infiltrad', snippet):
            update_worst('comprometidas', ev)
        if re.search(r'exigu|tangenciad', snippet):
            update_worst('exiguas', ev)
        if re.search(r'livres?|livre\s+de|respeitad', snippet):
            update_worst('livres', ev)

    # Individual margins listed: "MARGEM ANTERIOR: COMPROMETIDA; MARGEM POSTERIOR: LIVRE"
    indiv = re.findall(r'margem\s+\w+\s*:\s*(livre|comprometid\w*|exigu\w*)', text_norm)
    if indiv:
        for v in indiv:
            if 'comprometid' in v:
                update_worst('comprometidas', f'individual: {v}')
            elif 'exigu' in v:
                update_worst('exiguas', f'individual: {v}')
            else:
                update_worst('livres', f'individual: {v}')

    # Broader "margem ... comprometida" (up to 4 words between)
    m = re.search(r'marg\w*\s+(?:cirurgica[s]?\s+)?(?:\w+\s+){0,4}comprometid', text_norm)
    if m:
        matched = m.group(0)
        if not re.search(r'linfon|mamilo|pele|areola', matched):
            update_worst('comprometidas', matched.strip()[:80])

    # "compromete as margens" (verb form: X compromete as margens cirúrgicas)
    m = re.search(r'compromete\w*\s+(?:as?\s+)?marg', text_norm)
    if m:
        update_worst('comprometidas', m.group(0).strip()[:80])

    # "encontra-se livre/comprometida" near margin context
    for m in re.finditer(r'encontra[\s-]se\s+(livres?|comprometid\w*|exigu\w*)', text_norm):
        ctx = text_norm[max(0, m.start()-120):m.start()]
        if re.search(r'marg', ctx):
            val_txt = m.group(1)
            if 'comprometid' in val_txt:
                update_worst('comprometidas', m.group(0).strip()[:80])
            elif 'exigu' in val_txt:
                update_worst('exiguas', m.group(0).strip()[:80])
            else:
                update_worst('livres', m.group(0).strip()[:80])
            break

    # "margem mais próxima" — exíguas ONLY when not inside a parenthetical of "livres"
    m_proxima = re.search(r'margem\s+(?:cirurgica\s+)?mais\s+proxima', text_norm)
    if m_proxima:
        # Check if preceded by "livres" in same margin section (informational, not classificatory)
        ctx_before = text_norm[max(0, m_proxima.start()-80):m_proxima.start()]
        if not re.search(r'livres?\s+(?:de\s+)?(?:comprometimento|neoplasi)', ctx_before):
            update_worst('exiguas', m_proxima.group(0).strip())

    # Broader livres
    m = re.search(r'margens?\s+(?:cirurgicas?\s+)?livres?\b', text_norm)
    if m:
        update_worst('livres', m.group(0).strip())

    # Exíguas anywhere in margin context (search for "exigu" near "marg")
    for m in re.finditer(r'\bexigu\w*', text_norm):
        ctx = text_norm[max(0, m.start()-120):m.start()]
        if re.search(r'marg', ctx):
            update_worst('exiguas', text_norm[max(0,m.start()-40):m.end()+10].strip()[:80])
            break

    # Fallback: broad "margens ... livres" pattern (up to 80 chars for listed margins)
    if worst is None:
        m = re.search(r'margens?\b.{0,80}livres?', text_norm)
        if m:
            update_worst('livres', m.group(0).strip()[:100])

    if worst:
        return worst, evidence
    return None, None


# ===========================================================================
# pT — Pathological T stage
# ===========================================================================

def detect_pT(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect pT staging. Handles ypT prefix."""
    # Full staging line (most reliable)
    m = re.search(r'(?:estadiamento[^:]*:\s*)?(y?pt(?:is|[0-4])(?:mi|[a-d])?(?:\([^)]*\))?)', text_norm)
    if m:
        val = m.group(1).strip()
        return val, m.group(0).strip()[:80]
    return None, None


# ===========================================================================
# pN — Pathological N stage
# ===========================================================================

def detect_pN(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect pN staging. Handles ypN, (sn), (ls) suffixes.
    Also handles:
      - Missing 'p' prefix: "pT2 N0 Mx" → N0 without p
      - Letter O instead of digit 0: "pNO(LS)" → pN0(LS)
      - yN0 without p: "ypT1c; yN0" → yN0
    """
    # Full pN pattern with optional suffix — p prefix required
    pn_pat = r'(y?pn(?:[0-3])(?:[a-c])?(?:\s*\([^)]*\))?|y?pnx)'

    # Within staging line (p prefix present)
    m = re.search(r'(?:estadiamento[^:]*:\s*(?:y?pt\S+\s*;?\s*)?)' + pn_pat, text_norm)
    if m:
        val = m.group(1).strip()
        return val, m.group(0).strip()[:80]

    # Standalone pN (p prefix present)
    m = re.search(r'\b' + pn_pat, text_norm)
    if m:
        val = m.group(1).strip()
        return val, m.group(0).strip()[:80]

    # Handle letter O instead of digit 0: pno → pn0
    m = re.search(r'\b(y?pno)(?:\s*(\([^)]*\)))?', text_norm)
    if m:
        val = m.group(1).replace('o', '0')
        if m.group(2):
            val += ' ' + m.group(2)
        return val.strip(), m.group(0).strip()[:80]

    # Handle missing 'p' prefix: bare N0/N1/N2/N3/Nx in staging context
    # Must be near pT (within staging line) to avoid false positives
    m = re.search(r'(?:estadiamento[^:]*:\s*(?:y?pt\S+\s*;?\s*)?)(y?n[0-3x])(?:[a-c])?(?:\s*\([^)]*\))?', text_norm)
    if m:
        raw = m.group(1)
        # Reconstruct canonical form: insert 'p' after optional 'y'
        if raw.startswith('y'):
            val = 'yp' + raw[1:]
        else:
            val = 'p' + raw
        # Capture full match including suffix
        full = m.group(0).strip()[:80]
        return val, full

    return None, None


# ===========================================================================
# LN_EXAM / LN_POS — Lymph node counts
# ===========================================================================

WORD_NUMBERS = {
    'um': 1, 'uma': 1, 'dois': 2, 'duas': 2, 'tres': 3, 'quatro': 4,
    'cinco': 5, 'seis': 6, 'sete': 7, 'oito': 8, 'nove': 9, 'dez': 10,
    'onze': 11, 'doze': 12, 'treze': 13, 'quatorze': 14, 'catorze': 14,
    'quinze': 15, 'dezesseis': 16, 'dezessete': 17, 'dezoito': 18,
    'dezenove': 19, 'vinte': 20, 'vinte e um': 21, 'vinte e dois': 22,
    'vinte e tres': 23, 'vinte e quatro': 24, 'vinte e cinco': 25,
    'vinte e seis': 26, 'vinte e sete': 27, 'vinte e oito': 28,
    'vinte e nove': 29, 'trinta': 30, 'zero': 0, 'nenhum': 0,
}

def parse_number(text: str) -> Optional[int]:
    """Parse a number from digits or Portuguese words."""
    text = text.strip().lower()
    if re.match(r'^\d+$', text):
        return int(text)
    # Try multi-word phrases first (longest match wins)
    for word, val in sorted(WORD_NUMBERS.items(), key=lambda x: -len(x[0])):
        if word in text:
            return val
    return None

def detect_ln(text_norm: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """Detect lymph node examined/positive counts. Returns (exam, pos, evidence)."""
    ln_exam = None
    ln_pos = None
    evidence_parts = []

    # "LINFONODOS EXAMINADOS: XX" or "LINFONODOS ISOLADOS: XX"
    m = re.search(r'linfon.dos?\s+(?:examinados?|isolados?)\s*:\s*(\d+)', text_norm)
    if m:
        ln_exam = int(m.group(1))
        evidence_parts.append(m.group(0).strip())

    # "LINFONODOS COMPROMETIDOS: XX"
    m = re.search(r'linfon.dos?\s+(?:comprometidos?|positivos?|metastaticos?)\s*:\s*(\d+)', text_norm)
    if m:
        ln_pos = int(m.group(1))
        evidence_parts.append(m.group(0).strip())

    # "MACROMETÁSTASES (>Xmm): N" pattern (structured pathology reports)
    if ln_pos is None:
        m = re.search(r'macrometastases?\s*(?:\([^)]*\))?\s*:\s*(\d+)', text_norm)
        if m:
            ln_pos = int(m.group(1))
            evidence_parts.append(m.group(0).strip())

    # "COMPROMETIDOS: XX" without "LINFONODOS" prefix (in LN context)
    if ln_pos is None:
        m = re.search(r'comprometidos?\s*:\s*(\d+)', text_norm)
        if m:
            # Verify it's in lymph node context (within 200 chars before)
            ctx_start = max(0, m.start() - 200)
            ctx = text_norm[ctx_start:m.start()]
            if re.search(r'linfon|axilar|sentinela|isolados?', ctx):
                ln_pos = int(m.group(1))
                evidence_parts.append(m.group(0).strip())

    # "XX (ZERO)" pattern
    if ln_pos is None:
        m = re.search(r'comprometidos?\s*:\s*(\d+)\s*\((?:zero|nenhum)\)', text_norm)
        if m:
            ln_pos = 0
            evidence_parts.append(m.group(0).strip())

    # "X/Y linfonodos" or "X de Y linfonodos" or "X em Y linfonodos"
    if ln_exam is None:
        m = re.search(r'(\d+)\s*(?:/|de|em)\s*(\d+)\s+linfon', text_norm)
        if m:
            ln_pos = int(m.group(1))
            ln_exam = int(m.group(2))
            evidence_parts.append(m.group(0).strip())

    # Numbers written in words: "DEZESSETE LINFONODOS COMPROMETIDOS EM VINTE E SETE LINFONODOS EXAMINADOS"
    if ln_exam is None:
        m = re.search(r'(\w+(?:\s+e\s+\w+)?)\s+linfon\w+\s+comprometid\w+\s+em\s+(\w+(?:\s+e\s+\w+)?)\s+linfon\w+\s+examinad', text_norm)
        if m:
            pos_num = parse_number(m.group(1))
            exam_num = parse_number(m.group(2))
            if pos_num is not None and exam_num is not None:
                ln_pos = pos_num
                ln_exam = exam_num
                evidence_parts.append(m.group(0).strip()[:80])

    # "(N LINFONODOS EXAMINADOS)" pattern
    if ln_exam is None:
        m = re.search(r'\((\d+)\s+linfon\w+\s+examinad\w+', text_norm)
        if m:
            ln_exam = int(m.group(1))
            evidence_parts.append(m.group(0).strip())

    # "FORAM AVALIADOS N LINFONODOS" / "FORAM EXAMINADOS N LINFONODOS"
    if ln_exam is None:
        m = re.search(r'foram\s+(?:avaliados?|examinados?)\s+(\w+(?:\s+e\s+\w+)?)\s+linfon', text_norm)
        if m:
            n = parse_number(m.group(1))
            if n is not None:
                ln_exam = n
                evidence_parts.append(m.group(0).strip()[:80])

    # "LINFONODO INTRAPARENQUIMATOSO (UM/DOIS...) LIVRE" — incidental LN in biopsy
    if ln_exam is None:
        m = re.search(r'linfon\w+\s+\w+\s+\((\w+(?:\s+e\s+\w+)?)\)\s+livres?\s+de', text_norm)
        if m:
            n = parse_number(m.group(1))
            if n is not None:
                ln_exam = n
                ln_pos = 0
                evidence_parts.append(m.group(0).strip()[:80])

    # "EXAMINADOS: XX" (without linfonodos prefix, within LN section)
    if ln_exam is None:
        m = re.search(r'examinados?\s*:\s*(\d+)', text_norm)
        if m:
            # Verify this is in lymph node context (within 200 chars of "linfon")
            ctx_start = max(0, m.start() - 200)
            if re.search(r'linfon|axilar|sentinela', text_norm[ctx_start:m.start()]):
                ln_exam = int(m.group(1))
                evidence_parts.append(m.group(0).strip())

    # "LIVRE DE METÁSTASE" / "NEGATIVOS" / "LIVRE DE COMPROMETIMENTO"
    # MUST be in lymph node context — not from margin section!
    if ln_exam is not None and ln_pos is None:
        # Search for livre/negativo patterns near LN context
        for m in re.finditer(r'livre\s+de\s+(?:metastas|neoplasi|comprometimento)|negativos?\b|sem\s+metastas', text_norm):
            ctx_start = max(0, m.start() - 200)
            ctx = text_norm[ctx_start:m.start()]
            # Must be near lymph node keywords, not margin keywords
            if re.search(r'linfon|axilar|sentinela', ctx) and not re.search(r'marg\w*\s+cirurg|pele|mamilo|areola', ctx[-60:]):
                ln_pos = 0
                evidence_parts.append('livre/negativo')
                break

    # Sentinel node counting: "(N LINFONODOS)" or specific count
    if ln_exam is None:
        # Count sentinel nodes: "LINFONODO SENTINELA (1)" or "LINFONODOS SENTINELAS (2)"
        sn_matches = re.findall(r'linfon\w+\s+sentinela\w*\s*\((\d+)\)', text_norm)
        if sn_matches:
            ln_exam = sum(int(x) for x in sn_matches)
            evidence_parts.append(f'sentinel({ln_exam})')
        else:
            # "LINFONODOS SENTINELAS DE AXILA" + "(N LINFONODOS EXAMINADOS)"
            sn = re.search(r'linfon\w+\s+sentinela', text_norm)
            if sn:
                # Look for explicit count nearby
                exam_nearby = re.search(r'\((\d+)\s+linfon\w+\s+examinad', text_norm)
                if exam_nearby:
                    ln_exam = int(exam_nearby.group(1))
                    evidence_parts.append(f'sentinel + ({ln_exam} examinados)')
                else:
                    # Try to read count from prose: "CINCO LINFONODOS SENTINELA"
                    # or "N LINFONODOS SENTINELA"
                    sn_count_m = re.search(r'(\w+(?:\s+e\s+\w+)?)\s+linfon\w+\s+sentinela', text_norm)
                    sn_parsed = None
                    if sn_count_m:
                        sn_parsed = parse_number(sn_count_m.group(1))
                    if sn_parsed is not None and sn_parsed > 0:
                        ln_exam = sn_parsed
                        evidence_parts.append(f'sentinel count ({sn_parsed})')
                    else:
                        # Last resort: count distinct "linfonodo sentinela" mentions
                        sn_count = len(re.findall(r'linfonodo\s+sentinela', text_norm))
                        if sn_count >= 1:
                            ln_exam = sn_count
                            evidence_parts.append(f'sentinel implied ({sn_count})')
                    # Check if free — but in LN context only
                    if ln_exam is not None and ln_pos is None:
                        for fm in re.finditer(r'livres?\s+de\s+(?:metastas|neoplasi|comprometimento)|negativos?\b|sem\s+(?:metastas|comprometimento)', text_norm):
                            fctx = text_norm[max(0, fm.start()-100):fm.start()]
                            if re.search(r'linfon|sentinela|axilar', fctx):
                                ln_pos = 0
                                evidence_parts.append('livre')
                                break

    evidence = ' | '.join(evidence_parts) if evidence_parts else None
    return ln_exam, ln_pos, evidence


# ===========================================================================
# GRAU_NOTT — Nottingham histological grade
# ===========================================================================

def detect_grau_nott(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect Nottingham grade (I/II/III → 1/2/3). Returns (grade, evidence)."""
    ROMAN_MAP = {'i': '1', 'ii': '2', 'iii': '3'}

    # HIGH PRIORITY: "NOVO GRAU HISTOLOGICO FINAL: GRAU N MBR" (amended reports)
    m = re.search(
        r'novo\s+grau\s+histologico\s+final\s*:\s*grau\s+(i{1,3}|[1-3])\s+(?:de\s+)?'
        r'(?:nottingham|mbr|bloom\s+richardson|richardson)',
        text_norm)
    if m:
        val = ROMAN_MAP.get(m.group(1), m.group(1))
        return val, m.group(0).strip()[:80]

    # "GRAU [HISTOLOGICO:] GRAU N [DE] NOTTINGHAM/MBR/BLOOM RICHARDSON"
    m = re.search(
        r'grau\s+(?:histologico\s*:\s*)?grau\s+(i{1,3}|[1-3])\s+(?:de\s+)?'
        r'(?:nottingham|mbr|bloom\s+richardson|richardson)',
        text_norm)
    if m:
        val = ROMAN_MAP.get(m.group(1), m.group(1))
        return val, m.group(0).strip()[:80]

    # "GRAU N [DE] NOTTINGHAM/MBR" (without "GRAU HISTOLOGICO:" prefix)
    m = re.search(
        r'grau\s+(i{1,3}|[1-3])\s+(?:de\s+)?(?:nottingham|mbr|bloom\s+richardson|richardson)',
        text_norm)
    if m:
        val = ROMAN_MAP.get(m.group(1), m.group(1))
        return val, m.group(0).strip()[:80]

    # "GRAU N MBR" (minimal format)
    m = re.search(r'grau\s+(i{1,3}|[1-3])\s+mbr\b', text_norm)
    if m:
        val = ROMAN_MAP.get(m.group(1), m.group(1))
        return val, m.group(0).strip()[:80]

    # Score decomposition: "3+2+1=6" or "(3+3+2=8)" → derive grade from total
    m = re.search(r'(\d)\s*\+\s*(\d)\s*\+\s*(\d)\s*=\s*(\d+)', text_norm)
    if m:
        total = int(m.group(4))
        if 3 <= total <= 5:
            return '1', f'score {m.group(0).strip()} → grade 1'
        elif 6 <= total <= 7:
            return '2', f'score {m.group(0).strip()} → grade 2'
        elif 8 <= total <= 9:
            return '3', f'score {m.group(0).strip()} → grade 3'

    return None, None


# ===========================================================================
# NOTTINGHAM COMPONENT SCORES — Tubular, Nuclear, Mitotic
# ===========================================================================

def detect_nottingham_components(text_norm: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Extract Nottingham component scores: tubular, nuclear, mitotic.
    Returns (esc_tubular, esc_nuclear, esc_mitotico, evidence).

    Supports multiple text formats:
    - Structured triple: "diferenciacao tubular: 2; grau nuclear: 3; indice mitotico: 1"
    - Narrative with "escore N": "grau tubular: intermediario, escore 2"
    - Score in parentheses: "grau nuclear: alto grau (3)"
    - Bare number after colon: "formacao tubular: 3"
    - Score decomposition: "2+3+1=6"
    - Synonyms: formacao tubular, pleomorfismo nuclear, mitose/mitoses
    - Zero-padded: "02" → "2"
    - Non-standard separators: "tubular 3: nuclear 2: mitotico 1"
    """
    # Keyword groups for each component
    TUB_KW = r'(?:diferenciacao|formacao|grau)\s+tubular'
    NUC_KW = r'(?:grau|pleomorfismo)\s+nuclear'
    MIT_KW = r'(?:indice\s+mitotico|indico\s+mitotico|grau\s+mitotico|mitoses?)'

    # Descriptor → score mapping
    DESC_MAP = {
        'baixo': '1', 'leve': '1',
        'intermediario': '2', 'moderado': '2',
        'elevado': '3', 'alto': '3',
    }

    def _extract_score(kw_pat, text):
        """Try to extract score 1-3 after a keyword. Returns (value, evidence_str).
        First pass: try ALL keyword occurrences for structured patterns (1-4).
        Second pass: fall back to descriptor mapping (5) only if no structured match.
        Uses fixed-width window to prevent catastrophic backtracking.
        """
        # Pass 1: try structured patterns on ALL keyword occurrences
        for kw_match in re.finditer(kw_pat, text):
            start = kw_match.start()
            window = text[start:start + 80]
            # Strategy 1: "escore N"
            m = re.search(kw_pat + r'.{0,40}escore\s+0?([1-3])', window)
            if m:
                return m.group(1), m.group(0).strip()[:40]
            # Strategy 2: "descriptor (N)" — score in parens
            m = re.search(kw_pat + r'\s*:?\s*.{0,30}\(0?([1-3])\)', window)
            if m:
                return m.group(1), m.group(0).strip()[:40]
            # Strategy 3: bare number after colon
            m = re.search(kw_pat + r'\s*:\s*0?([1-3])\b', window)
            if m:
                return m.group(1), m.group(0).strip()[:40]
            # Strategy 4: "keyword N:" — number before colon (non-standard separator)
            m = re.search(kw_pat + r'\s+0?([1-3])\s*:', window)
            if m:
                return m.group(1), m.group(0).strip()[:40]

        # Pass 2: descriptor mapping — only if no structured match found anywhere
        for kw_match in re.finditer(kw_pat, text):
            start = kw_match.start()
            window = text[start:start + 80]
            m = re.search(kw_pat + r'\s*:?\s*(baixo|leve|intermediario|moderado|elevado|alto)\b', window)
            if m:
                val = DESC_MAP.get(m.group(1))
                if val:
                    return val, m.group(0).strip()[:40]
        return None, None

    tub_val, tub_ev = _extract_score(TUB_KW, text_norm)
    nuc_val, nuc_ev = _extract_score(NUC_KW, text_norm)
    mit_val, mit_ev = _extract_score(MIT_KW, text_norm)

    if any([tub_val, nuc_val, mit_val]):
        parts = [e for e in [tub_ev, nuc_ev, mit_ev] if e]
        return tub_val, nuc_val, mit_val, ' | '.join(parts)

    # Fallback: score decomposition (A+B+C=D)
    m = re.search(r'(\d)\s*\+\s*(\d)\s*\+\s*(\d)\s*=\s*(\d+)', text_norm)
    if m:
        return m.group(1), m.group(2), m.group(3), f'score {m.group(0).strip()}'

    return None, None, None, None


# ===========================================================================
# DERIVED VARIABLES — behavior_class, invasive_malignancy, malignancy_any
# ===========================================================================

INVASIVE_TYPES = {'CDI_SOE', 'CLI', 'misto', 'mucinoso', 'tubular', 'medular',
                  'micropapilar', 'papilar', 'metaplasico', 'outro_maligno'}
INSITU_TYPES = {'CDIS', 'CLIS'}
BENIGN_TYPES = {'fibroadenoma', 'fibrocistico', 'outro_benigno'}

def derive_behavior_class(tipo_hist: Optional[str]) -> Optional[str]:
    """Derive behavior class from histological type."""
    if tipo_hist is None:
        return None
    if tipo_hist in INVASIVE_TYPES:
        return 'invasivo'
    if tipo_hist in INSITU_TYPES:
        return 'in_situ'
    if tipo_hist in BENIGN_TYPES:
        return 'benigno'
    return None

def derive_invasive_malignancy(tipo_hist: Optional[str]) -> Optional[str]:
    """Derive whether there is invasive malignancy."""
    if tipo_hist is None:
        return None
    return 'sim' if tipo_hist in INVASIVE_TYPES else 'nao'

def derive_malignancy_any(tipo_hist: Optional[str]) -> Optional[str]:
    """Derive whether there is any malignancy (including in situ)."""
    if tipo_hist is None:
        return None
    return 'sim' if tipo_hist in (INVASIVE_TYPES | INSITU_TYPES) else 'nao'


# ===========================================================================
# INV_LINFOV — Lymphovascular invasion
# ===========================================================================

def detect_inv_linfov(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect lymphovascular invasion. Negation-first, BIN.
    Handles combined pattern: 'invasao angiolinfatica e perineural: nao evidenciada'
    """
    # Synonym group: linfo-vascular | angiolinfática | vascular linfática
    LV_SYN = r'(?:linfo[\s-]?vascular|angiolinfatica|vascular\s+linfatica)'
    # Verb: invasão | infiltração
    INV_VERB = r'(?:invasao|infiltracao)'
    # Negation tail (handles typo "evidencias" besides standard forms)
    NEG_TAIL = r'(?:nao\s+(?:observad|evidenciad|visualizad|identificad)\w*|nao\s+evidencias?\b|ausente)'

    # Negation patterns (check FIRST)
    neg_pats = [
        INV_VERB + r'\s+' + LV_SYN + r'(?:\s+e\s+(?:peri)?neural)?\s*:\s*' + NEG_TAIL,
        r'sem\s+' + INV_VERB.replace('(?:', '(?:') + r'\s+' + LV_SYN,
        INV_VERB + r'\s+' + LV_SYN + r'(?:\s+e\s+(?:peri)?neural)?\s+' +
        r'nao\s+(?:observad|evidenciad|visualizad|identificad)',
    ]
    for pat in neg_pats:
        m = re.search(pat, text_norm)
        if m:
            return 'nao', m.group(0).strip()[:100]

    # Positive patterns
    pos_pats = [
        INV_VERB + r'\s+' + LV_SYN + r'(?:\s+e\s+(?:peri)?neural)?\s*:\s*(?:presente|sim)',
        INV_VERB + r'\s+' + LV_SYN + r'\s+presente',
        r'embolos?\s+(?:neoplasicos?\s+)?(?:em\s+)?(?:linfaticos?|vasculares?)',
    ]
    for pat in pos_pats:
        m = re.search(pat, text_norm)
        if m:
            return 'sim', m.group(0).strip()[:100]

    return None, None


# ===========================================================================
# INV_PERIN — Perineural invasion
# ===========================================================================

def detect_inv_perin(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect perineural invasion. Negation-first, BIN.
    Handles combined pattern: 'invasao angiolinfatica e perineural: nao evidenciada'
    """
    # Verb: invasão | infiltração
    INV_VERB_P = r'(?:invasao|infiltracao)'
    # Perineural synonyms: perineural | neural | filetes nervosos
    PN_SYN = r'(?:(?:peri)?neural(?:\s+e\s+neural)?|filetes?\s+nervosos?)'
    # Combined prefix: angiolinfática e perineural
    COMBINED_P = r'(?:angiolinfatica|linfo[\s-]?vascular|vascular\s+linfatica)\s+e\s+' + PN_SYN
    # Negation tail
    NEG_TAIL_P = r'(?:nao\s+(?:observad|evidenciad|visualizad|identificad)\w*|nao\s+evidencias?\b|ausente)'

    # Negation patterns (check FIRST)
    neg_pats = [
        INV_VERB_P + r'\s+' + PN_SYN + r'\s*:\s*' + NEG_TAIL_P,
        r'sem\s+' + INV_VERB_P + r'\s+' + PN_SYN,
        INV_VERB_P + r'\s+' + PN_SYN + r'\s+nao\s+(?:observad|evidenciad|visualizad|identificad)',
        # Combined: "invasao angiolinfatica e perineural: nao evidenciada"
        INV_VERB_P + r'\s+' + COMBINED_P + r'\s*:\s*' + NEG_TAIL_P,
        INV_VERB_P + r'\s+' + COMBINED_P + r'\s+nao\s+(?:observad|evidenciad|visualizad|identificad)',
    ]
    for pat in neg_pats:
        m = re.search(pat, text_norm)
        if m:
            return 'nao', m.group(0).strip()[:100]

    # Positive patterns
    pos_pats = [
        INV_VERB_P + r'\s+' + PN_SYN + r'\s*:\s*(?:presente|sim)',
        INV_VERB_P + r'\s+' + PN_SYN + r'\s+presente',
        INV_VERB_P + r'\s+' + COMBINED_P + r'\s*:\s*(?:presente|sim)',
    ]
    for pat in pos_pats:
        m = re.search(pat, text_norm)
        if m:
            return 'sim', m.group(0).strip()[:100]

    return None, None


# ===========================================================================
# NECROSE_TUMORAL — Tumor necrosis
# ===========================================================================

def detect_necrose_tumoral(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect tumor necrosis. Negation-first, BIN.
    Matches structured fields: 'necrose tumoral:' AND 'necrose:' (without tumoral).
    Also matches 'sem necrose' (semi-structured negation in CDIS descriptions).
    Excludes: narrative 'com necrose' (CDIS comedo pattern), 'necrose adiposa'
    (fat necrosis), 'comedonecrose' (compound word).
    """
    NEG_TAIL = r'(?:nao\s+(?:observad|evidenciad|visualizad|identificad)\w*|ausente|nao\s+evidencias?\b)'

    # Negation patterns (check FIRST)
    neg_pats = [
        r'necrose\s+tumoral\s*:\s*' + NEG_TAIL,
        r'sem\s+necrose\s+tumoral',
        # Structured 'necrose:' without 'tumoral' (colon ensures structured field)
        r'(?<!\w)necrose\s*:\s*' + NEG_TAIL,
        # Semi-structured 'sem necrose' (but NOT 'sem necrose adiposa')
        r'sem\s+necrose(?!\s+adiposa)',
    ]
    for pat in neg_pats:
        m = re.search(pat, text_norm)
        if m:
            return 'nao', m.group(0).strip()[:100]

    # Positive patterns
    pos_pats = [
        r'necrose\s+tumoral\s*:\s*(?:presente|sim|focal|extensa|central)',
        r'necrose\s+tumoral\s*:\s*presente\s+do\s+tipo\s+\w+',
        r'necrose\s+tumoral\s+presente',
        # Structured 'necrose:' without 'tumoral'
        r'(?<!\w)necrose\s*:\s*(?:presente|sim|focal|extensa|central)',
    ]
    for pat in pos_pats:
        m = re.search(pat, text_norm)
        if m:
            return 'sim', m.group(0).strip()[:100]

    return None, None


# ===========================================================================
# MULTIFOCALIDADE — Multifocality / multicentricity
# ===========================================================================

def detect_multifocalidade(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect multifocality/multicentricity. Negation-first, BIN."""
    NEG_TAIL = r'(?:nao\s+(?:observad|evidenciad|identificad)\w*|ausente|unic[oa])'

    # Negation patterns
    neg_pats = [
        r'multifocalidade\s*/?\s*multicentricidade\s*:\s*' + NEG_TAIL,
        r'(?:multifocal|multicentr)\w*\s*:\s*' + NEG_TAIL,
        r'focalidade[^:]*:\s*(?:unifocal|foco\s+unico|unico)',
    ]
    for pat in neg_pats:
        m = re.search(pat, text_norm)
        if m:
            return 'nao', m.group(0).strip()[:100]

    # Positive patterns
    pos_pats = [
        r'multifocalidade\s*/?\s*multicentricidade\s*:\s*(?:presente|sim|observad)',
        r'(?:multifocal|multicentr)\w*\s*:\s*(?:presente|sim|observad)',
        r'focalidade[^:]*:\s*(?:multifocal|multicentr|multiplos?\s+focos|dois\s+focos|\(?\s*dois\s+focos\s*\)?)',
        r'focalidade[^:]*:\s*(?:presenca\s+de\s+focos)',
        r'(?:multifocal|multicentr)\w*',
    ]
    for pat in pos_pats:
        m = re.search(pat, text_norm)
        if m:
            return 'sim', m.group(0).strip()[:100]

    return None, None


# ===========================================================================
# MICROCALCIFICACOES — Microcalcifications
# ===========================================================================

def detect_microcalcificacoes(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect microcalcifications. Negation-first, BIN.
    Matches structured 'microcalcificacoes:' and semi-structured patterns.
    Excludes narrative 'com microcalcificacoes' in CDIS descriptions.
    """
    NEG_TAIL = r'(?:nao\s+(?:observad|evidenciad|visualizad|identificad)\w*|ausentes?|nao\s+evidencias?\b)'

    # Negation patterns (check FIRST)
    neg_pats = [
        r'microcalcificac\w*\s*:\s*' + NEG_TAIL,
        r'sem\s+microcalcificac',
    ]
    for pat in neg_pats:
        m = re.search(pat, text_norm)
        if m:
            return 'nao', m.group(0).strip()[:100]

    # Positive patterns — structured fields only
    pos_pats = [
        r'microcalcificac\w*\s*:\s*(?:presente|presentes|sim|observad\w*)',
        r'microcalcificac\w*\s+presentes?\b',
    ]
    for pat in pos_pats:
        m = re.search(pat, text_norm)
        if m:
            return 'sim', m.group(0).strip()[:100]

    return None, None


# ===========================================================================
# PELE — Skin involvement
# ===========================================================================

def detect_pele(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect skin involvement. BIN.
    Priority: positive (comprometida) > negative (livre) > not-assessable (None).
    In bilateral cases multiple pele specimens may exist; a definitive answer
    anywhere in the text takes priority over 'sem particularidades'/'nao amostrada'.
    """
    # PELE_PREFIX matches 'pele', 'pele de mama direita', 'pele da mama', etc.
    # Note: esquerd[oa]/direit[oa] handles both feminine (mama esquerda) and
    # masculine (tecido mamario esquerdo) forms.
    PELE_PFX = r'pele(?:\s+(?:d[aeo]\s+)?(?:mama|tecido\s+mamario|plastrao|areola)(?:\s+(?:esquerd[oa]|direit[oa]))?)?'

    # 1. Positive patterns — skin invasion (check FIRST — most clinically important)
    pos_pats = [
        PELE_PFX + r'\s*(?::\s*\.?\s*|\s+)comprometid\w*',
        PELE_PFX + r'\s*(?::\s*\.?\s*|\s+)(?:neoplasia\s+invade|infiltrad|acometid)\w*',
        r'derme\s+(?:profunda\s+)?comprometid\w*\s+(?:pela\s+)?neoplasi',
    ]
    for pat in pos_pats:
        m = re.search(pat, text_norm)
        if m:
            return 'sim', m.group(0).strip()[:100]

    # 2. Negation patterns (livre = free of tumor)
    neg_pats = [
        PELE_PFX + r'\s*(?::\s*\.?\s*|\s+)livr\w*(?:\s+de\s+(?:neoplasia|comprometimento\s+neoplas\w*))?',
        PELE_PFX + r'\s*:\s*\.?\s*(?:nao\s+(?:evidenciad|comprometid)\w*|sem\s+neoplasia)',
        r'pele\s+e\s+mamilo\s*:\s*livr\w*',
    ]
    for pat in neg_pats:
        m = re.search(pat, text_norm)
        if m:
            return 'nao', m.group(0).strip()[:100]

    # 3. Not-assessable (only if no definitive answer found above)
    # 'nao amostrada'/'nao avaliavel' or 'sem particularidades' → None
    na_pats = [
        PELE_PFX + r'\s*(?::\s*\.?\s*)?(?:nao\s+amostra|nao\s+avalia)',
        PELE_PFX + r'(?:\s*:\s*\.?\s*|\s+)sem\s+particularidades',
    ]
    for pat in na_pats:
        m = re.search(pat, text_norm)
        if m:
            return None, None

    return None, None


# ===========================================================================
# MAMILO — Nipple involvement
# ===========================================================================

def detect_mamilo(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect nipple involvement. BIN.
    Priority: positive (comprometido) > negative (livre) > not-assessable (None).
    In bilateral cases, definitive answer takes priority over 'sem particularidades'.
    """
    # MAMILO_PREFIX matches 'mamilo', 'mamilo e areola de mama direita', 'areola e mamilo da mama', etc.
    MAMILO_PFX = r'(?:mamilo(?:\s+e\s+areola)?|areola\s+e\s+mamilo)(?:\s+(?:d[aeo]\s+)?(?:mama|tecido\s+mamario)(?:\s+(?:esquerd[oa]|direit[oa]))?)?'

    # 1. Positive patterns — nipple involvement (check FIRST)
    pos_pats = [
        MAMILO_PFX + r'\s*(?::\s*\.?\s*|\s+)comprometid\w*',
        MAMILO_PFX + r'\s*(?::\s*\.?\s*|\s+)(?:neoplasia\s+invade|infiltrad|acometid)\w*',
    ]
    for pat in pos_pats:
        m = re.search(pat, text_norm)
        if m:
            return 'sim', m.group(0).strip()[:100]

    # 2. Negation patterns (livre = free of tumor)
    neg_pats = [
        MAMILO_PFX + r'\s*(?::\s*\.?\s*|\s+)livr\w*(?:\s+de\s+(?:neoplasia|comprometimento|carcinom\w*))?',
        MAMILO_PFX + r'\s*:\s*\.?\s*(?:nao\s+(?:evidenciad|comprometid)\w*|sem\s+neoplasia)',
        r'pele\s+e\s+mamilo\s*:\s*livr\w*',
    ]
    for pat in neg_pats:
        m = re.search(pat, text_norm)
        if m:
            return 'nao', m.group(0).strip()[:100]

    # 3. Not-assessable (only if no definitive answer found)
    na_pats = [
        MAMILO_PFX + r'\s*(?::\s*\.?\s*)?(?:nao\s+amostra|nao\s+avalia)',
        MAMILO_PFX + r'(?:\s*:\s*\.?\s*|\s+)sem\s+particularidades',
    ]
    for pat in na_pats:
        m = re.search(pat, text_norm)
        if m:
            return None, None

    return None, None


# ===========================================================================
# EFEITO_TERAPEUTICO — Neoadjuvant therapy response
# ===========================================================================

def detect_efeito_terapeutico(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect neoadjuvant therapy response. CAT.
    Values: completa, parcial, sem_info, None.
    'ausencia de informacoes clinicas' = sem_info (explicitly not neoadjuvant).
    Reports that don't mention efeito_terapeutico at all = None.
    """
    # Match the structured field
    m = re.search(r'(?:efeito\s+terapeutico|resposta\s+(?:ao\s+tratamento|a\s+quimioterapia)\s+neoadjuvante)\s*:\s*([^;.]+)', text_norm)
    if not m:
        return None, None

    val_text = m.group(1).strip()

    # Check for 'no info about pre-op therapy'
    if re.search(r'ausencia\s+de\s+informac', val_text):
        return 'sem_info', m.group(0).strip()[:100]

    # Check for complete response (pCR)
    if re.search(r'completa|desaparecimento\s+de\s+todo|ausencia\s+de\s+neoplasia', val_text):
        return 'completa', m.group(0).strip()[:100]

    # Check for partial response
    if re.search(r'parcial|presenca\s+de\s+carcinoma|residual', val_text):
        return 'parcial', m.group(0).strip()[:100]

    # Fallback: unrecognized value → return raw
    return val_text[:30], m.group(0).strip()[:100]


# ===========================================================================
# INFILTRADO_LINFOCITICO — Tumor-infiltrating lymphocytes (TILs)
# ===========================================================================

def detect_infiltrado_linfocitico(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect tumor-infiltrating lymphocytes (TILs). CAT.
    Values: escasso, moderado, intenso, nao_evidenciado, None.
    Structured field: 'INFILTRADO LINFOCITICO INTRATUMORAL: VALUE'
    Also matches: 'TILs', 'reacao linfocitaria', 'componente linfocitico'.
    """
    # Match structured field patterns
    # Note: 'intramural' is a known typo for 'intratumoral' (23027631AP)
    m = re.search(
        r'(?:infiltrado\s+linfocit\w*(?:\s+(?:intratumoral|intramural|peritumoral|estromal))?'
        r'|componente\s+linfocit\w*'
        r'|reacao\s+linfocitaria'
        r'|tils?)\s*:\s*([^;.\n]+)',
        text_norm
    )
    if not m:
        return None, None

    val_text = m.group(1).strip()

    # Escasso / discreto (low TILs) — check BEFORE negation to avoid routing through it
    if re.search(r'escasso|discreto|leve|raro', val_text):
        return 'escasso', m.group(0).strip()[:100]

    # Negation / absent
    if re.search(r'nao\s+(?:observad|evidenciad|identificad)\w*|ausente', val_text):
        return 'nao_evidenciado', m.group(0).strip()[:100]

    # Intensity categories (feminine/masculine: moderado/moderada, intenso/intensa)
    if re.search(r'intens[oa]|exuberante|acentuad|proeminente', val_text):
        return 'intenso', m.group(0).strip()[:100]
    if re.search(r'moderad[oa]', val_text):
        return 'moderado', m.group(0).strip()[:100]

    # Presence without qualifier
    if re.search(r'presente|sim|observad', val_text):
        return 'presente', m.group(0).strip()[:100]

    # Fallback: unrecognized value → return raw
    return val_text[:30], m.group(0).strip()[:100]


# ===========================================================================
# EXTENSAO_EXTRANODAL — Extranodal extension
# ===========================================================================

def detect_extensao_extranodal(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect extranodal extension. Negation-first, BIN.
    Structured field: 'EXTENSAO EXTRANODAL: PRESENTE/NAO OBSERVADA'
    Also: 'extensao extracapsular', 'rompimento capsular'.
    Only relevant in surgical specimens with lymph nodes (ca_peca stratum).
    """
    NEG_TAIL = r'(?:nao\s+(?:observad|evidenciad|visualizad|identificad)\w*|ausente|nao\s+evidencias?\b)'

    # Negation patterns (check FIRST)
    neg_pats = [
        r'extens.o\s+extra[\s-]?nodal\s*:\s*' + NEG_TAIL,
        r'sem\s+extens.o\s+extra[\s-]?nodal',
        r'extens.o\s+extra[\s-]?capsular\s*:\s*' + NEG_TAIL,
        r'sem\s+extens.o\s+extra[\s-]?capsular',
        r'rompimento\s+capsular\s*:\s*' + NEG_TAIL,
        r'sem\s+rompimento\s+capsular',
    ]
    for pat in neg_pats:
        m = re.search(pat, text_norm)
        if m:
            return 'nao', m.group(0).strip()[:100]

    # Positive patterns
    EXTRA_SYN = r'extra[\s-]?(?:nodal|capsular)'
    pos_pats = [
        r'extens.o\s+' + EXTRA_SYN + r'\s*:\s*(?:presente|sim|observad)',
        r'extens.o\s+' + EXTRA_SYN + r'\s+presente',
        r'(?:com|apresentando)\s+extens.o\s+' + EXTRA_SYN,
        r'presenca\s+de\s+extens.o\s+' + EXTRA_SYN,
        r'rompimento\s+capsular\s*:\s*(?:presente|sim)',
        r'rompimento\s+capsular\s+presente',
    ]
    for pat in pos_pats:
        m = re.search(pat, text_norm)
        if m:
            return 'sim', m.group(0).strip()[:100]

    return None, None


# ===========================================================================
# REF_IHQ — IHQ referral
# ===========================================================================

def detect_ref_ihq(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect IHQ referral. Returns (sim/nao/None, evidence with IH number if present)."""
    # "VIDE EXAME COMPLEMENTAR [Nº] NNNNNNIH" or "VIDE EXAME DE IMUNOHISTOQUIMICA [Nº] NNNNNNIH"
    m = re.search(r'vide\s+exame\s+(?:complementar|(?:de\s+)?imuno[\s-]?histoquimica)\s+(?:n[o.º°]?\s+)?(\d+[\w-]*ih)\b',
                  text_norm)
    if m:
        return 'sim', m.group(0).strip()[:100]

    # "EXAME DE IMUNOHISTOQUIMICA [Nº] NNNNNNIH"
    m = re.search(r'exame\s+de\s+imuno[\s-]?histoquimica\s+(?:n[o.º°]?\s+)?(\d+[\w-]*ih)\b', text_norm)
    if m:
        return 'sim', m.group(0).strip()[:100]

    # Generic "imunohistoquimica" or "imuno-histoquimica" mention (recommendation)
    m = re.search(r'(?:sugerimos|necessidade\s+de|encaminhado\s+para)\s+(?:\w+\s+){0,5}imuno[\s-]?histoquimic', text_norm)
    if m:
        return 'sim', m.group(0).strip()[:100]

    # "VIDE" + IH number anywhere
    m = re.search(r'vide\s+\w+\s+(\d+[\w-]*ih)\b', text_norm)
    if m:
        return 'sim', m.group(0).strip()[:100]

    return None, None


# ===========================================================================
# SMOKE TESTS
# ===========================================================================

def run_smoke_tests():
    """Run smoke tests for all detectors."""
    passed = 0
    failed = 0

    def check(name, got, expected):
        nonlocal passed, failed
        if got == expected:
            passed += 1
        else:
            failed += 1
            print(f'  FAIL {name}: got={got}, expected={expected}')

    # --- tam_cm ---
    t = normalize("MAIOR EXTENSÃO DA NEOPLASIA NESTA AMOSTRA: 8,0 mm.")
    v, _ = detect_tam_cm(t)
    check("tam_cm_extensao", v, "8,0 mm")

    t = normalize("TAMANHO DA NEOPLASIA INVASIVA: 2,2 X 1,5 X 1,5 CM;")
    v, _ = detect_tam_cm(t)
    check("tam_cm_tamanho_inv", v, "2,2 x 1,5 x 1,5 cm")

    t = normalize("TAMANHO DO TUMOR: 1,5 X 1,3 X 1,0 CM;")
    v, _ = detect_tam_cm(t)
    check("tam_cm_tumor", v, "1,5 x 1,3 x 1,0 cm")

    t = normalize("TAMANHO DA NEOPLASIA INVASIVA: 2 mm (MAIOR DIÂMETRO)")
    v, _ = detect_tam_cm(t)
    check("tam_cm_int", v, "2 mm")

    t = normalize("FIBROADENOMA SEM ATIPIAS")
    v, _ = detect_tam_cm(t)
    check("tam_cm_none", v, None)

    t = normalize("MAIOR DIMENSÃO LINEAR DA NEOPLASIA, NAS AMOSTRAS:1,3 cm = 13,0 mm;")
    v, _ = detect_tam_cm(t)
    check("tam_cm_dimensao", v, "1,3 cm")

    # --- comp_insitu ---
    t = normalize('COMPONENTE "IN SITU": NÃO OBSERVADO.')
    v, _ = detect_comp_insitu(t)
    check("insitu_neg", v, "nao")

    t = normalize('COMPONENTE "IN SITU": PRESENTE, ALTO GRAU NUCLEAR')
    v, _ = detect_comp_insitu(t)
    check("insitu_pos", v, "sim")

    t = normalize("REPRESENTADO POR CDIS, PADRÃO SÓLIDO")
    v, _ = detect_comp_insitu(t)
    check("insitu_cdis", v, "sim")

    t = normalize("CARCINOMA DUCTAL IN SITU (CDIS) DE MAMA DIREITA.")
    v, _ = detect_comp_insitu(t)
    check("insitu_primary", v, "sim")

    t = normalize("SEM COMPONENTE IN SITU")
    v, _ = detect_comp_insitu(t)
    check("insitu_sem", v, "nao")

    t = normalize("FIBROADENOMA")
    v, _ = detect_comp_insitu(t)
    check("insitu_benign_none", v, None)

    # --- procedimento ---
    t = normalize("PROCEDIMENTO CIRÚRGICO: MASTECTOMIA RADICAL À DIREITA")
    v, _ = detect_procedimento(t)
    check("proc_mast_rad", v, "mastectomia_radical")

    t = normalize("BIÓPSIA POR AGULHA DE MAMA ESQUERDA:")
    v, _ = detect_procedimento(t)
    check("proc_agulha", v, "biopsia_agulha")

    t = normalize("PRODUTO DE MAMOTOMIA, APRESENTANDO:")
    v, _ = detect_procedimento(t)
    check("proc_mamotomia", v, "mamotomia")

    t = normalize("PROCEDIMENTO CIRÚRGICO: SETORECTOMIA;")
    v, _ = detect_procedimento(t)
    check("proc_setor", v, "excisional")

    t = normalize("PROCEDIMENTO CIRÚRGICO: BIÓPSIA EXCISIONAL;")
    v, _ = detect_procedimento(t)
    check("proc_excisional", v, "excisional")

    t = normalize("PROCEDIMENTO REALIZADO: MAMOTOMIA;")
    v, _ = detect_procedimento(t)
    check("proc_labeled_mamo", v, "mamotomia")

    t = normalize("RESSECÇÃO SEGMENTAR/MAMA ESQUERDA")
    v, _ = detect_procedimento(t)
    check("proc_resseccao", v, "excisional")

    # --- tipo_hist ---
    t = normalize("CARCINOMA DUCTAL INVASIVO, DO TIPO NÃO ESPECIAL (OMS 2019)")
    v, _ = detect_tipo_hist(t)
    check("tipo_cdi_nst", v, "CDI_SOE")

    t = normalize("CARCINOMA LOBULAR INVASIVO, VARIANTE PLEOMÓRFICA")
    v, _ = detect_tipo_hist(t)
    check("tipo_cli", v, "CLI")

    t = normalize("TIPO CARCINOMA MICROPAPILAR INVASIVO")
    v, _ = detect_tipo_hist(t)
    check("tipo_micropap", v, "micropapilar")

    t = normalize("CARCINOMA MAMÁRIO DUCTAL INVASIVO COM DIFERENCIAÇÃO MUCINOSA")
    v, _ = detect_tipo_hist(t)
    check("tipo_mucinoso", v, "mucinoso")

    t = normalize("FIBROADENOMA SEM ATIPIAS")
    v, _ = detect_tipo_hist(t)
    check("tipo_fibroadenoma", v, "fibroadenoma")

    t = normalize("CARCINOMA DUCTAL INFILTRANTE EM QSL DE MAMA DIREITA")
    v, _ = detect_tipo_hist(t)
    check("tipo_cdi_infilt", v, "CDI_SOE")

    t = normalize("CARCINOMA DUCTAL IN SITU DE MAMA ESQUERDA")
    v, _ = detect_tipo_hist(t)
    check("tipo_cdis_only", v, "CDIS")

    t = normalize("ALTERAÇÃO DE CÉLULAS COLUNARES SEM ATIPIAS; ADENOSE; CISTOS")
    v, _ = detect_tipo_hist(t)
    check("tipo_benign_adenose", v, "fibrocistico")  # 2+ features → fibrocystic

    # --- margens ---
    t = normalize("MARGENS CIRÚRGICAS: LIVRES DE NEOPLASIA (FORAM AVALIADAS CINCO MARGENS)")
    v, _ = detect_margens(t)
    check("marg_livres", v, "livres")

    t = normalize("MARGEM CIRÚRGICA PROFUNDA FOCALMENTE COMPROMETIDA")
    v, _ = detect_margens(t)
    check("marg_comprometida", v, "comprometidas")

    t = normalize("MARGENS CIRÚRGICAS LIVRES. A MARGEM CIRÚRGICA MAIS PRÓXIMA É A INFERIOR")
    v, _ = detect_margens(t)
    check("marg_exiguas", v, "exiguas")

    t = normalize("MARGENS CIRÚRGICAS TANGENCIADAS PELO FIBROADENOMA.")
    v, _ = detect_margens(t)
    check("marg_tangenciadas", v, "exiguas")

    # --- pT/pN ---
    t = normalize("ESTADIAMENTO PATOLÓGICO: pT2; pN1a; pMx.")
    v, _ = detect_pT(t)
    check("pT_standard", v, "pt2")
    v, _ = detect_pN(t)
    check("pN_standard", v, "pn1a")

    t = normalize("ESTADIAMENTO PATOLÓGICO: ypT2; ypN0;")
    v, _ = detect_pT(t)
    check("pT_neo", v, "ypt2")
    v, _ = detect_pN(t)
    check("pN_neo", v, "ypn0")

    t = normalize("ESTADIAMENTO (AJCC 7ª ED): pT1b(m) (CLI) pN0 (LS).")
    v, _ = detect_pT(t)
    check("pT_multifocal", v, "pt1b(m)")
    v, _ = detect_pN(t)
    check("pN_ls", v, "pn0 (ls)")

    t = normalize("ESTADIAMENTO ANATÔMICO (AJCC 8ª ED): pT1a pNx Mx")
    v, _ = detect_pT(t)
    check("pT_ajcc8", v, "pt1a")
    v, _ = detect_pN(t)
    check("pN_x", v, "pnx")

    t = normalize("ESTADIAMENTO ANATÔMICO  (AJCC 8ª ed): pT1mi;  pN0 (sn)")
    v, _ = detect_pT(t)
    check("pT_microinvasion", v, "pt1mi")

    t = normalize("ESTADIAMENTO: pT4d; pN3a")
    v, _ = detect_pT(t)
    check("pT_inflammatory", v, "pt4d")

    # --- comp_insitu patches ---
    t = normalize("CARCINOMA DUCTAL INFILTRANTE COM COMPONENTE IN SITU (INTRADUCTO) ASSOCIADO")
    v, _ = detect_comp_insitu(t)
    check("insitu_componente_associado", v, "sim")

    t = normalize("COMPONENTE DE CARCINOMA DUCTAL IN SITU (CDIS) ASSOCIADO")
    v, _ = detect_comp_insitu(t)
    check("insitu_componente_cdis", v, "sim")

    t = normalize("PRESENÇA DE CARCINOMA INTRADUCTO (CDIS)")
    v, _ = detect_comp_insitu(t)
    check("insitu_presenca_intraducto", v, "sim")

    t = normalize("CARCINOMA INTRADUCTAL PADRÃO SÓLIDO")
    v, _ = detect_comp_insitu(t)
    check("insitu_intraductal", v, "sim")

    # Negated CDIS/intraducto patterns (iteration 3 patches)
    t = normalize("CARCINOMA DUCTAL IN SITU (CDIS): NÃO EVIDENCIADO.")
    v, _ = detect_comp_insitu(t)
    check("insitu_cdis_nao_evidenciado", v, "nao")

    t = normalize("COMPONENTE INTRADUCTO: NÃO OBSERVADO.")
    v, _ = detect_comp_insitu(t)
    check("insitu_intraducto_nao_obs", v, "nao")

    t = normalize("AUSÊNCIA DE CARCINOMA DUCTAL IN SITU RESIDUAL.")
    v, _ = detect_comp_insitu(t)
    check("insitu_ausencia_cdis", v, "nao")

    t = normalize("COMPONENTE INTRADUCTO: PRESENTE, PADRÃO CRIBRIFORME")
    v, _ = detect_comp_insitu(t)
    check("insitu_intraducto_presente", v, "sim")

    t = normalize("NÃO SE EVIDENCIOU CARCINOMA DUCTAL IN SITU RESIDUAL")
    v, _ = detect_comp_insitu(t)
    check("insitu_nao_se_evidenciou_cdis", v, "nao")

    # Intraductal (with L) variant
    t = normalize("COMPONENTE INTRADUCTAL: PRESENTE, PADRÕES SÓLIDO, CRIBRIFORME")
    v, _ = detect_comp_insitu(t)
    check("insitu_intraductal_presente", v, "sim")

    t = normalize("COMPONENTE INTRADUCTAL: NÃO EVIDENCIADO")
    v, _ = detect_comp_insitu(t)
    check("insitu_intraductal_nao_evid", v, "nao")

    # Colon + positive qualifiers (extenso, focal)
    t = normalize("COMPONENTE INTRADUCTO: EXTENSO, ALTO GRAU NUCLEAR")
    v, _ = detect_comp_insitu(t)
    check("insitu_intraducto_extenso", v, "sim")

    t = normalize("COMPONENTE INTRADUCTO: FOCAL, PADRÃO CRIBRIFORME")
    v, _ = detect_comp_insitu(t)
    check("insitu_intraducto_focal", v, "sim")

    # Lobular in situ
    t = normalize("COMPONENTE LOBULAR IN SITU: PRESENTE")
    v, _ = detect_comp_insitu(t)
    check("insitu_lobular_presente", v, "sim")

    # "CARCINOMA INTRADUCTO: NÃO EVIDENCIADO" (primary negated)
    t = normalize("CARCINOMA INTRADUCTO: NÃO EVIDENCIADO")
    v, _ = detect_comp_insitu(t)
    check("insitu_carc_intraducto_nao", v, "nao")

    # "CARCINOMA DUCTAL IN SITU NÃO EVIDENCIADO" (inline negation)
    t = normalize("CARCINOMA DUCTAL IN SITU NÃO EVIDENCIADO")
    v, _ = detect_comp_insitu(t)
    check("insitu_cdis_inline_nao", v, "nao")

    # Multi-section: neg in one section, pos in another — positive wins
    t = normalize('COMPONENTE "IN SITU": NÃO OBSERVADO. XXX COMPONENTE "IN SITU": PRESENTE, GRAU NUCLEAR INTERMEDIÁRIO')
    v, _ = detect_comp_insitu(t)
    check("insitu_multi_section_pos_wins", v, "sim")

    # CDIS primary with "de" before qualifier
    t = normalize('CARCINOMA DUCTAL "IN SITU" DE ALTO GRAU NUCLEAR')
    v, _ = detect_comp_insitu(t)
    check("insitu_cdis_de_alto_grau", v, "sim")

    t = normalize('CARCINOMA DUCTAL "IN SITU" DE GRAU NUCLEAR MODERADO')
    v, _ = detect_comp_insitu(t)
    check("insitu_cdis_de_grau", v, "sim")

    # --- tipo_hist patches ---
    t = normalize("CARCINOMA DUCTAL MICROINVASIVO DE MAMA DIREITA")
    v, _ = detect_tipo_hist(t)
    check("tipo_microinvasivo", v, "CDI_SOE")

    t = normalize("CARCINOMA INTRADUCTAL PADRÃO SÓLIDO, ALTO GRAU")
    v, _ = detect_tipo_hist(t)
    check("tipo_intraductal_only", v, "CDIS")

    t = normalize("TUMOR PHYLLODES VARIANTE BENIGNA")
    v, _ = detect_tipo_hist(t)
    check("tipo_phyllodes", v, "outro_benigno")

    t = normalize("NECROSE ADIPOSA COM CALCIFICAÇÕES")
    v, _ = detect_tipo_hist(t)
    check("tipo_necrose", v, "outro_benigno")

    t = normalize("TECIDO MAMÁRIO APRESENTANDO: CISTOS; ADENOSE; METAPLASIA APÓCRINA; FIBROSE ESTROMAL")
    v, _ = detect_tipo_hist(t)
    check("tipo_fibrocistico_pattern", v, "fibrocistico")

    t = normalize("CARCINOMA PAPILAR ENCAPSULADO INVASIVO DE MAMA ESQUERDA")
    v, _ = detect_tipo_hist(t)
    check("tipo_papilar_enc", v, "papilar")

    t = normalize("CARCINOMA INVASIVO COM PADRÃO MISTO TIPO DUCTAL E LOBULAR")
    v, _ = detect_tipo_hist(t)
    check("tipo_misto", v, "misto")

    t = normalize("ADENOCARCINOMA POUCO DIFERENCIADO")
    v, _ = detect_tipo_hist(t)
    check("tipo_adenocarcinoma", v, "outro_maligno")

    # Iteration 4 patches
    t = normalize("A NEOPLASIA TEM PADRÃO MICROPAPILAR")
    v, _ = detect_tipo_hist(t)
    check("tipo_padrao_micropapilar", v, "micropapilar")

    t = normalize("CARCINOMA DUCTAL INFILTRANTE COM ÁREAS DE PADRÃO MUCINOSO")
    v, _ = detect_tipo_hist(t)
    check("tipo_areas_mucinoso_cdi", v, "CDI_SOE")

    t = normalize("CARCINOMA MUCINOSO DE MAMA ESQUERDA")
    v, _ = detect_tipo_hist(t)
    check("tipo_carcinoma_mucinoso", v, "mucinoso")

    t = normalize("HIPERPLASIA FIBROADENOMATÓIDE; FIBROSE ESTROMAL")
    v, _ = detect_tipo_hist(t)
    check("tipo_fibroadenomatoide_not_fibroadenoma", v, "fibrocistico")

    t = normalize("PAPILOMA INTRADUCTO; ADENOSE; MICROCISTOS; METAPLASIA APÓCRINA")
    v, _ = detect_tipo_hist(t)
    check("tipo_papiloma_over_fibrocystic", v, "outro_benigno")

    t = normalize("CARCINOMA LOBULAR INVASIVO (foco 1); CARCINOMA MAMÁRIO INVASIVO DO TIPO NÃO ESPECIAL (foco 2)")
    v, _ = detect_tipo_hist(t)
    check("tipo_misto_multifocal", v, "misto")

    t = normalize("CARCINOMA DUCTAL IN SITU COM FOCO DE MICROINVASÃO ESTROMAL")
    v, _ = detect_tipo_hist(t)
    check("tipo_cdis_microinvasao", v, "CDI_SOE")

    # CDIS with negated invasive — shared error bug fix
    t = normalize("AUSÊNCIA DE CARCINOMA INVASIVO. CARCINOMA DUCTAL IN SITU (CDIS) FOCAL. ESTADIAMENTO: PTIS")
    v, _ = detect_tipo_hist(t)
    check("tipo_cdis_ausencia_invasivo", v, "CDIS")

    # CDIS with "sem componente invasivo"
    t = normalize("CARCINOMA DUCTAL IN SITU (CDIS), SEM COMPONENTE INVASIVO")
    v, _ = detect_tipo_hist(t)
    check("tipo_cdis_sem_invasivo", v, "CDIS")

    # --- ln_exam/ln_pos ---
    t = normalize("LINFONODOS EXAMINADOS: 15; LINFONODOS COMPROMETIDOS: 02")
    ex, pos, _ = detect_ln(t)
    check("ln_standard", (ex, pos), (15, 2))

    t = normalize("LINFONODOS EXAMINADOS: 03; LINFONODOS COMPROMETIDOS: 00 (ZERO).")
    ex, pos, _ = detect_ln(t)
    check("ln_zero", (ex, pos), (3, 0))

    t = normalize("LINFONODO SENTINELA LIVRE DE METÁSTASE")
    ex, pos, _ = detect_ln(t)
    check("ln_sentinel", (ex, pos), (1, 0))

    t = normalize("DEZESSETE LINFONODOS COMPROMETIDOS EM VINTE E SETE LINFONODOS EXAMINADOS")
    ex, pos, _ = detect_ln(t)
    check("ln_words", (ex, pos), (27, 17))

    t = normalize("LINFONODOS SENTINELAS (5 LINFONODOS EXAMINADOS)")
    ex, pos, _ = detect_ln(t)
    check("ln_sentinel_count", (ex, pos), (5, None))

    t = normalize("LINFONODOS ISOLADOS: 02 (DOIS); LINFONODOS COMPROMETIDOS: 00 (ZERO);")
    ex, pos, _ = detect_ln(t)
    check("ln_isolados", (ex, pos), (2, 0))

    # --- margens worst-case-wins ---
    t = normalize("MARGENS CIRÚRGICAS: LIVRES DE COMPROMETIMENTO NEOPLÁSICO (MARGEM MAIS PRÓXIMA = ANTERIOR = 0,7 CM)")
    v, _ = detect_margens(t)
    check("marg_livres_com_proxima", v, "livres")

    t = normalize("MARGENS CIRÚRGICAS LIVRES. PORÉM AS MARGENS INFERIOR (DISTA 0,1 MM DO CDIS) E MEDIAL SÃO EXÍGUAS.")
    v, _ = detect_margens(t)
    check("marg_livres_porem_exiguas", v, "exiguas")

    t = normalize("MARGEM SUPERIOR: LIVRE; MARGEM ANTERIOR: COMPROMETIDA; MARGEM POSTERIOR: LIVRE")
    v, _ = detect_margens(t)
    check("marg_individual_worst_case", v, "comprometidas")

    # Iteration 7: "encontra-se livre" near margin context
    t = normalize("MARGENS CIRÚRGICAS: A MARGEM CIRÚRGICA PINTADA COM TINTA (BLOCO 3) ENCONTRA-SE LIVRE")
    v, _ = detect_margens(t)
    check("marg_encontra_se_livre", v, "livres")

    # "compromete as margens cirúrgicas"
    t = normalize("CARCINOMA QUE MEDE 2,5 CM E COMPROMETE AS MARGENS CIRÚRGICAS")
    v, _ = detect_margens(t)
    check("marg_compromete_as", v, "comprometidas")

    # Listed margins with colon: "margens ... anterior, posterior, ...: livres"
    t = normalize("MARGENS CIRÚRGICAS ANTERIOR, POSTERIOR, LATERAL, MEDIAL, SUPERIOR E INFERIOR: LIVRES DE NEOPLASIA")
    v, _ = detect_margens(t)
    check("marg_listed_livres", v, "livres")

    # --- procedimento fixes ---
    t = normalize("NO MATERIAL DE BIÓPSIA DE MAMA DIREITA")
    v, _ = detect_procedimento(t)
    check("proc_material_biopsia", v, "biopsia_agulha")

    t = normalize("PROCEDIMENTO: EXCISÃO DO NÓDULO")
    v, _ = detect_procedimento(t)
    check("proc_excisao", v, "excisional")

    # Iteration 7: biopsia_incisional → excisional (open surgical, not needle)
    t = normalize("CARCINOMA DUCTAL INFILTRANTE DE MAMA DIREITA (BIÓPSIA INCISIONAL)")
    v, _ = detect_procedimento(t)
    check("proc_biopsia_incisional", v, "excisional")

    # Earliest match wins: "biopsia por agulha" at pos 0 beats "biopsia excisional" in note
    t = normalize("BIÓPSIA POR AGULHA DE MAMA ESQUERDA. XXX COM AUXÍLIO DA BIÓPSIA EXCISIONAL")
    v, _ = detect_procedimento(t)
    check("proc_earliest_wins", v, "biopsia_agulha")

    # --- ln_exam fixes ---
    t = normalize("NOTA: FORAM AVALIADOS DOIS LINFONODOS.")
    ex, pos, _ = detect_ln(t)
    check("ln_foram_avaliados", (ex,), (2,))

    t = normalize("LINFONODO INTRAPARENQUIMATOSO (UM) LIVRE DE NEOPLASIA.")
    ex, pos, _ = detect_ln(t)
    check("ln_intraparenquimatoso_um", (ex, pos), (1, 0))

    # --- grau_nott ---
    t = normalize("GRAU HISTOLÓGICO: GRAU II DE NOTTINGHAM (MBR)")
    v, _ = detect_grau_nott(t)
    check("grau_II_nottingham", v, "2")

    t = normalize("GRAU III DE BLOOM RICHARDSON MODIFICADO")
    v, _ = detect_grau_nott(t)
    check("grau_III_bloom", v, "3")

    t = normalize("GRAU 1 MBR")
    v, _ = detect_grau_nott(t)
    check("grau_1_mbr", v, "1")

    t = normalize("GRAU HISTOLÓGICO: GRAU 2 DE NOTTINGHAM (MBR) (3+2+1=6)")
    v, _ = detect_grau_nott(t)
    check("grau_2_with_score", v, "2")

    t = normalize("DIFERENCIAÇÃO TUBULAR: 3; GRAU NUCLEAR: 3; ÍNDICE MITÓTICO: 2 (3+3+2=8)")
    v, _ = detect_grau_nott(t)
    check("grau_from_score_8", v, "3")

    t = normalize("FIBROADENOMA SEM ATIPIAS")
    v, _ = detect_grau_nott(t)
    check("grau_benign_none", v, None)

    # Iteration 7: amended report — "novo grau" should override earlier grau
    t = normalize("GRAU HISTOLÓGICO: GRAU 2 DE NOTTINGHAM (MBR). XXX NOVO GRAU HISTOLÓGICO FINAL: GRAU 1 MBR")
    v, _ = detect_grau_nott(t)
    check("grau_amended_novo", v, "1")

    # --- inv_linfov ---
    t = normalize("INVASÃO LINFO-VASCULAR: NÃO OBSERVADA.")
    v, _ = detect_inv_linfov(t)
    check("inv_linfov_neg", v, "nao")

    t = normalize("INVASÃO LINFO-VASCULAR: PRESENTE.")
    v, _ = detect_inv_linfov(t)
    check("inv_linfov_pos", v, "sim")

    t = normalize("INVASÃO ANGIOLINFÁTICA E PERINEURAL NÃO EVIDENCIADAS.")
    v, _ = detect_inv_linfov(t)
    check("inv_linfov_combined_neg", v, "nao")

    t = normalize("INVASÃO ANGIOLINFÁTICA: NÃO EVIDENCIADA.")
    v, _ = detect_inv_linfov(t)
    check("inv_linfov_angio_neg", v, "nao")

    t = normalize("ÊMBOLOS NEOPLÁSICOS EM LINFÁTICOS DÉRMICOS")
    v, _ = detect_inv_linfov(t)
    check("inv_linfov_embolos", v, "sim")

    t = normalize("FIBROADENOMA")
    v, _ = detect_inv_linfov(t)
    check("inv_linfov_benign_none", v, None)

    # Iteration 7 patches: split "vascular linfática" form
    t = normalize("INVASÃO VASCULAR LINFÁTICA: PRESENTE")
    v, _ = detect_inv_linfov(t)
    check("inv_linfov_vascular_linfatica_pos", v, "sim")

    t = normalize("INVASÃO VASCULAR LINFÁTICA: NÃO EVIDENCIADO")
    v, _ = detect_inv_linfov(t)
    check("inv_linfov_vascular_linfatica_neg", v, "nao")

    # "infiltração" instead of "invasão"
    t = normalize("INFILTRAÇÃO ANGIOLINFÁTICA E PERINEURAL: NÃO EVIDENCIADA")
    v, _ = detect_inv_linfov(t)
    check("inv_linfov_infiltracao_neg", v, "nao")

    # Typo "evidências" (plural)
    t = normalize("INVASÃO ANGIOLINFÁTICA E PERINEURAL: NÃO EVIDÊNCIAS")
    v, _ = detect_inv_linfov(t)
    check("inv_linfov_typo_evidencias", v, "nao")

    # --- inv_perin ---
    t = normalize("INVASÃO PERINEURAL: NÃO OBSERVADA.")
    v, _ = detect_inv_perin(t)
    check("inv_perin_neg", v, "nao")

    t = normalize("INVASÃO PERINEURAL: PRESENTE.")
    v, _ = detect_inv_perin(t)
    check("inv_perin_pos", v, "sim")

    t = normalize("INVASÃO PERINEURAL E NEURAL: NÃO EVIDENCIADA.")
    v, _ = detect_inv_perin(t)
    check("inv_perin_neural_neg", v, "nao")

    t = normalize("INVASÃO ANGIOLINFÁTICA E PERINEURAL: NÃO EVIDENCIADA.")
    v, _ = detect_inv_perin(t)
    check("inv_perin_combined_neg", v, "nao")

    t = normalize("INVASÃO PERINEURAL E NEURAL: PRESENTE.")
    v, _ = detect_inv_perin(t)
    check("inv_perin_neural_pos", v, "sim")

    # Iteration 7 patches: "filetes nervosos" synonym
    t = normalize("INVASÃO FILETES NERVOSOS: NÃO EVIDENCIADO")
    v, _ = detect_inv_perin(t)
    check("inv_perin_filetes_nervosos_neg", v, "nao")

    t = normalize("INVASÃO FILETES NERVOSOS: PRESENTE")
    v, _ = detect_inv_perin(t)
    check("inv_perin_filetes_nervosos_pos", v, "sim")

    # "infiltração" instead of "invasão" (combined)
    t = normalize("INFILTRAÇÃO ANGIOLINFÁTICA E PERINEURAL: NÃO EVIDENCIADA")
    v, _ = detect_inv_perin(t)
    check("inv_perin_infiltracao_combined_neg", v, "nao")

    # Typo "evidências"
    t = normalize("INVASÃO ANGIOLINFÁTICA E PERINEURAL: NÃO EVIDÊNCIAS")
    v, _ = detect_inv_perin(t)
    check("inv_perin_typo_evidencias", v, "nao")

    # --- ref_ihq ---
    t = normalize("VIDE EXAME COMPLEMENTAR 21002225-IH")
    v, _ = detect_ref_ihq(t)
    check("ref_ihq_complementar", v, "sim")

    t = normalize("VIDE EXAME DE IMUNOHISTOQUÍMICA 24002241IH")
    v, _ = detect_ref_ihq(t)
    check("ref_ihq_ihq", v, "sim")

    t = normalize("SUGERIMOS NOVA BIÓPSIA PARA ESTUDO IMUNOHISTOQUÍMICO")
    v, _ = detect_ref_ihq(t)
    check("ref_ihq_sugestao", v, "sim")

    t = normalize("CARCINOMA DUCTAL INVASIVO, SEM MENÇÃO A IHQ")
    v, _ = detect_ref_ihq(t)
    check("ref_ihq_none", v, None)

    # Iteration 7: "nº" (numero) between keyword and IH number
    t = normalize("VIDE EXAME DE IMUNOHISTOQUÍMICA Nº 15001245IH")
    v, _ = detect_ref_ihq(t)
    check("ref_ihq_numero_sign", v, "sim")

    # "vide exame imunohistoquimica" (without "de")
    t = normalize("VIDE EXAME IMUNOHISTOQUÍMICA Nº 15001246IH")
    v, _ = detect_ref_ihq(t)
    check("ref_ihq_sem_de", v, "sim")

    # --- Nottingham component scores ---
    t = normalize("GRAU HISTOLÓGICO: GRAU II DE NOTTINGHAM (MBR) [DIFERENCIAÇÃO TUBULAR: 3; GRAU NUCLEAR: 2; ÍNDICE MITÓTICO: 1]")
    tub, nuc, mit, _ = detect_nottingham_components(t)
    check("nott_comp_structured", (tub, nuc, mit), ('3', '2', '1'))

    t = normalize("DIFERENCIAÇÃO TUBULAR: 2; GRAU NUCLEAR: 3; ÍNDICE MITÓTICO: 2")
    tub, nuc, mit, _ = detect_nottingham_components(t)
    check("nott_comp_semicolon", (tub, nuc, mit), ('2', '3', '2'))

    t = normalize("GRAU NUCLEAR: INTERMEDIÁRIO, ESCORE 2; GRAU TUBULAR: ELEVADO, ESCORE 3; GRAU MITÓTICO: BAIXO, ESCORE 1")
    tub, nuc, mit, _ = detect_nottingham_components(t)
    check("nott_comp_narrative", (tub, nuc, mit), ('3', '2', '1'))

    t = normalize("(3+2+1=6)")
    tub, nuc, mit, _ = detect_nottingham_components(t)
    check("nott_comp_score_only", (tub, nuc, mit), ('3', '2', '1'))

    t = normalize("FIBROADENOMA SEM ATIPIAS")
    tub, nuc, mit, _ = detect_nottingham_components(t)
    check("nott_comp_benign_none", (tub, nuc, mit), (None, None, None))

    # Bug 1: bare number after colon (no "escore")
    t = normalize("GRAU NUCLEAR: 2; ÍNDICE MITÓTICO: 2; DIFERENCIAÇÃO TUBULAR: 2")
    tub, nuc, mit, _ = detect_nottingham_components(t)
    check("nott_comp_bare_number", (tub, nuc, mit), ('2', '2', '2'))

    # Bug 2: synonym keywords (formacao tubular, pleomorfismo nuclear, mitose)
    t = normalize("FORMAÇÃO TUBULAR: 3; PLEOMORFISMO NUCLEAR: 3; MITOSE: 3")
    tub, nuc, mit, _ = detect_nottingham_components(t)
    check("nott_comp_synonyms", (tub, nuc, mit), ('3', '3', '3'))

    # Bug 3: score in parentheses after descriptor
    t = normalize("GRAU NUCLEAR: ALTO GRAU (3)")
    tub, nuc, mit, _ = detect_nottingham_components(t)
    check("nott_comp_paren_score", (tub, nuc, mit), (None, '3', None))

    t = normalize("GRAU NUCLEAR: GRAU INTERMEDIÁRIO (2)")
    tub, nuc, mit, _ = detect_nottingham_components(t)
    check("nott_comp_paren_interm", (tub, nuc, mit), (None, '2', None))

    # Bug 4: zero-padded values
    t = normalize("FORMAÇÃO TUBULAR: 02; GRAU NUCLEAR: 02; MITOSES: 02")
    tub, nuc, mit, _ = detect_nottingham_components(t)
    check("nott_comp_zero_padded", (tub, nuc, mit), ('2', '2', '2'))

    # Bug 6: mixed with zero-pad
    t = normalize("FORMAÇÃO TUBULAR: 03; GRAU NUCLEAR: 02; MITOSES: 01")
    tub, nuc, mit, _ = detect_nottingham_components(t)
    check("nott_comp_zero_padded_mixed", (tub, nuc, mit), ('3', '2', '1'))

    # Combined: descriptor + bare + synonym
    t = normalize("DIFERENCIAÇÃO TUBULAR: 3. GRAU NUCLEAR: ALTO GRAU (3). ÍNDICE MITÓTICO: 2")
    tub, nuc, mit, _ = detect_nottingham_components(t)
    check("nott_comp_mixed_formats", (tub, nuc, mit), ('3', '3', '2'))

    # Strategy 4: number before colon separator (non-standard)
    t = normalize("[DIFERENCIAÇÃO TUBULAR 3: GRAU NUCLEAR 2: INDICO MITÓTICO: 1]")
    tub, nuc, mit, _ = detect_nottingham_components(t)
    check("nott_comp_number_before_colon", (tub, nuc, mit), ('3', '2', '1'))

    # Strategy 5: descriptor mapping without number
    t = normalize("GRAU NUCLEAR INTERMEDIÁRIO COM NECROSE")
    tub, nuc, mit, _ = detect_nottingham_components(t)
    check("nott_comp_descriptor_intermediario", (tub, nuc, mit), (None, '2', None))

    t = normalize("GRAU NUCLEAR ALTO")
    tub, nuc, mit, _ = detect_nottingham_components(t)
    check("nott_comp_descriptor_alto", (tub, nuc, mit), (None, '3', None))

    t = normalize("GRAU NUCLEAR BAIXO")
    tub, nuc, mit, _ = detect_nottingham_components(t)
    check("nott_comp_descriptor_baixo", (tub, nuc, mit), (None, '1', None))

    # Multi-lesion: DCIS narrative + structured Nottingham — structured wins
    t = normalize("CDIS GRAU NUCLEAR INTERMEDIÁRIO COM NECROSE. CARCINOMA INVASIVO [DIFERENCIAÇÃO TUBULAR: 2; GRAU NUCLEAR: 3; ÍNDICE MITÓTICO: 2]")
    tub, nuc, mit, _ = detect_nottingham_components(t)
    check("nott_comp_structured_over_narrative", (tub, nuc, mit), ('2', '3', '2'))

    # --- Derived variables ---
    check("derive_behavior_invasivo", derive_behavior_class('CDI_SOE'), 'invasivo')
    check("derive_behavior_insitu", derive_behavior_class('CDIS'), 'in_situ')
    check("derive_behavior_benigno", derive_behavior_class('fibroadenoma'), 'benigno')
    check("derive_behavior_none", derive_behavior_class(None), None)

    check("derive_invasive_sim", derive_invasive_malignancy('CDI_SOE'), 'sim')
    check("derive_invasive_nao", derive_invasive_malignancy('CDIS'), 'nao')

    check("derive_malig_sim_inv", derive_malignancy_any('CDI_SOE'), 'sim')
    check("derive_malig_sim_insitu", derive_malignancy_any('CDIS'), 'sim')
    check("derive_malig_nao", derive_malignancy_any('fibroadenoma'), 'nao')

    # --- Iteration 5 patches ---

    # tam_cm: "medindo o maior foco" patterns
    t = normalize("PRESENÇA DE FOCOS DE NEOPLASIA INVASIVA, MEDINDO O MAIOR FOCO 11 MILÍMETROS")
    v, _ = detect_tam_cm(t)
    check("tam_cm_medindo_maior_foco", v, "11 milimetros")

    t = normalize("MEDINDO O MAIOR FOCO NEOPLÁSICO 1,2 CM DE EXTENSÃO")
    v, _ = detect_tam_cm(t)
    check("tam_cm_medindo_foco_neoplasico", v, "1,2 cm")

    # tam_cm: multifocal — should return largest
    t = normalize("MAIOR DIMENSÃO LINEAR DA NEOPLASIA: 0,4 CM = 04 MM. XXX MAIOR DIMENSÃO LINEAR DE NEOPLASIA: 0,5 CM = 05 MM")
    v, _ = detect_tam_cm(t)
    check("tam_cm_multifocal_largest", v, "0,5 cm")

    # tam_cm: "ÁREA DE" filler between label and measurement
    t = normalize("TAMANHO DA LESÃO: ÁREA DE 2,5 X 1,5 CM PERMEADA POR FIBROSE")
    v, _ = detect_tam_cm(t)
    check("tam_cm_area_de_filler", v, "2,5 x 1,5 cm")

    # tam_cm: dual notation — L1 extracts "33 mm" which is semantically equivalent
    # to "3,3 x 2,5 x 2,3 cm" (33mm = 3.3cm). Comparison normalization handles this.
    t = normalize("TAMANHO DA NEOPLASIA INVASIVA: 33 MM (MAIOR DIÂMETRO) (3,3 X 2,5 X 2,3 CM).")
    v, _ = detect_tam_cm(t)
    check("tam_cm_dual_notation", v, "33 mm")

    # pN: missing 'p' prefix (bare N0 in staging context)
    t = normalize("ESTADIAMENTO ANATÔMICO (AJCC 8ª ED): pT2   N0  Mx")
    v, _ = detect_pN(t)
    check("pN_bare_N0", v, "pn0")

    # pN: yN0 without 'p' prefix
    t = normalize("ESTADIAMENTO ANATÔMICO (AJCC 8ª ED): ypT1c;    yN0")
    v, _ = detect_pN(t)
    check("pN_yN0_no_p", v, "ypn0")

    # pN: letter O instead of digit 0
    t = normalize("ESTADIAMENTO (AJCC 7ª ED): pTis (CDIS) pNO(LS) pMx.")
    v, _ = detect_pN(t)
    check("pN_letter_O", v, "pn0 (ls)")

    # pN: bare N0 another format
    t = normalize("ESTADIAMENTO (AJCC 7ª ED):  pT3   N0")
    v, _ = detect_pN(t)
    check("pN_bare_N0_v2", v, "pn0")

    # ln_exam: "CINCO LINFONODOS SENTINELA" — read actual count from prose
    t = normalize("HIPERPLASIA LINFÓIDE REACIONAL EM CINCO LINFONODOS SENTINELA DE MAMA DIREITA.")
    ex, pos, _ = detect_ln(t)
    check("ln_sentinel_cinco", (ex,), (5,))

    # ln_exam: "TRÊS LINFONODOS SENTINELA" — word number parsing
    t = normalize("TRÊS LINFONODOS SENTINELA DE AXILA ESQUERDA, LIVRES DE METÁSTASE")
    ex, pos, _ = detect_ln(t)
    check("ln_sentinel_tres", (ex, pos), (3, 0))

    # --- necrose_tumoral ---
    t = normalize("NECROSE TUMORAL: PRESENTE.")
    v, _ = detect_necrose_tumoral(t)
    check("necrose_presente", v, "sim")

    t = normalize("NECROSE TUMORAL: PRESENTE DO TIPO COMEDO.")
    v, _ = detect_necrose_tumoral(t)
    check("necrose_presente_comedo", v, "sim")

    t = normalize("NECROSE TUMORAL: NÃO OBSERVADA.")
    v, _ = detect_necrose_tumoral(t)
    check("necrose_nao_observada", v, "nao")

    t = normalize("NECROSE TUMORAL: AUSENTE.")
    v, _ = detect_necrose_tumoral(t)
    check("necrose_ausente", v, "nao")

    t = normalize("NECROSE TUMORAL: NÃO EVIDENCIADA.")
    v, _ = detect_necrose_tumoral(t)
    check("necrose_nao_evidenciada", v, "nao")

    t = normalize("GRAU NUCLEAR INTERMEDIÁRIO COM NECROSE E MICROCALCIFICAÇÃO")
    v, _ = detect_necrose_tumoral(t)
    check("necrose_narrative_not_tumoral", v, None)

    t = normalize("NECROSE TUMORAL: FOCAL")
    v, _ = detect_necrose_tumoral(t)
    check("necrose_focal", v, "sim")

    t = normalize("FIBROADENOMA SEM ATIPIAS")
    v, _ = detect_necrose_tumoral(t)
    check("necrose_benign_none", v, None)

    # Structured 'necrose:' without 'tumoral'
    t = normalize("7) NECROSE: NÃO EVIDENCIADA; 8) PELE: LIVRE")
    v, _ = detect_necrose_tumoral(t)
    check("necrose_field_nao_evidenciada", v, "nao")

    t = normalize("- NECROSE: PRESENTE; - COMPONENTE INTRADUCTO: PRESENTE")
    v, _ = detect_necrose_tumoral(t)
    check("necrose_field_presente", v, "sim")

    t = normalize("- NECROSE: AUSENTE; - ELEMENTOS HETEROLOGICOS: AUSENTES")
    v, _ = detect_necrose_tumoral(t)
    check("necrose_field_ausente", v, "nao")

    # 'sem necrose' semi-structured
    t = normalize("GRAU NUCLEAR INTERMEDIÁRIO, SEM NECROSE")
    v, _ = detect_necrose_tumoral(t)
    check("necrose_sem_necrose", v, "nao")

    t = normalize("CRIBRIFORME, ALTO GRAU SEM NECROSE - MÚLTIPLOS FOCOS")
    v, _ = detect_necrose_tumoral(t)
    check("necrose_alto_grau_sem", v, "nao")

    # Exclusions (must remain None)
    t = normalize("NECROSE ADIPOSA COM CALCIFICAÇÕES DISTRÓFICAS")
    v, _ = detect_necrose_tumoral(t)
    check("necrose_adiposa_excluded", v, None)

    t = normalize("ALTO GRAU COM COMEDONECROSE")
    v, _ = detect_necrose_tumoral(t)
    check("necrose_comedo_compound", v, None)

    # --- multifocalidade ---
    t = normalize("MULTIFOCALIDADE / MULTICENTRICIDADE: NÃO OBSERVADA.")
    v, _ = detect_multifocalidade(t)
    check("multi_nao_observada", v, "nao")

    t = normalize("MULTIFOCALIDADE / MULTICENTRICIDADE: PRESENTE.")
    v, _ = detect_multifocalidade(t)
    check("multi_presente", v, "sim")

    t = normalize("FOCALIDADE: UNIFOCAL")
    v, _ = detect_multifocalidade(t)
    check("multi_unifocal", v, "nao")

    t = normalize("FOCALIDADE E TAMANHO DO TUMOR: PRESENÇA DE FOCOS DE NEOPLASIA INVASIVA, MULTIFOCAL")
    v, _ = detect_multifocalidade(t)
    check("multi_focos_multifocal", v, "sim")

    t = normalize("CDIS MULTIFOCAL, PADRÃO SÓLIDO")
    v, _ = detect_multifocalidade(t)
    check("multi_cdis_multifocal", v, "sim")

    t = normalize("FOCALIDADE DO TUMOR: DOIS FOCOS (AMPLIAÇÃO DE MARGEM E EM REGIÃO AXILAR)")
    v, _ = detect_multifocalidade(t)
    check("multi_dois_focos", v, "sim")

    t = normalize("FOCALIDADE E TAMANHO DO TUMOR: PRESENÇA DE FOCOS DE NEOPLASIA INVASIVA")
    v, _ = detect_multifocalidade(t)
    check("multi_presenca_focos", v, "sim")

    t = normalize("FIBROADENOMA SEM ATIPIAS")
    v, _ = detect_multifocalidade(t)
    check("multi_benign_none", v, None)

    # --- microcalcificacoes ---
    t = normalize("MICROCALCIFICAÇÕES: NÃO OBSERVADAS")
    v, _ = detect_microcalcificacoes(t)
    check("microcalc_nao_observadas", v, "nao")

    t = normalize("MICROCALCIFICAÇÕES: PRESENTE")
    v, _ = detect_microcalcificacoes(t)
    check("microcalc_presente", v, "sim")

    t = normalize("MICROCALCIFICAÇÕES PRESENTES")
    v, _ = detect_microcalcificacoes(t)
    check("microcalc_presentes", v, "sim")

    t = normalize("SEM MICROCALCIFICAÇÕES")
    v, _ = detect_microcalcificacoes(t)
    check("microcalc_sem", v, "nao")

    t = normalize("CDIS DE ALTO GRAU COM NECROSE E MICROCALCIFICAÇÕES")
    v, _ = detect_microcalcificacoes(t)
    check("microcalc_narrative_none", v, None)

    t = normalize("FIBROADENOMA SEM ATIPIAS")
    v, _ = detect_microcalcificacoes(t)
    check("microcalc_benign_none", v, None)

    # --- pele ---
    t = normalize("PELE: LIVRE DE NEOPLASIA")
    v, _ = detect_pele(t)
    check("pele_livre", v, "nao")

    t = normalize("PELE: LIVRE")
    v, _ = detect_pele(t)
    check("pele_livre_short", v, "nao")

    t = normalize("PELE: COMPROMETIDA POR CONTIGUIDADE")
    v, _ = detect_pele(t)
    check("pele_comprometida", v, "sim")

    t = normalize("PELE: NEOPLASIA INVADE DIRETAMENTE A DERME SEM ULCERAÇÃO")
    v, _ = detect_pele(t)
    check("pele_invade", v, "sim")

    t = normalize("PELE: NÃO AMOSTRADA")
    v, _ = detect_pele(t)
    check("pele_nao_amostrada", v, None)

    t = normalize("PELE: NÃO AVALIÁVEL NO MATERIAL DE REVISÃO")
    v, _ = detect_pele(t)
    check("pele_nao_avaliavel", v, None)

    t = normalize("PELE: NÃO EVIDENCIADA")
    v, _ = detect_pele(t)
    check("pele_nao_evidenciada", v, "nao")

    # Extended patterns
    t = normalize("2) PELE DE MAMA DIREITA LIVRE DE NEOPLASIA.")
    v, _ = detect_pele(t)
    check("pele_de_mama_livre", v, "nao")

    t = normalize("PELE DE MAMA DIREITA: . DERME PROFUNDA COMPROMETIDA PELA NEOPLASIA")
    v, _ = detect_pele(t)
    check("pele_de_mama_comprometida", v, "sim")

    t = normalize("PELE DE MAMA ESQUERDA SEM PARTICULARIDADES HISTOPATOLÓGICAS")
    v, _ = detect_pele(t)
    check("pele_sem_particularidades", v, None)

    t = normalize("PELE E MAMILO: LIVRES DE NEOPLASIA")
    v, _ = detect_pele(t)
    check("pele_e_mamilo_livres", v, "nao")

    t = normalize("PELE DE MAMA DIREITA: . LIVRE DE NEOPLASIA")
    v, _ = detect_pele(t)
    check("pele_de_mama_colon_livre", v, "nao")

    t = normalize("PELE DE MAMA ESQUERDA LIVRE DE COMPROMETIMENTO NEOPLÁSICO")
    v, _ = detect_pele(t)
    check("pele_livre_comprometimento", v, "nao")

    t = normalize("FIBROADENOMA SEM ATIPIAS")
    v, _ = detect_pele(t)
    check("pele_benign_none", v, None)

    # --- mamilo ---
    t = normalize("MAMILO: LIVRE DE NEOPLASIA")
    v, _ = detect_mamilo(t)
    check("mamilo_livre", v, "nao")

    t = normalize("MAMILO: LIVRE")
    v, _ = detect_mamilo(t)
    check("mamilo_livre_short", v, "nao")

    t = normalize("MAMILO: COMPROMETIDO POR CONTIGUIDADE")
    v, _ = detect_mamilo(t)
    check("mamilo_comprometido", v, "sim")

    t = normalize("MAMILO: NÃO AMOSTRADO")
    v, _ = detect_mamilo(t)
    check("mamilo_nao_amostrado", v, None)

    t = normalize("MAMILO: NÃO EVIDENCIADO")
    v, _ = detect_mamilo(t)
    check("mamilo_nao_evidenciado", v, "nao")

    t = normalize("MAMILO: LIVRES DE NEOPLASIA")
    v, _ = detect_mamilo(t)
    check("mamilo_livres", v, "nao")

    # Extended patterns
    t = normalize("MAMILO E AREOLA DE MAMA ESQUERDA LIVRES DE NEOPLASIA")
    v, _ = detect_mamilo(t)
    check("mamilo_areola_livres", v, "nao")

    t = normalize("AREOLA E MAMILO DA MAMA DIREITA COMPROMETIDA PELA NEOPLASIA")
    v, _ = detect_mamilo(t)
    check("areola_mamilo_comprometida", v, "sim")

    t = normalize("MAMILO E AREOLA DE MAMA ESQUERDA SEM PARTICULARIDADES HISTOPATOLÓGICAS")
    v, _ = detect_mamilo(t)
    check("mamilo_sem_particularidades", v, None)

    t = normalize("PELE E MAMILO: LIVRES DE NEOPLASIA")
    v, _ = detect_mamilo(t)
    check("mamilo_pele_e_mamilo_livres", v, "nao")

    t = normalize("MAMILO E AREOLA DE MAMA DIREITA LIVRE DE CARCINOMA")
    v, _ = detect_mamilo(t)
    check("mamilo_livre_carcinoma", v, "nao")

    t = normalize("FIBROADENOMA SEM ATIPIAS")
    v, _ = detect_mamilo(t)
    check("mamilo_benign_none", v, None)

    # --- efeito_terapeutico ---
    t = normalize("EFEITO TERAPÊUTICO: RESPOSTA COMPLETA (DESAPARECIMENTO DE TODO O TUMOR)")
    v, _ = detect_efeito_terapeutico(t)
    check("efeito_completa", v, "completa")

    t = normalize("EFEITO TERAPÊUTICO: RESPOSTA PARCIAL")
    v, _ = detect_efeito_terapeutico(t)
    check("efeito_parcial", v, "parcial")

    t = normalize("EFEITO TERAPÊUTICO: AUSÊNCIA DE INFORMAÇÕES CLÍNICAS SOBRE TERAPIA PRÉ-OPERATÓRIA")
    v, _ = detect_efeito_terapeutico(t)
    check("efeito_sem_info", v, "sem_info")

    t = normalize("RESPOSTA AO TRATAMENTO NEOADJUVANTE: PARCIAL")
    v, _ = detect_efeito_terapeutico(t)
    check("efeito_neoadj_parcial", v, "parcial")

    t = normalize("EFEITO TERAPÊUTICO: COMPLETA (AUSÊNCIA DE NEOPLASIA INVASIVA RESIDUAL)")
    v, _ = detect_efeito_terapeutico(t)
    check("efeito_completa_pcr", v, "completa")

    t = normalize("RESPOSTA A QUIMIOTERAPIA NEOADJUVANTE: RESPOSTA PARCIAL (GRAU 3 DO SISTEMA DE GRADAÇÃO MILLER-PAYNE)")
    v, _ = detect_efeito_terapeutico(t)
    check("efeito_parcial_miller", v, "parcial")

    t = normalize("FIBROADENOMA SEM ATIPIAS")
    v, _ = detect_efeito_terapeutico(t)
    check("efeito_benign_none", v, None)

    # --- tam_cm labeled priority (Bug #26) ---
    t = normalize("TAMANHO DA NEOPLASIA INVASIVA: 12 MM (MAIOR DIÂMETRO) EM AMPLIAÇÃO DE MARGEM; "
                  "MEDINDO 3,0 X 2,5 X 2,5 CM EM REGIÃO AXILAR")
    v, _ = detect_tam_cm(t)
    check("tam_cm_labeled_priority", v, "12 mm")

    t = normalize("MEDINDO 2,5 X 2,0 CM, CORRESPONDENDO AO NÓDULO PALPÁVEL")
    v, _ = detect_tam_cm(t)
    check("tam_cm_medindo_fallback", v, "2,5 x 2,0 cm")

    # --- infiltrado_linfocitico ---
    t = normalize("INFILTRADO LINFOCITÁRIO INTRATUMORAL: ESCASSO")
    v, _ = detect_infiltrado_linfocitico(t)
    check("tils_escasso", v, "escasso")

    t = normalize("INFILTRADO LINFOCITÁRIO INTRATUMORAL: MODERADO")
    v, _ = detect_infiltrado_linfocitico(t)
    check("tils_moderado", v, "moderado")

    t = normalize("INFILTRADO LINFOCITÁRIO INTRATUMORAL: INTENSO")
    v, _ = detect_infiltrado_linfocitico(t)
    check("tils_intenso", v, "intenso")

    t = normalize("INFILTRADO LINFOCITÁRIO INTRATUMORAL: NÃO OBSERVADO")
    v, _ = detect_infiltrado_linfocitico(t)
    check("tils_nao_observado", v, "nao_evidenciado")

    t = normalize("COMPONENTE LINFOCITÁRIO: PRESENTE")
    v, _ = detect_infiltrado_linfocitico(t)
    check("tils_presente", v, "presente")

    t = normalize("REAÇÃO LINFOCITÁRIA: EXUBERANTE")
    v, _ = detect_infiltrado_linfocitico(t)
    check("tils_exuberante", v, "intenso")

    t = normalize("INFILTRADO LINFOCITÁRIO INTRAMURAL: ESCASSO")
    v, _ = detect_infiltrado_linfocitico(t)
    check("tils_intramural_typo", v, "escasso")

    t = normalize("INFILTRADO LINFOCITÁRIO INTRATUMORAL: MODERADA")
    v, _ = detect_infiltrado_linfocitico(t)
    check("tils_moderada_fem", v, "moderado")

    t = normalize("FIBROADENOMA SEM ATIPIAS")
    v, _ = detect_infiltrado_linfocitico(t)
    check("tils_benign_none", v, None)

    # --- extensao_extranodal ---
    t = normalize("EXTENSÃO EXTRANODAL: PRESENTE")
    v, _ = detect_extensao_extranodal(t)
    check("extranodal_presente", v, "sim")

    t = normalize("EXTENSÃO EXTRANODAL: NÃO OBSERVADA")
    v, _ = detect_extensao_extranodal(t)
    check("extranodal_nao_observada", v, "nao")

    t = normalize("SEM EXTENSÃO EXTRANODAL")
    v, _ = detect_extensao_extranodal(t)
    check("extranodal_sem", v, "nao")

    t = normalize("EXTENSÃO EXTRACAPSULAR: PRESENTE")
    v, _ = detect_extensao_extranodal(t)
    check("extranodal_extracapsular", v, "sim")

    t = normalize("ROMPIMENTO CAPSULAR: NÃO OBSERVADO")
    v, _ = detect_extensao_extranodal(t)
    check("extranodal_rompimento_nao", v, "nao")

    t = normalize("COM EXTENSÃO EXTRANODAL PARA TECIDO ADIPOSO PERILINFONODAL")
    v, _ = detect_extensao_extranodal(t)
    check("extranodal_com_extensao", v, "sim")

    t = normalize("PRESENÇA DE EXTENSÃO EXTRANODAL EM 01 LINFONODO")
    v, _ = detect_extensao_extranodal(t)
    check("extranodal_presenca_de", v, "sim")

    t = normalize("COM EXTENSÃO EXTRACAPSULAR")
    v, _ = detect_extensao_extranodal(t)
    check("extranodal_com_extracapsular", v, "sim")

    t = normalize("FIBROADENOMA SEM ATIPIAS")
    v, _ = detect_extensao_extranodal(t)
    check("extranodal_benign_none", v, None)

    print(f'\n  Smoke tests: {passed} passed, {failed} failed')
    return failed == 0


# ===========================================================================
# CORPUS RUNNER
# ===========================================================================

def run_corpus(holdout_csv: str, llm_json: str, output_csv: str):
    """Run L1 on holdout corpus and compare with LLM."""

    # Load data
    texts = {}
    with open(holdout_csv, 'r', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            texts[row['numExame']] = row

    with open(llm_json, 'r', encoding='utf-8') as f:
        llm_list = json.load(f)
    llm = {r['numExame']: r for r in llm_list}

    results = []

    # Variables to track
    vars_to_compare = ['tam_cm', 'comp_insitu', 'procedimento', 'tipo_hist',
                       'margens', 'pT', 'pN', 'ln_exam', 'ln_pos',
                       'grau_nott', 'inv_linfov', 'inv_perin', 'ref_ihq',
                       'esc_tubular', 'esc_nuclear', 'esc_mitotico',
                       'behavior_class', 'invasive_malignancy', 'malignancy_any']

    stats = {v: {'agree': 0, 'disagree': 0, 'l1_only': 0, 'llm_only': 0,
                 'both_na': 0, 'total': 0, 'disagree_examples': []}
             for v in vars_to_compare}

    for numExame, rec in texts.items():
        text_norm = normalize(rec['conclusao'])
        mat_norm = normalize(rec.get('materialEspecificado', ''))
        estrato = rec['estrato']
        llm_rec = llm.get(numExame, {})

        # Extract all variables
        tam_val, tam_ev = detect_tam_cm(text_norm)
        insitu_val, insitu_ev = detect_comp_insitu(text_norm)
        proc_val, proc_ev = detect_procedimento(text_norm, mat_norm)
        tipo_val, tipo_ev = detect_tipo_hist(text_norm)
        marg_val, marg_ev = detect_margens(text_norm)
        pt_val, pt_ev = detect_pT(text_norm)
        pn_val, pn_ev = detect_pN(text_norm)
        ln_ex, ln_po, ln_ev = detect_ln(text_norm)
        grau_val, grau_ev = detect_grau_nott(text_norm)
        linfov_val, linfov_ev = detect_inv_linfov(text_norm)
        perin_val, perin_ev = detect_inv_perin(text_norm)
        ihq_val, ihq_ev = detect_ref_ihq(text_norm)
        esc_tub, esc_nuc, esc_mit, esc_ev = detect_nottingham_components(text_norm)
        beh_class = derive_behavior_class(tipo_val)
        inv_malig = derive_invasive_malignancy(tipo_val)
        malig_any = derive_malignancy_any(tipo_val)

        # DCIS guard (Bug #28): Nottingham components are defined for invasive
        # carcinoma only. In pure DCIS/CLIS reports, nuclear grade uses the same
        # 1-3 scale but is NOT a Nottingham component — extracting it as
        # esc_nuclear is an ontology collision. Null out all three components
        # when behavior_class is 'in_situ' (no invasive component present).
        if beh_class == 'in_situ':
            esc_tub, esc_nuc, esc_mit, esc_ev = None, None, None, None
        necrose_val, necrose_ev = detect_necrose_tumoral(text_norm)
        multi_val, multi_ev = detect_multifocalidade(text_norm)
        microcalc_val, microcalc_ev = detect_microcalcificacoes(text_norm)
        pele_val, pele_ev = detect_pele(text_norm)
        mamilo_val, mamilo_ev = detect_mamilo(text_norm)
        efeito_val, efeito_ev = detect_efeito_terapeutico(text_norm)
        tils_val, tils_ev = detect_infiltrado_linfocitico(text_norm)
        extranodal_val, extranodal_ev = detect_extensao_extranodal(text_norm)

        row = {
            'numExame': numExame,
            'estrato': estrato,
            'l1_tam_cm': tam_val or '',
            'l1_tam_cm_evidence': tam_ev or '',
            'l1_comp_insitu': insitu_val or '',
            'l1_comp_insitu_evidence': insitu_ev or '',
            'l1_procedimento': proc_val or '',
            'l1_procedimento_evidence': proc_ev or '',
            'l1_tipo_hist': tipo_val or '',
            'l1_tipo_hist_evidence': tipo_ev or '',
            'l1_margens': marg_val or '',
            'l1_margens_evidence': marg_ev or '',
            'l1_pT': pt_val or '',
            'l1_pT_evidence': pt_ev or '',
            'l1_pN': pn_val or '',
            'l1_pN_evidence': pn_ev or '',
            'l1_ln_exam': str(ln_ex) if ln_ex is not None else '',
            'l1_ln_pos': str(ln_po) if ln_po is not None else '',
            'l1_ln_evidence': ln_ev or '',
            'l1_grau_nott': grau_val or '',
            'l1_grau_nott_evidence': grau_ev or '',
            'l1_inv_linfov': linfov_val or '',
            'l1_inv_linfov_evidence': linfov_ev or '',
            'l1_inv_perin': perin_val or '',
            'l1_inv_perin_evidence': perin_ev or '',
            'l1_ref_ihq': ihq_val or '',
            'l1_ref_ihq_evidence': ihq_ev or '',
            'l1_esc_tubular': esc_tub or '',
            'l1_esc_nuclear': esc_nuc or '',
            'l1_esc_mitotico': esc_mit or '',
            'l1_esc_evidence': esc_ev or '',
            'l1_behavior_class': beh_class or '',
            'l1_invasive_malignancy': inv_malig or '',
            'l1_malignancy_any': malig_any or '',
            'l1_necrose_tumoral': necrose_val or '',
            'l1_necrose_tumoral_evidence': necrose_ev or '',
            'l1_multifocalidade': multi_val or '',
            'l1_multifocalidade_evidence': multi_ev or '',
            'l1_microcalcificacoes': microcalc_val or '',
            'l1_microcalcificacoes_evidence': microcalc_ev or '',
            'l1_pele': pele_val or '',
            'l1_pele_evidence': pele_ev or '',
            'l1_mamilo': mamilo_val or '',
            'l1_mamilo_evidence': mamilo_ev or '',
            'l1_efeito_terapeutico': efeito_val or '',
            'l1_efeito_terapeutico_evidence': efeito_ev or '',
            'l1_infiltrado_linfocitico': tils_val or '',
            'l1_infiltrado_linfocitico_evidence': tils_ev or '',
            'l1_extensao_extranodal': extranodal_val or '',
            'l1_extensao_extranodal_evidence': extranodal_ev or '',
        }
        results.append(row)

        # Compare with LLM
        l1_vals = {
            'tam_cm': tam_val or '',
            'comp_insitu': insitu_val or '',
            'procedimento': proc_val or '',
            'tipo_hist': tipo_val or '',
            'margens': marg_val or '',
            'pT': pt_val or '',
            'pN': pn_val or '',
            'ln_exam': str(ln_ex) if ln_ex is not None else '',
            'ln_pos': str(ln_po) if ln_po is not None else '',
            'grau_nott': grau_val or '',
            'inv_linfov': linfov_val or '',
            'inv_perin': perin_val or '',
            'ref_ihq': ihq_val or '',
            'esc_tubular': esc_tub or '',
            'esc_nuclear': esc_nuc or '',
            'esc_mitotico': esc_mit or '',
            'behavior_class': beh_class or '',
            'invasive_malignancy': inv_malig or '',
            'malignancy_any': malig_any or '',
        }

        for var in vars_to_compare:
            l1v = l1_vals[var].lower().strip()
            llm_v = str(llm_rec.get(var, '') or '').lower().strip()
            # Normalize accents on LLM values for fair comparison
            llm_v = llm_v.translate(ACCENT_MAP)
            if llm_v in ('none', 'null', ''): llm_v = ''

            # Variable-specific normalization for fair comparison
            if var == 'pN' and l1v and llm_v:
                # Strip (sn)/(ls) suffix from both for base comparison
                l1_base = re.sub(r'\s*\((?:sn|ls)\)', '', l1v)
                llm_base = re.sub(r'\s*\((?:sn|ls)\)', '', llm_v)
                # Canonicalize: add 'p' prefix if missing, fix letter O→0
                llm_base = re.sub(r'^(y?)n([0-3x])', r'\1pn\2', llm_base)
                llm_base = llm_base.replace('pno', 'pn0')
                if l1_base == llm_base:
                    l1v = llm_v = l1_base  # treat as agree
            elif var == 'tam_cm' and l1v and llm_v:
                # Normalize unit synonyms: milimetros/milímetros → mm
                l1_n = re.sub(r'\s*milimetros?\b', ' mm', l1v).strip()
                llm_n = re.sub(r'\s*milimetros?\b', ' mm', llm_v).strip()
                # Normalize milímetros with bad encoding too
                llm_n = re.sub(r'\s*mil[^m\s]*metros?\b', ' mm', llm_n).strip()
                if l1_n == llm_n:
                    l1v = llm_v = l1_n  # treat as agree
                else:
                    # Compare by largest dimension in mm (handles "33 mm" vs "3,3 x 2,5 x 2,3 cm")
                    l1_mm = _meas_to_mm(l1v)
                    llm_mm = _meas_to_mm(llm_v)
                    if l1_mm is not None and llm_mm is not None and abs(l1_mm - llm_mm) < 0.5:
                        l1v = llm_v = l1v  # same value, different format — agree
            elif var == 'ref_ihq' and l1v and llm_v:
                # L1 returns "sim", LLM returns IH number — both agree there IS a referral
                if l1v == 'sim' and re.search(r'\d+.*ih', llm_v):
                    l1v = llm_v = 'sim'  # treat as agree
            elif var == 'grau_nott' and l1v and llm_v:
                # Normalize roman→arabic for comparison
                roman = {'i': '1', 'ii': '2', 'iii': '3'}
                llm_v = roman.get(llm_v, llm_v)
            elif var in ('esc_tubular', 'esc_nuclear', 'esc_mitotico') and l1v and llm_v:
                # Both should be single digit 1-3; strip any extra text
                l1_d = re.search(r'[1-3]', l1v)
                llm_d = re.search(r'[1-3]', llm_v)
                if l1_d:
                    l1v = l1_d.group(0)
                if llm_d:
                    llm_v = llm_d.group(0)

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
                        (numExame, estrato, l1v, llm_v))

    # Save results
    if output_csv:
        fieldnames = list(results[0].keys())
        with open(output_csv, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)

    # Print report
    print('\n' + '='*80)
    print('L1 vs LLM CONCORDANCE REPORT — Mama AP Multi-variable')
    print('='*80)

    for var in vars_to_compare:
        s = stats[var]
        total = s['total']
        both_have = s['agree'] + s['disagree'] - s['both_na']
        agree_when_both = s['agree'] - s['both_na']

        print(f'\n--- {var} ---')
        print(f'  Agree (incl both NA): {s["agree"]}/{total} = {s["agree"]/total*100:.1f}%')
        print(f'    Both NA: {s["both_na"]}')
        print(f'    Agree on value: {agree_when_both}')
        print(f'  L1-only (L1 has, LLM missing): {s["l1_only"]}')
        print(f'  LLM-only (LLM has, L1 missing): {s["llm_only"]}')
        print(f'  Disagree (both have, different): {s["disagree"]}')

        if s['disagree_examples']:
            print(f'  Disagreement examples:')
            for ne, est, l1v, llmv in s['disagree_examples'][:5]:
                print(f'    [{ne}] {est}: L1={l1v} vs LLM={llmv}')

    return stats


# ===========================================================================
# MAIN
# ===========================================================================

if __name__ == '__main__':
    print("=== SMOKE TESTS ===")
    ok = run_smoke_tests()

    if not ok:
        print("\nSmoke tests failed. Fix before running corpus.")
        sys.exit(1)

    holdout_csv = r'Y:\IDOC-Patologia\04_artigos\artigo1_validacao_ia\holdout\holdout_mama_ap.csv'
    llm_json = r'Y:\IDOC-Patologia\04_artigos\artigo1_validacao_ia\holdout\llm_mama_ap.json'
    output_csv = r'Y:\DDPA\framework_extracao_v4\04_piloto\mama_ap_multi_l1_holdout.csv'

    if os.path.exists(holdout_csv) and os.path.exists(llm_json):
        print("\n=== RUNNING ON HOLDOUT CORPUS ===")
        run_corpus(holdout_csv, llm_json, output_csv)
    else:
        print("\nHoldout files not found. Skipping corpus run.")
