from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from banking_shared.identity_models import (
    CustomerUser,
    IdentityAudit,
    Operator,
    Role,
    User,
    UserRole,
)
from banking_data.models import Customer
from banking_data import seed_demo_users


@pytest.fixture
def database(monkeypatch: Any) -> Iterator[Any]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    engine = engine.execution_options(schema_translate_map={"support": None, "oauth": None})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Role(name="customer"))
        session.add(Role(name="operator"))
        session.add(Customer(customer_id="C001", email=" First@Example.com ", country="Colombia"))
        session.add(Customer(customer_id="C003", email="third@example.com", country="Brazil"))
        session.commit()

    @contextmanager
    def session_factory() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(seed_demo_users, "create_session", session_factory)
    yield engine
    engine.dispose()


def test_seedUsersSkipsUnknownCustomersAndUpsertsValidOnes(database: Any) -> None:
    result = seed_demo_users.seed_users(("C001", "UNKNOWN", "C003"), None, "demo-test-password")
    assert result.seeded_customer_ids == ("C001", "C003")
    assert result.skipped_customer_ids == ("UNKNOWN",)
    with Session(database) as session:
        users = session.exec(select(User).order_by(User.email)).all()
        assert [user.locale for user in users] == ["es", "pt"]
        assert users[0].email == "first@example.com"
        assert all(user.status == "active" for user in users)
        assert users[0].password_hash != users[1].password_hash
        assert all(seed_demo_users.password_hasher.verify("demo-test-password", user.password_hash) for user in users)
        original_id, original_created = users[0].id, users[0].created_at
        assert session.get(Role, session.get(UserRole, original_id).role_id).name == "customer"
        assert session.get(CustomerUser, original_id).customer_id == "C001"
    manifest = seed_demo_users.build_manifest(("C001",))
    assert manifest[0]["user_id"] == original_id
    seed_demo_users.seed_users(("C001",), "en", "replacement-password")
    with Session(database) as session:
        user = session.get(User, original_id)
        assert user.created_at == original_created
        assert user.identity_version == 2
        assert user.updated_at >= user.created_at
        assert user.locale == "en"
        assert seed_demo_users.password_hasher.verify("replacement-password", user.password_hash)
        assert len(session.exec(select(IdentityAudit)).all()) == 3


def test_seedUsersWithOnlyUnknownCustomersCommitsNoWrites(database: Any) -> None:
    result = seed_demo_users.seed_users(("UNKNOWN",), "en", "unused-password")
    assert result.seeded_customer_ids == ()
    assert result.skipped_customer_ids == ("UNKNOWN",)
    with Session(database) as session:
        assert session.exec(select(User)).all() == []
        assert session.exec(select(IdentityAudit)).all() == []


def test_collisionRollsBackEntireSeedWithoutPromotingStaff(database: Any) -> None:
    with Session(database) as session:
        staff = User(email="third@example.com", password_hash="existing-hash", locale="en")
        session.add(staff)
        session.flush()
        session.add(UserRole(user_id=staff.id, role_id=session.exec(select(Role).where(Role.name == "operator")).one().id))
        staff_id = staff.id
        session.commit()
    with pytest.raises(ValueError, match="another identity"):
        seed_demo_users.seed_users(("C001", "C003"), None, "demo-test-password")
    with Session(database) as session:
        assert len(session.exec(select(User)).all()) == 1
        assert session.get(Role, session.get(UserRole, staff_id).role_id).name == "operator"
        assert session.exec(select(CustomerUser)).all() == []
        assert session.exec(select(IdentityAudit)).all() == []


def test_refreshActivatesOnlySelectedCustomersAndRejectsBrokenAssociation(database: Any) -> None:
    seed_demo_users.seed_users(("C001", "C003"), None, "demo-test-password")
    with Session(database) as session:
        users = session.exec(select(User).order_by(User.email)).all()
        for user in users:
            user.status = "inactive"
            session.add(user)
        user_id, untouched_id = users[0].id, users[1].id
        created_at = users[0].created_at
        session.commit()
    seed_demo_users.seed_users(("C001",), None, "replacement-password")
    with Session(database) as session:
        user = session.get(User, user_id)
        assert user.status == "active"
        assert user.identity_version == 2
        assert user.created_at == created_at
        assert user.updated_at >= user.created_at
        assert session.get(User, untouched_id).status == "inactive"
        assert session.get(User, untouched_id).identity_version == 1
        assert len(session.exec(select(IdentityAudit).where(IdentityAudit.target_id == user_id)).all()) == 2
        session.delete(session.get(UserRole, user_id))
        session.commit()
    assert seed_demo_users.build_manifest(("C001",))[0]["status"] == "active"
    with pytest.raises(ValueError, match="Invalid customer"):
        seed_demo_users.seed_users(("C001",), None, "replacement-password")


