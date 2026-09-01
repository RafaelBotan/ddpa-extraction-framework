"""Canary tests for mama_ihq_l1_v4 — DDPA contract mama_ihq_megabase_v2.yaml."""
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mama_ihq_l1_v4 import (
    normalize,
    detect_re_status, detect_re_pct, detect_re_allred,
    detect_rp_status, detect_rp_pct, detect_rp_allred,
    detect_her2_score, detect_her2_status_standalone,
    detect_ki67_pct, detect_tipo_hist_ihq,
    detect_subtipo_molecular_standalone,
)


# ============================================================================
# RE STATUS
# ============================================================================

class TestREStatus:
    def test_canary_table_pos(self):
        t = normalize("RE (6F11): Positivo em 100% das celulas (Escore de Allred: 8)")
        v, _ = detect_re_status(t)
        assert v == "positivo"

    def test_canary_table_neg(self):
        t = normalize("RE (6F11): Negativo")
        v, _ = detect_re_status(t)
        assert v == "negativo"

    def test_canary_narrative_pos(self):
        t = normalize("CARCINOMA DUCTAL INFILTRANTE, RECEPTOR DE ESTROGENO-POSITIVO")
        v, _ = detect_re_status(t)
        assert v == "positivo"

    def test_canary_triplo_neg(self):
        t = normalize("CARCINOMA DUCTAL INFILTRANTE, TRIPLO-NEGATIVO")
        v, _ = detect_re_status(t)
        assert v == "negativo"

    def test_no_match_returns_none(self):
        t = normalize("EXAME NORMAL SEM ALTERACOES")
        v, _ = detect_re_status(t)
        assert v is None


# ============================================================================
# RE PCT
# ============================================================================

class TestREPct:
    def test_canary_90pct(self):
        t = normalize("RE (6F11): Positivo em 90% das celulas (Escore de Allred: 7)")
        v, _ = detect_re_pct(t)
        assert v == 90

    def test_no_pct_returns_none(self):
        t = normalize("RECEPTOR DE ESTROGENO-POSITIVO")
        v, _ = detect_re_pct(t)
        assert v is None


# ============================================================================
# RE ALLRED
# ============================================================================

class TestREAllred:
    def test_canary_allred_7(self):
        t = normalize("RE (6F11): Positivo em 90% das celulas (Escore de Allred: 7)")
        v, _ = detect_re_allred(t)
        assert v == 7


# ============================================================================
# RP STATUS
# ============================================================================

class TestRPStatus:
    def test_canary_table_pos(self):
        t = normalize("RP (16): Positivo em 60% das celulas (Escore de Allred: 7)")
        v, _ = detect_rp_status(t)
        assert v == "positivo"

    def test_canary_narrative_neg(self):
        t = normalize("RECEPTOR DE PROGESTERONA-NEGATIVO")
        v, _ = detect_rp_status(t)
        assert v == "negativo"


# ============================================================================
# RP PCT
# ============================================================================

class TestRPPct:
    def test_canary_60pct(self):
        t = normalize("RP (16): Positivo em 60% das celulas")
        v, _ = detect_rp_pct(t)
        assert v == 60


# ============================================================================
# HER2 SCORE
# ============================================================================

class TestHER2Score:
    def test_canary_3plus(self):
        t = normalize("HER2 / c-erb B-2 (SP3): Escore 3+ (Positivo)")
        v, _ = detect_her2_score(t)
        assert v == "3+"

    def test_canary_2plus(self):
        t = normalize("HER2 / c-erb B-2 (SP3): Escore 2+ (Equivoco)")
        v, _ = detect_her2_score(t)
        assert v == "2+"

    def test_canary_0(self):
        t = normalize("HER2 / c-erb B-2 (SP3): Escore 0 (Negativo)")
        v, _ = detect_her2_score(t)
        assert v == "0"

    def test_cerb_alone_with_escore_paren(self):
        t = normalize("c-erbB-2 (SP3): Negativo (escore 0)")
        v, _ = detect_her2_score(t)
        assert v == "0"

    def test_her2_status_bare_digit(self):
        t = normalize("HER-2 (SP3): Negativo (0)")
        v, _ = detect_her2_score(t)
        assert v == "0"

    def test_ocr_letter_o_as_zero(self):
        t = normalize('HER2 / c-erb B-2 (SP3): Escore "O" (Negativo)')
        v, _ = detect_her2_score(t)
        assert v == "0"

    def test_reference_footer_not_captured(self):
        t = normalize(
            "Notas: (1) Quantificacao do RE, RP e HER-2 conforme ASCO/CAP. "
            "(2) Em casos de resultados duvidosos (Escore 2+) para o Produto "
            "do Oncogene c-erbB-2(HER2) e recomendado FISH."
        )
        v, _ = detect_her2_score(t)
        assert v is None


