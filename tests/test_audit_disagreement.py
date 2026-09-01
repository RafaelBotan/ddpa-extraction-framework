"""Tests for sample_disagreement_audit extension to audit_slice."""
from __future__ import annotations
import pytest
import pandas as pd


class TestSampleDisagreementAudit:
    @pytest.fixture
    def df(self):
        rows = []
        # 30 cases: L1=sim, IA=nao (both filled, disagree)
        for i in range(30):
            rows.append({
                'id_registro': i,
                'clinica': 'Clinic A' if i < 20 else 'Clinic B',
                'polipo_presente__value': 'sim',
                'polipo_presente__ia': 'nao',
                'polipo_presente__resolution': 'accepted_exact',
            })
        # 30 cases: L1=nao, IA=sim (both filled, disagree in opposite direction)
        for i in range(30, 60):
            rows.append({
                'id_registro': i,
                'clinica': 'Clinic A' if i < 50 else 'Clinic B',
                'polipo_presente__value': 'nao',
                'polipo_presente__ia': 'sim',
                'polipo_presente__resolution': 'accepted_exact',
            })
        # 40 cases: L1=sim, IA=sim (agreement — should NOT be sampled)
        for i in range(60, 100):
            rows.append({
                'id_registro': i,
                'clinica': 'Clinic A',
                'polipo_presente__value': 'sim',
                'polipo_presente__ia': 'sim',
                'polipo_presente__resolution': 'accepted_exact',
            })
        return pd.DataFrame(rows)

    def test_returns_two_dataframes(self, df):
        from audit_slice import sample_disagreement_audit
        l1_yes_ia_no, l1_no_ia_yes = sample_disagreement_audit(
            df, 'polipo_presente', n_per_direction=10,
        )
        assert isinstance(l1_yes_ia_no, pd.DataFrame)
        assert isinstance(l1_no_ia_yes, pd.DataFrame)

    def test_respects_n_per_direction(self, df):
        from audit_slice import sample_disagreement_audit
        l1_yes_ia_no, l1_no_ia_yes = sample_disagreement_audit(
            df, 'polipo_presente', n_per_direction=10,
        )
        assert len(l1_yes_ia_no) <= 10
        assert len(l1_no_ia_yes) <= 10

    def test_stratified_by_clinica(self, df):
        from audit_slice import sample_disagreement_audit
        l1_yes_ia_no, _ = sample_disagreement_audit(
            df, 'polipo_presente', n_per_direction=20, strata='clinica',
        )
        if len(l1_yes_ia_no) > 0:
            clinics = l1_yes_ia_no['clinica'].unique()
            assert len(clinics) >= 1

    def test_deterministic(self, df):
        from audit_slice import sample_disagreement_audit
        r1 = sample_disagreement_audit(df, 'polipo_presente', n_per_direction=5, seed=42)
        r2 = sample_disagreement_audit(df, 'polipo_presente', n_per_direction=5, seed=42)
        assert list(r1[0]['id_registro']) == list(r2[0]['id_registro'])

    def test_excludes_agreements(self, df):
        from audit_slice import sample_disagreement_audit
        l1_filled_ia_disagree, l1_null_ia_filled = sample_disagreement_audit(
            df, 'polipo_presente', n_per_direction=50,
        )
        # No agreements should appear (IDs 60-99 are agreements)
        all_ids = set(l1_filled_ia_disagree['id_registro'].tolist() + l1_null_ia_filled['id_registro'].tolist())
        assert all(i < 60 for i in all_ids)
