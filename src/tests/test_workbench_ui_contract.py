from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web"


class WorkbenchUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        cls.app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
        cls.styles = (WEB_ROOT / "workbench.css").read_text(encoding="utf-8")
        cls.runtime = (WEB_ROOT / "workbench.js").read_text(encoding="utf-8")

    def test_prepaint_cache_and_runtime_have_separate_single_owners(self) -> None:
        app_runtime = '<script src="/static/app.js"></script>'
        workbench_runtime = '<script src="/static/workbench.js"></script>'
        brief_runtime = '<script src="/static/librarian_brief.js"></script>'
        head = self.index.split("</head>", 1)[0]
        self.assertIn('localStorage.getItem("auto-research-appearance-v1")', head)
        self.assertNotIn('src="/static/workbench.js"', head)
        self.assertNotIn("addEventListener", head)
        self.assertEqual(self.index.count(workbench_runtime), 1)
        self.assertLess(self.index.index(app_runtime), self.index.index(workbench_runtime))
        self.assertLess(self.index.index(workbench_runtime), self.index.index(brief_runtime))
        self.assertIn('document.addEventListener("DOMContentLoaded", initialize', self.runtime)
        self.assertIn("applyPreferences();", self.runtime)

    def test_appearance_preferences_are_versioned_and_non_sensitive(self) -> None:
        self.assertIn('auto-research-appearance-v1', self.runtime)
        for value in ('"system"', '"light"', '"dark"', '"comfortable"', '"compact"'):
            self.assertIn(value, self.runtime)
        self.assertNotIn("api_key", self.runtime)
        self.assertNotIn("fetch(", self.runtime)
        self.assertNotIn("/api/", self.runtime)
        self.assertIn('schema_version !== "desktop-settings-v1"', self.runtime)
        self.assertIn("hydratePreferences", self.runtime)
        self.assertIn("no-flash cache", self.runtime)

    def test_saved_appearance_is_applied_before_dom_ready(self) -> None:
        node_program = f"""
const fs = require("fs");
globalThis.localStorage = {{
  getItem: () => JSON.stringify({{version: 1, theme: "dark", density: "compact"}}),
  setItem: () => {{}}
}};
const root = {{dataset: {{}}, style: {{}}}};
globalThis.document = {{
  documentElement: root,
  readyState: "loading",
  querySelectorAll: () => [],
  addEventListener: () => {{}}
}};
globalThis.matchMedia = () => ({{matches: false}});
eval(fs.readFileSync({str(WEB_ROOT / "workbench.js")!r}, "utf8"));
if (root.dataset.theme !== "dark" || root.dataset.density !== "compact") process.exit(1);
"""
        completed = subprocess.run(
            ["node", "-e", node_program],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_single_router_owns_navigation_and_keyboard_shortcuts(self) -> None:
        self.assertIn("function switchView(name, options = {})", self.app)
        self.assertNotIn("function switchView", self.runtime)
        self.assertIn('if (typeof switchView !== "function") return;', self.runtime)
        self.assertIn('const viewByKey = { "1": "paper", "2": "search", "3": "personal", "4": "package" }', self.runtime)
        self.assertIn('event.key.toLocaleLowerCase("en-US") === "k"', self.runtime)
        self.assertIn('settings: { kicker: "WORKBENCH SETTINGS"', self.app)
        self.assertIn('matches?.("input,textarea,select,[contenteditable=\'true\']")', self.runtime)

    def test_roving_tabindex_and_accessible_selection_are_explicit(self) -> None:
        self.assertIn('role="tablist" aria-label="设置类别"', self.index)
        self.assertIn('role="tabpanel" data-settings-panel="appearance"', self.index)
        self.assertIn('role="option" aria-selected="true" tabindex="0"', self.index)
        self.assertIn('button.setAttribute("aria-selected", active ? "true" : "false")', self.runtime)
        self.assertIn('button.tabIndex = active ? 0 : -1', self.runtime)
        self.assertIn('["ArrowDown", "ArrowUp", "Enter"]', self.runtime)
        self.assertIn('event.key === "Home" || event.key === "End"', self.runtime)
        self.assertIn('media?.matches ? "horizontal" : "vertical"', self.runtime)

    def test_personal_and_package_surfaces_have_static_single_owners(self) -> None:
        personal = re.search(r'<section class="view" id="view-personal".*?</section>\s*</section>', self.index, re.DOTALL)
        package = re.search(r'<section class="view package-center" id="view-package".*?<section class="package-center-card package-official-card".*?</section>', self.index, re.DOTALL)
        self.assertIsNotNone(personal)
        self.assertIsNotNone(package)
        self.assertIn('id="personal-import-panel"', personal.group(0))
        self.assertIn('id="desktop-official-package-status"', package.group(0))
        self.assertEqual(self.index.count('id="personal-import-panel"'), 1)
        self.assertEqual(self.index.count('id="desktop-official-package-status"'), 1)

    def test_settings_shell_is_accessible_and_honest_about_language_and_ai(self) -> None:
        self.assertIn('id="view-settings"', self.index)
        self.assertIn('id="workbench-settings-open"', self.index)
        self.assertIn('简体中文', self.index)
        self.assertIn('English', self.index)
        self.assertIn('计划支持', self.index)
        self.assertIn('模型服务接口正在接入', self.index)
        self.assertIn('不会显示密钥片段', self.index)
        self.assertIn('aria-keyshortcuts="Meta+K Control+K"', self.index)
        self.assertIn('role="listbox"', self.index)

    def test_workbench_tokens_cover_light_dark_density_focus_and_reduced_motion(self) -> None:
        for marker in (
            ':root[data-theme="dark"]',
            ':root[data-density="compact"]',
            '--wb-canvas:',
            '--wb-surface:',
            '--wb-sidebar:',
            '--wb-accent:',
            ':focus-visible',
            '@media(prefers-reduced-motion:reduce)',
            '@media(max-width:900px)',
        ):
            self.assertIn(marker, self.styles)
        self.assertNotIn("radial-gradient", self.styles)
        self.assertNotIn("@keyframes", self.styles)

    def test_activity_bar_has_four_primary_research_views_and_one_settings_control(self) -> None:
        navigation = re.search(r'<nav aria-label="工作区".*?</nav>', self.index, re.DOTALL)
        self.assertIsNotNone(navigation)
        for view in ("paper", "search", "personal", "package"):
            self.assertEqual(navigation.group(0).count(f'data-view="{view}"'), 1)
        self.assertNotIn('data-view="settings"', navigation.group(0))
        self.assertEqual(self.index.count('id="workbench-settings-open"'), 1)


if __name__ == "__main__":
    unittest.main()
