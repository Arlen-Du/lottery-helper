"""Entry point for the Lottery Helper Web / fnOS native server."""

from __future__ import annotations

import logging
import os

import uvicorn

from src.web.app import get_default_app


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOTTERY_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    host = os.environ.get("LOTTERY_HOST", "0.0.0.0")
    port = int(os.environ.get("LOTTERY_PORT", "18680"))
    app = get_default_app()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
