
import os
import logging
from banking_account.observability.logging_config import configure_logging
from banking_account.mcp_tools import mcp
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from banking_shared.tracing import create_meter_provider, create_tracer_provider
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from banking_account.routers.products import router as account_routers
import uvicorn

logger = logging.getLogger(__name__)

def create_app() -> FastAPI:
    # Initialize logging for the app
    configure_logging()
    logger = logging.getLogger(__name__)


   #Add mcp server to the FastAPI app
    mcp_app = mcp.http_app(path='/')
    app = FastAPI(title="Account API and MCP server", lifespan=mcp_app.lifespan)
    app.mount("/mcp", mcp_app)
    FastAPIInstrumentor.instrument_app(
        app,
        exclude_spans=["send", "receive"],
        tracer_provider=create_tracer_provider(
            "banking-assistant-account", os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
        ),
        meter_provider=create_meter_provider(
            "banking-assistant-account", os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
        ),
    )

    # Include the transaction router
    app.include_router(account_routers, prefix="/api", tags=["accounts"])

    allowed_origins = [
        origin.strip()
        for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:5170").split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    logger.info("FastAPI application created successfully")
    return app

app = create_app()

if __name__ == "__main__":

    profile = os.environ.get("PROFILE", "prod")
    port = 8070 if profile == "dev" else 8080
    logger.info(f"Starting account service server with profile: {profile}, port: {port}")
    # App Service terminates TLS at its own front end and forwards plain HTTP to
    # the container, so Uvicorn must trust X-Forwarded-Proto to avoid generating
    # http:// redirects (e.g. the mounted MCP app's trailing-slash redirect).
    uvicorn.run(
        "banking_account.main:app", host="0.0.0.0", port=port,
        proxy_headers=True, forwarded_allow_ips="*",
    )