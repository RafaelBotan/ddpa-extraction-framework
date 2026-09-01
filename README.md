# DDPA — Disagreement-Driven Policy Adjudication

Safety-gated, contract-first framework for auditable extraction of structured
data from free-text pathology and endoscopy reports (Brazilian Portuguese).
Companion repository of the manuscript submitted to JAMIA (Research and
Applications, 2026): deterministic anchor detectors act as independent auditors
of language-model extractions; disagreements are adjudicated as recurring error
types; a grounding filter and a five-barrier sandbox (plus viability gate)
refuse to encode model output the source text does not support.

## Contents

| Path | What it is |
|---|---|
| `framework_core.py`, `runtime/` | contract-driven runner, resolution contract, policy registry, gates |
| `runtime/sandbox/` | the five safety barriers + viability gate (patch cards) |
| `runtime/observability/` | cross-run dashboards, drift alerting, pattern discovery |
| `*_l1_v4.py` | the 12 deterministic detectors (5 pathology domains, endoscopy variables) |
| `contracts/` | study contracts — including the full structured prompts, verbatim |
| `policies/` | policy registry files |
| `tests/` | automated suite (run `pytest tests/`: 450 passed, 3 skipped) |
| `docs/L1_PLAYBOOK.md` | the catalogue of 32 recurring error patterns |
| `run_manifests/` | hash-bearing manifests of the production runs cited in the paper |
| `sandbox_reports/` | patch-card JSON of the rule-synthesis ablation (Table S1) |
| `sampling/` | the stratified-sampling scripts for the development sets |
| `scripts/run_sandbox.py` | reproduces the ablation arms (`--use-pattern-family`, `--force-trivial-regex`, `--min-family-coverage 0.10`) |
| `revisor/` | the type-level adjudication interface used by the pathologist governor |

## What this public copy withholds, and why

- **No report text and no individual-level data.** Clinical reports — even
  pseudonymised — are not shared, and neither are per-report rows. In
  `sandbox_reports/`, every per-report evidence list (`evidence_windows`,
  `evidence_discovery`, `evidence_eval`) is replaced by an aggregate summary
  `{n, note}`; value distributions, barrier results and every decision are
  retained, so the ablation remains auditable end to end. De-identified
  development and validation datasets are available from the corresponding
  author on reasonable request.
- **Neutral source codes.** Identifiers of the contributing clinics and
  laboratories, and local filesystem paths, were replaced by neutral codes
  ("Clinic A", "Lab Z", `<path>`) in this public copy. The substitution is
  purely textual; the run manifests record the hashes of the exact runtime
  files, which differ from these public copies only by that substitution.
- The test suite runs standalone on synthetic fixtures; the skipped tests
  require the private corpora.

## Reproducing

```bash
pip install -r environment/requirements.txt
pytest tests/          # 450 passed, 3 skipped
python scripts/run_sandbox.py --variable polipo_tamanho_max_mm --use-pattern-family [--force-trivial-regex] [--min-family-coverage 0.10]
```

Running extraction end-to-end requires a report corpus (not shared); the
contracts in `contracts/` document every prompt and variable panel verbatim.

## Ethics

Secondary, de-identified reports under research-ethics approval Parecer
8.342.176 (CAAE 96930626.0.0000.0257, Plataforma Brasil), with waiver of
individual informed consent; pseudonymisation at source. See `ETHICS.md`.

## Licenses

Code: MIT (`LICENSE`). Aggregate/derived data artefacts: CC-BY-4.0
(`LICENSE-DATA.md`).
