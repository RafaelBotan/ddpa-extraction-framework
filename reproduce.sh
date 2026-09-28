#!/usr/bin/env bash
set -euo pipefail
# Runs the automated test suite (synthetic fixtures; 470 pass, 3 are skipped because they need the private corpora).
pip install -r environment/requirements.txt
pytest tests/
# The pilot counts reported in the manuscript are in pilot/outputs/independent_recount_NO_PHI.json.
# The pilot scripts (pilot/run_pilot.py, pilot/independent_recount.R) document the procedure; they read report texts and
# field-level outputs that are not shared and cannot be re-run from this repository.
