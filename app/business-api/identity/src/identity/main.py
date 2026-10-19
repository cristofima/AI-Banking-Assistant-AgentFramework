"""Auth HTTP boundary. Synchronous handlers run database/hash work in worker threads."""
import logging
import re
from collections.abc import Iterator
from contextlib import asynccontextmanager
from hmac import compare_digest
from typing import Annotated, AsyncIterator

from banking_shared.tracing import create_meter_provider, create_tracer_provider
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import Field
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, create_engine
from starlette.concurrency import run_in_threadpool

from identity.models.schemas import (
    CustomerProfile, IntrospectionRequest, LoginRequest, LoginResponse,
    OperatorCreate, PasswordReset, UserProfile,
)
from identity.services.service import IdentityService, unauthorized
from identity.settings import Settings

logger = logging.getLogger("uvicorn.error.identity")
bearer = HTTPBearer(auto_error=False)


def token_value(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized()
    return credentials.credentials


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    config = settings or Settings()
    owned_engine = engine is None
    database = engine if engine is not None else create_engine(
        config.database_url.get_secret_value(), pool_pre_ping=True,
        connect_args={"connect_timeout": 3, "options": "-c statement_timeout=3000"},
    )
    service = IdentityService(config)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        yield
        await run_in_threadpool(service.oauth_signer.close)
        if owned_engine:
            await run_in_threadpool(database.dispose)

    app = FastAPI(title="Banking Identity", lifespan=lifespan)
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=create_tracer_provider(
            "banking-assistant-identity", config.applicationinsights_connection_string
        ),
        meter_provider=create_meter_provider(
            "banking-assistant-identity", config.applicationinsights_connection_string
        ),
    )

    def session() -> Iterator[Session]:
        with Session(database) as current:
            yield current

    @app.exception_handler(IntegrityError)
    async def conflict(request: Request, exc: IntegrityError) -> JSONResponse:
        if request.url.path in {"/oauth/authorize", "/oauth/token", "/internal/oauth/introspect"}:
            logger.error("Identity OAuth database request failed: category=integrity")
        return JSONResponse(status_code=409, content={"detail": {"code": "AUTH_CONFLICT"}})

    @app.exception_handler(SQLAlchemyError)
    async def unavailable(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        original = getattr(exc, "orig", None)
        sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
        safe_sqlstate = (
            sqlstate if isinstance(sqlstate, str) and re.fullmatch(r"[0-9A-Z]{5}", sqlstate)
            else "unknown"
        )
        logger.error(
            "Identity database request failed: exception=%s sqlstate=%s",
            type(exc).__name__, safe_sqlstate,
        )
        return JSONResponse(status_code=503, content={"detail": {"code": "AUTH_UNAVAILABLE"}})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        if request.url.path in {"/oauth/authorize", "/oauth/token", "/internal/oauth/introspect"}:
            logger.error("Identity OAuth request rejected: category=validation")
        return JSONResponse(status_code=422, content={"detail": {"code": "AUTH_INVALID_REQUEST"}})

    @app.post("/auth/login", response_model=LoginResponse, response_model_exclude_none=True)
    def login(body: LoginRequest, db: Annotated[Session, Depends(session)]) -> LoginResponse:
        return service.login(db, body.email, body.password.get_secret_value())

    @app.get("/auth/me", response_model=CustomerProfile | UserProfile,
             response_model_exclude_none=True)
    def me(db: Annotated[Session, Depends(session)],
           token: Annotated[str, Depends(token_value)]) -> UserProfile:
        return service.profile(db, service.authenticate(db, token))

    def internal(token: Annotated[str, Depends(token_value)]) -> None:
        if not compare_digest(token.encode(), config.auth_internal_secret.get_secret_value().encode()):
            raise unauthorized()

    @app.post("/internal/introspect", response_model=CustomerProfile | UserProfile,
              response_model_exclude_none=True, dependencies=[Depends(internal)])
    def introspect(body: IntrospectionRequest,
                   db: Annotated[Session, Depends(session)]) -> UserProfile:
        return service.profile(db, service.authenticate(db, body.token.get_secret_value()))

    @app.get("/admin/operators", response_model=list[UserProfile], response_model_exclude_none=True)
    def operators(db: Annotated[Session, Depends(session)],
                  token: Annotated[str, Depends(token_value)]) -> list[UserProfile]:
        return service.list_operators(db, token)

    @app.post("/admin/operators", response_model=UserProfile,
              response_model_exclude_none=True, status_code=201)
    def create_operator(body: OperatorCreate, db: Annotated[Session, Depends(session)],
                        token: Annotated[str, Depends(token_value)]) -> UserProfile:
        return service.create_operator(db, token, body)

    @app.post("/admin/operators/{user_id}/activate", response_model=UserProfile,
              response_model_exclude_none=True)
    def activate(user_id: str, db: Annotated[Session, Depends(session)],
                 token: Annotated[str, Depends(token_value)]) -> UserProfile:
        return service.update_operator(db, token, user_id, "activate")

    @app.post("/admin/operators/{user_id}/deactivate", response_model=UserProfile,
              response_model_exclude_none=True)
    def deactivate(user_id: str, db: Annotated[Session, Depends(session)],
                   token: Annotated[str, Depends(token_value)]) -> UserProfile:
        return service.update_operator(db, token, user_id, "deactivate")

    @app.post("/admin/operators/{user_id}/reset-password", response_model=UserProfile,
              response_model_exclude_none=True)
    def reset(user_id: str, body: PasswordReset, db: Annotated[Session, Depends(session)],
              token: Annotated[str, Depends(token_value)]) -> UserProfile:
        return service.update_operator(db, token, user_id, "reset_password",
                                       body.password.get_secret_value())

    @app.get("/admin/customers", response_model=list[CustomerProfile],
             response_model_exclude_none=True)
    def customers(db: Annotated[Session, Depends(session)],
                  token: Annotated[str, Depends(token_value)]) -> list[CustomerProfile]:
        return service.list_customers(db, token)

    @app.post("/admin/customers/{user_id}/activate", response_model=CustomerProfile,
              response_model_exclude_none=True)
    def activate_customer(
        user_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=128)],
        db: Annotated[Session, Depends(session)],
        token: Annotated[str, Depends(token_value)],
    ) -> CustomerProfile:
        return service.update_customer(db, token, user_id, "activate")

    @app.post("/admin/customers/{user_id}/deactivate", response_model=CustomerProfile,
              response_model_exclude_none=True)
    def deactivate_customer(
        user_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=128)],
        db: Annotated[Session, Depends(session)],
        token: Annotated[str, Depends(token_value)],
    ) -> CustomerProfile:
        return service.update_customer(db, token, user_id, "deactivate")

    from identity.oauth_routes import oauth_router
    app.include_router(oauth_router(config, service, session, token_value, internal))
    return app
