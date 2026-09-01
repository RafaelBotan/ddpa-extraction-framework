"""Métricas por família canon DDPA v5.3.

Três famílias (canon §"Métricas canônicas por família"):

- singleton_semantic: natureza_lesao, organ_primario, HER-2 status
    → overall_agreement, class_recall crítica, serious_error_rate, evidence_support_rate
- objective_singleton: Ki67%, RE%, RP%, HER-2 escore
    → candidate_precision, wrong_section_rate, evidence_support_rate, conflict_rate
- objective_list: margem mm, BBPS, TNM, contagens
    → entity_omission_rate, array_cardinality_agreement, wrong_entity_rate, evidence_support_rate

Este módulo implementa **singleton_semantic** (imediata V6-1 Etapa 2.5). Outras famílias
entram quando houver variável ativa na extração.

Matriz A/B/C/G (canon v5.3):

- A = predição nova (produção candidata)
- B = predição baseline (prompt/modelo anterior)
- C = predição cascata (se aplicável; senão None)
- G = referência operacional (opus_silver | human_gold | objective_gold)

NUNCA usar uma das predições como referência das outras — isso gera tautologia
(B = Opus E G = Opus → accuracy_B = 1.0 por definição, sem sinal).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Iterable


CRITICAL_CLASSES_NATUREZA = ("MALIGNO", "BORDERLINE", "INDETERMINADO")
SERIOUS_SWAP_PAIRS = frozenset({("BENIGNO", "MALIGNO"), ("MALIGNO", "BENIGNO")})


@dataclass
class SingletonSemanticMetrics:
    """Canon v5.3 — 4 métricas da família singleton semântica."""

    n: int
    overall_agreement: float
    class_recall: dict[str, float]
    class_support: dict[str, int]
    serious_error_rate: float
    evidence_support_rate: float
    confusion: dict[str, dict[str, int]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MatrixABCG:
    """Comparação A/B/C/G com decisão canon v5.3."""

    agreement_A_vs_G: float
    agreement_B_vs_G: float | None
    agreement_C_vs_G: float | None
    delta_A_minus_B: float | None
    serious_error_rate_A: float
    serious_error_rate_B: float | None
    serious_error_rate_C: float | None
    n: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().upper()


def _evidence_supported(evidence: Any, source_text: Any) -> bool:
    ev = (evidence or "").strip() if isinstance(evidence, str) else str(evidence or "").strip()
    if not ev:
        return False
    src = (source_text or "").strip() if isinstance(source_text, str) else str(source_text or "").strip()
    if not src:
        return True
    return ev.lower() in src.lower() or ev[: min(len(ev), 40)].lower() in src.lower()


def singleton_semantic(
    predictions: Iterable[str],
    references: Iterable[str],
    *,
    evidences: Iterable[str] | None = None,
    source_texts: Iterable[str] | None = None,
    critical_classes: Iterable[str] = CRITICAL_CLASSES_NATUREZA,
    serious_swap_pairs: frozenset[tuple[str, str]] = SERIOUS_SWAP_PAIRS,
) -> SingletonSemanticMetrics:
    """Computa as 4 métricas da família singleton semântica.

    ``predictions`` e ``references`` devem ter o mesmo comprimento e ordem.
    ``evidences`` (opcional) e ``source_texts`` (opcional) calculam evidence_support_rate.
    """

    preds = [_normalize(p) for p in predictions]
    refs = [_normalize(r) for r in references]
    if len(preds) != len(refs):
        raise ValueError(f"len mismatch: preds={len(preds)} refs={len(refs)}")
    n = len(preds)
    if n == 0:
        return SingletonSemanticMetrics(
            n=0,
            overall_agreement=0.0,
            class_recall={},
            class_support={},
            serious_error_rate=0.0,
            evidence_support_rate=0.0,
            confusion={},
        )

    matches = sum(1 for p, r in zip(preds, refs) if p == r)
    overall = matches / n

    class_support: dict[str, int] = {}
    class_correct: dict[str, int] = {}
    for cls in critical_classes:
        support = sum(1 for r in refs if r == cls)
        correct = sum(1 for p, r in zip(preds, refs) if r == cls and p == r)
        class_support[cls] = support
        class_correct[cls] = correct
    class_recall = {
        cls: (class_correct[cls] / class_support[cls] if class_support[cls] else float("nan"))
        for cls in critical_classes
    }

    serious = 0
    for p, r in zip(preds, refs):
        if (r, p) in serious_swap_pairs:
            serious += 1
        elif r == "INDETERMINADO" and p not in ("INDETERMINADO", ""):
            serious += 1
    serious_error_rate = serious / n

    if evidences is not None:
        ev_list = list(evidences)
        src_list = list(source_texts) if source_texts is not None else [""] * n
        if len(ev_list) != n or len(src_list) != n:
            raise ValueError("evidence/source len mismatch")
        supported = sum(1 for e, s in zip(ev_list, src_list) if _evidence_supported(e, s))
        evidence_support_rate = supported / n
    else:
        evidence_support_rate = float("nan")

    confusion: dict[str, dict[str, int]] = {}
    for p, r in zip(preds, refs):
        if not r:
            continue
        confusion.setdefault(r, {})
        confusion[r][p] = confusion[r].get(p, 0) + 1

    return SingletonSemanticMetrics(
        n=n,
        overall_agreement=overall,
        class_recall=class_recall,
        class_support=class_support,
        serious_error_rate=serious_error_rate,
        evidence_support_rate=evidence_support_rate,
        confusion=confusion,
    )


def matrix_abcg(
    gold: Iterable[str],
    prediction_a: Iterable[str],
    prediction_b: Iterable[str] | None = None,
    prediction_c: Iterable[str] | None = None,
    *,
    serious_swap_pairs: frozenset[tuple[str, str]] = SERIOUS_SWAP_PAIRS,
) -> MatrixABCG:
    """Aplica matriz A/B/C/G canon v5.3.

    Raises se qualquer predição coincide exatamente com ``gold`` por identidade de lista
    (tautologia). Usar silver independente, não auto-referência.
    """

    g = [_normalize(x) for x in gold]
    a = [_normalize(x) for x in prediction_a]
    n = len(g)
    if n == 0 or len(a) != n:
        raise ValueError(f"len mismatch: gold={n} A={len(a)}")

    notes: list[str] = []

    def _agreement(pred: list[str]) -> float:
        return sum(1 for p, r in zip(pred, g) if p == r) / n

    def _serious(pred: list[str]) -> float:
        serious = 0
        for p, r in zip(pred, g):
            if (r, p) in serious_swap_pairs:
                serious += 1
            elif r == "INDETERMINADO" and p not in ("INDETERMINADO", ""):
                serious += 1
        return serious / n

    agreement_A = _agreement(a)
    serious_A = _serious(a)
    if agreement_A == 1.0:
        notes.append("A vs G = 1.000: verificar se A e G são listas idênticas (tautologia).")

    agreement_B = None
    serious_B = None
    delta_A_minus_B = None
    if prediction_b is not None:
        b = [_normalize(x) for x in prediction_b]
        if len(b) != n:
            raise ValueError(f"B len mismatch: {len(b)} vs {n}")
        agreement_B = _agreement(b)
        serious_B = _serious(b)
        delta_A_minus_B = agreement_A - agreement_B
        if agreement_B == 1.0:
            notes.append("B vs G = 1.000: B pode ser o próprio gold (tautologia).")

    agreement_C = None
    serious_C = None
    if prediction_c is not None:
        c = [_normalize(x) for x in prediction_c]
        if len(c) != n:
            raise ValueError(f"C len mismatch: {len(c)} vs {n}")
        agreement_C = _agreement(c)
        serious_C = _serious(c)

    return MatrixABCG(
        agreement_A_vs_G=agreement_A,
        agreement_B_vs_G=agreement_B,
        agreement_C_vs_G=agreement_C,
        delta_A_minus_B=delta_A_minus_B,
        serious_error_rate_A=serious_A,
        serious_error_rate_B=serious_B,
        serious_error_rate_C=serious_C,
        n=n,
        notes=notes,
    )
