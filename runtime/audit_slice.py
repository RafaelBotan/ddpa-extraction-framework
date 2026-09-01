"""audit_slice — amostragem estratificada para auditoria humana dos paths
"silenciosos" do DDPA (must-close #3 do double-check GPT, task #96).

Motivação:
    O runtime produz 4 resoluções que NÃO passam por Gate #2:
      - accepted_exact        — L1 já canônico, IA não envolvida
      - accepted_semantic     — alias aplicado, IA deu mesmo valor normalizado
      - accepted_cross_verified — cascade L4 (modelos independentes) concordaram
      - abstained_by_consensus — cascade L4 (modelos independentes) abstiveram

    Se dois modelos independentes sempre concordam MAS estão ambos errados
    (viés sistemático compartilhado — dados de treino similares, bug idêntico),
    nunca descobriríamos. Este módulo extrai amostra estratificada desses
    caminhos "silenciosos" para revisão humana — prova que consenso não é
    conluio.

Saída:
    <run_dir>/audit/silent_consensus_sample.jsonl — 1 registro por laudo
    amostrado, com evidence, value, resolution, stratum. Humano preenche
    `human_label` e `human_note`, re-roda análise para quantificar
    accuracy dentro do grupo silencioso.

    <run_dir>/audit/audit_manifest.json — contagem de população vs. amostrado
    por (var × resolution × stratum); seed; parâmetros.

Uso:
    python runtime/audit_slice.py <run_dir> [--per-stratum 10] [--seed 42]
                                            [--resolutions RES ...]
                                            [--stratum-cols COL ...]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd


SILENT_RESOLUTIONS: tuple = (
    'accepted_exact',
    'accepted_semantic',
    'accepted_cross_verified',
    'abstained_by_consensus',
)


def _sample_group(g: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    if len(g) <= n:
        return g
    return g.sample(n=n, random_state=seed)


def build_audit_slice(
    run_dir: Path,
    per_stratum: int = 10,
    resolutions: Iterable[str] = SILENT_RESOLUTIONS,
    stratum_cols: Iterable[str] = ('clinica',),
    seed: int = 42,
) -> dict:
    """Gera amostra estratificada dos resolutions silenciosos.

    Retorna dict com {sample_path, manifest_path, total_sampled, per_var}.
    """
    resolutions = tuple(resolutions)
    stratum_cols = tuple(stratum_cols)

    audit_dir = run_dir / 'audit'
    audit_dir.mkdir(exist_ok=True)
    sample_path = audit_dir / 'silent_consensus_sample.jsonl'
    manifest_path = audit_dir / 'audit_manifest.json'

    per_var = {}
    total_pop = 0
    total_sampled = 0

    with sample_path.open('w', encoding='utf-8') as out:
        for csv_path in sorted(run_dir.glob('*.csv')):
            var = csv_path.stem
            df = pd.read_csv(csv_path)
            rcol = f'{var}__resolution'
            if rcol not in df.columns:
                continue

            pool = df[df[rcol].isin(resolutions)].copy()
            pop_by_resolution = (
                pool[rcol].value_counts(dropna=False).to_dict()
            )
            if len(pool) == 0:
                per_var[var] = {
                    'population': 0, 'sampled': 0,
                    'by_resolution': pop_by_resolution,
                }
                continue

            strat_cols_effective = [rcol] + [
                c for c in stratum_cols if c in pool.columns
            ]
            groups = pool.groupby(strat_cols_effective, dropna=False, sort=False)
            sampled_frames = [
                _sample_group(g, per_stratum, seed) for _, g in groups
            ]
            sampled = (
                pd.concat(sampled_frames, ignore_index=True)
                if sampled_frames else pool.iloc[0:0]
            )

            vcol = f'{var}__value'
            ncol = f'{var}__normalized'
            scol = f'{var}__status'
            ecol = f'{var}__evidence'
            seccol = f'{var}__section'
            iacol = f'{var}__ia'
            ecls = f'{var}__evidence_class'
            certcol = f'{var}__certainty'

            for _, row in sampled.iterrows():
                record = {
                    'variable': var,
                    'id_registro': row.get('id_registro'),
                    'resolution': row.get(rcol),
                    'value': row.get(vcol),
                    'normalized': row.get(ncol) if ncol in sampled.columns else None,
                    'status': row.get(scol),
                    'section': row.get(seccol),
                    'evidence': row.get(ecol),
                    'evidence_class': row.get(ecls),
                    'certainty': row.get(certcol),
                    'ia_value': row.get(iacol) if iacol in sampled.columns else None,
                    'strata': {c: row.get(c) for c in stratum_cols if c in sampled.columns},
                    # campos para humano preencher
                    'human_label': None,
                    'human_note': None,
                }
                out.write(json.dumps(record, default=str, ensure_ascii=False) + '\n')

            per_var[var] = {
                'population': int(len(pool)),
                'sampled': int(len(sampled)),
                'by_resolution': pop_by_resolution,
                'strata_used': strat_cols_effective,
            }
            total_pop += len(pool)
            total_sampled += len(sampled)

    manifest = {
        'seed': seed,
        'per_stratum': per_stratum,
        'resolutions': list(resolutions),
        'stratum_cols': list(stratum_cols),
        'total_population': total_pop,
        'total_sampled': total_sampled,
        'per_variable': per_var,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, default=str, ensure_ascii=False),
        encoding='utf-8',
    )

    return {
        'sample_path': str(sample_path),
        'manifest_path': str(manifest_path),
        'total_population': total_pop,
        'total_sampled': total_sampled,
        'per_variable': per_var,
    }


def sample_disagreement_audit(
    df: pd.DataFrame,
    var_name: str,
    n_per_direction: int = 50,
    strata: str = 'clinica',
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Sample cases where L1 and IA disagree, stratified by clinic.

    Returns:
        l1_filled_ia_disagree: both L1 and IA filled, but values differ
        l1_null_ia_filled: L1=null but IA has value
    """
    vcol = f'{var_name}__value'
    iacol = f'{var_name}__ia'

    empty = pd.DataFrame()

    if vcol not in df.columns or iacol not in df.columns:
        return empty, empty

    # Direction 1: both filled but values differ (string comparison)
    both_filled = df[vcol].notna() & df[iacol].notna()
    values_differ = df[vcol].astype(str) != df[iacol].astype(str)
    dir1_mask = both_filled & values_differ
    dir1 = df[dir1_mask].copy()

    # Direction 2: L1 null, IA filled
    dir2_mask = df[vcol].isna() & df[iacol].notna()
    dir2 = df[dir2_mask].copy()

    def _stratified_sample(subset: pd.DataFrame, n: int) -> pd.DataFrame:
        if len(subset) == 0:
            return subset
        if strata in subset.columns:
            groups = subset.groupby(strata, dropna=False, sort=False)
            frames = [_sample_group(g, n, seed) for _, g in groups]
            result = pd.concat(frames, ignore_index=True) if frames else subset.iloc[0:0]
            # Cap total to n_per_direction
            if len(result) > n:
                result = result.sample(n=n, random_state=seed)
            return result
        return _sample_group(subset, n, seed)

    l1_filled_ia_disagree = _stratified_sample(dir1, n_per_direction)
    l1_null_ia_filled = _stratified_sample(dir2, n_per_direction)

    return l1_filled_ia_disagree, l1_null_ia_filled


