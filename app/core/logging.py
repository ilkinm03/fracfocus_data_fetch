import logging
import logging.config


def setup_logging(level: str = "INFO") -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {
                    "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
                    "format": "%(asctime)s %(name)s %(levelname)s %(message)s",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                }
            },
            "root": {"level": level, "handlers": ["console"]},
            "loggers": {
                "apscheduler": {"level": "WARNING", "propagate": True},
                "sqlalchemy.engine": {"level": "WARNING", "propagate": True},
            },
        }
    )
