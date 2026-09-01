"""
state_store — checkpoint JSONL append-only por variável (GPT-5 R5 item #4).

Durante `run_variable`, cada linha processada é gravada em `<var>.jsonl` no
diretório do run. Em caso de interrupção, `--resume <run_dir>` relê os JSONL
existentes, pula ids já processados e continua. Ao final, o JSONL é convertido
para CSV final e removido.

Invariantes:
- `id` (coluna id_registro do corpus) identifica a linha.
- Append-only: nunca sobrescreve. Dedup por id é responsabilidade do reader.
- Se uma linha aparecer duplicada no JSONL (crash entre append e flush), a
  última ocorrência vence.
- Após `finalize`, o JSONL é descartado; CSV é a fonte de verdade.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Iterator


def checkpoint_path(out_dir: Path, variable_name: str) -> Path:
    return out_dir / f'{variable_name}.jsonl'


def load_processed(path: Path, id_key: str) -> dict[Any, dict]:
    """Retorna {id: last_row_dict} a partir de um JSONL. Vazio se não existir."""
    out: dict[Any, dict] = {}
    if not path.exists():
        return out
    with path.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if id_key in row:
                out[row[id_key]] = row
    return out


def append_row(path: Path, row: dict) -> None:
    """Append JSON line. Abre/fecha a cada chamada para garantir flush."""
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(row, default=str, ensure_ascii=False))
        f.write('\n')


def iter_rows(path: Path) -> Iterator[dict]:
    with path.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def finalize(path: Path, csv_path: Path, id_key: str) -> int:
    """Compacta JSONL (dedup por id, última ocorrência vence) em CSV. Remove JSONL.
    Retorna N linhas compactadas."""
    import pandas as pd
    rows = load_processed(path, id_key)
    df = pd.DataFrame(list(rows.values()))
    df.to_csv(csv_path, index=False)
    path.unlink()
    return len(df)
