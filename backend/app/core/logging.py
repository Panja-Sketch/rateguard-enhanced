import logging
import sys

from app.core.config import get_settings


def configure_logging(level: str | None = None) -> None:
    """Configures standard Python logging using a clean, readable structured format.

    Args:
        level: Optional log level string override. Defaults to value from app settings.
    """
    settings = get_settings()
    log_level_str = level or settings.log_level
    numeric_level = getattr(logging, log_level_str.upper(), logging.INFO)

    log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=numeric_level,
        format=log_format,
        datefmt=date_format,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )

    # Set third-party loggers to WARNING to reduce noise
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
