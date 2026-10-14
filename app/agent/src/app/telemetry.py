"""Responses telemetry compatibility configuration for application startup."""

from __future__ import annotations

import os


def configure_telemetry_flush() -> None:
    """Preserve awaited, off-event-loop export instead of background flushing.

    Call before serving Responses requests. The host reads this supported setting
    for each request; an environment override must not silently weaken this policy.
    """
    os.environ["AGENTSERVER_FLUSH_MODE"] = "async"
