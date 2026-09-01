"""Tests for PatchCard dataclasses and generation."""
from __future__ import annotations
import pytest


class TestProposedChange:
    def test_new_regex_construction(self):
        from sandbox.patch_card import ProposedChange
        pc = ProposedChange(
            change_type='new_regex',
            description='add regex for polipo de Xmm pattern',
            regex_skeleton=r'p[oó]lipo.*?(\d+)\s*mm',
            capture_map={'group_1': 'value_mm'},
            target_scope=['descricao', 'conclusao'],
            precedence_hint='after measure_explicit',
            near_miss_patterns=[r'polipo.*sem.*medida'],
            risk_level='medium',
        )
        assert pc.change_type == 'new_regex'
        assert pc.risk_level == 'medium'
        assert pc.capture_map == {'group_1': 'value_mm'}

    def test_new_alias_construction(self):
        from sandbox.patch_card import ProposedChange
        pc = ProposedChange(
            change_type='new_alias',
            description='map nao_mencionado to nao',
            regex_skeleton=None,
            capture_map=None,
            target_scope=['conclusao'],
            precedence_hint=None,
            near_miss_patterns=[],
            risk_level='low',
        )
        assert pc.change_type == 'new_alias'
        assert pc.risk_level == 'low'


class TestBarrierResult:
    def test_construction(self):
        from sandbox.patch_card import BarrierResult
        br = BarrierResult(
            barrier='canary_pass',
            passed=True,
            score=1.0,
            detail='All 3 canaries passed',
        )
        assert br.passed is True
        assert br.barrier == 'canary_pass'


class TestSandboxResult:
    def test_approved(self):
        from sandbox.patch_card import SandboxResult, BarrierResult
        sr = SandboxResult(
            status='APPROVED',
            barriers=[
                BarrierResult('canary_pass', True, 1.0, 'ok'),
                BarrierResult('target_hit', True, 0.85, '85% hit'),
            ],
            impact_report={'fill_rate_delta': 0.02, 'cases_changed': 150},
            generated_canaries=[{'id': 'GEN-01', 'text': 'polipo de 5mm', 'expected_value': 5.0}],
            generated_hard_negatives=[],
        )
        assert sr.status == 'APPROVED'
        assert len(sr.barriers) == 2

    def test_rejected(self):
        from sandbox.patch_card import SandboxResult, BarrierResult
        sr = SandboxResult(
            status='REJECTED_CANARY',
            barriers=[BarrierResult('canary_pass', False, 0.0, 'BBPS-01 broke')],
            impact_report={},
            generated_canaries=[],
            generated_hard_negatives=[],
        )
        assert sr.status == 'REJECTED_CANARY'
        assert sr.barriers[0].passed is False


class TestExecutionResult:
    def test_changed(self):
        from sandbox.patch_card import ExecutionResult
        er = ExecutionResult(
            baseline_value=None,
            baseline_status='no_match',
            proposed_value=5.0,
            proposed_status='overlay_match',
            changed=True,
            overlay_matched=True,
            section_compliant=True,
            ontology_compliant=True,
        )
        assert er.changed is True
        assert er.overlay_matched is True

    def test_unchanged(self):
        from sandbox.patch_card import ExecutionResult
        er = ExecutionResult(
            baseline_value=5.0,
            baseline_status='measure_explicit',
            proposed_value=5.0,
            proposed_status='measure_explicit',
            changed=False,
            overlay_matched=False,
            section_compliant=True,
            ontology_compliant=True,
        )
        assert er.changed is False


class TestPatchCard:
    def test_construction(self):
        from sandbox.patch_card import PatchCard, ProposedChange
        card = PatchCard(
            card_id='pc_tamanho_5mm_abc1',
            variable='polipo_tamanho_max_mm',
            source_cluster={'ia_value': '5.0', 'count': 305},
            proposed_change=ProposedChange(
                change_type='new_regex',
                description='test',
                regex_skeleton=r'(\d+)\s*mm',
                capture_map={'group_1': 'value_mm'},
                target_scope=['descricao'],
                precedence_hint=None,
                near_miss_patterns=[],
                risk_level='medium',
            ),
            evidence_discovery=[{'id': 1, 'text': 'polipo de 5mm'}],
            evidence_eval=[{'id': 2, 'text': 'polipo de 5mm no ceco'}],
            hard_negatives=[{'id': 3, 'text': 'sem polipos'}],
            site_distribution={'Clinic A': 200, 'Clinic B': 105},
            impact_estimate=305,
            plausibility_tag='plausible',
            support_tag='coherent_cluster',
            sandbox_result=None,
            human_decision=None,
            overlaps_with=[],
        )
        assert card.card_id == 'pc_tamanho_5mm_abc1'
        assert card.impact_estimate == 305
        assert card.sandbox_result is None


class TestDecisionRecord:
    def test_construction(self):
        from sandbox.patch_card import DecisionRecord
        dr = DecisionRecord(
            decision_id='dec_001',
            source_type='patch_card',
            change_domain='detector',
            card_id='pc_tamanho_5mm_abc1',
            human_decision='approve',
            applied_via='manual_merge',
            timestamp='2026-04-16T20:00:00Z',
            new_canaries=[],
            new_hard_negatives=[],
        )
        assert dr.source_type == 'patch_card'
        assert dr.change_domain == 'detector'


