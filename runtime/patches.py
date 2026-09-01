"""
patches — aplicador declarativo de decisões humanas (GPT-5 R5 item #6, v1).

Humano adjudica um item em `<run_dir>/gates/gate2.jsonl` preenchendo:
- `resolution`: 'apply' | 'reject' | 'escalate'
- `suggested_patch` (já preenchido pelo build_gate2)

Este módulo lê gate2.jsonl, filtra items com resolution='apply', e aplica os
patches ao policy_registry.yaml. Backup do registry em `.bak` antes de mudar.

v1: só suporta ações simples:
- add_vocabulary_alias: adiciona entrada em `vocabulary_aliases` da policy.
- add_abstention_status: adiciona string em `abstention_statuses`.
- add_ontology_value: adiciona valor em `output_ontology.values`.

Ações desconhecidas ou complexas (ex: add_policy) são deixadas como TODO e
ignoradas com warning. v2 (futuro): compilador genérico.

Uso:
    python runtime/patches.py <run_dir> <registry_yaml>
"""
from __future__ import annotations
import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

import yaml


def _load_gate_items(gate_path: Path) -> list[dict]:
    items = []
    if not gate_path.exists():
        return items
    with gate_path.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def _parse_pair_key(pair_str: str) -> tuple[str, str]:
    """Extrai L1_value, IA_value de uma chave 'L1=<x> vs IA=<y>'."""
    # Formato emitido em _normalization_key: f'L1={l1!r} vs IA={ia!r}'
    try:
        left, right = pair_str.split(' vs ', 1)
        l1 = left[len('L1='):].strip()
        ia = right[len('IA='):].strip()
        # strip quotes
        for q in ("'", '"'):
            if l1.startswith(q) and l1.endswith(q):
                l1 = l1[1:-1]
            if ia.startswith(q) and ia.endswith(q):
                ia = ia[1:-1]
        return l1, ia
    except Exception:
        return '', ''


CANONICAL_BINARY_VARS: frozenset = frozenset({
    'polipo_presente',
    'hernia_hiatal_estrita',
    'hernia_hiatal',
    'barrett_presente',
    'ulcera_presente',
    'ee_presente',
    'sangramento_ativo',
    'ceco_atingido',
})


# Taxonomias numéricas/ordinais — valores não são sinônimos textuais, então
# `add_vocabulary_alias_or_reject` NUNCA é seguro aplicar.
NUMERIC_TAXONOMIES: frozenset = frozenset({
    'NUM-C',   # numérico contínuo (ex: BBPS total)
    'MEAS-N',  # medida numérica (ex: tamanho polipo mm)
    'ORD-E',   # ordinal enumerado (ex: LA A/B/C/D, Bethesda I-VI)
    'NUM-I',   # numérico inteiro
})


def _max_bucket_share(distribution: dict) -> tuple[float, Optional[str], Optional[str]]:
    """Dada uma distribuição {stratum: {bucket: n}}, retorna (maior_share,
    stratum_dominante, bucket_dominante) considerando todos strata.

    Share = bucket_n / sum(stratum_ns). Se distribuição vazia, retorna (0, None, None).
    """
    best = (0.0, None, None)
    for stratum, buckets in (distribution or {}).items():
        total = sum(int(v) for v in buckets.values())
        if total <= 0:
            continue
        for bucket, n in buckets.items():
            share = int(n) / total
            if share > best[0]:
                best = (share, stratum, bucket)
    return best


