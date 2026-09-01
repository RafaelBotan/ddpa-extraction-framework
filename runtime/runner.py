"""
runner — executa um study_contract.yaml no corpus declarado.

Fluxo:
  1. load_contract(path)
  2. carrega corpus (CSV) e valida colunas
  3. para cada variável: importa detector, roda em cada laudo
  4. roda canaries declarados (regression test L0)
  5. emite per-variable CSV + run_manifest.json (5 hashes — GPT-5 R5 adoção #5)

Substitui os `run_<var>_l1_full.py` per-detector legacy.

Uso:
    python runtime/runner.py contracts/e1_endoscopia_piloto.yaml
    python runtime/runner.py contracts/e1_endoscopia_piloto.yaml --only bbps
    python runtime/runner.py contracts/e1_endoscopia_piloto.yaml --row-limit 1000
"""
from __future__ import annotations
import argparse
import hashlib
import importlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

# garante que módulos detector (bbps_l1_v4, etc) sejam importáveis
_PILOTO_DIR = Path(__file__).parent.parent
if str(_PILOTO_DIR) not in sys.path:
    sys.path.insert(0, str(_PILOTO_DIR))

from study_contract import (  # type: ignore[import-not-found]
    StudyContract, VariableSpec, Canary, load_contract, summarize,
)
from policy_registry import (  # type: ignore[import-not-found]
    PolicyRegistry, load_registry, validate_contract as validate_policies,
    apply_to_dataframe,
)
from state_store import (  # type: ignore[import-not-found]
    checkpoint_path, load_processed, append_row, finalize,
)
from gates import (  # type: ignore[import-not-found]
    build_gate1, build_gate2, write_gates, summarize_gates,
)
from cascade import (  # noqa: E402
    build_verifier, apply_cascade,
)


# ============================================================================
# Adaptador de interfaces de detector
# ============================================================================

def _call_detector(fn: Any, text: str, pre_normalize: bool) -> Any:
    """Chama o detector. Se pre_normalize, normaliza antes (detectores pato)."""
    if pre_normalize:
        import framework_core
        text = framework_core.normalize(text)
    return fn(text)


def _extract_result(raw: Any, spec: VariableSpec) -> dict:
    """Converte output heterogêneo (dataclass/tuple/dict) para dict uniforme."""
    det = spec.detector
    iface = det.interface

    if iface == 'dataclass':
        out = {
            'value': getattr(raw, det.value_field, None),
            'status': getattr(raw, det.status_field, None) if det.status_field else None,
            'section': getattr(raw, det.section_field, None) if det.section_field else None,
            'evidence': getattr(raw, det.evidence_field, '') if det.evidence_field else '',
            # v1.1 (GPT-5 #2): offset-first verifier precisa de spans.
            'span_start': getattr(raw, 'span_start', None),
            'span_end': getattr(raw, 'span_end', None),
        }
        if det.certainty_field:
            out['certainty'] = getattr(raw, det.certainty_field, None)
        if det.scope_meta_field:
            out['scope_meta'] = getattr(raw, det.scope_meta_field, None)
        if det.evidence_class_field:
            out['evidence_class'] = getattr(raw, det.evidence_class_field, None)
        for f in det.extra_fields:
            out[f] = getattr(raw, f, None)
        return out

    if iface == 'tuple':
        # convenção pato: (value, evidence)
        value = raw[0] if len(raw) > 0 else None
        evidence = raw[1] if len(raw) > 1 else ''
        out = {'value': value, 'status': None, 'section': None, 'evidence': evidence}
        if det.scope_meta_field:
            out['scope_meta'] = None
        for f in det.extra_fields:
            out[f] = None
        return out

    if iface == 'dict':
        out = {
            'value': raw.get(det.value_field),
            'status': raw.get(det.status_field) if det.status_field else None,
            'section': raw.get(det.section_field) if det.section_field else None,
            'evidence': raw.get(det.evidence_field, '') if det.evidence_field else '',
        }
        if det.certainty_field:
            out['certainty'] = raw.get(det.certainty_field)
        if det.scope_meta_field:
            out['scope_meta'] = raw.get(det.scope_meta_field)
        for f in det.extra_fields:
            out[f] = raw.get(f)
        return out

    raise ValueError(f'Interface desconhecida: {iface}')


def _load_detector(spec: VariableSpec):
    """Importa módulo e resolve a função."""
    mod = importlib.import_module(spec.detector.module)
    return getattr(mod, spec.detector.function)


