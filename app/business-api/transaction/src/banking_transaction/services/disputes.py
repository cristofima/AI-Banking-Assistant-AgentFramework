"""Persisted transaction-dispute support-case workflow.

State machine: OPEN -> WAITING_USER_APPROVAL -> IN_REVIEW -> RESOLVED. The AI agent
only performs intake/triage; it never decides a dispute's legitimacy. Deterministic
triage uses fraud_score (populated at ingestion, never computed by the agent) to
classify low-risk cases and persist review routing. Every approved case stays
IN_REVIEW until explicit resolution; no score triggers automatic closure.
Real operator takeover is independent from the retained historical ServiceAgent catalog.
New intake persists only after consent; operator verdicts and financial effects remain
separate explicit operations. See
app/business-api/data/scripts/evaluate_fraud_threshold.py for the threshold's offline
precision/recall evidence.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from banking_shared.database import create_session
from banking_shared.models import (
    Product,
    SupportCase,
    SupportCaseEvent,
    TransactionRecord,
)
from banking_shared.product_types import CARD_PRODUCT_TYPES
from banking_transaction.models.transactions import DisputeCase, DisputeCaseEvent, DisputePreview
from banking_transaction.models.conversation import CaseConversation, ConversationMessage
from banking_transaction.services.conversation import append_conversation, read_conversation
from banking_transaction.consent.preview import (
    DisputePreviewError, configured_preview_secret, evidence_digest, issue_preview, read_preview,
)
from banking_transaction.projections.transactions import to_transaction
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]

# fraud_score is a 0-100 scale (not [0,1]); >=32 reaches precision 1.0 / recall 0.668 /
# F1 0.801 against the dataset's own is_fraud label (see evaluate_fraud_threshold.py).
FRAUD_SCORE_ESCALATION_THRESHOLD = Decimal("32")

DISPUTE_WINDOW_DAYS = 365

DISPUTABLE_TRANSACTION_STATUS = "Approved"
ACTIVE_PRODUCT_STATUS = "Active"

TRIAGE_FAST_TRACK = "fast_track"
TRIAGE_ESCALATED = "escalated"
TRIAGE_INSUFFICIENT_SIGNAL = "insufficient_signal"
MAX_TIMELINE_EVENTS = 100


class ActiveDisputeError(ValueError):
    """An existing active case prevents sequential duplicate intake."""


class CardOnlyDisputeError(ValueError):
    """New disputes are restricted to debit and credit card transactions."""


from banking_transaction.projections.cases import _to_dispute_case, _to_dispute_case_event


class SupportCaseService:
    def __init__(
        self, session_factory: SessionFactory = create_session, preview_secret: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._preview_secret = preview_secret

    def _signing_secret(self) -> str:
        return self._preview_secret or configured_preview_secret()

    def preview_transaction_dispute(
        self, transaction_id: str, customer_id: str, reason: str,
    ) -> DisputePreview:
        _require_identifier(reason, "reason")
        with self._session_factory() as session:
            transaction, product = _eligible_intake(session, transaction_id, customer_id)
            token, expires = issue_preview(
                self._signing_secret(), customer_id, transaction_id, reason.strip(),
                evidence_digest(transaction, product),
            )
            return DisputePreview(
                previewToken=token, transactionId=transaction_id, reason=reason.strip(),
                expiresAt=expires.isoformat(), transaction=to_transaction(transaction, product),
            )

    def recover_transaction_dispute(
        self, preview_token: str, customer_id: str,
    ) -> DisputeCase | None:
        claims = read_preview(self._signing_secret(), preview_token, customer_id)
        with self._session_factory() as session:
            case = session.get(SupportCase, f"CASE-{claims['jti']}")
            if case is None:
                return None
            if (case.customer_id != customer_id or case.transaction_id != claims["transactionId"]
                    or case.reason != claims["reason"]):
                raise PermissionError("Dispute preview unavailable")
            return _to_dispute_case(session, case, _get_case_product(session, case))

    def get_case_conversation(self, case_id: str, customer_id: str) -> CaseConversation:
        with self._session_factory() as session:
            case = _get_owned_case(session, case_id, customer_id)
            return read_conversation(session, case.case_id)

    def accept_transaction_dispute(
        self, preview_token: str, customer_id: str,
        conversation_history: list[ConversationMessage] | None = None,
    ) -> DisputeCase:
        history = CaseConversation(messages=conversation_history) if conversation_history is not None else None
        claims = read_preview(self._signing_secret(), preview_token, customer_id)
        recovered = self.recover_transaction_dispute(preview_token, customer_id)
        if recovered is not None:
            return recovered
        if datetime.now(timezone.utc).timestamp() >= claims["acceptUntil"]:
            raise DisputePreviewError("DISPUTE_PREVIEW_EXPIRED")
        case_id = f"CASE-{claims['jti']}"
        with self._session_factory() as session:
            try:
                transaction, product = _eligible_intake(
                    session, claims["transactionId"], customer_id, lock=True,
                )
            except ActiveDisputeError:
                recovered = self.recover_transaction_dispute(preview_token, customer_id)
                if recovered is not None:
                    return recovered
                raise
            if datetime.now(timezone.utc).timestamp() >= claims["acceptUntil"]:
                raise DisputePreviewError("DISPUTE_PREVIEW_EXPIRED")
            if evidence_digest(transaction, product) != claims["evidence"]:
                raise DisputePreviewError("DISPUTE_PREVIEW_STALE")
            case = SupportCase(
                case_id=case_id, customer_id=customer_id, product_id=product.product_id,
                transaction_id=transaction.transaction_id, reason=claims["reason"],
                status="WAITING_USER_APPROVAL", fraud_score_at_open=transaction.fraud_score,
            )
            session.add(case)
            try:
                session.flush()
                _add_event(session, case_id, "CASE_OPENED", "customer",
                           f"Dispute opened for transaction {transaction.transaction_id}")
                _grant_approval(session, case, commit=False)
                if history is not None:
                    append_conversation(session, case_id, history)
                session.commit()
            except IntegrityError:
                session.rollback()
                recovered = self.recover_transaction_dispute(preview_token, customer_id)
                if recovered is not None:
                    return recovered
                _ensure_no_active_case(session, claims["transactionId"], customer_id)
                raise
            session.refresh(case)
            return _to_dispute_case(session, case, product)

    def open_transaction_dispute(
        self,
        transaction_id: str,
        customer_id: str,
        reason: str,
    ) -> DisputeCase:
        logger.info(
            "open_transaction_dispute called with transaction_id=%s, customer_id=%s",
            transaction_id,
            customer_id,
        )
        _require_identifier(reason, "reason")
        with self._session_factory() as session:
            transaction = _get_owned_transaction(session, transaction_id, customer_id)
            product = _get_active_product(session, transaction.product_id, customer_id)
            if product.product_type not in CARD_PRODUCT_TYPES:
                raise CardOnlyDisputeError("Only debit and credit card transactions can be disputed")
            _ensure_disputable_status(transaction)
            _ensure_no_active_case(session, transaction_id, customer_id)
            _ensure_within_dispute_window(transaction.transaction_date)

            case = SupportCase(
                customer_id=customer_id,
                product_id=product.product_id,
                transaction_id=transaction.transaction_id,
                reason=reason.strip(),
                status="WAITING_USER_APPROVAL",
                fraud_score_at_open=transaction.fraud_score,
            )
            session.add(case)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                _ensure_no_active_case(session, transaction_id, customer_id)
                raise
            _add_event(
                session,
                case.case_id,
                "CASE_OPENED",
                "customer",
                f"Dispute opened for transaction {transaction_id}",
            )
            _add_event(
                session,
                case.case_id,
                "APPROVAL_REQUESTED",
                "system",
                "Customer confirmation required to proceed with the persisted dispute workflow; "
                "no financial posting or card protection has occurred",
            )
            session.commit()
            session.refresh(case)
            return _to_dispute_case(session, case, product)

    def respond_to_approval(
        self,
        case_id: str,
        customer_id: str,
        approved: bool,
    ) -> DisputeCase:
        logger.info(
            "respond_to_approval called with case_id=%s, approved=%s", case_id, approved
        )
        with self._session_factory() as session:
            case = _get_owned_case(session, case_id, customer_id)
            if case.status != "WAITING_USER_APPROVAL":
                raise ValueError(f"Case {case_id} is not awaiting approval")

            case = _decline_approval(session, case) if not approved else _grant_approval(session, case)
            return _to_dispute_case(session, case, _get_case_product(session, case))

    def resolve_case(
        self,
        case_id: str,
        customer_id: str,
        resolution_outcome: str,
        resolution_notes: str | None = None,
    ) -> DisputeCase:
        with self._session_factory() as session:
            _get_owned_case(session, case_id, customer_id)
        raise PermissionError("Assigned operator adjudication is required")

    def list_cases(self, customer_id: str) -> list[DisputeCase]:
        with self._session_factory() as session:
            statement = (
                select(SupportCase)
                .where(SupportCase.customer_id == customer_id)
                .order_by(SupportCase.opened_at.desc())
            )
            return [
                _to_dispute_case(session, case, _get_case_product(session, case))
                for case in session.exec(statement).all()
            ]

    def get_case(self, case_id: str, customer_id: str) -> DisputeCase:
        with self._session_factory() as session:
            case = _get_owned_case(session, case_id, customer_id)
            return _to_dispute_case(session, case, _get_case_product(session, case))

    def get_case_timeline(self, case_id: str, customer_id: str) -> list[DisputeCaseEvent]:
        with self._session_factory() as session:
            _get_owned_case(session, case_id, customer_id)
            statement = (
                select(SupportCaseEvent)
                .where(SupportCaseEvent.case_id == case_id)
                .order_by(SupportCaseEvent.created_at.asc())
                .limit(MAX_TIMELINE_EVENTS)
            )
            return [_to_dispute_case_event(event) for event in session.exec(statement).all()]

    def dismiss_recommendation(self, case_id: str, customer_id: str) -> DisputeCase:
        logger.info("dismiss_recommendation called with case_id=%s", case_id)
        with self._session_factory() as session:
            case = _get_owned_case(session, case_id, customer_id)
            if case.recommendation_type is None:
                raise ValueError(f"Case {case_id} has no recommendation to dismiss")
            case.recommendation_opted_out = True
            case.updated_at = datetime.now(timezone.utc)
            session.add(case)
            _add_event(
                session,
                case.case_id,
                "RECOMMENDATION_DISMISSED",
                "customer",
                "Customer dismissed the post-resolution recommendation",
            )
            session.commit()
            session.refresh(case)
            return _to_dispute_case(session, case, _get_case_product(session, case))


support_case_service_singleton = SupportCaseService()


def _decline_approval(session: Session, case: SupportCase) -> SupportCase:
    _transition(case, "RESOLVED", resolution_outcome="withdrawn_by_customer")
    _add_event(
        session,
        case.case_id,
        "APPROVAL_DECLINED",
        "customer",
        "Customer declined to proceed with the dispute",
    )
    _add_event(session, case.case_id, "RESOLVED", "system", "Case closed: withdrawn by customer")
    session.add(case)
    session.commit()
    session.refresh(case)
    return case


def _grant_approval(session: Session, case: SupportCase, *, commit: bool = True) -> SupportCase:
    triage_outcome = _triage(case.fraud_score_at_open)
    case.triage_outcome = triage_outcome
    _transition(case, "IN_REVIEW")
    _add_event(
        session,
        case.case_id,
        "APPROVAL_GRANTED",
        "customer",
        "Customer approved continuation of the persisted dispute workflow; "
        "no card block or financial posting was performed",
    )

    if triage_outcome == TRIAGE_FAST_TRACK:
        _add_event(
            session,
            case.case_id,
            "REVIEW_REQUIRED",
            "system",
            "Low stored fraud-risk score; explicit resolution required. "
            "The score does not authorize automatic closure or establish a legitimacy verdict",
        )
    else:
        reason = (
            "Elevated fraud-risk score"
            if triage_outcome == TRIAGE_ESCALATED
            else "No fraud score available for this transaction"
        )
        _add_event(
            session,
            case.case_id,
            "ESCALATED_TO_REVIEW",
            "system",
            f"{reason}; awaiting authenticated operator takeover; "
            "no human investigation or verdict has occurred",
        )

    session.add(case)
    if commit:
        session.commit()
        session.refresh(case)
    return case


def _eligible_intake(
    session: Session, transaction_id: str, customer_id: str, *, lock: bool = False,
) -> tuple[TransactionRecord, Product]:
    transaction = _get_owned_transaction(session, transaction_id, customer_id)
    if lock:
        locked_transaction = session.exec(select(TransactionRecord).where(
            TransactionRecord.transaction_id == transaction_id,
            TransactionRecord.customer_id == customer_id,
        ).with_for_update().execution_options(populate_existing=True)).first()
        if locked_transaction is None:
            raise PermissionError("Transaction unavailable")
        transaction = locked_transaction
        if transaction.customer_id != customer_id:
            raise PermissionError("Transaction unavailable")
    product = _get_active_product(session, transaction.product_id, customer_id)
    if lock:
        locked_product = session.exec(select(Product).where(
            Product.product_id == transaction.product_id,
            Product.customer_id == customer_id,
        ).with_for_update().execution_options(populate_existing=True)).first()
        if locked_product is None:
            raise PermissionError("Product unavailable")
        product = locked_product
        if product.product_status != ACTIVE_PRODUCT_STATUS:
            raise PermissionError("Product unavailable")
    if product.product_type not in CARD_PRODUCT_TYPES:
        raise CardOnlyDisputeError("Only card transactions can be disputed")
    _ensure_disputable_status(transaction)
    _ensure_no_active_case(session, transaction_id, customer_id)
    _ensure_within_dispute_window(transaction.transaction_date)
    return transaction, product


def _require_identifier(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} is empty or null")


def _get_owned_transaction(
    session: Session, transaction_id: str, customer_id: str
) -> TransactionRecord:
    _require_identifier(transaction_id, "transaction_id")
    statement = (
        select(TransactionRecord)
        .where(TransactionRecord.transaction_id == transaction_id)
        .where(TransactionRecord.customer_id == customer_id)
    )
    transaction = session.exec(statement).first()
    if transaction is None:
        raise PermissionError("Transaction does not belong to the authenticated customer")
    return transaction


def _get_active_product(session: Session, product_id: str, customer_id: str) -> Product:
    statement = (
        select(Product)
        .where(Product.product_id == product_id)
        .where(Product.customer_id == customer_id)
    )
    product = session.exec(statement).first()
    if product is None:
        raise PermissionError("Product does not belong to the authenticated customer")
    if product.product_status != ACTIVE_PRODUCT_STATUS:
        raise ValueError(
            f"Product must be {ACTIVE_PRODUCT_STATUS} to open a dispute, "
            f"current status: {product.product_status}"
        )
    return product


def _ensure_disputable_status(transaction: TransactionRecord) -> None:
    if transaction.source_kind != "source":
        raise CardOnlyDisputeError("Generated restitution movements cannot be disputed")
    if transaction.transaction_status != DISPUTABLE_TRANSACTION_STATUS:
        raise ValueError(f"Only {DISPUTABLE_TRANSACTION_STATUS} transactions can be disputed")


def _ensure_no_active_case(session: Session, transaction_id: str, customer_id: str) -> None:
    statement = (
        select(SupportCase)
        .where(SupportCase.transaction_id == transaction_id)
        .where(SupportCase.customer_id == customer_id)
        .where(SupportCase.status.in_(("OPEN", "WAITING_USER_APPROVAL", "IN_REVIEW", "PENDING_EFFECTS")))
    )
    if session.exec(statement).first() is not None:
        raise ActiveDisputeError("Transaction already has an active dispute case")


def _ensure_within_dispute_window(transaction_date: datetime) -> None:
    if transaction_date.tzinfo is None:
        transaction_date = transaction_date.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=DISPUTE_WINDOW_DAYS)
    if transaction_date < cutoff:
        raise ValueError(f"Transaction is outside the {DISPUTE_WINDOW_DAYS}-day dispute window")


def _get_owned_case(session: Session, case_id: str, customer_id: str) -> SupportCase:
    _require_identifier(case_id, "case_id")
    statement = (
        select(SupportCase)
        .where(SupportCase.case_id == case_id)
        .where(SupportCase.customer_id == customer_id)
    )
    case = session.exec(statement).first()
    if case is None:
        raise PermissionError("Case does not belong to the authenticated customer")
    return case


def _transition(
    case: SupportCase,
    new_status: str,
    *,
    resolution_outcome: str | None = None,
    resolution_notes: str | None = None,
) -> None:
    case.case_version += 1
    case.status = new_status
    case.updated_at = datetime.now(timezone.utc)
    if new_status == "RESOLVED":
        case.resolved_at = case.updated_at
        if resolution_outcome:
            case.resolution_outcome = resolution_outcome
        if resolution_notes:
            case.resolution_notes = resolution_notes


def _triage(fraud_score: Decimal | None) -> str:
    if fraud_score is None:
        return TRIAGE_INSUFFICIENT_SIGNAL
    if fraud_score >= FRAUD_SCORE_ESCALATION_THRESHOLD:
        return TRIAGE_ESCALATED
    return TRIAGE_FAST_TRACK


def _add_event(
    session: Session,
    case_id: str,
    event_type: str,
    actor: str,
    message: str | None,
) -> None:
    session.add(
        SupportCaseEvent(
            case_id=case_id,
            event_type=event_type,
            actor=actor,
            message=message,
        )
    )


def _get_case_product(session: Session, case: SupportCase) -> Product | None:
    return session.get(Product, case.product_id)
