"""Placeholder-only templates: ``{{ name }}`` is replaced by the value, nothing else runs.

Organisations write their own templates, so no template language (loops, tags, filters,
attribute access) is exposed: a template can only show the values the sender passed.
"""

import re
from collections.abc import Iterable, Mapping

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
_ANY_TAG = re.compile(r"\{[{%#]")


def render(template: str, context: Mapping[str, object]) -> str:
    return _PLACEHOLDER.sub(lambda m: str(context.get(m.group(1), "")), template)


def validate(template: str, allowed: Iterable[str]) -> None:
    """Raise ValueError for unknown placeholders or any other template syntax."""
    allowed_set = set(allowed)
    used = set(_PLACEHOLDER.findall(template))
    unknown = used - allowed_set
    if unknown:
        raise ValueError(f"Unknown placeholders: {', '.join(sorted(unknown))}")
    leftover = _PLACEHOLDER.sub("", template)
    if _ANY_TAG.search(leftover):
        raise ValueError("Only {{ placeholder }} substitutions are allowed.")
