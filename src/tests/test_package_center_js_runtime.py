from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_CENTER_JS = (
    PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web" / "package_center.js"
)
DESKTOP_PRODUCT_JS = (
    PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web" / "desktop_product.js"
)


class PackageCenterJavaScriptRuntimeTests(unittest.TestCase):
    def test_package_view_remains_visible_when_private_search_finishes_late(self) -> None:
        script = r"""
const fs = require("fs");
const assert = require("assert");
const vm = require("vm");

let resolveSearch;
let reveals = 0;
const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      id, hidden: false, disabled: false, value: "", checked: false,
      textContent: "", innerHTML: "", dataset: {}, parentElement: null,
      children: [],
      classList: { toggle() {}, add() {}, remove() {} },
      addEventListener() {}, setAttribute() {}, toggleAttribute() {},
      appendChild(child) { child.parentElement = this; this.children.push(child); },
      querySelector() { return null; }, querySelectorAll() { return []; },
      scrollIntoView() { reveals += 1; },
    });
  }
  return elements.get(id);
}

const context = vm.createContext({
  console,
  URLSearchParams,
  document: {
    body: { dataset: { view: "personal" } },
    getElementById: element,
    querySelector() { return null; },
    querySelectorAll() { return []; },
  },
  window: {},
});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
context.assert = assert;
context.element = element;
context.setResolveSearch = resolve => { resolveSearch = resolve; };
context.finishSearch = () => resolveSearch({ results: [], total: 0 });
vm.runInContext(`
  const state = { searchScope: "all", searchExperience: "precise", searchMode: "item", searchRequest: 0 };
  const api = async route => {
    if (route === "/api/desktop/evidence-packages") return { active: false };
    if (route === "/api/desktop/credentials/deepseek") return { configured: false };
    if (route === "/api/desktop/personal-imports/search-status") return { state: "ready", ready: true, document_count: 1 };
    if (route.startsWith("/api/desktop/federated-search?")) return new Promise(resolve => { setResolveSearch(resolve); });
    throw new Error("unexpected route: " + route);
  };
  const toast = () => {};
  const esc = value => String(value);
  const paperMatchesFilters = () => true;
  const setSearchExperience = () => {};
  const setText = () => {};
  const setSearchBusy = () => {};
  const switchView = name => { document.body.dataset.view = name; };
  const waitForJob = async () => {};
  globalThis.__runtimePromise = (async () => {
    await globalThis.AutoResearchDesktopProduct.initialize();
    element("personal-import-panel").hidden = false;
    const pending = globalThis.__showPrivateSearchResultsForTest();
    assert.strictEqual(document.body.dataset.view, "search");
    switchView("package");
    finishSearch();
    await pending;
    assert.strictEqual(document.body.dataset.view, "package");
  })();
`, context);
context.__runtimePromise.then(() => {
  assert.strictEqual(reveals, 0);
}).catch(error => { console.error(error); process.exitCode = 1; });
"""
        source = DESKTOP_PRODUCT_JS.read_text(encoding="utf-8")
        marker = "globalThis.AutoResearchDesktopProduct = { initialize, handleSearch, applySearchUI, openPersonalImport, openPackageCenter };"
        instrumented = source.replace(
            marker,
            "globalThis.__showPrivateSearchResultsForTest = showPrivateSearchResults;\n  " + marker,
        )
        with self.subTest("late private search"):
            import tempfile

            with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8") as handle:
                handle.write(instrumented)
                handle.flush()
                completed = subprocess.run(
                    ["node", "-e", script, handle.name],
                    cwd=PROJECT_ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_desktop_product_loads_before_shared_app_runtime_without_eager_port_access(self) -> None:
        script = r"""
const fs = require("fs");
const assert = require("assert");
const vm = require("vm");

const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      id,
      hidden: false,
      disabled: false,
      value: "",
      checked: false,
      textContent: "",
      innerHTML: "",
      dataset: {},
      parentElement: null,
      classList: { toggle() {}, add() {}, remove() {} },
      addEventListener() {},
      appendChild(child) { child.parentElement = this; },
      setAttribute() {},
      toggleAttribute() {},
      querySelector() { return null; },
      querySelectorAll() { return []; },
    });
  }
  return elements.get(id);
}

let createCalls = 0;
let initCalls = 0;
const context = vm.createContext({
  console,
  document: {
  body: { dataset: {} },
  getElementById: element,
  querySelector() { return null; },
  querySelectorAll() { return []; },
  },
  window: {},
});
context.AutoResearchPackageCenter = {
  create() {
    createCalls += 1;
    return {
      init() { initCalls += 1; },
      loadStatus: async () => {},
      refreshView() {},
    };
  },
};

// The production page loads desktop_product.js before app.js declares api/toast/state.
// Loading this script must therefore register its facade without touching those ports.
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
assert(context.AutoResearchDesktopProduct);
assert.strictEqual(createCalls, 0);

// app.js declares the shared ports later and then initializes the desktop facade.
// Repeated initialization may refresh state, but must not duplicate the controller.
vm.runInContext(`
  const state = { searchScope: "all", searchExperience: "precise", searchMode: "item" };
  const api = async route => {
    if (route === "/api/desktop/evidence-packages") return { active: false };
    if (route === "/api/desktop/credentials/deepseek") return { configured: false };
    if (route === "/api/desktop/personal-imports/search-status") {
      return { state: "empty", ready: false, document_count: 0 };
    }
    throw new Error("unexpected route: " + route);
  };
  const toast = () => {};
  const esc = value => String(value);
  const paperMatchesFilters = () => true;
  const switchView = () => {};
  const waitForJob = async () => {};
  globalThis.__runtimePromise = (async () => {
    await globalThis.AutoResearchDesktopProduct.initialize();
    await globalThis.AutoResearchDesktopProduct.initialize();
  })();
`, context);
context.__runtimePromise.then(() => {
  assert.strictEqual(createCalls, 1);
  assert.strictEqual(initCalls, 1);
  assert.strictEqual(element("personal-import-panel").parentElement, element("view-personal"));
}).catch(error => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            ["node", "-e", script, str(DESKTOP_PRODUCT_JS)],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_personal_import_mount_is_not_blocked_by_package_center_failure(self) -> None:
        script = r"""
const fs = require("fs");
const assert = require("assert");
const vm = require("vm");

const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      id, hidden: true, disabled: false, value: "", checked: false,
      textContent: "", innerHTML: "", dataset: {}, parentElement: null,
      classList: { toggle() {}, add() {}, remove() {} },
      addEventListener() {}, setAttribute() {}, toggleAttribute() {},
      appendChild(child) { child.parentElement = this; },
      querySelector() { return null; }, querySelectorAll() { return []; },
    });
  }
  return elements.get(id);
}

