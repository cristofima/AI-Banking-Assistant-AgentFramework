from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine
from sqlalchemy.pool import StaticPool

from banking_data.models import LegacyServiceAgent, SQLModel


from banking_data.ingestion import verify_load


@pytest.mark.parametrize("expected_count", [1, 2])
def test_legacy_manifest_verifies_archived_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expected_count: int,
) -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    engine = engine.execution_options(schema_translate_map={"support": None, "oauth": None})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(LegacyServiceAgent(agent_id="historical"))
        session.commit()
    source = tmp_path / "service_agents.csv"
    source.write_text("agent_id\nhistorical\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "status": "completed", "rejected_rows": 0,
        "transaction_window": {
            "start_date": "2026-01-01", "end_date": "2026-01-01",
        },
        "files": [{
            "path": "service_agents.csv",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }],
        "processed_rows": {
            "branches": 0, "customers": 0, "products": 0, "transactions": 0,
            "service_agents": expected_count,
        },
    }), encoding="utf-8")
    monkeypatch.setattr(verify_load, "create_database_engine", lambda: engine)
    monkeypatch.setattr(
        verify_load, "parse_args",
        lambda: argparse.Namespace(manifest=manifest, source=tmp_path),
    )
    try:
        if expected_count == 1:
            verify_load.main()
        else:
            with pytest.raises(RuntimeError, match="count_mismatches"):
                verify_load.main()
        source.write_text("changed", encoding="utf-8")
        with pytest.raises(RuntimeError, match="Source checksum changed"):
            verify_load.main()
    finally:
        engine.dispose()
