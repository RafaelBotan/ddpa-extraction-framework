# Data dictionary (aggregate files)

| File | Content |
|---|---|
| `pilot/outputs/independent_recount_NO_PHI.json` | Per set (cervical; thyroid arms A and B1; breast immunohistochemistry) and variable: units; fields accepted by agreement; discordance among accepted fields (count, percentage, exact 95% CI); fields routed to review and without an answer; composition of routing (detector silent, readers disagreed, model silent); capture of the model's discordant answers; coverage and discordance of the detector alone, the model alone and the model-first hybrid. Counts only. |
| `pilot/outputs/accepted_confusion_matrices_NO_PHI.json` | Per set and variable, the counts of accepted value × annotated value among fields accepted by agreement (Ki-67: discordant count and absolute differences). Counts only. |
| `pilot/outputs/reference_distribution_NO_PHI.json` | Distribution of the reference annotations by category in each set (counts; for Ki-67, median and quartiles). |
| `regression_sets/MANIFEST.json` | SHA-256, size and composition (by case type) of each versioned regression set. |
| `regression_sets/examples/synthetic_cases_polipo_tamanho_max_mm.json` | The 14 synthetic regression cases (synthetic sentences, expected value and note). |
