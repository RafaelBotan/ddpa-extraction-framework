"""
policy_registry — silence policy loader + validator (GPT-5 R5 item #2).

Um registry é um YAML com políticas declarativas por variável. Separado do
study_contract porque a mesma política se aplica entre estudos (bbps é bbps
em E1, E3, ou qualquer estudo futuro).

A política declara:
- output_ontology: tipo + range/values válidos
- allows_implicit_negative: silêncio semântico = negativo?
- default_if_silent: valor imputado quando silêncio conta como default
- abstention_statuses: status L1 que devem virar abstained_*
- section_scope / section_excluded: onde a variável pode aparecer
- vocabulary_aliases: normalização de sinônimos (resolve T3 disagreements)
- logical_dependencies: restrições inter-variável textuais

O registry expõe:
- load_registry(path) -> PolicyRegistry
- validate_contract(contract, registry) -> lista de warnings
- apply_to_result(row, policy) -> (resolution_state, normalized_value)

Resolution states (do spec v4-B):
    accepted_exact | accepted_semantic
    abstained_unsupported | abstained_source_incomplete
    escalated_policy | escalated_case
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


# ============================================================================
# Dataclasses
# ============================================================================

@dataclass
class OntologySpec:
    type: str                         # 'enum' | 'integer' | 'float' | 'string'
    values: list = field(default_factory=list)
    range: Optional[tuple] = None     # (min, max) para numéricos
    unit: Optional[str] = None


@dataclass
class Policy:
    variable: str
    taxonomy: list[str]
    ontology: OntologySpec
    allows_implicit_negative: bool
    default_if_silent: Any
    abstention_statuses: list[str] = field(default_factory=list)
    section_scope: list[str] = field(default_factory=list)
    section_excluded: list[str] = field(default_factory=list)
    vocabulary_aliases: dict[str, Any] = field(default_factory=dict)
    logical_dependencies: list[str] = field(default_factory=list)
    site_template_overrides: list[dict] = field(default_factory=list)
    notes: str = ''


@dataclass
class PolicyRegistry:
    id: str
    version: str
    domain: str
    language: str
    created: str
    policies: dict[str, Policy]       # {variable_name: Policy}
    source_path: Optional[str] = None
    raw_yaml: str = ''

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.raw_yaml.encode('utf-8')).hexdigest()

    def get(self, variable: str) -> Optional[Policy]:
        return self.policies.get(variable)


# ============================================================================
# Loader
# ============================================================================

def _build_ontology(d: dict) -> OntologySpec:
    rng = d.get('range')
    if rng and isinstance(rng, list) and len(rng) == 2:
        rng = tuple(rng)
    else:
        rng = None
    return OntologySpec(
        type=d['type'],
        values=list(d.get('values', [])),
        range=rng,
        unit=d.get('unit'),
    )


def _build_policy(name: str, d: dict) -> Policy:
    ont_d = d.get('output_ontology') or d.get('ontology')
    if not ont_d:
        raise ValueError(f'Policy {name!r}: falta output_ontology')
    return Policy(
        variable=name,
        taxonomy=list(d.get('taxonomy', [])),
        ontology=_build_ontology(ont_d),
        allows_implicit_negative=bool(d.get('allows_implicit_negative', False)),
        default_if_silent=d.get('default_if_silent'),
        abstention_statuses=list(d.get('abstention_statuses', [])),
        section_scope=list(d.get('section_scope', [])),
        section_excluded=list(d.get('section_excluded', [])),
        vocabulary_aliases=dict(d.get('vocabulary_aliases', {})),
        logical_dependencies=list(d.get('logical_dependencies', [])),
        site_template_overrides=list(d.get('site_template_overrides', [])),
        notes=d.get('notes', ''),
    )


def load_registry(path: str | Path) -> PolicyRegistry:
    path = Path(path)
    raw = path.read_text(encoding='utf-8')
    data = yaml.safe_load(raw)

    reg_d = data.get('registry', {})
    policies_d = data.get('policies', {})

    policies = {name: _build_policy(name, d) for name, d in policies_d.items()}

    return PolicyRegistry(
        id=reg_d.get('id', path.stem),
        version=reg_d.get('version', '0'),
        domain=reg_d.get('domain', ''),
        language=reg_d.get('language', 'pt-BR'),
        created=reg_d.get('created', ''),
        policies=policies,
        source_path=str(path),
        raw_yaml=raw,
    )


# ============================================================================
# Contract validation — GPT-5 R5: bloqueia run se CRÍTICA não tiver policy
# ============================================================================

def validate_contract(contract, registry: PolicyRegistry,
                      min_criticality: int = 5) -> dict:
    """Retorna dict com `errors` (CRÍTICA sem policy — bloqueia run) e
    `warnings` (ALTA/MÉDIA sem policy — permite mas avisa).

    CRÍTICA = criticality >= 7. ALTA = 5-6. min_criticality define o gate.

    Erros estruturais da policy (bloqueiam run):
    - ontology.type='enum' sem `values` declarado. O auto-adjudicator v1.1 usa
      ontology.values como fonte primária da guard anti-inversão semântica; um
      enum vazio deixaria a guard (a) cega. Falhar cedo aqui é muito mais barato
      que um patch que inverte polipo_presente em produção.
    """
    errors = []
    warnings = []
    ok = []

    for v in contract.variables:
        pol = registry.get(v.name)
        if pol is None:
            if v.criticality >= 7:
                errors.append(
                    f'{v.name}: CRÍTICA (crit={v.criticality}) sem policy no registry {registry.id}'
                )
            elif v.criticality >= min_criticality:
                warnings.append(
                    f'{v.name}: crit={v.criticality} sem policy — silêncio não será validado'
                )
            continue

        # Erro estrutural: enum sem values é armadilha para auto-adjudicator.
        if pol.ontology.type == 'enum' and not pol.ontology.values:
            errors.append(
                f'{v.name}: ontology.type=enum SEM values declarados — '
                f'guard anti-inversão do auto-adjudicator ficaria cega. '
                f'Declarar `output_ontology.values` no registry.'
            )
            continue

        # Consistência: ontology.values do registry bate com o declarado no contrato?
        inline = v.silence_policy
        if inline and inline.output_ontology:
            reg_vals = pol.ontology.values
            if reg_vals and set(str(x) for x in inline.output_ontology) != set(str(x) for x in reg_vals):
                warnings.append(
                    f'{v.name}: output_ontology inline ({inline.output_ontology}) '
                    f'difere do registry ({reg_vals})'
                )
        ok.append(v.name)

    return {'errors': errors, 'warnings': warnings, 'ok': ok}


# ============================================================================
# Apply policy to a single L1 result row
# ============================================================================

@dataclass
class PolicyDecision:
    resolution: str                   # accepted_exact | accepted_semantic | abstained_* | escalated_*
    normalized_value: Any
    reason: str = ''
    # v1.1 (GPT-5 #3): audit trail de silêncio — coverage claim, não span.
    evidence_meta: Optional[dict] = None


def apply_policy(value: Any, status: Optional[str], policy: Policy) -> PolicyDecision:
    """Aplica a política a um resultado L1 e retorna estado terminal do
    resolution contract (6 estados, v4-B §0).

    Lógica:
    1. status em abstention_statuses -> abstained_unsupported
    2. value None + allows_implicit_negative + default_if_silent -> accepted_exact(default)
    3. value None + !allows_implicit_negative + default_if_silent -> abstained_source_incomplete
    4. value None sem default -> abstained_source_incomplete
    5. value em vocabulary_aliases -> accepted_semantic(aliased)
    6. value em ontology_values -> accepted_exact
    7. value fora da ontology -> escalated_case
    """
    # 1. Abstenção por status
    if status and status in policy.abstention_statuses:
        return PolicyDecision('abstained_unsupported', None, f'status={status}')

    # 2-4. Silêncio
    if value is None:
        if policy.default_if_silent is not None and policy.allows_implicit_negative:
            return PolicyDecision('accepted_exact', policy.default_if_silent,
                                  'silence_mapped_to_default')
        if policy.default_if_silent is not None:
            return PolicyDecision('abstained_source_incomplete', None,
                                  'silence_but_no_implicit_negative')
        return PolicyDecision('abstained_source_incomplete', None, 'silence')

    # 5. Alias de vocabulário -> accepted_semantic
    value_str = str(value)
    if value_str in policy.vocabulary_aliases:
        aliased = policy.vocabulary_aliases[value_str]
        return PolicyDecision('accepted_semantic', aliased,
                              f'aliased_from_{value_str}')

    # 6. Verifica ontology
    ont = policy.ontology
    if ont.type == 'enum':
        if value_str in {str(v) for v in ont.values}:
            return PolicyDecision('accepted_exact', value, 'enum_match')
        return PolicyDecision('escalated_case', value,
                              f'value {value!r} fora do enum {ont.values}')

    if ont.type in ('integer', 'float'):
        try:
            num = float(value)
        except (TypeError, ValueError):
            return PolicyDecision('escalated_case', value,
                                  f'valor nao numerico: {value!r}')
        if ont.range:
            lo, hi = ont.range
            if not (lo <= num <= hi):
                return PolicyDecision('escalated_case', value,
                                      f'out-of-range {num} not in [{lo},{hi}]')
        return PolicyDecision('accepted_exact', num, 'numeric_match')

    # string ou tipo não tratado
    return PolicyDecision('accepted_exact', value, 'default')


# ============================================================================
# Mode C — evidence-first hybrid verifier (GPT-5 R5 adoção #8)
# ============================================================================

@dataclass
class EvidenceVerdict:
    verified: bool                     # evidence é substring literal do texto?
    support: str                       # 'strong' | 'weak' | 'weak_candidate' | 'none'
    reason: str = ''                   # verifier_mode: offset_exact|offset_normalized|legacy_exact_substring|legacy_partial_substring|...
    occurrence_count: int = 0          # quantas vezes evidence ocorre em text_norm


def _normalize_for_offset(s: str) -> str:
    """Normalização barata para offset_normalized: strip + collapse whitespace."""
    return ' '.join(s.split()).strip().lower()


def verify_evidence(
    value: Any,
    evidence: Optional[str],
    certainty: Optional[str],
    text_norm: str,
    span_start: Optional[int] = None,
    span_end: Optional[int] = None,
) -> EvidenceVerdict:
    """Mode C v1.1 — offset-first hybrid verifier (GPT-5 #2, 2026-04-13).

    Ordem canônica:
      1. offset_exact         → text_norm[s:e] casa literal com evidence_core
      2. offset_normalized    → text_norm[s:e] casa após strip/lowercase/whitespace
      3. legacy_exact_substring → evidence_core é substring de text_norm (weak)
      4. legacy_partial_substring → 20-char prefix é substring (verified=False,
         support=weak_candidate) — NUNCA strong. Padrão de facto (brat, Prodigy,
         spaCy Doc.char_span, Transformers QA offset_mapping).

    Invariantes de borda (como antes):
      - value ausente + evidence ausente  → verified=True, support='none' (silêncio legítimo)
      - value presente + evidence ausente → verified=False (extração sem suporte)
      - evidence presente + offsets ruins + não-substring → verified=False
    """
    if value is None and not evidence:
        return EvidenceVerdict(True, 'none', 'silence')
    if value is not None and not evidence:
        return EvidenceVerdict(False, 'none', 'value_without_evidence')

    ev = (evidence or '').strip()
    if not ev:
        return EvidenceVerdict(False, 'none', 'empty_evidence')

    core = ev.strip('. ').strip()
    if not core:
        return EvidenceVerdict(False, 'none', 'empty_evidence')

    cert = (certainty or 'high').lower()
    strong_by_certainty = cert in ('high', 'strong')

    # 1. offset_exact — detector diz "isto está em [s:e]". Se bate literalmente,
    #    é o padrão-ouro (brat, Prodigy, spaCy char_span).
    if (span_start is not None and span_end is not None
            and span_start >= 0 and span_end > span_start
            and span_end <= len(text_norm)):
        frag = text_norm[span_start:span_end]
        # core pode estar contido em frag (evidence_around expande janela) ou frag
        # pode estar contido em core (núcleo mais largo que a janela).
        if core == frag or core in frag or frag in core:
            sup = 'strong' if strong_by_certainty else 'weak'
            return EvidenceVerdict(True, sup, 'offset_exact', occurrence_count=1)

        # 2. offset_normalized — bate após strip+lowercase+whitespace collapse.
        if _normalize_for_offset(core) == _normalize_for_offset(frag) or \
           _normalize_for_offset(core) in _normalize_for_offset(frag) or \
           _normalize_for_offset(frag) in _normalize_for_offset(core):
            sup = 'strong' if strong_by_certainty else 'weak'
            return EvidenceVerdict(True, sup, 'offset_normalized', occurrence_count=1)

    # 3. legacy_exact_substring — fallback histórico, rebaixado para weak.
    if core in text_norm:
        occ = text_norm.count(core)
        # GPT-5: legacy_exact_substring NUNCA é strong verification porque não
        # tem compromisso com posicionamento (múltiplas ocorrências podem existir).
        return EvidenceVerdict(True, 'weak', 'legacy_exact_substring', occurrence_count=occ)

    # 4. legacy_partial_substring — 20-char prefix. GPT-5: verified=False,
    #    support=weak_candidate. Escalona via apply_mode_c para evitar falso-
    #    positivo em textos com repetição.
    sub = core[:20] if len(core) > 20 else core
    if sub and sub in text_norm:
        occ = text_norm.count(sub)
        return EvidenceVerdict(False, 'weak_candidate', 'legacy_partial_substring',
                               occurrence_count=occ)

    return EvidenceVerdict(False, 'none', 'evidence_not_in_text')


def _build_silence_evidence_meta(
    scope_meta: Optional[dict],
    policy: Policy,
    status: Optional[str],
) -> dict:
    """Normaliza scope_meta emitido pelo detector em schema canônico v1.1.

    Schema (GPT-5 #3, 2026-04-13):
        evidence_kind:        'silence_default'
        policy_id:            f'{variable}.silence.{version}'
        detector_version:     repassado do detector (ou 'unknown')
        scope_complete:       bool (False se detector não declarar)
        sections_scanned:     list[str]
        chars_scanned:        int
        pattern_families_run: list[str]
        hits_total:           int
        explicit_negative_hits: int
        historical_hits:      int
    """
    base = {
        'evidence_kind': 'silence_default',
        'policy_id': f'{policy.variable}.silence.v1',
        'scope_complete': False,
        'sections_scanned': [],
        'chars_scanned': 0,
        'pattern_families_run': [],
        'hits_total': 0,
        'explicit_negative_hits': 0,
        'historical_hits': 0,
        'detector_version': 'unknown',
        'status_from_detector': status,
    }
    if scope_meta:
        for k, v in scope_meta.items():
            if k in base or k in ('detector_version', 'sections_scanned',
                                  'chars_scanned', 'pattern_families_run',
                                  'hits_total', 'explicit_negative_hits',
                                  'historical_hits', 'scope_complete'):
                base[k] = v
    return base


_EVIDENCE_CLASS_EXACT = {'literal', 'lexical_alias'}
_EVIDENCE_CLASS_SEMANTIC = {'ontology_alias', 'bound', 'qualitative'}
_EVIDENCE_CLASS_SILENCE = {'silence_default'}


def apply_mode_c(
    value: Any,
    status: Optional[str],
    evidence: Optional[str],
    certainty: Optional[str],
    text_norm: str,
    policy: Policy,
    scope_meta: Optional[dict] = None,
    span_start: Optional[int] = None,
    span_end: Optional[int] = None,
    evidence_class: Optional[str] = None,
) -> PolicyDecision:
    """Combina verificação de evidência (Mode C) com resolução de política.

    Override regras:
    - status=no_match + policy.allows_implicit_negative + scope_meta.scope_complete=True
      → accepted_exact com evidence_meta (silence_default_audited)
    - status=no_match + policy.allows_implicit_negative SEM scope_meta.scope_complete
      → abstained_source_incomplete (silêncio não auditável — GPT-5 #3)
      Exceção: sem scope_meta algum (detector legacy não retrofitted) → mantém
      accepted_exact com reason=silence_default_unaudited, flag para retrofit.
    - verified=False → escalated_case (evidence alucinada ou ausente)
    - verified=True, support='weak' → accepted_semantic se value na ontology
    - caso contrário: delega para apply_policy() normal
    """
    # v1.1: Silêncio auditável. Não é mais bypass cego — é coverage claim.
    if status == 'no_match' and policy.allows_implicit_negative:
        meta = _build_silence_evidence_meta(scope_meta, policy, status)
        if scope_meta is None:
            # Detector legacy. Backward-compat: aceita, mas marca unaudited para
            # visibilidade no manifest. Próxima iteração deve retrofit o detector.
            d = apply_policy(None, None, policy)
            meta['scope_complete'] = False
            meta['audit_status'] = 'unaudited_legacy_detector'
            return PolicyDecision(
                d.resolution, d.normalized_value,
                f'{d.reason}+mode_c:silence_default_unaudited',
                evidence_meta=meta,
            )
        if meta.get('scope_complete') is True:
            d = apply_policy(None, None, policy)
            meta['audit_status'] = 'audited_scope_complete'
            return PolicyDecision(
                d.resolution, d.normalized_value,
                f'{d.reason}+mode_c:silence_default_audited',
                evidence_meta=meta,
            )
        # scope_meta presente mas scope_complete=False: silêncio suspeito.
        meta['audit_status'] = 'scope_incomplete'
        return PolicyDecision(
            'abstained_source_incomplete', None,
            'mode_c:silence_without_scope_completeness',
            evidence_meta=meta,
        )

    verdict = verify_evidence(value, evidence, certainty, text_norm,
                              span_start=span_start, span_end=span_end)
    if not verdict.verified:
        return PolicyDecision(
            'escalated_case', value,
            f'mode_c_evidence_failed:{verdict.reason}',
        )
    base = apply_policy(value, status, policy)

    # v1.1 #1 (GPT-5): evidence_class é o eixo que determina accepted_exact
    # vs accepted_semantic. certainty é SÓ confiança epistêmica, não distância
    # semântica. Fallback: se detector não emite evidence_class, cai no
    # mapping legacy (certainty=medium → weak → accepted_semantic).
    ec = (evidence_class or '').lower().strip() or None
    if ec:
        if ec in _EVIDENCE_CLASS_EXACT and base.resolution == 'accepted_exact':
            return PolicyDecision(
                base.resolution, base.normalized_value,
                f'{base.reason}+mode_c[{ec}]:{verdict.reason}',
            )
        if ec in _EVIDENCE_CLASS_SEMANTIC and base.resolution == 'accepted_exact':
            return PolicyDecision(
                'accepted_semantic', base.normalized_value,
                f'{base.reason}+mode_c[{ec}]:{verdict.reason}',
            )
        # low certainty sem classe útil → escalated (GPT-5 #1)
        cert_lower = (certainty or '').lower()
        if ec not in _EVIDENCE_CLASS_SILENCE and cert_lower == 'low':
            return PolicyDecision(
                'escalated_case', base.normalized_value,
                f'mode_c[{ec},certainty=low]:{verdict.reason}',
            )

    if verdict.support == 'weak' and base.resolution == 'accepted_exact':
        return PolicyDecision(
            'accepted_semantic', base.normalized_value,
            f'{base.reason}+mode_c_weak:{verdict.reason}',
        )
    return PolicyDecision(
        base.resolution, base.normalized_value,
        f'{base.reason}+mode_c:{verdict.reason}',
    )


# ============================================================================
# Batch apply para DataFrame de resultados
# ============================================================================

def apply_to_dataframe(
    df,
    value_col: str,
    status_col: Optional[str],
    policy: Policy,
    mode: str = 'A',
    evidence_col: Optional[str] = None,
    certainty_col: Optional[str] = None,
    text_norm_series=None,
    scope_meta_col: Optional[str] = None,
    span_start_col: Optional[str] = None,
    span_end_col: Optional[str] = None,
    evidence_class_col: Optional[str] = None,
):
    """Adiciona colunas __resolution, __normalized, __evidence_meta ao DataFrame.

    Modo A/B: apenas apply_policy().
    Modo C: exige evidence_col, certainty_col e text_norm_series alinhada por
    índice — aplica apply_mode_c() que verifica literal span antes de resolver.
    Se scope_meta_col existir, propaga scope_meta para auditoria de silêncio (v1.1).
    Retorna dict com distribuição dos estados terminais.
    """
    import json
    import pandas as pd
    resolutions = []
    normalized = []
    evidence_metas = []
    for i, (_, row) in enumerate(df.iterrows()):
        v = row[value_col]
        if pd.isna(v):
            v = None
        s = row[status_col] if status_col and status_col in row else None
        if isinstance(s, float) and pd.isna(s):
            s = None

        if mode == 'C':
            ev = row[evidence_col] if evidence_col and evidence_col in row else None
            if isinstance(ev, float) and pd.isna(ev):
                ev = None
            cert = row[certainty_col] if certainty_col and certainty_col in row else None
            if isinstance(cert, float) and pd.isna(cert):
                cert = None
            tn = text_norm_series.iloc[i] if text_norm_series is not None else ''
            scope_meta = None
            if scope_meta_col and scope_meta_col in row:
                raw_meta = row[scope_meta_col]
                if isinstance(raw_meta, dict):
                    scope_meta = raw_meta
                elif isinstance(raw_meta, str) and raw_meta:
                    try:
                        scope_meta = json.loads(raw_meta)
                    except (json.JSONDecodeError, ValueError):
                        scope_meta = None
                elif isinstance(raw_meta, float) and pd.isna(raw_meta):
                    scope_meta = None
            ss = None
            if span_start_col and span_start_col in row:
                raw_ss = row[span_start_col]
                if not (isinstance(raw_ss, float) and pd.isna(raw_ss)):
                    try:
                        ss = int(raw_ss)
                    except (TypeError, ValueError):
                        ss = None
            se = None
            if span_end_col and span_end_col in row:
                raw_se = row[span_end_col]
                if not (isinstance(raw_se, float) and pd.isna(raw_se)):
                    try:
                        se = int(raw_se)
                    except (TypeError, ValueError):
                        se = None
            ec = None
            if evidence_class_col and evidence_class_col in row:
                raw_ec = row[evidence_class_col]
                if isinstance(raw_ec, str) and raw_ec:
                    ec = raw_ec
            d = apply_mode_c(v, s, ev, cert, tn or '', policy,
                             scope_meta=scope_meta, span_start=ss, span_end=se,
                             evidence_class=ec)
        else:
            d = apply_policy(v, s, policy)

        resolutions.append(d.resolution)
        normalized.append(d.normalized_value)
        evidence_metas.append(json.dumps(d.evidence_meta) if d.evidence_meta else None)
    df[f'{policy.variable}__resolution'] = resolutions
    df[f'{policy.variable}__normalized'] = normalized
    df[f'{policy.variable}__evidence_meta'] = evidence_metas
    return {
        'distribution': pd.Series(resolutions).value_counts().to_dict(),
    }


# ============================================================================
# CLI
# ============================================================================

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print('usage: python policy_registry.py <registry.yaml> [<contract.yaml>]')
        sys.exit(2)

    reg = load_registry(sys.argv[1])
    print(f'Registry: {reg.id} v{reg.version} ({reg.domain}, {reg.language})')
    print(f'  hash={reg.hash[:16]}...')
    print(f'  policies ({len(reg.policies)}):')
    for name, pol in reg.policies.items():
        ont = pol.ontology
        vals = (f'enum[{len(ont.values)}]' if ont.type == 'enum'
                else f'{ont.type}{ont.range if ont.range else ""}')
        print(f'    - {name:25s} taxo={pol.taxonomy}  ont={vals}  '
              f'default_silent={pol.default_if_silent!r}  '
              f'aliases={len(pol.vocabulary_aliases)}')

    if len(sys.argv) >= 3:
        from study_contract import load_contract  # type: ignore[import-not-found]
        contract = load_contract(sys.argv[2])
        res = validate_contract(contract, reg)
        print()
        print(f'Validation vs contract {contract.id}:')
        print(f'  OK: {len(res["ok"])} vars')
        for n in res['ok']:
            print(f'    OK  {n}')
        if res['warnings']:
            print(f'  WARNINGS ({len(res["warnings"])}):')
            for w in res['warnings']:
                print(f'    WARN {w}')
        if res['errors']:
            print(f'  ERRORS ({len(res["errors"])}):')
            for e in res['errors']:
                print(f'    ERR  {e}')
            sys.exit(1)
