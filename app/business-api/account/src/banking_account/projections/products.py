from __future__ import annotations

import re
from decimal import Decimal

from banking_shared.models import Customer, Product
from banking_shared.product_types import card_type, normalize_product_type
from banking_account.models.products import (
    Account,
    AccountSummary,
    Card,
    CardSummary,
    PaymentMethodSummary,
)

def _to_account(product: Product, customer: Customer, cards: list[Product]) -> Account:
    full_name = " ".join(
        part for part in (customer.first_name, customer.last_name) if part
    )
    return Account(
        accountNumber=(
            product.product_number.strip()
            if product.product_number and product.product_number.strip()
            else None
        ),
        userName=customer.email,
        accountHolderFullName=full_name,
        currency=product.currency,
        activationDate=_date_value(product.opening_date),
        balance=_decimal_text(product.current_balance),
        paymentMethods=[_to_payment_method_summary(card) for card in cards] or None,
    )


def _masked_card_number(number: str | None) -> str | None:
    compact = re.sub(r"[\s-]", "", number or "")
    if re.fullmatch(r"[0-9]{4}\*+[0-9]{4}", compact):
        return f"{compact[:4]} **** **** {compact[-4:]}"
    if re.fullmatch(r"\*+[0-9]{4}", compact):
        return f"**** {compact[-4:]}"
    if not re.fullmatch(r"[0-9]{12,19}", compact):
        return None
    return f"{compact[:4]} **** **** {compact[-4:]}"


def _to_payment_method_summary(product: Product) -> PaymentMethodSummary:
    return PaymentMethodSummary(
        number=_masked_card_number(product.product_number),
        type=card_type(product.product_type),
        activationDate=_date_value(product.opening_date),
        expirationDate=_date_value(product.expiration_date),
    )


def _to_account_summary(product: Product) -> AccountSummary:
    return AccountSummary(
        product_id=product.product_id,
        type=normalize_product_type(product.product_type),
        status=product.product_status,
        opened=_date_value(product.opening_date),
        number=product.product_number,
        currency=product.currency,
        balance=_decimal_text(product.current_balance),
    )


def _to_card_summary(product: Product) -> CardSummary:
    return CardSummary(
        product_id=product.product_id,
        type=normalize_product_type(product.product_type),
        status=product.product_status,
        opened=_date_value(product.opening_date),
        expires=_date_value(product.expiration_date),
        number=_masked_card_number(product.product_number),
        currency=product.currency,
        balance=_decimal_text(product.current_balance),
        credit_limit=_decimal_text(product.credit_limit),
    )


def _to_card(product: Product) -> Card:
    return Card(
        type=card_type(product.product_type),
        number=_masked_card_number(product.product_number),
        name=normalize_product_type(product.product_type),
        activationDate=_date_value(product.opening_date),
        expirationDate=_date_value(product.expiration_date),
        balance=_decimal_float(product.current_balance),
        limit=_decimal_float(product.credit_limit),
        status=product.product_status,
    )


def _date_value(value: object | None) -> str | None:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else None


def _decimal_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None
