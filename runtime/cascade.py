"""
cascade — Cross-family L4 verifier (GPT-5 R5 item #7, interface v1).

L4 é o árbitro da cascata DDPA:
    L1 (detector) -> L2 (evidence verify) -> L3 (policy resolver) -> L4 (cross-family)

Só é invocado para linhas cuja resolution de L3 é 'escalated_case' (ou, opcional,
'escalated_policy'). L4 pede a uma família diferente de modelo para produzir:
    {value, evidence_span, confidence}
A partir do texto original + da ontology da policy.

Decisão terminal:
- value ∈ ontology_values + evidence ∈ texto → accepted_cross_verified
- caso contrário, permanece escalated_case (humano via Gate #2)

Por construção, L4 nunca 'rebaixa' um accepted de L3. Cascata assimétrica:
aceitamos se L3 já resolveu; L4 só intervém quando L3 escalou.

Constraint operacional (2026-04-13): user.feedback_evitar_api_ate_creditos_acabarem
    Preferir subscription (Code agents). API (GPT-5) só em produção pós-paper
    ou quando créditos esgotarem. Por isso o GPT5Verifier aqui é skeleton —
    levanta NotImplementedError até o toggle ser virado explicitamente.

Uso no runner:
    --cascade none|stub|gpt5 [--cascade-budget N]
    'none'  — desativa (default)
    'stub'  — lê verdicts pré-computados de <run_dir>/cascade_cache.jsonl
    'gpt5'  — chama API GPT-5 (skeleton, requer OPENAI_API_KEY + opt-in)
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional, Protocol


# ============================================================================
# Verdict contract
# ============================================================================

@dataclass
class CrossVerdict:
    accepted: bool
    model: str                          # 'null' | 'stub' | 'gpt-5' | ...
    value: Any = None                   # valor proposto pelo modelo L4
    evidence: Optional[str] = None      # span textual
    confidence: Optional[str] = None    # 'high' | 'medium' | 'low'
    reason: str = ''
    raw: Optional[dict] = None          # resposta bruta (para auditoria)


class CrossFamilyVerifier(Protocol):
    """Protocolo: qualquer implementação que cumpra `.verify(...)`."""
    name: str

    def verify(
        self,
        text: str,
        variable: str,
        candidate_value: Any,
        ontology_values: list,
        ontology_type: str,
    ) -> CrossVerdict: ...


# ============================================================================
# NullVerifier — default (no-op)
# ============================================================================

class NullVerifier:
    name = 'null'

    def verify(self, text, variable, candidate_value, ontology_values, ontology_type):
        return CrossVerdict(
            accepted=False, model='null',
            reason='cascade disabled',
        )


# ============================================================================
# StubVerifier — lê verdicts pré-computados de arquivo JSONL
# ============================================================================

class StubVerifier:
    """Lê `cascade_cache.jsonl` com linhas {id_key, variable, verdict:{...}}.

    Útil para:
    - Rodar L4 offline (ex: via outro script que chama API em lote)
    - Reproducibility: congela respostas num arquivo versionável
    - Probe de viabilidade: pré-computar N casos, medir cost/latency,
      comparar kappa vs Gate #2 sem rebuildar a cascata.
    """
    name = 'stub'

    def __init__(self, cache_path: Path, id_series=None):
        self.cache_path = Path(cache_path)
        self.cache: dict[tuple, CrossVerdict] = {}
        if self.cache_path.exists():
            with self.cache_path.open('r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = (str(row.get('id_key')), row.get('variable'))
                    v = row.get('verdict') or {}
                    self.cache[key] = CrossVerdict(
                        accepted=bool(v.get('accepted', False)),
                        model=v.get('model', 'stub'),
                        value=v.get('value'),
                        evidence=v.get('evidence'),
                        confidence=v.get('confidence'),
                        reason=v.get('reason', 'stub_cached'),
                        raw=v.get('raw'),
                    )
        self._current_id = None

    def set_current_id(self, id_key):
        self._current_id = str(id_key)

    def verify(self, text, variable, candidate_value, ontology_values, ontology_type):
        key = (self._current_id, variable)
        v = self.cache.get(key)
        if v is None:
            return CrossVerdict(
                accepted=False, model='stub',
                reason=f'cache_miss:{key}',
            )
        return v


# ============================================================================
# GPT5Verifier — skeleton. NÃO CHAMA API até opt-in explícito.
# ============================================================================

GPT5_PROMPT_TEMPLATE = """\
Você é um árbitro independente de extração médica. Leia o laudo abaixo e
adjudique a variável solicitada.

Variável: {variable}
Ontology permitida: {ontology}

