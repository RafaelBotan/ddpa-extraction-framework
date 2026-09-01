"""
Prepara amostra estratificada para re-extração de polipo_tamanho_max_mm via
agentes do Claude Code (não API).

Objetivo metodológico:
  Medir a acurácia da IA (Claude Opus 4.6) na extração de tamanho de pólipo
  QUANDO O DADO ESTÁ NO TEXTO. L1 v4 atua como proxy de gold standard
  (extração literal determinística).

Sampling design:
  - Estratificado por L1 status (measure_explicit, measure_multi_max,
    measure_interval, qualitative_diminuto, qualitative_subcm, qualitative_large)
  - Estratificado por clínica (Clinic A, Clinic C, Clinic B)
  - EXCLUI polipo_no_size e no_polipo (são "missing data", problema metodológico
    separado da pergunta de acurácia da IA)
  - N alvo = 50 laudos para proof of concept; ajustável

Output:
  - sample_for_ia_extraction.json — uma lista de dicts {id, clinica, texto, l1_value, l1_status}
    pronta para distribuir entre K agentes Claude Code

Uso depois (próximo passo):
  Em sessão Claude Code, ler o JSON, dividir em K batches, disparar K agentes
  paralelos via Tool Agent, cada um retornando JSON com (id, ia_value,
  ia_status, ia_evidence). Aggregar e cruzar com L1 para calcular acurácia
  condicional.
"""
from pathlib import Path
import json
import pandas as pd
import numpy as np

ROOT_PILOTO = Path('Y:/DDPA/framework_extracao_v4/04_piloto')
ROOT_E1 = Path('Y:/IDOC-Endoscopia/06_analises/estudos_ejima/estudo1_qualidade_colonoscopia')

SEED = 42
N_TARGET = 50  # tamanho do sample para o proof of concept

# Estratos: combinações (status, clinica) — vamos amostrar proporcional dentro
# das classes onde o dado EXISTE no texto
STATUS_INCLUIR = [
    'measure_explicit',
    'measure_multi_max',
    'measure_interval',
    'qualitative_diminuto',
    'qualitative_subcm',
    'qualitative_large',
]


def main():
    rng = np.random.default_rng(SEED)

    print('Loading L1 output...')
    l1 = pd.read_csv(ROOT_PILOTO / 'tamanho_polipo_l1_v4_full.csv')
    print(f'  N total = {len(l1)}')

    print('Loading E1 base for laudo texts...')
    base = pd.read_csv(
        ROOT_E1 / 'BASE_ANALITICA_ESTUDO1.csv',
        usecols=['id_registro', 'texto_laudo_completo'],
    )

    df = l1.merge(base, on='id_registro', how='left')
    print(f'  After merge = {len(df)}')

    # Filtrar para casos onde o dado EXISTE no texto
    elig = df[df['l1_status'].isin(STATUS_INCLUIR)].copy()
    print(f'  Eligible (data present in text) = {len(elig)}')

    # Distribuição por status × clínica
    print('\nDistribution by status × clínica (eligible):')
    print(pd.crosstab(elig['l1_status'], elig['clinica']))

    # Amostragem estratificada
    # Estratégia: dentro de cada (status, clinica), amostrar proporcionalmente
    # mas garantindo pelo menos 1 representante por estrato não-vazio quando possível.
    rows = []
    elig['stratum'] = elig['l1_status'].astype(str) + '||' + elig['clinica'].astype(str)
    strata = elig['stratum'].value_counts()
    print(f'\nNumber of non-empty strata: {len(strata)}')

    # Alocação proporcional ao tamanho do estrato com piso 1 quando possível
    total_pop = strata.sum()
    alloc = {}
    remaining = N_TARGET
    for stratum, count in strata.items():
        share = max(1, round(N_TARGET * count / total_pop))
        alloc[stratum] = min(share, count, remaining)
        remaining -= alloc[stratum]
        if remaining <= 0:
            break
    actual_n = sum(alloc.values())
    print(f'Allocated total: {actual_n}')

    for stratum, n in alloc.items():
        sub = elig[elig['stratum'] == stratum]
        if n > 0 and len(sub) > 0:
            picked = sub.sample(n=min(n, len(sub)), random_state=int(rng.integers(0, 1_000_000)))
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

    print(f'\nFinal sample size: {len(rows)}')
    print('Final sample by status:')
    print(pd.Series([r['l1_status'] for r in rows]).value_counts())
    print('Final sample by clínica:')
    print(pd.Series([r['clinica'] for r in rows]).value_counts())

    out = ROOT_PILOTO / 'sample_for_ia_extraction.json'
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f'\nSaved: {out}')

    # Também salvar uma versão "agent-ready" com prompt embutido
    prompt_template = """Extraia o tamanho do MAIOR pólipo descrito no laudo de colonoscopia abaixo.

REGRAS:
1. Retorne APENAS o que está literalmente no texto. Não infira, não complete.
2. Se houver múltiplos pólipos com medidas, retorne a MAIOR.
3. Se for intervalo (ex: "5 a 8 mm"), retorne o limite SUPERIOR (8).
4. Se houver pólipo SEM medida explícita, retorne {{"polipo_presente": "sim", "tamanho_mm": null, "tipo": "presente_sem_medida", "evidencia": "..."}}
5. Se houver descrição qualitativa SEM número (diminuto, puntiforme, subcentimétrico, volumoso, grande), retorne {{"polipo_presente": "sim", "tamanho_mm": null, "tipo": "qualitativo_<termo>", "evidencia": "..."}}
6. Se NÃO houver pólipo, retorne {{"polipo_presente": "nao", "tamanho_mm": null, "tipo": "ausente", "evidencia": "..."}}
7. NÃO confunda tamanho de pólipo com extensão de lesão neoplásica/estenosante, comprimento de hemorroidas, distância da borda anal, etc.
8. NÃO confunda com escores (Boston, BBPS, NICE, Mayo, Paris, Forrest, Bormann).
9. EXCLUA medidas em [INDICACAO] e [BIOPSIA].

LAUDO:
{texto}

OUTPUT JSON (e nada além):
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

    out2 = ROOT_PILOTO / 'sample_for_ia_extraction_agent_ready.json'
    with open(out2, 'w', encoding='utf-8') as f:
        json.dump(agent_ready, f, ensure_ascii=False, indent=2)
    print(f'Saved: {out2}')


if __name__ == '__main__':
    main()
