"""Piloto enxuto do DDPA (PRD_piloto_enxuto_para_submissao_20260922, seção 7).

Referências humanas já existentes (nenhuma leitura nova):
  - cervical: 250 laudos da LAB_C, pathologist 1, leitura cega (análise principal);
  - tireoide: 270 citologias, pathologist 2, cegamento suave (análise separada; braços A e B1 à parte);
  - IHQ de mama: 180 casos do breast study, pathologist 2, cegamento suave (análise separada).
L1 = detectores congelados v2.2, chamados como nos próprios runners. L2 = GPT-4.1 com os prompts de
desenvolvimento de 2026-04-12 (fill_llm_gaps.py), uma execução, temperatura 0 — configuração nova,
congelada antes desta execução. Regra de resolução do desenho v3, seção 4.
Saídas por campo ficam locais (`_piloto_saidas/`); agregados NO_PHI em `_piloto_resultados_NO_PHI.json`.
Uso: python _piloto_executar.py [--limite N] [--sem-modelo]
"""
import argparse
import hashlib
import importlib.util
import json
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from scipy.stats import beta

AQUI = Path(__file__).parent
SAIDAS = AQUI / "_piloto_saidas"
SAIDAS.mkdir(exist_ok=True)
PIL = Path(r"<local path>")
A1 = Path(r"<local path>")
BREAST_STUDY = Path(r"<local path>")
FILL = A1 / "app_validacao" / "fill_llm_gaps.py"
SCRUB = Path(r"<local path>")
SEC = Path(r"<local path>")
REF_LOCAL = AQUI / "_piloto_referencias_LOCAL"
MODELO_L2 = "gpt-4.1"
TRAVA = threading.Lock()

sys.path.insert(0, str(PIL))


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def carregar(nome, caminho):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    mod = importlib.util.module_from_spec(spec)
    saida = sys.stdout
    spec.loader.exec_module(mod)
    if sys.stdout is not saida:
        trocado = sys.stdout
        sys.stdout = saida
        trocado.detach()
    return mod


def prompt(nome: str) -> str:
    m = re.search(rf'PROMPT_{nome} = """(.*?)"""', FILL.read_text(encoding="utf-8"), re.S)
    if not m:
        raise SystemExit(f"ABORTADO: PROMPT_{nome} não encontrado")
    return m.group(1)


# ---------------------------------------------------------------- canonização (fixada antes de rodar)
def canon_cerv(x):
    if x is None:
        return None
    s = str(x).strip().upper().replace("-", "_").replace(" ", "_")
    return None if s in {"", "NC", "NULL", "NONE"} else s


def canon_adeq(x):
    if x is None:
        return None
    s = str(x).strip().lower()
    if s.startswith("insatisf"):
        return "INSATISFATORIA"
    if s.startswith("satisf"):
        return "SATISFATORIA"  # "satisfatória, mas limitada" é satisfatória (Bethesda 2014)
    return None


def canon_beth(x):
    if x is None:
        return None
    s = str(x).strip().upper().replace("CATEGORIA", "").replace("BETHESDA", "").replace("DE", "").strip(" -:")
    return s if s in {"I", "II", "III", "IV", "V", "VI"} else None


def canon_status(x):
    if x is None:
        return None
    s = str(x).strip().lower()
    if s.startswith(("low_positive", "positiv", "positive")):
        return "POSITIVO"  # positivo baixo 1-10% = positivo (ASCO/CAP 2020)
    if s.startswith(("negativ", "negative")):
        return "NEGATIVO"
    return None


def canon_her2(x):
    if x is None:
        return None
    s = str(x).strip().replace(" ", "")
    s = {"0+": "0", "1": "1+", "2": "2+", "3": "3+"}.get(s, s)
    return s if s in {"0", "1+", "2+", "3+"} else None


