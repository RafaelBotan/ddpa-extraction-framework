#!/usr/bin/env python3
"""
L1 Deterministic Detector: inv_linfov + inv_perin (Mama AP)
Framework DDPA v4 — Pathology cross-domain pilot

Category: Cat 4 (binary) — Assertion/Presence
Variables: inv_linfov (invasão linfovascular), inv_perin (invasão perineural)
Domain: Breast surgical pathology (anatomopatológico mama)

Vocab mining source: holdout 200 laudos mama AP (2026-04-12)
"""

import re
import sys
import csv
import json
from pathlib import Path

# ─── Normalization (adapted from framework_core.py) ──────────────────────

def normalize(text: str) -> str:
    """Normalize text for pattern matching."""
    if not text:
        return ""
    t = text.lower()
    # Normalize unicode
    replacements = {
        '\u00e3': 'a', '\u00e1': 'a', '\u00e2': 'a',
        '\u00f5': 'o', '\u00f3': 'o', '\u00f4': 'o',
        '\u00e9': 'e', '\u00ea': 'e',
        '\u00ed': 'i', '\u00fa': 'u', '\u00fc': 'u',
        '\u00e7': 'c', '\u00f1': 'n',
    }
    for src, dst in replacements.items():
        t = t.replace(src, dst)
    # Normalize whitespace
    t = re.sub(r'\s+', ' ', t)
    return t.strip()


# ─── Pattern definitions ─────────────────────────────────────────────────

# Positive patterns (invasion present)
LINFOV_POS = [
    # "invasão linfo-vascular: presente"
    r'invasao\s*linfo[\s-]*vascular\s*:\s*presente',
    # "invasão linfovascular: presente"
    r'invasao\s*linfovascular\s*:\s*presente',
    # "invasão angiolinfática: presente"
    r'invasao\s*angiolinfatica\s*:\s*presente',
    # "invasão vascular linfática: presente"
    r'invasao\s*vascular\s*linfatica\s*:\s*presente',
    # "invasão linfo-vascular: sim"
    r'invasao\s*linfo[\s-]*vascular\s*:\s*sim',
    # "invasão linfo-vascular presente" (without colon)
    r'invasao\s*linfo[\s-]*vascular\s+presente',
    r'invasao\s*angiolinfatica\s+presente',
    # P1: "infiltração" as synonym of "invasão"
    r'infiltracao\s*angiolinfatica\s*:\s*presente',
    r'infiltracao\s*angiolinfatica\s+presente',
    r'infiltracao\s*linfo[\s-]*vascular\s*:\s*presente',
    # "presença de invasão linfovascular"
    r'presenca\s+de\s+invasao\s*linfo[\s-]*vascular',
    # "embolos linfáticos" / "êmbolos"
    r'embolo[s]?\s+linf[aá]tico',
    # Catch "invasão angiolinfática e perineural: presente" — linfov part
    r'invasao\s*angiolinfatica\s+e\s+perineural\s*:\s*presente',
    r'invasao\s*angiolinfatica\s+e\s+perineural\s+presente',
]

