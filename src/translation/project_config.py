"""Resolve a selected cloud project to its own GitHub OIDC identity."""

import json
import os
import re
from pathlib import Path

DEFAULT_PROJECT = "project-fdadb4dd-cb77-4270-9cd"


def resolve_project(
    requested, default_project, projects_json, legacy_provider, legacy_account
):
    default_project = default_project or DEFAULT_PROJECT
    selected = requested.strip() or default_project
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", selected):
        raise ValueError("Invalid Google Cloud project ID")
    try:
        projects = json.loads(projects_json or "{}")
    except json.JSONDecodeError:
        raise ValueError("TRANSLATION_PROJECTS must be a JSON object") from None
    if not isinstance(projects, dict):
        raise ValueError("TRANSLATION_PROJECTS must be a JSON object")
    if selected in projects:
        entry = projects[selected]
        if not isinstance(entry, dict):
            raise ValueError("Project connection entry must be a JSON object")
        provider = entry.get("provider", "")
        account = entry.get("service_account", "")
    elif selected == default_project:
        provider = legacy_provider
        if legacy_account and not legacy_account.endswith(
            f"@{selected}.iam.gserviceaccount.com"
        ):
            raise ValueError(
                "Legacy service account belongs to a different project; register the selected project in TRANSLATION_PROJECTS"
            )
        account = (
            legacy_account or f"translation-vertex@{selected}.iam.gserviceaccount.com"
        )
    else:
        raise ValueError(
            "The selected project has not been registered in TRANSLATION_PROJECTS"
        )
    if not isinstance(provider, str) or not re.fullmatch(
        r"projects/[0-9]+/locations/global/workloadIdentityPools/[a-z0-9-]+/providers/[a-z0-9-]+",
        provider,
    ):
        raise ValueError(
            "Workload Identity provider is missing or invalid for the selected project"
        )
    if not isinstance(account, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9-]*@[a-z0-9][a-z0-9-]*\.iam\.gserviceaccount\.com", account
    ):
        raise ValueError(
            "Service account is missing or invalid for the selected project"
        )
    return {"project_id": selected, "provider": provider, "service_account": account}


def main():
    result = resolve_project(
        os.environ.get("TRANSLATION_REQUESTED_PROJECT", ""),
        os.environ.get("TRANSLATION_DEFAULT_PROJECT", ""),
        os.environ.get("TRANSLATION_PROJECTS_JSON", ""),
        os.environ.get("TRANSLATION_LEGACY_PROVIDER", ""),
        os.environ.get("TRANSLATION_LEGACY_ACCOUNT", ""),
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as out:
        for key, value in result.items():
            out.write(f"{key}={value}\n")
    print("Selected Google Cloud project:", result["project_id"])


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        raise SystemExit(str(error)) from None
