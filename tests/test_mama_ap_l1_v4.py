"""Pytest discovery wrapper for mama_ap_multi_l1_v4 — DDPA contract mama_ap_megabase_v1.yaml.

This file exposes the 247 inline smoke tests living in `run_smoke_tests()` of
the mama_ap_multi_l1_v4 module as pytest-collectable test functions. It does
NOT modify the module itself (which is mature, status AT_OPERATIONAL_EXHAUSTION
Level 1, 247/247 inline smoke tests passing).

Two layers of testing here:
  1. A canonical subset of canary tests (mirrors the contract canaries) — used
     by the framework gate runner (`pytest --collect-only`) to confirm L1
     interface stability.
  2. A meta test `test_run_full_smoke_suite` that invokes the module's
     `run_smoke_tests()` to ensure all 247 inline checks still pass.

Both layers are CPU-only and DO NOT call any LLM API. They are safe to run in
CI/gate context.
"""
from __future__ import annotations

import os
import sys

import pytest

# Make the piloto root importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mama_ap_multi_l1_v4 import (  # noqa: E402
    normalize,
    detect_tam_cm,
    detect_comp_insitu,
    detect_procedimento,
    detect_tipo_hist,
    detect_margens,
    detect_pT,
    detect_pN,
    detect_ln,
    detect_grau_nott,
    detect_nottingham_components,
    derive_behavior_class,
    derive_invasive_malignancy,
    derive_malignancy_any,
    detect_inv_linfov,
    detect_inv_perin,
    detect_necrose_tumoral,
    detect_multifocalidade,
    detect_microcalcificacoes,
    detect_pele,
    detect_mamilo,
    detect_efeito_terapeutico,
    detect_infiltrado_linfocitico,
    detect_extensao_extranodal,
    detect_ref_ihq,
    run_smoke_tests,
)


# ============================================================================
# TAM_CM
# ============================================================================

class TestTamCm:
    def test_canary_extensao_mm(self):
        v, _ = detect_tam_cm(normalize("MAIOR EXTENSÃO DA NEOPLASIA NESTA AMOSTRA: 8,0 mm."))
        assert v == "8,0 mm"

    def test_canary_tamanho_invasiva(self):
        v, _ = detect_tam_cm(normalize("TAMANHO DA NEOPLASIA INVASIVA: 2,2 X 1,5 X 1,5 CM;"))
        assert v == "2,2 x 1,5 x 1,5 cm"

    def test_canary_tamanho_tumor(self):
        v, _ = detect_tam_cm(normalize("TAMANHO DO TUMOR: 1,5 X 1,3 X 1,0 CM;"))
        assert v == "1,5 x 1,3 x 1,0 cm"

    def test_canary_mm_int(self):
        v, _ = detect_tam_cm(normalize("TAMANHO DA NEOPLASIA INVASIVA: 2 mm (MAIOR DIÂMETRO)"))
        assert v == "2 mm"

    def test_no_match_benign(self):
        v, _ = detect_tam_cm(normalize("FIBROADENOMA SEM ATIPIAS"))
        assert v is None


# ============================================================================
# COMP_INSITU
# ============================================================================

class TestCompInsitu:
    def test_canary_neg(self):
        v, _ = detect_comp_insitu(normalize('COMPONENTE "IN SITU": NÃO OBSERVADO.'))
        assert v == "nao"

    def test_canary_pos(self):
        v, _ = detect_comp_insitu(normalize('COMPONENTE "IN SITU": PRESENTE, ALTO GRAU NUCLEAR'))
        assert v == "sim"

    def test_canary_cdis_abbrev(self):
        v, _ = detect_comp_insitu(normalize("REPRESENTADO POR CDIS, PADRÃO SÓLIDO"))
        assert v == "sim"

    def test_canary_ausencia_residual(self):
        v, _ = detect_comp_insitu(normalize("AUSÊNCIA DE CARCINOMA DUCTAL IN SITU RESIDUAL."))
        assert v == "nao"

    def test_multi_section_pos_wins(self):
        t = normalize('COMPONENTE "IN SITU": NÃO OBSERVADO. XXX COMPONENTE "IN SITU": PRESENTE, GRAU NUCLEAR INTERMEDIÁRIO')
        v, _ = detect_comp_insitu(t)
        assert v == "sim"


# ============================================================================
# PROCEDIMENTO
# ============================================================================

