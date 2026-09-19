"""One shared logging setup for both entry points (the bot process and the
web process) so production log lines look the same regardless of which
service emitted them — important when a process manager or `docker compose
logs` interleaves both.
"""

import logging


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Both drivers log every query/heartbeat at INFO, which drowns out
    # actual application log lines; leave them at WARNING unless someone
    # explicitly asks for DEBUG (then they're useful again).
    if level.upper() != "DEBUG":
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("aiogram.event").setLevel(logging.WARNING)
