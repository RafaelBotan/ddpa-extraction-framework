"""
study_contract — loader + validator para contratos de estudo DDPA.

Um `study_contract.yaml` é o entry point único da plataforma. Substitui os
per-detector `run_*_l1_full.py` por uma especificação declarativa.

Estrutura do contrato (ver contracts/README.md):

    contract:       metadados (id, version, study, description, hypotheses)
    corpus:         fonte de dados (path, id/text columns, strata)
    extractor:      config universal (section_preset, language)
    variables[]:    lista de variáveis com taxonomy, criticality, detector,
                    comparison_column, silence_policy, canaries
    output:         destino (dir, format)

Este módulo apenas carrega + valida. Execução é feita por `runner.py`.

Ordem de implementação (GPT-5 R5): este é o item #1.
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


# ============================================================================
# Dataclasses do schema
# ============================================================================

@dataclass
class DetectorSpec:
    """Como o runner encontra e chama o detector L1 da variável."""
    module: str                         # nome do módulo (ex: 'bbps_l1_v4')
    function: str                       # função a chamar (ex: 'detect_bbps')
    interface: str = 'dataclass'        # 'dataclass' | 'tuple' | 'dict'
    mode: str = 'A'                     # 'A' label / 'B' copy+parse / 'C' evidence-first
    value_field: str = 'value'          # nome do campo do valor (p/ dataclass)
    status_field: Optional[str] = 'status'
    section_field: Optional[str] = 'section'
    evidence_field: Optional[str] = 'evidence'
    certainty_field: Optional[str] = None    # Mode C: campo com support_level
    scope_meta_field: Optional[str] = None   # v1.1: campo dict com audit de silêncio
    evidence_class_field: Optional[str] = None  # v1.1 #1: literal|lexical_alias|ontology_alias|bound|qualitative|silence_default
    extra_fields: list[str] = field(default_factory=list)
    pre_normalize: bool = False         # se True, chamar normalize(text) antes


@dataclass
class SilencePolicy:
    """Política de silêncio para uma variável (GPT-5 R5 adoção #3)."""
    output_ontology: list[str] = field(default_factory=list)
    default_if_silent: Optional[str] = None
    allows_implicit_negative: bool = False
    section_scope: list[str] = field(default_factory=list)


@dataclass
class Canary:
    """Caso-teste declarativo (L0 do spec v4-B)."""
    id: str
    text: str
    expected_value: Any
    expected_section: Optional[str] = None
    note: str = ''


@dataclass
class VariableSpec:
    name: str
    taxonomy: list[str]                 # ex: ['NUM-C', 'ORD-E']
    criticality: int                    # 0-10
    detector: DetectorSpec
    comparison_column: Optional[str] = None  # coluna IA original p/ cross-audit
    silence_policy: Optional[SilencePolicy] = None
    canaries: list[Canary] = field(default_factory=list)
    description: str = ''

    @property
    def criticality_band(self) -> str:
        if self.criticality >= 7:
            return 'CRITICA'
        if self.criticality >= 5:
            return 'ALTA'
        if self.criticality >= 3:
            return 'MEDIA'
        return 'BAIXA'


@dataclass
class CorpusSpec:
    path: str
    id_column: str
    text_column: str
    strata: list[str] = field(default_factory=list)
    extra_columns: list[str] = field(default_factory=list)
    row_limit: Optional[int] = None     # p/ smoke runs


@dataclass
class ExtractorConfig:
    section_preset: str = 'ENDOSCOPY_SECTIONS'  # nome da constante em framework_core
    language: str = 'pt-BR'


@dataclass
class OutputConfig:
    dir: str
    format: str = 'csv'
    write_evidence: bool = True


@dataclass
class StudyContract:
    id: str
    version: str
    study: str
    created: str
    description: str
    corpus: CorpusSpec
    extractor: ExtractorConfig
    variables: list[VariableSpec]
    output: OutputConfig
    hypotheses: list[dict] = field(default_factory=list)
    policy_registry: Optional[str] = None   # path (rel ao contrato) para registry.yaml
    source_path: Optional[str] = None   # preenchido pelo loader
    raw_yaml: str = ''                  # texto bruto p/ hash

    @property
    def contract_hash(self) -> str:
        """SHA256 do YAML cru (frozen plan, L0 v4-B)."""
        return hashlib.sha256(self.raw_yaml.encode('utf-8')).hexdigest()

    def variable(self, name: str) -> VariableSpec:
        for v in self.variables:
            if v.name == name:
                return v
        raise KeyError(f'Variável {name!r} não existe no contrato {self.id!r}')


# ============================================================================
# Loader
# ============================================================================

def _build_detector(d: dict) -> DetectorSpec:
    return DetectorSpec(
        module=d['module'],
        function=d['function'],
        interface=d.get('interface', 'dataclass'),
        mode=d.get('mode', 'A'),
        value_field=d.get('value_field', 'value'),
        status_field=d.get('status_field', 'status'),
        section_field=d.get('section_field', 'section'),
        evidence_field=d.get('evidence_field', 'evidence'),
        certainty_field=d.get('certainty_field'),
        scope_meta_field=d.get('scope_meta_field'),
        evidence_class_field=d.get('evidence_class_field'),
        extra_fields=list(d.get('extra_fields', [])),
        pre_normalize=bool(d.get('pre_normalize', False)),
    )


def _build_silence_policy(d: Optional[dict]) -> Optional[SilencePolicy]:
    if not d:
        return None
    return SilencePolicy(
        output_ontology=list(d.get('output_ontology', [])),
        default_if_silent=d.get('default_if_silent'),
        allows_implicit_negative=bool(d.get('allows_implicit_negative', False)),
        section_scope=list(d.get('section_scope', [])),
    )


def _build_canaries(lst: list) -> list[Canary]:
    out = []
    for c in lst or []:
        out.append(Canary(
            id=c['id'],
            text=c['text'],
            expected_value=c.get('expected_value'),
            expected_section=c.get('expected_section'),
            note=c.get('note', ''),
        ))
    return out


def _build_variable(d: dict) -> VariableSpec:
    return VariableSpec(
        name=d['name'],
        taxonomy=list(d['taxonomy']),
        criticality=int(d['criticality']),
        detector=_build_detector(d['detector']),
        comparison_column=d.get('comparison_column'),
        silence_policy=_build_silence_policy(d.get('silence_policy')),
        canaries=_build_canaries(d.get('canaries', [])),
        description=d.get('description', ''),
    )


def load_contract(path: str | Path) -> StudyContract:
    path = Path(path)
    raw = path.read_text(encoding='utf-8')
    data = yaml.safe_load(raw)

    if 'contract' not in data:
        raise ValueError(f'{path}: falta bloco `contract` no topo do YAML')
    c = data['contract']

    corpus_d = data.get('corpus')
    if not corpus_d:
        raise ValueError(f'{path}: falta bloco `corpus`')

    corpus = CorpusSpec(
        path=corpus_d['path'],
        id_column=corpus_d['id_column'],
        text_column=corpus_d['text_column'],
        strata=list(corpus_d.get('strata', [])),
        extra_columns=list(corpus_d.get('extra_columns', [])),
        row_limit=corpus_d.get('row_limit'),
    )

    ext_d = data.get('extractor', {})
    extractor = ExtractorConfig(
        section_preset=ext_d.get('section_preset', 'ENDOSCOPY_SECTIONS'),
        language=ext_d.get('language', 'pt-BR'),
    )

    vars_d = data.get('variables', [])
    if not vars_d:
        raise ValueError(f'{path}: contrato sem variáveis')
    variables = [_build_variable(v) for v in vars_d]

    out_d = data.get('output', {})
    output = OutputConfig(
        dir=out_d.get('dir', str(path.parent / 'runs')),
        format=out_d.get('format', 'csv'),
        write_evidence=bool(out_d.get('write_evidence', True)),
    )

    contract = StudyContract(
        id=c['id'],
        version=c['version'],
        study=c.get('study', ''),
        created=c.get('created', ''),
        description=c.get('description', ''),
        corpus=corpus,
        extractor=extractor,
        variables=variables,
        output=output,
        hypotheses=list(c.get('hypotheses', [])),
        policy_registry=c.get('policy_registry'),
        source_path=str(path),
        raw_yaml=raw,
    )
    validate_contract(contract)
    return contract


# ============================================================================
# Validação
# ============================================================================

def validate_contract(c: StudyContract) -> None:
    """Valida invariantes estruturais. Levanta ValueError se inválido."""
    names = [v.name for v in c.variables]
    if len(set(names)) != len(names):
        dupes = [n for n in names if names.count(n) > 1]
        raise ValueError(f'Variáveis duplicadas: {set(dupes)}')

    for v in c.variables:
        if not (0 <= v.criticality <= 10):
            raise ValueError(f'{v.name}: criticality {v.criticality} fora de 0-10')
        if v.detector.interface not in ('dataclass', 'tuple', 'dict'):
            raise ValueError(
                f'{v.name}: detector.interface={v.detector.interface!r} '
                'deve ser dataclass|tuple|dict'
            )
        if v.detector.mode not in ('A', 'B', 'C'):
            raise ValueError(
                f'{v.name}: detector.mode={v.detector.mode!r} deve ser A|B|C'
            )
        if v.detector.mode == 'C':
            if not v.detector.evidence_field:
                raise ValueError(
                    f'{v.name}: mode=C exige evidence_field (literal span)'
                )
            if not v.detector.certainty_field:
                raise ValueError(
                    f'{v.name}: mode=C exige certainty_field (support_level)'
                )

    # corpus file pode não existir no momento do load (ex: criando contrato);
    # deixar o runner reclamar na hora de carregar.


# ============================================================================
# Pretty print
# ============================================================================

def summarize(c: StudyContract) -> str:
    lines = [
        f'Contract: {c.id} v{c.version}',
        f'  study={c.study!r}   created={c.created}',
        f'  hash={c.contract_hash[:16]}...',
        f'  corpus: {c.corpus.path}',
        f'     id={c.corpus.id_column}  text={c.corpus.text_column}'
        f'  strata={c.corpus.strata}',
        f'  variables ({len(c.variables)}):',
    ]
    for v in c.variables:
        lines.append(
            f'    - {v.name:25s} [{",".join(v.taxonomy):15s}] '
            f'crit={v.criticality} ({v.criticality_band}) '
            f'-> {v.detector.module}.{v.detector.function}'
        )
    return '\n'.join(lines)


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 2:
        print('usage: python study_contract.py <contract.yaml>')
        sys.exit(2)
    c = load_contract(sys.argv[1])
    print(summarize(c))
