"""Ownership and state-machine checks for the transaction-dispute support-case workflow."""

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from banking_shared.models import (
    Customer,
    Product,
    SQLModel,
    SupportCase,
    SupportCaseEvent,
    TransactionRecord,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from banking_transaction.routers import disputes as dispute_routers
from banking_transaction.services.disputes import CardOnlyDisputeError, SupportCaseService
from banking_transaction.models.transactions import DisputeCase
from banking_transaction.auth.jwt_identity import get_jwt_customer_id
from banking_transaction.auth.operator_identity import OperatorPrincipal
from banking_transaction.models.conversation import CaseConversation, ConversationMessage
from banking_transaction.models.conversation_record import CaseConversationSnapshot
from banking_transaction.models.transactions import AcceptDisputeRequest
from banking_transaction.services.operator import OperatorCaseService
from pydantic import ValidationError

NOW = datetime.now(timezone.utc)


@pytest.fixture
def session_factory() -> Callable[[], Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    engine = engine.execution_options(schema_translate_map={"support": None})
    SQLModel.metadata.create_all(engine)
    CaseConversationSnapshot.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Customer(customer_id="customer-owned", email="owner@example.com"),
                Customer(customer_id="customer-foreign", email="foreign@example.com"),
                Product(
                    product_id="card-owned",
                    product_number="4111111111111111",
                    customer_id="customer-owned",
                    product_type="Credit Card",
                    currency="USD",
                    product_status="Active",
                ),
                Product(
                    product_id="card-blocked",
                    product_number="4222222222222222",
                    customer_id="customer-owned",
                    product_type="Credit Card",
                    currency="USD",
                    product_status="Blocked",
                ),
            ]
        )
        session.add_all(
            [
                TransactionRecord(
                    transaction_id="tx-low-risk",
                    transaction_date=NOW - timedelta(days=5),
                    process_date=date(2026, 6, 1),
                    product_id="card-owned",
                    customer_id="customer-owned",
                    amount=Decimal("120.0000"),
                    currency="USD",
                    transaction_status="Approved",
                    fraud_score=Decimal("10.0000"),
                ),
                TransactionRecord(
                    transaction_id="tx-high-risk",
                    transaction_date=NOW - timedelta(days=5),
                    process_date=date(2026, 6, 1),
                    product_id="card-owned",
                    customer_id="customer-owned",
                    amount=Decimal("330.0000"),
                    currency="USD",
                    transaction_status="Approved",
                    fraud_score=Decimal("87.0000"),
                ),
                TransactionRecord(
                    transaction_id="tx-no-score",
                    transaction_date=NOW - timedelta(days=5),
                    process_date=date(2026, 6, 1),
                    product_id="card-owned",
                    customer_id="customer-owned",
                    amount=Decimal("50.0000"),
                    currency="USD",
                    transaction_status="Approved",
                    fraud_score=None,
                ),
                TransactionRecord(
                    transaction_id="tx-declined",
                    transaction_date=NOW - timedelta(days=5),
                    process_date=date(2026, 6, 1),
                    product_id="card-owned",
                    customer_id="customer-owned",
                    amount=Decimal("75.0000"),
                    currency="USD",
                    transaction_status="Declined",
                    fraud_score=Decimal("5.0000"),
                ),
                TransactionRecord(
                    transaction_id="tx-too-old",
                    transaction_date=NOW - timedelta(days=366),
                    process_date=date(2025, 1, 1),
                    product_id="card-owned",
                    customer_id="customer-owned",
                    amount=Decimal("60.0000"),
                    currency="USD",
                    transaction_status="Approved",
                    fraud_score=Decimal("10.0000"),
                ),
                TransactionRecord(
                    transaction_id="tx-blocked-card",
                    transaction_date=NOW - timedelta(days=5),
                    process_date=date(2026, 6, 1),
                    product_id="card-blocked",
                    customer_id="customer-owned",
                    amount=Decimal("20.0000"),
                    currency="USD",
                    transaction_status="Approved",
                    fraud_score=Decimal("10.0000"),
                ),
                TransactionRecord(
                    transaction_id="tx-foreign",
                    transaction_date=NOW - timedelta(days=5),
                    process_date=date(2026, 6, 1),
                    product_id="card-owned",
                    customer_id="customer-foreign",
                    amount=Decimal("20.0000"),
                    currency="USD",
                    transaction_status="Approved",
                    fraud_score=Decimal("10.0000"),
                ),
            ]
        )
        session.commit()
    return lambda: Session(engine)


