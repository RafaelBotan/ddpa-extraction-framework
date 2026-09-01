"""
gates — Gate #1 (definições/políticas) e Gate #2 (adjudicação por tipo)
GPT-5 R5 item #5.

Filosofia DDPA: humano adjudica **tipo de discordância**, não caso. Quando o
pipeline produz N casos de escalated_case/normalization_disagreement, agrupamos
por padrão e emitimos um item de fila único por grupo. Humano edita a policy
em `endoscopia.yaml`, roda `--resume` e o efeito aplica-se a todos os casos.

Gate #1 — definitions/policies
    item_type ∈ {missing_policy, ontology_mismatch}
    trigger: validate_policies() retorna errors/warnings

Gate #2 — policy decisions
    item_type ∈ {escalated_case, normalization_disagreement, abstained_review}
    trigger: resolution column após apply_policy/apply_mode_c

Output: JSON Lines em `<run_dir>/gates/gate1.jsonl` e `gate2.jsonl`.
Humano inspeciona, adiciona `resolution` + `patch` ao item, re-roda.
"""
from __future__ import annotations
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


@dataclass
class GateItem:
    gate: str                         # 'gate1' | 'gate2'
    item_id: str                      # id único determinístico
    item_type: str
    variable: str
    count: int = 1
    description: str = ''
    examples: list[dict] = field(default_factory=list)
    suggested_patch: Optional[dict] = None
    resolution: Optional[str] = None  # preenchido por humano
    severity: str = 'info'            # 'info' | 'warn' | 'block'
    # Distribuição por stratum (clinica, era, template, ...) — alimenta skew
    # guard do auto-adjudicator. Formato: {stratum_name: {bucket: count}}.
    distribution: dict = field(default_factory=dict)


# ============================================================================
# Gate #1 — definições/políticas
# ============================================================================

def build_gate1(contract, registry_validation: dict) -> list[GateItem]:
    """Constrói itens de Gate #1 a partir do validate_policies() output.

    registry_validation: dict {errors, warnings, ok} do policy_registry.validate_contract.
    """
    items: list[GateItem] = []
    for err in registry_validation.get('errors', []):
        # formato: '{var}: CRÍTICA (crit=X) sem policy no registry {id}'
        var = err.split(':', 1)[0].strip()
        items.append(GateItem(
            gate='gate1',
            item_id=f'gate1_missing_policy_{var}',
            item_type='missing_policy',
            variable=var,
            description=err,
            severity='block',
            suggested_patch={
                'action': 'add_policy_to_registry',
                'variable': var,
                'required_fields': [
                    'taxonomy', 'output_ontology', 'allows_implicit_negative',
                    'default_if_silent', 'section_scope',
                ],
            },
        ))
    for w in registry_validation.get('warnings', []):
        var = w.split(':', 1)[0].strip()
        itype = 'ontology_mismatch' if 'output_ontology' in w else 'missing_policy_optional'
        items.append(GateItem(
            gate='gate1',
            item_id=f'gate1_{itype}_{var}',
            item_type=itype,
            variable=var,
            description=w,
            severity='warn',
        ))
    return items


# ============================================================================
# Gate #2 — decisões de política (por TIPO)
# ============================================================================

def _normalization_key(l1_value: Any, ia_value: Any) -> str:
    """Chave de agrupamento para normalization disagreements: par (L1, IA)."""
    return f'L1={l1_value!r} vs IA={ia_value!r}'


_DETECTOR_COL_SUFFIXES = (
    '__value', '__status', '__section', '__evidence', '__ia',
    '__resolution', '__normalized', '__span_start', '__span_end',
    '__scope_meta', '__evidence_class', '__certainty',
    '__cascade_accepted', '__evidence_meta',
)


def _detect_stratum_cols(df, id_col_candidates=('id_registro', 'id')) -> list[str]:
    """Stratum columns no DataFrame de uma variável: strata clínicas típicas
    (clinica, era, template, site) — qualquer coluna sem `__` e que não seja id.

    Convenção: detector outputs e metadata usam `__` (ex: `bbps__value`,
    `polipo_tamanho_max_mm__all_measurements`). Colunas "limpas" (sem `__`)
    são as copiadas via `strata_cols` + `extra_columns` do contrato. Filtro
    permissivo pode incluir `extra_columns` com dado clínico (p.ex.
    polipo_presente como comparison_column de outra var), mas skew guard
    ignora naturalmente distribuições balanceadas.
    """
    stratum_cols = []
    for c in df.columns:
        if '__' in c:
            continue
        if c in id_col_candidates:
            continue
        stratum_cols.append(c)
    return stratum_cols


def _compute_distribution(disagree_df, stratum_cols: list[str]) -> dict:
    """Por stratum_col presente, retorna {col: {bucket: n}} (bucket pode ser None).

    Limita bucket labels a str (bucket=None vira "__missing__"). Útil para
    skew guard do auto-adjudicator: se algum stratum tem bucket dominante
    acima de threshold do count total, o patch está enviesado.
    """
    dist = {}
    if len(disagree_df) == 0 or not stratum_cols:
        return dist
    for col in stratum_cols:
        if col not in disagree_df.columns:
            continue
        vc = disagree_df[col].fillna('__missing__').astype(str).value_counts()
        dist[col] = {str(k): int(v) for k, v in vc.items()}
    return dist


