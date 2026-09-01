#!/usr/bin/env python3
"""
L1 Deterministic Detector -- Tireoide Bethesda

Extracts 3 variables from thyroid FNA cytopathology reports:
  1. bethesda      -- Bethesda category (I, II, III, IV, V, VI)
  2. bethesda_desc -- Descriptive text (BENIGNO, MALIGNO, etc.)
  3. subtipo       -- Histological subtype (papilar, folicular, etc.)

Data:
  Holdout: holdout_tireoide.csv (200)
  R regex: regex_tireoide.csv
"""

import re
import sys
import csv
import unicodedata
from typing import Optional, Tuple


# ===========================================================================
# NORMALIZE
# ===========================================================================

def normalize(text: str) -> str:
    """Normalize text: lowercase, strip accents, collapse whitespace."""
    if not text:
        return ''
    text = text.replace('\ufffd', '')
    text = unicodedata.normalize('NFD', text)
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    text = text.lower()
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def strip_comentarios(text_norm: str) -> str:
    """Strip COMENTÁRIOS/reference section from normalized text.

    Thyroid FNA reports often include a COMENTÁRIOS section with educational
    text listing all Bethesda categories. This causes false matches (e.g.,
    'Categoria IV' in comments matched when actual result is Cat II).
    Truncates at the first occurrence of a section marker.
    """
    # Common section markers (already normalized/lowercased)
    # Some reports use "COMENTÁRIOS:" (with colon), others just "COMENTÁRIOS"
    # followed by content. Also strip REFERÊNCIAS section.
    for marker in [
        r'\bcomentarios\b',
        r'\breferencias\b',
        r'\bnota\s*:',
        r'\bobservacao\s*:',
        r'\bobservacoes\s*:',
    ]:
        m = re.search(marker, text_norm)
        if m:
            return text_norm[:m.start()].strip()
    return text_norm


# ===========================================================================
# BETHESDA -- Category (I through VI)
# ===========================================================================

