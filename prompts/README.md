# Extraction prompts (verbatim)

The five system prompts below are the language-model side of the endoscopy
cross-audits reported in the paper, published verbatim as run (Brazilian
Portuguese, the language of the reports):

| File | Role |
|---|---|
| `colonoscopy_main_22vars_gpt41mini.md` | main production read, 67,816 colonoscopy reports |
| `colonoscopy_repescagem_15vars_gpt41mini.md` | rescue pass (additional variables) |
| `colonoscopy_bunified_followup_gpt41mini.md` | unified follow-up re-extraction (adopted refinement) |
| `upperGI_main_20vars_gpt41mini.md` | main production read, 128,167 upper-GI reports |
| `upperGI_repescagem_15vars_gpt41mini.md` | rescue pass |

The runtime's own cross-family arbitrator prompt template ships with the code
(`runtime/cascade.py`, `GPT5_PROMPT_TEMPLATE`; gated, one variable per call,
literal evidence span required).

The pathology development cross-audits used the archive's pre-existing
language-model extractions of the same reports as the model side; those
prompts belong to the source-base extraction pipeline and are available from
the corresponding author on reasonable request.
