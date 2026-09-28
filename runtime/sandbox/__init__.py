"""DDPA Proposal Sandbox — patch card generation, validation, and reporting."""
from sandbox.patch_card import (
    ProposedChange, BarrierResult, SandboxResult, ExecutionResult,
    PatchCard, DecisionRecord, generate_patch_cards,
)
from sandbox.plausibility import (
    classify_plausibility, classify_support,
    filter_clusters, filter_clusters_all,
)
from sandbox.proposal_executor import (
    execute_overlay_regex,
    execute_proposal_single,
    execute_proposal_batch,
)
from sandbox.evaluator import (
    barrier_canary_pass,
    barrier_target_hit,
    barrier_near_miss_safety,
    barrier_corpus_skew,
    barrier_corpus_wide_shadow,
    site_skew_warning,
    corpus_wide_impact_forecast,
    evaluate_card,
)
from sandbox.regression import (
    RegressionCase,
    load_regression_set,
    barrier_regression_gate,
)
from sandbox.report import (
    card_to_jsonl,
    write_cards_jsonl,
    render_cards_markdown,
    write_cards_report,
)

__all__ = [
    'ProposedChange', 'BarrierResult', 'SandboxResult', 'ExecutionResult',
    'PatchCard', 'DecisionRecord', 'generate_patch_cards',
    'classify_plausibility', 'classify_support',
    'filter_clusters', 'filter_clusters_all',
    'execute_overlay_regex',
    'execute_proposal_single',
    'execute_proposal_batch',
    'barrier_canary_pass',
    'barrier_target_hit',
    'barrier_near_miss_safety',
    'barrier_corpus_skew',
    'site_skew_warning',
    'corpus_wide_impact_forecast',
    'RegressionCase',
    'load_regression_set',
    'barrier_regression_gate',
    'barrier_corpus_wide_shadow',
    'evaluate_card',
    'card_to_jsonl',
    'write_cards_jsonl',
    'render_cards_markdown',
    'write_cards_report',
]
