from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


class TemplateDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_template(template: str, context: Dict[str, Any]) -> str:
    enriched = TemplateDict(context)
    return template.format_map(enriched)
