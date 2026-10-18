"""Disposable in-memory identity fixtures only."""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from identity.main import create_app
from identity.services.service import IdentityService
from identity.settings import Settings
from banking_shared import Customer, CustomerUser, Operator, Role, User, UserRole


@pytest.fixture
def settings() -> Settings:
    return Settings(database_url="sqlite://", jwt_secret_key="s" * 40,
                    jwt_issuer="synthetic", jwt_audience="synthetic",
                    auth_internal_secret="i" * 40)


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    database = create_engine("sqlite://", connect_args={"check_same_thread": False},
                             poolclass=StaticPool)
    database = database.execution_options(schema_translate_map={"support": None, "oauth": None})
    SQLModel.metadata.create_all(database)
    service = IdentityService(settings)
    with Session(database) as session:
        session.add_all([Role(name=name) for name in ("customer", "operator", "admin")])
        session.add(Customer(customer_id="synthetic-customer", email="customer@synthetic.invalid"))
        session.flush()
        for role in ("customer", "operator", "admin"):
            user = User(id=role, email=f"{role}@synthetic.invalid", locale="es", status="active",
                        password_hash=service.passwords.hash("synthetic-password"))
            session.add(user)
            session.flush()
            session.add(UserRole(user_id=role, role_id=session.exec(select(Role).where(Role.name == role)).one().id))
            if role == "customer":
                session.add(CustomerUser(user_id=role, customer_id="synthetic-customer"))
            if role == "operator":
                session.add(Operator(user_id=role))
        session.commit()
    yield database
    database.dispose()


@pytest.fixture
def client(settings: Settings, engine: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(settings, engine)) as current:
        yield current


@pytest.fixture
def tokens(client: TestClient) -> dict[str, str]:
    return {role: client.post("/auth/login", json={"email": f"{role}@synthetic.invalid",
                                                 "password": "synthetic-password"}).json()[
        "access_token"] for role in ("customer", "operator", "admin")}
