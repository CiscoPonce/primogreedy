import logging
import sys

_configured = False


def get_logger(name: str = "primogreedy") -> logging.Logger:
    """Return a named logger with consistent formatting.

    All PrimoGreedy modules should call ``get_logger(__name__)`` to get a
    logger scoped to their module.  The root ``primogreedy`` logger is
    configured once with a StreamHandler so every child inherits it.
    """
    global _configured

    root = logging.getLogger("primogreedy")

    if not _configured:
        root.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root.addHandler(handler)
        root.propagate = False
        _configured = True

    # Namespace every caller under the configured "primogreedy" logger.  Callers
    # pass ``__name__`` (e.g. "src.whale_hunter"), which is NOT a child of
    # "primogreedy" — returning that directly meant INFO records had no handler
    # and were dropped (only WARNING+ leaked via logging.lastResort, to stderr).
    if name == "primogreedy" or name.startswith("primogreedy."):
        logger_name = name
    else:
        logger_name = f"primogreedy.{name}"

    return logging.getLogger(logger_name)
