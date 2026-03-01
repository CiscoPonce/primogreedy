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
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root.addHandler(handler)
        _configured = True

    return logging.getLogger(name)
