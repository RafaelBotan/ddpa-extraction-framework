"""Coloca runtime/ no sys.path para os tests importarem direto."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / 'runtime'
for p in (ROOT, RUNTIME):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)