def auto_adjudicate_items(
    items: list[dict],
    min_count: int = 10,
    allowed_actions: tuple = ('add_vocabulary_alias_or_reject',),
    ontologies: Optional[dict] = None,
    canonical_binary_vars: Optional[frozenset] = None,
    skew_threshold: float = 0.70,
    skew_min_count: int = 10,
    var_taxonomies: Optional[dict] = None,
) -> dict:
    """Auto-preenche `resolution` de items de Gate #2 quando SEGURO.

    Regra conservadora:
    - só `normalization_disagreement` com action em `allowed_actions`
    - count do grupo >= `min_count`  (evita decisões baseadas em N baixo)
    - item ainda não tem resolution
    - **guard anti-inversão semântica (defesa em 2 camadas)** (tarefa #93 v1.1):
      (a) se `ontologies` é fornecido e AMBOS L1_val e IA_val estão na ontology
          enum da variável, escala (inverteria valores canônicos opostos — ex:
          polipo_presente: 'nao' ↔ 'sim').
      (b) fallback: se variável está em `canonical_binary_vars` (allowlist
          explícito de binárias com valores opostos), escala mesmo sem ontology
          declarada.
    - **numeric guard** (Test E Camada 1 achado 2026-04-15):
      se `var_taxonomies` fornecido e var tem taxonomia numérica
      (NUM-C/MEAS-N/ORD-E/NUM-I), escala. Valores numéricos não são sinônimos
      textuais — `vocabulary_alias` nunca é a resolução correta.
    - **skew guard (tarefa #95, DDPA must-close #2 do double-check GPT)**:
      se o item tem `distribution` (construído por build_gate2) e algum stratum
      (clinica, era, template...) tem bucket dominante com share >= `skew_threshold`
      do count total daquele stratum E count >= `skew_min_count`, escala.
      Impede que padrão de 1 clínica/era/template vire regra universal do registry.

    Items sem essas condições ficam com resolution='escalate' (vai para humano).
    Edita items in-place e retorna {auto_applied, escalated, untouched,
    escalated_reasons}.
    """
    if canonical_binary_vars is None:
        canonical_binary_vars = CANONICAL_BINARY_VARS
    auto, esc, untouched = [], [], []
    reasons = {}
    for it in items:
        if it.get('resolution'):
            untouched.append(it['item_id'])
            continue
        if it.get('item_type') != 'normalization_disagreement':
            esc.append(it['item_id'])
            it['resolution'] = 'escalate'
            reasons[it['item_id']] = 'not_normalization_disagreement'
            continue
        patch = it.get('suggested_patch') or {}
        action = patch.get('action')
        if action not in allowed_actions:
            esc.append(it['item_id'])
            it['resolution'] = 'escalate'
            reasons[it['item_id']] = f'action_not_safe:{action}'
            continue
        count = int(it.get('count', 0))
        if count < min_count:
            esc.append(it['item_id'])
            it['resolution'] = 'escalate'
            reasons[it['item_id']] = f'count_below_threshold:{it.get("count")}'
            continue
        # guard anti-inversão semântica (2 camadas)
        if action == 'add_vocabulary_alias_or_reject':
            var = patch.get('variable') or it.get('variable')
            pair = patch.get('pair', '')
            l1_val, ia_val = _parse_pair_key(pair)
            # (a) ontology-based
            ont_vals = (ontologies or {}).get(var, [])
            if ont_vals and l1_val in ont_vals and ia_val in ont_vals:
                esc.append(it['item_id'])
                it['resolution'] = 'escalate'
                reasons[it['item_id']] = (
                    f'canonical_value_inversion:{l1_val}<->{ia_val} '
                    f'both in ontology'
                )
                continue
            # (b) allowlist fallback para binárias sem enum declarada
            if var in canonical_binary_vars and l1_val and ia_val and l1_val != ia_val:
                esc.append(it['item_id'])
                it['resolution'] = 'escalate'
                reasons[it['item_id']] = (
                    f'canonical_binary_inversion:{l1_val}<->{ia_val} '
                    f'var in canonical_binary_vars allowlist'
                )
                continue
            # (c) numeric guard (Test E Camada 1, 2026-04-15): vocab_alias
            # não se aplica a variáveis numéricas/ordinais; patch seria
            # clinicamente errado (ex: alias '10.0' -> '5.0' em tamanho).
            taxonomy = set((var_taxonomies or {}).get(var, set()) or [])
            if taxonomy & NUMERIC_TAXONOMIES:
                esc.append(it['item_id'])
                it['resolution'] = 'escalate'
                reasons[it['item_id']] = (
                    f'numeric_variable:{var} taxonomy={sorted(taxonomy)} '
                    f'vocab_alias nao aplicavel a numericos'
                )
                continue
        # skew guard: distribuição dominada por 1 bucket em algum stratum
        if count >= skew_min_count:
            dist = it.get('distribution') or {}
            share, stratum, bucket = _max_bucket_share(dist)
            if stratum is not None and share >= skew_threshold:
                esc.append(it['item_id'])
                it['resolution'] = 'escalate'
                reasons[it['item_id']] = (
                    f'skew:{stratum}={bucket} share={share:.2f}>=threshold={skew_threshold:.2f} '
                    f'(count={count})'
                )
                continue
        it['resolution'] = 'apply'
        it['auto_adjudicated'] = True
        auto.append(it['item_id'])
    return {
        'auto_applied': auto,
        'escalated': esc,
        'untouched': untouched,
        'escalated_reasons': reasons,
    }


