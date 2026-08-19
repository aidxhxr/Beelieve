"""Entrypoint: configure logging, install signal handlers, run the processor."""

from __future__ import annotations

import logging
import signal
import sys
from types import FrameType

from .config import Settings
from .db import BatchedWriter
from .processor import StreamProcessor

log = logging.getLogger(__name__)


def main() -> int:
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    processor = StreamProcessor(settings, BatchedWriter(settings))

    def _handle_signal(signum: int, _frame: FrameType | None) -> None:
        log.info("Received %s; shutting down gracefully", signal.Signals(signum).name)
        processor.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        processor.run()
    except Exception:  # noqa: BLE001 — top-level guard: log and exit non-zero
        log.exception("stream-processor crashed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