LINFOV_NEG = [
    # Structured: "invasão linfo-vascular: não observada"
    r'invasao\s*linfo[\s-]*vascular\s*:\s*nao\s+(observad|evidenciad|identificad|detectad)',
    r'invasao\s*linfovascular\s*:\s*nao\s+(observad|evidenciad|identificad|detectad)',
    # "invasão linfo-vascular: ausente"
    r'invasao\s*linfo[\s-]*vascular\s*:\s*ausente',
    r'invasao\s*linfovascular\s*:\s*ausente',
    # "invasão angiolinfática: não evidenciada"
    r'invasao\s*angiolinfatica\s*:\s*nao\s+(evidenciad|observad)',
    r'invasao\s*angiolinfatica\s+nao\s+(evidenciad|observad)',
    # "invasão angiolinfática: ausente"
    r'invasao\s*angiolinfatica\s*:\s*ausente',
    # "sem invasão linfovascular"
    r'sem\s+invasao\s*linfo[\s-]*vascular',
    # "ausência de invasão linfovascular"
    r'ausencia\s+de\s+invasao\s*linfo[\s-]*vascular',
    # "invasão vascular linfática: não evidenciado"
    r'invasao\s*vascular\s*linfatica\s*:\s*nao\s+(evidenciad|observad)',
    r'invasao\s*vascular\s*linfatica\s*:\s*ausente',
    # Structured with "nao": "invasão linfo-vascular: nao"
    r'invasao\s*linfo[\s-]*vascular\s*:\s*nao\b',
    r'invasao\s*linfovascular\s*:\s*nao\b',
    # Combined negation: "invasão angiolinfática e perineural: não evidenciadas"
    r'invasao\s*angiolinfatica\s+e\s+perineural\s*:\s*nao\s+(evidenciad|observad|evidencia)',
    r'invasao\s*angiolinfatica\s+e\s+perineural\s+nao\s+(evidenciad|observad|evidencia)',
    # "invasão linfo-vascular não observada" (without colon)
    r'invasao\s*linfo[\s-]*vascular\s+nao\s+(observad|evidenciad)',
    r'invasao\s*linfo[\s-]*vascular\s+ausente',
    # P1: "infiltração angiolinfática" (synonym of "invasão")
    r'infiltracao\s*angiolinfatica\s*:\s*nao\s+(evidenciad|observad)',
    r'infiltracao\s*angiolinfatica\s+e\s+perineural\s*:\s*nao\s+(evidenciad|observad|evidencia)',
    r'infiltracao\s*angiolinfatica\s+nao\s+(evidenciad|observad)',
    r'infiltracao\s*angiolinfatica\s*:\s*ausente',
    r'infiltracao\s*linfo[\s-]*vascular\s*:\s*nao\s+(evidenciad|observad)',
    r'infiltracao\s*linfo[\s-]*vascular\s*:\s*ausente',
]

PERIN_POS = [
    r'invasao\s*perineural\s*:\s*presente',
    r'invasao\s*perineural\s+presente',
    r'invasao\s*perineural\s+e\s+neural\s*:\s*presente',
    r'invasao\s*perineural\s+e\s+neural\s+presente',
    r'presenca\s+de\s+invasao\s*perineural',
    r'invasao\s*perineural\s*:\s*sim',
    # Combined: "invasão angiolinfática e perineural: presente"
    r'invasao\s*angiolinfatica\s+e\s+perineural\s*:\s*presente',
    r'invasao\s*angiolinfatica\s+e\s+perineural\s+presente',
    # P1: "infiltração" synonym
    r'infiltracao\s*perineural\s*:\s*presente',
    r'infiltracao\s*angiolinfatica\s+e\s+perineural\s*:\s*presente',
    r'infiltracao\s*perineural\s+e\s+neural\s*:\s*presente',
]

PERIN_NEG = [
    r'invasao\s*perineural\s*:\s*nao\s+(observad|evidenciad|identificad|detectad)',
    r'invasao\s*perineural\s*:\s*ausente',
    r'invasao\s*perineural\s+e\s+neural\s*:\s*ausente',
    r'invasao\s*perineural\s+e\s+neural\s*:\s*nao\s+(observad|evidenciad)',
    r'invasao\s*perineural\s*:\s*nao\b',
    r'sem\s+invasao\s*perineural',
    r'ausencia\s+de\s+invasao\s*perineural',
    # Combined negation
    r'invasao\s*angiolinfatica\s+e\s+perineural\s*:\s*nao\s+(evidenciad|observad|evidencia)',
    r'invasao\s*angiolinfatica\s+e\s+perineural\s+nao\s+(evidenciad|observad|evidencia)',
    # Without colon
    r'invasao\s*perineural\s+nao\s+(observad|evidenciad)',
    r'invasao\s*perineural\s+ausente',
    # P1: "infiltração" synonym
    r'infiltracao\s*perineural\s*:\s*nao\s+(evidenciad|observad)',
    r'infiltracao\s*angiolinfatica\s+e\s+perineural\s*:\s*nao\s+(evidenciad|observad|evidencia)',
    r'infiltracao\s*perineural\s*:\s*ausente',
    r'infiltracao\s*perineural\s+e\s+neural\s*:\s*nao\s+(evidenciad|observad)',
]


