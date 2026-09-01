"""Load and validate DDPA run manifests from a runs directory."""
from __future__ import annotations
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_manifests(runs_dir: Path | str) -> list[dict]:
    """Scan runs_dir recursively for run_manifest.json files.
    Returns list of manifest dicts sorted by started_at ascending.
    Each dict gets an extra '_run_dir' key with the parent directory path.
    Invalid JSON files are skipped with a warning.
    """
    runs_dir = Path(runs_dir)
    manifests = []
    for manifest_path in sorted(runs_dir.rglob('run_manifest.json')):
        try:
            raw = manifest_path.read_text(encoding='utf-8')
            data = json.loads(raw)
            data['_run_dir'] = str(manifest_path.parent)
            manifests.append(data)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning('Skipping invalid manifest %s: %s', manifest_path, exc)
    manifests.sort(key=lambda m: m.get('started_at', ''))
    return manifests


def filter_by_contract(
    manifests: list[dict],
    contract_id: str,
    prefix: bool = False,
) -> list[dict]:
    """Filter manifests by contract_id (exact match or prefix match)."""
    if prefix:
        return [m for m in manifests if m.get('contract_id', '').startswith(contract_id)]
    return [m for m in manifests if m.get('contract_id') == contract_id]
