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
    assert "services enable" not in (tmp_path / "calls").read_text()


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