REGRAS DE ADJUDICAÇÃO (nesta ordem):

R1. HIERARQUIA DE SEÇÕES. Só conte como achado atual o que está em
    [DESCRIÇÃO], [ACHADOS] ou [CONCLUSÃO]. NÃO conte:
    - [INDICAÇÃO], [HISTÓRICO], [ANTECEDENTES] (historicidade)
    - [PREPARO], [SEDAÇÃO], [EQUIPAMENTO] (logística, não achado)
    - Instruções de dieta/preparo ("dieta sem resíduos por 3 dias")

R2. STUB INSTITUCIONAL. Se o laudo tiver <150 caracteres clínicos ou for
    apenas cabeçalho institucional (ex: "IPAD INSTITUTO..." sem conteúdo),
    retorne value="nao_mencionado", confidence="high",
    evidence="" e reason="stub_report".

R3. SILÊNCIO vs NEGAÇÃO. Distinguir:
    - Negação explícita ("ausência de pólipos") → valor negativo
    - Silêncio (variável não mencionada) → "nao_mencionado"
    - Historicidade ("pós-polipectomia prévia") → "nao_mencionado" para o atual

R4. EVIDENCE OBRIGATÓRIO. Se value ≠ "nao_mencionado", o campo `evidence`
    DEVE ser uma substring LITERAL do laudo (copie caracteres exatos, sem
    paráfrase, sem acentuação alterada).

R5. ONTOLOGY. `value` deve estar em {ontology} ou ser "nao_mencionado".
    Não invente rótulos.

R6. CONFIANÇA. `confidence` ∈ {{"high", "medium", "low"}}:
    - high: evidence inequívoca e literal
    - medium: evidence presente mas exige inferência lexical simples
    - low: evidence fraca ou ambígua → ainda assim responda; humano revisa

RESPONDA EM JSON com: value, evidence, confidence, reason.

LAUDO:
{text}

