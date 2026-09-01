# Playbook: Construcao de Detectores L1 Deterministicos

**Versao:** 4.0 (2026-04-13)
**Origem:** 5 dominios patologia (mama AP, mama IHQ, gastrico OLGA, tireoide Bethesda, cervical Bethesda), 55 variaveis, 717+ smoke tests
**Resultado:** Zero observed residual L1 errors on adjudicated extractable cases across all 5 domains. 32 bug patterns (29 prior + 3 new). Cross-domain validated.
**Objetivo:** Reproduzir o processo de construcao de L1 em qualquer dominio novo, incorporando experiencia acumulada desde o inicio.

**IMPORTANTE:** O n=200 usado para iterar e um *development adjudication set*, NAO um holdout congelado. O L1 foi corrigido com base nos erros encontrados nesse conjunto. Validacao congelada requer holdout separado com freeze point.

---

## 0. Filosofia

O L1 e uma **testemunha conservadora**: nao inventa, nao infere, nao alucina. Retorna `None` quando nao tem certeza. O objetivo NAO e "100% de concordancia bruta" — e **100% de acuracia no denominador correto** (dados realmente extraiveis).

### Metrica correta: Resolution Contract

Cada par (laudo, variavel) termina em UM estado:

| Estado | Conta no denominador? | Significado |
|---|---|---|
| accepted_exact | SIM | Valor extraido corretamente |
| accepted_semantic | SIM | Valor semanticamente equivalente |
| abstained_unsupported | NAO | Dado nao esta no texto |
| abstained_source_incomplete | NAO | Texto defeituoso (ex: medida sem unidade) |
| escalated_policy | NAO (ate decisao) | Ambiguidade de politica (ex: fibroadenoma vs fibrocistico) |
| escalated_case | NAO | Caso individual requer humano |

**Abstencao e escalacao contam como SUCESSO do sistema.**

O alvo e: `accepted / (accepted + erros) = 100%`

Tudo que nao e extraivel sai do denominador. Se o sistema nao consegue decidir, ele diz "nao sei" — e isso e correto.

### 4 Metricas de Report (nao apenas 2)

| Metrica | Formula | O que mostra |
|---|---|---|
| **Surface agreement** | agree / total | Concordancia bruta L1 vs LLM |
| **Adjusted exactness** | accepted / (accepted + erros) | Acuracia no extractable |
| **Resolution coverage** | (accepted + erros) / total | Fracao in-scope resolvida |
| **Exclusion profile** | breakdown por policy/case/src/unsupported | Maturidade da variavel |

**Por que 4:** Surface agreement esconde exclusoes. Adjusted exactness esconde quanto foi excluido. Resolution coverage mostra o quanto do espaço o L1 realmente cobre. Exclusion profile mostra onde estao os gaps restantes.

---

## 1. Workflow por Variavel (7 etapas)

### Etapa 1: Vocab Mining
- Ler 20-50 laudos reais (nao amostrar, ler inteiros)
- Listar TODOS os termos que expressam o conceito da variavel
- Incluir: sinonimos, abreviacoes, variantes de grafia, formas negadas
- **Armadilha PT-BR:** accents, OCR errors, "NÃO EVIDENCIADO"="ausente", "COMPATÍVEL COM"="presente"

### Etapa 2: Classificar a variavel
| Classe | Estrategia L1 | Exemplo |
|---|---|---|
| BIN (presenca binaria) | Negacao-first + positive patterns | comp_insitu, inv_linfov |
| CAT (categoria) | Cascata especifico→generico | tipo_hist, procedimento |
| CAT-ORD (ordinal) | Pattern direto + normalizacao | pT, pN, grau_nott |
| MEAS-N (medida numerica) | Label → medida → unidade | tam_cm, distancia_margem |
| NUM (contagem) | Pattern + parse_number | ln_exam, ln_pos |
| TEXT (texto literal) | Copy + normalize | localizacao |

### Etapa 3: Construir detector inicial
Principios obrigatorios:
1. **Negacao ANTES de positivo** — NUNCA testar positivo primeiro
2. **Evidence-first** — retornar o texto que casou, nao so o rotulo
3. **Section-aware** — usar conclusao, nao introducao/material
4. **Conservative** — se nao tem certeza, retornar None

### Etapa 4: Smoke tests
- Escrever 5-10 testes por variavel (positivo, negativo, edge case)
- Incluir: negacao, variante de grafia, secao errada, formato atipico
- Rodar antes de cada iteracao