def apply_patches(gate_path: Path, registry_path: Path, dry_run: bool = False) -> dict:
    """Lê gate items com resolution='apply' e edita registry YAML.

    Retorna {applied: [...], skipped: [...], errors: [...]}.
    """
    items = _load_gate_items(gate_path)
    registry_raw = registry_path.read_text(encoding='utf-8')
    registry = yaml.safe_load(registry_raw)
    policies = registry.setdefault('policies', {})

    applied, skipped, errors = [], [], []

    for it in items:
        res = (it.get('resolution') or '').lower()
        if res != 'apply':
            skipped.append({
                'item_id': it['item_id'],
                'reason': f'resolution={res!r}',
            })
            continue

        patch = it.get('suggested_patch') or {}
        action = patch.get('action')
        var = patch.get('variable') or it.get('variable')
        if var not in policies:
            errors.append({
                'item_id': it['item_id'],
                'reason': f'variable {var!r} não existe no registry',
            })
            continue

        pol = policies[var]

        if action == 'add_vocabulary_alias_or_reject':
            # IA -> L1 (assume L1 é canônico)
            pair = patch.get('pair', '')
            l1_val, ia_val = _parse_pair_key(pair)
            if not (l1_val and ia_val):
                errors.append({'item_id': it['item_id'],
                               'reason': f'pair inválido: {pair!r}'})
                continue
            aliases = pol.setdefault('vocabulary_aliases', {}) or {}
            if ia_val in aliases:
                skipped.append({'item_id': it['item_id'],
                                'reason': f'alias {ia_val} já existe'})
                continue
            aliases[ia_val] = l1_val
            pol['vocabulary_aliases'] = aliases
            applied.append({'item_id': it['item_id'],
                            'change': f'{var}: alias {ia_val!r} -> {l1_val!r}'})

        elif action == 'add_abstention_status_or_alias':
            status = patch.get('status')
            if not status:
                errors.append({'item_id': it['item_id'],
                               'reason': 'status ausente'})
                continue
            abstentions = pol.setdefault('abstention_statuses', []) or []
            if status in abstentions:
                skipped.append({'item_id': it['item_id'],
                                'reason': f'status {status} já em abstention_statuses'})
                continue
            abstentions.append(status)
            pol['abstention_statuses'] = abstentions
            applied.append({'item_id': it['item_id'],
                            'change': f'{var}: +abstention {status}'})

        elif action == 'add_ontology_value':
            val = patch.get('value')
            if val is None:
                errors.append({'item_id': it['item_id'],
                               'reason': 'value ausente'})
                continue
            ont = pol.setdefault('output_ontology', {})
            values = ont.setdefault('values', []) or []
            if val in values:
                skipped.append({'item_id': it['item_id'],
                                'reason': f'value {val} já na ontology'})
                continue
            values.append(val)
            ont['values'] = values
            applied.append({'item_id': it['item_id'],
                            'change': f'{var}: +ontology value {val}'})

        else:
            skipped.append({'item_id': it['item_id'],
                            'reason': f'action não suportada em v1: {action!r}'})

    if not dry_run and applied:
        bak = registry_path.with_suffix(registry_path.suffix + '.bak')
        shutil.copy(registry_path, bak)
        with registry_path.open('w', encoding='utf-8') as f:
            yaml.safe_dump(registry, f, allow_unicode=True, sort_keys=False)
        print(f'Registry atualizado: {registry_path} (backup em {bak})')

    return {'applied': applied, 'skipped': skipped, 'errors': errors}


