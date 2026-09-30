# DDPA — Disagreement-Driven Policy Adjudication

Contract-first framework for auditable extraction of structured data from free-text pathology and endoscopy reports
written in Brazilian Portuguese. Deterministic detectors that abstain rather than guess are paired with a language model.
Their outputs are compared field by field, and each field receives an explicit state: accepted by agreement, routed to
review, or without an answer. Recurring types of disagreement can be resolved as policies, and any rule proposed for a
detector must pass a sandbox that ends in a regression gate.

This repository accompanies the manuscript *Agreement and abstention for selective expert review in automated pathology
report extraction: a retrospective pilot study* (submitted to JAMIA, Research and Applications, 2026).

## Releases

- **v2.2.1 (this release):** the same frozen framework code as v2.2, plus the scripts and aggregate outputs of the post-hoc analyses added during review (`pilot/conditional_discordance_by_reader_state*`, `pilot/destinations_by_category_and_cases*`, with their R recounts): each reader's discordance by the other reader's state (manuscript Table 3), the destination of each annotated category (Supplementary Table S16), fields without an answer against the annotation, and routed fields per breast case. No detector or runtime file changed.
- **v2.2:** the frozen framework version evaluated in the retrospective pilot. The frozen set has 57 files, set hash `6333ade35add72553a32426af00ee9013a5c89d56a60c45c9e28d3240714d340`. This release adds:
  - the sandbox regression gate;
  - the pilot scripts;
  - the independent recount written in R;
  - the aggregate outputs behind the manuscript's Tables 1 and 2, Figure 2 and the discordant cells of Supplementary Table S16.
- **v1.1 / v1.0:** earlier development releases. v1.1 added the endoscopy extraction prompts.

## Contents

| Path | What it is |
|---|---|
| `framework_core.py`, `runtime/` | contract-driven runner, resolution states, policy registry, gates |
| `runtime/sandbox/` | viability gate, blocking checks (canaries, target hit, near misses), telemetry, and the regression gate (`regression.py`) |
| `runtime/observability/` | cross-run dashboards, drift alerting, pattern discovery |
| `*_l1_v4.py` | the deterministic detectors (pathology and endoscopy variables) |
| `contracts/` | per-domain study contracts (variable panels, canary examples, policies) |
| `policies/` | policy registry files |
| `prompts/` | the endoscopy extraction system prompts, verbatim as run |
| `regression_sets/` | `MANIFEST.json` (hash and composition of each versioned regression set); `examples/` holds the 14 synthetic regression cases, outside the directory the gate reads, so `scripts/run_sandbox.py` still refuses to run without the full set |
| `pilot/run_pilot.py` | pilot runner: calls the frozen detectors and the language model and applies the review-selection rule |
| `pilot/independent_recount.R` | separate implementation in R that recomputed every pilot count from the stored field-level outputs |
| `pilot/accepted_confusion_matrices.py` | accepted value × annotation cells (the discordant-cells column of Supplementary Table S16) |
| `pilot/PROMPTS_pilot.md` | the three language-model prompts used in the pilot, verbatim |
| `pilot/outputs/` | aggregate outputs: independent recount, accepted-value matrices, distribution of the reference annotations |
| `tests/` | automated suite (`pytest tests/`: 470 passed, 3 skipped) |
| `docs/L1_PLAYBOOK.md` | catalogue of recurring error patterns documented during development |
| `run_manifests/` | hash-bearing manifests of the production runs cited in the supplement |
| `sandbox_reports/` | patch-card records of the historical rule-synthesis runs |
| `sampling/` | stratified-sampling scripts for the development sets |
| `scripts/run_sandbox.py` | runs the sandbox on candidate rules |
| `revisor/` | the type-level adjudication interface |

## What this public copy withholds, and why

- **No report text and no individual-level data.** Clinical reports, even pseudonymised, are not shared, and neither are per-report or per-field rows. In `sandbox_reports/`, every per-report evidence list is replaced by an aggregate summary; value distributions, barrier results and every decision are retained.
- **Regression sets.** The versioned regression set used by the gate (v2, 319 cases) contains 305 cases built from real report text. They are withheld; `regression_sets/MANIFEST.json` records their SHA-256 and composition. The 14 synthetic cases are published in `regression_sets/examples/`.
- **Neutral codes.** Identifiers of the contributing clinics and laboratories, annotators and source studies, and local filesystem paths were replaced by neutral codes in this public copy. The codes are "Clinic A", "Lab Z", `LAB_A`–`LAB_C` (the laboratories A–C of the manuscript), `annotator1`/`annotator2`, `breast_study`, `<path>` and `<local path>`; dates in the pilot runner are written as YYYY-MM-DD. The substitution is purely textual; the frozen-set hash above refers to the original files.
- **The pilot scripts cannot be re-run from this repository**, because they read report texts and field-level outputs that are not shared. They are the versions stored with the pilot outputs, unchanged apart from the substitution above and one line of the runner split in two, without change in behaviour, so that a secret scanner does not mistake reading the API key from a local file for a key written in the code. They document the procedure. The aggregate outputs they produced are in `pilot/outputs/`.
- **Test suite.** It runs standalone on synthetic fixtures; the 3 skipped tests require the private corpora.

## Known limitation of the frozen regression gate

For `metamorphic_invariance` cases, the expected output is the proposal's own output on the unperturbed text. When the
baseline detector abstains on both texts and a proposed rule answers only on the unperturbed one, the difference is
attributed to the baseline (`pre_existing`) and does not block the rule, although the rule introduced it. The limitation
was found in review after the freeze; it is documented here and left uncorrected in v2.2 so that this release remains
the version evaluated.

## Reproducing

```bash
pip install -r environment/requirements.txt
pytest tests/          # 470 passed, 3 skipped
python scripts/run_sandbox.py --help
```

Every count in the manuscript's Tables 1 and 2 can be checked against `pilot/outputs/independent_recount_NO_PHI.json`.

## Ethics

Secondary use of de-identified reports:
- Pathology (including the pilot): approval 8.342.120 (CAAE 95874226.3.0000.0257, 8 April 2026).
- Endoscopy: approval 8.342.176 (CAAE 96930626.0.0000.0257, 8 April 2026).

Both approvals were issued by the Research Ethics Committee of Hospital Daher, Brasília, Brazil, and both waive individual informed consent. See `ETHICS.md`.

## Licenses

Code: MIT (`LICENSE`). Aggregate/derived data artefacts: CC-BY-4.0 (`LICENSE-DATA.md`).
