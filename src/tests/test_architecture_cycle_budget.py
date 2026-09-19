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
        if isinstance(node, ast.ImportFrom):
            # Python executes a package initializer and only the named child
            # module, never every sibling beneath that package.
            base = (".".join(package[:len(package) - node.level + 1])
                    if node.level else "")
            if node.module:
                base = ".".join(filter(None, (base, node.module)))
            candidates = [base] + [f"{base}.{alias.name}" for alias in node.names if alias.name != "*"]
        for candidate in candidates:
            if candidate in modules:
                dependencies.add(candidate)
            elif f"{candidate}.__init__" in modules:
                dependencies.add(f"{candidate}.__init__")
            # Importing a child also executes its ancestor initializers.
            parts = candidate.split(".")
            dependencies.update(
                name for length in range(1, len(parts))
                if (name := ".".join(parts[:length]) + ".__init__") in modules
                and name != module
                and not module.startswith(".".join(parts[:length]) + ".")
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

    def test_absolute_package_import_does_not_import_siblings(self) -> None:
        modules = {"auto_research.paths", "auto_research.cli", "auto_research.evidence.db"}
        self.assertEqual(
            {"auto_research.paths"},
            _resolve_imports("auto_research.evidence.db", ast.parse("from auto_research import paths"), modules),
        )

    def test_imports_include_package_initializers(self) -> None:
        modules = {"auto_research.__init__", "auto_research.evidence.__init__", "auto_research.evidence.db"}
        self.assertEqual(modules, _resolve_imports("consumer", ast.parse("import auto_research.evidence.db"), modules))

    def test_production_has_no_import_cycles(self) -> None:
        self.assertEqual([], [sorted(component) for component in _production_cycles()])


if __name__ == "__main__":
    unittest.main()