def build_gate2(
    per_var_results: dict,
) -> list[GateItem]:
    """Constrói itens de Gate #2 a partir dos DataFrames resultado de cada variável.

    per_var_results: {variable_name: DataFrame com colunas __value, __status,
                      __resolution, __normalized, opcionalmente __ia}
    """
    items: list[GateItem] = []
    for var, df in per_var_results.items():
        rcol = f'{var}__resolution'
        vcol = f'{var}__value'
        scol = f'{var}__status'
        iacol = f'{var}__ia'
        if rcol not in df.columns:
            continue

        # --- escalated_case: agrupar por status ----------------------------
        esc = df[df[rcol] == 'escalated_case']
        if len(esc):
            by_status = esc.groupby(scol, dropna=False).size().sort_values(ascending=False)
            for status, n in by_status.items():
                sample = esc[esc[scol] == status].head(3).to_dict(orient='records')
                items.append(GateItem(
                    gate='gate2',
                    item_id=f'gate2_escalated_{var}_{status}',
                    item_type='escalated_case',
                    variable=var,
                    count=int(n),
                    description=f'{n} casos com resolution=escalated_case, status={status!r}',
                    examples=sample,
                    severity='warn',
                    suggested_patch={
                        'action': 'add_abstention_status_or_alias',
                        'variable': var,
                        'status': status,
                    },
                ))

        # --- normalization_disagreement: L1 vs IA em ambos preenchidos ----
        # Regra consensus-first (tarefa #92): pular linhas já resolvidas por cascade.
        cascade_accepted_col = f'{var}__cascade_accepted'
        if iacol in df.columns:
            both = df[df[vcol].notna() & df[iacol].notna()]
            if cascade_accepted_col in df.columns:
                both = both[~both[cascade_accepted_col].fillna(False).astype(bool)]
                # também pular consensus-abstain (__resolution foi promovido)
                both = both[both[rcol] != 'abstained_by_consensus']
            if len(both):
                disagree = both[both[vcol].astype(str) != both[iacol].astype(str)]
                if len(disagree):
                    stratum_cols = _detect_stratum_cols(disagree)
                    pairs = Counter()
                    samples_by_pair: dict = defaultdict(list)
                    rows_by_pair: dict = defaultdict(list)
                    for _, row in disagree.iterrows():
                        key = _normalization_key(row[vcol], row[iacol])
                        pairs[key] += 1
                        if len(samples_by_pair[key]) < 3:
                            samples_by_pair[key].append(row.to_dict())
                        rows_by_pair[key].append(row)
                    for key, n in pairs.most_common(10):
                        import pandas as _pd
                        pair_df = _pd.DataFrame(rows_by_pair[key])
                        distribution = _compute_distribution(pair_df, stratum_cols)
                        items.append(GateItem(
                            gate='gate2',
                            item_id=f'gate2_disagree_{var}_{hash(key) & 0xffff:04x}',
                            item_type='normalization_disagreement',
                            variable=var,
                            count=int(n),
                            description=f'{n} casos de discordância L1 vs IA: {key}',
                            examples=samples_by_pair[key],
                            severity='info',
                            suggested_patch={
                                'action': 'add_vocabulary_alias_or_reject',
                                'variable': var,
                                'pair': key,
                            },
                            distribution=distribution,
                        ))
    return items


# ============================================================================
# Persistência
# ============================================================================

def write_gates(out_dir: Path, gate1: list[GateItem], gate2: list[GateItem]) -> dict:
    gates_dir = out_dir / 'gates'
    gates_dir.mkdir(exist_ok=True)
    g1_path = gates_dir / 'gate1.jsonl'
    g2_path = gates_dir / 'gate2.jsonl'
    with g1_path.open('w', encoding='utf-8') as f:
        for it in gate1:
            f.write(json.dumps(asdict(it), default=str, ensure_ascii=False) + '\n')
    with g2_path.open('w', encoding='utf-8') as f:
        for it in gate2:
            f.write(json.dumps(asdict(it), default=str, ensure_ascii=False) + '\n')
    return {
        'gate1_path': str(g1_path),
        'gate2_path': str(g2_path),
        'gate1_count': len(gate1),
        'gate2_count': len(gate2),
        'gate1_blocking': sum(1 for it in gate1 if it.severity == 'block'),
    }


def summarize_gates(gate1: list[GateItem], gate2: list[GateItem]) -> str:
    lines = [f'Gate #1 ({len(gate1)} items):']
    for it in gate1[:10]:
        lines.append(f'  [{it.severity}] {it.item_type:25s} {it.variable} — {it.description[:80]}')
    lines.append(f'Gate #2 ({len(gate2)} items, mostrando top-10):')
    for it in sorted(gate2, key=lambda x: -x.count)[:10]:
        lines.append(f'  [{it.severity}] {it.item_type:28s} {it.variable:25s} n={it.count:>5} — {it.description[:70]}')
    return '\n'.join(lines)