JSON:
"""


class GPT5Verifier:
    """OpenAI-family verifier. Gated por ALLOW_L4_API=1 + OPENAI_API_KEY.

    Default model: gpt-4.1-mini (barato, estruturado, adequado para
    adjudicação com evidence-in-text check pós-hoc).

    Cache opcional em JSONL: se `cache_path` é passado, respostas são
    persistidas (dedup por key (id_key, variable)) e reutilizadas em
    reruns — idempotência + custo zero na segunda passagem.
    """
    name = 'gpt-4.1-mini'

    def __init__(self, model: str = 'gpt-4.1-mini',
                 max_calls: Optional[int] = None,
                 cache_path: Optional[Path] = None):
        self.model = model
        self.name = model
        self.max_calls = max_calls
        self.n_calls = 0
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache: dict[tuple, CrossVerdict] = {}
        if self.cache_path and self.cache_path.exists():
            with self.cache_path.open('r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get('model') != model:
                        continue
                    key = (str(row.get('id_key')), row.get('variable'))
                    v = row.get('verdict') or {}
                    self.cache[key] = CrossVerdict(
                        accepted=bool(v.get('accepted', False)),
                        model=v.get('model', model),
                        value=v.get('value'),
                        evidence=v.get('evidence'),
                        confidence=v.get('confidence'),
                        reason=v.get('reason', 'cache_hit'),
                        raw=v.get('raw'),
                    )
        self._current_id = None
        self._enabled = (
            os.environ.get('ALLOW_L4_API') == '1'
            and bool(os.environ.get('OPENAI_API_KEY'))
        )

    def set_current_id(self, id_key):
        self._current_id = str(id_key)

    def _write_cache(self, verdict: CrossVerdict, variable: str):
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            'id_key': self._current_id,
            'variable': variable,
            'model': self.model,
            'verdict': {
                'accepted': verdict.accepted,
                'model': verdict.model,
                'value': verdict.value,
                'evidence': verdict.evidence,
                'confidence': verdict.confidence,
                'reason': verdict.reason,
                'raw': verdict.raw,
            },
        }
        with self.cache_path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')

    def verify(self, text, variable, candidate_value, ontology_values, ontology_type):
        # 1. Cache lookup — reruns sem custo.
        key = (self._current_id, variable)
        if key in self.cache:
            v = self.cache[key]
            return CrossVerdict(
                accepted=v.accepted, model=v.model, value=v.value,
                evidence=v.evidence, confidence=v.confidence,
                reason=f'cache_hit:{v.reason}', raw=v.raw,
            )

        # 2. Gate operacional.
        if not self._enabled:
            return CrossVerdict(
                False, self.model,
                reason='disabled:set ALLOW_L4_API=1 + OPENAI_API_KEY',
            )
        if self.max_calls is not None and self.n_calls >= self.max_calls:
            return CrossVerdict(False, self.model, reason='budget_exhausted')

        prompt = GPT5_PROMPT_TEMPLATE.format(
            variable=variable,
            ontology=ontology_values,
            text=(text or '')[:8000],
        )

        try:
            from openai import OpenAI
            client = OpenAI()
            resp = client.chat.completions.create(
                model=self.model,
                temperature=0,
                response_format={'type': 'json_object'},
                messages=[{'role': 'user', 'content': prompt}],
            )
            self.n_calls += 1
            raw_content = resp.choices[0].message.content
            data = json.loads(raw_content)
        except Exception as e:
            v = CrossVerdict(False, self.model,
                             reason=f'api_error:{type(e).__name__}:{e}')
            self._write_cache(v, variable)
            return v

        value = data.get('value')
        evidence = data.get('evidence') or ''
        confidence = data.get('confidence')
        reason_api = data.get('reason', '')

        # 3. Validações pós-hoc.
        if value == 'nao_mencionado':
            accepted = False
            reason = f'abstain:{reason_api}' if reason_api else 'abstain'
        else:
            value_ok = str(value) in {str(x) for x in ontology_values}
            evidence_in_text = bool(evidence) and (evidence in (text or ''))
            accepted = value_ok and evidence_in_text
            if not value_ok:
                reason = f'value_off_ontology:{value!r}'
            elif not evidence_in_text:
                reason = 'evidence_not_in_text'
            else:
                reason = f'accepted:{reason_api}' if reason_api else 'accepted'

        verdict = CrossVerdict(
            accepted=accepted, model=self.model, value=value,
            evidence=evidence, confidence=confidence,
            reason=reason, raw=data,
        )
        self._write_cache(verdict, variable)
        return verdict


# ============================================================================
# ConsensusVerifier — combina múltiplas famílias com regra de consenso estrita
# ============================================================================

class ConsensusVerifier:
    """N verifiers independentes; aceita só se TODOS aceitam com MESMO value.

    Regra:
      - all accept + same value  → accepted_cross_verified
      - all abstain (accepted=False por nao_mencionado/stub) → abstained_by_consensus
      - qualquer outro caso       → escalated_case (humano via Gate #2)

    O campo `model` do verdict combinado lista as famílias. O `reason` reporta
    o motivo estrutural (agree / abstain_consensus / split).
    """
    def __init__(self, verifiers: list, require_all_accept: bool = True):
        self.verifiers = verifiers
        self.require_all_accept = require_all_accept
        self.name = 'consensus(' + ','.join(v.name for v in verifiers) + ')'

    def set_current_id(self, id_key):
        for v in self.verifiers:
            if hasattr(v, 'set_current_id'):
                v.set_current_id(id_key)

    def verify(self, text, variable, candidate_value, ontology_values, ontology_type):
        verdicts = []
        for v in self.verifiers:
            try:
                verdicts.append(v.verify(text, variable, candidate_value,
                                         ontology_values, ontology_type))
            except Exception as e:
                verdicts.append(CrossVerdict(
                    False, getattr(v, 'name', '?'),
                    reason=f'error:{type(e).__name__}:{e}',
                ))

        models = ','.join(v.model for v in verdicts)
        all_accept = all(v.accepted for v in verdicts)
        all_abstain = all(not v.accepted for v in verdicts)
        values = {str(v.value) for v in verdicts if v.accepted}

        if all_accept and len(values) == 1:
            chosen = verdicts[0]
            return CrossVerdict(
                accepted=True, model=models, value=chosen.value,
                evidence=chosen.evidence, confidence=chosen.confidence,
                reason=f'consensus_agree:{chosen.reason}',
                raw={'verdicts': [v.__dict__ for v in verdicts]},
            )
        if all_abstain:
            return CrossVerdict(
                accepted=False, model=models, value=None,
                evidence=None, confidence='high',
                reason='abstained_by_consensus',
                raw={'verdicts': [v.__dict__ for v in verdicts]},
            )
        # divergência — mantém escalated_case
        return CrossVerdict(
            accepted=False, model=models, value=None,
            evidence=None, confidence='low',
            reason=f'split:{[(v.model, v.value, v.accepted) for v in verdicts]}',
            raw={'verdicts': [v.__dict__ for v in verdicts]},
        )


# ============================================================================
# Integration helper — aplica verifier nos escalated_case de um DF
# ============================================================================

def apply_cascade(
    df,
    variable: str,
    policy,                             # Policy
    text_norm_series,                   # pd.Series alinhada por índice posicional
    id_series,                          # pd.Series id do laudo
    verifier: CrossFamilyVerifier,
    budget: Optional[int] = None,
    only_resolutions: tuple = ('escalated_case',),
) -> dict:
    """Aplica verifier em linhas selecionadas. Edita df in-place:
    - Cria colunas <var>__cascade_model, <var>__cascade_accepted, <var>__cascade_value,
      <var>__cascade_evidence, <var>__cascade_reason.
    - Se aceito, sobrescreve <var>__resolution para 'accepted_cross_verified'
      e <var>__normalized para verdict.value.

    Retorna {n_invoked, n_accepted, n_rejected, budget_hit}.
    """
    import pandas as pd
    rcol = f'{variable}__resolution'
    ncol = f'{variable}__normalized'
    vcol = f'{variable}__value'
    if rcol not in df.columns:
        return {'n_invoked': 0, 'n_accepted': 0, 'n_rejected': 0, 'budget_hit': False}

    # init cascade columns
    df[f'{variable}__cascade_model'] = ''
    df[f'{variable}__cascade_accepted'] = False
    df[f'{variable}__cascade_value'] = None
    df[f'{variable}__cascade_evidence'] = ''
    df[f'{variable}__cascade_reason'] = ''

    mask = df[rcol].isin(only_resolutions)
    idxs = list(df.index[mask])
    n_invoked = n_accepted = n_rejected = 0
    budget_hit = False

    ont_vals = policy.ontology.values
    ont_type = policy.ontology.type

    for i in idxs:
        if budget is not None and n_invoked >= budget:
            budget_hit = True
            break
        pos = df.index.get_loc(i)
        text = text_norm_series.iloc[pos] if text_norm_series is not None else ''
        id_key = id_series.iloc[pos] if id_series is not None else None
        if hasattr(verifier, 'set_current_id') and id_key is not None:
            verifier.set_current_id(id_key)
        cand = df.at[i, vcol] if vcol in df.columns else None

        try:
            v = verifier.verify(text or '', variable, cand, ont_vals, ont_type)
        except NotImplementedError as e:
            v = CrossVerdict(False, getattr(verifier, 'name', '?'),
                             reason=f'not_implemented:{e}')

        n_invoked += 1
        df.at[i, f'{variable}__cascade_model'] = v.model
        df.at[i, f'{variable}__cascade_accepted'] = bool(v.accepted)
        df.at[i, f'{variable}__cascade_value'] = v.value
        df.at[i, f'{variable}__cascade_evidence'] = v.evidence or ''
        df.at[i, f'{variable}__cascade_reason'] = v.reason

        if v.accepted and v.value is not None:
            # promove a accepted_cross_verified
            if (ont_type != 'enum') or (str(v.value) in {str(x) for x in ont_vals}):
                df.at[i, rcol] = 'accepted_cross_verified'
                df.at[i, ncol] = v.value
                n_accepted += 1
            else:
                n_rejected += 1
        elif v.reason == 'abstained_by_consensus':
            # consenso de abstenção é terminal — não vai para Gate #2
            df.at[i, rcol] = 'abstained_by_consensus'
            n_rejected += 1
        else:
            n_rejected += 1

    return {
        'n_invoked': n_invoked,
        'n_accepted': n_accepted,
        'n_rejected': n_rejected,
        'budget_hit': budget_hit,
    }


# ============================================================================
# Factory
# ============================================================================

def build_verifier(kind: str, run_dir: Optional[Path] = None,
                   max_calls: Optional[int] = None,
                   model: str = 'gpt-4.1-mini') -> CrossFamilyVerifier:
    kind = (kind or 'none').lower()
    if kind in ('none', 'null', ''):
        return NullVerifier()
    if kind == 'stub':
        if run_dir is None:
            raise ValueError('stub verifier requer run_dir para cascade_cache.jsonl')
        return StubVerifier(Path(run_dir) / 'cascade_cache.jsonl')
    if kind in ('gpt5', 'gpt-5', 'gpt', 'openai', 'gpt-4.1-mini'):
        cache = Path(run_dir) / 'cascade_cache.jsonl' if run_dir else None
        return GPT5Verifier(model=model, max_calls=max_calls, cache_path=cache)
    if kind == 'consensus':
        # Por default: Claude subagent (stub) + GPT (openai).
        if run_dir is None:
            raise ValueError('consensus requer run_dir')
        stub = StubVerifier(Path(run_dir) / 'cascade_cache.jsonl')
        gpt = GPT5Verifier(model=model, max_calls=max_calls,
                           cache_path=Path(run_dir) / 'cascade_cache.jsonl')
        return ConsensusVerifier([stub, gpt])
    raise ValueError(f'cascade kind desconhecido: {kind!r}')
