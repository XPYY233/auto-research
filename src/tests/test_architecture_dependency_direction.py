import ast

from scripts.architecture_checks import dependency_violations, production_dependency_violations


def test_shared_production_dependencies_follow_declared_directions():
    assert production_dependency_violations() == []


def test_guards_cover_absolute_relative_and_literal_dynamic_imports():
    cases = [
        ('auto_research.product.transfer', 'from auto_research.desktop import routing'),
        ('auto_research.personal.repository', 'from ..desktop.routing import RequestContext'),
        ('auto_research.desktop.imports', 'from ..evidence import webapp'),
        ('auto_research.evidence.rules', 'import desktop_runtime'),
        ('auto_research.evidence.rules', 'import desktop.macos.desktop_server'),
        ('auto_research.evidence.rules', 'import importlib; importlib.import_module("desktop_runtime")'),
    ]
    for module, code in cases:
        assert dependency_violations(module, ast.parse(code), {'desktop_runtime'})


def test_platform_neutral_dependencies_and_sibling_imports_remain_allowed():
    assert dependency_violations('auto_research.desktop.imports', ast.parse('from ..evidence import uploads'), {'desktop_runtime'}) == []
    assert dependency_violations('auto_research.product.archive', ast.parse('from . import portable_repository'), {'desktop_runtime'}) == []
