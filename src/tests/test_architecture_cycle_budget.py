from __future__ import annotations

import ast
import unittest


from scripts.architecture_checks import _production_cycles, _resolve_imports


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
