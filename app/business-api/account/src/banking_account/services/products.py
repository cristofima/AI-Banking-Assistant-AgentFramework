from __future__ import annotations

import logging
import re
from collections.abc import Callable
from decimal import Decimal

from banking_shared.database import create_session
from banking_shared.models import Customer, Product
from banking_shared.runtime import project_runtime, project_runtime_many
from banking_shared.product_types import (
    ACCOUNT_PRODUCT_TYPES,
    CARD_PRODUCT_TYPES,
    card_type,
    normalize_product_type,
)
from banking_account.services.errors import OperationUnavailable
from banking_account.models.products import (
    Account,
    AccountSummary,
    Beneficiary,
    Card,
    CardDiscoveryCandidate,
    CardDiscoveryResult,
    CardSummary,
    PaymentMethod,
    PaymentMethodSummary,
)
from sqlalchemy import func
from sqlmodel import Session, select

from banking_account.projections.products import (
    _to_account,
    _masked_card_number,
    _to_payment_method_summary,
    _to_payment_method,
    _to_account_summary,
    _to_card_summary,
    _to_card,
    _date_value,
    _decimal_float,
    _decimal_text,
)

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


class AccountService:
    def __init__(self, session_factory: SessionFactory = create_session) -> None:
        self._session_factory = session_factory

    def get_account_details(self, account_id: str, customer_id: str) -> Account | None:
        logger.info("Request to get_account_details with account_id: %s", account_id)
        _require_identifier(account_id, "AccountId")
        with self._session_factory() as session:
            account_product = _get_owned_product(
                session,
                account_id,
                customer_id,
                ACCOUNT_PRODUCT_TYPES,
            )
            customer = session.get(Customer, customer_id)
            if customer is None:
                raise PermissionError("Account does not belong to the authenticated customer")
            cards = list(
                session.exec(
                    select(Product)
                    .where(Product.customer_id == customer_id)
                    .where(Product.product_type.in_(CARD_PRODUCT_TYPES))
                    .order_by(Product.product_id)
                ).all()
            )
            projected = project_runtime_many(session, [account_product, *cards])
            return _to_account(projected[0], customer, projected[1:])

    def get_payment_method_details(
        self,
        payment_method_id: str,
        customer_id: str,
    ) -> PaymentMethod | None:
        logger.info(
            "Request to get_payment_method_details with payment_method_id: %s",
            payment_method_id,
        )
        _require_identifier(payment_method_id, "PaymentMethodId")
        with self._session_factory() as session:
            product = _get_owned_product(session, payment_method_id, customer_id)
            return _to_payment_method(project_runtime(session, product))

    def get_registered_beneficiary(
        self,
        account_id: str,
        customer_id: str,
    ) -> list[Beneficiary]:
        logger.info("Request to get_registered_beneficiary with account_id: %s", account_id)
        _require_identifier(account_id, "AccountId")
        with self._session_factory() as session:
            _get_owned_product(session, account_id, customer_id, ACCOUNT_PRODUCT_TYPES)
        raise OperationUnavailable("Registered beneficiaries are unavailable for persisted products")

    def list_accounts(self, customer_id: str) -> list[AccountSummary]:
        logger.info("Request to list_accounts for customer_id: %s", customer_id)
        with self._session_factory() as session:
            products = session.exec(
                select(Product)
                .where(Product.customer_id == customer_id)
                .where(Product.product_type.in_(ACCOUNT_PRODUCT_TYPES))
                .order_by(Product.product_id)
            ).all()
            return [_to_account_summary(product) for product in project_runtime_many(session, list(products))]


class UserService:
    def __init__(self, session_factory: SessionFactory = create_session) -> None:
        self._session_factory = session_factory

    def get_accounts_by_user_name(
        self, user_name: str | None, customer_id: str,
    ) -> list[Account]:
        _require_identifier(customer_id, "CustomerId")
        if user_name is not None:
            _require_identifier(user_name, "UserName")
        with self._session_factory() as session:
            query = select(Customer).where(Customer.customer_id == customer_id)
            if user_name is not None:
                query = query.where(Customer.email == user_name.strip().lower())
            customer = session.exec(query).first()
            if customer is None:
                raise PermissionError("Account does not belong to the authenticated customer")

            products = list(
                session.exec(
                    select(Product)
                    .where(Product.customer_id == customer_id)
                    .where(Product.product_type.in_(ACCOUNT_PRODUCT_TYPES))
                    .order_by(Product.product_id)
                ).all()
            )
            return [_to_account(product, customer, []) for product in project_runtime_many(session, products)]