# ─── Detector ────────────────────────────────────────────────────────────

def detect_invasion(text: str, pos_patterns: list, neg_patterns: list) -> dict:
    """
    Detect binary invasion variable (sim/nao/null).

    Returns dict with:
        value: "sim" | "nao" | null
        status: "detected" | "not_mentioned"
        certainty: "high" | "medium"
        evidence: matched text span
    """
    if not text:
        return {"value": None, "status": "not_mentioned", "certainty": None, "evidence": None}

    norm_text = normalize(text)

    # Check NEGATION first (critical: avoids false positives)
    for pat in neg_patterns:
        m = re.search(pat, norm_text)
        if m:
            return {
                "value": "nao",
                "status": "detected",
                "certainty": "high",
                "evidence": m.group()
            }

    # Then check positive
    for pat in pos_patterns:
        m = re.search(pat, norm_text)
        if m:
            return {
                "value": "sim",
                "status": "detected",
                "certainty": "high",
                "evidence": m.group()
            }

    # Not mentioned
    return {"value": None, "status": "not_mentioned", "certainty": None, "evidence": None}


def detect_inv_linfov(text: str) -> dict:
    return detect_invasion(text, LINFOV_POS, LINFOV_NEG)

def detect_inv_perin(text: str) -> dict:
    return detect_invasion(text, PERIN_POS, PERIN_NEG)


# ─── Smoke tests ─────────────────────────────────────────────────────────

SMOKE_TESTS = [
    # inv_linfov positive
    ("INVASÃO LINFO-VASCULAR: PRESENTE.", "linfov", "sim"),
    ("Invasão linfovascular: presente.", "linfov", "sim"),
    ("INVASÃO ANGIOLINFÁTICA: PRESENTE", "linfov", "sim"),
    ("invasão vascular linfática: presente", "linfov", "sim"),
    ("Invasão angiolinfática e perineural: presente", "linfov", "sim"),

    # inv_linfov negative
    ("INVASÃO LINFO-VASCULAR: NÃO OBSERVADA.", "linfov", "nao"),
    ("Invasão linfovascular: não observada.", "linfov", "nao"),
    ("INVASÃO LINFO-VASCULAR: NÃO EVIDENCIADA.", "linfov", "nao"),
    ("invasão angiolinfática: não evidenciada", "linfov", "nao"),
    ("invasão angiolinfática e perineural: não evidenciadas", "linfov", "nao"),
    ("Invasão linfo-vascular: ausente", "linfov", "nao"),
    ("sem invasão linfovascular", "linfov", "nao"),
    ("INVASÃO LINFO VASCULAR: AUSENTE", "linfov", "nao"),
    ("invasão angiolinfática não evidenciada", "linfov", "nao"),

    # inv_linfov not mentioned
    ("EXAME NORMAL. SEM ATIPIAS.", "linfov", None),
    ("Fibroadenoma.", "linfov", None),
    ("CARCINOMA DUCTAL INVASIVO.", "linfov", None),

    # inv_perin positive
    ("INVASÃO PERINEURAL: PRESENTE.", "perin", "sim"),
    ("invasão perineural e neural: presente", "perin", "sim"),
    ("Invasão angiolinfática e perineural: presente", "perin", "sim"),

    # inv_perin negative
    ("INVASÃO PERINEURAL: NÃO OBSERVADA.", "perin", "nao"),
    ("invasão perineural e neural: não evidenciadas", "perin", "nao"),
    ("invasão perineural: ausente", "perin", "nao"),
    ("invasão angiolinfática e perineural: não evidenciadas", "perin", "nao"),
    ("sem invasão perineural", "perin", "nao"),
    ("INVASÃO PERINEURAL: NÃO EVIDENCIADA.", "perin", "nao"),

    # inv_perin not mentioned
    ("EXAME NORMAL.", "perin", None),
    ("Fibroadenoma.", "perin", None),

    # Edge cases: negation must beat positive
    ("INVASÃO LINFO-VASCULAR: NÃO OBSERVADA. INVASÃO PERINEURAL: PRESENTE.", "linfov", "nao"),
    ("INVASÃO LINFO-VASCULAR: NÃO OBSERVADA. INVASÃO PERINEURAL: PRESENTE.", "perin", "sim"),

    # Combined mention with split values
    ("invasão vascular sanguínea: não evidenciado; invasão vascular linfática: presente", "linfov", "sim"),

    # P1: "infiltração" as synonym of "invasão"
    ("INFILTRAÇÃO ANGIOLINFÁTICA E PERINEURAL: NÃO EVIDENCIADA", "linfov", "nao"),
    ("INFILTRAÇÃO ANGIOLINFÁTICA E PERINEURAL: NÃO EVIDENCIADA", "perin", "nao"),
    ("infiltração angiolinfática: presente", "linfov", "sim"),
    ("infiltração perineural: presente", "perin", "sim"),
    ("infiltração linfo-vascular: não observada", "linfov", "nao"),

    # P2: typo "evidencias" instead of "evidenciadas"
    ("INVASÃO ANGIOLINFÁTICA E PERINEURAL: NÃO EVIDENCIAS", "linfov", "nao"),
    ("INVASÃO ANGIOLINFÁTICA E PERINEURAL: NÃO EVIDENCIAS", "perin", "nao"),
]


