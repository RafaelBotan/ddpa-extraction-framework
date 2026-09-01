"""Tests for runtime.document_profile (canon DDPA v5.3)."""

from __future__ import annotations

import pytest

from runtime.document_profile import (
    DocumentProfile,
    profile_document,
    LONG_REPORT_CHAR_THRESHOLD,
    LONG_MULTI_ENTITY_COUNT_THRESHOLD,
)


def test_empty_report_is_unknown():
    p = profile_document(texto_completo="")
    assert p.complexity.char_count == 0
    assert p.complexity.long_report is False
    assert p.complexity.long_multi_entity is False
    assert p.format.unknown is True


def test_short_narrative_report():
    texto = (
        "Amostra biopsia de nodulo mamario com aspecto carcinomatoso bem diferenciado "
        "mostrando proliferacao ductal com figuras atipicas e estroma desmoplasico. "
        "Avaliacao imuno-histoquimica revelou marcacao positiva para receptor de "
        "estrogenio em 80% das celulas neoplasicas com intensidade forte difusa."
    )
    p = profile_document(texto_completo=texto)
    assert p.complexity.char_count == len(texto)
    assert p.complexity.long_report is False
    assert p.complexity.estimated_entity_count == 0
    assert p.format.narrative_style is True
    assert p.format.cap_style is False


def test_long_report_flag():
    texto = "A" * (LONG_REPORT_CHAR_THRESHOLD + 100)
    p = profile_document(texto_completo=texto)
    assert p.complexity.long_report is True


def test_long_multi_entity_by_specimen_count():
    texto = "\n".join(f"Bloco {i} descrito" for i in range(1, 13))
    p = profile_document(texto_completo=texto)
    assert p.complexity.specimen_count == 12
    assert p.complexity.long_multi_entity is True


def test_long_multi_entity_by_entity_count():
    texto = "\n".join(f"Peca {i} - lesao observada" for i in range(1, 11))
    p = profile_document(texto_completo=texto)
    assert p.complexity.estimated_entity_count >= LONG_MULTI_ENTITY_COUNT_THRESHOLD
    assert p.complexity.long_multi_entity is True


def test_entity_count_below_threshold_not_multi():
    texto = "Peca 1 lesao.\nPeca 2 margem.\nPeca 3 linfonodo."
    p = profile_document(texto_completo=texto)
    assert p.complexity.estimated_entity_count == 3
    assert p.complexity.long_multi_entity is False


def test_cap_style_with_header_value_blocks():
    texto = """MATERIAL:
- peca de mastectomia direita

MARGENS:
- livre anterior
- comprometida posterior

GRAU HISTOLOGICO:
- grau 2 de Nottingham

TAMANHO:
- 2,5 cm no maior eixo
"""
    p = profile_document(texto_completo=texto)
    assert p.format.cap_style is True
    assert p.format.header_value_blocks is True
    assert p.format.narrative_style is False


def test_table_like_ihq_antibody_list():
    """Formato típico IHQ: lista de anticorpos com pipe separator."""
    texto = """AE1/AE3 (AE1/AE3) | Negativo
BER-EP4 (BER-EP4) | Negativo
CALRETININA (DAK) | Positivo focalmente
CD 34 (QBEnd/10) | Positivo em raras celulas
Ki67 (MIB-1) | Positivo em 5% das celulas"""
    p = profile_document(texto_completo=texto)
    assert p.format.table_like is True


def test_bullet_list_detected():
    texto = """Achados microscopicos:
- proliferacao ductal atipica
- figuras de mitose raras
- ausencia de necrose
- margens livres
- estroma denso
- ausencia de invasao vascular
"""
    p = profile_document(texto_completo=texto)
    assert p.format.bullet_list is True


def test_section_count_from_structured_fields():
    p = profile_document(
        material_especificado="biopsia",
        tecnica="",
        macroscopia="peca de 2cm",
        microscopia="carcinoma ductal invasivo",
        conclusao="CDI mama grau 2",
    )
    assert p.complexity.section_count == 4


def test_section_count_zero_when_all_empty():
    p = profile_document(
        material_especificado="", tecnica="", macroscopia="", microscopia="", conclusao=""
    )
    assert p.complexity.section_count == 0
    assert p.complexity.char_count == 0


def test_texto_completo_rebuilt_from_sections_if_missing():
    p = profile_document(
        texto_completo=None,
        material_especificado="nodulo mama",
        conclusao="CDI grau 2 de Nottingham",
    )
    assert p.complexity.char_count > 0
    assert p.complexity.section_count == 2


def test_to_dict_flat_round_trip():
    p = profile_document(texto_completo="Peca 1 carcinoma.")
    d = p.to_dict()
    assert "complexity" in d and "format" in d
    assert d["complexity"]["char_count"] == len("Peca 1 carcinoma.")
    assert isinstance(d["format"]["cap_style"], bool)


def test_document_profile_is_dataclass_bundle():
    p = profile_document(texto_completo="xx")
    assert isinstance(p, DocumentProfile)


def test_calibration_thresholds_are_not_hardcoded_silently():
    """Contrato: thresholds default podem mudar, mas precisam ser exportados."""
    assert LONG_REPORT_CHAR_THRESHOLD == 5000
    assert LONG_MULTI_ENTITY_COUNT_THRESHOLD == 10


def test_custom_thresholds_override_defaults():
    texto = "x" * 1000
    p = profile_document(texto_completo=texto, long_report_threshold=500)
    assert p.complexity.long_report is True