class CardService:
    def __init__(self, session_factory: SessionFactory = create_session) -> None:
        self._session_factory = session_factory

    def get_credit_cards(self, account_id: str, customer_id: str) -> list[Card]:
        logger.info("Request to get_credit_cards with account_id: %s", account_id)
        _require_identifier(account_id, "AccountId")
        with self._session_factory() as session:
            _get_owned_product(session, account_id, customer_id, ACCOUNT_PRODUCT_TYPES)
            products = session.exec(
                select(Product)
                .where(Product.customer_id == customer_id)
                .where(Product.product_type.in_(CARD_PRODUCT_TYPES))
                .order_by(Product.product_id)
            ).all()
            return [_to_card(product) for product in project_runtime_many(session, list(products))]

    def get_card_details(self, card_id: str, customer_id: str) -> Card | None:
        logger.info("Request to get_card_details for card_id=%s", card_id)
        _require_identifier(card_id, "CardId")
        with self._session_factory() as session:
            product = _get_owned_product(session, card_id, customer_id, CARD_PRODUCT_TYPES)
            return _to_card(project_runtime(session, product))

    def list_cards(self, customer_id: str) -> list[CardSummary]:
        logger.info("Request to list_cards for customer_id: %s", customer_id)
        with self._session_factory() as session:
            products = session.exec(
                select(Product)
                .where(Product.customer_id == customer_id)
                .where(Product.product_type.in_(CARD_PRODUCT_TYPES))
                .order_by(Product.product_id)
            ).all()
            return [_to_card_summary(product) for product in project_runtime_many(session, list(products))]

    def discover_cards_by_suffix(self, suffix: str, customer_id: str) -> CardDiscoveryResult:
        if not re.fullmatch(r"[0-9]{4}", suffix):
            raise ValueError("Card suffix must be exactly four ASCII digits")
        _require_identifier(customer_id, "CustomerId")
        with self._session_factory() as session:
            products = session.exec(
                select(Product)
                .where(Product.customer_id == customer_id)
                .where(Product.product_type.in_(CARD_PRODUCT_TYPES))
                .where(func.rtrim(Product.product_number, " \t\n\r\v\f-").endswith(suffix))
                .order_by(Product.product_id)
                .limit(6)
            ).all()
            products = project_runtime_many(session, list(products), balances=False)
            matching_ids: dict[str, list[str]] = {}
            if products:
                matches = session.exec(
                    select(Product).where(
                        Product.customer_id == customer_id,
                        Product.product_number.in_([p.product_number for p in products]),
                    )
                ).all()
                for match in matches:
                    matching_ids.setdefault(match.product_number, []).append(match.product_id)
            candidates = [
                CardDiscoveryCandidate(
                    masked_number=_masked_card_number(product.product_number),
                    type=product.product_type,
                    currency=product.currency,
                    status=product.product_status,
                    lookup_product_number=_verified_card_lookup_number(product, matching_ids),
                )
                for product in products[:5]
            ]
            if not products:
                return CardDiscoveryResult(status="NO_MATCH", candidates=[])
            if len(products) > 1:
                return CardDiscoveryResult(
                    status="TOO_MANY_MATCHES" if len(products) > 5 else "AMBIGUOUS",
                    candidates=candidates,
                    truncated=len(products) > 5,
                )
            return CardDiscoveryResult(
                status="MATCH" if candidates[0].lookup_product_number else "LOOKUP_UNAVAILABLE",
                candidates=candidates,
            )

    def recharge_card(self, card_id: str, amount: float, customer_id: str) -> Card:
        self._authorize_card(card_id, customer_id)
        if amount <= 0:
            raise ValueError("Amount must be greater than zero")
        raise OperationUnavailable("Card recharge is unavailable for persisted products")

    def pay_with_card(self, card_id: str, amount: float, customer_id: str) -> Card:
        self._authorize_card(card_id, customer_id)
        if amount <= 0:
            raise ValueError("Amount must be greater than zero")
        raise OperationUnavailable("Card payment is unavailable for persisted products")

    def _authorize_card(self, card_id: str, customer_id: str) -> None:
        _require_identifier(card_id, "CardId")
        with self._session_factory() as session:
            _get_owned_product(session, card_id, customer_id, CARD_PRODUCT_TYPES)


card_service_singleton = CardService()
account_service_singleton = AccountService()


def _require_identifier(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} is empty or null")


def _get_owned_product(
    session: Session,
    product_id: str,
    customer_id: str,
    product_types: tuple[str, ...] | None = None,
) -> Product:
    statement = (
        select(Product)
        .where(Product.product_number == product_id)
        .where(Product.customer_id == customer_id)
    )
    if product_types is not None:
        statement = statement.where(Product.product_type.in_(product_types))
    products = session.exec(statement.limit(2)).all()
    if len(products) != 1:
        raise PermissionError("Account does not belong to the authenticated customer")
    return products[0]


def _verified_card_lookup_number(
    product: Product, matching_ids: dict[str, list[str]]
) -> str | None:
    number = product.product_number
    if not number or not re.fullmatch(r"[0-9]{12,19}", re.sub(r"[\s-]", "", number)):
        return None
    return number if matching_ids.get(number) == [product.product_id] else None
