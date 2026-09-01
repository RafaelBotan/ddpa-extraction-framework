"""Persistencia do revisor: leitura do sample JSONL e escrita incremental do annotated.

Estrategia:
- Input  : silent_consensus_sample.jsonl (read-only)
- Output : silent_consensus_sample.annotated.jsonl (mesma pasta)
- Cada anotacao faz rewrite atomico (tmp + replace) do annotated inteiro
  — volume baixo (247 linhas), simplicidade > otimizacao.
- Retomada: ao abrir, le o annotated existente e merge por (variable, id_registro).
"""
from __future__ import annotations

import csv
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


VALID_LABELS = {"correct", "incorrect", "ambiguous"}
VALID_CONFIDENCES = {"low", "medium", "high"}


def annotated_path_for(sample_path: Path) -> Path:
    """Dado silent_consensus_sample.jsonl, devolve silent_consensus_sample.annotated.jsonl."""
    name = sample_path.name
    if name.endswith(".jsonl"):
        stem = name[: -len(".jsonl")]
        return sample_path.with_name(f"{stem}.annotated.jsonl")
    return sample_path.with_name(sample_path.name + ".annotated.jsonl")


def load_sample(sample_path: Path) -> list[dict[str, Any]]:
    """Le JSONL e devolve lista de dicts. Ignora linhas em branco."""
    rows: list[dict[str, Any]] = []
    with sample_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _record_key(rec: dict[str, Any]) -> tuple[str, Any]:
    return (str(rec.get("variable")), rec.get("id_registro"))


def _atomic_write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False))
            fh.write("\n")
    os.replace(tmp, path)


class AnnotationStore:
    """Mantem em memoria a lista de records e persiste atomicamente no disco.

    Thread-safe para operacoes simples (servidor HTTP threaded).
    """

    def __init__(
        self,
        sample_path: Path,
        reviewer_id: str = "sergio",
        *,
        text_csv_path: Path | None = None,
        text_col: str = "texto_laudo_completo",
        id_col: str = "id_registro",
    ) -> None:
        self.sample_path = Path(sample_path)
        self.reviewer_id = reviewer_id
        self.annotated_path = annotated_path_for(self.sample_path)
        self._lock = threading.Lock()
        self._records = load_sample(self.sample_path)
        self._index: dict[tuple[str, Any], int] = {
            _record_key(r): i for i, r in enumerate(self._records)
        }
        self._merge_existing_annotations()
        self._full_texts: dict[str, str] = {}
        if text_csv_path is not None:
            self._load_full_texts(Path(text_csv_path), text_col=text_col, id_col=id_col)

    def full_text_for(self, id_registro: Any) -> str | None:
        if not self._full_texts:
            return None
        return self._full_texts.get(str(id_registro))

    # --- public API ---------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self._records)

    @property
    def done(self) -> int:
        return sum(1 for r in self._records if r.get("human_label"))

    def records(self) -> list[dict[str, Any]]:
        """Copia defensiva dos registros atuais."""
        with self._lock:
            return [dict(r) for r in self._records]

    def record_at(self, index: int) -> dict[str, Any]:
        with self._lock:
            if not (0 <= index < len(self._records)):
                raise IndexError(f"index {index} out of range [0, {len(self._records)})")
            return dict(self._records[index])

    def first_pending_index(self) -> int:
        """Indice do primeiro caso ainda nao anotado; 0 se tudo anotado."""
        with self._lock:
            for i, r in enumerate(self._records):
                if not r.get("human_label"):
                    return i
            return 0

    def annotate(
        self,
        index: int,
        human_label: str,
        *,
        human_note: str | None = None,
        confidence: str = "high",
        time_spent_s: float = 0.0,
    ) -> dict[str, Any]:
        if human_label not in VALID_LABELS:
            raise ValueError(f"human_label must be in {VALID_LABELS}, got {human_label!r}")
        if confidence not in VALID_CONFIDENCES:
            raise ValueError(f"confidence must be in {VALID_CONFIDENCES}, got {confidence!r}")
        if time_spent_s < 0:
            raise ValueError("time_spent_s must be >= 0")

        with self._lock:
            if not (0 <= index < len(self._records)):
                raise IndexError(f"index {index} out of range")
            rec = self._records[index]
            rec["human_label"] = human_label
            rec["human_note"] = (human_note or "").strip() or None
            rec["reviewer_id"] = self.reviewer_id
            rec["confidence"] = confidence
            rec["timestamp"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            rec["time_spent_s"] = round(float(time_spent_s), 2)
            self._persist_locked()
            return dict(rec)

    def clear(self, index: int) -> dict[str, Any]:
        """Remove anotacao humana (volta ao estado 'pendente')."""
        with self._lock:
            if not (0 <= index < len(self._records)):
                raise IndexError(f"index {index} out of range")
            rec = self._records[index]
            for k in ("human_label", "human_note", "reviewer_id", "confidence",
                      "timestamp", "time_spent_s"):
                if k in rec:
                    rec[k] = None
            self._persist_locked()
            return dict(rec)

    # --- internals ----------------------------------------------------------

    def _load_full_texts(self, csv_path: Path, *, text_col: str, id_col: str) -> None:
        """Carrega id_registro -> texto_laudo_completo do CSV em memoria."""
        try:
            with csv_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
                reader = csv.DictReader(fh)
                if reader.fieldnames is None or id_col not in reader.fieldnames or text_col not in reader.fieldnames:
                    return
                wanted_ids = {str(r.get("id_registro")) for r in self._records}
                for row in reader:
                    rid = str(row.get(id_col, ""))
                    if rid in wanted_ids:
                        self._full_texts[rid] = row.get(text_col, "") or ""
        except OSError:
            return

    def _merge_existing_annotations(self) -> None:
        """Se ja existir annotated.jsonl, copia human_* para self._records."""
        if not self.annotated_path.exists():
            return
        with self.annotated_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                prev = json.loads(line)
                key = _record_key(prev)
                idx = self._index.get(key)
                if idx is None:
                    continue
                cur = self._records[idx]
                for k in ("human_label", "human_note", "reviewer_id",
                          "confidence", "timestamp", "time_spent_s"):
                    if prev.get(k) is not None:
                        cur[k] = prev[k]

    def _persist_locked(self) -> None:
        _atomic_write_jsonl(self.annotated_path, self._records)
