from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
import logging
from typing import Annotated, Optional

from banking_shared.jwt_identity import get_jwt_customer_id
from banking_transaction.models.transactions import Transaction, TransactionPage
from banking_transaction.services.transactions import transaction_service_singleton as service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/products/{product_id}/history", response_model=TransactionPage)
def get_card_transaction_history(
    product_id: str,
    customer_id: Annotated[str, Depends(get_jwt_customer_id)],
    start_date: date | None = None,
    end_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TransactionPage:
    """Read an owned card by opaque product ID without requiring its full PAN."""
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must not be after end_date")
    try:
        items, total = service.get_card_transaction_history(
            product_id, customer_id, start_date, end_date, limit, offset,
        )
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return TransactionPage(
        items=items, total=total, limit=limit, offset=offset,
        start_date=start_date.isoformat() if start_date else None,
        end_date=end_date.isoformat() if end_date else None,
    )


@router.get("/{product_number}/history", response_model=TransactionPage)
def get_transaction_history(
    product_number: str,
    customer_id: Annotated[str, Depends(get_jwt_customer_id)],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Read an owned account's or card's transaction history with inclusive calendar dates."""
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must not be after end_date")
    try:
        items, total = service.get_transaction_history(
            product_number, customer_id, start_date, end_date, limit, offset,
        )
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except ValueError as ve:
        logger.exception("Validation error while getting transaction history")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    return TransactionPage(
        items=items, total=total, limit=limit, offset=offset,
        start_date=start_date.isoformat() if start_date else None,
        end_date=end_date.isoformat() if end_date else None,
    )


@router.get("/{product_number}")
def get_transactions(
    product_number: str,
    customer_id: Annotated[str, Depends(get_jwt_customer_id)],
    payment_type: Optional[str] = Query(None),
    transaction_type: Optional[str] = Query(None),
    card_product_number: Optional[str] = Query(None),
):
    """Get transactions for an account. Optionally filter by payment type.
    """
    try:
        if payment_type or transaction_type:
            transactions = service.get_transactions_by_type(
                product_number,
                customer_id,
                payment_type,
                transaction_type,
                card_product_number,
            )
        else:
            transactions = service.get_transactions(product_number, customer_id)
        return transactions
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except ValueError as ve:
        logger.exception("Validation error while getting transactions")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception:
        logger.exception("Unexpected error while getting transactions")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")


@router.post("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def notify_transaction(
    account_id: str,
    transaction: Transaction,
    customer_id: Annotated[str, Depends(get_jwt_customer_id)],
):
    """Notify a new transaction for an account.
    """
    logger.info("Received request to notify transaction for accountid[%s]. %s", account_id, transaction.json())
    try:
        service.notify_transaction(account_id, transaction, customer_id)
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except ValueError as ve:
        logger.exception("Validation error while notifying transaction")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except RuntimeError as re:
        logger.exception("Runtime error while notifying transaction")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(re))
    except Exception:
        logger.exception("Unexpected error while notifying transaction")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")
