"""Clean-architecture rules that import-linter cannot express.

1. A domain uses another domain only through its ``public`` module.
2. HTTP views and serializers never write to the database themselves; they call
   services (checked by forbidding ORM write calls in api modules).
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APPS = ROOT / "apps"
DOMAINS = sorted(p.name for p in APPS.iterdir() if (p / "apps.py").exists())


def _modules(domain: str) -> list[Path]:
    return [
        path
        for path in (APPS / domain).rglob("*.py")
        if "migrations" not in path.parts and "tests" not in path.parts
    ]


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names += [node.module] + [f"{node.module}.{a.name}" for a in node.names]
    return names


@pytest.mark.parametrize("domain", DOMAINS)
def test_other_domains_are_used_only_through_public(domain):
    violations = []
    for path in _modules(domain):
        for name in _imported_modules(path):
            parts = name.split(".")
            if len(parts) < 2 or parts[0] != "apps" or parts[1] == domain:
                continue
            if len(parts) == 2:
                continue  # "from apps.x import public" is checked through its member
            if parts[2] != "public":
                violations.append(f"{path.relative_to(ROOT)} imports {name}")
    assert violations == []


ORM_WRITES = {"save", "create", "update", "delete", "bulk_create", "bulk_update", "get_or_create"}


@pytest.mark.parametrize("domain", DOMAINS)
def test_api_layer_does_not_write_to_the_database(domain):
    violations = []
    for path in (APPS / domain / "api").rglob("*.py") if (APPS / domain / "api").exists() else []:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ORM_WRITES
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr in {"objects", "unscoped"}
            ):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno} .{node.func.attr}()")
    assert violations == []


def test_every_domain_has_a_public_interface():
    assert [d for d in DOMAINS if not (APPS / d / "public.py").exists()] == []
