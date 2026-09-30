"""PRD v6, complementos pedidos pela conselheira (2026-09-29), só agregados NO_PHI:
(a) destino de cada categoria anotada (aceito igual / aceito diferente / encaminhado / sem resposta) nas variáveis categóricas;
(b) campos sem resposta de ambos os leitores: quantos tinham valor anotado e quantos "não informa";
(c) mama: casos com pelo menos um campo encaminhado e distribuição de campos encaminhados por caso;
(d) Ki-67: cobertura e discordância de TODOS os aceitos automaticamente (concordância + suporte textual)."""
import json
import math
from pathlib import Path

import pandas as pd
from scipy.stats import beta

SAI = Path(r"<local path>")
AQUI = Path(__file__).parent
REC = json.loads((AQUI / "_conferencia_independente_piloto_NO_PHI.json").read_text(encoding="utf-8"))["resultados"]
CONJ = [("cervical", "campos_cervical_todos_LOCAL.csv", ["resultado", "adequab"]),
        ("tireoide_A", "campos_tireoide_braco_A_sorteio_LOCAL.csv", ["bethesda"]),
        ("tireoide_B1", "campos_tireoide_braco_B1_enriquecido_LOCAL.csv", ["bethesda"]),
        ("mama_ihq", "campos_mama_ihq_todos_LOCAL.csv", ["re", "rp", "her2", "ki67"])]


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


out = {"destino_por_categoria_anotada": {}, "sem_resposta_por_anotacao": {}, "mama_casos": {}, "ki67_todos_os_aceitos": {}}
for conj, arq, variaveis in CONJ:
    d = pd.read_csv(SAI / arq, dtype=str)
    for var in variaveis:
        ref = d[f"ref_{var}"].map(lambda x: canon(x, var))
        est = d[f"estado_{var}"]
        val = d[f"ddpa_{var}"].map(lambda x: canon(x, var))
        aceito = est.isin(["aceito_concordancia", "aceito_suporte_textual"])
        assert int((est == "sem_resposta").sum()) == REC[conj][var]["sem_resposta"]
        out["sem_resposta_por_anotacao"][f"{conj}|{var}"] = {
            "sem_resposta": int((est == "sem_resposta").sum()),
            "com_valor_anotado": int(((est == "sem_resposta") & ref.notna()).sum()),
            "anotacao_nao_informa": int(((est == "sem_resposta") & ref.isna()).sum())}
        if var != "ki67":
            tab = {}
            for cat in sorted(ref.fillna("NAO_INFORMA").unique()):
                m = (ref.fillna("NAO_INFORMA") == cat)
                tab[cat] = {"anotados": int(m.sum()),
                            "aceito_igual": int((m & aceito & (val == ref)).sum()),
                            "aceito_diferente": int((m & aceito & (val != ref)).sum()),
                            "encaminhado": int((m & (est == "escalado")).sum()),
                            "sem_resposta": int((m & (est == "sem_resposta")).sum())}
                assert sum(v for k, v in tab[cat].items() if k != "anotados") == tab[cat]["anotados"]
            out["destino_por_categoria_anotada"][f"{conj}|{var}"] = tab
        else:
            k, n = int((aceito & (val != ref)).sum()), int(aceito.sum())
            out["ki67_todos_os_aceitos"] = {"aceitos": n, "discordantes": k, "pct": round(100 * k / n, 1), "ic95": ic(k, n),
                                            "por_concordancia": int((est == "aceito_concordancia").sum()),
                                            "por_suporte_textual": int((est == "aceito_suporte_textual").sum())}
    if conj == "mama_ihq":
        enc = sum((d[f"estado_{v}"] == "escalado").astype(int) for v in variaveis)
        out["mama_casos"] = {"casos": len(d), "casos_com_algum_campo_encaminhado": int((enc > 0).sum()),
                             "campos_encaminhados_total": int(enc.sum()),
                             "distribuicao_campos_encaminhados_por_caso": {str(k): int(v) for k, v in enc.value_counts().sort_index().items()},
                             "casos_com_algum_campo_sem_resposta": int(sum((d[f"estado_{v}"] == "sem_resposta").astype(int) for v in variaveis).gt(0).sum())}
(AQUI / "_analise_destinos_e_casos_NO_PHI.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=1))
