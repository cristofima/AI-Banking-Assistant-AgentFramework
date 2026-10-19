"""Execute the workflow's Bash blocks offline, without Azure or database access."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

WORKFLOW = Path(__file__).resolve().parents[4] / ".github" / "workflows" / "manual-database-operations.yaml"


def workflow_script(step: str) -> str:
    block = WORKFLOW.read_text(encoding="utf-8").split(f"      - name: {step}\n", 1)[1]
    block = block.split("      - ", 1)[0].split("        run: |\n", 1)[1]
    return "\n".join(line[10:] for line in block.splitlines())


@pytest.fixture
def bash() -> str:
    git = shutil.which("git")
    git_bash = Path(git).parent.parent / "bin" / "bash.exe" if git else Path("missing")
    executable = str(git_bash) if os.name == "nt" and git_bash.is_file() else shutil.which("bash")
    if not executable:
        pytest.skip("Bash is required to execute workflow blocks offline")
    return executable


def environment(operation: str = "register-client") -> dict[str, str]:
    values = os.environ.copy()
    values.update({name: "synthetic" for name in (
        "AZURE_OPERATIONS_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID",
        "AZURE_RESOURCE_GROUP", "AZURE_KEY_VAULT_NAME", "AZURE_POSTGRES_SERVER_NAME",
        "AZURE_POSTGRES_DATABASE_NAME", "OAUTH_SIGNING_KEY_URI", "OAUTH_SIGNING_KEY_ID",
    )})
    values.update(
        OPERATION=operation, CONFIRMATION=f"{operation}:Development",
        OAUTH_CLIENT_ID="bff-client", OAUTH_REDIRECT_URI="https://bff.invalid/callback",
        ADDITIONAL_CLIENT_ID="", ADDITIONAL_REDIRECT_URI="", ADDITIONAL_CLIENT_SECRET_NAME="",
        LEGACY_USER_STATUS="unspecified", GITHUB_WORKSPACE="/synthetic",
    )
    return values


def execute(bash: str, script: str, values: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [bash, "--noprofile", "--norc", "-s"], input=script, env=values,
        text=True, capture_output=True, check=False, timeout=15,
    )


@pytest.mark.parametrize(("operation", "overrides", "accepted"), [
    ("migrate", {}, True), ("init-oauth", {}, True), ("register-client", {}, True),
    ("register-client", {"CONFIRMATION": "wrong"}, False),
    ("register-client", {"ADDITIONAL_REDIRECT_URI": ""}, False),
    ("migrate", {"ADDITIONAL_CLIENT_ID": "native"}, False),
    ("register-client", {"ADDITIONAL_CLIENT_ID": "bff-client"}, False),
    ("register-client", {"ADDITIONAL_CLIENT_SECRET_NAME": "oauth-client-secret"}, False),
    ("register-client", {"ADDITIONAL_CLIENT_SECRET_NAME": "OAUTH-CLIENT-SECRET"}, False),
    ("register-client", {"ADDITIONAL_CLIENT_SECRET_NAME": "database-url"}, False),
    ("register-client", {"ADDITIONAL_REDIRECT_URI": "http://native.invalid/callback"}, False),
    ("register-client", {"ADDITIONAL_CLIENT_ID": "$(exit 0)"}, False),
    ("init-oauth", {"OAUTH_SIGNING_KEY_URI": ""}, False),
    ("register-client", {"OAUTH_CLIENT_ID": "", "OAUTH_REDIRECT_URI": ""}, True),
])
def test_configuration_rejects_unsafe_or_partial_overrides(
    bash: str, operation: str, overrides: dict[str, str], accepted: bool,
) -> None:
    values = environment(operation)
    if overrides and "CONFIRMATION" not in overrides:
        values.update(ADDITIONAL_CLIENT_ID="native-account",
                      ADDITIONAL_REDIRECT_URI="https://native.invalid/callback",
                      ADDITIONAL_CLIENT_SECRET_NAME="oauth-client-native-account")
    values.update(overrides)
    result = execute(bash, workflow_script("Validate explicit operation and configuration"), values)
    assert (result.returncode == 0) is accepted, result.stderr + result.stdout


@pytest.mark.parametrize(("operation", "additional"), [
    ("migrate", False), ("init-oauth", False), ("register-client", False),
    ("init-oauth", True), ("register-client", True),
])
def test_execution_preserves_default_and_additional_client_arguments(
    bash: str, operation: str, additional: bool,
) -> None:
    values = environment(operation)
    if additional:
        values.update(ADDITIONAL_CLIENT_ID="native-account",
                      ADDITIONAL_REDIRECT_URI="https://native.invalid/callback?x=1&y=2",
                      ADDITIONAL_CLIENT_SECRET_NAME="oauth-client-native-account")
    stubs = 'az() { printf "synthetic\\n"; }; uv() { printf "%s\\n" "$@"; };\n'
    result = execute(bash, stubs + workflow_script(
        "Verify Azure target and execute the single explicit operation"), values)
    assert result.returncode == 0, result.stderr
    arguments = result.stdout.splitlines()
    assert "--no-env-file" in arguments
    assert "--confirm" in arguments
    if operation == "migrate":
        assert "--client-id" not in arguments
        assert "--data-project" in arguments
    else:
        assert arguments[arguments.index("--client-id") + 1] == (
            "native-account" if additional else "bff-client")
        assert arguments[arguments.index("--redirect-uri") + 1] == (
            values["ADDITIONAL_REDIRECT_URI"] if additional else values["OAUTH_REDIRECT_URI"])
        assert ("--client-secret-name" in arguments) is additional
        if additional:
            assert arguments[arguments.index("--client-secret-name") + 1] == (
                "oauth-client-native-account")
        assert ("--key-uri" in arguments) is (operation == "init-oauth")
