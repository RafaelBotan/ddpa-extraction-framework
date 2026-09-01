"""Tests for runtime.family_metrics (canon DDPA v5.3)."""

from __future__ import annotations

import math

import pytest

from runtime.family_metrics import (
    matrix_abcg,
    singleton_semantic,
)


def test_perfect_prediction_agreement_one():
    m = singleton_semantic(
        predictions=["MALIGNO", "BENIGNO", "BORDERLINE"],
        references=["MALIGNO", "BENIGNO", "BORDERLINE"],
    )
    assert m.overall_agreement == 1.0
    assert m.serious_error_rate == 0.0


def test_ben_to_mal_is_serious_error():
    m = singleton_semantic(
        predictions=["MALIGNO", "BENIGNO", "MALIGNO"],
        references=["MALIGNO", "BENIGNO", "BENIGNO"],
    )
    assert m.overall_agreement == pytest.approx(2 / 3)
    assert m.serious_error_rate == pytest.approx(1 / 3)


def test_mal_to_ben_is_serious_error():
    m = singleton_semantic(
        predictions=["BENIGNO"],
        references=["MALIGNO"],
    )
    assert m.serious_error_rate == 1.0


def test_indet_resolved_artificially_is_serious():
    m = singleton_semantic(
        predictions=["MALIGNO", "BENIGNO"],
        references=["INDETERMINADO", "INDETERMINADO"],
    )
    assert m.serious_error_rate == 1.0
    assert m.overall_agreement == 0.0


def test_borderline_missed_not_counted_as_serious():
    m = singleton_semantic(
        predictions=["MALIGNO"],
        references=["BORDERLINE"],
    )
    assert m.serious_error_rate == 0.0
    assert m.overall_agreement == 0.0


def test_class_recall_computed_per_critical_class():
    m = singleton_semantic(
        predictions=[
            "MALIGNO", "MALIGNO", "BORDERLINE",
            "BORDERLINE", "INDETERMINADO", "BENIGNO",
        ],
        references=[
            "MALIGNO", "BORDERLINE", "BORDERLINE",
            "MALIGNO", "INDETERMINADO", "BENIGNO",
        ],
    )
    assert m.class_recall["MALIGNO"] == pytest.approx(1 / 2)
    assert m.class_recall["BORDERLINE"] == pytest.approx(1 / 2)
    assert m.class_recall["INDETERMINADO"] == 1.0
    assert m.class_support["MALIGNO"] == 2
    assert m.class_support["BORDERLINE"] == 2
    assert m.class_support["INDETERMINADO"] == 1


def test_evidence_support_rate_literal_match():
    m = singleton_semantic(
        predictions=["MALIGNO"],
        references=["MALIGNO"],
        evidences=["carcinoma ductal invasivo"],
        source_texts=["Laudo com diagnóstico de CARCINOMA DUCTAL INVASIVO grau 2"],
    )
    assert m.evidence_support_rate == 1.0


def test_evidence_support_rate_missing():
    m = singleton_semantic(
        predictions=["MALIGNO"],
        references=["MALIGNO"],
        evidences=[""],
        source_texts=["laudo"],
    )
    assert m.evidence_support_rate == 0.0


def test_evidence_support_rate_not_in_source_fails():
    m = singleton_semantic(
        predictions=["MALIGNO"],
        references=["MALIGNO"],
        evidences=["carcinoma micropapilar invasivo"],
        source_texts=["texto que não contém a frase"],
    )
    assert m.evidence_support_rate == 0.0


def test_evidence_support_rate_nan_without_sources():
    m = singleton_semantic(
        predictions=["MALIGNO"], references=["MALIGNO"],
    )
    assert math.isnan(m.evidence_support_rate)


def test_confusion_matrix_counts():
    m = singleton_semantic(
        predictions=["MALIGNO", "BENIGNO", "MALIGNO"],
        references=["MALIGNO", "MALIGNO", "BENIGNO"],
    )
    assert m.confusion["MALIGNO"] == {"MALIGNO": 1, "BENIGNO": 1}
    assert m.confusion["BENIGNO"] == {"MALIGNO": 1}


def test_normalization_case_insensitive_and_stripped():
    m = singleton_semantic(
        predictions=["  maligno  ", "Benigno"],
        references=["MALIGNO", "BENIGNO"],
    )
    assert m.overall_agreement == 1.0


def test_len_mismatch_raises():
    with pytest.raises(ValueError):
        singleton_semantic(predictions=["MAL"], references=["MAL", "BEN"])


def test_matrix_abcg_detects_tautology_note():
    gold = ["MALIGNO", "BENIGNO"]
    pred_a = ["MALIGNO", "BENIGNO"]
    m = matrix_abcg(gold=gold, prediction_a=pred_a)
    assert m.agreement_A_vs_G == 1.0
    assert any("tautologia" in n.lower() for n in m.notes)


def test_matrix_abcg_with_B_baseline():
    gold = ["MALIGNO", "BENIGNO", "BORDERLINE", "MALIGNO"]
    pred_a = ["MALIGNO", "BENIGNO", "BORDERLINE", "MALIGNO"]
    pred_b = ["MALIGNO", "MALIGNO", "BORDERLINE", "BENIGNO"]
    m = matrix_abcg(gold=gold, prediction_a=pred_a, prediction_b=pred_b)
    assert m.agreement_A_vs_G == 1.0
    assert m.agreement_B_vs_G == 0.5
    assert m.delta_A_minus_B == pytest.approx(0.5)
    assert m.serious_error_rate_B == pytest.approx(2 / 4)


def test_matrix_abcg_without_B_has_none_fields():
    m = matrix_abcg(gold=["MALIGNO"], prediction_a=["MALIGNO"])
    assert m.agreement_B_vs_G is None
    assert m.delta_A_minus_B is None
    assert m.agreement_C_vs_G is None


def test_matrix_abcg_with_cascade_C():
    gold = ["MALIGNO", "BENIGNO"]
    pred_a = ["MALIGNO", "MALIGNO"]
    pred_c = ["MALIGNO", "BENIGNO"]
    m = matrix_abcg(gold=gold, prediction_a=pred_a, prediction_c=pred_c)
    assert m.agreement_A_vs_G == 0.5
    assert m.agreement_C_vs_G == 1.0


def test_matrix_abcg_len_mismatch_raises():
    with pytest.raises(ValueError):
        matrix_abcg(gold=["MAL"], prediction_a=["MAL", "BEN"])
