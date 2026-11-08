"""Synthetic ownership, masking and MCP suffix-discovery regressions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable, Iterator

import pytest
from banking_shared.models import Customer, Product, SQLModel
from fastmcp import Client
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from banking_shared import internal_identity
from banking_account import mcp_tools
from banking_account.services.products import CardService


@pytest.fixture
def session_factory() -> Iterator[Callable[[], Session]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    engine = engine.execution_options(schema_translate_map={"support": None})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            Customer(customer_id="owner", email="owner@example.test"),
            Customer(customer_id="foreign", email="foreign@example.test"),
        ])
        session.commit()
    yield lambda: Session(engine)
    engine.dispose()


def _add_card(
    factory: Callable[[], Session],
    number: str | None = "4111111111117036",
    *,
    product_id: str = "card",
    customer_id: str = "owner",
    product_type: str = "Debit Card",
) -> None:
    with factory() as session:
        session.add(Product(
            product_id=product_id, customer_id=customer_id, product_number=number,
            product_type=product_type, currency="USD", product_status="active",
        ))
        session.commit()


@pytest.mark.parametrize("product_type", ["Debit Card", "Credit Card"])
def test_discovery_returns_owned_masked_card_and_exact_internal_key(
    session_factory: Callable[[], Session], product_type: str
) -> None:
    _add_card(session_factory, product_type=product_type)
    _add_card(session_factory, customer_id="foreign", product_id="foreign-card")

    result = CardService(session_factory).discover_cards_by_suffix("7036", "owner")

    assert result.model_dump() == {
        "status": "MATCH", "truncated": False,
        "candidates": [{
            "masked_number": "4111 **** **** 7036", "type": product_type,
            "currency": "USD", "status": "active",
            "lookup_product_number": "4111111111117036",
        }],
    }
    assert CardService(session_factory).list_cards("owner")[0].number == "4111 **** **** 7036"


@pytest.mark.parametrize("customer_id", ["", " "])
def test_discovery_rejects_missing_customer_identity(
    session_factory: Callable[[], Session], customer_id: str,
) -> None:
    with pytest.raises(ValueError, match="CustomerId is empty or null"):
        CardService(session_factory).discover_cards_by_suffix("7036", customer_id)


def test_discovery_projects_current_card_status(
    session_factory: Callable[[], Session], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add_card(session_factory)

    def blocked_projection(
        session: Session, products: list[Product], *, balances: bool = True,
    ) -> list[Product]:
        assert balances is False
        return [product.model_copy(update={"product_status": "Blocked"}) for product in products]

    monkeypatch.setattr("banking_account.services.products.project_runtime_many", blocked_projection)
    result = CardService(session_factory).discover_cards_by_suffix("7036", "owner")
    assert result.candidates[0].status == "Blocked"
    assert result.candidates[0].lookup_product_number == "4111111111117036"


@pytest.mark.parametrize("product_type", ["Savings Account", "Checking Account", "Tarjeta Débito"])
def test_discovery_excludes_noncanonical_cards_and_foreign_matches(
    session_factory: Callable[[], Session], product_type: str
) -> None:
    _add_card(session_factory, product_type=product_type)
    _add_card(session_factory, customer_id="foreign", product_id="foreign-card")

    result = CardService(session_factory).discover_cards_by_suffix("7036", "owner")

    assert result.model_dump() == {"status": "NO_MATCH", "candidates": [], "truncated": False}


@pytest.mark.parametrize("suffix", ["", "36", " 7036", "7036 ", "****7036", "７０３６", "703%"])
def test_discovery_rejects_invalid_suffix(
    session_factory: Callable[[], Session], suffix: str
) -> None:
    with pytest.raises(ValueError, match="exactly four ASCII digits"):
        CardService(session_factory).discover_cards_by_suffix(suffix, "owner")


@pytest.mark.parametrize("number", [
    "**** 7036", "4111 **** **** 7036", "bad7036", "41117036",
    "41111111111111117036", "４１１１１１１１１１１１7036",
])
def test_discovery_does_not_reconstruct_unusable_number(
    session_factory: Callable[[], Session], number: str
) -> None:
    _add_card(session_factory, number)

    result = CardService(session_factory).discover_cards_by_suffix("7036", "owner")

    assert result.status == "LOOKUP_UNAVAILABLE"
    assert result.candidates[0].lookup_product_number is None
    assert result.candidates[0].masked_number == (
        "**** 7036" if number == "**** 7036" else
        "4111 **** **** 7036" if number == "4111 **** **** 7036" else None
    )


@pytest.mark.parametrize("number", [None, "", "4111111111111234"])
def test_discovery_missing_or_other_suffix_is_not_a_match(
    session_factory: Callable[[], Session], number: str | None
) -> None:
    _add_card(session_factory, number)
    assert CardService(session_factory).discover_cards_by_suffix("7036", "owner").status == "NO_MATCH"


@pytest.mark.parametrize("number", [
    " 4111-1111-1111-7036 ", "\t4111 1111 1111 7036\r\n", "4111111111117036-",
])
def test_discovery_preserves_formatted_persisted_lookup_key(
    session_factory: Callable[[], Session], number: str,
) -> None:
    _add_card(session_factory, number)
    result = CardService(session_factory).discover_cards_by_suffix("7036", "owner")
    assert result.status == "MATCH"
    assert result.candidates[0].lookup_product_number == number
    assert result.candidates[0].masked_number == "4111 **** **** 7036"


@pytest.mark.parametrize("count", [2, 5, 6, 9])
def test_discovery_reports_ambiguity_without_claiming_truncated_uniqueness(
    session_factory: Callable[[], Session], count: int
) -> None:
    for index in range(count):
        _add_card(session_factory, f"41111111{index:04d}7036", product_id=f"card-{index}")
    result = CardService(session_factory).discover_cards_by_suffix("7036", "owner")
    assert result.status == ("AMBIGUOUS" if count <= 5 else "TOO_MANY_MATCHES")
    assert result.truncated is (count > 5)
    assert len(result.candidates) == min(count, 5)
    assert all(candidate.lookup_product_number for candidate in result.candidates)


@pytest.mark.parametrize("product_type", ["Debit Card", "Checking Account"])
def test_duplicate_lookup_numbers_never_return_usable_keys(
    session_factory: Callable[[], Session], product_type: str
) -> None:
    _add_card(session_factory)
    _add_card(session_factory, product_id="duplicate", product_type=product_type)
    result = CardService(session_factory).discover_cards_by_suffix("7036", "owner")
    assert result.status == ("AMBIGUOUS" if product_type == "Debit Card" else "LOOKUP_UNAVAILABLE")
    assert all(candidate.lookup_product_number is None for candidate in result.candidates)


@pytest.mark.asyncio
async def test_mcp_discovery_schema_and_invocation(
    session_factory: Callable[[], Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_card(session_factory)
    monkeypatch.setattr(mcp_tools, "card_service_singleton", CardService(session_factory))
    monkeypatch.setattr(mcp_tools, "get_customer_id", lambda headers: "owner")
    async with Client(mcp_tools.mcp) as client:
        tools = await client.list_tools()
        tool = next(tool for tool in tools if tool.name == "discoverCardsBySuffix")
        assert set(tool.inputSchema["properties"]) == {"suffix"}
        assert "never user-facing prose" in tool.description
        result = await client.call_tool("discoverCardsBySuffix", {"suffix": "7036"})
    assert not result.is_error
    assert result.data.status == "MATCH"
    assert result.data.candidates[0].lookup_product_number == "4111111111117036"


@pytest.mark.asyncio
async def test_discovery_authenticates_before_accessing_storage(
    session_factory: Callable[[], Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_card(session_factory)
    monkeypatch.setattr(mcp_tools, "card_service_singleton", CardService(session_factory))
    monkeypatch.setenv("INTERNAL_IDENTITY_SECRET", "synthetic-test-signing-key")
    monkeypatch.setattr(internal_identity.time, "time", lambda: 1000)
    payload = base64.urlsafe_b64encode(json.dumps({
        "customer_id": "owner", "sub": "synthetic-owner", "exp": 1060,
    }).encode()).rstrip(b"=").decode()
    signature = hmac.new(
        b"synthetic-test-signing-key", payload.encode(), hashlib.sha256
    ).hexdigest()
    result = await mcp_tools.discover_cards_by_suffix(
        "7036", {"authorization": f"Bearer v1.{payload}.{signature}"}
    )
    assert result.status == "MATCH"
    async with Client(mcp_tools.mcp) as client:
        denied = await client.call_tool(
            "discoverCardsBySuffix", {"suffix": "7036"}, raise_on_error=False
        )
    assert denied.is_error
    assert denied.structured_content is None
    with pytest.raises(PermissionError, match="Invalid"):
        await mcp_tools.discover_cards_by_suffix("7036", {"authorization": "Bearer invalid"})
