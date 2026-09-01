"""DDPA Sandbox — PatchCard serialization to JSONL and Markdown reports."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sandbox.patch_card import PatchCard


# ---------------------------------------------------------------------------
# JSONL serialization
# ---------------------------------------------------------------------------

def card_to_jsonl(card: "PatchCard") -> str:
    """Serialize one PatchCard to a JSONL line.

    Uses ``dataclasses.asdict()`` so nested dataclasses (ProposedChange,
    SandboxResult, BarrierResult) are expanded automatically.
    Non-serialisable objects fall back to ``str()``.
    """
    return json.dumps(dataclasses.asdict(card), default=str, ensure_ascii=False)


def write_cards_jsonl(cards: "list[PatchCard]", path: "Path | str") -> None:
    """Write all PatchCards to *path*, one JSON object per line.

    Parent directories are created automatically.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [card_to_jsonl(c) for c in cards]
    out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

def render_cards_markdown(cards: "list[PatchCard]", var_name: str) -> str:
    """Render a human-readable Markdown report for a list of PatchCards.

    Structure
    ---------
    1. Header — variable name + card count
    2. Summary table — one row per card
    3. Per-card detail sections
    4. Approval instructions
    """
    lines: list[str] = []

    # ------------------------------------------------------------------
    # 1. Header
    # ------------------------------------------------------------------
    lines.append(f"# PatchCard Report — `{var_name}`")
    lines.append("")
    lines.append(f"**Variable:** `{var_name}`  ")
    lines.append(f"**Cards:** {len(cards)}")
    lines.append("")

    # ------------------------------------------------------------------
    # 2. Summary table
    # ------------------------------------------------------------------
    lines.append("## Summary")
    lines.append("")
    lines.append(
        "| card_id | IA value | count | change_type | status | impact |"
    )
    lines.append(
        "| --- | --- | --- | --- | --- | --- |"
    )

    for card in cards:
        ia_value = card.source_cluster.get("ia_value", "—")
        count = card.source_cluster.get("count", "—")
        change_type = card.proposed_change.change_type
        status = (
            card.sandbox_result.status
            if card.sandbox_result is not None
            else "pending"
        )
        impact = card.impact_estimate

        lines.append(
            f"| {card.card_id} | {ia_value} | {count} | {change_type} | {status} | {impact} |"
        )

    lines.append("")

    # ------------------------------------------------------------------
    # 3. Per-card detail sections
    # ------------------------------------------------------------------
    lines.append("## Card Details")
    lines.append("")

    for card in cards:
        lines.append(f"### {card.card_id}")
        lines.append("")

        # --- Proposed change
        pc = card.proposed_change
        lines.append(f"**Change type:** {pc.change_type}  ")
        lines.append(f"**Description:** {pc.description}  ")
        lines.append(f"**Risk level:** {pc.risk_level}  ")
        lines.append(f"**Target scope:** {', '.join(pc.target_scope)}  ")
        lines.append("")

        # --- Regex skeleton
        if pc.regex_skeleton:
            lines.append("**Regex skeleton:**")
            lines.append("")
            lines.append(f"```regex")
            lines.append(pc.regex_skeleton)
            lines.append("```")
            lines.append("")

        if pc.capture_map:
            capture_str = ", ".join(f"`{k}` → `{v}`" for k, v in pc.capture_map.items())
            lines.append(f"**Capture map:** {capture_str}  ")
            lines.append("")

        if pc.near_miss_patterns:
            lines.append("**Near-miss patterns (must NOT match):**")
            for p in pc.near_miss_patterns:
                lines.append(f"- `{p}`")
            lines.append("")

        # --- Site distribution
        if card.site_distribution:
            lines.append("**Site distribution:**")
            lines.append("")
            lines.append("| site | count |")
            lines.append("| --- | --- |")
            for site, cnt in card.site_distribution.items():
                lines.append(f"| {site} | {cnt} |")
            lines.append("")

        # --- Tags
        lines.append(
            f"**Plausibility:** `{card.plausibility_tag}`  "
            f"**Support:** `{card.support_tag}`  "
            f"**Impact estimate:** {card.impact_estimate}  "
        )
        lines.append("")

        # --- Family coverage (v2.0.1 per GPT-5 verdict)
        coverage = card.source_cluster.get("family_coverage_rate")
        if coverage is not None:
            detail = card.source_cluster.get("family_coverage_detail", {})
            frag = card.source_cluster.get("family_fragmentation", {})
            lines.append(
                f"**Family coverage:** {coverage:.1%} "
                f"(claimed {detail.get('claimed_here', 0)} / "
                f"source_cluster {detail.get('source_cluster_total', 0)})"
            )
            if frag:
                top_frag = list(frag.items())[:5]
                frag_str = ", ".join(f"{k}={v}" for k, v in top_frag)
                lines.append(f"**Family fragmentation (top 5):** {frag_str}  ")
            lines.append("")

        # --- Overlaps
        if card.overlaps_with:
            lines.append(f"**Overlaps with:** {', '.join(card.overlaps_with)}  ")
            lines.append("")

        # --- Sandbox / barrier results
        if card.sandbox_result is not None:
            sr = card.sandbox_result
            lines.append(f"**Sandbox status:** `{sr.status}`")
            lines.append("")
            if sr.barriers:
                lines.append("**Barrier results:**")
                lines.append("")
                lines.append("| barrier | passed | score | detail |")
                lines.append("| --- | --- | --- | --- |")
                for br in sr.barriers:
                    passed_str = "✓" if br.passed else "✗"
                    score_str = f"{br.score:.4g}" if br.score is not None else "—"
                    detail_safe = str(br.detail).replace("|", "\\|")
                    lines.append(
                        f"| {br.barrier} | {passed_str} | {score_str} | {detail_safe} |"
                    )
                lines.append("")

            if sr.impact_report:
                lines.append("**Impact report:**")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(sr.impact_report, ensure_ascii=False, indent=2))
                lines.append("```")
                lines.append("")
        else:
            lines.append("**Sandbox status:** `pending`  ")
            lines.append("")

        # --- Human decision
        if card.human_decision:
            lines.append(f"**Human decision:** `{card.human_decision}`  ")
            lines.append("")

        # --- Eval examples (first 3)
        if card.evidence_eval:
            lines.append("**Eval examples** (first 3):")
            lines.append("")
            for ex in card.evidence_eval[:3]:
                lines.append(f"- `{ex}`")
            lines.append("")

        # --- Hard negatives (first 3)
        if card.hard_negatives:
            lines.append("**Hard negatives** (first 3):")
            lines.append("")
            for hn in card.hard_negatives[:3]:
                texto = hn.get("texto_laudo", str(hn))
                lines.append(f"- `{texto}`")
            lines.append("")

        lines.append("---")
        lines.append("")

    # ------------------------------------------------------------------
    # 4. Approval instructions
    # ------------------------------------------------------------------
    lines.append("## Approval Instructions")
    lines.append("")
    lines.append(
        "To approve a card, set `human_decision = 'approve'` in the JSONL file "
        "and re-run the adjudication pipeline.  "
    )
    lines.append(
        "To reject, set `human_decision = 'reject'`.  "
    )
    lines.append(
        "To defer for later review, set `human_decision = 'defer'`.  "
    )
    lines.append("")
    lines.append(
        "> Cards with `APPROVED` sandbox status and `human_decision = 'approve'` "
        "will be applied automatically on the next pipeline run."
    )
    lines.append("")

    return "\n".join(lines)


def write_cards_report(
    cards: "list[PatchCard]", var_name: str, path: "Path | str"
) -> None:
    """Write the Markdown report for *cards* to *path*.

    Parent directories are created automatically.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_cards_markdown(cards, var_name), encoding="utf-8"
    )
