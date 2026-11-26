import logging
from threading import Lock


LOGGER_NAME = "text_to_sql_api"
_CONSOLE_HANDLER_NAME = "text_to_sql_api.console"
_CONFIGURATION_LOCK = Lock()


def configure_logging() -> None:
    """Configure application logs for both the Python and Uvicorn entrypoints."""
    with _CONFIGURATION_LOCK:
        logger = logging.getLogger(LOGGER_NAME)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if any(handler.get_name() == _CONSOLE_HANDLER_NAME for handler in logger.handlers):
            return

        handler = logging.StreamHandler()
        handler.set_name(_CONSOLE_HANDLER_NAME)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(operation)s] "
            "request_id=%(request_id)s stage=%(stage)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            defaults={"request_id": "-", "stage": "-", "operation": "indexing_schema"},
        ))
        logger.addHandler(handler)
