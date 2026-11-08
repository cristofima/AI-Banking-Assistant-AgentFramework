"""Ownership and storage mapping checks for Account services."""

import asyncio
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from banking_shared.models import Customer, Product, SQLModel
from banking_shared.product_types import PRODUCT_TYPE_LABELS
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp import Client
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from banking_account import mcp_tools
from banking_account.routers import products as routers
from banking_account.auth.jwt_identity import get_jwt_customer_id
from banking_account.services.products import AccountService, CardService, UserService


@pytest.fixture
def session_factory() -> Callable[[], Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    engine = engine.execution_options(schema_translate_map={"support": None})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Customer(
                    customer_id="customer-owned",
                    email="owner@example.com",
                    first_name="Ada",
                    last_name="Lovelace",
                ),
                Customer(
                    customer_id="customer-foreign",
                    email="foreign@example.com",
                    first_name="Grace",
                    last_name="Hopper",
                ),
                Product(
                    product_id="account-owned",
                    customer_id="customer-owned",
                    product_type="Checking Account",
                    product_number="ACCOUNT-SOURCE-NUMBER",
                    currency="USD",
                    current_balance=Decimal("1250.5000"),
                    opening_date=date(2024, 1, 2),
                    product_status="active",
                ),
                Product(
                    product_id="card-owned",
                    customer_id="customer-owned",
                    product_type="Credit Card",
                    product_number="4111111111111111",
                    currency="USD",
                    current_balance=Decimal("210.2500"),
                    credit_limit=Decimal("3000.0000"),
                    product_status="active",
                ),
                Product(
                    product_id="account-foreign",
                    customer_id="customer-foreign",
                    product_type="Savings Account",
                    currency="USD",
                ),
                Product(
                    product_id="card-foreign",
                    customer_id="customer-foreign",
                    product_type="Debit Card",
                    currency="USD",
                ),
            ]
        )
        session.commit()
    return lambda: Session(engine)


def test_list_accounts_and_cards_are_scoped_to_owner(
    session_factory: Callable[[], Session],
) -> None:
    account_service = AccountService(session_factory)
    card_service = CardService(session_factory)

    owned_accounts = account_service.list_accounts("customer-owned")
    owned_cards = card_service.list_cards("customer-owned")
    foreign_accounts = account_service.list_accounts("customer-foreign")

    assert [account.number for account in owned_accounts] == ["ACCOUNT-SOURCE-NUMBER"]
    assert [account.product_id for account in owned_accounts] == ["account-owned"]
    assert owned_accounts[0].type == "Checking Account"
    assert owned_accounts[0].balance == "1250.5000"
    assert len(owned_cards) == 1
    assert owned_cards[0].number == "4111 **** **** 1111"
    assert owned_cards[0].product_id == "card-owned"
    assert owned_cards[0].credit_limit == "3000.0000"
    assert len(foreign_accounts) == 1
    assert foreign_accounts[0].product_id == "account-foreign"
    assert foreign_accounts[0].number is None