def test_customerWithOperatorAssociationIsRejected(database: Any) -> None:
    seed_demo_users.seed_users(("C001",), None, "demo-test-password")
    with Session(database) as session:
        user = session.exec(select(User)).one()
        session.add(Operator(user_id=user.id))
        user_id = user.id
        session.commit()
    with pytest.raises(ValueError, match="Invalid customer"):
        seed_demo_users.seed_users(("C001",), None, "replacement-password")
    with Session(database) as session:
        assert session.get(User, user_id).identity_version == 1
        assert len(session.exec(select(IdentityAudit)).all()) == 1


@pytest.mark.parametrize("refresh", [False, True], ids=["create", "refresh"])
@pytest.mark.parametrize(
    ("first_name", "last_name", "expected_name"),
    [
        ("  Ana  María ", "  Pérez  ", "Ana  María Pérez"),
        ("Ana", None, "Ana"),
        (None, "Pérez", "Pérez"),
        (" ", None, None),
        (None, None, None),
        ("", "", None),
        ("A" * 50, "B" * 50, "A" * 50 + " " + "B" * 50),
    ],
)
def test_seed_users_migrates_customer_name(
    database: Any,
    refresh: bool,
    first_name: str | None,
    last_name: str | None,
    expected_name: str | None,
) -> None:
    if refresh:
        seed_demo_users.seed_users(("C001", "C003"), None, "demo-test-password")
        with Session(database) as session:
            for user in session.exec(select(User)).all():
                user.name = "Previous name"
                session.add(user)
            session.commit()
    with Session(database) as session:
        customer = session.get(Customer, "C001")
        assert customer is not None
        customer.first_name = first_name
        customer.last_name = last_name
        session.add(customer)
        session.commit()

    seed_demo_users.seed_users(("C001",), None, "demo-test-password")

    with Session(database) as session:
        user = session.exec(select(User).where(User.email == "first@example.com")).one()
        assert user.name == expected_name
        if refresh:
            untouched = session.exec(
                select(User).where(User.email == "third@example.com")
            ).one()
            assert untouched.name == "Previous name"


def test_localeForCustomerUsesExplicitOverride() -> None:
    customer = Customer(
        customer_id="C001",
        email="customer@example.com",
        country="Brazil",
    )

    assert seed_demo_users.locale_for_customer(customer, "es") == "es"


@pytest.fixture
def isolated_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEMO_USER_PASSWORD", raising=False)
    monkeypatch.delenv("ADMIN_BOOTSTRAP_PASSWORD", raising=False)


def test_shared_password_keeps_existing_trim_contract(
    isolated_credentials: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEMO_USER_PASSWORD", " customer-synthetic-password ")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "administrator-synthetic-password")
    assert seed_demo_users.load_shared_password() == "customer-synthetic-password"


@pytest.mark.parametrize("value", [None, "", " "])
def test_missing_customer_password_has_no_default(
    value: str | None, isolated_credentials: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if value is not None:
        monkeypatch.setenv("DEMO_USER_PASSWORD", value)
    with pytest.raises(ValueError, match="must be set"):
        seed_demo_users.load_shared_password()


def test_password_reuse_fails_before_customer_database_access(
    isolated_credentials: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_session() -> None:
        pytest.fail("Credential validation must precede database access")

    monkeypatch.setattr(seed_demo_users, "create_session", unexpected_session)
    monkeypatch.setenv("DEMO_USER_PASSWORD", " shared-synthetic-password ")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "shared-synthetic-password")
    with pytest.raises(ValueError, match="must differ"):
        seed_demo_users.load_shared_password()
    with pytest.raises(ValueError, match="must differ"):
        seed_demo_users.seed_users(("C001",), "en", "shared-synthetic-password")


def test_missing_migrated_role_rejects_customer_provisioning(database: Any) -> None:
    with Session(database) as session:
        role = session.exec(select(Role).where(Role.name == "customer")).one()
        session.delete(role)
        session.commit()
    with pytest.raises(ValueError, match="Apply identity migrations"):
        seed_demo_users.seed_users(("C001",), None, "customer-synthetic-password")
    with Session(database) as session:
        assert session.exec(select(User)).all() == []
        assert session.exec(select(CustomerUser)).all() == []