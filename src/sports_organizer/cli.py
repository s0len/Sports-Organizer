from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

from rich.console import Console
from rich.logging import RichHandler

from .config import AppConfig, Settings, load_config
from .processor import Processor

LOGGER = logging.getLogger(__name__)
CONSOLE = Console()
LOG_LEVEL_CHOICES = ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]
LOG_RECORD_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _parse_env_bool(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    return None


def _env_bool(name: str) -> Optional[bool]:
    return _parse_env_bool(os.getenv(name))


def _env_int(name: str) -> Tuple[Optional[int], bool]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None, False
    try:
        return int(raw), False
    except ValueError:
        return None, True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sports Organizer")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.getenv("CONFIG_PATH", "/config/sports.yaml")),
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
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging on the console")
    parser.add_argument(
        "--log-level",
        choices=LOG_LEVEL_CHOICES,
        help="Log level for the persistent log file (default INFO, or DEBUG when --verbose)",
    )
    parser.add_argument(
        "--console-level",
        choices=LOG_LEVEL_CHOICES,
        help="Log level for console output (defaults to --log-level)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        help="Path to the persistent log file (default ./sports.log or $LOG_FILE)",
    )
    parser.add_argument(
        "--clear-processed-cache",
        action="store_true",
        help="Clear the processed-file cache before running",
    )
    return parser.parse_args()


def _resolve_previous_log_path(log_file: Path) -> Path:
    if log_file.suffix:
        return log_file.with_suffix(f"{log_file.suffix}.previous")
    return log_file.with_name(f"{log_file.name}.previous")


def _resolve_level(name: str) -> int:
    return getattr(logging, name.upper(), logging.INFO)


def configure_logging(log_level_name: str, log_file: Path, console_level_name: Optional[str] = None) -> None:
    log_level = _resolve_level(log_level_name)
    console_level = _resolve_level(console_level_name or log_level_name)

    log_file = log_file.resolve()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    previous_log = _resolve_previous_log_path(log_file)
    if previous_log.exists():
        previous_log.unlink()
    rotated = False
    if log_file.exists():
        log_file.replace(previous_log)
        rotated = True

    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # pragma: no cover - defensive cleanup
            pass

    formatter = logging.Formatter(LOG_RECORD_FORMAT, LOG_DATE_FORMAT)

    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)

    plain_console_env = _env_bool("PLAIN_CONSOLE_LOGS")
    rich_console_env = _env_bool("RICH_CONSOLE_LOGS")
    if plain_console_env is True:
        use_rich_console = False
    elif rich_console_env is True:
        use_rich_console = True
    else:
        use_rich_console = CONSOLE.is_terminal

    if use_rich_console:
        console_handler = RichHandler(console=CONSOLE, rich_tracebacks=True, markup=True)
    else:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
    console_handler.setLevel(console_level)

    root_logger.setLevel(min(log_level, console_level))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logging.captureWarnings(True)

    if rotated:
        LOGGER.debug("Rotated previous log to %s", previous_log)
    LOGGER.info(
        "Logging to %s (file level %s, console level %s, console style %s)",
        log_file,
        logging.getLevelName(log_level),
        logging.getLevelName(console_level),
        "rich" if use_rich_console else "plain",
    )


def apply_runtime_overrides(config: AppConfig, args: argparse.Namespace) -> None:
    dry_run = args.dry_run or config.settings.dry_run
    env_dry_run = _env_bool("DRY_RUN")
    if env_dry_run is not None:
        dry_run = env_dry_run
    config.settings.dry_run = bool(dry_run)

    interval = args.interval if args.interval is not None else config.settings.poll_interval
    env_interval, invalid_interval = _env_int("PROCESS_INTERVAL")
    if invalid_interval:
        LOGGER.warning(
            "Invalid integer for PROCESS_INTERVAL: %s",
            os.getenv("PROCESS_INTERVAL"),
        )
    if env_interval is not None:
        interval = env_interval
    if interval is not None:
        config.settings.poll_interval = interval

    source_override = os.getenv("SOURCE_DIR")
    dest_override = os.getenv("DESTINATION_DIR")
    cache_override = os.getenv("CACHE_DIR")
    if source_override:
        config.settings.source_dir = Path(source_override)
    if dest_override:
        config.settings.destination_dir = Path(dest_override)
    if cache_override:
        config.settings.cache_dir = Path(cache_override)

    webhook_override = os.getenv("DISCORD_WEBHOOK_URL")
    if webhook_override is not None:
        config.settings.discord_webhook_url = webhook_override.strip() or None


def main() -> int:
    args = parse_args()
    env_verbose = _env_bool("VERBOSE")
    verbose = args.verbose
    if not verbose and env_verbose is not None:
        verbose = env_verbose
    if not verbose:
        debug_env = _env_bool("DEBUG")
        if debug_env:
            verbose = debug_env

    log_dir_env = os.getenv("LOG_DIR")
    log_file_env = os.getenv("LOG_FILE")
    if args.log_file:
        log_file = args.log_file
    elif log_dir_env:
        log_file = Path(log_dir_env) / "sports.log"
    elif log_file_env:
        log_file = Path(log_file_env)
    else:
        log_file = Path("sports.log")

    log_level_env = os.getenv("LOG_LEVEL")
    console_level_env = os.getenv("CONSOLE_LEVEL")

    resolved_log_level = (args.log_level or log_level_env or ("DEBUG" if verbose else "INFO"))
    resolved_console_level: Optional[str]
    if args.console_level:
        resolved_console_level = args.console_level
    elif console_level_env:
        resolved_console_level = console_level_env
    elif verbose:
        resolved_console_level = "DEBUG"
    else:
        resolved_console_level = None

    configure_logging(resolved_log_level.upper(), log_file, resolved_console_level.upper() if resolved_console_level else None)

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
    env_clear_cache = _env_bool("CLEAR_PROCESSED_CACHE")
    clear_processed_cache = args.clear_processed_cache
    if env_clear_cache is not None:
        clear_processed_cache = env_clear_cache

    if clear_processed_cache:
        LOGGER.info("Clearing processed file cache at %s", processor.processed_cache.cache_path)
        processor.clear_processed_cache()

    env_run_once = _env_bool("RUN_ONCE")
    default_run_once = True if env_run_once is None else env_run_once
    once = args.once or default_run_once
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
            if LOGGER.isEnabledFor(logging.DEBUG):
                LOGGER.debug("Sleeping for %s seconds", interval)
            time.sleep(interval)
    except KeyboardInterrupt:
        LOGGER.info("Interrupted by user")
    return 0


if __name__ == "__main__":
    sys.exit(main())