def test_list_accounts_rest_endpoint_requires_jwt_identity(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-key-with-at-least-32-bytes")
    monkeypatch.setattr(routers, "account_service_singleton", AccountService(session_factory))
    monkeypatch.setattr(routers, "card_service_singleton", CardService(session_factory))
    app = FastAPI()
    app.include_router(routers.router, prefix="/api")

    with TestClient(app) as client:
        denied = client.get("/api/accounts")
        app.dependency_overrides[get_jwt_customer_id] = lambda: "customer-owned"
        allowed = client.get("/api/accounts")
        cards = client.get("/api/cards")

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert [item["number"] for item in allowed.json()] == ["ACCOUNT-SOURCE-NUMBER"]
    assert [item["product_id"] for item in allowed.json()] == ["account-owned"]
    assert "account-foreign" not in allowed.text
    assert cards.status_code == 200
    assert [item["number"] for item in cards.json()] == ["4111 **** **** 1111"]
    assert [item["product_id"] for item in cards.json()] == ["card-owned"]
    assert "4111111111111111" not in cards.text


def test_owned_account_resources_are_mapped_from_storage(
    session_factory: Callable[[], Session],
) -> None:
    account_service = AccountService(session_factory)
    card_service = CardService(session_factory)
    user_service = UserService(session_factory)

    account = account_service.get_account_details("ACCOUNT-SOURCE-NUMBER", "customer-owned")
    cards = card_service.get_credit_cards("ACCOUNT-SOURCE-NUMBER", "customer-owned")
    user_accounts = user_service.get_accounts_by_user_name(
        "owner@example.com",
        "customer-owned",
    )

    assert account is not None
    assert account.accountNumber == "ACCOUNT-SOURCE-NUMBER"
    assert user_accounts[0].accountNumber == "ACCOUNT-SOURCE-NUMBER"
    assert account.accountHolderFullName == "Ada Lovelace"
    assert account.balance == "1250.5000"
    assert [method.number for method in account.paymentMethods or []] == ["4111 **** **** 1111"]
    assert len(cards) == 1
    assert "account-owned" not in account.model_dump_json()
    assert "card-owned" not in account.model_dump_json()
    assert cards[0].number == "4111 **** **** 1111"
    assert cards[0].type == "credit"
    assert cards[0].name == "Credit Card"
    assert account.paymentMethods[0].type == "credit"
    assert "4111111111111111" not in cards[0].model_dump_json()
    assert cards[0].circuit is None
    assert len(user_accounts) == 1
    with pytest.raises(RuntimeError, match="unavailable"):
        account_service.get_registered_beneficiary(
            "ACCOUNT-SOURCE-NUMBER",
            "customer-owned",
        )


def test_multiple_accounts_share_customer_card_catalog(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        session.add(Product(
            product_id="second-owned", customer_id="customer-owned",
            product_type="Savings Account", product_number="SECOND-ACCOUNT", currency="USD",
        ))
        session.commit()

    service = CardService(session_factory)
    first = service.get_credit_cards("ACCOUNT-SOURCE-NUMBER", "customer-owned")
    second = service.get_credit_cards("SECOND-ACCOUNT", "customer-owned")

    assert [card.model_dump() for card in first] == [card.model_dump() for card in second]
    assert [card.number for card in second] == ["4111 **** **** 1111"]


@pytest.mark.parametrize("message", ["unavailable", "not found", "changed domain wording"])
@pytest.mark.parametrize("status_code", [400, 404, 503])
def test_domain_http_status_is_independent_of_prose(message: str, status_code: int) -> None:
    from banking_account.services.errors import AccountOperationError

    error = routers._to_runtime_http_error(AccountOperationError(message, status_code=status_code))

    assert error.status_code == status_code
    assert error.detail == message
    assert routers._to_runtime_http_error(RuntimeError(message)).status_code == 400


def test_foreign_product_numbers_are_denied(session_factory: Callable[[], Session]) -> None:
    with session_factory() as session:
        for product_id in ("account-foreign", "card-foreign"):
            product = session.get(Product, product_id)
            assert product is not None
            product.product_number = f"NUMBER-{product_id}"
            session.add(product)
        session.commit()

    with pytest.raises(PermissionError, match="authenticated customer"):
        AccountService(session_factory).get_account_details(
            "NUMBER-account-foreign", "customer-owned",
        )
    with pytest.raises(PermissionError, match="authenticated customer"):
        CardService(session_factory).get_card_details("NUMBER-card-foreign", "customer-owned")
    debit = CardService(session_factory).get_card_details(
        "NUMBER-card-foreign", "customer-foreign",
    )
    assert debit is not None
    assert debit.type == "debit"
    assert debit.name == "Debit Card"


@pytest.mark.parametrize(("canonical", "legacy"), PRODUCT_TYPE_LABELS.items())
def test_payment_method_type_is_normalized(
    session_factory: Callable[[], Session], canonical: str, legacy: str,
) -> None:
    for label in (canonical, legacy):
        with session_factory() as session:
            product = session.get(Product, "card-owned")
            assert product is not None
            product.product_type = label
            session.add(product)
            session.commit()

        method = AccountService(session_factory).get_payment_method_details(
            "4111111111111111", "customer-owned",
        )
        assert method is not None
        assert method.type == {"Credit Card": "credit", "Debit Card": "debit"}.get(
            canonical, canonical,
        )


@pytest.mark.parametrize(
    ("resource_id", "service_method"),
    [
        ("account-foreign", "account"),
        ("account-owned", "account"),
        ("card-foreign", "card"),
        ("missing-product", "account"),
    ],
)
def test_foreign_and_missing_resources_are_indistinguishable(
    session_factory: Callable[[], Session],
    resource_id: str,
    service_method: str,
) -> None:
    if service_method == "card":
        operation = lambda: CardService(session_factory).get_card_details(
            resource_id,
            "customer-owned",
        )
    else:
        operation = lambda: AccountService(session_factory).get_account_details(
            resource_id,
            "customer-owned",
        )

    with pytest.raises(PermissionError, match="authenticated customer"):
        operation()


@pytest.mark.parametrize("number", [None, "", "   ", "1234", " 1234 ", "1234567890"])
def test_account_number_is_full_or_unavailable(
    session_factory: Callable[[], Session], number: str | None
) -> None:
    with session_factory() as session:
        product = session.get(Product, "account-owned")
        assert product is not None
        product.product_number = number
        session.add(product)
        session.commit()

    account = UserService(session_factory).get_accounts_by_user_name(
        "owner@example.com", "customer-owned"
    )[0]

    assert account is not None
    assert account.accountNumber == ((number.strip() or None) if number else None)


def test_duplicate_owned_numbers_are_denied(session_factory: Callable[[], Session]) -> None:
    with session_factory() as session:
        session.add(Product(
            product_id="duplicate", customer_id="customer-owned",
            product_type="Savings Account", currency="USD",
            product_number="ACCOUNT-SOURCE-NUMBER",
        ))
        session.commit()

    with pytest.raises(PermissionError, match="authenticated customer"):
        AccountService(session_factory).get_account_details(
            "ACCOUNT-SOURCE-NUMBER", "customer-owned",
        )


def test_mcp_number_lookup_returns_full_account_without_primary_keys(
    session_factory: Callable[[], Session], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_tools, "account_service", AccountService(session_factory))
    monkeypatch.setattr(mcp_tools, "get_customer_id", lambda headers: "customer-owned")

    async def call_account_tool() -> None:
        async with Client(mcp_tools.mcp) as client:
            result = await client.call_tool(
                "getAccountDetails", {"product_number": "ACCOUNT-SOURCE-NUMBER"},
            )
        assert not result.is_error
        text = " ".join(content.text for content in result.content)
        assert "ACCOUNT-SOURCE-NUMBER" in text
        assert "account-owned" not in text
        assert "card-owned" not in text
        assert "4111111111111111" not in text

    asyncio.run(call_account_tool())


@pytest.mark.parametrize("customer_id", ["customer-owned", "customer-foreign"])
def test_account_list_without_username_uses_only_verified_owner(
    session_factory: Callable[[], Session], customer_id: str,
) -> None:
    accounts = UserService(session_factory).get_accounts_by_user_name(None, customer_id)
    assert len(accounts) == 1
    assert accounts[0].accountNumber == (
        "ACCOUNT-SOURCE-NUMBER" if customer_id == "customer-owned" else None
    )


@pytest.mark.parametrize("customer_id", ["missing-customer", ""])
def test_account_list_without_username_rejects_invalid_owner(
    session_factory: Callable[[], Session], customer_id: str,
) -> None:
    with pytest.raises((PermissionError, ValueError)):
        UserService(session_factory).get_accounts_by_user_name(None, customer_id)


@pytest.mark.parametrize("user_name", ["", "   ", "foreign@example.com"])
def test_optional_legacy_username_cannot_override_owner(
    session_factory: Callable[[], Session], user_name: str,
) -> None:
    with pytest.raises((PermissionError, ValueError)):
        UserService(session_factory).get_accounts_by_user_name(user_name, "customer-owned")


async def test_mcp_account_list_schema_and_no_username_read(
    session_factory: Callable[[], Session], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_tools, "user_service", UserService(session_factory))
    monkeypatch.setattr(mcp_tools, "get_customer_id", lambda headers: "customer-owned")
    async with Client(mcp_tools.mcp) as client:
        tools = await client.list_tools()
        tool = next(tool for tool in tools if tool.name == "getAccountsByUserName")
        assert "userName" not in tool.inputSchema.get("required", [])
        result = await client.call_tool("getAccountsByUserName", {})
        denied = await client.call_tool(
            "getAccountsByUserName", {"userName": "foreign@example.com"},
            raise_on_error=False,
        )
    assert not result.is_error
    text = " ".join(content.text for content in result.content)
    assert "ACCOUNT-SOURCE-NUMBER" in text
    assert "Grace" not in text
    assert denied.is_error


def test_account_list_without_username_returns_empty_for_customer_without_accounts(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        session.add(Customer(customer_id="customer-empty", email="empty@example.com"))
        session.commit()
    assert UserService(session_factory).get_accounts_by_user_name(None, "customer-empty") == []


def test_optional_legacy_username_preserves_normalized_matching(
    session_factory: Callable[[], Session],
) -> None:
    accounts = UserService(session_factory).get_accounts_by_user_name(
        " OWNER@EXAMPLE.COM ", "customer-owned",
    )
    assert len(accounts) == 1
    assert accounts[0].accountNumber == "ACCOUNT-SOURCE-NUMBER"


async def test_mcp_account_list_denies_before_service_when_identity_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny_identity(headers: object) -> str:
        raise PermissionError("Authentication required")

    def unexpected_read(user_name: str | None, customer_id: str) -> list[object]:
        pytest.fail("Service must not run after identity verification fails")

    monkeypatch.setattr(mcp_tools, "get_customer_id", deny_identity)
    monkeypatch.setattr(mcp_tools.user_service, "get_accounts_by_user_name", unexpected_read)
    async with Client(mcp_tools.mcp) as client:
        result = await client.call_tool("getAccountsByUserName", {}, raise_on_error=False)
    assert result.is_error
    assert result.structured_content is None


def test_foreign_user_name_is_denied(session_factory: Callable[[], Session]) -> None:
    with pytest.raises(PermissionError, match="authenticated customer"):
        UserService(session_factory).get_accounts_by_user_name(
            "foreign@example.com",
            "customer-owned",
        )


@pytest.mark.parametrize("resource_id", ["account-foreign", "missing-product"])
def test_mcp_account_denial_preserves_error_without_resource_data(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
    resource_id: str,
) -> None:
    monkeypatch.setattr(mcp_tools, "account_service", AccountService(session_factory))
    monkeypatch.setattr(mcp_tools, "get_customer_id", lambda headers: "customer-owned")

    async def call_account_tool() -> None:
        async with Client(mcp_tools.mcp) as client:
            result = await client.call_tool(
                "getAccountDetails", {"product_number": resource_id}, raise_on_error=False,
            )
        assert result.is_error is True
        assert result.structured_content is None
        assert len(result.content) == 1
        assert result.content[0].text == (
            "Error calling tool 'getAccountDetails': "
            "Account does not belong to the authenticated customer"
        )

    asyncio.run(call_account_tool())


@pytest.mark.parametrize("resource_id", ["account-foreign", "missing-product"])
def test_rest_account_cards_denial_returns_403(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
    resource_id: str,
) -> None:
    monkeypatch.setattr(routers, "card_service_singleton", CardService(session_factory))
    app = FastAPI()
    app.include_router(routers.router, prefix="/api")
    app.dependency_overrides[get_jwt_customer_id] = lambda: "customer-owned"

    with TestClient(app) as client:
        response = client.get(f"/api/accounts/{resource_id}/cards")

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Account does not belong to the authenticated customer",
    }