def canon_ki67(x):
    if x is None:
        return None
    nums = [float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", str(x))]
    if not nums:
        return None
    v = nums[0] if len(nums) == 1 else (nums[0] + nums[1]) / 2  # faixa -> ponto médio (declarado)
    return int(round(v)) if 0 <= v <= 100 else None


# ---------------------------------------------------------------- referências
def ref_cervical():
    cfg = json.loads((A1 / "A1_paper" / "_config_cervical_annotator1.json").read_text(encoding="utf-8"))
    casos = {c["key"]: c["laudo"] for r in cfg["reviewers"] for c in r["cases"]}
    resp = json.loads((REF_LOCAL / "cervical_annotator1_respostas_LOCAL.json").read_text(encoding="utf-8"))
    linhas, sem_caso = [], 0
    for k, v in resp.items():
        chave = k.split(":", 2)[2]
        if chave not in casos:
            sem_caso += 1
            continue
        a = (v or {}).get("answer") or {}
        linhas.append({"conjunto": "cervical", "braco": "-", "caso": chave, "laudo": casos[chave],
                       "completo": bool((v or {}).get("complete")),
                       "ref_resultado": canon_cerv(a.get("categoria_bethesda")),
                       "ref_resultado_bruto": a.get("categoria_bethesda"),
                       "ref_adequab": canon_adeq(a.get("adequabilidade")),
                       "is_cervical": a.get("is_cervical")})
    custodia = {"casos_no_instrumento": len(casos), "respostas": len(resp), "resposta_sem_caso": sem_caso,
                "casos_sem_resposta": len(set(casos) - {k.split(':', 2)[2] for k in resp}),
                "respostas_incompletas": sum(1 for l in linhas if not l["completo"]),
                "sobreposicao_desenvolvimento": 0,
                "nota_sobreposicao": "laudos da LAB_C; desenvolvimento cervical do DDPA foi na LAB_A (conferido: 0/250)"}
    return pd.DataFrame(linhas), custodia


def ref_tireoide():
    gold = [json.loads(l) for l in open(A1 / "A1_paper" / "_GOLDS_CUSTODIA" / "gold_tireoide_annotator2_270.jsonl", encoding="utf-8") if l.strip()]
    adj = {}
    for l in open(Path(r"<local path>"), encoding="utf-8"):
        if l.strip():
            o = json.loads(l)
            adj[o["id"]] = o
    dev = set(pd.read_csv(A1 / "holdout" / "holdout_tireoide.csv", usecols=["numExame"]).numExame.astype(str))
    linhas = []
    for g in gold:
        num = g["id"].split("::")[-1]
        linhas.append({"conjunto": "tireoide", "braco": g.get("brace"), "caso": g["id"],
                       "laudo": adj.get(g["id"], {}).get("laudo"),
                       "ref_bethesda": canon_beth(g.get("bethesda")), "ref_bethesda_bruto": g.get("bethesda"),
                       "no_desenvolvimento": num in dev})
    d = pd.DataFrame(linhas)
    custodia = {"respostas": len(gold), "sem_texto": int(d["laudo"].isna().sum()),
                "sem_categoria_na_referencia": int(d["ref_bethesda"].isna().sum()),
                "sobreposicao_desenvolvimento": int(d["no_desenvolvimento"].sum())}
    d = d[d["laudo"].notna() & d["ref_bethesda"].notna() & ~d["no_desenvolvimento"]]
    return d, custodia


def textos_dev_ihq():
    ids = set(pd.read_csv(A1 / "holdout" / "holdout_mama_ihq.csv", usecols=["numExame"]).numExame.astype(str))
    for f in ("gabarito_700_mama_ihq.csv", "gabarito_200_mama_ihq.csv", "p1_amostra.csv"):
        p = PIL / "playbooks" / "her2_mama_ihq" / f
        if p.exists():
            ids |= set(pd.read_csv(p, usecols=["numExame"]).numExame.astype(str))
    corp = pd.read_csv(PIL / "runs" / "mama_ihq" / "corpus_mama_ihq_15k_v3.csv", usecols=["numExame", "texto"], dtype=str)
    txt = list(corp[corp.numExame.isin(ids)].texto.fillna(""))
    h = pd.read_csv(A1 / "holdout" / "holdout_mama_ihq.csv", dtype=str)
    txt += list((h.get("conclusao", "").fillna("") + " " + h.get("microscopia", "").fillna("")))
    return txt, len(ids)


def shingles(t, k=8):
    w = re.findall(r"[a-z0-9]+", str(t).lower())
    return {" ".join(w[i:i + k]) for i in range(max(0, len(w) - k + 1))}


def ref_breast_study():
    gold = [json.loads(l) for l in open(A1 / "A1_paper" / "_GOLDS_CUSTODIA" / "golden_breast_study_adjudicado_180.jsonl", encoding="utf-8") if l.strip()]
    casos = {c["case_id"]: c for c in json.loads((BREAST_STUDY / "F38_golden_cases_INTERNAL_SENSITIVE.json").read_text(encoding="utf-8"))}
    dev_txt, n_dev = textos_dev_ihq()
    dev_sh = [shingles(t) for t in dev_txt]
    linhas = []
    for g in gold:
        c = casos.get(g["case_id"])
        if c is None:
            continue
        ihq = "\n\n".join(s["text"] for s in c["segments"] if s.get("kind") == "ihq")
        sh = shingles(ihq)
        jac = max((len(sh & d) / max(1, len(sh | d)) for d in dev_sh if d), default=0.0) if sh else 0.0
        sobre = jac >= 0.9  # mesmo laudo = quase idêntico; texto padrão de IHQ dá 0,4-0,6 (medido)
        r = g["respostas"]
        linhas.append({"conjunto": "mama_ihq", "braco": "-", "caso": g["case_id"], "laudo": ihq,
                       "ref_re": canon_status(r["er"]["humano"]), "ref_rp": canon_status(r["pr"]["humano"]),
                       "ref_her2": canon_her2(r["her2_ihc"]["humano"]), "ref_ki67": canon_ki67(r["ki67"]["humano"]),
                       "ref_re_bruto": r["er"]["humano"], "ref_rp_bruto": r["pr"]["humano"],
                       "no_desenvolvimento": sobre})
    d = pd.DataFrame(linhas)
    custodia = {"respostas": len(gold), "casos_com_texto": len(d), "textos_de_desenvolvimento_comparados": len(dev_txt),
                "ids_de_desenvolvimento": n_dev, "sobreposicao_desenvolvimento": int(d["no_desenvolvimento"].sum()),
                "criterio_sobreposicao": "Jaccard de trechos de 8 palavras >= 0,9 com algum laudo de desenvolvimento (mesmo laudo); texto padrão de IHQ fica em 0,4-0,6"}
    return d[~d["no_desenvolvimento"]], custodia


# ---------------------------------------------------------------- L1 congelado
def aplicar_l1(d: pd.DataFrame):
    if d.empty:
        return d
    conj = d["conjunto"].iloc[0]
    if conj == "cervical":
        m = carregar("cerv", PIL / "cervical_bethesda_l1_v4.py")
        td = d["laudo"].map(lambda t: m.strip_comentarios(m.normalize(t or "")))
        d["l1_resultado"] = td.map(lambda t: canon_cerv(m.detect_resultado(t)[0]))
        d["l1_adequab"] = td.map(lambda t: canon_adeq(m.detect_adequab(t)[0]))
    elif conj == "tireoide":
        m = carregar("tir", PIL / "tireoide_bethesda_l1_v4.py")
        d["l1_bethesda"] = d["laudo"].map(lambda t: canon_beth(m.detect_bethesda(m.strip_comentarios(m.normalize(t or "")))[0]))
    else:
        import framework_core
        m = carregar("ihq", PIL / "mama_ihq_l1_v4.py")
        tn = d["laudo"].map(lambda t: framework_core.normalize(t or ""))  # igual ao runner (pre_normalize)
        d["l1_re"] = tn.map(lambda t: canon_status(m.detect_re_status(t)[0]))
        d["l1_rp"] = tn.map(lambda t: canon_status(m.detect_rp_status(t)[0]))
        d["l1_her2"] = tn.map(lambda t: canon_her2(m.detect_her2_score(t)[0]))
        d["l1_ki67"] = tn.map(lambda t: canon_ki67(m.detect_ki67_pct(t)[0]))
    return d


# ---------------------------------------------------------------- L2 (GPT-4.1, prompts de abril)
RE_RESIDUO = [re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), re.compile(r"(?<!\d)\d{11,}(?!\d)"),
              re.compile(r"(?i)\b(nome\s+do\s+paciente|paciente|nascimento|data\s+de\s+nascimento|cpf)\s*[:\-]\s*[A-Za-zÀ-ÿ0-9]")]
