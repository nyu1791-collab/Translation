"""Exercise setup rate-limit handling without calling Google Cloud."""

import os
from pathlib import Path
import subprocess

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_vertex_wif.sh"
SERVICES = "\n".join(
    [
        "aiplatform.googleapis.com",
        "iam.googleapis.com",
        "iamcredentials.googleapis.com",
        "sts.googleapis.com",
        "cloudresourcemanager.googleapis.com",
    ]
)


def setup_stub(tmp_path):
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "gcloud").write_text("""#!/usr/bin/env python3
import os, pathlib, sys
args = sys.argv[1:]
log = pathlib.Path(os.environ["TEST_CALLS"])
with log.open("a") as out:
    out.write(" ".join(args) + "\\n")
if args[:2] == ["projects", "describe"]:
    print("123456789012")
elif args[:2] == ["services", "list"]:
    print(os.environ.get("TEST_ENABLED", ""))
elif args[:2] == ["services", "enable"]:
    flag = pathlib.Path(os.environ["TEST_FLAG"])
    if os.environ.get("TEST_MODE") == "forbidden":
        print("PERMISSION_DENIED", file=sys.stderr)
        sys.exit(1)
    if os.environ.get("TEST_MODE") == "rate" and not flag.exists():
        flag.touch()
        print("HTTP 429: RATE_LIMIT_EXCEEDED", file=sys.stderr)
        sys.exit(1)
elif args[:2] == ["projects", "add-iam-policy-binding"]:
    flag = pathlib.Path(os.environ["TEST_FLAG"])
    if os.environ.get("TEST_MODE") == "service_account_lag" and not flag.exists():
        flag.touch()
        print("Service account newly created\\ndoes not exist", file=sys.stderr)
        sys.exit(1)
""")
    (binary / "gh").write_text("#!/bin/sh\nexit 1\n")
    (binary / "sleep").write_text('#!/bin/sh\necho "$1" >> "$TEST_SLEEP"\n')
    for path in binary.iterdir():
        path.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "TEST_CALLS": str(tmp_path / "calls"),
        "TEST_FLAG": str(tmp_path / "flag"),
        "TEST_SLEEP": str(tmp_path / "sleep"),
    }
    return env


def test_already_enabled_services_skip_mutating_request(tmp_path):
    env = {**setup_stub(tmp_path), "TEST_ENABLED": SERVICES}
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "Required Google Cloud services are already enabled" in result.stdout
    calls = (tmp_path / "calls").read_text()
    assert "services enable" not in calls
    assert "--role=roles/editor" in calls


def test_rate_limit_retries_only_missing_services(tmp_path):
    env = {
        **setup_stub(tmp_path),
        "TEST_ENABLED": "aiplatform.googleapis.com\niam.googleapis.com",
        "TEST_MODE": "rate",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0
    enables = [
        row
        for row in (tmp_path / "calls").read_text().splitlines()
        if row.startswith("services enable")
    ]
    assert len(enables) == 2
    assert (
        "iamcredentials.googleapis.com" in enables[0]
        and "sts.googleapis.com" in enables[0]
    )
    assert "aiplatform.googleapis.com" not in enables[0]
    assert (tmp_path / "sleep").read_text().strip() == "60"


def test_permission_denial_does_not_retry(tmp_path):
    env = {**setup_stub(tmp_path), "TEST_MODE": "forbidden"}
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "PERMISSION_DENIED" in result.stderr
    assert not (tmp_path / "sleep").exists()


def test_new_service_account_visibility_is_retried(tmp_path):
    env = {
        **setup_stub(tmp_path),
        "TEST_ENABLED": SERVICES,
        "TEST_MODE": "service_account_lag",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0
    assert (tmp_path / "sleep").read_text().strip() == "10"
    calls = (tmp_path / "calls").read_text()
    assert calls.count("projects add-iam-policy-binding") == 2


def test_new_project_registration_preserves_existing_project(tmp_path):
    import json

    env = {
        **setup_stub(tmp_path),
        "TEST_ENABLED": SERVICES,
        "GOOGLE_CLOUD_PROJECT": "second-project",
        "TEST_OLD_MAP": json.dumps(
            {"first-project": {"provider": "old", "service_account": "old"}}
        ),
        "TEST_SET_FILE": str(tmp_path / "set"),
    }
    gh = tmp_path / "bin" / "gh"
    gh.write_text("""#!/usr/bin/env python3
import os, pathlib, sys
args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    sys.exit(0)
if args[:3] == ["variable", "get", "TRANSLATION_PROJECTS"]:
    print(os.environ["TEST_OLD_MAP"])
    sys.exit(0)
if args[:3] == ["variable", "set", "TRANSLATION_PROJECTS"]:
    pathlib.Path(os.environ["TEST_SET_FILE"]).write_text(args[args.index("--body") + 1])
    sys.exit(0)
sys.exit(1)
""")
    gh.chmod(0o755)
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    mapping = json.loads((tmp_path / "set").read_text())
    assert mapping["first-project"] == {"provider": "old", "service_account": "old"}
    assert (
        mapping["second-project"]["service_account"]
        == "translation-vertex@second-project.iam.gserviceaccount.com"
    )
    assert mapping["second-project"]["provider"].startswith("projects/123456789012/")


def test_denied_mapping_read_does_not_overwrite_existing_settings(tmp_path):
    env = {
        **setup_stub(tmp_path),
        "TEST_ENABLED": SERVICES,
        "TRANSLATION_SET_DEFAULT": "1",
        "TEST_SET_FILE": str(tmp_path / "set"),
    }
    gh = tmp_path / "bin" / "gh"
    gh.write_text("""#!/usr/bin/env python3
import os, pathlib, sys
args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    sys.exit(0)
if args[:2] == ["variable", "get"]:
    print("HTTP 403: Resource not accessible", file=sys.stderr)
    sys.exit(1)
pathlib.Path(os.environ["TEST_SET_FILE"]).write_text("unexpected write")
sys.exit(0)
""")
    gh.chmod(0o755)
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0
    assert not (tmp_path / "set").exists()
    assert "leaving GitHub variables unchanged" in result.stderr
    assert "Default project was not changed" in result.stderr