# ============================================================================
# Canaries — regression test L0
# ============================================================================

def run_canaries(spec: VariableSpec) -> list[dict]:
    fn = _load_detector(spec)
    results = []
    for c in spec.canaries:
        try:
            raw = _call_detector(fn, c.text, spec.detector.pre_normalize)
            r = _extract_result(raw, spec)
            got = r['value']
            passed = got == c.expected_value
            results.append({
                'canary_id': c.id,
                'expected': c.expected_value,
                'got': got,
                'passed': passed,
                'status': r.get('status'),
                'section': r.get('section'),
                'note': c.note,
            })
        except Exception as e:
            results.append({
                'canary_id': c.id,
                'expected': c.expected_value,
                'got': None,
                'passed': False,
                'status': f'ERROR: {type(e).__name__}: {e}',
                'section': None,
                'note': c.note,
            })
    return results


# ============================================================================
# Per-variable run
# ============================================================================

def run_variable(
    spec: VariableSpec,
    df: pd.DataFrame,
    id_col: str,
    text_col: str,
    strata_cols: list[str],
    progress: bool = True,
    ckpt_path: Optional[Path] = None,
) -> pd.DataFrame:
    """Executa detector em df, com checkpoint JSONL opcional.

    Se `ckpt_path` dado e arquivo existe, linhas com id já processado são
    puladas (resume). A cada linha nova, append em JSONL.
    """
    fn = _load_detector(spec)
    n = len(df)
    step = max(1, n // 20)

    processed: dict = {}
    if ckpt_path is not None and ckpt_path.exists():
        processed = load_processed(ckpt_path, id_col)
        if processed:
            print(f'    resume: {len(processed)}/{n} já processados em {ckpt_path.name}')

    rows = list(processed.values())

    for i, row in enumerate(df.itertuples(index=False)):
        if progress and i % step == 0:
            print(f'    {i}/{n}', flush=True)
        rid = getattr(row, id_col)
        if rid in processed:
            continue

        text = getattr(row, text_col)
        if not isinstance(text, str):
            text = ''
        try:
            raw = _call_detector(fn, text, spec.detector.pre_normalize)
            r = _extract_result(raw, spec)
        except Exception as e:
            r = {'value': None, 'status': f'ERROR:{type(e).__name__}',
                 'section': None, 'evidence': str(e)[:200]}
            for f in spec.detector.extra_fields:
                r[f] = None

        out = {id_col: rid}
        for s in strata_cols:
            out[s] = getattr(row, s, None)
        out[f'{spec.name}__value'] = r['value']
        out[f'{spec.name}__status'] = r['status']
        out[f'{spec.name}__section'] = r['section']
        out[f'{spec.name}__evidence'] = (
            (r['evidence'] or '')[:200] if isinstance(r['evidence'], str) else r['evidence']
        )
        if spec.detector.certainty_field:
            out[f'{spec.name}__certainty'] = r.get('certainty')
        if spec.detector.mode == 'C':
            out[f'{spec.name}__span_start'] = r.get('span_start')
            out[f'{spec.name}__span_end'] = r.get('span_end')
        if spec.detector.scope_meta_field:
            sm = r.get('scope_meta')
            out[f'{spec.name}__scope_meta'] = (
                json.dumps(sm) if isinstance(sm, dict) else None
            )
        if spec.detector.evidence_class_field:
            out[f'{spec.name}__evidence_class'] = r.get('evidence_class')
        for f in spec.detector.extra_fields:
            out[f'{spec.name}__{f}'] = r.get(f)
        if spec.comparison_column:
            out[f'{spec.name}__ia'] = getattr(row, spec.comparison_column, None)

        if ckpt_path is not None:
            append_row(ckpt_path, out)
        rows.append(out)

    return pd.DataFrame(rows)


def variable_summary(df: pd.DataFrame, spec: VariableSpec) -> dict:
    vcol = f'{spec.name}__value'
    scol = f'{spec.name}__status'
    iacol = f'{spec.name}__ia'
    n = len(df)
    n_filled = int(df[vcol].notna().sum())
    summary = {
        'name': spec.name,
        'n_total': n,
        'n_filled': n_filled,
        'fill_rate': round(n_filled / n, 4) if n else 0.0,
        'status_distribution': {
            str(k): int(v) for k, v in df[scol].value_counts(dropna=False).items()
        },
    }
    if spec.comparison_column and iacol in df.columns:
        ia_filled = int(df[iacol].notna().sum())
        both = df[df[vcol].notna() & df[iacol].notna()]
        if len(both):
            agree = _count_matches(both[vcol], both[iacol])
        else:
            agree = None
        summary['ia_fill'] = ia_filled
        summary['n_both_filled'] = len(both)
        summary['n_agree_both'] = agree
        if agree is not None and len(both):
            summary['agreement_surface'] = round(agree / len(both), 4)
    return summary


def _count_matches(s1: pd.Series, s2: pd.Series) -> int:
    """Compara duas séries robustamente. Tenta numeric match, cai para string.

    Nota: isto é surface agreement (exato) por design — a filosofia DDPA exige
    que divergências semânticas (ex: 'nao_recomendado' vs 'nao_aplicavel')
    sejam expostas para adjudicação de tipo, não escondidas em métrica limpa.
    """
    try:
        n1 = pd.to_numeric(s1, errors='coerce')
        n2 = pd.to_numeric(s2, errors='coerce')
        if n1.notna().all() and n2.notna().all():
            return int((n1 == n2).sum())
    except Exception:
        pass
    return int((s1.astype(str) == s2.astype(str)).sum())


# ============================================================================
# Run manifest — GPT-5 R5 adoção #5
# ============================================================================

def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module_hash(module_name: str) -> str:
    mod = importlib.import_module(module_name)
    if not mod.__file__:
        return 'nofile'
    return _file_hash(Path(mod.__file__))


def build_manifest(
    contract: StudyContract,
    started: datetime,
    completed: datetime,
    variable_summaries: list[dict],
    canary_results: dict[str, list[dict]],
    registry: Optional[PolicyRegistry] = None,
    kb_semver: str = 'v4.0',
) -> dict:
    policy_registry_hash = registry.hash if registry else 'none'
    policy_registry_id = (
        f'{registry.id}@{registry.version}' if registry else 'none'
    )
    extractor_hashes = {}
    for v in contract.variables:
        try:
            extractor_hashes[v.name] = _module_hash(v.detector.module)
        except Exception as e:
            extractor_hashes[v.name] = f'ERROR:{e}'

    framework_core_hash = 'nofile'
    try:
        framework_core_hash = _module_hash('framework_core')
    except Exception:
        pass

    return {
        'manifest_version': '1.0.0',
        'contract_id': contract.id,
        'contract_version': contract.version,
        'contract_path': contract.source_path,
        'started_at': started.isoformat(),
        'completed_at': completed.isoformat(),
        'duration_s': round((completed - started).total_seconds(), 2),
        'hashes': {
            'study_contract': contract.contract_hash,
            'kb_semver': kb_semver,
            'policy_registry': policy_registry_hash,
            'framework_core': framework_core_hash,
            'extractors': extractor_hashes,
        },
        'policy_registry_id': policy_registry_id,
        'variables': variable_summaries,
        'canaries': canary_results,
    }


# ============================================================================
# Main
# ============================================================================

def main():
    ap = argparse.ArgumentParser(description='DDPA study contract runner')
    ap.add_argument('contract', help='path to study_contract.yaml')
    ap.add_argument('--only', nargs='*', help='restringir a estas variáveis')
    ap.add_argument('--row-limit', type=int, help='processar só N primeiros laudos')
    ap.add_argument('--skip-canaries', action='store_true')
    ap.add_argument('--dry-run', action='store_true',
                    help='só valida contrato e testa canaries, não roda corpus')
    ap.add_argument('--resume', metavar='RUN_DIR',
                    help='reusa diretório de run existente e retoma de JSONL checkpoints')
    ap.add_argument('--cascade', choices=['none', 'stub', 'gpt5'], default='none',
                    help='L4 cross-family verifier aplicado em escalated_case')
    ap.add_argument('--cascade-budget', type=int, default=None,
                    help='max chamadas L4 por variável (probe mode)')
    args = ap.parse_args()

    contract = load_contract(args.contract)
    print(summarize(contract))
    print()

    # --- policy registry (GPT-5 R5 adoção #3) --------------------------------
    registry = None
    registry_validation: dict = {'errors': [], 'warnings': [], 'ok': []}
    if contract.policy_registry:
        reg_path = Path(contract.policy_registry)
        if not reg_path.is_absolute():
            reg_path = Path(contract.source_path).parent / reg_path
        if not reg_path.exists():
            print(f'ERRO: policy_registry não encontrado: {reg_path}')
            sys.exit(1)
        registry = load_registry(reg_path)
        print(f'Policy registry: {registry.id} v{registry.version} '
              f'hash={registry.hash[:16]}...')
        registry_validation = validate_policies(contract, registry)
        for n in registry_validation['ok']:
            print(f'  OK   {n}')
        for w in registry_validation['warnings']:
            print(f'  WARN {w}')
        for e in registry_validation['errors']:
            print(f'  ERR  {e}')
        if registry_validation['errors']:
            print('\nContrato bloqueado: variáveis CRÍTICAS sem policy. '
                  'Veja gate1.jsonl para patches sugeridos.')
            # emite Gate #1 e termina.
            out_dir = Path(contract.output.dir) / f'{contract.id}_blocked_{started.strftime("%Y%m%dT%H%M%S")}'
            out_dir.mkdir(parents=True, exist_ok=True)
            g1 = build_gate1(contract, registry_validation)
            write_gates(out_dir, g1, [])
            sys.exit(1)
        print()

    vars_to_run = contract.variables
    if args.only:
        vars_to_run = [v for v in contract.variables if v.name in args.only]
        missing = set(args.only) - {v.name for v in vars_to_run}
        if missing:
            print(f'AVISO: variáveis pedidas mas não existentes no contrato: {missing}')
        if not vars_to_run:
            print('Nenhuma variável selecionada. Abortando.')
            sys.exit(2)

    started = datetime.now(timezone.utc)
    t0 = time.time()

    # --- canaries ------------------------------------------------------------
    canary_results = {}
    if not args.skip_canaries:
        print('=== Canaries ===')
        for v in vars_to_run:
            if not v.canaries:
                continue
            res = run_canaries(v)
            canary_results[v.name] = res
            passed = sum(1 for r in res if r['passed'])
            total = len(res)
            flag = 'OK' if passed == total else 'FAIL'
            print(f'  {v.name:25s} {passed}/{total}  [{flag}]')
            if passed < total:
                for r in res:
                    if not r['passed']:
                        print(f'      - {r["canary_id"]}: expected={r["expected"]!r} '
                              f'got={r["got"]!r} status={r["status"]!r}')
        print()

    if args.dry_run:
        print('Dry-run solicitado. Pulando corpus.')
        return

    # --- corpus --------------------------------------------------------------
    corpus_path = Path(contract.corpus.path)
    if not corpus_path.exists():
        print(f'ERRO: corpus não encontrado: {corpus_path}')
        sys.exit(1)

    usecols = {contract.corpus.id_column, contract.corpus.text_column}
    usecols.update(contract.corpus.strata)
    usecols.update(contract.corpus.extra_columns)
    for v in vars_to_run:
        if v.comparison_column:
            usecols.add(v.comparison_column)

    print(f'Loading corpus: {corpus_path}')
    df = pd.read_csv(corpus_path, usecols=list(usecols))
    if contract.corpus.row_limit:
        df = df.head(contract.corpus.row_limit)
    if args.row_limit:
        df = df.head(args.row_limit)
    print(f'  N={len(df)}  cols={list(df.columns)}\n')

    if args.resume:
        out_dir = Path(args.resume)
        if not out_dir.exists():
            print(f'ERRO: --resume dir não existe: {out_dir}')
            sys.exit(1)
        print(f'RESUME: reusando {out_dir}')
    else:
        out_dir = Path(contract.output.dir) / f'{contract.id}_{started.strftime("%Y%m%dT%H%M%S")}'
        out_dir.mkdir(parents=True, exist_ok=True)

    variable_summaries = []
    per_var_results = {}
    for v in vars_to_run:
        print(f'=== Running {v.name} ({v.detector.module}.{v.detector.function}) ===')
        vt0 = time.time()
        csv_path = out_dir / f'{v.name}.csv'
        if args.resume and csv_path.exists():
            print(f'  SKIP (CSV já finalizado): {csv_path.name}')
            result = pd.read_csv(csv_path)
            dt = 0.0
        else:
            ckpt = checkpoint_path(out_dir, v.name)
            result = run_variable(
                v, df,
                id_col=contract.corpus.id_column,
                text_col=contract.corpus.text_column,
                strata_cols=contract.corpus.strata,
                ckpt_path=ckpt,
            )
            dt = time.time() - vt0

        # aplica silence policy se houver registry (Mode A/B) ou Mode C validator
        resolution_dist = None
        if registry is not None:
            pol = registry.get(v.name)
            if pol is not None:
                mode = v.detector.mode
                kwargs = dict(
                    value_col=f'{v.name}__value',
                    status_col=f'{v.name}__status',
                    policy=pol,
                    mode=mode,
                )
                if mode == 'C':
                    # normaliza texto uma vez para verificar literal span.
                    import framework_core
                    text_norm_series = df[contract.corpus.text_column].fillna('').map(
                        framework_core.normalize
                    )
                    kwargs.update(
                        evidence_col=f'{v.name}__evidence',
                        certainty_col=f'{v.name}__certainty',
                        text_norm_series=text_norm_series.reset_index(drop=True),
                        span_start_col=f'{v.name}__span_start',
                        span_end_col=f'{v.name}__span_end',
                    )
                    if v.detector.scope_meta_field:
                        kwargs['scope_meta_col'] = f'{v.name}__scope_meta'
                    if v.detector.evidence_class_field:
                        kwargs['evidence_class_col'] = f'{v.name}__evidence_class'
                r = apply_to_dataframe(result, **kwargs)
                resolution_dist = r['distribution']

                # Cascata L4 (GPT-5 R5 #7): só toca escalated_case.
                if args.cascade != 'none':
                    import framework_core as _fc
                    tn = df[contract.corpus.text_column].fillna('').map(_fc.normalize).reset_index(drop=True)
                    ids = df[contract.corpus.id_column].reset_index(drop=True)
                    verifier = build_verifier(args.cascade, run_dir=out_dir,
                                              max_calls=args.cascade_budget)
                    cascade_stats = apply_cascade(
                        result, v.name, pol,
                        text_norm_series=tn, id_series=ids,
                        verifier=verifier, budget=args.cascade_budget,
                    )
                    print(f'  cascade[{args.cascade}] invoked={cascade_stats["n_invoked"]} '
                          f'accepted={cascade_stats["n_accepted"]} '
                          f'rejected={cascade_stats["n_rejected"]}')
                    # reflete no resolution_dist
                    import pandas as _pd
                    resolution_dist = _pd.Series(result[f'{v.name}__resolution']).value_counts().to_dict()

                # Normaliza coluna IA para comparação no Gate #2: aplica
                # vocabulary_aliases do policy. Isto é o que fecha o loop DDPA —
                # quando humano adiciona alias via patch, o próximo run deixa
                # de ver essas entradas como disagreement.
                ia_col = f'{v.name}__ia'
                if ia_col in result.columns and pol.vocabulary_aliases:
                    def _norm_ia(x):
                        if x is None:
                            return None
                        return pol.vocabulary_aliases.get(str(x), x)
                    result[ia_col] = result[ia_col].map(_norm_ia)

        # Persiste CSV final: se veio de run novo, escreve; se resume, já existe.
        result.to_csv(csv_path, index=False)
        # Remove JSONL checkpoint agora que CSV foi materializado.
        ckpt_file = checkpoint_path(out_dir, v.name)
        if ckpt_file.exists():
            ckpt_file.unlink()

        s = variable_summary(result, v)
        s['duration_s'] = round(dt, 2)
        s['output_csv'] = str(csv_path)
        if resolution_dist is not None:
            s['resolution_distribution'] = resolution_dist
        variable_summaries.append(s)
        per_var_results[v.name] = result
        print(f'  fill_rate={s["fill_rate"]:.3f}  '
              f'({dt:.1f}s)  -> {csv_path}\n')

    # --- gates ---------------------------------------------------------------
    gate1 = build_gate1(contract, registry_validation)
    gate2 = build_gate2(per_var_results)
    gates_info = write_gates(out_dir, gate1, gate2)
    print('\n=== Gates ===')
    print(summarize_gates(gate1, gate2))
    print(f'\nGate #1: {gates_info["gate1_path"]} ({gates_info["gate1_count"]} items)')
    print(f'Gate #2: {gates_info["gate2_path"]} ({gates_info["gate2_count"]} items)')

    completed = datetime.now(timezone.utc)
    manifest = build_manifest(
        contract, started, completed,
        variable_summaries, canary_results,
        registry=registry,
    )
    manifest['gates'] = gates_info
    manifest_path = out_dir / 'run_manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding='utf-8')
    print(f'\n=== Manifest: {manifest_path}')
    print(f'Total duration: {time.time()-t0:.1f}s')


if __name__ == '__main__':
    main()