CAMPOS_L2 = {"cervical": ("CERVICAL", {"l2_resultado": ("resultado", canon_cerv), "l2_adequab": ("adequab", canon_adeq)}),
             "tireoide": ("TIREOIDE", {"l2_bethesda": ("bethesda", canon_beth)}),
             "mama_ihq": ("MAMA_IHQ", {"l2_re": ("re_status", canon_status), "l2_rp": ("rp_status", canon_status),
                                       "l2_her2": ("her2_score", canon_her2), "l2_ki67": ("ki67_pct", canon_ki67)})}


def aplicar_l2(d: pd.DataFrame, cliente, scrub):
    if d.empty:
        return d, {}
    conj = d["conjunto"].iloc[0]
    nome_p, campos = CAMPOS_L2[conj]
    sistema = prompt(nome_p)
    brutos = SAIDAS / f"l2_{conj}_LOCAL.jsonl"
    feitos = {}
    if brutos.exists():
        for l in brutos.read_text(encoding="utf-8").splitlines():
            o = json.loads(l)
            if "erro" not in o:
                feitos.setdefault(o["caso"], o)

    def uma(caso, texto):
        limpo, _ = scrub.scrub(texto or "")
        msg = f"REPORT TEXT (conclusao):\n{limpo}"
        hmsg = sha(msg.encode())[:16]
        if any(rx.search(msg) for rx in RE_RESIDUO):
            out = {"caso": caso, "hash_msg": hmsg, "erro": "trava_identificador"}
        else:
            out = None
            for tent in range(3):
                try:
                    r = cliente.chat.completions.create(model=MODELO_L2, temperature=0, max_tokens=600,
                                                        response_format={"type": "json_object"},
                                                        messages=[{"role": "system", "content": sistema},
                                                                  {"role": "user", "content": msg}])
                    bruto = r.choices[0].message.content
                    out = {"caso": caso, "hash_msg": hmsg, "hash_prompt": sha(sistema.encode())[:16],
                           "servido": r.model, "json": json.loads(bruto, strict=False)}
                    break
                except Exception as e:  # noqa: BLE001
                    out = {"caso": caso, "hash_msg": hmsg, "erro": str(e)[:200]}
                    time.sleep(2 ** tent)
        with TRAVA:
            with open(brutos, "a", encoding="utf-8") as f:
                f.write(json.dumps(out, ensure_ascii=False) + "\n")
        return out

    fila = [(r.caso, r.laudo) for r in d.itertuples() if r.caso not in feitos]
    with ThreadPoolExecutor(max_workers=8) as ex:
        for o in ex.map(lambda a: uma(*a), fila):
            if o and "erro" not in o:
                feitos[o["caso"]] = o
    falhas = 0
    for col, (campo, canon) in campos.items():
        d[col] = d["caso"].map(lambda c: canon((feitos.get(c) or {}).get("json", {}).get(campo)) if c in feitos else None)
    falhas = int((~d["caso"].isin(feitos)).sum())
    servidos = sorted({o.get("servido") for o in feitos.values() if o.get("servido")})
    return d, {"falhas_l2_tratadas_como_abstencao": falhas, "modelo_servido": servidos,
               "hash_prompt": sha(sistema.encode())[:16]}