### Etapa 5: Rodar no development adjudication set
- Comparar L1 vs LLM (ou reference standard)
- **NAO chamar de "holdout"** — o L1 esta sendo corrigido com base nos erros deste conjunto
- Classificar cada discordancia:
  - **agree** — ambos extrairam o mesmo valor
  - **L1-only** — L1 extraiu, LLM nao → verificar amostra (pode ser L1 correto, mas pode ser ontology collision como Bug #28)
  - **LLM-only** — LLM extraiu, L1 nao → gap de cobertura do L1
  - **disagree** — ambos extrairam valores diferentes → adjudicar

### Etapa 6: Adjudicar discordancias
Para cada disagree, ler o texto e classificar:
- L1 correto → nenhuma acao
- LLM correto → fix no L1 (novo pattern, bug fix)
- Ambiguo / policy → escalated_policy (remover do denominador)
- Texto defeituoso → abstained_source_incomplete

### Etapa 7: Auditar concordancias (cego para erro compartilhado)
O DDPA so detecta erros quando L1 e LLM discordam. Se AMBOS erram da mesma forma, ninguem percebe. Para mitigar:
- Amostrar 40 concordancias com valor (both have, same value)
- Amostrar 10 concordancias both-None (verificar se dado realmente nao esta no texto)
- Amostrar 20 concordancias de variáveis high-risk (tipo_hist, tam_cm, procedimento)
- Total minimo: **70 casos** de auditoria de concordancias
- Ler os textos reais — comparar com valor extraido por ambos

### Etapa 8: Iterar ate convergencia
Criterio de parada: **todas as discordancias adjudicadas, denominador limpo, acuracia = 100% no extractable, auditoria de concordancias sem erro sistematico**.

---

## 2. Padroes de Bug Universais (transferem entre dominios)

### 2.1 Negacao-cego (P1 critico)
**Bug:** Testar positivo ANTES de negativo no if-else.
**Resultado:** 100% FP em variáveis como inv_linfov, inv_perin.
**Exemplo:** "NÃO EVIDENCIADA INVASÃO LINFOVASCULAR" → regex matcha "INVASÃO LINFOVASCULAR" (positivo) antes de ver o "NÃO".
**Fix:** Sempre check negation patterns FIRST. Idealmente usar span-based resolution.

### 2.2 Cross-section contamination
**Bug:** Snippet de 120+ chars captura texto de outra secao do laudo.
**Resultado:** "MARGENS LIVRES" contamina ln_pos; "LINFONODOS COMPROMETIDOS" contamina margens.
**Exemplo:** "MARGENS CIRÚRGICAS LIVRES. LINFONODOS: 1/3 COMPROMETIDOS" — regex de margem captura "COMPROMETIDOS".
**Fix:** Truncar snippet em section boundaries (linfonodo, mamilo, pele, numbered sections).

### 2.3 First-match-wins em multifocal
**Bug:** `re.search` retorna primeiro match, nao o clinicamente correto.
**Resultado:** Em laudos multifocais, extrai o menor tumor em vez do maior.
**Exemplo:** 2 focos (0,4 cm e 0,5 cm) — regex pega 0,4 cm (primeiro).
**Fix:** Usar `re.finditer`, coletar todos os matches, retornar o maior (para staging).

### 2.4 Formato dual (X cm = Y mm)
**Bug:** Regex captura uma metade da notacao dual.
**Resultado:** "33 mm" em vez de "3,3 x 2,5 x 2,3 cm" (mesmo valor, formatos diferentes).
**Fix:** Normalizar na comparacao (converter ambos para mm, comparar com tolerancia).

### 2.5 Variante morfologica sem word boundary
**Bug:** `fibroadenoma` matcha dentro de `fibroadenomatóide`.
**Resultado:** FP em fibroadenoma quando o diagnostico e hiperplasia fibroadenomatoide.
**Fix:** `fibroadenoma\b` com word boundary.

### 2.6 Preposicao portuguesa faltando
**Bug:** `d[ao]` nao captura "de" (da=de+a, do=de+o, de=plain).
**Resultado:** "TAMANHO DE NEOPLASIA" nao matchado.
**Fix:** `d[aoe]` para cobrir todas as preposicoes portuguesas.

### 2.7 Letra O vs digito 0
**Bug:** Patologista digita "pNO" (letra O) em vez de "pN0" (digito 0).
**Resultado:** Regex `pn[0-3]` nao matcha.
**Fix:** `pn[o0]` ou normalizar O→0 em contexto de staging.

### 2.8 Prefixo faltando em contexto
**Bug:** "N0" sem "p" em staging line (pT2 N0 Mx).
**Resultado:** Regex `y?pn[0-3]` nao matcha "n0".
**Fix:** Aceitar prefixo opcional quando proximo de pT (contexto de estadiamento).

### 2.9 "Filler words" entre label e valor
**Bug:** "TAMANHO DA LESÃO: ÁREA DE 2,5 x 1,5 cm" — regex espera digito logo apos ":".
**Resultado:** LLM-only (L1 nao extrai).
**Fix:** Permitir filler opcional entre label e medida: `(?:area\s+de\s+)?`.

### 2.10 Unidade faltando no texto
**Bug:** "0,6 x 0,5" sem "cm" ou "mm".
**Resultado:** MEAS_PAT nao matcha (requer unidade).
**Fix:** Nao corrigir — classificar como abstained_source_incomplete. Texto defeituoso.

### 2.11 Catch-all mascara subtipos
**Bug:** "carcinoma" generico matcha CDI_SOE, mascara micropapilar/mucinoso/misto.
**Resultado:** Subtipos especificos classificados como generico.
**Fix:** Cascata especifico→generico: testar subtipos ANTES do catch-all.

### 2.12 Sinonimo clinico nao reconhecido
**Bug:** "intraducto" = sinonimo de "in situ" nao coberto.
**Resultado:** FN em comp_insitu.
**Fix:** Vocab mining extensivo. `intraduct\w*` cobre intraducto/intraductal/intraducta.

### 2.13 Vocab split forms (iter 7)
**Bug:** Mesmo conceito em formas compostas vs separadas: "linfovascular" vs "vascular linfatica".
**Resultado:** 7 FN em inv_linfov (3.5% do holdout).
**Fix:** Listar TODAS as variantes: `(?:linfo[\s-]?vascular|angiolinfatica|vascular\s+linfatica)`.

### 2.14 Verb synonyms (iter 7)
**Bug:** "invasao" e "infiltracao" sao intercambiaveis em histopatologia, mas L1 so cobre um.
**Resultado:** 1 FN em inv_linfov, 1 em inv_perin.
**Fix:** Grupo de verbos: `(?:invasao|infiltracao)`.

### 2.15 Ordinal indicator mismatch (iter 7)
**Bug:** "Nº" (U+00BA, masculine ordinal) vs "No" vs "N." — regex `n[o.]?` nao matcha "nº".
**Resultado:** ref_ihq nao detecta "VIDE EXAME DE IMUNOHISTOQUIMICA Nº 15001245IH".
**Fix:** `n[o.º°]?` para cobrir todas as variantes tipograficas.

### 2.16 Amendment override (iter 7)
**Bug:** Laudos com correcao/aditamento: "NOVO GRAU HISTOLOGICO FINAL: GRAU 1" aparece APOS grau original "GRAU 2".
**Resultado:** First-match retorna grau original, nao o corrigido.
**Fix:** Pattern de alta prioridade para "novo grau histologico final" que sobrescreve matches anteriores.

### 2.17 Pattern-order vs text-position priority (iter 7)
**Bug:** PROC_PATTERNS lista "excisional" antes de "biopsia_agulha". Quando "biopsia excisional" aparece no texto em nota de revisao (posicao 800+) e "biopsia por agulha" aparece no cabecalho (posicao 0), o pattern-order vence e retorna excisional.
**Resultado:** Procedimento errado em laudos com nota de revisao.
**Fix:** Coletar TODOS os matches, preferir o de posicao mais CEDO no texto (mais proximo do cabecalho = mais confiavel).

### 2.18 Procedimento clinico != procedimento textual (iter 7)
**Bug:** "biopsia incisional" mapeado como "biopsia_agulha" (needle). Na verdade e cirurgica (excisional).
**Resultado:** Classificacao incorreta do procedimento.
**Fix:** Conhecimento clinico: biopsia incisional = procedimento aberto → excisional, nao agulha.

### 2.19 Zero-is-information omission (iter 8 / double-check)
**Bug:** ln_pos=0 quando "linfonodos livres de metastase" esta explicito. LLM omite o zero, L1 extrai.
**Resultado:** 23 casos L1-only em ln_pos. Zero e informacao clinica — "0 linfonodos positivos" NAO e o mesmo que "nao informado".
**Fix:** Extrair zero explicitamente. Na comparacao, L1-only com valor 0 = L1 correto.

### 2.20 Modifier loss (iter 8 / double-check)
**Bug:** Sufixos clinicos significativos perdidos na canonicalizacao: pT1mi → pT1, pN0(sn) → pN0.
**Resultado:** Comparacao trata como agree quando na verdade ha perda de informacao clinica.
**Fix:** Preservar modifier na evidencia. Se a variavel requer o modifier, escalar como policy.

### 2.21 Scope leakage / field mismatch (iter 8 / double-check)
**Bug:** L1 le campo A (ex: materialEspecificado), LLM le campo B (ex: conclusao). Comparacao e injusta.
**Resultado:** L1-only ou LLM-only massivo por escopo diferente, nao competencia diferente.
**Fix:** Documentar explicitamente o *variable contract* (quais campos permitidos). Garantir que ambos extratores leiam os mesmos campos. Se escopo diverge, nao comparar — e scope mismatch, nao erro.

### 2.22 Keyword matched inside negation context (iter 9 / concordance audit)
**Bug:** `has_invasive = re.search(r'invasiv|infiltrant', text)` matcha "invasiv" dentro de "ausencia de carcinoma invasivo". L1 classifica CDIS como CDI_SOE.
**Resultado:** Shared error — L1 e LLM concordam no erro. So detectavel por concordance audit (ponto cego do DDPA).
**Fix:** Para CADA match de keyword binaria, checar N chars de contexto anterior para padroes de negacao. Iterar re.finditer, nao re.search.
**Caso:** 20017973AP — texto diz "ausencia de carcinoma invasivo", descreve CDIS puro. Ambos classificaram como CDI_SOE.

### 2.23 Suffix/modifier truncation in staging regex (iter 9 / concordance audit)
**Bug:** Regex pT `(?:[a-c])?` nao captura "mi" (microinvasao) nem "d" (inflamatorio). pT1mi → pt1.
**Resultado:** Perda de informacao clinica relevante (microinvasao tem tratamento diferente).
**Fix:** Expandir alternativas de sufixo: `(?:mi|[a-d])?`. Testar com todos os sufixos AJCC validos.
**Caso:** 22022832AP — "pT1mi; pN0 (sn)" → L1 captava "pt1" em vez de "pt1mi".

### 2.24 Structured field keyword without qualifier (iter 10)
**Bug:** `necrose tumoral:` captured but `necrose:` (same field, different pathologist style) missed. Same concept, different phrasing.
**Resultado:** Fill rate 30.5% instead of 35.5%. 10 false negatives (6 structured + 4 semi-structured `sem necrose`).
**Fix:** Add patterns for both `necrose tumoral:` AND `necrose:` (excluding `necrose adiposa`). Also add `sem necrose(?!\s+adiposa)`.
**Generalizacao:** Para qualquer campo estruturado, minerar TODAS as variantes de nome do campo no corpus, nao apenas a mais longa.

### 2.25 Priority ordering in multi-specimen reports (iter 10)
**Bug:** In bilateral mastectomy reports, "pele de mama direita livre de neoplasia" + "pele de mama esquerda sem particularidades" — function checked "sem particularidades" (→ None) BEFORE "livre" (→ nao), returning wrong answer.
**Resultado:** Fill rate 12.5% instead of 15.0% for pele. Bilateral cases lost.
**Fix:** Reorder: positive > negative > not-assessable. Definitive answer anywhere trumps "sem particularidades"/"nao amostrada" elsewhere.
**Generalizacao:** Para variaveis de envolvimento (pele, mamilo, musculo), se o mesmo orgao aparece em multiplos especimes, usar hierarquia: comprometido > livre > nao amostrado.

### 2.26 Labeled vs uncontextualized measurement priority (iter 11)
**Bug:** tam_cm collected ALL measurements (labeled structured fields + "medindo X cm" anywhere) and returned the LARGEST. In multi-specimen reports, narrative "MEDINDO 3,0 X 2,5 CM" from axillary mass addendum overrode "TAMANHO DA NEOPLASIA INVASIVA: 12 MM".
**Resultado:** tam_cm disagreement 20005953AP: L1=3.0cm (axillary mass), correct=12mm (primary tumor).
**Fix:** Labeled matches (structured field names) take priority over "medindo" fallbacks. Only use "medindo" when no labeled match exists.
**Generalizacao:** Para MEAS-N, structured labels > narrative measurements. "TAMANHO DO TUMOR:" is the pathologist's explicit answer; "medindo X cm" is descriptive and context-dependent.

### 2.27 Masculine/feminine laterality in prefix patterns (iter 11)
**Bug:** `esquerda?|direita?` in PELE_PFX/MAMILO_PFX only matches feminine forms ("mama esquerda") but not masculine ("tecido mamario esquerdo/direito"). The `?` makes the final 'a' optional (matching "esquerd"/"direit"), not switching to masculine 'o'.
**Fix:** `esquerd[oa]|direit[oa]` — matches both gendered forms.
**Generalizacao:** PT-BR anatomical laterality must handle gender agreement: "mama esquerda" (fem) vs "tecido mamario esquerdo" (masc).

### 2.28 Shared-scale ontology collision (iter 12 / double-check R9)
**Bug:** DCIS nuclear grade uses the same 1-3 scale as Nottingham nuclear component, but they are different clinical constructs. Nottingham is ONLY for invasive carcinoma. In pure DCIS reports, "grau nuclear intermediario" → 2 was extracted as esc_nuclear, mixing ontologies.
**Resultado:** 11/25 esc_nuclear L1-only cases were DCIS-only. The extracted score is factually correct (the number matches the text) but ontologically wrong (it's not a Nottingham component).
**Fix:** Post-extraction DCIS guard: if behavior_class == 'in_situ', null out esc_tubular/esc_nuclear/esc_mitotico. Applied in the runner, not in the detector (detector remains scale-agnostic).
**Generalizacao:** When two clinical systems share the same numeric scale (e.g., Bloom-Richardson nuclear vs DCIS nuclear, Gleason 3+4 vs ISUP grade group), the L1 must disambiguate by tumor type AFTER extraction, not during pattern matching. Test for same-scale collisions whenever adding ordinal/numeric variables.

### 2.29 Multi-specimen target-selection ambiguity (iter 11 / double-check R9)
**Bug:** Multi-biopsy reports have measurements from different specimens: "BIOPSIA 1: MAIOR EXTENSAO: 6 MM" + "BIOPSIA 2: MAIOR EXTENSAO: 10 MM". L1 returns the largest across all biopsies. LLM returns the first. Both are literally correct extractions from different specimens.
**Resultado:** 2 tam_cm policy disagreements (22027607AP, 25001194AP). Neither is wrong — the ambiguity is which specimen's measurement is "the" answer.
**Fix:** Classify as escalated_policy. Document in variable contract: `aggregation_rule: largest_across_specimens` (current) vs `flag_ambiguous` (alternative). Future: specimen-level intermediate representation where each specimen gets its own row.
**Generalizacao:** For ANY variable where a report describes multiple specimens (bilateral, multi-biopsy, tumor + node), the variable contract MUST specify specimen selection policy. Default: document the policy and flag multi-specimen cases for human review.

### 2.30 Educational/reference section contamination (gastrico/tireoide/cervical)
**Bug:** Laudo contem secao educativa (COMENTÁRIOS, REFERÊNCIAS, NOTA) que lista TODAS as categorias de uma classificacao (e.g., Bethesda I-VI com taxas de malignidade, ou Bethesda cervical com HSIL/LSIL).
**Resultado:** Regex sem section guard matcha categoria errada da secao educativa. LLM igualmente vulneravel (26.4% error rate em tireoide Bethesda).
**Exemplo:** Resultado real = Bethesda I (amostra nao diagnostica). COMENTÁRIOS lista "IV (FN/SFN): Categoria IV de Bethesda - taxa de malignidade 16,67%". Regex matcha "Categoria IV" → FN.
**Fix:** `strip_comentarios()` trunca texto em markers: `comentarios`, `referencias`, `nota:`, `observacao:`. Aplicar ANTES da extracao de variaveis diagnosticas.
**Generalizacao:** Qualquer laudo com secao educativa/bibliografica precisa de section stripping. O guard protege AMBOS regex e LLM. E o bug mais impactante descoberto nos dominios citologicos.

### 2.31 Broad pattern matches hedge/exclusion language (cervical)
**Bug:** Pattern de categoria alta (e.g., HSIL `les.o.*alto grau`) matcha linguagem de exclusao usada em categoria mais baixa: "nao sendo possivel excluir lesao de alto grau" = ASC-H, nao HSIL.
**Resultado:** R regex classifica ASC-H como HSIL (37/200 casos no holdout cervical). L1 sem guard tambem erraria.
**Exemplo:** "ATIPIAS EM CÉLULAS ESCAMOSAS NÃO SENDO POSSÍVEL EXCLUIR LESÃO DE ALTO GRAU" = ASC-H. Pattern `ALTO GRAU` matcha → FP para HSIL.
**Fix:** Prefix guard: verificar N chars antes do match para "excluir", "afastar", "nao podendo". Se presente, bloquear HSIL e deixar cair para ASC-H.
**Generalizacao:** Em classificacoes hierarquicas onde categorias se referenciam ("excluir X", "descartar Y"), a presenca textual do termo NAO significa presenca diagnostica. Guard de contexto e obrigatorio.

### 2.32 Variable-specific section routing (cervical)
**Bug:** Algumas variaveis aparecem DENTRO da secao educativa (COMENTÁRIOS) mas sao dados clinicos genuinos, nao conteudo educativo. Se strip_comentarios() remove tudo, essas variaveis sao perdidas.
**Resultado:** Hormonal pattern ("quadro cito-hormonal: trófico") aparece em COMENTÁRIOS mas e dado do paciente. Stripping universal = FN em hormonal (9 casos perdidos).
**Exemplo:** "IV) COMENTÁRIOS: quadro cito-hormonal: trófico. O presente laudo foi elaborado..." — "trófico" e diagnostico real do paciente.
**Fix:** Roteamento por variavel: variaveis diagnosticas (resultado, ZT) usam texto truncado. Variaveis clinicas (hormonal) usam texto completo. Decidir ANTES de codificar qual variavel precisa de qual texto.
**Generalizacao:** Section stripping nao e binario (tudo ou nada). Cada variavel pode precisar de escopo diferente. Documentar no variable contract.

---

## 2b. Variable Contract (obrigatorio antes de construir detector)

Para cada variavel, definir ANTES de codificar:

```yaml
variable_contract:
  name: procedimento
  field_scope:        # quais campos sao lidos
    - conclusao
    - materialEspecificado
  specimen_scope:     # quais especimes contam
    - peca_principal
    - biopsia
  entity_scope:       # qual entidade dentro do laudo
    - tumor_invasivo   # (para tam_cm)
    - qualquer         # (para procedimento)
  aggregation_rule:   # como agregar multiplos matches
    - primeiro         # (para procedimento: earliest position)
    - maior            # (para tam_cm: largest measurement)
    - pior             # (para margens: worst-case-wins)
  modifier_policy:    # sufixos preservados ou colapsados
    - preservar: "(sn)", "(ls)", "mi"
    - colapsar: acentos, espacos
```

**Por que isso importa:** os maiores problemas residuais do Round 7 (tam_cm, margens, procedimento, pT1mi) sao problemas de escopo/entidade/representacao, nao de pattern matching puro. Sem variable contract, "bugs de regex" mascaram problemas ontologicos.

---

## 3. Estrategias por Classe de Variavel

### BIN (presenca binaria): Span-based neg/pos resolution
```
1. Coletar TODOS os spans de negacao
2. Coletar TODOS os spans positivos
3. Para cada positivo, checar se overlaps com negacao
4. Se positivo nao-overlapping existe → "sim"
5. Se so negacao → "nao"
6. Se nada → None
```
**Por que span-based:** laudos multi-biopsia podem ter negacao em uma secao e positivo em outra. Pattern simples (first-match) falha.

### CAT (categoria): Cascata especifico → generico
```
1. Testar subtipos especificos PRIMEIRO (micropapilar, mucinoso, misto)
2. Depois subtipos intermediarios (CLI, papilar)
3. Depois catch-all (CDI_SOE, outro_maligno)
4. Depois benignos (tumor phyllodes → papiloma → fibroadenoma → fibrocistico → outro_benigno)
5. Se nada → None
```
**Ordem importa:** se catch-all vem antes, mascara subtipos.

### MEAS-N (medida numerica): Label + collect + max
```
1. Tentar labeled patterns (mais confiavel): "TAMANHO DA NEOPLASIA:"
2. Tentar patterns textuais: "mede/medindo"
3. Coletar TODOS os matches (nao parar no primeiro)
4. Converter para unidade comum (mm)
5. Retornar o MAIOR (para staging)
6. Fallback: "= X mm" em contexto relevante
```
**Por que collect+max:** laudos multifocais. Staging usa maior foco invasivo.

### NUM (contagem): Context-restricted
```
1. Tentar patterns rotulados: "LINFONODOS EXAMINADOS: XX"
2. Para counts sem rotulo, EXIGIR contexto (linfon/axilar/sentinela em 200 chars)
3. Para sentinel: ler contagem real da prosa (CINCO → 5), nao defaultar para 1
4. Para "livre/negativo": verificar contexto LN, EXCLUIR contexto de margem
```
**Armadilha:** "MARGENS LIVRES" contamina ln_pos se nao filtrar contexto.

### CAT-ORD (ordinal): Pattern + canonicalizacao
```
1. Pattern principal: y?pt[0-4][a-c]?(...)
2. Normalizar: O→0, adicionar prefixo se faltando, strip suffixes para comparacao
3. Aceitar variantes: ypT (neoadjuvante), ptis (in situ), (sn)/(ls) suffixes
```

---

## 4. Checklist de Transferencia para Novo Dominio

Ao iniciar L1 em um dominio novo (ex: patologia ginecologica, radiologia torax):

### Pre-requisitos
- [ ] Holdout de 100-200 casos com LLM extractions como reference
- [ ] Conclusao/texto principal identificado no CSV
- [ ] Variaveis-alvo definidas com classe (BIN/CAT/MEAS-N/etc)

### Para CADA variavel
- [ ] Vocab mining: 20+ laudos, todos os termos listados
- [ ] Negacao patterns identificados (especificos do dominio)
- [ ] Section boundaries definidos
- [ ] Detector construido seguindo principios (neg-first, evidence-first, conservative)
- [ ] 5+ smoke tests escritos
- [ ] Rodou no corpus, adjudicou discordancias
- [ ] Denominador limpo (abstained/escalated removidos)
- [ ] Acuracia = 100% no extractable

### Padroes que SEMPRE transferem (universais)
- Negacao-first (2.1)
- Cross-section contamination (2.2)
- Word boundary para morfologia (2.5)
- Preposicao d[aoe] (2.6)
- Catch-all mascara subtipos (2.11)
- Vocab split forms (2.13)
- Verb synonyms (2.14)
- Pattern-order vs text-position (2.17)
- Educational section contamination (2.30) — qualquer laudo com COMENTÁRIOS/REFERÊNCIAS
- Broad pattern matches hedge language (2.31) — classificacoes hierarquicas
- Variable-specific section routing (2.32) — section stripping nao e binario

### Padroes que NAO transferem (dominio-especifico)
- Vocabulario (cada especialidade tem termos unicos)
- Section boundaries (cada tipo de laudo tem estrutura diferente)
- Suffixes de staging (sn, ls, m, etc)
- Contexto clinico para desambiguacao
- Ordinal indicators (variacao tipografica regional)
- Amendment patterns (correcoes pos-IHQ)
- Formato de tabela estruturada (Sydney Modified vs Bethesda vs IHQ)

---

## 5. Tabela de Convergencia — Mama AP (referencia)

| Variavel | Classe | Iter1 | Iter5 | Iter7 | Iter9 | Iter11 | Iter12 | Adj | Iter para convergir |
|---|---|---|---|---|---|---|---|---|---|
| pN | CAT-ORD | 96.0% | 100% | **100%** | **100%** | **100%** | **100%** | **100%** | 5 |
| pT | CAT-ORD | 99.5% | 99.5% | 99.5% | **100%** | **100%** | **100%** | **100%** | 9 |
| comp_insitu | BIN | 79.5% | 99.5% | 99.5% | 99.5% | 99.5% | 99.5% | **100%** | 3 |
| ln_exam | NUM | 89.0% | 99.0% | **100%** | **100%** | **100%** | **100%** | **100%** | 5 |
| margens | CAT | 96.0% | 97.5% | 99.0% | 99.0% | 99.0% | 99.0% | **100%** | 7 |
| tam_cm | MEAS-N | 91.5% | 96.0% | 96.0% | 96.0% | **96.5%** | 96.5% | **100%** | 11 |
| ln_pos | NUM | 91.0% | 89.0% | 88.5% | 88.5% | 88.5% | 88.5% | **100%** | 3 |
| tipo_hist | CAT | 62.5% | 83.5% | 84.0% | 83.5% | 83.5% | 83.5% | **100%** | 9 |
| procedimento | CAT | 75.5% | 76.5% | 81.0% | 81.0% | 81.0% | 81.0% | **100%** | 7 |
| grau_nott | CAT-ORD | — | 99.0% | 99.5% | 99.5% | 99.5% | 99.5% | **100%** | 7 |
| inv_linfov | BIN | — | 96.5% | **100%** | **100%** | **100%** | **100%** | **100%** | 7 |
| inv_perin | BIN | — | 98.5% | **100%** | **100%** | **100%** | **100%** | **100%** | 7 |
| ref_ihq | BIN | — | 98.5% | 99.0% | 99.0% | 99.0% | 99.0% | **100%** | 7 |
| esc_tubular | NUM-C | — | — | — | 99.5% | 99.5% | 99.5% | **100%** | 8 |
| esc_nuclear | NUM-C | — | — | — | 87.0% | 87.0% | **92.5%** | **100%** | 12 |
| esc_mitotico | NUM-C | — | — | — | 99.0% | 99.0% | 99.0% | **100%** | 8 |
| behavior_class | CAT (der) | — | — | — | 94.5% | 94.5% | 94.5% | **100%** | derived |
| invasive_malignancy | BIN (der) | — | — | — | 98.0% | 98.0% | 98.0% | **100%** | derived |
| malignancy_any | BIN (der) | — | — | — | 98.0% | 98.0% | 98.0% | **100%** | derived |

**L1-only variables (no LLM comparison, iter 11):**

| Variavel | Classe | Fill % | Structured FN | Notes |
|---|---|---|---|---|
| necrose_tumoral | BIN | 35.5% | 0 | Bug #24 fixed: `necrose:` without `tumoral` |
| microcalcificacoes | BIN | 21.5% | 0 | Narrative `com microcalcificacoes` correctly excluded |
| multifocalidade | BIN | 17.0% | 0 | Multiple field name variants covered |
| pele | BIN | 15.0% | 0 | Bug #25/#27: bilateral priority + masc laterality |
| mamilo | BIN | 12.5% | 0 | Bug #27: masc laterality fix |
| efeito_terapeutico | CAT | 12.0% | 0 | completa/parcial/sem_info |
| infiltrado_linfocitico | CAT | 5.0% | 0 | escasso/moderado/intenso/nao_evidenciado. Typo `intramural` covered |
| extensao_extranodal | BIN | 4.5% | 0 | extranodal + extracapsular + rompimento capsular |

*Adj = ajustado pelo resolution contract (denominador = extractable only)
*Derived variables inherit disagrees from tipo_hist (22/29 L1_correct, 0 LLM_correct, 7 policy/norm)
*Concordance audit: 80 casos, 1 shared error encontrado e fixado, 0 residuais
*Iter 12: 246 smoke tests, 29 bug patterns, 27 total variables
*Iter 11: tam_cm Bug #26 fix (labeled>medindo priority), 96.0%→96.5%
*Iter 12: DCIS guard (Bug #28) — esc_nuclear 87.0%→92.5% (11 DCIS L1-only nulled)

### Cross-Domain Convergence Table (todos os 5 dominios, 2026-04-13)

| Dominio | Vars | Smoke tests | Iters | Holdout N | Adj vs R regex | Adj vs LLM | R regex bugs | Key bug patterns |
|---|---|---|---|---|---|---|---|---|
| Mama AP | 27 | 332 | 12 | 200 | — | 100% adj | 29 | neg-cego, cross-section, DCIS guard |
| Mama IHQ | 12 | 224 | 8 | 200+14963 | — | 96-99% adj | — | reference contamination, multi-word clone, CDIS guard |
| Gastrico OLGA | 8 | 69 | 1 | 180 | **100% adj** | 100% | 20 | ATIVA\b in NEGATIVA, MI colon-only |
| Tireoide Bethesda | 3 | 36 | 1 | 200 | **100% adj** | 100% (33 LLM bugs) | 1 | COMENTÁRIOS contamination (2.30) |
| Cervical Bethesda | 5 | 56 | 2 | 200 | **100% adj** | 98.4%+ | 61 | COMENTÁRIOS (2.30), hedge language (2.31), section routing (2.32) |
| **Total** | **55** | **717** | — | **980** | — | — | **111+** | |

**Observacoes cross-domain:**
1. Dominios com texto altamente estruturado (gastrico Sydney, tireoide/cervical Bethesda) convergem em 1-2 iteracoes
2. Dominios com texto narrativo (mama AP) precisam de 7-12 iteracoes
3. Dominios com secao educativa (tireoide, cervical) precisam de section guard — LLM igualmente vulneravel
4. R regex bugs sao mais prevalentes em cervical (61/200 = 30.5%) por falta de section stripping e guards de hedge language
5. IHQ mama converge em iteracoes de vocab mining — cada lab tem formato diferente, mas patterns sao regulares
6. Gastrico OLGA e o mais "limpo" — texto Sydney Modified muito padronizado, poucos edge cases

### Lições da convergência
1. **BIN converge rapido** (2-3 iter) — vocab mining + neg-first resolve
2. **CAT precisa de cascata cuidadosa** (4-5 iter) — cada subtipo novo e uma iteracao
3. **MEAS-N precisa de collect+max** — first-match-wins falha em multifocal
4. **NUM precisa de contexto rigoroso** — sem contexto, contamina entre secoes
5. **CAT-ORD converge rapido** (1-2 iter) — patterns sao regulares
6. **Section guards sao cross-domain** — qualquer laudo com COMENTÁRIOS precisa de strip
7. **Texto padronizado converge em 1 iter** — Bethesda/Sydney/IHQ tabular

---

## 6. Economia de Iteracoes

### O que DESCOBRIR na primeira iteracao (evita rework)
1. **Todos os sinonimos** — vocab mining exaustivo ANTES de codificar
2. **Negacao patterns** — listar TODAS as formas de negacao do dominio
3. **Section boundaries** — mapear TODAS as secoes do laudo
4. **Variantes de grafia** — accents, OCR, abreviacoes
5. **Multi-instance** — laudo pode ter 2+ tumores/biopsias?

### O que so aparece na iteracao 2+
1. **Cross-section contamination** — so visivel no corpus real
2. **Catch-all mascarando subtipo** — so visivel nos disagrees
3. **First-match-wins bug** — so visivel em laudos multifocais
4. **Formato dual** — so visivel quando L1 e LLM discordam em formato

### L1-only variables (sem comparacao LLM)
Quando a LLM nao extrai uma variavel, o L1 pode extrair sozinho como "L1-only". Validacao:
1. **Mine structured field patterns** no corpus (colon-separated)
2. **Build detector** com negation-first + evidence
3. **Check distribution** (sim/nao/NA ratios clinicamente razoaveis?)
4. **Scan keyword FNs** — buscar keyword no texto onde L1=NA
5. **Classify FNs** — structured (real FN) vs narrative (correctly excluded)
6. **Fix and retest** ate 0 structured FNs

### Criterios de exaustao (dois niveis)

**Nivel 1 — Structured-field operational exhaustion (local):**
O detector L1 esgotou todas as variaveis extraiveis por campos estruturados no development set.
1. Todos os LLM-compared vars estao a 100% adjusted
2. Todos os L1-only vars estao a 0% structured FN
3. Candidatos restantes tem fill rate <5% (retorno marginal decrescente)
4. Coverage: 100% dos laudos tem >=1 variavel extraida

**Nivel 2 — Domain exhaustion (confirmado):**
Requer validacao em holdout congelado apos freeze point.
1. Criterios do Nivel 1 atendidos
2. Holdout de 80-120 casos separados do development set
3. Acuracia ajustada >= 99% no holdout (threshold para claim publicavel)
4. Nenhum novo bug pattern descoberto no holdout

**IMPORTANTE:** NAO usar "domain exhaustion" sem qualificador. Mama AP atingiu Nivel 1 (structured-field operational exhaustion) na iter 12. Nivel 2 requer holdout separado.

### Estimativa de iteracoes por classe
| Classe | Iteracoes tipicas | Por que |
|---|---|---|
| BIN simples | 2 | neg + pos + edge |
| BIN complexa | 3-4 | span-based + vocab |
| CAT poucos valores | 2-3 | cascata + 1-2 subtipos |
| CAT muitos valores | 4-5 | cascata + subtipos + benigno |
| MEAS-N | 3-5 | label + multifocal + formato |
| NUM | 2-4 | context + word numbers |
| CAT-ORD | 1-2 | patterns regulares |

---

## 7. Smoke Test Patterns (template)

Para cada variavel, incluir testes para:

```python
# POSITIVO direto
check("var_positive", detect_var(normalize("TEXTO POSITIVO CLARO")), "valor_esperado")

# NEGACAO (negation-first)
check("var_negated", detect_var(normalize("SEM EVIDÊNCIA DE X")), "nao")

# AUSENTE (dado nao esta no texto)
check("var_absent", detect_var(normalize("TEXTO IRRELEVANTE")), None)

# VARIANTE DE GRAFIA
check("var_variant", detect_var(normalize("SINONIMO OU ABREVIACAO")), "valor_esperado")

# FORMATO ATIPICO
check("var_atypical", detect_var(normalize("FORMATO INCOMUM MAS VALIDO")), "valor_esperado")

# MULTI-INSTANCE (se aplicavel)
check("var_multi", detect_var(normalize("INSTANCIA1... INSTANCIA2...")), "maior/principal")

# CROSS-SECTION (se aplicavel)
check("var_cross_section", detect_var(normalize("MARGEM LIVRE. LINFONODO COMPROMETIDO")), "valor_correto_da_secao")
```

---

## 8. Anti-patterns de Comparacao L1 vs LLM

### Normalizar ANTES de comparar
1. **Accents** — LLM retorna "milímetros", L1 retorna "milimetros" → strip accents
2. **Unidades** — "33 mm" vs "3,3 cm" → converter para mm, comparar com tolerancia
3. **Suffixes** — "pN0(sn)" vs "pN0" → strip suffix para base comparison
4. **Canonicalizacao** — "N0" vs "pN0" → adicionar prefixo canonico

### Contar corretamente
- Both NA = agree (dado nao extraivel)
- L1-only = VERIFICAR amostra (pode ser L1 correto OU ontology collision — Bug #28 mostrou que 44% dos L1-only de esc_nuclear eram DCIS, nao Nottingham)
- LLM-only = gap de cobertura do L1 (priorizar fix)
- Disagree = adjudicar caso a caso

---

## 9. Quando Parar

### Criterio de convergencia
Uma variavel esta "convergida" quando:
1. Todos os disagrees foram adjudicados
2. Todos os L1-only verificados (L1 correto ou nao)
3. Todos os LLM-only investigados (L1 fixado ou dado nao-extraivel)
4. Denominador limpo (abstained/escalated removidos)
5. Acuracia = 100% no extractable
6. Smoke tests cobrem todos os padroes descobertos

### Quando gerar double-check round
- Apos 3+ iteracoes sem progresso significativo
- Quando decisoes de politica pendentes (escalated_policy)
- Quando duvida sobre se L1 ou LLM esta correto em grupo de casos

### Sinal de que o detector atingiu structured-field operational exhaustion
- Todos os disagrees sao policy/ambiguos (nao extracao)
- L1-only sao todos verificados (corretos OU nulled por guard ontologico)
- Novos patterns nao aparecem nos ultimos 2 iteracoes
- Candidatos restantes abaixo do threshold de fill rate (<5%)

**NOTA:** "Structured-field operational exhaustion" NAO e "domain exhaustion". Para claim de exaustao de dominio, validar em holdout congelado (ver criterios acima).
