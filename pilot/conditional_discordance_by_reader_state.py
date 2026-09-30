"""PRD v6 (2026-09-29), análise pós-hoc declarada: discordância do MODELO com a anotação estratificada pelo estado do
DETECTOR (concorda / discorda / calado), e o espelho (discordância do detector pelo estado do modelo), por conjunto e
variável, com IC exato. Lê só as saídas por campo guardadas localmente (nunca saem da máquina); grava só agregados.
Trava: os estratos têm de fechar com a recontagem independente já publicada (Tabelas 1 e 2)."""
import json
import math
from pathlib import Path

import pandas as pd
from scipy.stats import beta

SAI = Path(r"<local path>")
AQUI = Path(__file__).parent
REC = json.loads((AQUI / "_conferencia_independente_piloto_NO_PHI.json").read_text(encoding="utf-8"))["resultados"]
CONJ = [("cervical", "campos_cervical_todos_LOCAL.csv", [("resultado", "Bethesda category"), ("adequab", "Specimen adequacy")]),
        ("tireoide_A", "campos_tireoide_braco_A_sorteio_LOCAL.csv", [("bethesda", "Bethesda category")]),
        ("tireoide_B1", "campos_tireoide_braco_B1_enriquecido_LOCAL.csv", [("bethesda", "Bethesda category")]),
        ("mama_ihq", "campos_mama_ihq_todos_LOCAL.csv", [("re", "Estrogen receptor"), ("rp", "Progesterone receptor"),
                                                        ("her2", "HER2 score"), ("ki67", "Ki-67")])]


def canon(x, var):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    s = str(x).strip()
    if s in ("", "NA", "nan", "None"):
        return None
    if var == "ki67":
        try:
            return str(int(round(float(s))))
        except ValueError:
            return None
    return s


def ic(k, n):
    if n == 0:
        return None
    lo = 0.0 if k == 0 else beta.ppf(0.025, k, n - k + 1)
    hi = 1.0 if k == n else beta.ppf(0.975, k + 1, n - k)
    return [round(100 * lo, 1), round(100 * hi, 1)]


def celula(k, n):
    return {"n": n, "discordantes": k, "pct": (round(100 * k / n, 1) if n else None), "ic95": ic(k, n)}


out = {"_o_que_e": "Análise pós-hoc (PRD v6): discordância do modelo com a anotação por estado do detector, e do detector por "
                   "estado do modelo. Discordante = valor diferente da anotação, inclusive valor onde a anotação é 'não informa'. "
                   "Só agregados.", "resultados": {}}
for conj, arq, variaveis in CONJ:
    d = pd.read_csv(SAI / arq, dtype=str, keep_default_na=True)
    out["resultados"][conj] = {}
    for var, rotulo in variaveis:
        ref = d[f"ref_{var}"].map(lambda x: canon(x, var))
        l1 = d[f"l1_{var}"].map(lambda x: canon(x, var))
        l2 = d[f"l2_{var}"].map(lambda x: canon(x, var))
        est = d[f"estado_{var}"]
        n = len(d)
        agree = (l1.notna()) & (l2.notna()) & (l1 == l2)
        disagree = (l1.notna()) & (l2.notna()) & (l1 != l2)
        det_sil = l1.isna() & l2.notna()
        mod_sil = l2.isna() & l1.notna()
        both_sil = l1.isna() & l2.isna()
        m_disc = (l2 != ref) & l2.notna()   # ref None -> True (valor onde não informa)
        d_disc = (l1 != ref) & l1.notna()
        r = REC[conj][var]
        # travas contra a recontagem publicada
        assert int(agree.sum()) == r["aceitos_por_concordancia"], (conj, var, "aceitos")
        assert int((agree & m_disc).sum()) == r["disc_aceitos"]["discordantes"], (conj, var, "disc aceitos")
        assert int(disagree.sum()) == r["composicao"]["discordancia_real"], (conj, var, "discordância real")
        sup = est == "aceito_suporte_textual"
        assert int(det_sil.sum()) == r["composicao"]["detector_absteve"] + int(sup.sum()), (conj, var, "detector calado")
        assert int(mod_sil.sum()) == r["composicao"]["modelo_absteve"], (conj, var, "modelo calado")
        assert int(both_sil.sum()) == r["sem_resposta"], (conj, var, "sem resposta")
        assert int(m_disc.sum()) == r["so_modelo"]["discordantes"] and int(l2.notna().sum()) == r["so_modelo"]["n"], (conj, var, "modelo sozinho")
        assert int(d_disc.sum()) == r["so_detector"]["discordantes"] and int(l1.notna().sum()) == r["so_detector"]["n"], (conj, var, "detector sozinho")
        bloco = {
            "variavel": rotulo, "unidades": n,
            "modelo_por_estado_do_detector": {
                "detector_concorda": celula(int((agree & m_disc).sum()), int(agree.sum())),
                "detector_discorda": celula(int((disagree & m_disc).sum()), int(disagree.sum())),
                "detector_calado": celula(int((det_sil & m_disc).sum()), int(det_sil.sum())),
            },
            "detector_por_estado_do_modelo": {
                "modelo_concorda": celula(int((agree & d_disc).sum()), int(agree.sum())),
                "modelo_discorda": celula(int((disagree & d_disc).sum()), int(disagree.sum())),
                "modelo_calado": celula(int((mod_sil & d_disc).sum()), int(mod_sil.sum())),
            },
            "ambos_calados": int(both_sil.sum()),
            "em_discordancia_quem_bate_com_a_anotacao": {
                "so_o_modelo": int((disagree & ~m_disc & d_disc).sum()),
                "so_o_detector": int((disagree & m_disc & ~d_disc).sum()),
                "nenhum": int((disagree & m_disc & d_disc).sum()),
            },
        }
        if var == "ki67":
            bloco["detector_calado_por_suporte_textual"] = {
                "com_suporte": celula(int((det_sil & sup & m_disc).sum()), int((det_sil & sup).sum())),
                "sem_suporte": celula(int((det_sil & ~sup & m_disc).sum()), int((det_sil & ~sup).sum())),
            }
        out["resultados"][conj][var] = bloco
p = AQUI / "_analise_condicional_estado_detector_NO_PHI.json"
p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
for conj in out["resultados"]:
    for var, b in out["resultados"][conj].items():
        m = b["modelo_por_estado_do_detector"]
        print(f"{conj:11s} {var:9s} modelo disc | concorda {m['detector_concorda']['discordantes']}/{m['detector_concorda']['n']} "
              f"| discorda {m['detector_discorda']['discordantes']}/{m['detector_discorda']['n']} "
              f"| calado {m['detector_calado']['discordantes']}/{m['detector_calado']['n']} || em discordância bate: "
              f"modelo {b['em_discordancia_quem_bate_com_a_anotacao']['so_o_modelo']} / detector {b['em_discordancia_quem_bate_com_a_anotacao']['so_o_detector']} / nenhum {b['em_discordancia_quem_bate_com_a_anotacao']['nenhum']}")
print("travas contra a recontagem: OK")