const context = vm.createContext({
  console,
  document: {
    body: { dataset: { view: "personal" } },
    getElementById: element,
    querySelector() { return null; },
    querySelectorAll() { return []; },
  },
  window: {},
});
context.AutoResearchPackageCenter = {
  create() { throw new Error("package center unavailable"); },
};
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
vm.runInContext(`
  const state = { searchScope: "all", searchExperience: "precise", searchMode: "item" };
  const api = async route => {
    if (route === "/api/desktop/evidence-packages") return { active: false };
    if (route === "/api/desktop/credentials/deepseek") return { configured: false };
    if (route === "/api/desktop/personal-imports/search-status") return { state: "empty", ready: false, document_count: 0 };
    throw new Error("unexpected route: " + route);
  };
  const toast = () => {};
  const esc = value => String(value);
  const paperMatchesFilters = () => true;
  const switchView = () => {};
  const waitForJob = async () => {};
  globalThis.__runtimePromise = globalThis.AutoResearchDesktopProduct.initialize();
`, context);
context.__runtimePromise.then(() => {
  const panel = element("personal-import-panel");
  assert.strictEqual(panel.parentElement, element("view-personal"));
  context.AutoResearchDesktopProduct.openPersonalImport();
  assert.strictEqual(panel.hidden, false);
}).catch(error => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            ["node", "-e", script, str(DESKTOP_PRODUCT_JS)],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_cancelled_risk_confirmation_sends_no_export_request_and_full_ack_is_sent_once(self) -> None:
        script = r"""
const fs = require("fs");
const assert = require("assert");

const elements = new Map();
const literatureRight = {
  checked: true,
  dataset: { packageRightsPaper: "paper_uid_1" },
  addEventListener() {},
};
function element(id) {
  if (!elements.has(id)) {
    const handlers = {};
    elements.set(id, {
      id, handlers, hidden: false, disabled: false, value: "", checked: false,
      innerHTML: "", dataset: {}, parentElement: null,
      addEventListener(type, handler) { handlers[type] = handler; },
      appendChild(child) { child.parentElement = this; },
      querySelector() { return null; },
      insertAdjacentHTML() {},
    });
  }
  return elements.get(id);
}

let confirmResult = false;
let destinationCalls = 0;
const apiCalls = [];
global.window = { confirm: () => confirmResult };
eval(fs.readFileSync(process.argv[1], "utf8"));

const ports = {
  api: async (route, options = {}) => {
    apiCalls.push({ route, options });
    if (route.endsWith("/export-plan")) {
      const request = JSON.parse(options.body);
      return {
        plan_token: request.kind === "literature_collection" ? "literature-plan" : "personal-plan",
        paper_count: request.kind === "literature_collection" ? 1 : 0, item_count: 1,
        estimated_bytes: 128, missing_pdf_count: 0,
        rights_requirements: request.kind === "literature_collection"
          ? [{ paper_uid: "paper_uid_1", title: "Test paper" }]
          : [],
        expires_in_seconds: 60,
        exceeds_size_limit: false,
      };
    }
    if (route.endsWith("/export")) {
      return { job_id: "job-1", stage: "completed", terminal: true, progress: 100, result: {} };
    }
    throw new Error(`unexpected route: ${route}`);
  },
  toast() {},
  esc: value => String(value),
  el: element,
  query: selector => selector === 'input[name="literature-scope"]:checked'
    ? { value: "selected" }
    : null,
  queryAll: selector => selector === "[data-package-rights-paper]"
    ? [literatureRight]
    : [],
  getLiteratureSnapshot: () => ({ selectedIds: ["1"], filter: {}, paperCount: 1 }),
  getOfficialStatus: () => ({}),
  officialReady: () => false,
  refreshOfficialStatus: async () => {},
  refreshPrivateStatus: async () => {},
  runOfficialRollback: async () => {},
  selectEvidencePackage: async () => ({ cancelled: true }),
  selectExportDestination: async () => {
    destinationCalls += 1;
    return { ok: true, destination: { destination_token: "destination-token" } };
  },
};

(async () => {
  const controller = globalThis.AutoResearchPackageCenter.create(ports);
  controller.init();
  await element("package-personal-plan").handlers.click();
  const callsAfterPlan = apiCalls.length;

  confirmResult = false;
  element("package-personal-export").handlers.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.strictEqual(apiCalls.length, callsAfterPlan);
  assert.strictEqual(destinationCalls, 0);

  confirmResult = true;
  element("package-personal-export").handlers.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.strictEqual(destinationCalls, 1);
  const exportCall = apiCalls.find(call => call.route.endsWith("/export"));
  assert(exportCall);
  const body = JSON.parse(exportCall.options.body);
  assert.deepStrictEqual(body.rights_confirmations, {
    unencrypted_ack: true,
    unauthenticated_source_ack: true,
    internal_use_only_ack: true,
    paper_rights: {},
  });

  await element("package-literature-plan-form").handlers.submit({ preventDefault() {} });
  element("package-literature-export").handlers.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.strictEqual(destinationCalls, 2);
  const literatureExport = apiCalls.filter(call => call.route.endsWith("/export"))[1];
  assert(literatureExport);
  const literatureBody = JSON.parse(literatureExport.options.body);
  assert.deepStrictEqual(literatureBody.rights_confirmations, {
    unencrypted_ack: true,
    unauthenticated_source_ack: true,
    internal_use_only_ack: true,
    paper_rights: {
      paper_uid_1: {
        allowed: true,
        basis: "用户逐篇确认具有课题组内部分享权限",
      },
    },
  });
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            ["node", "-e", script, str(PACKAGE_CENTER_JS)],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


if __name__ == "__main__":
    unittest.main()
