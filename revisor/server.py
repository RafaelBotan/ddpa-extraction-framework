"""Servidor HTTP local do revisor. Stdlib only.

Rotas:
  GET  /              -> HTML SPA (index page)
  GET  /api/state     -> { total, done, reviewer_id }
  GET  /api/record/<i>-> registro no indice i (ja com campos human_*)
  POST /api/annotate  -> body JSON {index, human_label, human_note?, confidence?, time_spent_s?}
  POST /api/clear     -> body JSON {index}  (remove anotacao)
  GET  /api/export    -> devolve o annotated.jsonl cru para debug
"""
from __future__ import annotations

import json
import math
import socket
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .store import AnnotationStore

HTML_PATH = Path(__file__).with_name("index.html")
LEGEND_PATH = Path(__file__).with_name("prompt_legend.json")


def _load_legend() -> dict[str, Any]:
    try:
        return json.loads(LEGEND_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(_sanitize(payload), ensure_ascii=False, allow_nan=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    raw = handler.rfile.read(length) if length > 0 else b""
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def make_handler(store: AnnotationStore, html: str):
    class Handler(BaseHTTPRequestHandler):
        # --- silence default logging ---
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            return

        # --- GET ---
        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            try:
                if path == "/" or path == "/index.html":
                    # Re-le do disco para permitir hot-reload durante ajustes de UI.
                    try:
                        data = HTML_PATH.read_text(encoding="utf-8").encode("utf-8")
                    except OSError:
                        data = html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if path == "/api/state":
                    _json_response(self, 200, {
                        "total": store.total,
                        "done": store.done,
                        "reviewer_id": store.reviewer_id,
                        "first_pending": store.first_pending_index(),
                    })
                    return
                if path.startswith("/api/record/"):
                    try:
                        idx = int(path.rsplit("/", 1)[1])
                    except ValueError:
                        _json_response(self, 400, {"error": "invalid index"})
                        return
                    try:
                        rec = store.record_at(idx)
                    except IndexError as exc:
                        _json_response(self, 404, {"error": str(exc)})
                        return
                    legend = _load_legend().get(rec.get("variable"), {})
                    full_text = store.full_text_for(rec.get("id_registro"))
                    _json_response(self, 200, {
                        "index": idx,
                        "record": rec,
                        "legend": legend,
                        "full_text": full_text,
                        "total": store.total,
                        "done": store.done,
                    })
                    return
                if path == "/api/export":
                    data = "\n".join(
                        json.dumps(r, ensure_ascii=False) for r in store.records()
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_error(404, "Not Found")
            except Exception as exc:  # pragma: no cover — defensive
                _json_response(self, 500, {"error": f"{type(exc).__name__}: {exc}"})

        # --- POST ---
        def do_POST(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            try:
                if path == "/api/annotate":
                    body = _read_json_body(self)
                    try:
                        rec = store.annotate(
                            index=int(body["index"]),
                            human_label=str(body["human_label"]),
                            human_note=body.get("human_note"),
                            confidence=str(body.get("confidence", "high")),
                            time_spent_s=float(body.get("time_spent_s", 0.0) or 0.0),
                        )
                    except (KeyError, ValueError, TypeError) as exc:
                        _json_response(self, 400, {"error": f"{type(exc).__name__}: {exc}"})
                        return
                    except IndexError as exc:
                        _json_response(self, 404, {"error": str(exc)})
                        return
                    _json_response(self, 200, {
                        "record": rec, "total": store.total, "done": store.done,
                    })
                    return
                if path == "/api/clear":
                    body = _read_json_body(self)
                    try:
                        rec = store.clear(int(body["index"]))
                    except (KeyError, ValueError, TypeError) as exc:
                        _json_response(self, 400, {"error": f"{type(exc).__name__}: {exc}"})
                        return
                    except IndexError as exc:
                        _json_response(self, 404, {"error": str(exc)})
                        return
                    _json_response(self, 200, {
                        "record": rec, "total": store.total, "done": store.done,
                    })
                    return
                self.send_error(404, "Not Found")
            except Exception as exc:  # pragma: no cover
                _json_response(self, 500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def _find_free_port(preferred: int = 8765) -> int:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", preferred))
            return preferred
    except OSError:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def build_server(store: AnnotationStore, port: int = 8765) -> ThreadingHTTPServer:
    html = HTML_PATH.read_text(encoding="utf-8")
    handler_cls = make_handler(store, html)
    actual_port = _find_free_port(port)
    server = ThreadingHTTPServer(("127.0.0.1", actual_port), handler_cls)
    return server


def serve_blocking(store: AnnotationStore, port: int = 8765) -> None:
    server = build_server(store, port)
    host, p = server.server_address[:2]
    print(f"[revisor] sample    : {store.sample_path}")
    print(f"[revisor] annotated : {store.annotated_path}")
    print(f"[revisor] total     : {store.total} (ja anotados: {store.done})")
    print(f"[revisor] servindo  : http://{host}:{p}/")
    print("[revisor] Ctrl+C para encerrar")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[revisor] encerrando...")
    finally:
        server.server_close()


def serve_in_thread(store: AnnotationStore, port: int = 8765) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """Uso em testes: roda servidor em thread daemon."""
    server = build_server(store, port)
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    return server, th
