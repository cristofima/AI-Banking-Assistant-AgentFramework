"""Resolve repository-owned fixtures and source contracts independently of installation."""

from __future__ import annotations

import os
from pathlib import Path


def repository_root() -> Path:
    """Use an explicit root or discover a unique checkout above the working directory."""
    configured = os.getenv("BANKING_EVALS_ROOT")
    candidates = [Path(configured).resolve()] if configured else [Path.cwd(), *Path.cwd().parents]
    matches = [root for root in candidates if (root / "evals" / "dispute_cases.json").is_file()
               and (root / "app" / "agent" / "pyproject.toml").is_file()]
    if len(matches) != 1:
        raise ValueError("Set BANKING_EVALS_ROOT to a unique banking repository checkout")
    return matches[0]


def resource(relative_path: str) -> Path:
    root = repository_root()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Required evaluation resource is unavailable")
    return path
