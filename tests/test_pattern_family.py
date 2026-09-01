"""Tests for pattern_family — normalized skeletons + family routing (Proposer v2.0)."""
from __future__ import annotations
import pytest


def test_normalize_window_strips_accents_and_lowercases():
    from sandbox.pattern_family import _strip_accents
    assert _strip_accents('Pólipo SÉSSIL'.lower()) == 'polipo sessil'
    assert _strip_accents('superfície') == 'superficie'


def test_normalize_window_replaces_numbers_with_N():
    from sandbox.pattern_family import normalize_window
    out = normalize_window('pólipo 5mm', unit='mm')
    assert 'N' in out
    assert '5' not in out


def test_normalize_window_collapses_unit():
    from sandbox.pattern_family import normalize_window
    out = normalize_window('5mm', unit='mm')
    assert 'UNIT_MM' in out


def test_normalize_window_collapses_polipo_subject():
    from sandbox.pattern_family import normalize_window
    a = normalize_window('polipo sessil', unit='mm')
    b = normalize_window('pólipo pediculado', unit='mm')
    assert 'POLIPO_SUBJ' in a
    assert 'POLIPO_SUBJ' in b


def test_normalize_window_collapses_lesao_subject():
    from sandbox.pattern_family import normalize_window
    a = normalize_window('lesão plana/elevada', unit='mm')
    b = normalize_window('lesao polipoide', unit='mm')
    assert 'LESAO_SUBJ' in a
    assert 'LESAO_SUBJ' in b


def test_normalize_window_keeps_cerca_and_plusminus():
    from sandbox.pattern_family import normalize_window
    a = normalize_window('com cerca de 10mm', unit='mm')
    b = normalize_window('com +/- 15 mm', unit='mm')
    assert 'CERCA' in a
    assert 'PLUSMINUS' in b


def test_detect_negative_subject_papilofibroma():
    from sandbox.pattern_family import detect_negative_subject
    text = 'papilofibroma em canal anal medindo +/- 15 mm'
    assert detect_negative_subject(text, var_name='polipo_tamanho_max_mm') is True


def test_detect_negative_subject_hemorroida():
    from sandbox.pattern_family import detect_negative_subject
    text = 'hemorroida interna grau II de 5mm'
    assert detect_negative_subject(text, var_name='polipo_tamanho_max_mm') is True


def test_detect_negative_subject_false_for_polipo():
    from sandbox.pattern_family import detect_negative_subject
    text = 'polipo sessil 5mm superficie regular'
    assert detect_negative_subject(text, var_name='polipo_tamanho_max_mm') is False


def test_detect_multidim_pattern():
    from sandbox.pattern_family import detect_multidim
    assert detect_multidim('3x4 mm') is True
    assert detect_multidim('3 x 4mm') is True
    assert detect_multidim('8mm e 10mm') is False  # lista, não multidim geométrico
    assert detect_multidim('5mm') is False


def test_build_pattern_families_groups_f1_template():
    """F1: 'exibia pólipo séssil,N mm' de múltiplas janelas vira UMA família."""
    from sandbox.pattern_family import build_pattern_families
    # Simulate clusters (output of filter_clusters)
    clusters = [
        {
            'ia_value': '5.0',
            'variable': 'polipo_tamanho_max_mm',
            'count': 300,
            'evidence_windows': [
                'mucosa do colon ascendente exibia polipo sessil,5mm,superficie regular',
                'mucosa do reto exibia polipo sessil,5mm,superficie regular',
                'mucosa do sigmoide exibia polipo sessil,5mm,superficie regular',
            ],
            'grounding_rate': 0.63,
            'plausibility_tag': 'plausible',
            'support_tag': 'coherent_cluster',
            'grounding_tag': 'weakly_grounded',
        },
        {
            'ia_value': '6.0',
            'variable': 'polipo_tamanho_max_mm',
            'count': 80,
            'evidence_windows': [
                'mucosa do colon descendente exibia polipo sessil,6mm,superficie regular',
                'mucosa do reto exibia polipo sessil,6mm,superficie regular',
            ],
            'grounding_rate': 0.52,
            'plausibility_tag': 'plausible',
            'support_tag': 'coherent_cluster',
            'grounding_tag': 'weakly_grounded',
        },
    ]
    families = build_pattern_families(
        clusters, var_name='polipo_tamanho_max_mm', unit='mm',
    )
    # Same template should merge into single positive family
    positive = [f for f in families if f['lane'] == 'positive']
    assert len(positive) == 1
    fam = positive[0]
    assert fam['total_count'] == 380
    assert set(fam['ia_values']) == {'5.0', '6.0'}


def test_build_pattern_families_routes_f4_multidim():
    from sandbox.pattern_family import build_pattern_families
    clusters = [
        {
            'ia_value': '3.0',
            'variable': 'polipo_tamanho_max_mm',
            'count': 50,
            'evidence_windows': [
                'lesao com 3x4 mm em colon sigmoide',
                'polipo de 3 x 5 mm em reto',
            ],
            'grounding_rate': 0.4,
            'plausibility_tag': 'plausible',
            'support_tag': 'coherent_cluster',
            'grounding_tag': 'weakly_grounded',
        },
    ]
    families = build_pattern_families(
        clusters, var_name='polipo_tamanho_max_mm', unit='mm',
    )
    lanes = {f['lane'] for f in families}
    assert 'multidim' in lanes


def test_build_pattern_families_routes_f5_negative():
    from sandbox.pattern_family import build_pattern_families
    clusters = [
        {
            'ia_value': '15.0',
            'variable': 'polipo_tamanho_max_mm',
            'count': 30,
            'evidence_windows': [
                'papilofibroma em canal anal medindo +/- 15 mm',
                'papilofibroma canal anal com 15 mm',
            ],
            'grounding_rate': 0.4,
            'plausibility_tag': 'plausible',
            'support_tag': 'coherent_cluster',
            'grounding_tag': 'weakly_grounded',
        },
    ]
    families = build_pattern_families(
        clusters, var_name='polipo_tamanho_max_mm', unit='mm',
    )
    lanes = {f['lane'] for f in families}
    assert 'negative' in lanes


def test_family_purity_gate():
    """Famílias heterogêneas demais devem virar NEEDS_RECLUSTERING."""
    from sandbox.pattern_family import build_pattern_families
    # Windows from same cluster but with drastically different templates
    clusters = [
        {
            'ia_value': '5.0',
            'variable': 'polipo_tamanho_max_mm',
            'count': 40,
            'evidence_windows': [
                'exibia polipo sessil,5mm,superficie regular',
                'papilofibroma em canal anal medindo 5 mm',
                'lesao com 5x7 mm em sigmoide',
                'hemorroida interna 5mm',
            ],
            'grounding_rate': 0.5,
            'plausibility_tag': 'plausible',
            'support_tag': 'coherent_cluster',
            'grounding_tag': 'weakly_grounded',
        },
    ]
    families = build_pattern_families(
        clusters, var_name='polipo_tamanho_max_mm', unit='mm',
    )
    # Must have at least multidim + negative + some positive/reclustering
    lanes = {f['lane'] for f in families}
    assert 'multidim' in lanes
    assert 'negative' in lanes
