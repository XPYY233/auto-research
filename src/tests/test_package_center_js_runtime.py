from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_CENTER_JS = (
    PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web" / "package_center.js"
)


class PackageCenterJavaScriptRuntimeTests(unittest.TestCase):
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
