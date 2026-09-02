# Upper-GI endoscopy — rescue pass

**Model:** GPT-4.1-mini, temperature 0. **Use:** rescue pass over the upper-GI corpus. Extracted verbatim from the production script's `SYSTEM_PROMPT` (sha256 of the exact string: `a39212ce7f8801e9e90705a4c2a50f257c9ea09177889cc0f27d788e158cbbb4`); published as run. Field counts in this rescue prompt are as run; any internal count-vs-list mismatch is preserved verbatim. Prompts are in Brazilian
Portuguese, the language of the reports.

```text
Extract variables from Brazilian upper endoscopy reports. Return JSON array only.
Keys per object (15 + idx + id_registro = 17 total):

idx: (copy)
id_registro: (copy)

ind: indication category. Read [INDICAÇÃO] section FIRST. Map to: "disp"/"drge"/"disf"/"epig"/"sang"/"anem"/"pp"/"ctrl"/"rast"/"preop"/"out"/"ni"
  ni = no indication found anywhere in text

drge: "1"/"0" — text mentions GERD/refluxo/pirose/azia as indication or context

ee2: second opinion on esophagitis. "def"/"sem"/"ind"
  "def": explicit erosive esophagitis/erosions in esophagus/LA grade mentioned
  "sem": normal esophagus explicitly stated in findings/conclusion, OR "esofagite não erosiva"/"esofagite de refluxo não erosiva" in conclusion
  "ind": truly ambiguous/insufficient

gp: gastritis present "1"/"0"
gt: gastritis type "enan"/"eros"/"atrof"/"hiper"/"hem"/"out"/null
gi: gastritis intensity "lev"/"mod"/"int"/null
gg: gastritis topography "ant"/"corp"/"fund"/"pan"/null

vz: esophageal varices "1"/"0"
vc: varices caliber "fin"/"med"/"gro"/null

en: esophagus normal "1"/"0"
sn: stomach normal "1"/"0"
dn: duodenum normal "1"/"0"

pg: gastric polyp "1"/"0"
pl: polyp location "fund"/"corp"/"ant"/"card"/null

RULES:
1. Extract from text only. Never invent.
2. For ind: ALWAYS check [INDICAÇÃO] section first. Many texts have it.
3. For ee2: "esofagite não erosiva" or "esofagite de refluxo não erosiva" = "sem" (NOT "def")
4. For ee2: "mucosa normal"/"mucosa branco nacarada"/"sem erosões" in esophagus = "sem"
5. No content → "0"/null/"ni" as appropriate
6. Use short values to minimize output tokens
```
