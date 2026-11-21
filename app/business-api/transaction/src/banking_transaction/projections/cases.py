"""Read-only support-case DTO and historical audit projections."""
from __future__ import annotations

from sqlmodel import Session, select

from banking_shared.models import CardProtection, RuntimePosting, Product, SupportCase, SupportCaseEvent
from banking_shared.product_types import CARD_PRODUCT_TYPES
from banking_transaction.models.transactions import DisputeCase, DisputeCaseEvent

FAVORABLE_RESOLUTION_OUTCOMES = {"fast_tracked_provisional_credit", "fraud_confirmed_refund_issued"}


def effects(session: Session, case: SupportCase) -> dict[str, str] | None:
    posting = session.exec(select(RuntimePosting).where(RuntimePosting.case_id == case.case_id)).first()
    return _effects_payload(posting)


def _effects_payload(posting: RuntimePosting | None) -> dict[str, str] | None:
    if posting is None:
        return None
    return {"movementId": posting.movement_id, "destinationProductId": posting.product_id,
            "amount": str(posting.amount), "currency": posting.currency,
            "balanceDelta": str(posting.balance_delta), "executedAt": posting.executed_at.isoformat()}


def protection(session: Session, case: SupportCase) -> dict[str, object] | None:
    return _protection_payload(session.get(CardProtection, case.product_id))


def _protection_payload(record: CardProtection | None) -> dict[str, object] | None:
    if record is None:
        return None
    return {"blocked": record.blocked, "priorStatus": record.prior_status,
            "rationale": record.rationale, "caseId": record.case_id,
            "updatedAt": record.updated_at.isoformat(), "scope": "LOCAL_PRODUCT_ONLY"}


def product_protection_status(session: Session, case: SupportCase) -> str | None:
    product = session.exec(select(Product).where(
        Product.product_id == case.product_id, Product.customer_id == case.customer_id,
    )).first()
    if product is None:
        return None
    record = session.get(CardProtection, product.product_id)
    return "Blocked" if record is not None and record.blocked else product.product_status


def _product_display_number(product: Product | None) -> str | None:
    if product is None:
        return None
    product_number = product.product_number
    if product.product_type in CARD_PRODUCT_TYPES and product_number and len(product_number) > 4:
        return f"**** {product_number[-4:]}"
    return product_number


def _to_dispute_case(session: Session, case: SupportCase, product: Product | None) -> DisputeCase:
    posting = session.exec(select(RuntimePosting).where(RuntimePosting.case_id == case.case_id)).first()
    card_protection = session.get(CardProtection, case.product_id)
    return DisputeCase(
        caseId=case.case_id,
        productNumber=_product_display_number(product),
        transactionId=case.transaction_id,
        reason=case.reason,
        status=case.status,
        triageOutcome=case.triage_outcome,
        resolutionOutcome=case.resolution_outcome,
        resolutionNotes=case.resolution_notes,
        financialEffectsStatus="EXECUTED" if posting else ("PENDING" if case.status == "PENDING_EFFECTS" else "NOT_EXECUTED"),
        cardProtectionStatus="BLOCKED" if card_protection and card_protection.blocked else "NOT_BLOCKED",
        caseVersion=case.case_version,
        verdict=case.verdict,
        rationale=case.resolution_notes,
        effectCode=case.effect_code,
        effects=_effects_payload(posting),
        cardProtection=_protection_payload(card_protection),
        recommendationType=case.recommendation_type,
        recommendationRationale=case.recommendation_rationale,
        recommendationOptedOut=case.recommendation_opted_out,
        openedAt=case.opened_at.isoformat(),
        updatedAt=case.updated_at.isoformat(),
        resolvedAt=case.resolved_at.isoformat() if case.resolved_at else None,
    )


def _to_dispute_case_event(event: SupportCaseEvent) -> DisputeCaseEvent:
    legacy_messages = {
        ("APPROVAL_REQUESTED", "system", "Customer confirmation required to proceed with the dispute and card block"):
            "Customer confirmation requested for the persisted dispute workflow; "
            "card protection and financial effects are not implemented",
        ("APPROVAL_GRANTED", "customer", "Customer approved the dispute and card block"):
            "Customer approved the dispute workflow; no card block or posting was performed",
        ("FAST_TRACKED", "system", "Low fraud-risk score; fast-tracked without manual review"):
            "Low stored fraud-risk score; rule-based fast-track, not a legitimacy verdict",
        ("RESOLVED", "system", "Provisional credit issued; case resolved without manual review"):
            "Case closed; no provisional credit, refund, balance change, or card protection was executed",
    }
    for outcome in FAVORABLE_RESOLUTION_OUTCOMES:
        legacy_messages[("RESOLVED", "agent", f"Case resolved: {outcome}")] = (
            "Case closed; no financial or card-protection effects were executed, "
            "and no human verdict is recorded"
        )
    display_message = legacy_messages.get((event.event_type, event.actor, event.message), event.message)
    legacy_review_prefixes = (
        "Elevated fraud-risk score; routed to manual review",
        "No fraud score available for this transaction; routed to manual review",
    )
    if event.event_type == "ESCALATED_TO_REVIEW" and event.actor == "system":
        for prefix in legacy_review_prefixes:
            suffix = event.message.removeprefix(prefix)
            if event.message.startswith(prefix) and (
                suffix == " (no agent available)"
                or (suffix.startswith(" (assigned to agent ") and suffix.endswith(")"))
            ):
                display_message = (
                    "Review routing and catalog assignment recorded; "
                    "no human investigation or verdict is recorded"
                )
                break
    return DisputeCaseEvent(
        eventType=event.event_type,
        actor=event.actor,
        message=event.message,
        displayMessage=display_message,
        createdAt=event.created_at.isoformat(),
    )