# ---------------------------------------------------------------- regra de resolução (desenho v3, seção 4)
RE_KI67 = re.compile(r"ki\s*-?\s*67[^%]{0,80}?(\d{1,3}(?:[.,]\d+)?)\s*%", re.I)


def ki67_ancorado(texto, v):
    return any(canon_ki67(n) == v for n in RE_KI67.findall(texto or ""))


def faltante(x):
    """None, NaN do pandas e string vazia contam como 'sem valor' (bug corrigido em 22/09: NaN passava por valor)."""
    return x is None or (isinstance(x, float) and x != x) or (isinstance(x, str) and x.strip() == "")


def resolver(v1, v2, var, texto):
    v1 = None if faltante(v1) else v1
    v2 = None if faltante(v2) else v2
    if v1 is not None and v2 is not None:
        return ("aceito_concordancia", v1) if v1 == v2 else ("escalado", None)
    if v1 is not None:
        return ("escalado", None)
    if v2 is not None:
        if var == "ki67" and ki67_ancorado(texto, v2):
            return ("aceito_suporte_textual", v2)
        return ("escalado", None)
    return ("sem_resposta", None)


def ic(k, n):
    if n == 0:
        return None
    lo = 0.0 if k == 0 else beta.ppf(0.025, k, n - k + 1)
    hi = 1.0 if k == n else beta.ppf(0.975, k + 1, n - k)
    return [round(100 * lo, 1), round(100 * hi, 1)]


def pct(k, n):
    return round(100 * k / n, 1) if n else None


