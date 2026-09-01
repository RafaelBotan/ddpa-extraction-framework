#!/usr/bin/env python3
"""
L1 Deterministic Detector — Gástrico OLGA (Sydney Modified)

Extracts 8 variables from gastric biopsy pathology reports:
  1. olga          — OLGA staging (0, I, II, III, IV)
  2. hp            — H. pylori status (positivo, negativo)
  3. metodo_hp     — H. pylori detection method (giemsa, ihq, warthin_starry)
  4. mi            — Intestinal metaplasia (sim, nao)
  5. atrofia       — Atrophy (sim, nao)
  6. displasia     — Dysplasia (ausente, baixo_grau, alto_grau)
  7. atividade     — Neutrophilic activity (sim, nao)
  8. polipos_fund  — Fundic gland polyps (sim, nao)

Data:
  Holdout: holdout_gastrico.csv (180)
  R regex: regex_gastrico.csv
  LLM dev: llm_gastrico.json (35)
  LLM test: llm_gastrico.json (134)

Usage:
  python gastrico_olga_l1_v4.py --smoke       # smoke tests only
  python gastrico_olga_l1_v4.py --run         # holdout comparison
  python gastrico_olga_l1_v4.py               # smoke + holdout
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
    # The data has U+FFFD replacement characters from legacy encoding
    text = text.replace('\ufffd', '')
    # NFD decompose, strip combining marks
    text = unicodedata.normalize('NFD', text)
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    text = text.lower()
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ===========================================================================
# OLGA — OLGA staging grade (0, I, II, III, IV)
# ===========================================================================

def detect_olga(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect OLGA staging from conclusion text."""

    # "OLGA 0" or "OLGA ZERO"
    m = re.search(r'olga\s*(?:0|zero)\b', text_norm)
    if m:
        return '0', m.group(0).strip()[:40]

    # "OLGA I/II/III/IV" (roman numerals) or "OLGA 1/2/3/4" (arabic)
    m = re.search(r'olga\s+([ivx]+|\d)', text_norm)
    if m:
        val = m.group(1).upper()
        mapping = {
            'I': 'I', '1': 'I',
            'II': 'II', '2': 'II',
            'III': 'III', '3': 'III',
            'IV': 'IV', '4': 'IV',
        }
        stage = mapping.get(val)
        if stage:
            return stage, m.group(0).strip()[:40]

    return None, None


# ===========================================================================
# HP — H. pylori status (positivo, negativo)
# ===========================================================================

