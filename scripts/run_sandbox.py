"""CLI entry point for DDPA Sandbox — plausibility filter, patch cards, evaluation.

Usage:
    python scripts/run_sandbox.py \\
        --contract contracts/e1_endoscopia.yaml \\
        --policy policies/e1_endoscopia.yaml \\
        --var polipo_tamanho_max_mm \\
        --csv runs/e1_endoscopia/.../polipo_tamanho_max_mm.csv \\
        --output-dir sandbox_reports/ \\
        --min-count 10

    # With audit disagreement mode:
    python scripts/run_sandbox.py \\
        --contract contracts/e1_endoscopia.yaml \\
        --policy policies/e1_endoscopia.yaml \\
        --var polipo_tamanho_max_mm \\
        --csv runs/e1_endoscopia/.../polipo_tamanho_max_mm.csv \\
        --audit-disagreement \\
        --n-per-direction 50
"""
from __future__ import annotations
import argparse
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / 'runtime'
for p in (ROOT, RUNTIME):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

import pandas as pd
from study_contract import load_contract
from policy_registry import load_registry
from observability.pattern_discovery import find_l1_misses_ia_hits, cluster_by_ia_value
from sandbox.plausibility import filter_clusters
from sandbox.patch_card import generate_patch_cards
from sandbox.pattern_family import build_pattern_families, generate_patch_cards_from_families
from sandbox.evaluator import evaluate_card
from sandbox.report import write_cards_jsonl, write_cards_report


def _load_detector(contract, var_name: str):
    """Dynamically import detector function from contract's variable spec."""
    var_spec = contract.variable(var_name)
    detector_mod = importlib.import_module(var_spec.detector.module)
    detector_func = getattr(detector_mod, var_spec.detector.function)
    return detector_func


def _load_canaries(contract, var_name: str) -> list:
    """Load canaries for the specified variable from the contract."""
    try:
        var_spec = contract.variable(var_name)
        return var_spec.canaries
    except KeyError:
        return []


