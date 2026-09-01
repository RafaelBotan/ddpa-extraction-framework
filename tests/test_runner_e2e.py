"""
Teste end-to-end mínimo do runner.

Monta: contrato + registry + corpus CSV 5 laudos + detector fake.
Roda: run_variable → apply_to_dataframe(Mode C) → build_manifest.
Valida:
- distribuição de resolution: 1 exact_literal, 2 semantic (alias+bound),
  1 escalated (out-of-enum), 1 silence-default
- manifest tem as 5 hashes canônicas
- canaries executam
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

# Adiciona tests/fixtures ao path para importar fake_detector
_FIXTURES = Path(__file__).parent / 'fixtures'
if str(_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_FIXTURES))

from study_contract import (  # type: ignore[import-not-found]
    StudyContract, VariableSpec, DetectorSpec, CorpusSpec, OutputConfig, Canary,
    ExtractorConfig,
)
from policy_registry import (  # type: ignore[import-not-found]
    Policy, OntologySpec, PolicyRegistry, apply_to_dataframe,
)
from runner import run_variable, run_canaries, build_manifest  # type: ignore[import-not-found]


# ============================================================================
# Fixtures estruturais
# ============================================================================

@pytest.fixture
def fake_policy() -> Policy:
    return Policy(
        variable='fakevar',
        taxonomy=['LEX-S'],
        ontology=OntologySpec(type='enum', values=['sim', 'nao']),
        allows_implicit_negative=True,
        default_if_silent='nao',
    )


@pytest.fixture
def fake_registry(fake_policy) -> PolicyRegistry:
    return PolicyRegistry(
        id='fake_test', version='1.0.0', domain='test',
        language='pt-BR', created='2026-04-14',
        policies={'fakevar': fake_policy},
        raw_yaml='registry:\n  id: fake_test\npolicies:\n  fakevar: {}\n',
    )


@pytest.fixture
def fake_contract(tmp_path) -> StudyContract:
    detector = DetectorSpec(
        module='fake_detector',
        function='detect',
        interface='dataclass',
        mode='C',
        certainty_field='certainty',
        scope_meta_field='scope_meta',
        evidence_class_field='evidence_class',
    )
    var = VariableSpec(
        name='fakevar',
        taxonomy=['LEX-S'],
        criticality=7,
        detector=detector,
        comparison_column=None,
        canaries=[
            Canary(id='FAKE-01', text='LITERAL sim exemplo',
                   expected_value='sim', note='literal'),
            Canary(id='FAKE-02', text='apenas silêncio',
                   expected_value=None, note='silence'),
        ],
    )
    return StudyContract(
        id='fake_contract', version='1.0.0', study='TEST',
        created='2026-04-14', description='', hypotheses=[],
        corpus=CorpusSpec(path=str(tmp_path / 'corpus.csv'),
                          id_column='id', text_column='texto'),
        extractor=ExtractorConfig(section_preset='ENDOSCOPY_SECTIONS'),
        variables=[var],
        output=OutputConfig(dir=str(tmp_path / 'runs'), format='csv'),
        policy_registry=None,
        source_path=str(tmp_path / 'contract.yaml'),
        raw_yaml='contract:\n  id: fake_contract\n',
    )


@pytest.fixture
def corpus_df(tmp_path) -> pd.DataFrame:
    df = pd.DataFrame([
        {'id': 1, 'texto': 'conclusão: LITERAL sim em polipo séssil.'},
        {'id': 2, 'texto': 'achados: ALIAS lst gigante em sigmoide.'},
        {'id': 3, 'texto': 'descrição: BOUND 5-8mm polipo séssil.'},
        {'id': 4, 'texto': 'conclusão: OUTOFENUM talvez polipo incerto.'},
        {'id': 5, 'texto': 'conclusão: exame normal sem achados.'},  # silence
    ])
    return df


# ============================================================================
# Tests
# ============================================================================

class TestRunVariableE2E:

    def test_run_variable_produces_expected_columns(self, fake_contract, corpus_df):
        spec = fake_contract.variables[0]
        result = run_variable(spec, corpus_df, id_col='id', text_col='texto',
                              strata_cols=[], progress=False, ckpt_path=None)
        assert len(result) == 5
        assert 'fakevar__value' in result.columns
        assert 'fakevar__status' in result.columns
        assert 'fakevar__evidence' in result.columns
        assert 'fakevar__evidence_class' in result.columns
        assert 'fakevar__scope_meta' in result.columns
        assert 'fakevar__span_start' in result.columns

    def test_run_variable_captures_all_five_scenarios(self, fake_contract, corpus_df):
        spec = fake_contract.variables[0]
        result = run_variable(spec, corpus_df, id_col='id', text_col='texto',
                              strata_cols=[], progress=False, ckpt_path=None)
        statuses = result.sort_values('id')['fakevar__status'].tolist()
        assert statuses == ['literal_match', 'polipo_synonym', 'measure_interval',
                            'literal_match', 'no_match']

    def test_resolution_distribution_via_apply_to_dataframe(
        self, fake_contract, corpus_df, fake_policy
    ):
        import framework_core
        spec = fake_contract.variables[0]
        result = run_variable(spec, corpus_df, id_col='id', text_col='texto',
                              strata_cols=[], progress=False, ckpt_path=None)

        # normaliza texto igual ao runner faz para Mode C
        text_norm = corpus_df['texto'].map(framework_core.normalize).reset_index(drop=True)

        r = apply_to_dataframe(
            result,
            value_col='fakevar__value',
            status_col='fakevar__status',
            policy=fake_policy,
            mode='C',
            evidence_col='fakevar__evidence',
            certainty_col='fakevar__certainty',
            text_norm_series=text_norm,
            scope_meta_col='fakevar__scope_meta',
            span_start_col='fakevar__span_start',
            span_end_col='fakevar__span_end',
            evidence_class_col='fakevar__evidence_class',
        )

        dist = r['distribution']
        # 1 literal → accepted_exact
        # 2 semantic (ontology_alias, bound)
        # 1 out-of-enum → escalated_case
        # 1 silence → accepted_exact (via default)
        assert dist.get('accepted_exact', 0) == 2
        assert dist.get('accepted_semantic', 0) == 2
        assert dist.get('escalated_case', 0) == 1


class TestCanariesE2E:

    def test_canaries_run_and_report_pass_fail(self, fake_contract):
        spec = fake_contract.variables[0]
        res = run_canaries(spec)
        assert len(res) == 2
        by_id = {r['canary_id']: r for r in res}
        assert by_id['FAKE-01']['passed'] is True
        assert by_id['FAKE-01']['got'] == 'sim'
        assert by_id['FAKE-02']['passed'] is True
        assert by_id['FAKE-02']['got'] is None


class TestManifest:

    def test_manifest_has_five_canonical_hashes(self, fake_contract, fake_registry):
        # contract_hash é property derivada de raw_yaml (fixture já popula)
        started = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
        completed = datetime(2026, 4, 14, 10, 1, tzinfo=timezone.utc)
        m = build_manifest(
            fake_contract, started, completed,
            variable_summaries=[{'name': 'fakevar', 'fill_rate': 0.8}],
            canary_results={'fakevar': []},
            registry=fake_registry,
        )
        # GPT-5 R5 adoção #5: manifest precisa de 5 hashes canônicas
        hashes = m['hashes']
        assert 'study_contract' in hashes
        assert 'kb_semver' in hashes
        assert 'policy_registry' in hashes
        assert 'framework_core' in hashes
        assert 'extractors' in hashes
        # extractors é um dict {var_name: hash}
        assert 'fakevar' in hashes['extractors']

    def test_manifest_has_duration_and_ids(self, fake_contract, fake_registry):
        started = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
        completed = datetime(2026, 4, 14, 10, 0, 30, tzinfo=timezone.utc)
        m = build_manifest(
            fake_contract, started, completed,
            variable_summaries=[],
            canary_results={},
            registry=fake_registry,
        )
        assert m['duration_s'] == 30.0
        assert m['contract_id'] == 'fake_contract'
        assert m['policy_registry_id'] == 'fake_test@1.0.0'


class TestResumeCheckpoint:
    """Roda 3 ids, interrompe, retoma — deve pular os 3 e processar só os restantes."""

    def test_resume_picks_up_from_checkpoint(self, fake_contract, corpus_df, tmp_path):
        from state_store import checkpoint_path
        spec = fake_contract.variables[0]
        ckpt = checkpoint_path(tmp_path, 'fakevar')

        # 1ª "run" processa apenas 3 primeiros
        part1 = run_variable(spec, corpus_df.head(3), id_col='id', text_col='texto',
                             strata_cols=[], progress=False, ckpt_path=ckpt)
        assert len(part1) == 3
        assert ckpt.exists()

        # 2ª "run" vê corpus completo mas deve pular os 3 já processados
        part2 = run_variable(spec, corpus_df, id_col='id', text_col='texto',
                             strata_cols=[], progress=False, ckpt_path=ckpt)
        assert len(part2) == 5
        # nenhuma perda de informação
        assert set(part2['id'].tolist()) == {1, 2, 3, 4, 5}
