#!/usr/bin/env python3
"""
L1 Deterministic Detector -- Cervical Bethesda

Extracts 5 variables from cervical cytopathology reports:
  1. adequab    -- Specimen adequacy (satisfatoria, insatisfatoria)
  2. zt         -- Transformation zone (presente, ausente)
  3. resultado  -- Bethesda classification (NILM, ASC_US, LSIL, etc.)
  4. organismos -- Microorganisms (multi-valued, semicolon-separated)
  5. hormonal   -- Hormonal pattern (trofico, atrofico, hipoestrogenico)

Data:
  Holdout: holdout_cervical.csv (200)
  R regex: regex_cervical.csv
"""

import re
import sys
import csv
import unicodedata
from typing import Optional, Tuple, List


# ===========================================================================
# NORMALIZE + SECTION GUARD
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

    Cervical reports have section IV) COMENTÁRIOS with Bethesda reference text.
    Truncates at the first occurrence of a section marker.
    """
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
# ADEQUAB -- Specimen adequacy
# ===========================================================================

def detect_adequab(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect specimen adequacy."""

    # Check insatisfatoria first (more specific — contains "satisfat")
    m = re.search(r'insatisfat', text_norm)
    if m:
        return 'insatisfatoria', m.group(0).strip()[:40]

    m = re.search(r'satisfat', text_norm)
    if m:
        return 'satisfatoria', m.group(0).strip()[:40]

    return None, None


# ===========================================================================
# ZT -- Transformation zone
# ===========================================================================