class TestProcedimento:
    def test_canary_mast_radical(self):
        v, _ = detect_procedimento(normalize("PROCEDIMENTO CIRÚRGICO: MASTECTOMIA RADICAL À DIREITA"))
        assert v == "mastectomia_radical"

    def test_canary_biopsia_agulha(self):
        v, _ = detect_procedimento(normalize("BIÓPSIA POR AGULHA DE MAMA ESQUERDA:"))
        assert v == "biopsia_agulha"

    def test_canary_mamotomia(self):
        v, _ = detect_procedimento(normalize("PRODUTO DE MAMOTOMIA, APRESENTANDO:"))
        assert v == "mamotomia"

    def test_canary_setorectomia(self):
        v, _ = detect_procedimento(normalize("PROCEDIMENTO CIRÚRGICO: SETORECTOMIA;"))
        assert v == "excisional"

    def test_canary_excisional(self):
        v, _ = detect_procedimento(normalize("PROCEDIMENTO CIRÚRGICO: BIÓPSIA EXCISIONAL;"))
        assert v == "excisional"


# ============================================================================
# TIPO_HIST
# ============================================================================

class TestTipoHist:
    def test_canary_cdi_nst(self):
        v, _ = detect_tipo_hist(normalize("CARCINOMA DUCTAL INVASIVO, DO TIPO NÃO ESPECIAL (OMS 2019)"))
        assert v == "CDI_SOE"

    def test_canary_cli(self):
        v, _ = detect_tipo_hist(normalize("CARCINOMA LOBULAR INVASIVO, VARIANTE PLEOMÓRFICA"))
        assert v == "CLI"

    def test_canary_micropapilar(self):
        v, _ = detect_tipo_hist(normalize("TIPO CARCINOMA MICROPAPILAR INVASIVO"))
        assert v == "micropapilar"

    def test_canary_mucinoso(self):
        v, _ = detect_tipo_hist(normalize("CARCINOMA MAMÁRIO DUCTAL INVASIVO COM DIFERENCIAÇÃO MUCINOSA"))
        assert v == "mucinoso"

    def test_canary_fibroadenoma(self):
        v, _ = detect_tipo_hist(normalize("FIBROADENOMA SEM ATIPIAS"))
        assert v == "fibroadenoma"

    def test_canary_cdis_only(self):
        v, _ = detect_tipo_hist(normalize("CARCINOMA DUCTAL IN SITU DE MAMA ESQUERDA"))
        assert v == "CDIS"

    def test_canary_fibrocistico_pattern(self):
        v, _ = detect_tipo_hist(normalize("TECIDO MAMÁRIO APRESENTANDO: CISTOS; ADENOSE; METAPLASIA APÓCRINA; FIBROSE ESTROMAL"))
        assert v == "fibrocistico"

    def test_canary_misto(self):
        v, _ = detect_tipo_hist(normalize("CARCINOMA INVASIVO COM PADRÃO MISTO TIPO DUCTAL E LOBULAR"))
        assert v == "misto"

    def test_canary_papilar_encapsulado(self):
        v, _ = detect_tipo_hist(normalize("CARCINOMA PAPILAR ENCAPSULADO INVASIVO DE MAMA ESQUERDA"))
        assert v == "papilar"

    def test_canary_adenocarcinoma_genérico(self):
        v, _ = detect_tipo_hist(normalize("ADENOCARCINOMA POUCO DIFERENCIADO"))
        assert v == "outro_maligno"

    def test_canary_microinvasivo_to_cdi(self):
        v, _ = detect_tipo_hist(normalize("CARCINOMA DUCTAL MICROINVASIVO DE MAMA DIREITA"))
        assert v == "CDI_SOE"


# ============================================================================
# MARGENS
# ============================================================================

class TestMargens:
    def test_canary_livres(self):
        v, _ = detect_margens(normalize("MARGENS CIRÚRGICAS: LIVRES DE NEOPLASIA (FORAM AVALIADAS CINCO MARGENS)"))
        assert v == "livres"

    def test_canary_comprometida(self):
        v, _ = detect_margens(normalize("MARGEM CIRÚRGICA PROFUNDA FOCALMENTE COMPROMETIDA"))
        assert v == "comprometidas"

    def test_canary_exiguas_mais_proxima(self):
        v, _ = detect_margens(normalize("MARGENS CIRÚRGICAS LIVRES. A MARGEM CIRÚRGICA MAIS PRÓXIMA É A INFERIOR"))
        assert v == "exiguas"

    def test_canary_tangenciadas(self):
        v, _ = detect_margens(normalize("MARGENS CIRÚRGICAS TANGENCIADAS PELO FIBROADENOMA."))
        assert v == "exiguas"


# ============================================================================
# pT / pN
# ============================================================================

