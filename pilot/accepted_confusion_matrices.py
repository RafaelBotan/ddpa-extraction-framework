"""Matrizes de confusão (valor aceito por concordância × leitura especialista) por variável categórica, e diferenças
absolutas do Ki-67 nos aceitos discordantes. Lê as saídas por campo locais; grava só contagens agregadas (NO_PHI)."""
import json
from collections import Counter
from pathlib import Path
import pandas as pd

S = Path(__file__).parent.parent / "MANUSCRIPT_v1" / "_piloto_saidas"
CONJ = [("cervical", "campos_cervical_todos_LOCAL.csv", ["resultado", "adequab"]),
        ("tireoide_A", "campos_tireoide_braco_A_sorteio_LOCAL.csv", ["bethesda"]),
        ("tireoide_B1", "campos_tireoide_braco_B1_enriquecido_LOCAL.csv", ["bethesda"]),
        ("mama_ihq", "campos_mama_ihq_todos_LOCAL.csv", ["re", "rp", "her2", "ki67"])]
out = {}
for nome, arq, variaveis in CONJ:
    d = pd.read_csv(S / arq, dtype=str)
    for v in variaveis:
        m = d[d[f"estado_{v}"] == "aceito_concordancia"]
        ref = m[f"ref_{v}"].fillna("NAO_INFORMA")
        val = m[f"ddpa_{v}"]
        if v == "ki67":
            r = pd.to_numeric(m[f"ref_{v}"], errors="coerce")
            x = pd.to_numeric(val, errors="coerce")
            dif = (x - r).abs()
            disc = m[(r.isna()) | (x != r)]
            out[f"{nome}|{v}"] = {"aceitos": int(len(m)), "discordantes": int(len(disc)),
                                  "ref_nao_informa_entre_discordantes": int(r[disc.index].isna().sum()),
                                  "diferenca_absoluta_pontos": sorted(int(z) for z in dif[disc.index].dropna())}
        else:
            ct = Counter(zip(val, ref))
            out[f"{nome}|{v}"] = {"aceitos": int(len(m)),
                                  "matriz": [{"aceito": a, "referencia": b, "n": int(n)} for (a, b), n in sorted(ct.items())]}
(Path(__file__).parent / "_matrizes_confusao_aceitos_NO_PHI.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
for k, v in out.items():
    if "matriz" in v:
        fora = [(z["aceito"], z["referencia"], z["n"]) for z in v["matriz"] if z["aceito"] != z["referencia"]]
        print(k, "aceitos", v["aceitos"], "| fora da diagonal:", fora)
    else:
        print(k, v)
