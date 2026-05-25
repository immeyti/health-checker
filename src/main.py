from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

import uvicorn
from dotenv import load_dotenv

from .config import ConfigError, load_config
from .daemon import MonitoringDaemon
from .state import StateTracker
from .web.app import create_app


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dashboard monitoring daemon")
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to config YAML (default: config/config.yaml)",
    )
    parser.add_argument(
        "--db",
        default="monitor.db",
        help="Path to SQLite database file (default: monitor.db)",
    )
    return parser.parse_args()


async def _main(args: argparse.Namespace) -> None:
    _setup_logging()
    logger = logging.getLogger(__name__)

    load_dotenv()

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        logger.critical("Configuration error: %s", exc)
        sys.exit(1)

    for t in config.dashboard_targets:
        logger.info("Dashboard target loaded: name=%r url=%r username=%r", t.name, t.url, t.username)

    state = StateTracker(db_path=args.db)
    daemon = MonitoringDaemon(config=config, state=state)

    host_map: dict[str, str] = {}
    for t in config.ping_targets:
        host_map[t.name] = t.host
    for t in config.tcp_targets:
        host_map[t.name] = f"{t.host}:{t.port}"
    for t in config.dashboard_targets:
        host_map[t.name] = t.url

    app = create_app(
        state=state,
        retention_days=config.storage.retention_days,
        screenshots_dir=config.storage.screenshots_dir,
        host_map=host_map,
    )

    uvicorn_config = uvicorn.Config(
        app=app,
        host=config.web.host,
        port=config.web.port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(uvicorn_config)

    loop = asyncio.get_running_loop()

    def _handle_signal():
        logger.info("Shutdown signal received")
        daemon.stop()
        server.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    logger.info(
        "Starting monitor — web UI at http://%s:%d",
        config.web.host,
        config.web.port,
    )

    await asyncio.gather(daemon.run(), server.serve())


def main() -> None:
    args = _parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