def run_smoke_tests():
    """Run all smoke tests and report results."""
    passed = 0
    failed = 0

    for text, var, expected in SMOKE_TESTS:
        if var == "linfov":
            result = detect_inv_linfov(text)
        else:
            result = detect_inv_perin(text)

        actual = result["value"]

        if actual == expected:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL [{var}]: expected={expected}, got={actual}")
            print(f"        text: \"{text[:80]}...\"")
            print(f"        evidence: {result['evidence']}")

    total = passed + failed
    print(f"\nSmoke tests: {passed}/{total} passed ({100*passed/total:.0f}%)")
    if failed == 0:
        print("ALL PASSED")
    return failed == 0


# ─── Corpus runner ───────────────────────────────────────────────────────

def run_corpus(csv_path: str, output_path: str):
    """Run L1 on a CSV corpus with 'conclusao' column."""
    results = []

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            num = row.get('numExame', '')
            texto = row.get('conclusao', '')

            linfov = detect_inv_linfov(texto)
            perin = detect_inv_perin(texto)

            results.append({
                'numExame': num,
                'l1_inv_linfov': linfov['value'],
                'l1_inv_linfov_status': linfov['status'],
                'l1_inv_linfov_evidence': linfov['evidence'],
                'l1_inv_perin': perin['value'],
                'l1_inv_perin_status': perin['status'],
                'l1_inv_perin_evidence': perin['evidence'],
            })

    # Write results
    if results:
        fieldnames = list(results[0].keys())
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)

    print(f"Processed {len(results)} records -> {output_path}")
    return results


# ─── Main ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== L1 Detector: inv_linfov + inv_perin (Mama AP) ===\n")

    # Run smoke tests
    print("Running smoke tests...")
    all_pass = run_smoke_tests()

    if not all_pass:
        print("\nSmoke tests FAILED. Fix before running corpus.")
        sys.exit(1)

    # If corpus path provided, run on corpus
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
        output_path = sys.argv[2] if len(sys.argv) > 2 else "mama_ap_inv_l1_results.csv"
        print(f"\nRunning on corpus: {csv_path}")
        run_corpus(csv_path, output_path)
