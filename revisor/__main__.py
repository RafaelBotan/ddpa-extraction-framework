"""Entry point: python -m revisor <caminho-do-jsonl> [--port N] [--no-browser]"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from .server import serve_blocking
from .store import AnnotationStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="revisor",
        description="Ferramenta local de auditoria humana para amostras DDPA.",
    )
    parser.add_argument(
        "sample",
        help="Caminho para silent_consensus_sample.jsonl (ou equivalente).",
    )
    parser.add_argument("--port", type=int, default=8765, help="Porta HTTP local (default 8765).")
    parser.add_argument("--reviewer", default="sergio", help="Identificador do revisor.")
    parser.add_argument("--no-browser", action="store_true", help="Nao abrir browser automaticamente.")
    parser.add_argument(
        "--text-csv",
        default=None,
        help="CSV com id_registro + texto_laudo_completo para exibir laudo integral (opcional).",
    )
    parser.add_argument(
        "--text-col",
        default="texto_laudo_completo",
        help="Nome da coluna de texto no CSV (default: texto_laudo_completo).",
    )
    parser.add_argument(
        "--id-col",
        default="id_registro",
        help="Nome da coluna de ID no CSV (default: id_registro).",
    )
    args = parser.parse_args(argv)

    sample_path = Path(args.sample).resolve()
    if not sample_path.exists():
        print(f"[revisor] ERRO: arquivo nao encontrado: {sample_path}", file=sys.stderr)
        return 2

    text_csv = Path(args.text_csv).resolve() if args.text_csv else None
    if text_csv and not text_csv.exists():
        print(f"[revisor] AVISO: CSV nao encontrado: {text_csv} (seguindo sem laudo completo)", file=sys.stderr)
        text_csv = None

    store = AnnotationStore(
        sample_path,
        reviewer_id=args.reviewer,
        text_csv_path=text_csv,
        text_col=args.text_col,
        id_col=args.id_col,
    )

    if not args.no_browser:
        try:
            webbrowser.open(f"http://127.0.0.1:{args.port}/")
        except Exception:  # pragma: no cover
            pass

    serve_blocking(store, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
