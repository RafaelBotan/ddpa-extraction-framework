"""Regression tests for la_grau_l1_v4 detector.

Cobre canarios do contrato e2_esofagite_full.yaml + buraco conhecido
que apareceu em platform_test_D (2026-04-15): "esofagite grau X" sem
"Los Angeles" explicito e sem "erosiva" no contexto proximo.
"""
from __future__ import annotations

import pytest

from la_grau_l1_v4 import detect_la_grau


CANARIES = [
    ("LA-01", "CONCLUSAO: esofagite erosiva grau A de Los Angeles.", "A"),
    ("LA-02", "CONCLUSAO: esofagite grau C.", "C"),
    ("LA-03", "CONCLUSAO: mucosa esofagica normal, sem esofagite.", "sem_esofagite"),
]


@pytest.mark.parametrize("canary_id,text,expected_letter", CANARIES)
def test_canary(canary_id, text, expected_letter):
    result = detect_la_grau(text)
    assert result.letter == expected_letter, (
        f"{canary_id}: expected letter={expected_letter!r}, "
        f"got letter={result.letter!r} status={result.status!r}"
    )


BARE_GRAU_CASES = [
    ("CONCLUSAO: esofagite grau A.", "A"),
    ("CONCLUSAO: esofagite grau B.", "B"),
    ("CONCLUSAO: esofagite grau C.", "C"),
    ("CONCLUSAO: esofagite grau D.", "D"),
]


@pytest.mark.parametrize("text,expected_letter", BARE_GRAU_CASES)
def test_bare_grau_resolved(text, expected_letter):
    """Esofagite grau X isolado (sem 'Los Angeles' nem 'erosiva') deve resolver."""
    result = detect_la_grau(text)
    assert result.letter == expected_letter, (
        f"text={text!r}: expected {expected_letter!r}, "
        f"got letter={result.letter!r} status={result.status!r}"
    )


HISTORICITY_SHOULD_STILL_WORK = [
    "CONCLUSAO: controle de esofagite grau B.",
    "INDICACAO: passado de esofagite grau C. CONCLUSAO: exame normal.",
]


@pytest.mark.parametrize("text", HISTORICITY_SHOULD_STILL_WORK)
def test_historicity_not_regressed(text):
    """Historicidade deve continuar bloqueando captura."""
    result = detect_la_grau(text)
    assert result.letter != "B" and result.letter != "C", (
        f"text={text!r}: historicity should block, got letter={result.letter!r} "
        f"status={result.status!r}"
    )
