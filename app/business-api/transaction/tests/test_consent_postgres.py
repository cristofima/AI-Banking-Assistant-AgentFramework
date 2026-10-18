"""Authorized local PostgreSQL consent races in disposable synthetic schemas."""
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from banking_shared.models import (
    CardProtection, CaseConversationSnapshot, Customer, Product, RuntimePosting,
    SQLModel, SupportCase, SupportCaseEvent, TransactionRecord,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp import Client
from sqlalchemy import MetaData, Table, event, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema
from sqlmodel import Session, create_engine, select

from banking_transaction import mcp_tools
from banking_transaction.auth.jwt_identity import get_jwt_customer_id
from banking_transaction.consent.preview import read_preview
from banking_transaction.models.conversation import ConversationMessage
from banking_transaction.routers import disputes as routes
from banking_transaction.services import disputes
from banking_transaction.services.disputes import ActiveDisputeError, SupportCaseService

SECRET = "synthetic-consent-test-signing-key"
OWNER = "consent-owner"
pytestmark = pytest.mark.skipif(
    os.getenv("CONSENT_TEST_ALLOW_WRITES") != "1" or not os.getenv("CONSENT_TEST_DATABASE_URL"),
    reason="Explicit local PostgreSQL target and write authorization required",
)


@pytest.fixture
def postgres_factory(record_property: Callable[[str, object], None]) -> Iterator[Callable[[], Session]]:
    url = make_url(os.environ["CONSENT_TEST_DATABASE_URL"])
    if url.get_backend_name() != "postgresql" or url.host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("Consent tests require an explicitly authorized loopback PostgreSQL target")
    engine = create_engine(
        url, isolation_level="READ COMMITTED",
        connect_args={
            "connect_timeout": 5,
            "options": "-c lock_timeout=10000 -c statement_timeout=20000",
        },
    )
    schema = "consent_acceptance_test_" + uuid4().hex
    created = False
    try:
        with engine.begin() as connection:
            record_property("postgres_version", ".".join(map(str, engine.dialect.server_version_info)))
            record_property("isolation", connection.get_isolation_level())
            if inspect(connection).has_table("alembic_version"):
                versions = Table("alembic_version", MetaData(), autoload_with=connection)
                record_property("target_migration", ",".join(connection.execute(select(versions.c.version_num)).scalars()))
            else:
                record_property("target_migration", "unavailable")
            connection.execute(CreateSchema(schema))
        created = True
        record_property("fixture_schema", schema)
        record_property("fixture_schema_source", "SQLModel metadata, not a migration test")
        isolated = engine.execution_options(schema_translate_map={None: schema, "support": schema})
        SQLModel.metadata.create_all(isolated)
        now = datetime.now(timezone.utc)
        with Session(isolated) as session:
            session.add_all([Customer(customer_id=customer, email=f"{customer}@synthetic.invalid")
                             for customer in (OWNER, "consent-foreign")])
            session.flush()
            session.add(Product(
                product_id="consent-card", customer_id=OWNER, product_type="Credit Card",
                product_number="4111111111111111", currency="USD", product_status="Active",
                current_balance=Decimal("100"),
            ))
            session.flush()
            session.add(TransactionRecord(
                transaction_id="consent-transaction", customer_id=OWNER, product_id="consent-card",
                transaction_date=now, process_date=now.date(), amount=Decimal("10"), currency="USD",
                transaction_status="Approved", fraud_score=Decimal("10"),
            ))
            session.commit()
        yield lambda: Session(isolated)
    finally:
        try:
            if created:
                with engine.begin() as connection:
                    connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        finally:
            engine.dispose()


@pytest.fixture
def service(postgres_factory: Callable[[], Session]) -> SupportCaseService:
    return SupportCaseService(postgres_factory, preview_secret=SECRET)


def assert_receipt(factory: Callable[[], Session], token: str) -> tuple[str, ...]:
    claims = read_preview(SECRET, token, OWNER)
    with factory() as session:
        case = session.exec(select(SupportCase)).one()
        assert case.case_id == f"CASE-{claims['jti']}"
        assert (case.customer_id, case.product_id, case.transaction_id, case.reason) == (
            OWNER, "consent-card", "consent-transaction", claims["reason"],
        )
        assert case.status == "IN_REVIEW" and case.triage_outcome == "fast_track"
        assert case.assigned_operator_sub is None and case.resolution_outcome is None
        assert case.resolved_at is None and case.verdict is None
        events = session.exec(select(SupportCaseEvent).order_by(
            SupportCaseEvent.created_at, SupportCaseEvent.event_id,
        )).all()
        assert len(events) == 3
        by_type = {item.event_type: item for item in events}
        assert set(by_type) == {"CASE_OPENED", "APPROVAL_GRANTED", "REVIEW_REQUIRED"}
        assert by_type["CASE_OPENED"].actor == "customer"
        assert by_type["APPROVAL_GRANTED"].actor == "customer"
        assert by_type["REVIEW_REQUIRED"].actor == "system"
        assert all(item.case_id == case.case_id for item in events)
        assert "no card block or financial posting" in (by_type["APPROVAL_GRANTED"].message or "")
        assert session.exec(select(RuntimePosting)).all() == []
        assert session.exec(select(CardProtection)).all() == []
        assert len(session.exec(select(TransactionRecord)).all()) == 1
        product = session.get(Product, "consent-card")
        assert product is not None and product.current_balance == Decimal("100")
        assert product.product_status == "Active"
        return tuple(sorted(item.event_id for item in events))


@pytest.mark.parametrize("adapter", ["service", "rest", "mcp"])
@pytest.mark.parametrize("same_token", [True, False], ids=["same-acceptance", "distinct-acceptances"])
def test_concurrent_acceptance(
    postgres_factory: Callable[[], Session], service: SupportCaseService,
    monkeypatch: pytest.MonkeyPatch, adapter: str, same_token: bool,
) -> None:
    first = service.preview_transaction_dispute("consent-transaction", OWNER, "Synthetic first reason")
    second = first if same_token else service.preview_transaction_dispute(
        "consent-transaction", OWNER, "Synthetic second reason",
    )
    barrier = Barrier(2)
    connections: list[int] = []
    eligible = disputes._eligible_intake

    def synchronized_intake(
        session: Session, transaction_id: str, customer_id: str, *, lock: bool = False,
    ) -> tuple[TransactionRecord, Product]:
        if lock:
            connection = session.connection()
            connections.append(connection.connection.driver_connection.info.backend_pid)
            barrier.wait(timeout=15)
        return eligible(session, transaction_id, customer_id, lock=lock)

    monkeypatch.setattr(disputes, "_eligible_intake", synchronized_intake)
    monkeypatch.setattr(routes, "service", service)
    monkeypatch.setattr(mcp_tools, "dispute_service", service)
    monkeypatch.setattr(mcp_tools, "get_customer_id", lambda headers, *, mutation=False: OWNER)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/support-cases")
    app.dependency_overrides[get_jwt_customer_id] = lambda: OWNER

    def accept(token: str) -> str | None:
        if adapter == "service":
            try:
                return service.accept_transaction_dispute(token, OWNER).caseId
            except ActiveDisputeError:
                return None
        if adapter == "rest":
            with TestClient(app) as client:
                response = client.post("/api/support-cases", json={"previewToken": token})
                if response.status_code == 409:
                    assert response.json() == {"detail": {"code": "DISPUTE_ALREADY_ACTIVE"}}
                    return None
                assert response.status_code == 201
                return response.json()["caseId"]

        async def call() -> str | None:
            async with Client(mcp_tools.mcp) as client:
                result = await client.call_tool(
                    "reportTransactionDispute", {"preview_token": token}, raise_on_error=False,
                )
                if result.is_error:
                    assert "Transaction already has an active dispute case" in result.content[0].text
                    return None
                return result.data["caseId"]

        return asyncio.run(call())

    tokens = [first.previewToken, second.previewToken]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(accept, tokens))
    assert len(connections) == 2 and len(set(connections)) == 2
    winners = [result for result in results if result is not None]
    assert len(winners) == (2 if same_token else 1)
    assert len(set(winners)) == 1
    winner_index = next(index for index, result in enumerate(results) if result is not None)
    winner_token = tokens[winner_index]
    receipt = assert_receipt(postgres_factory, winner_token)
    assert service.recover_transaction_dispute(winner_token, OWNER).caseId == winners[0]
    with pytest.raises(PermissionError):
        service.recover_transaction_dispute(winner_token, "consent-foreign")
    with pytest.raises(PermissionError):
        service.accept_transaction_dispute(winner_token, "consent-foreign")
    if not same_token:
        assert service.recover_transaction_dispute(tokens[1 - winner_index], OWNER) is None
    assert assert_receipt(postgres_factory, winner_token) == receipt