def main():
    ap = argparse.ArgumentParser(description='DDPA declarative patcher v1')
    ap.add_argument('run_dir', help='diretório do run (contendo gates/gate2.jsonl)')
    ap.add_argument('registry', help='policy_registry.yaml a editar')
    ap.add_argument('--dry-run', action='store_true',
                    help='mostra patches mas não escreve registry')
    ap.add_argument('--auto-adjudicate', action='store_true',
                    help='preenche resolution=apply automaticamente para disagreements '
                         'com count>=min-count e action segura (tarefa #93)')
    ap.add_argument('--min-count', type=int, default=10,
                    help='threshold para auto-adjudicação (default: 10)')
    ap.add_argument('--skew-threshold', type=float, default=0.70,
                    help='share máximo permitido em um bucket de stratum antes de escalar '
                         '(default: 0.70 — tarefa #95)')
    ap.add_argument('--skew-min-count', type=int, default=10,
                    help='count mínimo para ativar skew guard (default: 10)')
    args = ap.parse_args()

    gate = Path(args.run_dir) / 'gates' / 'gate2.jsonl'
    reg = Path(args.registry)
    if not gate.exists():
        print(f'ERRO: {gate} não existe')
        sys.exit(1)
    if not reg.exists():
        print(f'ERRO: {reg} não existe')
        sys.exit(1)

    if args.auto_adjudicate:
        items = _load_gate_items(gate)
        # carrega ontologias do registry para anti-inversão guard
        reg_data = yaml.safe_load(reg.read_text(encoding='utf-8')) or {}
        ontologies = {}
        for var, pol in (reg_data.get('policies') or {}).items():
            ont = (pol.get('output_ontology') or {}).get('values') or []
            ontologies[var] = [str(v) for v in ont]
        stats = auto_adjudicate_items(
            items, min_count=args.min_count, ontologies=ontologies,
            skew_threshold=args.skew_threshold,
            skew_min_count=args.skew_min_count,
        )
        # reescreve gate2.jsonl com resolutions preenchidas
        with gate.open('w', encoding='utf-8') as f:
            for it in items:
                f.write(json.dumps(it, default=str, ensure_ascii=False) + '\n')
        print(f'[auto-adjudicate] applied={len(stats["auto_applied"])} '
              f'escalated={len(stats["escalated"])} untouched={len(stats["untouched"])}')
        # mostra razões de escalation (útil para debug)
        reasons = stats.get('escalated_reasons', {})
        inversions = [k for k, r in reasons.items() if 'inversion' in r]
        if inversions:
            print(f'[auto-adjudicate] {len(inversions)} patches bloqueados por '
                  f'anti-inversão semântica (valores canônicos opostos)')
        skews = [k for k, r in reasons.items() if r.startswith('skew:')]
        if skews:
            print(f'[auto-adjudicate] {len(skews)} patches bloqueados por skew guard '
                  f'(padrão concentrado em 1 stratum — tarefa #95)')

    result = apply_patches(gate, reg, dry_run=args.dry_run)

    print(f'Applied ({len(result["applied"])}):')
    for a in result['applied']:
        print(f'  + {a["item_id"]}: {a["change"]}')
    print(f'Skipped ({len(result["skipped"])}):')
    for s in result['skipped'][:10]:
        print(f'  - {s["item_id"]}: {s["reason"]}')
    if result['errors']:
        print(f'Errors ({len(result["errors"])}):')
        for e in result['errors']:
            print(f'  ! {e["item_id"]}: {e["reason"]}')


if __name__ == '__main__':
    main()
