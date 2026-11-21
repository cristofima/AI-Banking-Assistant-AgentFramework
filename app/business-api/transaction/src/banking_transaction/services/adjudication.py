"""Assigned-operator decisions and ingestion-independent, atomic restitution."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from banking_shared.models import Product, RuntimePosting, SupportCase, SupportCaseEvent, TransactionRecord
from banking_shared.runtime import effective_balance
from banking_shared.identity_models import Operator
from sqlmodel import Session, select

from banking_transaction.auth.operator_identity import OperatorPrincipal


class AdjudicationConflict(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def owned_case(session: Session, case_id: str, principal: OperatorPrincipal, version: int) -> SupportCase:
    if session.get(Operator, principal.sub) is None:
        raise AdjudicationConflict("OPERATOR_UNAVAILABLE")
    case = session.exec(select(SupportCase).where(
        SupportCase.case_id == case_id, SupportCase.assigned_operator_sub == principal.sub,
    ).with_for_update()).first()
    if case is None or case.claimed_at is None:
        raise LookupError("Case not found")
    consent = session.exec(select(SupportCaseEvent).where(SupportCaseEvent.case_id == case.case_id, SupportCaseEvent.event_type == "APPROVAL_GRANTED", SupportCaseEvent.actor == "customer")).first()
    if consent is None:
        raise AdjudicationConflict("CASE_CONSENT_REQUIRED")
    if case.case_version != version:
        raise AdjudicationConflict("CASE_VERSION_CONFLICT")
    return case


def source_snapshot(session: Session, case: SupportCase) -> dict[str, object]:
    transaction = session.exec(select(TransactionRecord).where(
        TransactionRecord.transaction_id == case.transaction_id,
    ).with_for_update()).first()
    product = session.exec(select(Product).where(
        Product.product_id == case.product_id,
    ).with_for_update()).first()
    if transaction is None or product is None or transaction.customer_id != case.customer_id or product.customer_id != case.customer_id or transaction.product_id != case.product_id:
        raise AdjudicationConflict("EVIDENCE_UNAVAILABLE")
    return {"transactionId": transaction.transaction_id, "productId": product.product_id,
            "productType": product.product_type, "customerId": case.customer_id,
            "amount": str(transaction.amount), "currency": transaction.currency,
            "transactionDate": transaction.transaction_date.isoformat(),
            "status": transaction.transaction_status, "fraudScore": str(transaction.fraud_score) if transaction.fraud_score is not None else None,
            "merchant": transaction.merchant_name, "country": transaction.transaction_country,
            "city": transaction.transaction_city, "responseCode": transaction.response_code,
            "isFraud": transaction.is_fraud, "sourceKind": transaction.source_kind}


def capture_evidence(session: Session, case: SupportCase) -> None:
    if case.evidence_snapshot is None:
        case.evidence_snapshot = source_snapshot(session, case)
        case.evidence_version = 1
        session.add(case)


def destinations(session: Session, case: SupportCase) -> list[Product]:
    source = session.get(Product, case.product_id)
    if source is None or source.customer_id != case.customer_id:
        return []
    if source.product_type == "Credit Card":
        return [source] if source.currency == (case.evidence_snapshot or {}).get("currency") and source.product_status == "Active" else []
    if source.product_type != "Debit Card":
        return []
    products = session.exec(select(Product).where(
        Product.customer_id == case.customer_id, Product.product_status == "Active",
        Product.currency == (case.evidence_snapshot or {}).get("currency"),
        Product.product_type.in_(("Savings Account", "Checking Account")),
    ).order_by(Product.product_id)).all()
    savings = [product for product in products if product.product_type == "Savings Account"]
    return savings or list(products)


def audit(session: Session, case: SupportCase, principal: OperatorPrincipal, event_type: str, message: str) -> None:
    session.add(SupportCaseEvent(case_id=case.case_id, event_type=event_type, actor="operator",
        message=message, operator_sub=principal.sub, operator_identity_version=principal.identity_version,
        claim_version=case.claim_version))


def advance(case: SupportCase) -> None:
    case.case_version += 1
    case.updated_at = datetime.now(timezone.utc)


def execute_effects(session: Session, case: SupportCase, principal: OperatorPrincipal, destination_id: str | None) -> None:
    if case.status != "PENDING_EFFECTS" or case.verdict != "valid":
        raise AdjudicationConflict("CASE_VERDICT_CONFLICT")
    try:
        snapshot = source_snapshot(session, case)
    except AdjudicationConflict:
        case.effect_code = "EVIDENCE_UNAVAILABLE"
        return
    existing = session.get(RuntimePosting, case.transaction_id)
    if existing is not None:
        case.effect_code = "CREDIT_ALREADY_APPLIED"
        return
    if case.evidence_snapshot != snapshot:
        case.effect_code = "EVIDENCE_CHANGED"
        return
    choices = destinations(session, case)
    if not choices:
        case.effect_code = "DESTINATION_UNAVAILABLE"
        return
    if destination_id is None and len(choices) != 1:
        case.effect_code = "DESTINATION_REQUIRED"
        return
    destination = next((item for item in choices if item.product_id == destination_id), None) if destination_id else choices[0]
    if destination is None:
        case.effect_code = "DESTINATION_NOT_ELIGIBLE"
        return
    destination = session.exec(select(Product).where(Product.product_id == destination.product_id).with_for_update()).one()
    # Product locks serialize competing credits and eligibility changes.
    if (
        destination.product_status != "Active"
        or destination.customer_id != case.customer_id
        or destination.currency != case.evidence_snapshot["currency"]
        or destination.product_id not in {item.product_id for item in destinations(session, case)}
    ):
        case.effect_code = "DESTINATION_UNAVAILABLE"
        return
    balance = effective_balance(session, destination)
    amount = Decimal(str(case.evidence_snapshot["amount"]))
    if balance is None or amount <= 0 or case.evidence_snapshot["status"] != "Approved" or case.evidence_snapshot["sourceKind"] != "source":
        case.effect_code = "EVIDENCE_UNAVAILABLE"
        return
    now = datetime.now(timezone.utc)
    movement_id = f"REFUND-{uuid4().hex}"
    delta = -amount if destination.product_type == "Credit Card" else amount
    session.add(TransactionRecord(transaction_id=movement_id, transaction_date=now, process_date=now.date(),
        product_id=destination.product_id, customer_id=case.customer_id, transaction_type="Refund",
        transaction_category="Dispute refund", amount=amount, currency=destination.currency,
        transaction_status="Approved", channel="Dispute restitution", source_kind="runtime",
        original_transaction_id=case.transaction_id, support_case_id=case.case_id))
    session.flush()
    session.add(RuntimePosting(original_transaction_id=case.transaction_id, case_id=case.case_id,
        movement_id=movement_id, product_id=destination.product_id, customer_id=case.customer_id,
        amount=amount, balance_delta=delta, currency=destination.currency, operator_sub=principal.sub, executed_at=now))
    case.status = "RESOLVED_VALID"
    case.effect_code = None
    case.resolved_at = now
    case.resolution_outcome = "fraud_confirmed_refund_issued"
    case.recommendation_type = "transaction_alerts"
    case.recommendation_rationale = "Enabling instant transaction alerts can help identify unfamiliar charges."
    advance(case)
    audit(session, case, principal, "FINANCIAL_EFFECT_EXECUTED", "Full original-currency restitution posted to the selected owned product; source movement unchanged")
    session.add(case)
    session.flush()