def resumir(d: pd.DataFrame, var: str, rotulo: str):
    ref, v1, v2 = d[f"ref_{var}"], d[f"l1_{var}"], d[f"l2_{var}"]
    est = [resolver(a, b, var, t) for a, b, t in zip(v1, v2, d["laudo"])]
    estado = pd.Series([e[0] for e in est], index=d.index)
    valor = pd.Series([e[1] for e in est], index=d.index)
    n = len(d)

    def disc(vals, mask):
        m = mask & vals.notna()
        k = int((vals[m] != ref[m]).sum())
        return {"n": int(m.sum()), "discordantes": k, "pct": pct(k, int(m.sum())), "ic95": ic(k, int(m.sum()))}

    conc = estado == "aceito_concordancia"
    sup = estado == "aceito_suporte_textual"
    hibrido = pd.Series([None if faltante(a) and faltante(b) else (b if not faltante(b) else a) for a, b in zip(v1, v2)],
                        index=d.index)  # concordou -> o valor; discordou -> o modelo; modelo sem valor -> o detector
    out = {
        "variavel": rotulo, "n": n, "referencia_com_valor": int(ref.notna().sum()),
        "referencia_nao_informa": int(ref.isna().sum()),
        "DDPA": {
            "aceitos_por_concordancia": int(conc.sum()), "pct_aceitos": pct(int(conc.sum()), n),
            "discordancia_nos_aceitos_por_concordancia": disc(valor, conc),
            "aceitos_por_suporte_textual": int(sup.sum()),
            "discordancia_nos_aceitos_por_suporte": disc(valor, sup),
            "escalados": int((estado == "escalado").sum()), "pct_escalados": pct(int((estado == "escalado").sum()), n),
            "sem_resposta": int((estado == "sem_resposta").sum()),
            "sem_resposta_com_referencia_com_valor": int(((estado == "sem_resposta") & ref.notna()).sum()),
        },
        "so_detector": {"cobertura": int(v1.notna().sum()), "discordancia": disc(v1, pd.Series(True, index=d.index))},
        "so_modelo": {"cobertura": int(v2.notna().sum()), "discordancia": disc(v2, pd.Series(True, index=d.index))},
        "concordou_aceita_senao_modelo": {"cobertura": int(hibrido.notna().sum()),
                                          "discordancia": disc(hibrido, pd.Series(True, index=d.index))},
        "escalados_em_que_o_detector_estava_certo": int(((estado == "escalado") & (v1 == ref)).sum()),
        "escalados_em_que_o_modelo_estava_certo": int(((estado == "escalado") & (v2 == ref)).sum()),
    }
    d[f"estado_{var}"] = estado
    d[f"ddpa_{var}"] = valor
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=0)
    ap.add_argument("--sem-modelo", action="store_true")
    args = ap.parse_args()
    scrub = carregar("scrub", SCRUB)
    from openai import OpenAI
    k_api = json.loads((SEC / "openai_oracle.json").read_text(encoding="utf-8"))["api_key"]  # local key file, not shared
    cliente = OpenAI(api_key=k_api)

    res = {"configuracao": {"pipeline_congelado": "v2.2 (6333ade35add7255)", "L2": MODELO_L2,
                            "prompts": "fill_llm_gaps.py de 2026-04-12", "fill_llm_gaps_sha16": sha(FILL.read_bytes())[:16],
                            "regra": "desenho v3 seção 4"}, "conjuntos": {}}
    for nome, fn, variaveis in (("cervical", ref_cervical, [("resultado", "categoria Bethesda"), ("adequab", "adequação")]),
                                ("tireoide", ref_tireoide, [("bethesda", "categoria Bethesda")]),
                                ("mama_ihq", ref_breast_study, [("re", "RE"), ("rp", "RP"), ("her2", "HER2 IHQ"), ("ki67", "Ki-67")])):
        d, cust = fn()
        if args.limite:
            d = d.head(args.limite)
        d = aplicar_l1(d.copy())
        info_l2 = {}
        if args.sem_modelo:
            for col in CAMPOS_L2[nome][1]:
                d[col] = None
        else:
            d, info_l2 = aplicar_l2(d, cliente, scrub)
        bloco = {"cegamento": "cego" if nome == "cervical" else "suave (IA oculta por padrão, revelável)",
                 "custodia": cust, "l2": info_l2, "n_analisado": len(d), "resultados": {}}
        grupos = [("todos", d)]
        if nome == "tireoide":
            grupos = [("braco_A_sorteio", d[d["braco"] == "A"]), ("braco_B1_enriquecido", d[d["braco"] == "B1"])]
        for gnome, g in grupos:
            g = g.copy()
            bloco["resultados"][gnome] = [resumir(g, v, r) for v, r in variaveis]
            g.drop(columns=["laudo"]).to_csv(SAIDAS / f"campos_{nome}_{gnome}_LOCAL.csv", index=False, encoding="utf-8")
        res["conjuntos"][nome] = bloco
    (AQUI / "_piloto_resultados_NO_PHI.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
