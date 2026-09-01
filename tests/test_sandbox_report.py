"""Tests for sandbox report rendering — JSONL + Markdown."""
from __future__ import annotations
import json
import pytest
from sandbox.patch_card import (
    PatchCard, ProposedChange, SandboxResult, BarrierResult,
)


def _make_evaluated_card():
    return PatchCard(
        card_id='pc_test_abc123',
        variable='polipo_tamanho_max_mm',
        source_cluster={'ia_value': '5.0', 'count': 305},
        proposed_change=ProposedChange(
            change_type='new_regex',
            description='L1 misses 305 cases where IA=5.0',
            regex_skeleton=r'(\d+)\s*mm',
            capture_map={'group_1': 'value_mm'},
            target_scope=['descricao', 'conclusao'],
            precedence_hint=None,
            near_miss_patterns=[],
            risk_level='medium',
        ),
        evidence_discovery=[{'id_registro': 1, 'texto_laudo': 'polipo de 5mm'}],
        evidence_eval=[{'id_registro': 2, 'texto_laudo': 'polipo de 5mm no ceco'}],
        hard_negatives=[{'id_registro': 3, 'texto_laudo': 'sem polipos'}],
        site_distribution={'Clinic A': 200, 'Clinic B': 105},
        impact_estimate=305,
        plausibility_tag='plausible',
        support_tag='coherent_cluster',
        sandbox_result=SandboxResult(
            status='APPROVED',
            barriers=[
                BarrierResult('canary_pass', True, 1.0, 'All 2 canaries passed'),
                BarrierResult('target_hit', True, 0.85, '85% hit rate'),
                BarrierResult('near_miss_safety', True, 0.0, 'Zero FP in 200 cases'),
                BarrierResult('corpus_skew', True, 0.65, 'No skew'),
                BarrierResult('corpus_wide_shadow', True, 250.0, '250 cases changed'),
            ],
            impact_report={'cases_changed': 250, 'fill_rate_delta': 0.0037},
            generated_canaries=[],
            generated_hard_negatives=[],
        ),
        human_decision=None,
    )


class TestJsonlSerialization:
    def test_card_to_jsonl_line(self):
        from sandbox.report import card_to_jsonl
        card = _make_evaluated_card()
        line = card_to_jsonl(card)
        parsed = json.loads(line)
        assert parsed['card_id'] == 'pc_test_abc123'
        assert parsed['sandbox_result']['status'] == 'APPROVED'
        assert parsed['proposed_change']['regex_skeleton'] == r'(\d+)\s*mm'

    def test_write_cards_jsonl(self, tmp_path):
        from sandbox.report import write_cards_jsonl
        cards = [_make_evaluated_card()]
        out_path = tmp_path / 'patch_cards.jsonl'
        write_cards_jsonl(cards, out_path)
        lines = out_path.read_text(encoding='utf-8').strip().split('\n')
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed['card_id'] == 'pc_test_abc123'


class TestMarkdownReport:
    def test_render_has_summary_table(self):
        from sandbox.report import render_cards_markdown
        cards = [_make_evaluated_card()]
        md = render_cards_markdown(cards, 'polipo_tamanho_max_mm')
        assert 'pc_test_abc123' in md or 'pc_test_abc' in md
        assert 'APPROVED' in md
        assert 'polipo_tamanho_max_mm' in md

    def test_render_has_barrier_details(self):
        from sandbox.report import render_cards_markdown
        cards = [_make_evaluated_card()]
        md = render_cards_markdown(cards, 'polipo_tamanho_max_mm')
        assert 'canary_pass' in md
        assert 'target_hit' in md

    def test_render_has_regex_skeleton(self):
        from sandbox.report import render_cards_markdown
        cards = [_make_evaluated_card()]
        md = render_cards_markdown(cards, 'polipo_tamanho_max_mm')
        assert r'(\d+)\s*mm' in md

    def test_write_report_file(self, tmp_path):
        from sandbox.report import write_cards_report
        cards = [_make_evaluated_card()]
        out_path = tmp_path / 'report.md'
        write_cards_report(cards, 'polipo_tamanho_max_mm', out_path)
        assert out_path.exists()
        content = out_path.read_text(encoding='utf-8')
        assert 'APPROVED' in content