import pandas as pd


class TestGeneratePatchCards:
    @pytest.fixture
    def corpus_df(self):
        """Small corpus with known L1 misses and hits."""
        rows = []
        # 15 L1 misses with IA=5.0
        for i in range(15):
            clinic = 'Clinic A' if i < 10 else 'Clinic B'
            rows.append({
                'id_registro': i,
                'texto_laudo': f'polipo sessil de 5mm no ceco (caso {i})',
                'clinica': clinic,
                'polipo_tamanho_max_mm__value': None,
                'polipo_tamanho_max_mm__ia': 5.0,
                'polipo_tamanho_max_mm__resolution': 'abstained_unsupported',
                'polipo_tamanho_max_mm__status': 'no_match',
            })
        # 20 correct L1 extractions (for hard-negative bank)
        for i in range(15, 35):
            rows.append({
                'id_registro': i,
                'texto_laudo': f'polipo de {i}mm no sigmoide',
                'clinica': 'Clinic A',
                'polipo_tamanho_max_mm__value': float(i),
                'polipo_tamanho_max_mm__ia': float(i),
                'polipo_tamanho_max_mm__resolution': 'accepted_exact',
                'polipo_tamanho_max_mm__status': 'measure_explicit',
            })
        return pd.DataFrame(rows)

    @pytest.fixture
    def filtered_clusters(self):
        return [{
            'ia_value': '5.0',
            'variable': 'polipo_tamanho_max_mm',
            'count': 15,
            'evidence_samples': [f'polipo sessil de 5mm no ceco (caso {i})' for i in range(5)],
            'evidence_windows': ['polipo sessil de 5mm no ceco'] * 5,
            'plausibility_tag': 'plausible',
            'support_tag': 'coherent_cluster',
        }]

    def test_generates_one_card_per_cluster(self, filtered_clusters, corpus_df):
        from sandbox.patch_card import generate_patch_cards
        cards = generate_patch_cards(
            filtered_clusters, corpus_df, 'polipo_tamanho_max_mm', seed=42,
        )
        assert len(cards) == 1
        card = cards[0]
        assert card.variable == 'polipo_tamanho_max_mm'
        assert card.plausibility_tag == 'plausible'

    def test_discovery_eval_split(self, filtered_clusters, corpus_df):
        from sandbox.patch_card import generate_patch_cards
        cards = generate_patch_cards(
            filtered_clusters, corpus_df, 'polipo_tamanho_max_mm', seed=42,
        )
        card = cards[0]
        total = len(card.evidence_discovery) + len(card.evidence_eval)
        assert total == 15
        assert len(card.evidence_eval) >= 4   # ~30% of 15
        assert len(card.evidence_discovery) >= 10  # ~70% of 15

    def test_split_is_deterministic(self, filtered_clusters, corpus_df):
        from sandbox.patch_card import generate_patch_cards
        cards1 = generate_patch_cards(filtered_clusters, corpus_df, 'polipo_tamanho_max_mm', seed=42)
        cards2 = generate_patch_cards(filtered_clusters, corpus_df, 'polipo_tamanho_max_mm', seed=42)
        assert [e['id_registro'] for e in cards1[0].evidence_eval] == \
               [e['id_registro'] for e in cards2[0].evidence_eval]

    def test_hard_negatives_populated(self, filtered_clusters, corpus_df):
        from sandbox.patch_card import generate_patch_cards
        cards = generate_patch_cards(
            filtered_clusters, corpus_df, 'polipo_tamanho_max_mm', seed=42,
        )
        card = cards[0]
        assert len(card.hard_negatives) > 0
        for hn in card.hard_negatives:
            assert hn.get('resolution') == 'accepted_exact'

    def test_site_distribution(self, filtered_clusters, corpus_df):
        from sandbox.patch_card import generate_patch_cards
        cards = generate_patch_cards(
            filtered_clusters, corpus_df, 'polipo_tamanho_max_mm', seed=42,
        )
        card = cards[0]
        assert 'Clinic A' in card.site_distribution
        assert card.site_distribution['Clinic A'] == 10
        assert card.site_distribution['Clinic B'] == 5

    def test_card_id_is_deterministic(self, filtered_clusters, corpus_df):
        from sandbox.patch_card import generate_patch_cards
        cards1 = generate_patch_cards(filtered_clusters, corpus_df, 'polipo_tamanho_max_mm', seed=42)
        cards2 = generate_patch_cards(filtered_clusters, corpus_df, 'polipo_tamanho_max_mm', seed=42)
        assert cards1[0].card_id == cards2[0].card_id

    def test_proposed_change_has_regex_skeleton(self, filtered_clusters, corpus_df):
        from sandbox.patch_card import generate_patch_cards
        cards = generate_patch_cards(
            filtered_clusters, corpus_df, 'polipo_tamanho_max_mm', seed=42,
        )
        card = cards[0]
        assert card.proposed_change.change_type == 'new_regex'
        assert card.proposed_change.regex_skeleton is not None
        assert '5' in card.proposed_change.description or 'mm' in card.proposed_change.description
