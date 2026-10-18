"""Application entry point for the browser-facing Responses BFF."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
from banking_shared.tracing import create_meter_provider
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

from bff.routers.auth import router as auth_router
from bff.routers.oauth import router as oauth_router
from bff.clients.credentials import create_azure_credential
from bff.identity.responses import AsyncCredential
from bff.identity.oauth_client import OAuthClient, OAuthClientConfig, logger as oauth_logger
from bff.routers.responses import router as responses_router
from bff.config.settings import Settings
from bff.config.tracing import configure_tracing
from bff.routers.admin import customer_router, router as admin_router


CredentialFactory = Callable[[Settings], AsyncCredential]


def create_app(
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    credential_factory: CredentialFactory = create_azure_credential,
    auth_transport: httpx.AsyncBaseTransport | None = None,
    oauth_config: OAuthClientConfig | None = None,
) -> FastAPI:
    """Create the BFF with application-lifetime upstream resources."""
    app_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = app_settings
        app.state.oauth_client = OAuthClient(oauth_config or OAuthClientConfig())
        app.state.auth_client = httpx.AsyncClient(
            base_url=app_settings.auth_users_endpoint,
            timeout=3.0, transport=auth_transport, follow_redirects=False,
        )
        app.state.http_client = httpx.AsyncClient(timeout=None, transport=transport)
        for client in (app.state.auth_client, app.state.http_client):
            HTTPXClientInstrumentor.instrument_client(
                client, tracer_provider=tracer_provider, meter_provider=meter_provider
            )
        app.state.azure_credential = None
        try:
            if app_settings.responses_upstream_mode == "foundry":
                app.state.azure_credential = credential_factory(app_settings)
            yield
        finally:
            await app.state.auth_client.aclose()
            await app.state.http_client.aclose()
            if app.state.azure_credential is not None:
                await app.state.azure_credential.close()

    app = FastAPI(title=app_settings.app_name, lifespan=lifespan)
    tracer_provider = configure_tracing(app, app_settings.applicationinsights_connection_string)
    meter_provider = create_meter_provider(
        "banking-assistant-responses-bff", app_settings.applicationinsights_connection_string
    )
    @app.middleware("http")
    async def consent_no_store(
        request: Request, call_next: Callable[[Request], object],
    ) -> Response:
        response = await call_next(request)
        if request.url.path.startswith("/oauth/"):
            if response.status_code >= 400:
                oauth_logger.error("BFF OAuth request failed: status=%s", response.status_code)
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    app.include_router(auth_router, tags=["auth"])
    app.include_router(oauth_router, tags=["oauth"])
    app.include_router(admin_router, tags=["admin"])
    app.include_router(customer_router, tags=["admin"])
    app.include_router(responses_router, tags=["responses"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Conversation-Id"],
    )
    return app


app = create_app()