"""Tests para o pacote `revisor` (ferramenta local de auditoria humana DDPA)."""
from __future__ import annotations

import json
import http.client
import time
from pathlib import Path

import pytest

from revisor.store import (
    AnnotationStore,
    annotated_path_for,
    load_sample,
)
from revisor.server import serve_in_thread


SAMPLE_RECORDS = [
    {
        "variable": "bbps", "id_registro": 1, "resolution": "accepted_exact",
        "value": 6.0, "status": "bbps_soma_3seg", "section": "preparo",
        "evidence": "escala de boston: 2+2+2=6/9",
        "evidence_class": "literal", "certainty": None, "ia_value": 6.0,
        "strata": {"clinica": "Clinic A"},
        "human_label": None, "human_note": None,
    },
    {
        "variable": "polipo_presente", "id_registro": 42, "resolution": "accepted_semantic",
        "value": "sim", "status": "ok", "section": "conclusao",
        "evidence": "polipo em sigmoide", "evidence_class": "literal",
        "certainty": "high", "ia_value": "sim",
        "strata": {"clinica": "Clinic B"},
        "human_label": None, "human_note": None,
    },
    {
        "variable": "polipo_presente", "id_registro": 99, "resolution": "abstained_by_consensus",
        "value": "nao", "status": "silence_probed", "section": "conclusao",
        "evidence": "colonoscopia sem achados", "evidence_class": None,
        "certainty": "low", "ia_value": "nao",
        "strata": {"clinica": "Endomed"},
        "human_label": None, "human_note": None,
    },
]


@pytest.fixture()
def sample_file(tmp_path: Path) -> Path:
    p = tmp_path / "silent_consensus_sample.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for r in SAMPLE_RECORDS:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


# ---------------------------------------------------------------------------
# store / parser
# ---------------------------------------------------------------------------

def test_load_sample_parses_all_lines(sample_file: Path):
    rows = load_sample(sample_file)
    assert len(rows) == 3
    assert rows[0]["variable"] == "bbps"
    assert rows[1]["id_registro"] == 42


def test_load_sample_ignores_blank_lines(tmp_path: Path):
    p = tmp_path / "s.jsonl"
    p.write_text(
        json.dumps(SAMPLE_RECORDS[0]) + "\n\n\n" + json.dumps(SAMPLE_RECORDS[1]) + "\n",
        encoding="utf-8",
    )
    assert len(load_sample(p)) == 2


def test_annotated_path_for():
    assert annotated_path_for(Path("a/b/silent_consensus_sample.jsonl")).name \
        == "silent_consensus_sample.annotated.jsonl"
    assert annotated_path_for(Path("foo.txt")).name == "foo.txt.annotated.jsonl"


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

def test_store_initial_state(sample_file: Path):
    store = AnnotationStore(sample_file)
    assert store.total == 3
    assert store.done == 0
    assert store.first_pending_index() == 0


def test_annotate_persists_to_disk(sample_file: Path):
    store = AnnotationStore(sample_file, reviewer_id="sergio")
    store.annotate(0, "correct", human_note="ok", confidence="high", time_spent_s=4.2)
    out = store.annotated_path
    assert out.exists()
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["human_label"] == "correct"
    assert first["human_note"] == "ok"
    assert first["reviewer_id"] == "sergio"
    assert first["confidence"] == "high"
    assert first["time_spent_s"] == 4.2
    assert "T" in first["timestamp"]
    # unchanged records still in file
    assert json.loads(lines[1])["human_label"] is None


def test_annotate_validates_inputs(sample_file: Path):
    store = AnnotationStore(sample_file)
    with pytest.raises(ValueError):
        store.annotate(0, "maybe")  # invalid label
    with pytest.raises(ValueError):
        store.annotate(0, "correct", confidence="absolute")  # invalid confidence
    with pytest.raises(ValueError):
        store.annotate(0, "correct", time_spent_s=-1)
    with pytest.raises(IndexError):
        store.annotate(999, "correct")


def test_clear_annotation(sample_file: Path):
    store = AnnotationStore(sample_file)
    store.annotate(0, "correct", human_note="x")
    assert store.done == 1
    store.clear(0)
    assert store.done == 0
    rec = store.record_at(0)
    assert rec["human_label"] is None
    assert rec["human_note"] is None


