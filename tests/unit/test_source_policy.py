"""The source policy matrix must cover the dataset registry conservatively."""

from __future__ import annotations

from pathlib import Path

import pytest

from cnequity.compliance.source_policy import (
    PERMISSION_FIELDS,
    REQUIRED_FIELDS,
    SourcePolicyValidationError,
    is_explicitly_allowed,
    load_source_policies,
    policies_for_dataset,
    required_sources,
    usage_profile,
    validate_source_policies,
)

ROOT = Path(__file__).resolve().parents[2]


def test_matrix_covers_every_registered_source():
    policies = load_source_policies()

    assert required_sources() <= policies.keys()
    assert policies["derived"].derived is True
    assert policies_for_dataset("daily_bars", policies).primary.name == "tdx_protocol"


def test_every_policy_has_nonempty_required_fields():
    policies = load_source_policies()

    for source, policy in policies.items():
        assert REQUIRED_FIELDS <= set(policy)
        for field_name in REQUIRED_FIELDS:
            value = policy[field_name]
            assert value is not None, (source, field_name)
            assert not isinstance(value, str) or value.strip(), (source, field_name)


def test_unknown_permission_is_not_an_allow():
    policies = load_source_policies()
    policy = policies["sina"]

    for field_name in PERMISSION_FIELDS:
        assert policy[field_name] == "unknown"
        assert is_explicitly_allowed(policy[field_name]) is False

    assessment = usage_profile(policy, commercial=True, redistribution=True)
    assert assessment.allowed is False
    assert assessment.decision == "blocked"
    assert set(assessment.blocked_fields) == {"commercial_use", "redistribution"}


def test_reviewed_restricted_terms_remain_fail_closed():
    policies = load_source_policies()
    for source in ("eastmoney", "ths"):
        policy = policies[source]
        assert policy["tos_url"].startswith("https://")
        assert policy["tos_reviewed_at"] == "2026-08-29"
        assert policy["legal_status"] == "restricted"
        assert not is_explicitly_allowed(policy["commercial_use"])
        assert not is_explicitly_allowed(policy["redistribution"])
        assessment = usage_profile(policy, commercial=True, redistribution=True)
        assert assessment.decision == "blocked"


def test_commercial_and_redistribution_limits_are_exposed():
    assessment = usage_profile("derived", profile="commercial")
    assert assessment["allowed"] is False
    assert "commercial_use" in assessment["blocked_fields"]
    assert any("commercial" in reason for reason in assessment.reasons)


def test_validator_reports_missing_fields_and_registry_sources():
    errors = validate_source_policies(
        {
            "derived": {
                "owner": "local",
                "access_type": "local_derivation",
                "tos_url": "unknown",
                "tos_reviewed_at": "unknown",
                "authentication": "not_applicable",
                "personal_use": "unknown",
                "commercial_use": "unknown",
                "cache_allowed": "unknown",
                "redistribution": "unknown",
                "rate_limit": "not_applicable",
                "retained_payloads": "unknown",
                "legal_status": "unknown",
                # notes intentionally omitted
                "derived": True,
            }
        }
    )
    assert any("missing required field 'notes'" in error for error in errors)
    assert any("missing policy for registered source 'eastmoney'" in error for error in errors)


def test_load_rejects_incomplete_policy_document(tmp_path):
    path = tmp_path / "SOURCES.yml"
    path.write_text("sources:\n  test:\n    owner: test\n", encoding="utf-8")

    with pytest.raises(SourcePolicyValidationError):
        load_source_policies(path)
