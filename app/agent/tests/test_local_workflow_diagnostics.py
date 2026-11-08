import logging

from app.telemetry import _HOST_WORKFLOW_LOGGER, enable_local_workflow_error_details


def test_local_diagnostics_attach_handled_exception_to_host_record(caplog) -> None:
    host_logger = logging.getLogger(_HOST_WORKFLOW_LOGGER)
    enable_local_workflow_error_details()
    try:
        with caplog.at_level(logging.ERROR, logger=_HOST_WORKFLOW_LOGGER):
            try:
                raise RuntimeError("boom")
            except RuntimeError as exc:
                host_logger.error("failed (%s).", type(exc).__name__)
    finally:
        host_logger.filters.clear()
    assert caplog.records[0].exc_info is not None
    assert caplog.records[0].exc_info[1].args == ("boom",)