class TestPT:
    def test_canary_standard(self):
        v, _ = detect_pT(normalize("ESTADIAMENTO PATOLÓGICO: pT2; pN1a; pMx."))
        assert v == "pt2"

    def test_canary_neoadjuvant(self):
        v, _ = detect_pT(normalize("ESTADIAMENTO PATOLÓGICO: ypT2; ypN0;"))
        assert v == "ypt2"

    def test_canary_multifocal(self):
        v, _ = detect_pT(normalize("ESTADIAMENTO (AJCC 7ª ED): pT1b(m) (CLI) pN0 (LS)."))
        assert v == "pt1b(m)"

    def test_canary_microinvasion(self):
        v, _ = detect_pT(normalize("ESTADIAMENTO ANATÔMICO  (AJCC 8ª ed): pT1mi;  pN0 (sn)"))
        assert v == "pt1mi"

    def test_canary_inflammatory_pt4d(self):
        v, _ = detect_pT(normalize("ESTADIAMENTO: pT4d; pN3a"))
        assert v == "pt4d"


class TestPN:
    def test_canary_standard(self):
        v, _ = detect_pN(normalize("ESTADIAMENTO PATOLÓGICO: pT2; pN1a; pMx."))
        assert v == "pn1a"

    def test_canary_neoadjuvant(self):
        v, _ = detect_pN(normalize("ESTADIAMENTO PATOLÓGICO: ypT2; ypN0;"))
        assert v == "ypn0"

    def test_canary_ls_suffix(self):
        v, _ = detect_pN(normalize("ESTADIAMENTO (AJCC 7ª ED): pT1b(m) (CLI) pN0 (LS)."))
        assert v == "pn0 (ls)"

    def test_canary_x(self):
        v, _ = detect_pN(normalize("ESTADIAMENTO ANATÔMICO (AJCC 8ª ED): pT1a pNx Mx"))
        assert v == "pnx"


# ============================================================================
# LN_EXAM / LN_POS
# ============================================================================

class TestLN:
    def test_canary_exam_pos(self):
        ex, po, _ = detect_ln(normalize("LINFONODOS EXAMINADOS: 12. LINFONODOS COMPROMETIDOS: 3."))
        assert ex == 12 and po == 3

    def test_canary_isolados(self):
        ex, _, _ = detect_ln(normalize("LINFONODOS ISOLADOS: 8"))
        assert ex == 8

    def test_canary_macrometastases(self):
        _, po, _ = detect_ln(normalize("LINFONODOS ISOLADOS: 5 MACROMETÁSTASES (>2mm): 2"))
        assert po == 2


# ============================================================================
# GRAU_NOTT
# ============================================================================

class TestGrauNott:
    def test_canary_nott_ii(self):
        v, _ = detect_grau_nott(normalize("GRAU HISTOLÓGICO: GRAU II DE NOTTINGHAM"))
        assert v == "2"

    def test_canary_mbr_iii(self):
        v, _ = detect_grau_nott(normalize("GRAU III DE BLOOM RICHARDSON"))
        assert v == "3"

    def test_canary_novo_grau(self):
        v, _ = detect_grau_nott(normalize("NOVO GRAU HISTOLÓGICO FINAL: GRAU II DE NOTTINGHAM"))
        assert v == "2"

    def test_canary_score_decomposition(self):
        v, _ = detect_grau_nott(normalize("ESCORES: 3+3+2=8 / GRAU FINAL"))
        assert v == "3"


# ============================================================================
# NOTTINGHAM COMPONENTS
# ============================================================================

class TestNottinghamComponents:
    def test_canary_triple(self):
        tub, nuc, mit, _ = detect_nottingham_components(
            normalize("DIFERENCIAÇÃO TUBULAR: 3; GRAU NUCLEAR: 2; ÍNDICE MITÓTICO: 1")
        )
        assert (tub, nuc, mit) == ("3", "2", "1")

    def test_canary_descriptor(self):
        tub, _, _, _ = detect_nottingham_components(
            normalize("GRAU TUBULAR: INTERMEDIÁRIO, ESCORE 2")
        )
        assert tub == "2"

    def test_canary_paren_score(self):
        _, nuc, _, _ = detect_nottingham_components(
            normalize("GRAU NUCLEAR: ALTO GRAU (3)")
        )
        assert nuc == "3"


# ============================================================================
# DERIVED — behavior_class, invasive_malignancy, malignancy_any
# ============================================================================