def detect_hp(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect H. pylori status."""

    # "H. pylori ... POSITIVA/POSITIVO" or "HELICOBACTER ... POSITIV"
    m = re.search(
        r'(?:h\.?\s*pylori|helicobacter).{0,50}positiv[oa]',
        text_norm)
    if m:
        return 'positivo', m.group(0).strip()[:80]

    # "H. pylori ... NEGATIVA/NEGATIVO" or "HELICOBACTER ... NEGATIV"
    m = re.search(
        r'(?:h\.?\s*pylori|helicobacter).{0,50}negativ[oa]?',
        text_norm)
    if m:
        return 'negativo', m.group(0).strip()[:80]

    # "pesquisa ... negativa" near pylori context
    m = re.search(
        r'pesquisa\s+de\s+h\.?\s*pylori.{0,60}negativ[oa]',
        text_norm)
    if m:
        return 'negativo', m.group(0).strip()[:80]

    m = re.search(
        r'pesquisa\s+de\s+h\.?\s*pylori.{0,60}positiv[oa]',
        text_norm)
    if m:
        return 'positivo', m.group(0).strip()[:80]

    return None, None


# ===========================================================================
# METODO_HP — H. pylori detection method
# ===========================================================================

def detect_metodo_hp(text_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """Detect H. pylori detection method."""

    # Giemsa staining (most common)
    m = re.search(r'giemsa', text_norm)
    if m:
        return 'giemsa', m.group(0).strip()[:40]

    # Immunohistochemistry
    m = re.search(r'imuno[\s-]?histoqu', text_norm)
    if m:
        return 'ihq', m.group(0).strip()[:40]

    # Warthin-Starry
    m = re.search(r'warthin', text_norm)
    if m:
        return 'warthin_starry', m.group(0).strip()[:40]

    return None, None


# ===========================================================================
# MI — Intestinal metaplasia (sim/nao)
# ===========================================================================

def detect_mi(text_norm: str) -> Tuple[str, Optional[str]]:
    """Detect intestinal metaplasia presence."""

    # Sydney table format: "METAPLASIA INTESTINAL: LEVE/MODERADA/ACENTUADA/PRESENTE"
    m = re.search(
        r'metaplasia\s+intestinal:?\s*(?:leve|moderada|acentuada|presente|focal|completa|incompleta)',
        text_norm)
    if m:
        return 'sim', m.group(0).strip()[:60]

    # Conclusion: "ASSOCIADA A ... METAPLASIA INTESTINAL" or "COM METAPLASIA INTESTINAL"
    m = re.search(
        r'(?:associada?\s+a|com)\s+(?:atrofia\s+e\s+)?metaplasia\s+intestinal',
        text_norm)
    if m:
        return 'sim', m.group(0).strip()[:60]

    # "metaplasia intestinal" in any positive context (not preceded by "sem" or "ausencia")
    m = re.search(r'metaplasia\s+intestinal', text_norm)
    if m:
        # Check for negation in preceding context
        start = max(0, m.start() - 30)
        prefix = text_norm[start:m.start()]
        if re.search(r'(?:sem|ausencia\s+de|inexistente|negativ)', prefix):
            return 'nao', m.group(0).strip()[:60]
        # Check if followed by INEXISTENTE or AUSENTE
        end = min(len(text_norm), m.end() + 30)
        suffix = text_norm[m.end():end]
        if re.search(r':?\s*(?:inexistente|ausente)\b', suffix):
            return 'nao', (m.group(0) + suffix.split('\n')[0]).strip()[:60]
        return 'sim', m.group(0).strip()[:60]

    return 'nao', None


# ===========================================================================
# ATROFIA — Atrophy (sim/nao)
# ===========================================================================

def detect_atrofia(text_norm: str) -> Tuple[str, Optional[str]]:
    """Detect atrophy presence."""

    # Sydney table: "ATROFIA: LEVE/MODERADA/ACENTUADA/PRESENTE"
    m = re.search(
        r'atrofia:?\s*(?:leve|moderada|acentuada|presente)',
        text_norm)
    if m:
        return 'sim', m.group(0).strip()[:60]

    # Conclusion: "ASSOCIADA A ATROFIA" or "COM ATROFIA"
    m = re.search(
        r'(?:associada?\s+a|com)\s+atrofia',
        text_norm)
    if m:
        return 'sim', m.group(0).strip()[:60]

    # "atrofica" (adjective form)
    m = re.search(r'(?:gastrite|mucosa)\s+\w*\s*atrofica', text_norm)
    if m:
        return 'sim', m.group(0).strip()[:60]

    return 'nao', None


# ===========================================================================
# DISPLASIA — Dysplasia (ausente, baixo_grau, alto_grau)
# ===========================================================================

def detect_displasia(text_norm: str) -> Tuple[str, Optional[str]]:
    """Detect dysplasia grade. Hierarchy: alto_grau > baixo_grau > ausente."""

    # Alto grau first (hierarchy)
    m = re.search(
        r'(?:displasia\s+de\s+alto\s+grau|alto\s+grau\s*[^.]{0,20}displasia|displasia\s+acentuada)',
        text_norm)
    if m:
        return 'alto_grau', m.group(0).strip()[:60]

    # Baixo grau
    m = re.search(
        r'(?:displasia\s+(?:de\s+)?baixo\s+grau|baixo\s+grau\s*[^.]{0,20}displasia'
        r'|displasia\s+moderada|displasia\s+leve)',
        text_norm)
    if m:
        return 'baixo_grau', m.group(0).strip()[:60]

    # "atipias citoarquiteturais de baixo grau (displasia moderada)"
    m = re.search(
        r'atipias\s+citoarquiteturais\s+(?:de\s+)?baixo\s+grau',
        text_norm)
    if m:
        return 'baixo_grau', m.group(0).strip()[:60]

    # "atipias citoarquiteturais leves" or "moderadas"
    m = re.search(
        r'atipias\s+citoarquiteturais\s+(?:leves|moderadas)',
        text_norm)
    if m:
        return 'baixo_grau', m.group(0).strip()[:60]

    # "atipias citoarquiteturais de alto grau"
    m = re.search(
        r'atipias\s+citoarquiteturais\s+(?:de\s+)?alto\s+grau',
        text_norm)
    if m:
        return 'alto_grau', m.group(0).strip()[:60]

    # "atipias indefinidas para displasia"
    m = re.search(r'atipias\s+indefinidas\s+para\s+displasia', text_norm)
    if m:
        return 'baixo_grau', m.group(0).strip()[:60]

    # Explicit absence: "NEGATIVO PARA DISPLASIA" / "SEM DISPLASIA"
    m = re.search(
        r'(?:negativ[oa]\s+para\s+displasia|sem\s+displasia)',
        text_norm)
    if m:
        return 'ausente', m.group(0).strip()[:60]

    return 'ausente', None


# ===========================================================================
# ATIVIDADE — Neutrophilic activity (sim/nao)
# ===========================================================================

def detect_atividade(text_norm: str) -> Tuple[str, Optional[str]]:
    """Detect neutrophilic activity."""

    # Sydney table: "ATIVIDADE NEUTROFILICA: LEVE/MODERADA/ACENTUADA"
    m = re.search(
        r'atividade\s+neutrofilica:?\s*(?:leve|moderada|acentuada)',
        text_norm)
    if m:
        return 'sim', m.group(0).strip()[:60]

    # "ATIVIDADE INFLAMATORIA LEVE/MODERADA/ACENTUADA"
    m = re.search(
        r'atividade\s+inflamatoria:?\s*(?:leve|moderada|acentuada)',
        text_norm)
    if m:
        return 'sim', m.group(0).strip()[:60]

    # "ATIVIDADE INFLAMATORIA AUSENTE" — explicit negative
    m = re.search(r'atividade\s+(?:neutrofilica|inflamatoria):?\s*(?:ausente|inexistente)', text_norm)
    if m:
        return 'nao', m.group(0).strip()[:60]

    # "INFILTRADO NEUTROFILICO LEVE/MODERADO/ACENTUADO" (older format)
    m = re.search(
        r'infiltrado\s+neutrofilico:?\s*(?:leve|moderado|acentuado)',
        text_norm)
    if m:
        return 'sim', m.group(0).strip()[:60]

    # "INFILTRADO NEUTROFILICO AUSENTE"
    m = re.search(r'infiltrado\s+neutrofilico:?\s*ausente', text_norm)
    if m:
        return 'nao', m.group(0).strip()[:60]

    # "GASTRITE ... AGUDA" implies activity
    m = re.search(r'gastrite.{0,60}aguda', text_norm)
    if m:
        return 'sim', m.group(0).strip()[:60]

    # Conclusion: "ATIVA" (not "INATIVA")
    # Must be in conclusion context (gastrite ... ativa)
    m = re.search(r'gastrite\s+cronica\s+(?:\w+\s+)?ativa\b', text_norm)
    if m and 'inativ' not in m.group(0):
        return 'sim', m.group(0).strip()[:60]

    # Broader "ativa" at word boundary (not inativa)
    # Check for "ativa" as standalone or in "cronica ativa"
    m = re.search(r'\bativa\b', text_norm)
    if m:
        # Verify it's not "inativa"
        start = max(0, m.start() - 2)
        if text_norm[start:m.start()].rstrip() not in ('in', 'na'):
            if not text_norm[max(0, m.start()-3):m.start()].endswith('in'):
                return 'sim', text_norm[max(0, m.start()-20):m.end()+10].strip()[:60]

    # "INATIVA" → nao
    if re.search(r'\binativa\b', text_norm):
        return 'nao', 'inativa'

    return 'nao', None


# ===========================================================================
# POLIPOS_FUND — Fundic gland polyps (sim/nao)
# ===========================================================================

def detect_polipos_fund(text_norm: str) -> Tuple[str, Optional[str]]:
    """Detect fundic gland polyps."""

    # "polipo(s) de glandulas fundicas"
    m = re.search(r'p.lipos?\s+de\s+gl.ndulas?\s+f.ndicas?', text_norm)
    if m:
        return 'sim', m.group(0).strip()[:60]

    # "polipo fundico"
    m = re.search(r'p.lipo\s+f.ndico', text_norm)
    if m:
        return 'sim', m.group(0).strip()[:40]

    # "polipos fundicos"
    m = re.search(r'p.lipos\s+f.ndicos', text_norm)
    if m:
        return 'sim', m.group(0).strip()[:40]

    return 'nao', None


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

    # --- OLGA ---
    t = normalize("ESTÁGIO OLGA 0.")
    v, _ = detect_olga(t)
    check("olga_0", v, "0")

    t = normalize("ESTÁGIO OLGA ZERO.")
    v, _ = detect_olga(t)
    check("olga_zero", v, "0")

    t = normalize("ESTÁGIO OLGA I.")
    v, _ = detect_olga(t)
    check("olga_I", v, "I")

    t = normalize("ESTÁGIO OLGA II.")
    v, _ = detect_olga(t)
    check("olga_II", v, "II")

    t = normalize("ESTÁGIO OLGA III.")
    v, _ = detect_olga(t)
    check("olga_III", v, "III")

    t = normalize("ESTÁGIO OLGA IV.")
    v, _ = detect_olga(t)
    check("olga_IV", v, "IV")

    t = normalize("ESTAGIO OLGA 3")
    v, _ = detect_olga(t)
    check("olga_arabic_3", v, "III")

    t = normalize("GASTRITE CRÔNICA SEM OLGA")
    v, _ = detect_olga(t)
    check("olga_absent", v, None)

    # --- HP ---
    t = normalize("PESQUISA DE H.pylori (COLORAÇÃO DE GIEMSA): POSITIVA (+/+++)")
    v, _ = detect_hp(t)
    check("hp_pos", v, "positivo")

    t = normalize("PESQUISA DE H.pylori (COLORAÇÃO DE GIEMSA): NEGATIVA")
    v, _ = detect_hp(t)
    check("hp_neg", v, "negativo")

    t = normalize("HELICOBACTER PYLORI: POSITIVO")
    v, _ = detect_hp(t)
    check("hp_helicobacter_pos", v, "positivo")

    t = normalize("HELICOBACTER PYLORI: NEGATIVO")
    v, _ = detect_hp(t)
    check("hp_helicobacter_neg", v, "negativo")

    t = normalize("H. pylori NEGATIVA EM MUCOSA DE CORPO E ANTRO")
    v, _ = detect_hp(t)
    check("hp_neg_long", v, "negativo")

    t = normalize("Sem menção de bactéria")
    v, _ = detect_hp(t)
    check("hp_absent", v, None)

    # --- METODO_HP ---
    t = normalize("COLORAÇÃO DE GIEMSA")
    v, _ = detect_metodo_hp(t)
    check("metodo_giemsa", v, "giemsa")

    t = normalize("IMUNO-HISTOQUÍMICA")
    v, _ = detect_metodo_hp(t)
    check("metodo_ihq", v, "ihq")

    t = normalize("COLORAÇÃO DE WARTHIN-STARRY")
    v, _ = detect_metodo_hp(t)
    check("metodo_warthin", v, "warthin_starry")

    t = normalize("SEM COLORAÇÃO ESPECIAL")
    v, _ = detect_metodo_hp(t)
    check("metodo_absent", v, None)

    # --- MI ---
    t = normalize("METAPLASIA INTESTINAL: LEVE")
    v, _ = detect_mi(t)
    check("mi_leve", v, "sim")

    t = normalize("METAPLASIA INTESTINAL: MODERADA")
    v, _ = detect_mi(t)
    check("mi_moderada", v, "sim")

    t = normalize("METAPLASIA INTESTINAL: ACENTUADA")
    v, _ = detect_mi(t)
    check("mi_acentuada", v, "sim")

    t = normalize("METAPLASIA INTESTINAL: PRESENTE")
    v, _ = detect_mi(t)
    check("mi_presente", v, "sim")

    t = normalize("METAPLASIA INTESTINAL: INEXISTENTE")
    v, _ = detect_mi(t)
    check("mi_inexistente", v, "nao")

    t = normalize("METAPLASIA INTESTINAL FOCAL, COMPLETA")
    v, _ = detect_mi(t)
    check("mi_focal_completa", v, "sim")

    t = normalize("ASSOCIADA A ATROFIA E METAPLASIA INTESTINAL")
    v, _ = detect_mi(t)
    check("mi_associada", v, "sim")

    t = normalize("COM METAPLASIA INTESTINAL")
    v, _ = detect_mi(t)
    check("mi_com", v, "sim")

    t = normalize("METAPLASIA INTESTINAL AUSENTE. ESTÁGIO OLGA I.")
    v, _ = detect_mi(t)
    check("mi_ausente", v, "nao")

    t = normalize("METAPLASIA INTESTINAL (NEGATIVO PARA DISPLASIA)")
    v, _ = detect_mi(t)
    check("mi_negativo_displasia", v, "sim")

    t = normalize("METAPLASIA INTESTINAL INCOMPLETA: LEVE")
    v, _ = detect_mi(t)
    check("mi_incompleta_leve", v, "sim")

    t = normalize("GASTRITE SEM METAPLASIA")
    v, _ = detect_mi(t)
    check("mi_default_nao", v, "nao")

    # --- ATROFIA ---
    t = normalize("ATROFIA: LEVE")
    v, _ = detect_atrofia(t)
    check("atrofia_leve", v, "sim")

    t = normalize("ATROFIA: MODERADA")
    v, _ = detect_atrofia(t)
    check("atrofia_moderada", v, "sim")

    t = normalize("ATROFIA: ACENTUADA")
    v, _ = detect_atrofia(t)
    check("atrofia_acentuada", v, "sim")

    t = normalize("ATROFIA: PRESENTE")
    v, _ = detect_atrofia(t)
    check("atrofia_presente", v, "sim")

    t = normalize("ATROFIA: INEXISTENTE")
    v, _ = detect_atrofia(t)
    check("atrofia_inexistente", v, "nao")

    t = normalize("ASSOCIADA A ATROFIA")
    v, _ = detect_atrofia(t)
    check("atrofia_associada", v, "sim")

    t = normalize("COM ATROFIA")
    v, _ = detect_atrofia(t)
    check("atrofia_com", v, "sim")

    t = normalize("GASTRITE CRÔNICA ATRÓFICA")
    v, _ = detect_atrofia(t)
    check("atrofia_adjectivo", v, "sim")

    t = normalize("GASTRITE SEM ATROFIA")
    v, _ = detect_atrofia(t)
    check("atrofia_default_nao", v, "nao")

    # --- DISPLASIA ---
    t = normalize("DISPLASIA DE ALTO GRAU")
    v, _ = detect_displasia(t)
    check("displasia_alto", v, "alto_grau")

    t = normalize("DISPLASIA DE BAIXO GRAU")
    v, _ = detect_displasia(t)
    check("displasia_baixo", v, "baixo_grau")

    t = normalize("DISPLASIA MODERADA")
    v, _ = detect_displasia(t)
    check("displasia_moderada", v, "baixo_grau")

    t = normalize("DISPLASIA LEVE")
    v, _ = detect_displasia(t)
    check("displasia_leve", v, "baixo_grau")

    t = normalize("NEGATIVO PARA DISPLASIA")
    v, _ = detect_displasia(t)
    check("displasia_negativo", v, "ausente")

    t = normalize("SEM DISPLASIA")
    v, _ = detect_displasia(t)
    check("displasia_sem", v, "ausente")

    t = normalize("ALTO GRAU DE DISPLASIA EM ANTRO")
    v, _ = detect_displasia(t)
    check("displasia_alto_reversed", v, "alto_grau")

    t = normalize("ATIPIAS CITOARQUITETURAIS DE BAIXO GRAU (DISPLASIA MODERADA)")
    v, _ = detect_displasia(t)
    check("displasia_atipias_baixo", v, "baixo_grau")

    t = normalize("ATIPIAS CITOARQUITETURAIS LEVES")
    v, _ = detect_displasia(t)
    check("displasia_atipias_leves", v, "baixo_grau")

    t = normalize("ATIPIAS CITOARQUITETURAIS MODERADAS")
    v, _ = detect_displasia(t)
    check("displasia_atipias_moderadas", v, "baixo_grau")

    t = normalize("ATIPIAS INDEFINIDAS PARA DISPLASIA")
    v, _ = detect_displasia(t)
    check("displasia_indefinidas", v, "baixo_grau")

    t = normalize("MUCOSA GÁSTRICA SEM LESÕES")
    v, _ = detect_displasia(t)
    check("displasia_default_ausente", v, "ausente")

    # --- ATIVIDADE ---
    t = normalize("ATIVIDADE NEUTROFÍLICA: LEVE")
    v, _ = detect_atividade(t)
    check("ativ_neutrofilica_leve", v, "sim")

    t = normalize("ATIVIDADE NEUTROFÍLICA: MODERADA")
    v, _ = detect_atividade(t)
    check("ativ_neutrofilica_moderada", v, "sim")

    t = normalize("ATIVIDADE NEUTROFÍLICA: ACENTUADA")
    v, _ = detect_atividade(t)
    check("ativ_neutrofilica_acentuada", v, "sim")

    t = normalize("ATIVIDADE NEUTROFÍLICA: INEXISTENTE")
    v, _ = detect_atividade(t)
    check("ativ_neutrofilica_inexistente", v, "nao")

    t = normalize("ATIVIDADE INFLAMATÓRIA: LEVE")
    v, _ = detect_atividade(t)
    check("ativ_inflamatoria_leve", v, "sim")

    t = normalize("GASTRITE CRÔNICA ATIVA")
    v, _ = detect_atividade(t)
    check("ativ_gastrite_ativa", v, "sim")

    t = normalize("GASTRITE CRÔNICA LEVE ATIVA")
    v, _ = detect_atividade(t)
    check("ativ_gastrite_leve_ativa", v, "sim")

    t = normalize("GASTRITE CRÔNICA INATIVA")
    v, _ = detect_atividade(t)
    check("ativ_gastrite_inativa", v, "nao")

    t = normalize("ATIVIDADE INFLAMATÓRIA AUSENTE")
    v, _ = detect_atividade(t)
    check("ativ_inflamatoria_ausente", v, "nao")

    t = normalize("INFILTRADO NEUTROFÍLICO LEVE")
    v, _ = detect_atividade(t)
    check("ativ_infiltrado_leve", v, "sim")

    t = normalize("INFILTRADO NEUTROFÍLICO AUSENTE")
    v, _ = detect_atividade(t)
    check("ativ_infiltrado_ausente", v, "nao")

    t = normalize("GASTRITE EM MUCOSA DO ANTRO, PREDOMINANTEMENTE AGUDA, EROSIVA")
    v, _ = detect_atividade(t)
    check("ativ_aguda", v, "sim")

    t = normalize("MUCOSA GÁSTRICA HABITUAL")
    v, _ = detect_atividade(t)
    check("ativ_default_nao", v, "nao")

    # --- POLIPOS_FUND ---
    t = normalize("PÓLIPO DE GLÂNDULAS FÚNDICAS SEM ATIPIAS")
    v, _ = detect_polipos_fund(t)
    check("polipo_fund_singular", v, "sim")

    t = normalize("PÓLIPOS DE GLÂNDULAS FÚNDICAS")
    v, _ = detect_polipos_fund(t)
    check("polipo_fund_plural", v, "sim")

    t = normalize("PÓLIPO FÚNDICO")
    v, _ = detect_polipos_fund(t)
    check("polipo_fundico", v, "sim")

    t = normalize("PÓLIPO HIPERPLÁSICO")
    v, _ = detect_polipos_fund(t)
    check("polipo_hiperplasico_nao", v, "nao")

    t = normalize("GASTRITE CRÔNICA")
    v, _ = detect_polipos_fund(t)
    check("polipo_fund_default_nao", v, "nao")

    print(f"\n  Smoke tests: {total} passed, {failures} failed")
    return failures


# ===========================================================================
# HOLDOUT COMPARISON
# ===========================================================================

def run_comparison(holdout_csv: str, regex_csv: str, output_csv: str):
    """Run L1 on holdout and compare with R regex results."""
    import pandas as pd

    # Load holdout data
    df = pd.read_csv(holdout_csv, dtype=str, encoding='utf-8')
    print(f"Holdout loaded: {len(df)} records")

    # Load R regex baseline
    rg = pd.read_csv(regex_csv, dtype=str, encoding='utf-8')
    rg_dict = {r['numExame']: r for _, r in rg.iterrows()}
    print(f"R regex loaded: {len(rg)} records")

    vars_to_compare = ['olga', 'hp', 'metodo_hp', 'mi', 'atrofia',
                        'displasia', 'atividade', 'polipos_fund']

    # Stats per variable
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

        # Also include microscopia if available
        micro = str(rec.get('microscopia', ''))
        if micro and micro != 'nan':
            text_norm_full = normalize(text + ' ' + micro)
        else:
            text_norm_full = text_norm

        # Extract variables
        olga_val, olga_ev = detect_olga(text_norm_full)
        hp_val, hp_ev = detect_hp(text_norm_full)
        metodo_val, metodo_ev = detect_metodo_hp(text_norm_full)
        mi_val, mi_ev = detect_mi(text_norm_full)
        atrofia_val, atrofia_ev = detect_atrofia(text_norm_full)
        displasia_val, displasia_ev = detect_displasia(text_norm_full)
        atividade_val, atividade_ev = detect_atividade(text_norm_full)
        polipos_val, polipos_ev = detect_polipos_fund(text_norm_full)

        row = {
            'numExame': numExame,
            'estrato': rec.get('estrato', ''),
            'l1_olga': olga_val or '',
            'l1_olga_evidence': olga_ev or '',
            'l1_hp': hp_val or '',
            'l1_hp_evidence': hp_ev or '',
            'l1_metodo_hp': metodo_val or '',
            'l1_metodo_hp_evidence': metodo_ev or '',
            'l1_mi': mi_val,
            'l1_mi_evidence': mi_ev or '',
            'l1_atrofia': atrofia_val,
            'l1_atrofia_evidence': atrofia_ev or '',
            'l1_displasia': displasia_val,
            'l1_displasia_evidence': displasia_ev or '',
            'l1_atividade': atividade_val,
            'l1_atividade_evidence': atividade_ev or '',
            'l1_polipos_fund': polipos_val,
            'l1_polipos_fund_evidence': polipos_ev or '',
        }
        results.append(row)

        # Compare with R regex
        l1_vals = {
            'olga': olga_val or '',
            'hp': hp_val or '',
            'metodo_hp': metodo_val or '',
            'mi': mi_val,
            'atrofia': atrofia_val,
            'displasia': displasia_val,
            'atividade': atividade_val,
            'polipos_fund': polipos_val,
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

            if l1v == rg_v:
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
    print("L1 vs R-REGEX CONCORDANCE REPORT — Gástrico OLGA")
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
            holdout_csv = r'Y:\IDOC-Patologia\04_artigos\artigo1_validacao_ia\holdout\holdout_gastrico.csv'
            regex_csv = r'Y:\IDOC-Patologia\04_artigos\artigo1_validacao_ia\holdout\regex_gastrico.csv'
            output_csv = r'Y:\DDPA\framework_extracao_v4\04_piloto\gastrico_olga_l1_holdout.csv'
            run_comparison(holdout_csv, regex_csv, output_csv)
