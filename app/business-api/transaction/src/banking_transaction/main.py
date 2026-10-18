import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from banking_shared.tracing import create_meter_provider, create_tracer_provider
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
import uvicorn
from banking_transaction.observability.logging_config import configure_logging
from banking_transaction.mcp_tools import mcp
import logging

# import the transaction router we just added
from banking_transaction.routers.transactions import router as transaction_routers
from banking_transaction.routers.disputes import router as dispute_routers
from banking_transaction.routers.operator import router as operator_routers

logger = logging.getLogger(__name__)

def create_app() -> FastAPI:
    # Initialize logging for the app
    configure_logging()
    logger = logging.getLogger(__name__)


   #Add mcp server to the FastAPI app
    mcp_app = mcp.http_app(path='/')
    app = FastAPI(title="Transaction API and MCP server", lifespan=mcp_app.lifespan)
    app.mount("/mcp", mcp_app)
    FastAPIInstrumentor.instrument_app(
        app,
        exclude_spans=["send", "receive"],
        tracer_provider=create_tracer_provider(
            "banking-assistant-transaction", os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
        ),
        meter_provider=create_meter_provider(
            "banking-assistant-transaction", os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
        ),
    )

    # Include the transaction router
    app.include_router(transaction_routers, prefix="/api/transactions", tags=["transactions"])
    app.include_router(dispute_routers, prefix="/api/support-cases", tags=["support-cases"])
    app.include_router(operator_routers, prefix="/api/operator/support-cases", tags=["operator-support-cases"])

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
    port = 8071 if profile == "dev" else 8080
    logger.info(f"Starting transaction service server with profile: {profile}, port: {port}")
    # App Service terminates TLS at its own front end and forwards plain HTTP to
    # the container, so Uvicorn must trust X-Forwarded-Proto to avoid generating
    # http:// redirects (e.g. the mounted MCP app's trailing-slash redirect).
    uvicorn.run(
        "banking_transaction.main:app", host="0.0.0.0", port=port,
        proxy_headers=True, forwarded_allow_ips="*",
    )