def main():
    ap = argparse.ArgumentParser(
        description='DDPA audit slice sampler (task #96 / must-close #3)'
    )
    ap.add_argument('run_dir', help='run directory com CSVs por variável')
    ap.add_argument('--per-stratum', type=int, default=10,
                    help='N linhas amostradas por (resolution × stratum) (default: 10)')
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--resolutions', nargs='*', default=None,
                    help=f'resolutions a auditar (default: {list(SILENT_RESOLUTIONS)})')
    ap.add_argument('--stratum-cols', nargs='*', default=['clinica'],
                    help='colunas de stratum (default: [clinica])')
    args = ap.parse_args()

    run = Path(args.run_dir)
    if not run.exists():
        print(f'ERRO: {run} não existe', file=sys.stderr)
        sys.exit(1)

    res = build_audit_slice(
        run,
        per_stratum=args.per_stratum,
        resolutions=tuple(args.resolutions) if args.resolutions else SILENT_RESOLUTIONS,
        stratum_cols=tuple(args.stratum_cols),
        seed=args.seed,
    )
    print(f'[audit-slice] population={res["total_population"]} '
          f'sampled={res["total_sampled"]}')
    for var, stats in res['per_variable'].items():
        print(f'  {var}: pop={stats["population"]} sampled={stats["sampled"]}  '
              f'by_resolution={stats["by_resolution"]}')
    print(f'Sample: {res["sample_path"]}')
    print(f'Manifest: {res["manifest_path"]}')


if __name__ == '__main__':
    main()
