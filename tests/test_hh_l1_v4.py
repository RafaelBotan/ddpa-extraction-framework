"""Regression tests for hh_l1_v4 detector.

Cobre canarios do contrato e2_esofagite_full.yaml + buracos conhecidos
que apareceram em platform_test_D (2026-04-15).
"""
from __future__ import annotations

import pytest

from hh_l1_v4 import detect_hh


CANARIES = [
    ("HH-01", "ESOFAGO: hernia hiatal por deslizamento, aprox 2cm.", "sim"),
    ("HH-02", "CONCLUSAO: sem evidencia de hernia hiatal.", "nao"),
    ("HH-03", "ESOFAGO: JEC ao nivel do pincamento diafragmatico.", "nao"),
]


@pytest.mark.parametrize("canary_id,text,expected_value", CANARIES)
def test_canary(canary_id, text, expected_value):
    result = detect_hh(text)
    assert result.value == expected_value, (
        f"{canary_id}: expected value={expected_value!r}, "
        f"got value={result.value!r} status={result.status!r} evidence={result.evidence!r}"
    )


NEGATION_VARIANTS = [
    "sem evidencia de hernia hiatal",
    "sem evidencias de hernia hiatal",
    "sem sinais de hernia hiatal",
    "sem sinal de hernia de hiato",
    "sem evidencia de hernia de hiato",
]


@pytest.mark.parametrize("text", NEGATION_VARIANTS)
def test_negation_variants_return_nao(text):
    full_text = f"CONCLUSAO: {text}."
    result = detect_hh(full_text)
    assert result.value == "nao", (
        f"variant {text!r}: expected 'nao', got {result.value!r} "
        f"status={result.status!r}"
    )