def test_response_loss_after_commit_recovers_original(
    postgres_factory: Callable[[], Session], service: SupportCaseService,
) -> None:
    preview = service.preview_transaction_dispute("consent-transaction", OWNER, "Synthetic lost response")

    def lose_response(session: Session) -> None:
        raise RuntimeError("Synthetic response lost after commit")

    def failing_factory() -> Session:
        session = postgres_factory()
        event.listen(session, "after_commit", lose_response)
        return session

    with pytest.raises(RuntimeError, match="Synthetic response lost after commit"):
        SupportCaseService(failing_factory, SECRET).accept_transaction_dispute(preview.previewToken, OWNER)
    receipt = assert_receipt(postgres_factory, preview.previewToken)
    recovered = service.recover_transaction_dispute(preview.previewToken, OWNER)
    assert recovered is not None
    assert service.accept_transaction_dispute(preview.previewToken, OWNER) == recovered
    assert assert_receipt(postgres_factory, preview.previewToken) == receipt


@pytest.mark.parametrize("stage", ["after_flush_postexec", "before_commit"])
def test_precommit_failure_rolls_back_receipt_and_conversation(
    postgres_factory: Callable[[], Session], service: SupportCaseService, stage: str,
) -> None:
    preview = service.preview_transaction_dispute("consent-transaction", OWNER, "Synthetic rollback")
    history = [ConversationMessage(role="user", text="Synthetic explicit consent")]

    def mark_receipt(session: Session, *args: object) -> None:
        if any(isinstance(item, SupportCaseEvent) for item in session.new):
            session.info["receipt_flush"] = True

    def fail_write(session: Session, *args: object) -> None:
        if stage == "before_commit":
            session.flush()
        if session.info.get("receipt_flush"):
            assert len(session.exec(select(SupportCaseEvent)).all()) == 3
            raise RuntimeError("Synthetic failure after receipt writes")

    def failing_factory() -> Session:
        session = postgres_factory()
        event.listen(session, "before_flush", mark_receipt)
        event.listen(session, stage, fail_write)
        return session

    with pytest.raises(RuntimeError, match="Synthetic failure after receipt writes"):
        SupportCaseService(failing_factory, SECRET).accept_transaction_dispute(preview.previewToken, OWNER, history)
    with postgres_factory() as session:
        for model in (SupportCase, SupportCaseEvent, CaseConversationSnapshot, RuntimePosting, CardProtection):
            assert session.exec(select(model)).all() == []
    assert service.recover_transaction_dispute(preview.previewToken, OWNER) is None
    accepted = service.accept_transaction_dispute(preview.previewToken, OWNER, history)
    assert_receipt(postgres_factory, preview.previewToken)
    assert service.get_case_conversation(accepted.caseId, OWNER).messages == history


def test_resolved_transaction_can_have_new_consented_case(
    postgres_factory: Callable[[], Session], service: SupportCaseService,
) -> None:
    first = service.preview_transaction_dispute("consent-transaction", OWNER, "Synthetic original")
    accepted = service.accept_transaction_dispute(first.previewToken, OWNER)
    with postgres_factory() as session:
        case = session.get(SupportCase, accepted.caseId)
        assert case is not None
        case.status = "RESOLVED_INVALID"
        case.resolved_at = datetime.now(timezone.utc)
        session.add(case)
        session.commit()
    second = service.preview_transaction_dispute("consent-transaction", OWNER, "Synthetic new request")
    reopened = service.accept_transaction_dispute(second.previewToken, OWNER)
    assert reopened.caseId != accepted.caseId and reopened.status == "IN_REVIEW"
    assert service.accept_transaction_dispute(first.previewToken, OWNER).status == "RESOLVED_INVALID"
    with postgres_factory() as session:
        assert len(session.exec(select(SupportCase)).all()) == 2
        assert len(session.exec(select(SupportCaseEvent)).all()) == 6
        assert len(session.exec(select(SupportCase).where(SupportCase.status == "IN_REVIEW")).all()) == 1
