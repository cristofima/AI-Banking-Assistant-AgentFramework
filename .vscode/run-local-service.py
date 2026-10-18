"""Run local services with colored console output and daily project log files."""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICES = {
    "identity": ("app/business-api/identity", [
        "-m", "uvicorn", "identity.main:create_app", "--factory", "--host", "localhost",
        "--port", "8091", "--use-colors",
    ]),
    "account": ("app/business-api/account", ["-m", "banking_account.main"]),
    "transaction": ("app/business-api/transaction", ["-m", "banking_transaction.main"]),
    "agent": ("app/agent", ["main.py"]),
    "bff": ("app/responses-bff", [
        "-m", "uvicorn", "bff.main:app", "--port", "8080", "--use-colors",
    ]),
}
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
LEVEL = re.compile(r"\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\b")
SENSITIVE_QUERY = re.compile(
    r"(?i)([?&](?:code|state|token|access_token|refresh_token|client_secret|password)=)[^\s&\"']+"
)
BEARER = re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+")
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
COLORS = {
    "DEBUG": "36", "INFO": "32", "WARNING": "33", "WARN": "33",
    "ERROR": "31", "CRITICAL": "35",
}


def safe_line(line: str) -> str:
    """Remove common credentials and OAuth callback values from local output."""
    plain = ANSI.sub("", line)
    plain = SENSITIVE_QUERY.sub(r"\1[REDACTED]", plain)
    plain = BEARER.sub(r"\1[REDACTED]", plain)
    return JWT.sub("[REDACTED]", plain)


def console_line(line: str, *, color: bool) -> str:
    """Normalize ANSI output and color only the severity label."""
    plain = safe_line(line)
    if not color:
        return plain
    return LEVEL.sub(lambda match: f"\x1b[{COLORS[match[0]]}m{match[0]}\x1b[0m", plain, count=1)


def write_line(project: Path, service: str, line: str) -> None:
    """Append without ANSI escapes, selecting today's file on every output line."""
    directory = project / "logs"
    directory.mkdir(exist_ok=True)
    filename = directory / f"{service}-{datetime.now():%Y-%m-%d}.log"
    with filename.open("a", encoding="utf-8") as output:
        output.write(safe_line(line))


def run_service(service: str) -> int:
    """Forward combined output and preserve the service exit status."""
    # RTK can pipe this process through a legacy Windows encoding.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    relative, arguments = SERVICES[service]
    project = ROOT / relative
    command = [sys.executable, *arguments]
    if service == "identity":
        tls = Path.home() / ".banking-oauth-local"
        command.extend(["--ssl-certfile", str(tls / "localhost-cert.pem"),
                        "--ssl-keyfile", str(tls / "localhost-key.pem")])
    environment = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    color = "NO_COLOR" not in environment
    with subprocess.Popen(
        command, cwd=project, env=environment, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    ) as process:
        try:
            if process.stdout is None:
                raise RuntimeError("Service output pipe is unavailable")
            for line in process.stdout:
                write_line(project, service, line)
                print(console_line(line, color=color), end="", flush=True)
            return process.wait()
        except KeyboardInterrupt:
            # The foreground child receives Ctrl+C too; allow graceful shutdown first.
            try:
                return process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                return process.wait()
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait()


def main() -> int:
    """Select one of the root development task services."""
    if len(sys.argv) != 2 or sys.argv[1] not in SERVICES:
        print(f"Usage: {Path(__file__).name} {'|'.join(SERVICES)}", file=sys.stderr)
        return 2
    return run_service(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(main())