class TestDerived:
    def test_behavior_class_invasive(self):
        assert derive_behavior_class("CDI_SOE") == "invasivo"

    def test_behavior_class_insitu(self):
        assert derive_behavior_class("CDIS") == "in_situ"

    def test_behavior_class_benign(self):
        assert derive_behavior_class("fibroadenoma") == "benigno"

    def test_behavior_class_none(self):
        assert derive_behavior_class(None) is None

    def test_invasive_malignancy_sim(self):
        assert derive_invasive_malignancy("CLI") == "sim"

    def test_invasive_malignancy_nao_for_cdis(self):
        assert derive_invasive_malignancy("CDIS") == "nao"

    def test_invasive_malignancy_nao_for_fa(self):
        assert derive_invasive_malignancy("fibroadenoma") == "nao"

    def test_malignancy_any_invasive(self):
        assert derive_malignancy_any("CDI_SOE") == "sim"

    def test_malignancy_any_insitu(self):
        assert derive_malignancy_any("CDIS") == "sim"

    def test_malignancy_any_benign(self):
        assert derive_malignancy_any("fibroadenoma") == "nao"


# ============================================================================
# INV_LINFOV / INV_PERIN
# ============================================================================

class TestInvLinfov:
    def test_canary_combined_neg(self):
        v, _ = detect_inv_linfov(normalize("INVASÃO ANGIOLINFÁTICA E PERINEURAL: NÃO EVIDENCIADA"))
        assert v == "nao"

    def test_canary_pos(self):
        v, _ = detect_inv_linfov(normalize("INVASÃO LINFO-VASCULAR PRESENTE"))
        assert v == "sim"

    def test_canary_embolos(self):
        # L1 pattern: r'embolos?\s+(?:neoplasicos?\s+)?(?:em\s+)?(?:linfaticos?|vasculares?)'
        # — accepts "embolos em linfaticos" / "embolos vasculares", not "vasos linfaticos"
        v, _ = detect_inv_linfov(normalize("ÊMBOLOS NEOPLÁSICOS EM LINFÁTICOS"))
        assert v == "sim"


class TestInvPerin:
    def test_canary_combined_neg(self):
        v, _ = detect_inv_perin(normalize("INVASÃO ANGIOLINFÁTICA E PERINEURAL: NÃO EVIDENCIADA"))
        assert v == "nao"

    def test_canary_pos(self):
        v, _ = detect_inv_perin(normalize("INVASÃO PERINEURAL: PRESENTE"))
        assert v == "sim"


# ============================================================================
# NECROSE_TUMORAL
# ============================================================================

class TestNecroseTumoral:
    def test_canary_neg(self):
        v, _ = detect_necrose_tumoral(normalize("NECROSE TUMORAL: NÃO OBSERVADA"))
        assert v == "nao"

    def test_canary_pos(self):
        v, _ = detect_necrose_tumoral(normalize("NECROSE TUMORAL: PRESENTE, FOCAL"))
        assert v == "sim"

    def test_excludes_adipose(self):
        v, _ = detect_necrose_tumoral(normalize("SEM NECROSE ADIPOSA"))
        # 'sem necrose' but explicitly adiposa — must NOT match
        assert v is None


# ============================================================================
# MULTIFOCALIDADE
# ============================================================================

class TestMultifocalidade:
    def test_canary_unifocal_neg(self):
        v, _ = detect_multifocalidade(normalize("FOCALIDADE: UNIFOCAL"))
        assert v == "nao"

    def test_canary_pos(self):
        v, _ = detect_multifocalidade(normalize("MULTIFOCALIDADE/MULTICENTRICIDADE: PRESENTE"))
        assert v == "sim"


# ============================================================================
# MICROCALCIFICACOES
# ============================================================================

class TestMicrocalcificacoes:
    def test_canary_neg(self):
        v, _ = detect_microcalcificacoes(normalize("MICROCALCIFICAÇÕES: NÃO OBSERVADAS"))
        assert v == "nao"

    def test_canary_pos(self):
        v, _ = detect_microcalcificacoes(normalize("MICROCALCIFICAÇÕES: PRESENTES"))
        assert v == "sim"


# ============================================================================
# PELE / MAMILO
# ============================================================================

class TestPele:
    def test_canary_livre(self):
        v, _ = detect_pele(normalize("PELE: LIVRE DE NEOPLASIA"))
        assert v == "nao"

    def test_canary_comprometida(self):
        v, _ = detect_pele(normalize("PELE COMPROMETIDA PELA NEOPLASIA"))
        assert v == "sim"

    def test_canary_derme(self):
        v, _ = detect_pele(normalize("DERME PROFUNDA COMPROMETIDA PELA NEOPLASIA"))
        assert v == "sim"


