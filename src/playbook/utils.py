from __future__ import annotations

import errno
import hashlib
import os
import re
import shutil
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml


NORMALIZE_PATTERN = re.compile(r"[^a-z0-9]+")


def normalize_token(value: str) -> str:
    """Return a normalized token suitable for fuzzy comparisons."""
    lowered = value.lower()
    stripped = NORMALIZE_PATTERN.sub("", lowered)
    return stripped


def slugify(value: str, separator: str = "-") -> str:
    """Create a slug suitable for file system usage."""
    normalized = normalize_token(value)
    words = [word for word in re.split(r"[^a-z0-9]+", value.lower()) if word]
    if not words:
        return normalized or "item"
    return separator.join(words)


SAFE_FILENAME_CHARS = set(string.ascii_letters + string.digits + "-_. ()[]")


def sanitize_component(component: str, replacement: str = "_") -> str:
    component = component.strip()
    if not component:
        return "untitled"

    cleaned = "".join(ch if ch in SAFE_FILENAME_CHARS else replacement for ch in component)
    cleaned = re.sub(r"%s+" % re.escape(replacement), replacement, cleaned)
    cleaned = cleaned.strip(replacement) or "untitled"

    if cleaned in {".", ".."}:
        return "untitled"

    return cleaned


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env(val) for key, val in value.items()}
    return value


def load_yaml_file(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return expand_env(data)


def dump_yaml_file(path: Path, data: Dict[str, Any]) -> None:
    ensure_directory(path.parent)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)


def sha1_of_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def sha1_of_file(path: Path, chunk_size: int = 65536) -> str:
    """Compute SHA1 hash of a file's contents."""
    sha1 = hashlib.sha1()
    try:
        with path.open("rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                sha1.update(chunk)
        return sha1.hexdigest()
    except (OSError, IOError) as exc:
        raise ValueError(f"Cannot compute hash for {path}: {exc}") from exc


@dataclass
class LinkResult:
    created: bool
    reason: Optional[str] = None


def link_file(source: Path, destination: Path, mode: str = "hardlink") -> LinkResult:
    ensure_directory(destination.parent)

    if destination.exists():
        return LinkResult(created=False, reason="destination-exists")

    try:
        if mode == "hardlink":
            os.link(source, destination)
        elif mode == "copy":
            shutil.copy2(source, destination)
        elif mode == "symlink":
            destination.symlink_to(source)
        else:
            raise ValueError(f"Unsupported link mode: {mode}")
    except OSError as exc:
        if mode == "hardlink" and exc.errno in {errno.EXDEV, errno.EPERM}:
            try:
                shutil.copy2(source, destination)
                return LinkResult(created=True)
            except Exception as copy_exc:  # noqa: BLE001
                return LinkResult(created=False, reason=str(copy_exc))
        return LinkResult(created=False, reason=str(exc))
    except Exception as exc:  # noqa: BLE001
        return LinkResult(created=False, reason=str(exc))

    return LinkResult(created=True)
