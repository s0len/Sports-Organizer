from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from .config import AppConfig, Settings, load_config
from .processor import Processor

LOGGER = logging.getLogger(__name__)
CONSOLE = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sports Organizer")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.getenv("SPORTS_ORGANIZER_CONFIG", "/config/sports.yaml")),
        help="Path to the YAML configuration file",
    )
    parser.add_argument("--dry-run", action="store_true", help="Execute without writing to destination")
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit")
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Polling interval in seconds when running continuously",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose or os.getenv("DEBUG", "false").lower() == "true" else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=CONSOLE, rich_tracebacks=True, markup=True)],
    )


def apply_runtime_overrides(config: AppConfig, args: argparse.Namespace) -> None:
    if args.dry_run:
        config.settings.dry_run = True
    if args.interval is not None:
        config.settings.poll_interval = args.interval

    if os.getenv("SPORTS_ORGANIZER_DRY_RUN", "false").lower() == "true":
        config.settings.dry_run = True

    env_interval = os.getenv("SPORTS_ORGANIZER_PROCESS_INTERVAL")
    if env_interval and env_interval.isdigit():
        config.settings.poll_interval = int(env_interval)

    source_override = os.getenv("SPORTS_ORGANIZER_SOURCE")
    dest_override = os.getenv("SPORTS_ORGANIZER_DESTINATION")
    cache_override = os.getenv("SPORTS_ORGANIZER_CACHE")
    if source_override:
        config.settings.source_dir = Path(source_override)
    if dest_override:
        config.settings.destination_dir = Path(dest_override)
    if cache_override:
        config.settings.cache_dir = Path(cache_override)


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)

    if not args.config.exists():
        LOGGER.error("Configuration file %s does not exist", args.config)
        return 1

    try:
        config = load_config(args.config)
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Failed to load configuration: %s", exc)
        return 1

    apply_runtime_overrides(config, args)

    processor = Processor(config)
    once = args.once or os.getenv("SPORTS_ORGANIZER_RUN_ONCE", "true").lower() == "true"
    interval = config.settings.poll_interval

    LOGGER.info("Starting Sports Organizer%s", " (dry-run)" if config.settings.dry_run else "")

    try:
        while True:
            processor.run_once()
            if once:
                break
            if interval <= 0:
                LOGGER.info("No polling interval configured; exiting after one pass")
                break
            LOGGER.info("Sleeping for %s seconds", interval)
            time.sleep(interval)
    except KeyboardInterrupt:
        LOGGER.info("Interrupted by user")
    return 0


if __name__ == "__main__":
    sys.exit(main())
