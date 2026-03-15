"""Tests for EVIE database layer — row-to-model converters and query functions."""

from unittest.mock import patch, MagicMock

from src.evie.db import (
    _row_to_evidence_object, _row_to_envelope, _pair_evidence_with_envelope,
    get_client, list_trials, get_trial_summary,
    search_evidence, get_evidence_detail, get_safety_data,
)


class TestRowToEvidenceObject:
    def test_full_row(self, sample_evidence_row):
        eo = _row_to_evidence_object(sample_evidence_row)
        assert eo.id == "eo-001"
        assert eo.object_class == "primary_endpoint"
        assert eo.result_value == -15.2
        assert eo.confidence_interval == [-16.1, -14.3]
        assert eo.p_value == 0.0001

    def test_null_confidence_interval(self, sample_evidence_row):
        sample_evidence_row["confidence_interval_low"] = None
        sample_evidence_row["confidence_interval_high"] = None
        eo = _row_to_evidence_object(sample_evidence_row)
        assert eo.confidence_interval is None

    def test_null_result_value(self, sample_evidence_row):
        sample_evidence_row["result_value"] = None
        eo = _row_to_evidence_object(sample_evidence_row)
        assert eo.result_value is None

    def test_null_p_value(self, sample_evidence_row):
        sample_evidence_row["p_value"] = None
        eo = _row_to_evidence_object(sample_evidence_row)
        assert eo.p_value is None


class TestRowToEnvelope:
    def test_full_envelope(self, sample_envelope_row):
        env = _row_to_envelope(sample_envelope_row)
        assert env.safety_statement == "Common adverse events include nausea, vomiting, diarrhea."
        assert env.interpretation_guardrails == "Results apply to the mITT population."
        assert env.source_provenance is not None
        assert env.source_provenance.trial_name == "STEP-4"

    def test_string_provenance_ignored(self, sample_envelope_row):
        sample_envelope_row["source_provenance"] = "not a dict"
        env = _row_to_envelope(sample_envelope_row)
        assert env.source_provenance is None

    def test_null_optional_fields(self, sample_envelope_row):
        sample_envelope_row["population_constraints"] = None
        sample_envelope_row["endpoint_definition"] = None
        sample_envelope_row["source_provenance"] = None
        env = _row_to_envelope(sample_envelope_row)
        assert env.population_constraints is None
        assert env.source_provenance is None


class TestPairEvidenceWithEnvelope:
    def test_valid_pair(self, sample_evidence_row, sample_envelope_row):
        pair = _pair_evidence_with_envelope(sample_evidence_row, sample_envelope_row)
        assert pair is not None
        assert pair.evidence_object.id == "eo-001"
        assert pair.context_envelope.safety_statement is not None

    def test_missing_envelope_returns_none(self, sample_evidence_row):
        pair = _pair_evidence_with_envelope(sample_evidence_row, None)
        assert pair is None


# ─── Query function tests ───────────────────────────────────────────────────


class TestGetClient:
    def test_creates_client_without_token(self):
        with patch("src.evie.db.create_client") as mock_create:
            mock_client = MagicMock()
            mock_create.return_value = mock_client
            result = get_client()
            mock_create.assert_called_once()
            mock_client.postgrest.auth.assert_not_called()
            assert result is mock_client

    def test_creates_client_with_token(self):
        with patch("src.evie.db.create_client") as mock_create:
            mock_client = MagicMock()
            mock_create.return_value = mock_client
            result = get_client(access_token="user-jwt")
            mock_client.postgrest.auth.assert_called_once_with("user-jwt")
            assert result is mock_client



class TestListTrials:
    def test_returns_trial_summaries(self):
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.data = [
            {
                "id": "t-1",
                "name": "STEP-4",
                "drug_name": "Semaglutide",
                "indication": "Obesity",
                "phase": "Phase 3",
                "evidence_objects": [
                    {"object_class": "primary_endpoint"},
                    {"object_class": "adverse_event"},
                    {"object_class": "primary_endpoint"},  # duplicate
                ],
            }
        ]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_result

        trials = list_trials(mock_client)
        assert len(trials) == 1
        assert trials[0].name == "STEP-4"
        assert trials[0].available_object_classes == ["adverse_event", "primary_endpoint"]

    def test_skips_trials_with_no_evidence(self):
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.data = [
            {"id": "t-1", "name": "Empty", "evidence_objects": []},
        ]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_result

        trials = list_trials(mock_client)
        assert len(trials) == 0