@pytest.mark.parametrize("product_type", ["Checking Account", "Savings Account"])
def test_account_dispute_rejected_without_writes_and_legacy_cases_readable(
    session_factory: Callable[[], Session], monkeypatch: pytest.MonkeyPatch,
    product_type: str,
) -> None:
    with session_factory() as session:
        product = session.get(Product, "card-owned")
        assert product is not None
        product.product_type = product_type
        session.add(product)
        session.commit()
    service = SupportCaseService(session_factory)
    with pytest.raises(CardOnlyDisputeError):
        service.open_transaction_dispute("tx-low-risk", "customer-owned", "Unrecognized")
    monkeypatch.setattr(dispute_routers, "service", service)
    app = FastAPI()
    app.include_router(dispute_routers.router, prefix="/api/support-cases")
    app.dependency_overrides[get_jwt_customer_id] = lambda: "customer-owned"
    with TestClient(app) as client:
        response = client.post("/api/support-cases/preview", json={
            "transactionId": "tx-low-risk", "reason": "Unrecognized",
        })
    assert response.status_code == 400
    assert response.json() == {"detail": {"code": "DISPUTE_CARD_ONLY"}}
    with session_factory() as session:
        assert session.exec(select(SupportCase)).all() == []
        assert session.exec(select(SupportCaseEvent)).all() == []
        historical = SupportCase(
            customer_id="customer-owned", product_id="card-owned",
            transaction_id="tx-low-risk", reason="Historical account case",
            status="WAITING_USER_APPROVAL",
        )
        session.add(historical)
        session.commit()
        session.refresh(historical)
        case_id = historical.case_id
    assert service.get_case(case_id, "customer-owned").reason == "Historical account case"
    assert [case.caseId for case in service.list_cases("customer-owned")] == [case_id]
    assert service.get_case_timeline(case_id, "customer-owned") == []
    with pytest.raises(PermissionError):
        service.get_case(case_id, "customer-foreign")


@pytest.mark.parametrize("product_type", ["Debit Card", "Credit Card"])
def test_new_disputes_accept_both_card_types(
    session_factory: Callable[[], Session], product_type: str,
) -> None:
    with session_factory() as session:
        product = session.get(Product, "card-owned")
        assert product is not None
        product.product_type = product_type
        session.add(product)
        session.commit()
    opened = SupportCaseService(session_factory).open_transaction_dispute(
        "tx-low-risk", "customer-owned", "Unrecognized",
    )
    assert opened.status == "WAITING_USER_APPROVAL"


