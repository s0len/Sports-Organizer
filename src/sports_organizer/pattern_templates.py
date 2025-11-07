from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Any, Dict, List

from .utils import load_yaml_file


@lru_cache()
def load_builtin_pattern_sets() -> Dict[str, List[Dict[str, Any]]]:
    """Load the curated pattern sets shipped with Sports Organizer."""

    with resources.as_file(resources.files(__package__) / "pattern_templates.yaml") as path:
        data = load_yaml_file(path)

    pattern_sets = data.get("pattern_sets", {})
    if not isinstance(pattern_sets, dict):
        raise ValueError("Builtin pattern templates must define a mapping of pattern sets")

    return pattern_sets


