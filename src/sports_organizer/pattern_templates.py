from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources
from typing import Any, Dict, List

from .utils import load_yaml_file


PLACEHOLDER_RE = re.compile(r"(?<!\?P)<([A-Za-z0-9_]+)>")
_REGEX_TOKENS: Dict[str, str] = {}


def _resolve_regex_tokens(raw_tokens: Dict[str, str]) -> Dict[str, str]:
    resolved: Dict[str, str] = {}

    def resolve(name: str, stack: List[str]) -> str:
        if name in resolved:
            return resolved[name]
        if name not in raw_tokens:
            raise ValueError(f"Unknown regex token <{name}> referenced")
        if name in stack:
            cycle = " -> ".join(stack + [name])
            raise ValueError(f"Circular regex token reference detected: {cycle}")
        pattern = raw_tokens[name]

        def replace(match: re.Match[str]) -> str:
            token_name = match.group(1)
            return resolve(token_name, stack + [name])

        expanded = PLACEHOLDER_RE.sub(replace, pattern)
        resolved[name] = expanded
        return expanded

    for token_name in raw_tokens:
        resolve(token_name, [])

    return resolved


def _expand_placeholders(text: str, tokens: Dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        token_name = match.group(1)
        if token_name not in tokens:
            raise ValueError(f"Unknown regex token <{token_name}> referenced in pattern: {text}")
        return tokens[token_name]

    return PLACEHOLDER_RE.sub(replace, text)


@lru_cache()
def load_builtin_pattern_sets() -> Dict[str, List[Dict[str, Any]]]:
    """Load the curated pattern sets shipped with Sports Organizer."""

    with resources.as_file(resources.files(__package__) / "pattern_templates.yaml") as path:
        data = load_yaml_file(path)

    raw_tokens = data.get("regex_tokens") or {}
    if not isinstance(raw_tokens, dict):
        raise ValueError("'regex_tokens' must be a mapping of token -> regex fragment when provided")
    normalized_tokens = {str(key): str(value) for key, value in raw_tokens.items()}
    resolved_tokens = _resolve_regex_tokens(normalized_tokens)
    global _REGEX_TOKENS
    _REGEX_TOKENS = resolved_tokens

    pattern_sets = data.get("pattern_sets", {})
    if not isinstance(pattern_sets, dict):
        raise ValueError("Builtin pattern templates must define a mapping of pattern sets")

    for pattern_list in pattern_sets.values():
        if not isinstance(pattern_list, list):
            continue
        for pattern in pattern_list:
            if not isinstance(pattern, dict):
                continue
            regex_value = pattern.get("regex")
            if isinstance(regex_value, str):
                pattern["regex"] = _expand_placeholders(regex_value, resolved_tokens)

    return pattern_sets


def expand_regex_with_tokens(regex: str) -> str:
    if PLACEHOLDER_RE.search(regex) is None:
        return regex
    if not _REGEX_TOKENS:
        # Ensure tokens are loaded by forcing the builtin template load.
        load_builtin_pattern_sets()
    return _expand_placeholders(regex, _REGEX_TOKENS)