class TestGetTrialSummary:
    def test_returns_summary_dict(self, sample_evidence_row, sample_envelope_row):
        mock_client = MagicMock()

        # Trial query
        trial_result = MagicMock()
        trial_result.data = [{"id": "t-1", "name": "STEP-4", "drug_name": "Sema", "indication": "Obesity", "phase": "3"}]

        # Evidence query (with nested envelope)
        eo_row = {**sample_evidence_row, "context_envelopes": [sample_envelope_row]}
        eo_result = MagicMock()
        eo_result.data = [eo_row]

        # Chain: table().select().eq().limit().execute() for trial
        # Chain: table().select().eq().eq().execute() for evidence
        call_count = [0]
        def table_side_effect(name):
            mock_table = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:  # trials
                mock_table.select.return_value.eq.return_value.limit.return_value.execute.return_value = trial_result
            else:  # evidence_objects
                mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = eo_result
            return mock_table
        mock_client.table.side_effect = table_side_effect

        summary = get_trial_summary(mock_client, "t-1")
        assert summary is not None
        assert summary["trial"]["name"] == "STEP-4"
        assert len(summary["primary_endpoints"]) == 1

    def test_returns_none_for_unknown_trial(self):
        mock_client = MagicMock()
        trial_result = MagicMock()
        trial_result.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = trial_result

        assert get_trial_summary(mock_client, "unknown") is None


class TestSearchEvidence:
    def test_returns_paired_results(self, sample_evidence_row, sample_envelope_row):
        mock_client = MagicMock()
        eo_row = {**sample_evidence_row, "context_envelopes": [sample_envelope_row]}
        mock_result = MagicMock()
        mock_result.data = [eo_row]
        mock_client.table.return_value.select.return_value.limit.return_value.text_search.return_value.execute.return_value = mock_result

        results = search_evidence(mock_client, "weight loss")
        assert len(results) == 1
        assert results[0].evidence_object.id == "eo-001"

    def test_filters_by_trial_and_class(self, sample_evidence_row, sample_envelope_row):
        mock_client = MagicMock()
        eo_row = {**sample_evidence_row, "context_envelopes": [sample_envelope_row]}
        mock_result = MagicMock()
        mock_result.data = [eo_row]

        q = mock_client.table.return_value.select.return_value
        q.eq.return_value.eq.return_value.limit.return_value.text_search.return_value.execute.return_value = mock_result

        results = search_evidence(mock_client, "weight", trial_id="t-1", object_class="primary_endpoint")
        assert len(results) == 1

    def test_skips_rows_without_envelope(self, sample_evidence_row):
        mock_client = MagicMock()
        eo_row = {**sample_evidence_row, "context_envelopes": []}
        mock_result = MagicMock()
        mock_result.data = [eo_row]
        mock_client.table.return_value.select.return_value.limit.return_value.text_search.return_value.execute.return_value = mock_result

        results = search_evidence(mock_client, "test")
        assert len(results) == 0


class TestGetEvidenceDetail:
    def test_returns_evidence_with_envelope(self, sample_evidence_row, sample_envelope_row):
        mock_client = MagicMock()
        eo_row = {**sample_evidence_row, "context_envelopes": [sample_envelope_row]}
        mock_result = MagicMock()
        mock_result.data = [eo_row]
        mock_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = mock_result

        result = get_evidence_detail(mock_client, "eo-001")
        assert result is not None
        assert result.evidence_object.id == "eo-001"

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = mock_result

        assert get_evidence_detail(mock_client, "nonexistent") is None


class TestGetSafetyData:
    def test_returns_adverse_events(self, sample_evidence_row, sample_envelope_row):
        mock_client = MagicMock()
        sample_evidence_row["object_class"] = "adverse_event"
        eo_row = {**sample_evidence_row, "context_envelopes": [sample_envelope_row]}
        mock_result = MagicMock()
        mock_result.data = [eo_row]
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.execute.return_value = mock_result

        results = get_safety_data(mock_client, "t-1")
        assert len(results) == 1

    def test_excludes_entries_without_safety_statement(self, sample_evidence_row, sample_envelope_row):
        mock_client = MagicMock()
        sample_envelope_row["safety_statement"] = ""
        eo_row = {**sample_evidence_row, "context_envelopes": [sample_envelope_row]}
        mock_result = MagicMock()
        mock_result.data = [eo_row]
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.execute.return_value = mock_result

        results = get_safety_data(mock_client, "t-1")
        assert len(results) == 0

    def test_returns_empty_for_no_data(self):
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.execute.return_value = mock_result

        results = get_safety_data(mock_client, "t-1")
        assert len(results) == 0