def main():
    parser = argparse.ArgumentParser(description='DDPA Sandbox — PatchCard Pipeline')
    parser.add_argument('--contract', type=Path, required=True,
                        help='Path to study contract YAML')
    parser.add_argument('--policy', type=Path, required=True,
                        help='Path to policy registry YAML')
    parser.add_argument('--var', type=str, required=True,
                        help='Variable name to process')
    parser.add_argument('--csv', type=Path, required=True,
                        help='Path to run output CSV (L1 results for the variable)')
    parser.add_argument('--corpus', type=Path, default=None,
                        help='Optional path to corpus CSV with texto_laudo')
    parser.add_argument('--output-dir', type=Path, default=Path('sandbox_reports'),
                        help='Output directory (default: sandbox_reports/)')
    parser.add_argument('--min-count', type=int, default=10,
                        help='Minimum cluster count to include (default: 10)')
    parser.add_argument('--seed', type=int, default=42,
                        help='Random seed (default: 42)')
    parser.add_argument('--audit-disagreement', action='store_true',
                        help='Enable disagreement audit mode')
    parser.add_argument('--n-per-direction', type=int, default=50,
                        help='N per direction for disagreement audit (default: 50)')
    parser.add_argument('--use-pattern-family', action='store_true',
                        help='Proposer v2.0: cluster by normalized skeleton family '
                             '(not by ia_value). Emits template-based regex with '
                             'required/forbidden subject classes.')
    parser.add_argument('--baseline-ablation', action='store_true',
                        help='v2.0.1 ablation: replace family-specific regex with a '
                             'trivial number-near-unit baseline. Same family assignment '
                             'and evaluator. Measures whether v2.0 gain comes from the '
                             'regex/forbidden or just from the cleaner eval pipeline. '
                             'Only has effect with --use-pattern-family.')
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load contract and policy registry
    print(f'Loading contract: {args.contract}')
    contract = load_contract(args.contract)

    print(f'Loading policy registry: {args.policy}')
    registry = load_registry(args.policy)

    policy = registry.get(args.var)
    if policy is None:
        print(f'ERROR: no policy found for variable {args.var!r} in registry {args.policy}')
        sys.exit(1)

    # 2. Load CSV (+ merge corpus text if --corpus provided)
    print(f'Loading run CSV: {args.csv}')
    df = pd.read_csv(args.csv)

    if args.corpus is not None:
        print(f'Loading corpus: {args.corpus}')
        corpus_df = pd.read_csv(args.corpus)
        # Sandbox internals hardcode 'texto_laudo' — rename the contract's text_column
        # on ingest so downstream code finds it regardless of source schema.
        text_col = contract.corpus.text_column
        if text_col != 'texto_laudo':
            if text_col in corpus_df.columns:
                corpus_df = corpus_df.rename(columns={text_col: 'texto_laudo'})
                print(f'  Renamed corpus column {text_col!r} -> "texto_laudo"')
            else:
                print(f'  WARNING: text_column {text_col!r} not in corpus — sandbox will see empty text')
        if 'texto_laudo' not in df.columns and 'texto_laudo' in corpus_df.columns:
            id_col = contract.corpus.id_column
            if id_col in df.columns and id_col in corpus_df.columns:
                df = df.merge(
                    corpus_df[[id_col, 'texto_laudo']],
                    on=id_col,
                    how='left',
                )
                print(f'  Merged texto_laudo from corpus ({len(corpus_df)} rows)')
            else:
                print(f'  WARNING: id column {id_col!r} not found in both CSVs — skipping merge')
    else:
        corpus_df = df

    print(f'  Loaded {len(df)} rows')

    # 3. Pattern discovery
    print(f'\n--- Pattern Discovery: {args.var} ---')
    misses = find_l1_misses_ia_hits(df, args.var)
    print(f'  L1 misses with IA hit: {len(misses)} rows')

    if misses.empty:
        print('  No L1 misses found. Nothing to sandbox.')
        return

    clusters = cluster_by_ia_value(misses, args.var, policy=policy)
    print(f'  Clusters found: {len(clusters)}')
    for ia_val, info in clusters.items():
        gr = info.get('grounding_rate')
        gr_str = f', grounding_rate={gr:.1%}' if gr is not None else ', grounding_rate=unknown'
        print(f'    ia_value={ia_val!r}: count={info["count"]}{gr_str}')

    # 4. Plausibility filter
    print(f'\n--- Plausibility Filter (min_count={args.min_count}) ---')
    filtered = filter_clusters(clusters, policy, args.var, min_count=args.min_count)
    print(f'  Clusters after filter: {len(filtered)} (from {len(clusters)})')
    for c in filtered:
        gr = c.get('grounding_rate')
        gr_str = f', grounding={c["grounding_tag"]} ({gr:.1%})' if gr is not None else f', grounding={c["grounding_tag"]}'
        print(f'    ia_value={c["ia_value"]!r}: plausibility={c["plausibility_tag"]}, support={c["support_tag"]}, count={c["count"]}{gr_str}')

    if not filtered:
        print('  No plausible clusters with sufficient support. Exiting.')
        return

    # 5. Generate cards
    print(f'\n--- Generate PatchCards (seed={args.seed}) ---')
    if args.use_pattern_family:
        unit = policy.ontology.unit
        print(f'  Proposer v2.0 active: building pattern families (unit={unit!r})')
        families = build_pattern_families(filtered, var_name=args.var, unit=unit)
        lane_counts: dict[str, int] = {}
        for fam in families:
            lane_counts[fam['lane']] = lane_counts.get(fam['lane'], 0) + 1
        print(f'  Families built: {len(families)} '
              f'({", ".join(f"{k}={v}" for k, v in sorted(lane_counts.items()))})')
        for fam in families[:10]:
            print(f'    [{fam["lane"]}] {fam["family_id"]} count={fam["total_count"]} '
                  f'ia_values={sorted(fam.get("ia_values", []))[:6]}')
        cards = generate_patch_cards_from_families(
            families, df, args.var, unit=unit, seed=args.seed,
            min_family_count=args.min_count,
            force_trivial_regex=args.baseline_ablation,
        )
        if args.baseline_ablation:
            print('  [BASELINE_ABLATION active] — using trivial regex, no forbidden')
    else:
        cards = generate_patch_cards(filtered, df, args.var, seed=args.seed)
    print(f'  Generated {len(cards)} PatchCard(s)')

    # 6. Load canaries and detector, evaluate cards
    canaries = _load_canaries(contract, args.var)
    print(f'\n--- Evaluate Cards ({len(canaries)} canaries) ---')

    # Try to load detector dynamically; fall back to a null detector if unavailable
    detector_func = None
    try:
        detector_func = _load_detector(contract, args.var)
        print(f'  Detector loaded from contract')
    except (ImportError, AttributeError, KeyError) as exc:
        print(f'  WARNING: could not load detector ({exc}) — using null detector')
        detector_func = lambda text: None  # noqa: E731

    # Use `df` (run output with resolution + texto_laudo) for evaluation so
    # barrier 3's near_miss_safety samples rows that actually have a resolution
    # column. Falling back to the raw corpus_df would let L1 misses into the
    # "correct" set, producing false FALSE_POSITIVE_RISK flags.
    eval_corpus = df
    for card in cards:
        evaluate_card(card, canaries, policy, eval_corpus, detector_func)
        status = card.sandbox_result.status if card.sandbox_result else 'pending'
        print(f'  {card.card_id}: ia_value={card.source_cluster.get("ia_value")!r} -> {status}')

    # 7. Disagreement audit (optional)
    if args.audit_disagreement:
        print(f'\n--- Disagreement Audit (n_per_direction={args.n_per_direction}) ---')
        try:
            from audit_slice import sample_disagreement_audit
            audit_result = sample_disagreement_audit(
                df, args.var, n_per_direction=args.n_per_direction, seed=args.seed,
            )
            dir1, dir2 = audit_result
            dir1_path = args.output_dir / f'audit_{args.var}_l1_filled_ia_disagree.csv'
            dir2_path = args.output_dir / f'audit_{args.var}_l1_null_ia_filled.csv'
            dir1.to_csv(dir1_path, index=False)
            dir2.to_csv(dir2_path, index=False)
            print(f'  l1_filled_ia_disagree: {len(dir1)} rows -> {dir1_path}')
            print(f'  l1_null_ia_filled: {len(dir2)} rows -> {dir2_path}')
        except Exception as exc:
            print(f'  WARNING: disagreement audit failed ({exc})')

    # 8. Write JSONL + Markdown reports
    print(f'\n--- Write Reports ---')
    jsonl_path = args.output_dir / f'patch_cards_{args.var}.jsonl'
    report_path = args.output_dir / f'patch_cards_{args.var}_report.md'

    write_cards_jsonl(cards, jsonl_path)
    write_cards_report(cards, args.var, report_path)

    print(f'  JSONL: {jsonl_path}')
    print(f'  Report: {report_path}')

    # Summary
    print(f'\n--- Summary ---')
    status_counts: dict[str, int] = {}
    for card in cards:
        status = card.sandbox_result.status if card.sandbox_result else 'pending'
        status_counts[status] = status_counts.get(status, 0) + 1
    for status, count in sorted(status_counts.items()):
        print(f'  {status}: {count}')

    print('\nDone.')


if __name__ == '__main__':
    main()
