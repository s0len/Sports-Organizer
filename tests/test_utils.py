from __future__ import annotations

from sports_organizer.utils import (
    link_file,
    normalize_token,
    sanitize_component,
    slugify,
)


def test_normalize_token_removes_non_alphanumerics() -> None:
    assert normalize_token("FP1 Warm-Up!") == "fp1warmup"


def test_slugify_handles_punctuation_and_case() -> None:
    assert slugify("Grand Prix #1") == "grand-prix-1"


def test_sanitize_component_replaces_disallowed_characters() -> None:
    assert sanitize_component("  weird*name?.mkv  ") == "weird_name_.mkv"
    assert sanitize_component("???") == "untitled"


def test_sanitize_component_rejects_dot_segments() -> None:
    assert sanitize_component(".") == "untitled"
    assert sanitize_component("..") == "untitled"
    assert sanitize_component(" .. ") == "untitled"


def test_link_file_creates_destination_and_detects_existing(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("test-data", encoding="utf-8")

    destination = tmp_path / "nested" / "destination.txt"

    result = link_file(source, destination)
    assert result.created is True
    assert destination.exists()
    assert destination.read_text(encoding="utf-8") == "test-data"

    second = link_file(source, destination)
    assert second.created is False
    assert second.reason == "destination-exists"