def detect_bethesda(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect Bethesda category from conclusion text.

    Uses word boundaries to prevent CATEGORIA I matching inside CATEGORIA IV.
    Checks longest match first (VI before V, IV before I, III before II).
    """

    # Primary pattern: "CATEGORIA X DE BETHESDA"
    # Check in order from longest to shortest to avoid partial matches
    for cat, pattern in [
        ('VI',  r'categoria\s+vi\b'),
        ('IV',  r'categoria\s+iv\b'),
        ('III', r'categoria\s+iii\b'),
        ('II',  r'categoria\s+ii\b'),
        ('V',   r'categoria\s+v\b'),
        ('I',   r'categoria\s+i\b'),
    ]:
        m = re.search(pattern + r'\s+(?:de\s+)?bethesda', text_norm)
        if m:
            return cat, m.group(0).strip()[:60]

    # Fallback: "BETHESDA I/II/III/IV/V/VI" (without CATEGORIA)
    for cat, pattern in [
        ('VI',  r'bethesda\s+vi\b'),
        ('IV',  r'bethesda\s+iv\b'),
        ('III', r'bethesda\s+iii\b'),
        ('II',  r'bethesda\s+ii\b'),
        ('V',   r'bethesda\s+v\b'),
        ('I',   r'bethesda\s+i\b'),
    ]:
        m = re.search(pattern, text_norm)
        if m:
            return cat, m.group(0).strip()[:60]

    # Arabic numerals: "BETHESDA 1-6"
    m = re.search(r'bethesda\s+(\d)\b', text_norm)
    if m:
        d = m.group(1)
        mapping = {'1': 'I', '2': 'II', '3': 'III', '4': 'IV', '5': 'V', '6': 'VI'}
        if d in mapping:
            return mapping[d], m.group(0).strip()[:40]

    return None, None


# ===========================================================================
# BETHESDA_DESC -- Descriptive text
# ===========================================================================

def detect_bethesda_desc(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect Bethesda descriptive text after the dash."""

    # Pattern: "BETHESDA - DESCRIPTION" or "BETHESDA -- DESCRIPTION"
    m = re.search(r'bethesda\s*[-\u2013]+\s*([a-z][a-z\s]+)', text_norm)
    if m:
        desc = m.group(1).strip()
        # Clean up: take until end of uppercase-ish word block
        # Common values: benigno, maligno, amostra nao diagnostica,
        # suspeito de malignidade, atipias de significado indeterminado,
        # lesao folicular de significado indeterminado, etc.
        desc = re.sub(r'\s*[\(\.\,].*', '', desc)  # trim after paren/period/comma
        desc = desc.strip()
        if desc:
            return desc, m.group(0).strip()[:80]

    return None, None


# ===========================================================================
# SUBTIPO -- Histological subtype
# ===========================================================================

def detect_subtipo(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect histological subtype.

    Priority order (most specific first, generic last):
      1. nodulo_hiperplasico / adenomatoide (benign, very specific)
      2. hashimoto / tireoidite linfocitica (benign, specific)
      3. papilar / papilifero (most common malignant)
      4. medular (rare, very specific keyword)
      5. hurthle / oncocitario (specific: "folicular oxifilica (Hurthle)")
      6. folicular (generic catch-all — appears in many contexts)
    """

    # Nodulo hiperplasico / adenomatoide (benign, very specific)
    m = re.search(r'n.dulo\s+hiperpl.sico|adenomat.ide', text_norm)
    if m:
        return 'nodulo_hiperplasico', m.group(0).strip()[:40]

    # Hashimoto / tireoidite linfocitica (benign, specific)
    m = re.search(r'hashimoto|tireoidite\s+linfoc', text_norm)
    if m:
        return 'hashimoto', m.group(0).strip()[:40]

    # Papilar / papilifero (most common malignant)
    m = re.search(r'(?:carcinoma\s+)?papil[ai]r|papilifero', text_norm)
    if m:
        return 'papilar', m.group(0).strip()[:40]

    # Medular (very specific keyword)
    m = re.search(r'medular', text_norm)
    if m:
        return 'medular', m.group(0).strip()[:40]

    # Hurthle / oncocitario (before folicular: "neoplasia folicular oxifilica (Hurthle)")
    m = re.search(r'hurthle|oncocit', text_norm)
    if m:
        return 'hurthle', m.group(0).strip()[:40]

    # Folicular (generic catch-all — appears in many contexts)
    m = re.search(r'folicular', text_norm)
    if m:
        return 'folicular', m.group(0).strip()[:40]

    return None, None


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

    # --- BETHESDA CATEGORY ---
    t = normalize("CATEGORIA I DE BETHESDA - AMOSTRA NÃO DIAGNÓSTICA")
    v, _ = detect_bethesda(t)
    check("beth_I", v, "I")

    t = normalize("CATEGORIA II DE BETHESDA - BENIGNO")
    v, _ = detect_bethesda(t)
    check("beth_II", v, "II")

    t = normalize("CATEGORIA III DE BETHESDA - ATIPIAS DE SIGNIFICADO INDETERMINADO")
    v, _ = detect_bethesda(t)
    check("beth_III", v, "III")

    t = normalize("CATEGORIA IV DE BETHESDA - SUSPEITO DE NEOPLASIA FOLICULAR")
    v, _ = detect_bethesda(t)
    check("beth_IV", v, "IV")

    t = normalize("CATEGORIA V DE BETHESDA - SUSPEITO DE MALIGNIDADE")
    v, _ = detect_bethesda(t)
    check("beth_V", v, "V")

    t = normalize("CATEGORIA VI DE BETHESDA - MALIGNO")
    v, _ = detect_bethesda(t)
    check("beth_VI", v, "VI")

    # Word boundary tests (known bug in R regex)
    t = normalize("CATEGORIA IV DE BETHESDA")
    v, _ = detect_bethesda(t)
    check("beth_IV_not_I", v, "IV")  # should NOT match I

    t = normalize("CATEGORIA VI DE BETHESDA")
    v, _ = detect_bethesda(t)
    check("beth_VI_not_V", v, "VI")  # should NOT match V

    t = normalize("CATEGORIA III DE BETHESDA")
    v, _ = detect_bethesda(t)
    check("beth_III_not_II", v, "III")  # should NOT match II

    # Without "DE"
    t = normalize("CATEGORIA II BETHESDA - BENIGNO")
    v, _ = detect_bethesda(t)
    check("beth_II_no_de", v, "II")

    # No match
    t = normalize("PUNÇÃO DE TIREOIDE SEM CLASSIFICAÇÃO")
    v, _ = detect_bethesda(t)
    check("beth_absent", v, None)

    # --- BETHESDA DESC ---
    t = normalize("CATEGORIA II DE BETHESDA - BENIGNO (COMPATÍVEL COM NÓDULO)")
    v, _ = detect_bethesda_desc(t)
    check("desc_benigno", v, "benigno")

    t = normalize("CATEGORIA VI DE BETHESDA - MALIGNO. (COMPATÍVEL COM CARCINOMA)")
    v, _ = detect_bethesda_desc(t)
    check("desc_maligno", v, "maligno")

    t = normalize("CATEGORIA I DE BETHESDA - AMOSTRA NÃO DIAGNÓSTICA")
    v, _ = detect_bethesda_desc(t)
    check("desc_nao_diagnostica", v, "amostra nao diagnostica")

    t = normalize("CATEGORIA V DE BETHESDA - SUSPEITO DE MALIGNIDADE")
    v, _ = detect_bethesda_desc(t)
    check("desc_suspeito", v, "suspeito de malignidade")

    t = normalize("CATEGORIA III DE BETHESDA - ATIPIAS DE SIGNIFICADO INDETERMINADO")
    v, _ = detect_bethesda_desc(t)
    check("desc_atipias", v, "atipias de significado indeterminado")

    t = normalize("CATEGORIA IV DE BETHESDA - LESÃO FOLICULAR DE SIGNIFICADO INDETERMINADO")
    v, _ = detect_bethesda_desc(t)
    check("desc_lesao_fol", v, "lesao folicular de significado indeterminado")

    # --- SUBTIPO ---
    t = normalize("COMPATÍVEL COM CARCINOMA PAPILAR DA TIREOIDE")
    v, _ = detect_subtipo(t)
    check("subtipo_papilar", v, "papilar")

    t = normalize("CARCINOMA PAPILÍFERO DA TIREOIDE")
    v, _ = detect_subtipo(t)
    check("subtipo_papilifero", v, "papilar")

    t = normalize("SUSPEITO DE NEOPLASIA FOLICULAR")
    v, _ = detect_subtipo(t)
    check("subtipo_folicular", v, "folicular")

    t = normalize("CARCINOMA MEDULAR")
    v, _ = detect_subtipo(t)
    check("subtipo_medular", v, "medular")

    t = normalize("SUSPEITO DE NEOPLASIA FOLICULAR OXIFÍLICA (HÜRTHLE)")
    v, _ = detect_subtipo(t)
    check("subtipo_hurthle", v, "hurthle")

    t = normalize("LESÃO ONCOCÍTICA")
    v, _ = detect_subtipo(t)
    check("subtipo_oncocit", v, "hurthle")

    t = normalize("COMPATÍVEL COM TIREOIDITE LINFOCÍTICA")
    v, _ = detect_subtipo(t)
    check("subtipo_hashimoto", v, "hashimoto")

    t = normalize("TIREOIDITE DE HASHIMOTO")
    v, _ = detect_subtipo(t)
    check("subtipo_hashimoto2", v, "hashimoto")

    t = normalize("COMPATÍVEL COM NÓDULO HIPERPLÁSICO / ADENOMATÓIDE")
    v, _ = detect_subtipo(t)
    check("subtipo_hiperplasico", v, "nodulo_hiperplasico")

    t = normalize("CITOLOGIA BENIGNA")
    v, _ = detect_subtipo(t)
    check("subtipo_absent", v, None)

    # --- COMENTÁRIOS STRIPPING ---
    # Reference contamination: COMENTÁRIOS contains all Bethesda categories
    raw = ("CATEGORIA II DE BETHESDA - BENIGNO. "
           "COMENTÁRIOS: I (ND/UNS): Categoria I de Bethesda. "
           "IV (FN/SFN): Categoria IV de Bethesda - Suspeito de neoplasia folicular.")
    t = normalize(raw)
    t_diag = strip_comentarios(t)
    v, _ = detect_bethesda(t_diag)
    check("coment_beth_II_not_IV", v, "II")

    raw2 = ("CATEGORIA V DE BETHESDA - SUSPEITO DE MALIGNIDADE. "
            "NOTA: As categorias de Bethesda incluem Categoria VI (maligno).")
    t = normalize(raw2)
    t_diag = strip_comentarios(t)
    v, _ = detect_bethesda(t_diag)
    check("nota_beth_V_not_VI", v, "V")

    # No COMENTÁRIOS — should pass through unchanged
    t = normalize("CATEGORIA III DE BETHESDA - ATIPIAS")
    t_diag = strip_comentarios(t)
    v, _ = detect_bethesda(t_diag)
    check("no_coment_passthrough", v, "III")

    # --- SUBTIPO PRIORITY ---
    # Hashimoto should win over folicular when both present
    t = normalize("TIREOIDITE LINFOCÍTICA COM NÓDULO FOLICULAR")
    t_diag = strip_comentarios(t)
    v, _ = detect_subtipo(t_diag)
    check("subtipo_hashimoto_over_folic", v, "hashimoto")

    # Nodulo hiperplasico should win over folicular
    t = normalize("NÓDULO HIPERPLÁSICO COM PADRÃO FOLICULAR")
    t_diag = strip_comentarios(t)
    v, _ = detect_subtipo(t_diag)
    check("subtipo_hiperpl_over_folic", v, "nodulo_hiperplasico")

    # Papilar should still win over folicular (variante folicular do ca papilar)
    t = normalize("CARCINOMA PAPILAR VARIANTE FOLICULAR")
    t_diag = strip_comentarios(t)
    v, _ = detect_subtipo(t_diag)
    check("subtipo_papilar_over_folic", v, "papilar")

    # Folicular alone — should detect correctly
    t = normalize("SUSPEITO DE NEOPLASIA FOLICULAR")
    t_diag = strip_comentarios(t)
    v, _ = detect_subtipo(t_diag)
    check("subtipo_folicular_alone", v, "folicular")

    # Oxifílica without "Hürthle" keyword = folicular (not hurthle)
    # "Células oxifílicas" is descriptive morphology, not Hürthle neoplasm
    t = normalize("LESÃO FOLICULAR OXIFÍLICA DE SIGNIFICADO INDETERMINADO")
    t_diag = strip_comentarios(t)
    v, _ = detect_subtipo(t_diag)
    check("subtipo_oxifilica_is_folicular", v, "folicular")

    # Subtipo stripped from COMENTÁRIOS — should be None
    raw3 = ("CATEGORIA II DE BETHESDA - BENIGNO. "
            "COMENTÁRIOS: Carcinoma papilar da tireoide é o mais comum.")
    t = normalize(raw3)
    t_diag = strip_comentarios(t)
    v, _ = detect_subtipo(t_diag)
    check("subtipo_coment_stripped", v, None)

    print(f"\n  Smoke tests: {total} passed, {failures} failed")
    return failures


# ===========================================================================
# HOLDOUT COMPARISON
# ===========================================================================

def run_comparison(holdout_csv: str, regex_csv: str, output_csv: str):
    """Run L1 on holdout and compare with R regex results."""
    import pandas as pd

    df = pd.read_csv(holdout_csv, dtype=str, encoding='utf-8')
    print(f"Holdout loaded: {len(df)} records")

    rg = pd.read_csv(regex_csv, dtype=str, encoding='utf-8')
    rg_dict = {r['numExame']: r for _, r in rg.iterrows()}
    print(f"R regex loaded: {len(rg)} records")

    vars_to_compare = ['bethesda', 'bethesda_desc', 'subtipo']

    stats = {v: {'agree': 0, 'both_na': 0, 'l1_only': 0,
                  'rregex_only': 0, 'disagree': 0,
                  'disagree_examples': [], 'total': 0}
             for v in vars_to_compare}

    results = []

    for _, rec in df.iterrows():
        numExame = rec['numExame']
        text = str(rec.get('conclusao', ''))
        if not text or text == 'nan':
            text = ''
        text_norm = normalize(text)
        text_diag = strip_comentarios(text_norm)  # diagnostic section only

        beth_val, beth_ev = detect_bethesda(text_diag)
        desc_val, desc_ev = detect_bethesda_desc(text_diag)
        subtipo_val, subtipo_ev = detect_subtipo(text_diag)

        row = {
            'numExame': numExame,
            'estrato': rec.get('estrato', ''),
            'l1_bethesda': beth_val or '',
            'l1_bethesda_evidence': beth_ev or '',
            'l1_bethesda_desc': desc_val or '',
            'l1_bethesda_desc_evidence': desc_ev or '',
            'l1_subtipo': subtipo_val or '',
            'l1_subtipo_evidence': subtipo_ev or '',
        }
        results.append(row)

        l1_vals = {
            'bethesda': beth_val or '',
            'bethesda_desc': desc_val or '',
            'subtipo': subtipo_val or '',
        }

        rg_rec = rg_dict.get(numExame)
        for var in vars_to_compare:
            s = stats[var]
            s['total'] += 1
            l1v = l1_vals.get(var, '')
            if rg_rec is not None:
                rg_v = str(rg_rec.get(var, ''))
                if rg_v == 'nan':
                    rg_v = ''
            else:
                rg_v = ''

            # Normalized comparison for bethesda_desc
            # (L1 strips accents + lowercases; R preserves original)
            if var == 'bethesda_desc':
                l1v_cmp = normalize(l1v)
                rg_v_cmp = normalize(rg_v)
            else:
                l1v_cmp = l1v
                rg_v_cmp = rg_v

            if l1v_cmp == rg_v_cmp:
                s['agree'] += 1
                if l1v == '':
                    s['both_na'] += 1
            elif l1v and not rg_v:
                s['l1_only'] += 1
            elif not l1v and rg_v:
                s['rregex_only'] += 1
            else:
                s['disagree'] += 1
                if len(s['disagree_examples']) < 8:
                    s['disagree_examples'].append(
                        (numExame, rec.get('estrato', ''), l1v, rg_v))

    # Save results
    if results:
        keys = results[0].keys()
        with open(output_csv, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=keys, quoting=csv.QUOTE_ALL)
            writer.writeheader()
            writer.writerows(results)
        print(f"Results saved to {output_csv}")

    # Print report
    print(f"\n{'=' * 80}")
    print("L1 vs R-REGEX CONCORDANCE REPORT -- Tireoide Bethesda")
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
        print(f"  L1-only (L1 has, R missing): {s['l1_only']}")
        print(f"  R-only (R has, L1 missing): {s['rregex_only']}")
        print(f"  Disagree (both have, different): {s['disagree']}")
        if s['disagree_examples']:
            print(f"  Disagreement examples:")
            for nid, est, l1v, rg_v in s['disagree_examples']:
                print(f"    [{nid}] {est}: L1={l1v} vs R={rg_v}")


# ===========================================================================
# MAIN
# ===========================================================================

if __name__ == '__main__':
    if '--smoke' in sys.argv or len(sys.argv) == 1:
        print("=== SMOKE TESTS ===\n")
        failures = run_smoke_tests()

        if '--smoke' not in sys.argv or '--run' in sys.argv:
            print("\n=== RUNNING ON HOLDOUT CORPUS ===\n")
            holdout_csv = r'Y:\IDOC-Patologia\04_artigos\artigo1_validacao_ia\holdout\holdout_tireoide.csv'
            regex_csv = r'Y:\IDOC-Patologia\04_artigos\artigo1_validacao_ia\holdout\regex_tireoide.csv'
            output_csv = r'Y:\DDPA\framework_extracao_v4\04_piloto\tireoide_bethesda_l1_holdout.csv'
            run_comparison(holdout_csv, regex_csv, output_csv)
