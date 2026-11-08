from typing import Annotated, List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
import logging

from banking_account.services.errors import AccountOperationError
from banking_shared.jwt_identity import get_jwt_customer_id
from banking_account.models.products import AccountSummary, Card, CardSummary
from banking_account.services.products import account_service_singleton, card_service_singleton

logger = logging.getLogger(__name__)
router = APIRouter()


class CardAmountRequest(BaseModel):
    amount: float = Field(..., gt=0, description="Amount for the requested card operation")


def _to_runtime_http_error(err: RuntimeError) -> HTTPException:
    code = err.status_code if isinstance(err, AccountOperationError) else status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=str(err))


@router.get("/accounts", response_model=List[AccountSummary])
def list_accounts(customer_id: Annotated[str, Depends(get_jwt_customer_id)]):
    """List all bank accounts owned by the authenticated customer."""
    return account_service_singleton.list_accounts(customer_id)


@router.get("/cards", response_model=List[CardSummary])
def list_cards(customer_id: Annotated[str, Depends(get_jwt_customer_id)]):
    """List all cards owned by the authenticated customer."""
    return card_service_singleton.list_cards(customer_id)


@router.get("/accounts/{product_number}/cards", response_model=List[Card])
def list_credit_cards(
    product_number: str,
    customer_id: Annotated[str, Depends(get_jwt_customer_id)],
):
    """Return all customer cards after authorizing the account; no account-card linkage exists."""
    try:
        return card_service_singleton.get_credit_cards(product_number, customer_id)
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except ValueError as ve:
        logger.exception("Validation error while listing cards")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception:
        logger.exception("Unexpected error while listing cards")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")


@router.get("/cards/{product_number}", response_model=Card)
def get_card_details(
    product_number: str,
    customer_id: Annotated[str, Depends(get_jwt_customer_id)],
):
    """Return the card details for a single identifier."""
    try:
        card = card_service_singleton.get_card_details(product_number, customer_id)
        if card is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card not found")
        return card
    except ValueError as ve:
        logger.exception("Validation error while retrieving card detail")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error


@router.post("/cards/{card_id}/recharge", response_model=Card)
def recharge_card(
    card_id: str,
    request: CardAmountRequest,
    customer_id: Annotated[str, Depends(get_jwt_customer_id)],
):
    """Compatibility endpoint: validates ownership and amount, then reports recharge unavailable."""
    logger.info("Recharge card card_id=%s amount=%.2f", card_id, request.amount)
    try:
        return card_service_singleton.recharge_card(card_id, request.amount, customer_id)
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except ValueError as ve:
        logger.exception("Validation error during recharge")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except RuntimeError as re:
        logger.exception("Runtime error during recharge")
        raise _to_runtime_http_error(re)
    except Exception:
        logger.exception("Unexpected error during recharge")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")


@router.post("/cards/{card_id}/pay", response_model=Card)
def pay_with_card(
    card_id: str,
    request: CardAmountRequest,
    customer_id: Annotated[str, Depends(get_jwt_customer_id)],
):
    """Compatibility endpoint: validates ownership and amount, then reports payment unavailable."""
    logger.info("Pay with card card_id=%s amount=%.2f", card_id, request.amount)
    try:
        return card_service_singleton.pay_with_card(card_id, request.amount, customer_id)
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except ValueError as ve:
        logger.exception("Validation error during payment")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except RuntimeError as re:
        logger.exception("Runtime error during payment")
        raise _to_runtime_http_error(re)
    except Exception:
        logger.exception("Unexpected error during payment")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")