def test_resume_reads_prior_annotations(sample_file: Path):
    # First session
    s1 = AnnotationStore(sample_file)
    s1.annotate(0, "correct", human_note="n1", time_spent_s=2.0)
    s1.annotate(2, "incorrect", confidence="low", time_spent_s=5.0)

    # Second session: same sample file, new store
    s2 = AnnotationStore(sample_file)
    assert s2.total == 3
    assert s2.done == 2
    r0 = s2.record_at(0)
    r2 = s2.record_at(2)
    assert r0["human_label"] == "correct"
    assert r0["human_note"] == "n1"
    assert r2["human_label"] == "incorrect"
    assert r2["confidence"] == "low"
    # r1 still pending
    assert s2.record_at(1)["human_label"] is None
    assert s2.first_pending_index() == 1


def test_empty_note_stored_as_none(sample_file: Path):
    store = AnnotationStore(sample_file)
    store.annotate(0, "correct", human_note="   ")
    assert store.record_at(0)["human_note"] is None


# ---------------------------------------------------------------------------
# HTTP server end-to-end
# ---------------------------------------------------------------------------

def _get_json(conn: http.client.HTTPConnection, path: str) -> dict:
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    assert resp.status == 200, f"{path} -> {resp.status}: {body}"
    return json.loads(body)


def _post_json(conn: http.client.HTTPConnection, path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    text = resp.read().decode("utf-8")
    assert resp.status == 200, f"{path} -> {resp.status}: {text}"
    return json.loads(text)


def test_server_full_roundtrip(sample_file: Path):
    store = AnnotationStore(sample_file)
    server, thread = serve_in_thread(store, port=0)
    host, port = server.server_address[:2]
    try:
        # tiny wait for thread to start accepting
        time.sleep(0.05)
        conn = http.client.HTTPConnection(host, port, timeout=5)

        # GET / index.html
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
        html = resp.read().decode("utf-8")
        assert "revisor" in html
        assert "Evidencia" in html or "evidencia" in html.lower()

        # state
        state = _get_json(conn, "/api/state")
        assert state["total"] == 3
        assert state["done"] == 0

        # get record 0
        rec = _get_json(conn, "/api/record/0")
        assert rec["record"]["variable"] == "bbps"

        # annotate via POST
        out = _post_json(conn, "/api/annotate", {
            "index": 0, "human_label": "correct", "human_note": "via http",
            "confidence": "medium", "time_spent_s": 3.5,
        })
        assert out["done"] == 1
        assert out["record"]["human_label"] == "correct"
        assert out["record"]["confidence"] == "medium"

        # persisted?
        assert store.annotated_path.exists()
        first = json.loads(
            store.annotated_path.read_text(encoding="utf-8").splitlines()[0]
        )
        assert first["human_label"] == "correct"
        assert first["human_note"] == "via http"

        # clear
        out = _post_json(conn, "/api/clear", {"index": 0})
        assert out["done"] == 0
        assert out["record"]["human_label"] is None

        # invalid label -> 400
        conn.request(
            "POST", "/api/annotate",
            body=json.dumps({"index": 0, "human_label": "bogus"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 400

        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_server_output_format_matches_schema(sample_file: Path):
    """Output JSONL deve conter todos os campos originais + os humanos."""
    store = AnnotationStore(sample_file)
    server, thread = serve_in_thread(store, port=0)
    host, port = server.server_address[:2]
    try:
        time.sleep(0.05)
        conn = http.client.HTTPConnection(host, port, timeout=5)
        _post_json(conn, "/api/annotate", {
            "index": 1, "human_label": "incorrect", "human_note": "IA errou",
            "confidence": "high", "time_spent_s": 12.4,
        })
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    lines = store.annotated_path.read_text(encoding="utf-8").strip().splitlines()
    rec = json.loads(lines[1])
    # original fields preserved
    for k in ("variable", "id_registro", "resolution", "value", "status",
              "section", "evidence", "evidence_class", "certainty",
              "ia_value", "strata"):
        assert k in rec, f"campo original {k} sumiu do output"
    # new fields
    assert rec["human_label"] == "incorrect"
    assert rec["human_note"] == "IA errou"
    assert rec["reviewer_id"] == "sergio"
    assert rec["confidence"] == "high"
    assert rec["time_spent_s"] == 12.4
    assert isinstance(rec["timestamp"], str) and "T" in rec["timestamp"]
