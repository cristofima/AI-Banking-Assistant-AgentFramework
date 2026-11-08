"""Responses telemetry compatibility configuration for application startup."""

from __future__ import annotations

import logging
import os
import sys

_HOST_WORKFLOW_LOGGER = "agent_framework_foundry_hosting._workflow_responses"


class _AttachActiveException(logging.Filter):
    """Attach the exception being handled to records the host logs with only its type name."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info is None and sys.exc_info()[0] is not None:
            record.exc_info = sys.exc_info()
        return True


def enable_local_workflow_error_details() -> None:
    """Include the traceback of native workflow failures in local logs.

    The host logs only the exception type; hosted deployments keep that behavior.
    """
    logging.getLogger(_HOST_WORKFLOW_LOGGER).addFilter(_AttachActiveException())


def configure_telemetry_flush() -> None:
    """Preserve awaited, off-event-loop export instead of background flushing.

    Call before serving Responses requests. The host reads this supported setting
    for each request; an environment override must not silently weaken this policy.
    """
    os.environ["AGENTSERVER_FLUSH_MODE"] = "async"
