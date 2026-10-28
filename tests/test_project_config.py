import json

import pytest
from translation.project_config import (
    DEFAULT_PROJECT,
    DEFAULT_PROVIDER,
    resolve_project,
)

PROVIDER_ONE = "projects/111111111111/locations/global/workloadIdentityPools/translation-actions/providers/github"
PROVIDER_TWO = "projects/222222222222/locations/global/workloadIdentityPools/translation-actions/providers/github"
ACCOUNT_ONE = "translation-vertex@first-project.iam.gserviceaccount.com"
ACCOUNT_TWO = "translation-vertex@second-project.iam.gserviceaccount.com"


def test_legacy_default_still_connects_to_its_own_project():
    result = resolve_project("", "first-project", "", PROVIDER_ONE, ACCOUNT_ONE)
    assert result == {
        "project_id": "first-project",
        "provider": PROVIDER_ONE,
        "service_account": ACCOUNT_ONE,
    }


def test_verified_original_project_uses_its_default_provider():
    result = resolve_project("", "", "", "", "")
    assert result["project_id"] == DEFAULT_PROJECT
    assert result["provider"] == DEFAULT_PROVIDER


def test_second_project_uses_its_own_identity():
    mapping = json.dumps(
        {"second-project": {"provider": PROVIDER_TWO, "service_account": ACCOUNT_TWO}}
    )
    result = resolve_project(
        "second-project", "first-project", mapping, PROVIDER_ONE, ACCOUNT_ONE
    )
    assert result == {
        "project_id": "second-project",
        "provider": PROVIDER_TWO,
        "service_account": ACCOUNT_TWO,
    }
    assert (
        resolve_project("", "first-project", mapping, PROVIDER_ONE, ACCOUNT_ONE)[
            "provider"
        ]
        == PROVIDER_ONE
    )


def test_unregistered_project_does_not_fall_back_to_another_identity():
    with pytest.raises(ValueError, match="not been registered"):
        resolve_project(
            "second-project", "first-project", "{}", PROVIDER_ONE, ACCOUNT_ONE
        )


def test_legacy_default_does_not_use_another_projects_service_account():
    with pytest.raises(ValueError, match="different project"):
        resolve_project("", "second-project", "{}", PROVIDER_ONE, ACCOUNT_ONE)


def test_malformed_mapping_or_output_injection_is_rejected():
    with pytest.raises(ValueError, match="JSON object"):
        resolve_project("", "first-project", "[1]", PROVIDER_ONE, ACCOUNT_ONE)
    with pytest.raises(ValueError, match="Invalid Google Cloud project ID"):
        resolve_project(
            "first-project\nprovider=evil",
            "first-project",
            "{}",
            PROVIDER_ONE,
            ACCOUNT_ONE,
        )
