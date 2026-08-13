from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_JS = PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web" / "app.js"


class DesktopAIActionPortsRuntimeTests(unittest.TestCase):
    def test_routes_bodies_csrf_and_unknown_scope_zero_request(self) -> None:
        script = r'''
const fs = require("fs");
const assert = require("assert");
const source = fs.readFileSync(process.argv[1], "utf8");
const csrf = source.indexOf('const desktopCsrfHeader');
const scopes = source.indexOf('const AI_ACTION_SCOPES');
const beforeAuthorization = source.indexOf('async function authorizePreparedAIAction');
const actionPorts = source.indexOf('function invalidAIActionRequest');
const actionPortsEnd = source.indexOf('globalThis.AutoResearchDesktopPorts = Object.freeze');
const apiStart = source.indexOf('async function api(');
const apiEnd = source.indexOf('async function apiOptional');
const portSource = source.slice(csrf, scopes) + source.slice(scopes, beforeAuthorization) + source.slice(actionPorts, actionPortsEnd) + source.slice(apiStart, apiEnd);
const requests = [];
globalThis.Headers = class {
  constructor(values = {}) { this.values = {...values}; }
  set(name, value) { this.values[name] = value; }
  get(name) { return this.values[name] || null; }
};
globalThis.fetch = async (url, options = {}) => {
  requests.push({url, options});
  return {
    ok: true,
    status: 200,
    headers: {get: name => name === "X-Auto-Research-CSRF" ? "csrf-next" : null},
    json: async () => ({ok: true}),
  };
};
eval(portSource + `\n(async () => {
  desktopCsrfToken = "csrf-current";
  await prepareAIAction("personal_suggestion", {import_id: "i", sheet_index: 0});
  await issuePreparedConsent("action-1");
  await executePreparedAIAction("selected_evidence_chat", "action-2", "nonce-2");
  assert.deepStrictEqual(requests.map(row => row.url), [
    "/api/desktop/ai/actions/personal_suggestion/prepare",
    "/api/desktop/ai/consents",
    "/api/desktop/ai/actions/selected_evidence_chat/execute",
  ]);
  assert.deepStrictEqual(JSON.parse(requests[0].options.body), {import_id: "i", sheet_index: 0});
  assert.deepStrictEqual(JSON.parse(requests[1].options.body), {action_id: "action-1"});
  assert.deepStrictEqual(JSON.parse(requests[2].options.body), {action_id: "action-2", consent_nonce: "nonce-2"});
  for (const [index, request] of requests.entries()) {
    assert.strictEqual(request.options.method, "POST");
    assert.strictEqual(request.options.headers.values["X-Auto-Research-CSRF"], index === 0 ? "csrf-current" : "csrf-next");
    assert.strictEqual(request.options.headers.values["Content-Type"], "application/json");
  }
  const before = requests.length;
  await assert.rejects(() => prepareAIAction("../../escape", {}), error => error.code === "ai_action_request_invalid");
  await assert.rejects(() => executePreparedAIAction("unknown", "action", "nonce"), error => error.code === "ai_action_request_invalid");
  assert.strictEqual(requests.length, before);
})().catch(error => { console.error(error); process.exit(1); });`);
'''
        completed = subprocess.run(
            ["node", "-e", script, str(APP_JS)],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


if __name__ == "__main__":
    unittest.main()
