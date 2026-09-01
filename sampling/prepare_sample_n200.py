"""
Sample n=200 com over-sampling de estratos raros para re-extração via agents.
Usa o L1 ATUALIZADO (com fix de comparative bound).
"""
from pathlib import Path
import json
import pandas as pd
import numpy as np

ROOT_PILOTO = Path('Y:/DDPA/framework_extracao_v4/04_piloto')
ROOT_E1 = Path('Y:/IDOC-Endoscopia/06_analises/estudos_ejima/estudo1_qualidade_colonoscopia')

SEED = 42
N_TARGET = 200

# Alocação manual por estrato (over-sample raros)
ALLOC = {
    'qualitative_subcm': 12,       # tudo (12 no corpus)
    'qualitative_large': 20,       # 20/53
    'qualitative_diminuto': 30,    # 30/666
    'measure_interval': 20,        # 20/1238
    'measure_multi_max': 30,       # 30/2544
    'measure_explicit': 88,        # restante para fechar 200
}


def main():
    rng = np.random.default_rng(SEED)

    l1 = pd.read_csv(ROOT_PILOTO / 'tamanho_polipo_l1_v4_full.csv')
    base = pd.read_csv(
        ROOT_E1 / 'BASE_ANALITICA_ESTUDO1.csv',
        usecols=['id_registro', 'texto_laudo_completo'],
    )
    df = l1.merge(base, on='id_registro', how='left')

    elig = df[df['l1_status'].isin(ALLOC.keys())].copy()
    print(f'Eligible: {len(elig)}')

    rows = []
    for status, n_target in ALLOC.items():
        sub = elig[elig['l1_status'] == status]
        n = min(n_target, len(sub))
        if n == 0:
            continue
        picked = sub.sample(n=n, random_state=int(rng.integers(0, 1_000_000)))
        for _, r in picked.iterrows():
            rows.append({
                'id_registro': int(r['id_registro']),
                'clinica': str(r['clinica']),
                'l1_value': None if pd.isna(r['l1_value']) else float(r['l1_value']),
                'l1_status': str(r['l1_status']),
                'l1_section': None if pd.isna(r['l1_section']) else str(r['l1_section']),
                'l1_evidence': None if pd.isna(r['l1_evidence']) else str(r['l1_evidence'])[:300],
                'texto_laudo': str(r['texto_laudo_completo']),
            })

    print(f'Final n = {len(rows)}')
    print('By status:')
    print(pd.Series([r['l1_status'] for r in rows]).value_counts())
    print('By clinica:')
    print(pd.Series([r['clinica'] for r in rows]).value_counts())

    prompt_template = """Extraia o tamanho do MAIOR polipo descrito no laudo de colonoscopia abaixo.

REGRAS:
1. Retorne APENAS o que esta literalmente no texto. Nao infira, nao complete.
2. Se houver multiplos polipos com medidas, retorne a MAIOR.
3. Se for intervalo (ex: "5 a 8 mm"), retorne o limite SUPERIOR (8).
4. Se houver polipo SEM medida explicita mas mencionado: tipo="presente_sem_medida", tamanho_mm=null.
5. Se houver descricao qualitativa SEM numero (diminuto, puntiforme, subcentimetrico, volumoso, grande): tipo="qualitativo_<termo>", tamanho_mm=null.
6. Se houver bound comparativo ("menores que 5 mm", "inferiores a 10 mm"): tipo="qualitativo_diminuto" se <5mm, "qualitativo_subcm" se <10mm. NAO trate como medida pontual.
7. Se NAO houver polipo: tipo="ausente", tamanho_mm=null.
8. LST (lesao de crescimento lateral / laterally spreading tumor) CONTA como polipo.
9. NAO confunda tamanho de polipo com extensao de lesao neoplasica/estenosante, comprimento de hemorroidas, distancia da borda anal, etc.
10. NAO confunda com escores (Boston, BBPS, NICE, Mayo, Paris, Forrest, Bormann).
11. EXCLUA medidas em [INDICACAO] e [BIOPSIA].

LAUDO:
{texto}

OUTPUT JSON (e nada alem):
{{"polipo_presente": "sim|nao", "tamanho_mm": <numero ou null>, "tipo": "measure_explicit|measure_interval|measure_multi_max|qualitativo_diminuto|qualitativo_subcm|qualitativo_large|presente_sem_medida|ausente", "evidencia": "<trecho literal copiado do texto, max 200 chars>"}}
"""

    agent_ready = []
    for r in rows:
        agent_ready.append({
            'id_registro': r['id_registro'],
            'prompt': prompt_template.format(texto=r['texto_laudo']),
            'l1_value': r['l1_value'],
            'l1_status': r['l1_status'],
            'l1_evidence': r['l1_evidence'],
            'clinica': r['clinica'],
        })

    out = ROOT_PILOTO / 'sample_n200_agent_ready.json'
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(agent_ready, f, ensure_ascii=False, indent=2)
    print(f'\nSaved: {out}')


if __name__ == '__main__':
    main()