def test_duplicate_route_returns_controlled_conflict(
    session_factory: Callable[[], Session], monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SupportCaseService(session_factory, preview_secret="test-preview-signing-key-only")
    monkeypatch.setattr(dispute_routers, "service", service)
    app = FastAPI()
    app.include_router(dispute_routers.router, prefix="/api/support-cases")
    app.dependency_overrides[get_jwt_customer_id] = lambda: "customer-owned"
    payload = {"transactionId": "tx-low-risk", "reason": "Unrecognized charge"}

    with TestClient(app) as client:
        preview = client.post("/api/support-cases/preview", json=payload)
        assert preview.status_code == 200
        token_payload = {"previewToken": preview.json()["previewToken"]}
        assert client.post("/api/support-cases/recovery", json=token_payload).json() is None
        created = client.post("/api/support-cases", json=token_payload)
        assert created.status_code == 201
        assert created.json()["status"] == "IN_REVIEW"
        assert client.post("/api/support-cases", json=token_payload).json() == created.json()
        assert client.post("/api/support-cases/recovery", json=token_payload).json() == created.json()
        duplicate = client.post("/api/support-cases/preview", json=payload)
        assert duplicate.status_code == 409
        assert duplicate.json() == {"detail": {"code": "DISPUTE_ALREADY_ACTIVE"}}
        assert len(service.list_cases("customer-owned")) == 1


@pytest.mark.parametrize("transaction_id", ["tx-foreign", "tx-missing"])
def test_open_route_preserves_resource_denial(
    session_factory: Callable[[], Session], monkeypatch: pytest.MonkeyPatch,
    transaction_id: str,
) -> None:
    monkeypatch.setattr(dispute_routers, "service", SupportCaseService(session_factory))
    app = FastAPI()
    app.include_router(dispute_routers.router, prefix="/api/support-cases")
    app.dependency_overrides[get_jwt_customer_id] = lambda: "customer-owned"

    with TestClient(app) as client:
        response = client.post(
            "/api/support-cases/preview",
            json={"transactionId": transaction_id, "reason": "Unrecognized charge"},
        )
        assert response.status_code == 403


def test_low_fraud_score_case_stays_in_review_until_explicit_resolution(
    session_factory: Callable[[], Session],
) -> None:
    service = SupportCaseService(session_factory)
    opened = service.open_transaction_dispute("tx-low-risk", "customer-owned", "No reconozco este cargo")
    assert opened.status == "WAITING_USER_APPROVAL"
    assert opened.productNumber == "**** 1111"

    resolved = service.respond_to_approval(opened.caseId, "customer-owned", approved=True)

    assert resolved.status == "IN_REVIEW"
    assert resolved.triageOutcome == "fast_track"
    assert resolved.resolutionOutcome is None
    assert resolved.resolvedAt is None
    assert resolved.recommendationType is None
    assert service.get_case(opened.caseId, "customer-owned") == resolved
    assert resolved.financialEffectsStatus == "NOT_EXECUTED"
    assert resolved.cardProtectionStatus == "NOT_BLOCKED"
    assert "workflowMode" not in resolved.model_dump()
    timeline = service.get_case_timeline(opened.caseId, "customer-owned")
    assert all("simulat" not in event.displayMessage.lower() for event in timeline)
    assert [event.eventType for event in timeline] == [
        "CASE_OPENED", "APPROVAL_REQUESTED", "APPROVAL_GRANTED", "REVIEW_REQUIRED",
    ]
    with session_factory() as session:
        assert session.get(SupportCase, opened.caseId).legacy_assigned_agent_id is None
        assert session.get(Product, "card-owned").product_status == "Active"
        assert session.get(TransactionRecord, "tx-low-risk").amount == Decimal("120.0000")

    with pytest.raises(PermissionError, match="Assigned operator"):
        service.resolve_case(
            opened.caseId, "customer-owned", "no_fraud_found", "Explicit review decision",
        )
    assert service.get_case(opened.caseId, "customer-owned").status == "IN_REVIEW"


@pytest.mark.parametrize(
    ("event_type", "actor", "message", "projected"),
    [
        ("RESOLVED", "system",
         "Provisional credit issued; case resolved without manual review", True),
        ("RESOLVED", "agent", "Case resolved: fraud_confirmed_refund_issued", True),
        ("ESCALATED_TO_REVIEW", "system",
         "Elevated fraud-risk score; routed to manual review (assigned to agent agent-1)", True),
        ("ESCALATED_TO_REVIEW", "system",
         "No fraud score available for this transaction; routed to manual review (no agent available)",
         True),
        ("ESCALATED_TO_REVIEW", "system",
         "Elevated fraud-risk score; routed to manual review: custom note", False),
        ("RESOLVED", "agent", "Reviewed by agent-1", False),
        ("RESOLVED", "customer",
         "Provisional credit issued; case resolved without manual review", False),
    ],
)
def test_historical_timeline_projects_only_known_generated_messages(
    session_factory: Callable[[], Session],
    event_type: str,
    actor: str,
    message: str,
    projected: bool,
) -> None:
    service = SupportCaseService(session_factory)
    opened = service.open_transaction_dispute("tx-low-risk", "customer-owned", "Reclamo")
    with session_factory() as session:
        session.add(SupportCaseEvent(
            case_id=opened.caseId, event_type=event_type, actor=actor, message=message,
        ))
        session.commit()
    event = service.get_case_timeline(opened.caseId, "customer-owned")[-1]
    assert event.message == message
    assert (event.displayMessage != message) is projected
    if projected:
        assert "simulat" not in event.displayMessage.lower()
        assert "no " in event.displayMessage
    with session_factory() as session:
        stored = session.exec(select(SupportCaseEvent).where(
            SupportCaseEvent.case_id == opened.caseId,
            SupportCaseEvent.message == message,
        )).one()
        assert stored.message == message


def test_high_fraud_score_case_is_escalated_to_a_fraud_agent(
    session_factory: Callable[[], Session],
) -> None:
    service = SupportCaseService(session_factory)
    opened = service.open_transaction_dispute("tx-high-risk", "customer-owned", "Cargo sospechoso")

    escalated = service.respond_to_approval(opened.caseId, "customer-owned", approved=True)

    assert escalated.status == "IN_REVIEW"
    assert escalated.triageOutcome == "escalated"

    with session_factory() as session:
        case = session.get(SupportCase, escalated.caseId)
        assert case.legacy_assigned_agent_id is None


@pytest.mark.parametrize("transaction_id", ["tx-low-risk", "tx-high-risk", "tx-no-score"])
def test_approval_without_catalog_reviewer_remains_unresolved(
    session_factory: Callable[[], Session], transaction_id: str,
) -> None:
    service = SupportCaseService(session_factory)
    opened = service.open_transaction_dispute(transaction_id, "customer-owned", "Unrecognized charge")
    reviewing = service.respond_to_approval(opened.caseId, "customer-owned", approved=True)

    assert reviewing.status == "IN_REVIEW"
    assert reviewing.resolutionOutcome is None
    assert reviewing.resolvedAt is None
    assert reviewing.recommendationType is None
    with session_factory() as session:
        assert session.get(SupportCase, opened.caseId).legacy_assigned_agent_id is None
    assert "RESOLVED" not in [
        event.eventType for event in service.get_case_timeline(opened.caseId, "customer-owned")
    ]


def test_missing_fraud_score_escalates_as_insufficient_signal(
    session_factory: Callable[[], Session],
) -> None:
    service = SupportCaseService(session_factory)
    opened = service.open_transaction_dispute("tx-no-score", "customer-owned", "No fue mi compra")

    escalated = service.respond_to_approval(opened.caseId, "customer-owned", approved=True)

    assert escalated.status == "IN_REVIEW"
    assert escalated.triageOutcome == "insufficient_signal"


def test_declining_approval_withdraws_the_case(session_factory: Callable[[], Session]) -> None:
    service = SupportCaseService(session_factory)
    opened = service.open_transaction_dispute("tx-low-risk", "customer-owned", "Reclamo")

    declined = service.respond_to_approval(opened.caseId, "customer-owned", approved=False)

    assert declined.status == "RESOLVED"
    assert declined.resolutionOutcome == "withdrawn_by_customer"


def test_escalated_case_can_be_manually_resolved(session_factory: Callable[[], Session]) -> None:
    service = SupportCaseService(session_factory)
    opened = service.open_transaction_dispute("tx-high-risk", "customer-owned", "Cargo sospechoso")
    service.respond_to_approval(opened.caseId, "customer-owned", approved=True)

    with pytest.raises(PermissionError, match="Assigned operator"):
        service.resolve_case(
            opened.caseId, "customer-owned", "fraud_confirmed_refund_issued", "Reviewed by agent-1"
        )
    assert service.get_case(opened.caseId, "customer-owned").status == "IN_REVIEW"


def test_declined_transactions_cannot_be_disputed(session_factory: Callable[[], Session]) -> None:
    with pytest.raises(ValueError, match="Approved"):
        SupportCaseService(session_factory).open_transaction_dispute(
            "tx-declined", "customer-owned", "No reconozco"
        )


def test_transactions_within_one_year_can_be_disputed(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        transaction = session.get(TransactionRecord, "tx-low-risk")
        assert transaction is not None
        transaction.transaction_date = NOW - timedelta(days=364)
        session.add(transaction)
        session.commit()

    opened = SupportCaseService(session_factory).open_transaction_dispute(
        "tx-low-risk", "customer-owned", "Unrecognized charge"
    )

    assert opened.status == "WAITING_USER_APPROVAL"


def test_transactions_outside_the_dispute_window_are_rejected(
    session_factory: Callable[[], Session],
) -> None:
    with pytest.raises(ValueError, match="dispute window"):
        SupportCaseService(session_factory).open_transaction_dispute(
            "tx-too-old", "customer-owned", "No reconozco"
        )


def test_blocked_card_cannot_open_a_new_dispute(session_factory: Callable[[], Session]) -> None:
    with pytest.raises(ValueError, match="Active"):
        SupportCaseService(session_factory).open_transaction_dispute(
            "tx-blocked-card", "customer-owned", "No reconozco"
        )


def test_duplicate_dispute_on_same_transaction_is_rejected(
    session_factory: Callable[[], Session],
) -> None:
    service = SupportCaseService(session_factory)
    service.open_transaction_dispute("tx-low-risk", "customer-owned", "Primer reclamo")

    with pytest.raises(ValueError, match="already has an active dispute"):
        service.open_transaction_dispute("tx-low-risk", "customer-owned", "Segundo reclamo")


def test_foreign_transaction_cannot_be_disputed(session_factory: Callable[[], Session]) -> None:
    with pytest.raises(PermissionError, match="authenticated customer"):
        SupportCaseService(session_factory).open_transaction_dispute(
            "tx-foreign", "customer-owned", "No reconozco"
        )


def test_foreign_customer_cannot_read_or_approve_another_customers_case(
    session_factory: Callable[[], Session],
) -> None:
    service = SupportCaseService(session_factory)
    opened = service.open_transaction_dispute("tx-low-risk", "customer-owned", "Reclamo")

    with pytest.raises(PermissionError, match="authenticated customer"):
        service.get_case(opened.caseId, "customer-foreign")

    with pytest.raises(PermissionError, match="authenticated customer"):
        service.respond_to_approval(opened.caseId, "customer-foreign", approved=True)


def test_list_and_timeline_are_scoped_to_the_owning_customer(
    session_factory: Callable[[], Session],
) -> None:
    service = SupportCaseService(session_factory)
    opened = service.open_transaction_dispute("tx-low-risk", "customer-owned", "Reclamo")

    cases = service.list_cases("customer-owned")
    timeline = service.get_case_timeline(opened.caseId, "customer-owned")

    assert [case.caseId for case in cases] == [opened.caseId]
    assert [event.eventType for event in timeline] == ["CASE_OPENED", "APPROVAL_REQUESTED"]
    assert service.list_cases("customer-foreign") == []


def test_low_risk_case_gets_a_recommendation_only_after_explicit_resolution(
    session_factory: Callable[[], Session],
) -> None:
    service = SupportCaseService(session_factory)
    opened = service.open_transaction_dispute("tx-low-risk", "customer-owned", "Reclamo")

    reviewing = service.respond_to_approval(opened.caseId, "customer-owned", approved=True)
    assert reviewing.recommendationType is None
    resolved = seed_legacy_resolution(session_factory, service, opened.caseId)

    assert resolved.recommendationType == "transaction_alerts"
    assert resolved.recommendationRationale
    assert resolved.recommendationOptedOut is False


def test_withdrawn_case_gets_no_recommendation(session_factory: Callable[[], Session]) -> None:
    service = SupportCaseService(session_factory)
    opened = service.open_transaction_dispute("tx-low-risk", "customer-owned", "Reclamo")

    declined = service.respond_to_approval(opened.caseId, "customer-owned", approved=False)

    assert declined.recommendationType is None


def test_customer_can_dismiss_the_recommendation(session_factory: Callable[[], Session]) -> None:
    service = SupportCaseService(session_factory)
    opened = service.open_transaction_dispute("tx-low-risk", "customer-owned", "Reclamo")
    service.respond_to_approval(opened.caseId, "customer-owned", approved=True)
    resolved = seed_legacy_resolution(session_factory, service, opened.caseId)

    dismissed = service.dismiss_recommendation(resolved.caseId, "customer-owned")

    assert dismissed.recommendationOptedOut is True


def test_dismissing_a_recommendation_requires_one_to_exist(
    session_factory: Callable[[], Session],
) -> None:
    service = SupportCaseService(session_factory)
    opened = service.open_transaction_dispute("tx-low-risk", "customer-owned", "Reclamo")
    service.respond_to_approval(opened.caseId, "customer-owned", approved=False)

    with pytest.raises(ValueError, match="no recommendation"):
        service.dismiss_recommendation(opened.caseId, "customer-owned")


def test_foreign_customer_cannot_dismiss_another_customers_recommendation(
    session_factory: Callable[[], Session],
) -> None:
    service = SupportCaseService(session_factory)
    opened = service.open_transaction_dispute("tx-low-risk", "customer-owned", "Reclamo")
    service.respond_to_approval(opened.caseId, "customer-owned", approved=True)
    resolved = seed_legacy_resolution(session_factory, service, opened.caseId)

    with pytest.raises(PermissionError, match="authenticated customer"):
        service.dismiss_recommendation(resolved.caseId, "customer-foreign")


@pytest.mark.parametrize("score,outcome", [
    (Decimal("31.9999"), "fast_track"), (Decimal("32"), "escalated"),
])
def test_triage_threshold_boundary(
    session_factory: Callable[[], Session], score: Decimal, outcome: str,
) -> None:
    with session_factory() as session:
        transaction = session.get(TransactionRecord, "tx-low-risk")
        assert transaction is not None
        transaction.fraud_score = score
        session.add(transaction)
        session.commit()
    service = SupportCaseService(session_factory)
    opened = service.open_transaction_dispute("tx-low-risk", "customer-owned", "Unrecognized charge")
    result = service.respond_to_approval(opened.caseId, "customer-owned", approved=True)
    assert result.triageOutcome == outcome
    assert result.status == "IN_REVIEW"
    assert result.resolutionOutcome is None
    assert result.recommendationType is None


@pytest.mark.parametrize("transaction_id", ["tx-low-risk", "tx-high-risk"])
@pytest.mark.parametrize("approved", [True, False])
def test_repeated_approval_cannot_mutate_resolved_or_review_case(
    session_factory: Callable[[], Session], transaction_id: str, approved: bool,
) -> None:
    service = SupportCaseService(session_factory)
    opened = service.open_transaction_dispute(transaction_id, "customer-owned", "Unrecognized charge")
    original = service.respond_to_approval(opened.caseId, "customer-owned", approved=True)
    events = service.get_case_timeline(opened.caseId, "customer-owned")
    with pytest.raises(ValueError, match="not awaiting approval"):
        service.respond_to_approval(opened.caseId, "customer-owned", approved=approved)
    assert service.get_case(opened.caseId, "customer-owned") == original
    assert service.get_case_timeline(opened.caseId, "customer-owned") == events


def test_foreign_timeline_and_resolution_are_denied_without_mutation(
    session_factory: Callable[[], Session],
) -> None:
    service = SupportCaseService(session_factory)
    opened = service.open_transaction_dispute("tx-high-risk", "customer-owned", "Unrecognized charge")
    original = service.respond_to_approval(opened.caseId, "customer-owned", approved=True)
    with pytest.raises(PermissionError, match="authenticated customer"):
        service.get_case_timeline(opened.caseId, "customer-foreign")
    with pytest.raises(PermissionError, match="authenticated customer"):
        service.resolve_case(opened.caseId, "customer-foreign", "fraud_confirmed_refund_issued")
    assert service.get_case(opened.caseId, "customer-owned") == original


def test_resolution_and_optout_timeline_matches_service_transitions(
    session_factory: Callable[[], Session],
) -> None:
    service = SupportCaseService(session_factory)
    opened = service.open_transaction_dispute("tx-high-risk", "customer-owned", "Unrecognized charge")
    service.respond_to_approval(opened.caseId, "customer-owned", approved=True)
    seed_legacy_resolution(session_factory, service, opened.caseId)
    service.dismiss_recommendation(opened.caseId, "customer-owned")
    assert [event.eventType for event in service.get_case_timeline(opened.caseId, "customer-owned")] == [
        "CASE_OPENED", "APPROVAL_REQUESTED", "APPROVAL_GRANTED", "ESCALATED_TO_REVIEW",
        "RESOLVED", "RECOMMENDATION_DISMISSED",
    ]


def seed_legacy_resolution(
    session_factory: Callable[[], Session], service: SupportCaseService, case_id: str,
) -> DisputeCase:
    """Existing legacy resolutions remain readable without retroactive posting."""
    with session_factory() as session:
        case = session.get(SupportCase, case_id)
        assert case is not None
        case.status = "RESOLVED"
        case.resolution_outcome = "fraud_confirmed_refund_issued"
        case.recommendation_type = "transaction_alerts"
        case.recommendation_rationale = "Legacy recommendation"
        session.add(case)
        session.add(SupportCaseEvent(case_id=case_id, event_type="RESOLVED", actor="agent"))
        session.commit()
    return service.get_case(case_id, "customer-owned")


@pytest.fixture
def preview_service(session_factory: Callable[[], Session]) -> SupportCaseService:
    return SupportCaseService(session_factory, preview_secret="test-preview-signing-key-only")


def test_preview_and_recovery_are_read_only_with_safe_location(
    session_factory: Callable[[], Session], preview_service: SupportCaseService,
) -> None:
    with session_factory() as session:
        transaction = session.get(TransactionRecord, "tx-low-risk")
        assert transaction is not None
        transaction.transaction_country = "Colombia"
        transaction.transaction_city = "Bogota"
        session.add(transaction)
        session.commit()
    preview = preview_service.preview_transaction_dispute(
        "tx-low-risk", "customer-owned", "  Unrecognized  ",
    )
    assert preview.reason == "Unrecognized"
    assert preview.transaction.country == "Colombia"
    assert preview.transaction.city == "Bogota"
    assert "fraud_score" not in preview.model_dump_json()
    assert "4111111111111111" not in preview.model_dump_json()
    assert preview_service.recover_transaction_dispute(preview.previewToken, "customer-owned") is None
    with session_factory() as session:
        assert session.exec(select(SupportCase)).all() == []
        assert session.exec(select(SupportCaseEvent)).all() == []


@pytest.mark.parametrize("transaction_id,outcome", [
    ("tx-low-risk", "fast_track"), ("tx-high-risk", "escalated"),
    ("tx-no-score", "insufficient_signal"),
])
def test_consent_acceptance_is_atomic_and_same_token_is_idempotent(
    preview_service: SupportCaseService, transaction_id: str, outcome: str,
) -> None:
    preview = preview_service.preview_transaction_dispute(transaction_id, "customer-owned", "Unrecognized")
    accepted = preview_service.accept_transaction_dispute(preview.previewToken, "customer-owned")
    assert accepted.status == "IN_REVIEW"
    assert accepted.triageOutcome == outcome
    assert accepted.resolutionOutcome is None
    timeline = preview_service.get_case_timeline(accepted.caseId, "customer-owned")
    assert [event.eventType for event in timeline][:2] == ["CASE_OPENED", "APPROVAL_GRANTED"]
    assert len(timeline) == 3
    assert preview_service.accept_transaction_dispute(preview.previewToken, "customer-owned") == accepted
    assert preview_service.recover_transaction_dispute(preview.previewToken, "customer-owned") == accepted
    assert preview_service.get_case_timeline(accepted.caseId, "customer-owned") == timeline


@pytest.mark.parametrize("token", ["", "not-a-token", "a.b.c"])
def test_invalid_preview_rejected(preview_service: SupportCaseService, token: str) -> None:
    from banking_transaction.consent.preview import DisputePreviewError

    with pytest.raises(DisputePreviewError):
        preview_service.accept_transaction_dispute(token, "customer-owned")


def test_preview_is_customer_bound_and_tamper_resistant(preview_service: SupportCaseService) -> None:
    from banking_transaction.consent.preview import DisputePreviewError

    preview = preview_service.preview_transaction_dispute("tx-low-risk", "customer-owned", "Unrecognized")
    with pytest.raises(PermissionError):
        preview_service.accept_transaction_dispute(preview.previewToken, "customer-foreign")
    with pytest.raises(DisputePreviewError):
        preview_service.accept_transaction_dispute(preview.previewToken + "tampered", "customer-owned")


@pytest.mark.parametrize("field,value", [("amount", Decimal("121")), ("fraud_score", Decimal("90"))])
def test_changed_evidence_rejects_acceptance_without_writes(
    session_factory: Callable[[], Session], preview_service: SupportCaseService, field: str, value: Decimal,
) -> None:
    from banking_transaction.consent.preview import DisputePreviewError

    preview = preview_service.preview_transaction_dispute("tx-low-risk", "customer-owned", "Unrecognized")
    with session_factory() as session:
        transaction = session.get(TransactionRecord, "tx-low-risk")
        assert transaction is not None
        setattr(transaction, field, value)
        session.add(transaction)
        session.commit()
    with pytest.raises(DisputePreviewError, match="DISPUTE_PREVIEW_STALE"):
        preview_service.accept_transaction_dispute(preview.previewToken, "customer-owned")
    with session_factory() as session:
        assert session.exec(select(SupportCase)).all() == []
        assert session.exec(select(SupportCaseEvent)).all() == []


def test_acceptance_failure_rolls_back_case_and_events(
    session_factory: Callable[[], Session], preview_service: SupportCaseService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from banking_transaction.services import disputes as dispute_service
    preview = preview_service.preview_transaction_dispute("tx-low-risk", "customer-owned", "Unrecognized")

    def fail_approval(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic consent failure")

    monkeypatch.setattr(dispute_service, "_grant_approval", fail_approval)
    with pytest.raises(RuntimeError, match="synthetic consent failure"):
        preview_service.accept_transaction_dispute(preview.previewToken, "customer-owned")
    with session_factory() as session:
        assert session.exec(select(SupportCase)).all() == []
        assert session.exec(select(SupportCaseEvent)).all() == []


def test_distinct_proposals_cannot_create_duplicate_active_cases(preview_service: SupportCaseService) -> None:
    from banking_transaction.services.disputes import ActiveDisputeError

    first = preview_service.preview_transaction_dispute("tx-low-risk", "customer-owned", "First reason")
    second = preview_service.preview_transaction_dispute("tx-low-risk", "customer-owned", "Second reason")
    preview_service.accept_transaction_dispute(first.previewToken, "customer-owned")
    with pytest.raises(ActiveDisputeError):
        preview_service.accept_transaction_dispute(second.previewToken, "customer-owned")
    assert preview_service.recover_transaction_dispute(second.previewToken, "customer-owned") is None


@pytest.mark.parametrize("phase", ["acceptance", "recovery"])
def test_preview_deadlines_reject_without_writes(
    preview_service: SupportCaseService, session_factory: Callable[[], Session], phase: str,
) -> None:
    import jwt
    from banking_transaction.consent.preview import DisputePreviewError

    preview = preview_service.preview_transaction_dispute("tx-low-risk", "customer-owned", "Reason")
    claims = jwt.decode(preview.previewToken, options={"verify_signature": False})
    now = int(datetime.now(timezone.utc).timestamp())
    claims.update(iat=now - 90000, acceptUntil=now - 10)
    if phase == "recovery":
        claims["exp"] = now - 1
    token = jwt.encode(claims, "test-preview-signing-key-only", algorithm="HS256")
    operation = (preview_service.accept_transaction_dispute if phase == "acceptance"
                 else preview_service.recover_transaction_dispute)
    with pytest.raises(DisputePreviewError):
        operation(token, "customer-owned")
    with session_factory() as session:
        assert session.exec(select(SupportCase)).all() == []
        assert session.exec(select(SupportCaseEvent)).all() == []


@pytest.mark.parametrize("field,value", [
    ("jti", "../invalid"), ("reason", " "), ("transactionId", 123),
    ("acceptUntil", True), ("iat", False), ("evidence", None),
])
def test_malformed_signed_preview_claims_are_rejected(
    preview_service: SupportCaseService, field: str, value: object,
) -> None:
    import jwt
    from banking_transaction.consent.preview import DisputePreviewError

    preview = preview_service.preview_transaction_dispute("tx-low-risk", "customer-owned", "Reason")
    claims = jwt.decode(preview.previewToken, options={"verify_signature": False})
    claims[field] = value
    token = jwt.encode(claims, "test-preview-signing-key-only", algorithm="HS256")
    with pytest.raises(DisputePreviewError):
        preview_service.accept_transaction_dispute(token, "customer-owned")


@pytest.mark.parametrize("field,value", [
    ("customer_id", "customer-foreign"), ("product_status", "Blocked"),
    ("product_type", "Savings Account"), ("product_number", "4333333333333333"),
])
def test_product_changes_after_preview_reject_acceptance(
    preview_service: SupportCaseService, session_factory: Callable[[], Session],
    field: str, value: str,
) -> None:
    preview = preview_service.preview_transaction_dispute("tx-low-risk", "customer-owned", "Reason")
    with session_factory() as session:
        product = session.get(Product, "card-owned")
        assert product is not None
        setattr(product, field, value)
        session.add(product)
        session.commit()
    with pytest.raises((PermissionError, ValueError)):
        preview_service.accept_transaction_dispute(preview.previewToken, "customer-owned")
    with session_factory() as session:
        assert session.exec(select(SupportCase)).all() == []


def test_accepted_preview_recovers_terminal_case_without_reopening(
    preview_service: SupportCaseService, session_factory: Callable[[], Session],
) -> None:
    preview = preview_service.preview_transaction_dispute("tx-low-risk", "customer-owned", "Reason")
    accepted = preview_service.accept_transaction_dispute(preview.previewToken, "customer-owned")
    with session_factory() as session:
        case = session.get(SupportCase, accepted.caseId)
        assert case is not None
        case.status = "RESOLVED"
        session.add(case)
        session.commit()
    recovered = preview_service.accept_transaction_dispute(preview.previewToken, "customer-owned")
    assert recovered.caseId == accepted.caseId
    assert recovered.status == "RESOLVED"
    assert len(preview_service.get_case_timeline(accepted.caseId, "customer-owned")) == 3


@pytest.mark.parametrize("history", [
    [{"role": "system", "text": "hidden"}],
    [{"role": "user", "text": ""}],
    [{"role": "user", "text": 42}],
    [{"role": "user", "text": "x", "token": "excluded"}],
    [{"role": "user", "text": "x"}] * 101,
    [{"role": "user", "text": "x" * 100_001}],
    [{"role": "user", "text": "x" * 50_001}] * 2,
])
def test_acceptance_rejects_invalid_conversation(history: list[dict[str, object]]) -> None:
    with pytest.raises(ValidationError):
        AcceptDisputeRequest(previewToken="proposal", conversationHistory=history)


def test_conversation_snapshot_is_owned_and_immutable(
    preview_service: SupportCaseService, session_factory: Callable[[], Session],
) -> None:
    messages = [ConversationMessage(role="user", text="Óptica Visión"),
                ConversationMessage(role="assistant", text="Earlier purchases found")]
    preview = preview_service.preview_transaction_dispute(
        "tx-low-risk", "customer-owned", "Unrecognized",
    )
    accepted = preview_service.accept_transaction_dispute(
        preview.previewToken, "customer-owned", messages,
    )
    assert preview_service.get_case_conversation(
        accepted.caseId, "customer-owned",
    ) == CaseConversation(messages=messages)
    preview_service.accept_transaction_dispute(preview.previewToken, "customer-owned", [])
    assert preview_service.get_case_conversation(accepted.caseId, "customer-owned").messages == messages
    with pytest.raises(PermissionError):
        preview_service.get_case_conversation(accepted.caseId, "customer-foreign")
    with pytest.raises(PermissionError):
        preview_service.get_case_conversation("missing", "customer-owned")
    operator = OperatorCaseService(session_factory)
    principal = OperatorPrincipal("assigned-operator", 1)
    with pytest.raises(LookupError):
        operator.get_case_conversation(accepted.caseId, principal)
    with session_factory() as session:
        case = session.get(SupportCase, accepted.caseId)
        assert case is not None
        case.assigned_operator_sub = principal.sub
        case.claimed_at = datetime.now(timezone.utc)
        session.add(case)
        session.commit()
    assert operator.get_case_conversation(accepted.caseId, principal).messages == messages
    with pytest.raises(LookupError):
        operator.get_case_conversation(accepted.caseId, OperatorPrincipal("other", 1))


def test_direct_case_conversation_is_empty(
    preview_service: SupportCaseService,
) -> None:
    case = preview_service.open_transaction_dispute("tx-low-risk", "customer-owned", "Reason")
    assert preview_service.get_case_conversation(case.caseId, "customer-owned") == CaseConversation()


def test_conversation_failure_rolls_back_intake(
    preview_service: SupportCaseService, session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview = preview_service.preview_transaction_dispute(
        "tx-low-risk", "customer-owned", "Reason",
    )

    def fail_snapshot(session: Session, case_id: str, history: CaseConversation) -> None:
        raise RuntimeError("synthetic snapshot failure")

    from banking_transaction.services import disputes as dispute_service

    monkeypatch.setattr(dispute_service, "append_conversation", fail_snapshot)
    with pytest.raises(RuntimeError, match="synthetic snapshot failure"):
        preview_service.accept_transaction_dispute(
            preview.previewToken, "customer-owned", [ConversationMessage(role="user", text="Reason")],
        )
    with session_factory() as session:
        assert session.exec(select(SupportCase)).all() == []
        assert session.exec(select(SupportCaseEvent)).all() == []
        assert session.exec(select(CaseConversationSnapshot)).all() == []


def test_mcp_sdk_preview_acceptance_and_recovery(
    preview_service: SupportCaseService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from fastmcp import Client
    from banking_transaction import mcp_tools
    monkeypatch.setattr(mcp_tools, "dispute_service", preview_service)
    monkeypatch.setattr(
        mcp_tools, "get_customer_id", lambda headers, *, mutation=False: "customer-owned",
    )

    async def run_flow() -> None:
        async with Client(mcp_tools.mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            assert set(tools["reportTransactionDispute"].inputSchema["properties"]) == {"preview_token"}
            preview = await client.call_tool("previewTransactionDispute", {
                "transaction_id": "tx-low-risk", "reason": "Unrecognized",
            })
            token = preview.data["previewToken"]
            assert "caseId" not in preview.data
            empty = await client.call_tool("recoverTransactionDispute", {"preview_token": token})
            assert empty.data is None
            accepted = await client.call_tool("reportTransactionDispute", {"preview_token": token})
            assert accepted.data["status"] == "IN_REVIEW"
            recovered = await client.call_tool("recoverTransactionDispute", {"preview_token": token})
            assert recovered.data["caseId"] == accepted.data["caseId"]

    asyncio.run(run_flow())
