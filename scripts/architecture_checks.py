"""Static production import graph and explicit architectural boundaries."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
PACKAGE_ROOT = SOURCE_ROOT / "auto_research"

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
    for candidate in imported_names(module, tree):
        if candidate in modules:
            dependencies.add(candidate)
        elif f"{candidate}.__init__" in modules:
            dependencies.add(f"{candidate}.__init__")
        # A child executes its ancestor initializers, but never every sibling.
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


def imported_names(module: str, tree: ast.AST) -> set[str]:
    """Resolve direct, relative, and literal dynamic imports without executing."""
    names = set()
    package = module.split('.')[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = '.'.join(package[:len(package)-node.level+1]) if node.level else ''
            if node.module:
                base = '.'.join(filter(None, (base, node.module)))
            names.add(base)
            names.update(f'{base}.{alias.name}' for alias in node.names if alias.name != '*')
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            function = node.func
            if (isinstance(function, ast.Name) and function.id == '__import__') or (
                isinstance(function, ast.Attribute) and function.attr == 'import_module'
                and isinstance(function.value, ast.Name) and function.value.id == 'importlib'
            ):
                names.add(node.args[0].value)
    return names - {''}


def dependency_violations(module: str, tree: ast.AST, platform_modules: set[str]) -> list[str]:
    errors = []
    for target in sorted(imported_names(module, tree)):
        if target.split('.')[0] in platform_modules or target.startswith(('desktop.macos', 'desktop.windows')):
            errors.append(f'{module} imports platform adapter {target}')
        if module.startswith(('auto_research.product.', 'auto_research.personal.')) and target.startswith('auto_research.desktop'):
            errors.append(f'{module} imports desktop controller {target}')
        if module.startswith('auto_research.desktop.') and target.startswith('auto_research.evidence.webapp'):
            errors.append(f'{module} imports retired Web controller {target}')
    return errors


def production_dependency_violations() -> list[str]:
    platform_modules = {path.stem for path in (ROOT/'desktop/macos').glob('*.py') if not path.name.startswith('__')}
    return [error for module, path in _module_paths().items()
            for error in dependency_violations(module, ast.parse(path.read_text()), platform_modules)]
