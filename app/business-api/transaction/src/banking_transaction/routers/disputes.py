"""REST endpoints for the persisted transaction-dispute support-case workflow."""
from fastapi import APIRouter, Depends, HTTPException, status
import logging
from typing import Annotated

from banking_transaction.services.disputes import (
    ActiveDisputeError,
    CardOnlyDisputeError,
    support_case_service_singleton as service,
)
from banking_shared.jwt_identity import get_jwt_customer_id
from banking_transaction.consent.preview import DisputePreviewError
from banking_transaction.models.conversation import CaseConversation
from banking_transaction.models.transactions import (
    AcceptDisputeRequest, DisputeApprovalRequest, DisputeCase, DisputePreview,
    OpenDisputeRequest, ResolveCaseRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _intake_error(error: Exception) -> HTTPException:
    if isinstance(error, PermissionError):
        return HTTPException(403, detail={"code": "DISPUTE_UNAVAILABLE"})
    if isinstance(error, ActiveDisputeError):
        return HTTPException(409, detail={"code": "DISPUTE_ALREADY_ACTIVE"})
    if isinstance(error, CardOnlyDisputeError):
        return HTTPException(400, detail={"code": "DISPUTE_CARD_ONLY"})
    code = error.code if isinstance(error, DisputePreviewError) else "DISPUTE_INELIGIBLE"
    return HTTPException(400, detail={"code": code})


@router.get("")
def list_support_cases(customer_id: Annotated[str, Depends(get_jwt_customer_id)]):
    """List the authenticated customer's support cases."""
    return service.list_cases(customer_id)


@router.post("/preview")
def preview_support_case(
    request: OpenDisputeRequest,
    customer_id: Annotated[str, Depends(get_jwt_customer_id)],
) -> DisputePreview:
    """Read owned eligible context without creating a case or audit events."""
    try:
        return service.preview_transaction_dispute(request.transactionId, customer_id, request.reason)
    except (PermissionError, ValueError) as error:
        raise _intake_error(error) from error


@router.post("/recovery")
def recover_support_case(
    request: AcceptDisputeRequest,
    customer_id: Annotated[str, Depends(get_jwt_customer_id)],
) -> DisputeCase | None:
    """Read the confirmed result of the same proposal without resubmitting intake."""
    try:
        return service.recover_transaction_dispute(request.previewToken, customer_id)
    except (PermissionError, ValueError) as error:
        raise _intake_error(error) from error


@router.post("", status_code=status.HTTP_201_CREATED)
def open_support_case(
    request: AcceptDisputeRequest,
    customer_id: Annotated[str, Depends(get_jwt_customer_id)],
) -> DisputeCase:
    """Accept a verified proposal and atomically create the consented case."""
    try:
        return service.accept_transaction_dispute(
            request.previewToken, customer_id, request.conversationHistory,
        )
    except (PermissionError, ValueError) as error:
        raise _intake_error(error) from error


@router.get("/{case_id}")
def get_support_case(
    case_id: str,
    customer_id: Annotated[str, Depends(get_jwt_customer_id)],
):
    """Get a single support case by ID."""
    try:
        return service.get_case(case_id, customer_id)
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error


@router.get("/{case_id}/conversation", response_model=CaseConversation)
def get_support_case_conversation(
    case_id: str, customer_id: Annotated[str, Depends(get_jwt_customer_id)],
) -> CaseConversation:
    try:
        return service.get_case_conversation(case_id, customer_id)
    except PermissionError:
        raise HTTPException(403, detail={"code": "DISPUTE_UNAVAILABLE"}) from None


@router.get("/{case_id}/timeline")
def get_support_case_timeline(
    case_id: str,
    customer_id: Annotated[str, Depends(get_jwt_customer_id)],
):
    """Get the event timeline for a support case."""
    try:
        return service.get_case_timeline(case_id, customer_id)
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error


@router.post("/{case_id}/approval")
def respond_to_support_case_approval(
    case_id: str,
    request: DisputeApprovalRequest,
    customer_id: Annotated[str, Depends(get_jwt_customer_id)],
):
    """Record the customer's approval or decline for a case awaiting approval."""
    try:
        return service.respond_to_approval(case_id, customer_id, request.approved)
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except ValueError as ve:
        logger.exception("Validation error while responding to a support case approval")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve)) from ve


@router.post("/{case_id}/resolve")
def resolve_support_case(
    case_id: str,
    request: ResolveCaseRequest,
    customer_id: Annotated[str, Depends(get_jwt_customer_id)],
):
    """Retired customer resolution boundary; assigned operator adjudication only."""
    raise HTTPException(status_code=403, detail={"code": "OPERATOR_ADJUDICATION_REQUIRED"})


@router.post("/{case_id}/recommendation/dismiss")
def dismiss_support_case_recommendation(
    case_id: str,
    customer_id: Annotated[str, Depends(get_jwt_customer_id)],
) -> DisputeCase:
    try:
        return service.dismiss_recommendation(case_id, customer_id)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
