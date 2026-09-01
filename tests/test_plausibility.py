"""Tests for plausibility filter — impossible values + cluster quality."""
from __future__ import annotations
import pytest
from policy_registry import Policy, OntologySpec


def _make_policy(ont_type='float', ont_range=(0.1, 100.0), ont_values=None):
    return Policy(
        variable='test_var',
        taxonomy=['NUM-C'],
        ontology=OntologySpec(
            type=ont_type,
            values=ont_values or [],
            range=ont_range,
        ),
        allows_implicit_negative=False,
        default_if_silent=None,
    )


class TestClassifyPlausibility:
    def test_numeric_in_range_is_plausible(self):
        from sandbox.plausibility import classify_plausibility
        policy = _make_policy(ont_type='float', ont_range=(0.1, 100.0))
        assert classify_plausibility('5.0', policy) == 'plausible'

    def test_numeric_zero_when_range_starts_above_is_impossible(self):
        from sandbox.plausibility import classify_plausibility
        policy = _make_policy(ont_type='float', ont_range=(0.1, 100.0))
        assert classify_plausibility('0.0', policy) == 'impossible'
        assert classify_plausibility('0', policy) == 'impossible'

    def test_numeric_above_range_is_impossible(self):
        from sandbox.plausibility import classify_plausibility
        policy = _make_policy(ont_type='float', ont_range=(0.1, 100.0))
        assert classify_plausibility('200.0', policy) == 'impossible'

    def test_negative_is_impossible(self):
        from sandbox.plausibility import classify_plausibility
        policy = _make_policy(ont_type='float', ont_range=(0.1, 100.0))
        assert classify_plausibility('-5', policy) == 'impossible'

    def test_enum_valid_is_plausible(self):
        from sandbox.plausibility import classify_plausibility
        policy = _make_policy(ont_type='enum', ont_range=None, ont_values=['sim', 'nao'])
        assert classify_plausibility('sim', policy) == 'plausible'

    def test_enum_invalid_is_impossible(self):
        from sandbox.plausibility import classify_plausibility
        policy = _make_policy(ont_type='enum', ont_range=None, ont_values=['sim', 'nao'])
        assert classify_plausibility('talvez', policy) == 'impossible'

    def test_no_ontology_range_is_unverified(self):
        from sandbox.plausibility import classify_plausibility
        policy = _make_policy(ont_type='string', ont_range=None, ont_values=[])
        assert classify_plausibility('anything', policy) == 'unverified'

    def test_non_numeric_string_for_float_is_impossible(self):
        from sandbox.plausibility import classify_plausibility
        policy = _make_policy(ont_type='float', ont_range=(0.1, 100.0))
        assert classify_plausibility('abc', policy) == 'impossible'

    def test_integer_in_range(self):
        from sandbox.plausibility import classify_plausibility
        policy = _make_policy(ont_type='integer', ont_range=(0, 9))
        assert classify_plausibility('5', policy) == 'plausible'
        assert classify_plausibility('10', policy) == 'impossible'


class TestClassifySupport:
    def test_low_support(self):
        from sandbox.plausibility import classify_support
        cluster = {'count': 3, 'evidence_windows': ['a', 'b', 'c']}
        assert classify_support(cluster, min_count=10) == 'low_support'

    def test_coherent_cluster(self):
        from sandbox.plausibility import classify_support
        cluster = {
            'count': 50,
            'evidence_windows': [
                'polipo de 5mm no ceco',
                'polipo de 5mm no sigmoide',
                'polipo sessil de 5mm',
                'polipo de 5mm',
                'polipo pediculado de 5mm',
            ],
        }
        assert classify_support(cluster, min_count=10) == 'coherent_cluster'

    def test_noisy_cluster(self):
        from sandbox.plausibility import classify_support
        cluster = {
            'count': 50,
            'evidence_windows': [
                'boston 5+4=9',
                'adequado escore 5',
                'BBPS total: 5',
                'preparo 5 pontos',
                'clean colon score cinco',
                'relato indica 5.0',
            ],
        }
        assert classify_support(cluster, min_count=10) == 'noisy_cluster'


class TestFilterClusters:
    def test_filters_impossible_and_low_support(self):
        from sandbox.plausibility import filter_clusters
        policy = _make_policy(ont_type='float', ont_range=(0.1, 100.0))
        clusters = {
            '5.0': {'count': 305, 'evidence_samples': ['x'] * 5, 'evidence_windows': ['polipo de 5mm'] * 5},
            '0.0': {'count': 266, 'evidence_samples': ['y'] * 5, 'evidence_windows': ['sem polipo'] * 5},
            '3.0': {'count': 3, 'evidence_samples': ['z'] * 3, 'evidence_windows': ['polipo de 3mm'] * 3},
        }
        result = filter_clusters(clusters, policy, 'polipo_tamanho_max_mm', min_count=10)
        assert len(result) == 1
        assert result[0]['ia_value'] == '5.0'
        assert result[0]['plausibility_tag'] == 'plausible'
        assert result[0]['support_tag'] == 'coherent_cluster'

    def test_returns_all_with_tags_before_filtering(self):
        from sandbox.plausibility import filter_clusters_all
        policy = _make_policy(ont_type='float', ont_range=(0.1, 100.0))
        clusters = {
            '5.0': {'count': 305, 'evidence_samples': ['x'] * 5, 'evidence_windows': ['polipo de 5mm'] * 5},
            '0.0': {'count': 266, 'evidence_samples': ['y'] * 5, 'evidence_windows': ['sem polipo'] * 5},
        }
        result = filter_clusters_all(clusters, policy, 'polipo_tamanho_max_mm', min_count=10)
        assert len(result) == 2
        tags = {r['ia_value']: r['plausibility_tag'] for r in result}
        assert tags['0.0'] == 'impossible'
        assert tags['5.0'] == 'plausible'