class TestMamilo:
    def test_canary_livre(self):
        v, _ = detect_mamilo(normalize("MAMILO E ARÉOLA: LIVRE DE NEOPLASIA"))
        assert v == "nao"

    def test_canary_comprometido(self):
        v, _ = detect_mamilo(normalize("MAMILO COMPROMETIDO PELA NEOPLASIA"))
        assert v == "sim"

    def test_canary_pele_mamilo_combined(self):
        v, _ = detect_mamilo(normalize("PELE E MAMILO: LIVRES"))
        assert v == "nao"


# ============================================================================
# EFEITO_TERAPEUTICO
# ============================================================================

class TestEfeitoTerapeutico:
    def test_canary_completa(self):
        v, _ = detect_efeito_terapeutico(normalize("EFEITO TERAPÊUTICO: COMPLETA, AUSÊNCIA DE NEOPLASIA RESIDUAL"))
        assert v == "completa"

    def test_canary_parcial(self):
        v, _ = detect_efeito_terapeutico(normalize("EFEITO TERAPÊUTICO: PARCIAL, COM CARCINOMA RESIDUAL"))
        assert v == "parcial"

    def test_canary_sem_info(self):
        v, _ = detect_efeito_terapeutico(normalize("EFEITO TERAPÊUTICO: AUSÊNCIA DE INFORMAÇÕES CLÍNICAS"))
        assert v == "sem_info"


# ============================================================================
# INFILTRADO_LINFOCITICO
# ============================================================================

class TestInfiltradoLinfocitico:
    def test_canary_escasso(self):
        v, _ = detect_infiltrado_linfocitico(normalize("INFILTRADO LINFOCÍTICO INTRATUMORAL: ESCASSO"))
        assert v == "escasso"

    def test_canary_moderado(self):
        v, _ = detect_infiltrado_linfocitico(normalize("INFILTRADO LINFOCÍTICO INTRATUMORAL: MODERADO"))
        assert v == "moderado"

    def test_canary_intenso(self):
        v, _ = detect_infiltrado_linfocitico(normalize("INFILTRADO LINFOCÍTICO INTRATUMORAL: INTENSO"))
        assert v == "intenso"


# ============================================================================
# EXTENSAO_EXTRANODAL
# ============================================================================

class TestExtensaoExtranodal:
    def test_canary_neg(self):
        v, _ = detect_extensao_extranodal(normalize("EXTENSÃO EXTRANODAL: NÃO OBSERVADA"))
        assert v == "nao"

    def test_canary_pos(self):
        v, _ = detect_extensao_extranodal(normalize("EXTENSÃO EXTRANODAL: PRESENTE"))
        assert v == "sim"

    def test_canary_rompimento(self):
        v, _ = detect_extensao_extranodal(normalize("ROMPIMENTO CAPSULAR PRESENTE"))
        assert v == "sim"


# ============================================================================
# REF_IHQ
# ============================================================================

class TestRefIhq:
    def test_canary_vide_complementar(self):
        v, _ = detect_ref_ihq(normalize("VIDE EXAME COMPLEMENTAR Nº 123456IH"))
        assert v == "sim"

    def test_canary_exame_imuno(self):
        v, _ = detect_ref_ihq(normalize("EXAME DE IMUNOHISTOQUÍMICA Nº 234567IH"))
        assert v == "sim"

    def test_canary_sugerimos(self):
        v, _ = detect_ref_ihq(normalize("SUGERIMOS COMPLEMENTAÇÃO POR IMUNOHISTOQUÍMICA"))
        assert v == "sim"


# ============================================================================
# META: full inline smoke suite
# ============================================================================
# Invokes the module's own run_smoke_tests(). It prints FAIL lines to stdout
# and returns a boolean. We capture that boolean to convert it into a single
# pytest assertion. This guards against silent regressions when someone edits
# the L1 module without also updating these wrapper tests.

@pytest.mark.smoke_full
def test_run_full_smoke_suite(capsys):
    """All 247 inline smoke tests in mama_ap_multi_l1_v4.py must pass."""
    ok = run_smoke_tests()
    captured = capsys.readouterr()
    # Surface any FAIL lines to the pytest output for diagnostics
    if not ok:
        fail_lines = [l for l in captured.out.splitlines() if l.strip().startswith("FAIL")]
        pytest.fail(
            "mama_ap_multi_l1_v4 inline smoke tests failed:\n"
            + "\n".join(fail_lines[:30])
            + (f"\n... ({len(fail_lines)-30} more)" if len(fail_lines) > 30 else "")
        )
