from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "src"
PACKAGE_ROOT = SOURCE_ROOT / "auto_research"
BASELINE_PATH = ROOT / "config" / "python-import-cycle-baseline.json"


def _module_paths() -> dict[str, Path]:
    return {
        ".".join(path.relative_to(SOURCE_ROOT).with_suffix("").parts): path
        for path in PACKAGE_ROOT.rglob("*.py")
    }


def _resolve_imports(
    module: str,
    tree: ast.AST,
    modules: set[str],
) -> set[str]:
    dependencies: set[str] = set()
    package = module.split(".")[:-1]
    for node in ast.walk(tree):
        candidates: list[str] = []
        if isinstance(node, ast.Import):
            candidates.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = max(0, len(package) - node.level + 1)
                base = package[:keep]
                if node.module:
                    candidates.append(".".join(base + [node.module]))
                else:
                    candidates.extend(
                        ".".join(base + [alias.name])
                        for alias in node.names
                        if alias.name != "*"
                    )
            elif node.module:
                candidates.append(node.module)
        for candidate in candidates:
            dependencies.update(
                name
                for name in modules
                if name == candidate or name.startswith(f"{candidate}.")
            )
    return dependencies


def _production_cycles() -> list[frozenset[str]]:
    paths = _module_paths()
    modules = set(paths)
    graph = {
        module: _resolve_imports(
            module,
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path)),
            modules,
        )
        for module, path in paths.items()
    }

    counter = 0
    stack: list[str] = []
    active: set[str] = set()
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    components: list[frozenset[str]] = []

    def visit(module: str) -> None:
        nonlocal counter
        indexes[module] = counter
        lowlinks[module] = counter
        counter += 1
        stack.append(module)
        active.add(module)
        for dependency in graph[module]:
            if dependency not in indexes:
                visit(dependency)
                lowlinks[module] = min(lowlinks[module], lowlinks[dependency])
            elif dependency in active:
                lowlinks[module] = min(lowlinks[module], indexes[dependency])
        if lowlinks[module] != indexes[module]:
            return
        component: set[str] = set()
        while True:
            member = stack.pop()
            active.remove(member)
            component.add(member)
            if member == module:
                break
        if len(component) > 1:
            components.append(frozenset(component))

    for module in sorted(graph):
        if module not in indexes:
            visit(module)
    return components


class ArchitectureCycleBudgetTests(unittest.TestCase):
    def test_relative_package_import_resolves_only_named_module(self) -> None:
        modules = {
            "auto_research.evidence.table_structure",
            "auto_research.evidence.table_structure_store",
            "auto_research.evidence.webapp",
        }
        tree = ast.parse("from . import table_structure as contract")
        self.assertEqual(
            {"auto_research.evidence.table_structure"},
            _resolve_imports(
                "auto_research.evidence.table_structure_store",
                tree,
                modules,
            ),
        )

    def test_existing_import_cycles_can_only_shrink(self) -> None:
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            "auto-research-python-import-cycle-baseline-v1",
            baseline["schema"],
        )
        allowed = [frozenset(component) for component in baseline["components"]]
        actual = _production_cycles()
        unexpected = [
            sorted(component)
            for component in actual
            if not any(component <= known for known in allowed)
        ]
        self.assertEqual([], unexpected, "new or expanded import cycle detected")
        self.assertLessEqual(len(actual), len(allowed))


if __name__ == "__main__":
    unittest.main()
