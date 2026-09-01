"""Integration tests for the full sandbox pipeline."""
from __future__ import annotations
import json
import pytest
import pandas as pd
from policy_registry import Policy, OntologySpec
from study_contract import Canary


class TestEndToEndPipeline:
    @pytest.fixture
    def policy(self):
        return Policy(
            variable='polipo_tamanho_max_mm',
            taxonomy=['MEAS-N'],
            ontology=OntologySpec(type='float', values=[], range=(0.1, 100.0)),
            allows_implicit_negative=False,
            default_if_silent=None,
            section_scope=['descricao', 'conclusao'],
        )

    @pytest.fixture
    def canaries(self):
        return [
            Canary(id='C1', text='sem polipos no exame', expected_value=None),
            Canary(id='C2', text='PREPARO: Boston 2+3+3=8', expected_value=None),
        ]

    @pytest.fixture
    def corpus_df(self):
        rows = []
        # 20 L1 misses with IA=5.0
        for i in range(20):
            rows.append({
                'id_registro': i,
                'texto_laudo': f'polipo sessil de 5mm no ceco (caso {i})',
                'clinica': 'Clinic A' if i < 12 else 'Clinic B',
                'polipo_tamanho_max_mm__value': None,
                'polipo_tamanho_max_mm__ia': 5.0,
                'polipo_tamanho_max_mm__resolution': 'abstained_unsupported',
                'polipo_tamanho_max_mm__status': 'no_match',
            })
        # 5 L1 misses with IA=0.0 (impossible)
        for i in range(20, 25):
            rows.append({
                'id_registro': i,
                'texto_laudo': 'mucosa normal sem polipos',
                'clinica': 'Clinic A',
                'polipo_tamanho_max_mm__value': None,
                'polipo_tamanho_max_mm__ia': 0.0,
                'polipo_tamanho_max_mm__resolution': 'abstained_unsupported',
                'polipo_tamanho_max_mm__status': 'no_match',
            })
        # 30 correct extractions
        for i in range(25, 55):
            rows.append({
                'id_registro': i,
                'texto_laudo': f'polipo de {10 + i % 5}mm no sigmoide',
                'clinica': 'Clinic A' if i < 40 else 'Clinic B',
                'polipo_tamanho_max_mm__value': float(10 + i % 5),
                'polipo_tamanho_max_mm__ia': float(10 + i % 5),
                'polipo_tamanho_max_mm__resolution': 'accepted_exact',
                'polipo_tamanho_max_mm__status': 'measure_explicit',
            })
        return pd.DataFrame(rows)

    def test_full_pipeline(self, policy, canaries, corpus_df, tmp_path):
        from sandbox.plausibility import filter_clusters
        from sandbox.patch_card import generate_patch_cards
        from sandbox.evaluator import evaluate_card
        from sandbox.report import write_cards_jsonl, write_cards_report
        from observability.pattern_discovery import find_l1_misses_ia_hits, cluster_by_ia_value

        var = 'polipo_tamanho_max_mm'

        # Step 1: Pattern discovery
        misses = find_l1_misses_ia_hits(corpus_df, var)
        clusters = cluster_by_ia_value(misses, var)
        assert '5.0' in clusters
        assert '0.0' in clusters

        # Step 2: Plausibility filter
        filtered = filter_clusters(clusters, policy, var, min_count=5)
        ia_values = [c['ia_value'] for c in filtered]
        assert '5.0' in ia_values
        assert '0.0' not in ia_values  # impossible

        # Step 3: Generate cards
        cards = generate_patch_cards(filtered, corpus_df, var, seed=42)
        assert len(cards) >= 1

        # Step 4: Evaluate
        def fake_detector(t):
            return None
        for card in cards:
            evaluate_card(card, canaries, policy, corpus_df, fake_detector)

        # Step 5: Check results
        for card in cards:
            assert card.sandbox_result is not None
            assert card.sandbox_result.status in (
                'APPROVED', 'NEEDS_REFINEMENT', 'LOW_TARGET_HIT',
                'REJECTED_CANARY', 'FALSE_POSITIVE_RISK', 'SKEW_WARNING',
            )

        # Step 6: Write outputs
        jsonl_path = tmp_path / 'patch_cards.jsonl'
        report_path = tmp_path / 'patch_cards_report.md'
        write_cards_jsonl(cards, jsonl_path)
        write_cards_report(cards, var, report_path)

        assert jsonl_path.exists()
        assert report_path.exists()

        # Verify JSONL is valid
        with jsonl_path.open() as f:
            for line in f:
                parsed = json.loads(line)
                assert 'card_id' in parsed
                assert 'sandbox_result' in parsed

    def test_impossible_values_excluded(self, policy, corpus_df):
        from sandbox.plausibility import filter_clusters_all
        from observability.pattern_discovery import find_l1_misses_ia_hits, cluster_by_ia_value

        var = 'polipo_tamanho_max_mm'
        misses = find_l1_misses_ia_hits(corpus_df, var)
        clusters = cluster_by_ia_value(misses, var)
        all_tagged = filter_clusters_all(clusters, policy, var, min_count=1)

        tags = {c['ia_value']: c['plausibility_tag'] for c in all_tagged}
        assert tags['0.0'] == 'impossible'
        assert tags['5.0'] == 'plausible'