# ============================================================================
# HER2 STATUS (standalone wrapper)
# ============================================================================

class TestHER2Status:
    def test_canary_pos_from_score(self):
        t = normalize("HER2 / c-erb B-2 (SP3): Escore 3+ (Positivo)")
        v, _ = detect_her2_status_standalone(t)
        assert v == "positivo"

    def test_canary_equivoco(self):
        t = normalize("HER2 / c-erb B-2 (SP3): Escore 2+ (Equivoco)")
        v, _ = detect_her2_status_standalone(t)
        assert v == "equivoco"

    def test_canary_neg_narrative(self):
        t = normalize("COM EXPRESSAO NEGATIVA DOS PRODUTOS DO HER2")
        v, _ = detect_her2_status_standalone(t)
        assert v == "negativo"


# ============================================================================
# KI67 PCT
# ============================================================================

class TestKi67Pct:
    def test_canary_tabular(self):
        t = normalize("Ki-67 / MIB-1 (MM1): Positivo em 5% das celulas neoplasicas.")
        v, _ = detect_ki67_pct(t)
        assert v == 5

    def test_canary_narrative(self):
        t = normalize("INDICE DE PROLIFERACAO CELULAR DE 90% AO KI67")
        v, _ = detect_ki67_pct(t)
        assert v == 90


# ============================================================================
# TIPO HISTOLOGICO
# ============================================================================

class TestTipoHist:
    def test_canary_cdi(self):
        t = normalize("CARCINOMA DUCTAL INFILTRANTE DE MAMA ESQUERDA, GRAU II")
        v, _ = detect_tipo_hist_ihq(t)
        assert v == "CDI_SOE"

    def test_canary_cli(self):
        t = normalize("CARCINOMA LOBULAR INVASIVO DE MAMA DIREITA")
        v, _ = detect_tipo_hist_ihq(t)
        assert v == "CLI"

    def test_canary_cdis(self):
        t = normalize("CARCINOMA DUCTAL IN SITU DE MAMA")
        v, _ = detect_tipo_hist_ihq(t)
        assert v == "CDIS"


# ============================================================================
# SUBTIPO MOLECULAR (standalone wrapper)
# ============================================================================

class TestSubtipoMolecular:
    def test_canary_luminal_a(self):
        t = normalize(
            "RE (6F11): Positivo em 90%\n"
            "RP (16): Positivo em 60%\n"
            "HER2 / c-erb B-2 (SP3): Escore 0 (Negativo)\n"
            "Ki-67 / MIB-1 (MM1): Positivo em 5% das celulas neoplasicas."
        )
        v, _ = detect_subtipo_molecular_standalone(t)
        assert v == "luminal_a"

    def test_canary_triplo_negativo(self):
        t = normalize("CARCINOMA DUCTAL INFILTRANTE, TRIPLO-NEGATIVO")
        v, _ = detect_subtipo_molecular_standalone(t)
        assert v == "triplo_negativo"

    def test_canary_luminal_b_her2pos(self):
        t = normalize(
            "RE (6F11): Positivo em 80%\n"
            "RP (16): Positivo em 40%\n"
            "HER2 / c-erb B-2 (SP3): Escore 3+ (Positivo)\n"
            "Ki-67 / MIB-1 (MM1): Positivo em 35%"
        )
        v, _ = detect_subtipo_molecular_standalone(t)
        assert v == "luminal_b_her2pos"

    def test_canary_her2_enriched(self):
        t = normalize(
            "RE (6F11): Negativo\n"
            "RP (16): Negativo\n"
            "HER2 / c-erb B-2 (SP3): Escore 3+ (Positivo)\n"
            "Ki-67 / MIB-1 (MM1): Positivo em 60%"
        )
        v, _ = detect_subtipo_molecular_standalone(t)
        assert v == "her2_enriched"

    def test_non_mama_returns_none(self):
        t = normalize("TIREOIDE, CATEGORIA II DE BETHESDA")
        v, _ = detect_subtipo_molecular_standalone(t)
        assert v is None