def detect_zt(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect transformation zone / endocervical component."""

    # "zona de transformacao: presente/ausente"
    m = re.search(r'zona de transforma..o\s*:?\s*(presente|representad|ausente)', text_norm)
    if m:
        val = m.group(1)
        if val.startswith('ausente'):
            return 'ausente', m.group(0).strip()[:60]
        return 'presente', m.group(0).strip()[:60]

    # "componente endocervical ... presente/ausente"
    m = re.search(r'componente endocervical.{0,30}(presente|ausente)', text_norm)
    if m:
        val = m.group(1)
        return val, m.group(0).strip()[:60]

    # Older format: "EPITÉLIOS REPRESENTADOS: ESCAMOSO; GLANDULAR" → glandular implies ZT
    m = re.search(r'epitelios\s+representados.{0,40}glandular', text_norm)
    if m:
        return 'presente', m.group(0).strip()[:60]

    return None, None


# ===========================================================================
# RESULTADO -- Bethesda classification
# ===========================================================================

def detect_resultado(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect Bethesda classification result.

    Priority: highest grade first (carcinoma_escamoso > adenocarcinoma > AIS >
    HSIL > AGC > ASC_H > LSIL > ASC_US > NILM).

    AIS is checked BEFORE adenocarcinoma to prevent "adenocarcinoma in situ"
    from matching "adenocarcinoma" (R regex bug).
    """

    # 1. Carcinoma escamoso (invasive squamous carcinoma)
    m = re.search(r'carcinoma\s+escamo|carcinoma\s+epiderm', text_norm)
    if m:
        return 'carcinoma_escamoso', m.group(0).strip()[:60]

    # 2. AIS (check BEFORE adenocarcinoma — "adenocarcinoma in situ" contains "adenocarcinoma")
    # Handle quotes: adenocarcinoma "in situ" (common in older reports)
    m = re.search(r'\bais\b|adenocarcinoma\s*"?\s*in\s+situ', text_norm)
    if m:
        return 'AIS', m.group(0).strip()[:40]

    # 3. Adenocarcinoma (invasive — after AIS excluded)
    m = re.search(r'adenocarcinoma', text_norm)
    if m:
        return 'adenocarcinoma', m.group(0).strip()[:40]

    # 4. AGC (atypical glandular cells)
    m = re.search(r'\bagc\b|celulas\s+glandulares\s+at.picas|atipias.{0,25}celulas\s+glandulares', text_norm)
    if m:
        return 'AGC', m.group(0).strip()[:60]

    # 5. HSIL (high-grade squamous intraepithelial lesion)
    # Guard: "excluir lesão de alto grau" is ASC-H context, not HSIL
    # NIC II/III = CIN 2/3 = HSIL equivalent
    # "favorecendo lesão de alto grau" = indeterminate leaning HSIL
    m = re.search(
        r'\bhsil\b'
        r'|les.o\s+intraepitelial.{0,20}alto\s+grau'
        r'|intraepitelial\s+de\s+alto'
        r'|\bnic\s+(ii|iii|2|3)\b'
        r'|favorecendo.{0,20}alto\s+grau',
        text_norm)
    if m:
        # Check it's not in "excluir lesão" / "afastar" context (ASC-H)
        start = max(0, m.start() - 40)
        prefix = text_norm[start:m.start()]
        if 'excluir' not in prefix and 'afastar' not in prefix:
            return 'HSIL', m.group(0).strip()[:60]

    # 6. ASC-H (atypical squamous cells, cannot exclude HSIL)
    # Also match descriptive: "não podendo excluir/afastar lesão ... de alto grau"
    m = re.search(
        r'\basc[\s-]h\b'
        r'|excluir.{0,50}alto\s+grau'
        r'|afastar.{0,50}alto\s+grau',
        text_norm)
    if m:
        return 'ASC_H', m.group(0).strip()[:80]

    # 7. LSIL (low-grade squamous intraepithelial lesion)
    m = re.search(r'\blsil\b|les.o.{0,40}baixo\s+grau|intraepitelial.{0,30}baixo\s+grau|intraepitelial.{0,30}grau\s+i\b|\bnic\s+i\b|\bnic\s*1\b', text_norm)
    if m:
        return 'LSIL', m.group(0).strip()[:60]

    # 8. ASC-US (atypical squamous cells of undetermined significance)
    m = re.search(r'\basc[\s-]us\b|escamosas\s+at.picas\s+de\s+significado|atipias.{0,25}escamosas.{0,25}significado\s+indeterminado', text_norm)
    if m:
        return 'ASC_US', m.group(0).strip()[:80]

    # 9. NILM (negative for intraepithelial lesion or malignancy)
    m = re.search(r'\bnilm\b|negativo\s+para\s+les.o', text_norm)
    if m:
        return 'NILM', m.group(0).strip()[:60]

    return None, None


# ===========================================================================
# ORGANISMOS -- Microorganisms (multi-valued)
# ===========================================================================

def detect_organismos(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect microorganisms. Returns semicolon-separated list."""

    found = []
    evidences = []

    patterns = [
        ('lactobacilos', r'lactobacil'),
        ('gardnerella',  r'gardnerella|shift.{0,15}flora|vaginose'),
        ('candida',      r'candida|fungo|levedura|hifa|pseudo-?hifa|organism.s\s+fung'),
        ('trichomonas',  r'trichomonas|tricomonas'),
        ('actinomyces',  r'actinomyces'),
        ('herpes',       r'herpes|\bhsv\b'),
    ]

    for label, pat in patterns:
        m = re.search(pat, text_norm)
        if m:
            found.append(label)
            evidences.append(m.group(0).strip()[:30])

    if found:
        return ';'.join(found), '; '.join(evidences)

    return None, None


# ===========================================================================
# HORMONAL -- Hormonal pattern
# ===========================================================================

def detect_hormonal(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect hormonal pattern."""

    # Atrofico / hipotrofico first (more specific — contains "trofico")
    m = re.search(r'atr.fico|hipotr.fico|padrao\s+atr.fico', text_norm)
    if m:
        return 'atrofico', m.group(0).strip()[:40]

    # Hipoestrogenico
    m = re.search(r'hipoestr', text_norm)
    if m:
        return 'hipoestrogenico', m.group(0).strip()[:40]

    # Trofico (after excluding atrofico/hipotrofico)
    m = re.search(r'tr.fico', text_norm)
    if m:
        return 'trofico', m.group(0).strip()[:40]

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

    # --- ADEQUAB ---
    t = normalize("Satisfatório para avaliação.")
    v, _ = detect_adequab(t)
    check("adequab_satisf", v, "satisfatoria")

    t = normalize("Insatisfatório para avaliação.")
    v, _ = detect_adequab(t)
    check("adequab_insatisf", v, "insatisfatoria")

    t = normalize("Amostra satisfatória")
    v, _ = detect_adequab(t)
    check("adequab_satisf2", v, "satisfatoria")

    t = normalize("SEM INFORMAÇÃO DE ADEQUABILIDADE")
    v, _ = detect_adequab(t)
    check("adequab_absent", v, None)

    # --- ZT ---
    t = normalize("Componente endocervical / zona de transformação:presente")
    v, _ = detect_zt(t)
    check("zt_presente", v, "presente")

    t = normalize("zona de transformação: ausente")
    v, _ = detect_zt(t)
    check("zt_ausente", v, "ausente")

    t = normalize("zona de transformação: representada")
    v, _ = detect_zt(t)
    check("zt_representada", v, "presente")

    t = normalize("componente endocervical presente")
    v, _ = detect_zt(t)
    check("zt_endocerv_presente", v, "presente")

    t = normalize("componente endocervical ausente")
    v, _ = detect_zt(t)
    check("zt_endocerv_ausente", v, "ausente")

    # Older format: epitélios representados com glandular → ZT presente
    t = normalize("EPITÉLIOS REPRESENTADOS NA AMOSTRA: ESCAMOSO; GLANDULAR; METAPLÁSICO")
    v, _ = detect_zt(t)
    check("zt_epitelios_glandular", v, "presente")

    t = normalize("SEM INFORMAÇÃO")
    v, _ = detect_zt(t)
    check("zt_absent", v, None)

    # --- RESULTADO ---
    t = normalize("NILM | NEGATIVO PARA LESÃO INTRAEPITELIAL E MALIGNIDADE")
    v, _ = detect_resultado(t)
    check("res_nilm", v, "NILM")

    t = normalize("ASC-US | ATIPIAS EM CÉLULAS ESCAMOSAS DE SIGNIFICADO INDETERMINADO")
    v, _ = detect_resultado(t)
    check("res_ascus", v, "ASC_US")

    t = normalize("LSIL | LESÃO INTRAEPITELIAL ESCAMOSA DE BAIXO GRAU")
    v, _ = detect_resultado(t)
    check("res_lsil", v, "LSIL")

    t = normalize("HSIL | LESÃO INTRAEPITELIAL ESCAMOSA DE ALTO GRAU")
    v, _ = detect_resultado(t)
    check("res_hsil", v, "HSIL")

    t = normalize("ASC-H | ATIPIAS EM CÉLULAS ESCAMOSAS NÃO SENDO POSSÍVEL EXCLUIR LESÃO DE ALTO GRAU")
    v, _ = detect_resultado(t)
    check("res_asch", v, "ASC_H")

    t = normalize("CÉLULAS GLANDULARES ATÍPICAS (AGC)")
    v, _ = detect_resultado(t)
    check("res_agc", v, "AGC")

    t = normalize("ADENOCARCINOMA IN SITU")
    v, _ = detect_resultado(t)
    check("res_ais", v, "AIS")

    # Quoted form: adenocarcinoma "in situ"
    t = normalize('ADENOCARCINOMA "IN SITU"')
    v, _ = detect_resultado(t)
    check("res_ais_quoted", v, "AIS")

    t = normalize("ADENOCARCINOMA (ENDOMÉTRIO ? ENDOCÉRVICE ?)")
    v, _ = detect_resultado(t)
    check("res_adenoca", v, "adenocarcinoma")

    t = normalize("CARCINOMA ESCAMOSO INVASOR")
    v, _ = detect_resultado(t)
    check("res_carcesc", v, "carcinoma_escamoso")

    t = normalize("CARCINOMA EPIDERMÓIDE")
    v, _ = detect_resultado(t)
    check("res_epiderm", v, "carcinoma_escamoso")

    # Priority: HSIL > ASC-H when both present
    t = normalize("HSIL | LESÃO DE ALTO GRAU ASC-H | ATIPIAS")
    v, _ = detect_resultado(t)
    check("res_hsil_over_asch", v, "HSIL")

    # Priority: ASC-H > LSIL when both present (report highest)
    t = normalize("LSIL | LESÃO DE BAIXO GRAU ASC-H | ATIPIAS ESCAMOSAS")
    v, _ = detect_resultado(t)
    check("res_asch_over_lsil", v, "ASC_H")

    # "negativo para lesão" without abbreviation
    t = normalize("NEGATIVO PARA LESÃO INTRAEPITELIAL OU MALIGNIDADE")
    v, _ = detect_resultado(t)
    check("res_nilm_long", v, "NILM")

    # "lesão intraepitelial de alto grau" without HSIL abbreviation
    t = normalize("LESÃO INTRAEPITELIAL DE ALTO GRAU")
    v, _ = detect_resultado(t)
    check("res_hsil_long", v, "HSIL")

    t = normalize("LESÃO INTRAEPITELIAL DE BAIXO GRAU")
    v, _ = detect_resultado(t)
    check("res_lsil_long", v, "LSIL")

    t = normalize("ATIPIAS EM CÉLULAS ESCAMOSAS DE SIGNIFICADO INDETERMINADO")
    v, _ = detect_resultado(t)
    check("res_ascus_long", v, "ASC_US")

    # "lesão intraepitelial de grau I" (older format)
    t = normalize("LESÃO INTRAEPITELIAL ESCAMOSA DE GRAU I")
    v, _ = detect_resultado(t)
    check("res_lsil_grau_i", v, "LSIL")

    # NIC I
    t = normalize("COMPATÍVEL COM NIC I")
    v, _ = detect_resultado(t)
    check("res_nic_i", v, "LSIL")

    # "atipias em células glandulares" (alternate AGC word order)
    t = normalize("ATIPIAS EM CÉLULAS GLANDULARES ENDOCERVICAIS")
    v, _ = detect_resultado(t)
    check("res_agc_atipias", v, "AGC")

    # NIC II / NIC III → HSIL
    t = normalize("LESÃO INTRAEPITELIAL DE GRAU INDETERMINADO, FAVORECENDO LESÃO DE ALTO GRAU (NIC II / NIC III)")
    v, _ = detect_resultado(t)
    check("res_hsil_nic_ii", v, "HSIL")

    # NIC 2 alone
    t = normalize("COMPATÍVEL COM NIC 2")
    v, _ = detect_resultado(t)
    check("res_nic2", v, "HSIL")

    # NIC III alone
    t = normalize("COMPATÍVEL COM NIC III")
    v, _ = detect_resultado(t)
    check("res_nic_iii", v, "HSIL")

    # "não se podendo afastar lesão de alto grau" → ASC_H (not HSIL)
    t = normalize("LESÃO INTRAEPITELIAL ESCAMOSA DE GRAU INDETERMINADO, NÃO SE PODENDO AFASTAR LESÃO INTRAEPITELIAL ESCAMOSA DE ALTO GRAU")
    v, _ = detect_resultado(t)
    check("res_asch_afastar", v, "ASC_H")

    t = normalize("CITOLOGIA NORMAL")
    v, _ = detect_resultado(t)
    check("res_absent", v, None)

    # --- ORGANISMOS ---
    t = normalize("ORGANISMOS: Lactobacilos")
    v, _ = detect_organismos(t)
    check("org_lacto", v, "lactobacilos")

    t = normalize("ORGANISMOS: Desvio da flora sugestivo de Gardnerella")
    v, _ = detect_organismos(t)
    check("org_gard", v, "gardnerella")

    t = normalize("ORGANISMOS: Candida sp.")
    v, _ = detect_organismos(t)
    check("org_candida", v, "candida")

    t = normalize("ORGANISMOS: Trichomonas vaginalis")
    v, _ = detect_organismos(t)
    check("org_tricho", v, "trichomonas")

    t = normalize("ORGANISMOS: Actinomyces sp.")
    v, _ = detect_organismos(t)
    check("org_actino", v, "actinomyces")

    t = normalize("Efeito citopático compatível com Herpes")
    v, _ = detect_organismos(t)
    check("org_herpes", v, "herpes")

    # Multi-valued
    t = normalize("ORGANISMOS: Lactobacilos. Também Candida sp.")
    v, _ = detect_organismos(t)
    check("org_multi", v, "lactobacilos;candida")

    # Alternate spellings
    t = normalize("Presença de leveduras")
    v, _ = detect_organismos(t)
    check("org_levedura", v, "candida")

    t = normalize("Presença de hifas e pseudo-hifas")
    v, _ = detect_organismos(t)
    check("org_hifas", v, "candida")

    t = normalize("Organismos fúngicos")
    v, _ = detect_organismos(t)
    check("org_fungicos", v, "candida")

    t = normalize("Vaginose bacteriana")
    v, _ = detect_organismos(t)
    check("org_vaginose", v, "gardnerella")

    t = normalize("Tricomonas vaginalis")
    v, _ = detect_organismos(t)
    check("org_tricomonas_alt", v, "trichomonas")

    t = normalize("Bacilos")
    v, _ = detect_organismos(t)
    check("org_bacilos_no_match", v, None)

    # --- HORMONAL ---
    t = normalize("Quadro cito-hormonal: trófico")
    v, _ = detect_hormonal(t)
    check("horm_trofico", v, "trofico")

    t = normalize("Quadro cito-hormonal: atrófico")
    v, _ = detect_hormonal(t)
    check("horm_atrofico", v, "atrofico")

    t = normalize("Padrão hipotrófico")
    v, _ = detect_hormonal(t)
    check("horm_hipotrofico", v, "atrofico")

    t = normalize("Padrão hipoestrogênico")
    v, _ = detect_hormonal(t)
    check("horm_hipoest", v, "hipoestrogenico")

    t = normalize("Padrão atrófico")
    v, _ = detect_hormonal(t)
    check("horm_padrao_atrofico", v, "atrofico")

    t = normalize("SEM INFORMAÇÃO HORMONAL")
    v, _ = detect_hormonal(t)
    check("horm_absent", v, None)

    # --- COMENTÁRIOS STRIPPING ---
    raw = ("III) RESULTADO: NILM | NEGATIVO PARA LESÃO. "
           "IV) COMENTÁRIOS: The Bethesda System classifica HSIL e LSIL.")
    t = normalize(raw)
    t_diag = strip_comentarios(t)
    v, _ = detect_resultado(t_diag)
    check("coment_nilm_not_hsil", v, "NILM")

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

    vars_to_compare = ['adequab', 'zt', 'resultado', 'organismos', 'hormonal']

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
        text_diag = strip_comentarios(text_norm)

        adequab_val, adequab_ev = detect_adequab(text_diag)
        zt_val, zt_ev = detect_zt(text_diag)
        res_val, res_ev = detect_resultado(text_diag)
        org_val, org_ev = detect_organismos(text_diag)
        # Hormonal info is in COMENTÁRIOS section (genuine clinical data, not reference)
        horm_val, horm_ev = detect_hormonal(text_norm)

        row = {
            'numExame': numExame,
            'estrato': rec.get('estrato', ''),
            'l1_adequab': adequab_val or '',
            'l1_adequab_evidence': adequab_ev or '',
            'l1_zt': zt_val or '',
            'l1_zt_evidence': zt_ev or '',
            'l1_resultado': res_val or '',
            'l1_resultado_evidence': res_ev or '',
            'l1_organismos': org_val or '',
            'l1_organismos_evidence': org_ev or '',
            'l1_hormonal': horm_val or '',
            'l1_hormonal_evidence': horm_ev or '',
        }
        results.append(row)

        l1_vals = {
            'adequab': adequab_val or '',
            'zt': zt_val or '',
            'resultado': res_val or '',
            'organismos': org_val or '',
            'hormonal': horm_val or '',
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

            # Normalize comparison
            l1v_cmp = normalize(l1v) if l1v else ''
            rg_v_cmp = normalize(rg_v) if rg_v else ''

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
    print("L1 vs R-REGEX CONCORDANCE REPORT -- Cervical Bethesda")
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
            holdout_csv = r'Y:\IDOC-Patologia\04_artigos\artigo1_validacao_ia\holdout\holdout_cervical.csv'
            regex_csv = r'Y:\IDOC-Patologia\04_artigos\artigo1_validacao_ia\holdout\regex_cervical.csv'
            output_csv = r'Y:\DDPA\framework_extracao_v4\04_piloto\cervical_bethesda_l1_holdout.csv'
            run_comparison(holdout_csv, regex_csv, output_csv)
