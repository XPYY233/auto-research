from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_JS = PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web" / "app.js"


class LiteraturePreparedWorkflowRuntimeTests(unittest.TestCase):
    def test_multistage_cancel_and_commit_are_bounded(self) -> None:
        script = r'''
const fs = require("fs");
const assert = require("assert");
const source = fs.readFileSync(process.argv[1], "utf8");
const start = source.indexOf("const literatureStageKeys");
const end = source.indexOf("async function runCurrentExtraction");
const nodes = new Map();
globalThis.document = {querySelector: selector => {
  if (!nodes.has(selector)) nodes.set(selector, {hidden: true, textContent: ""});
  return nodes.get(selector);
}};
globalThis.setText = (id, value) => { document.querySelector(`#${id}`).textContent = String(value); };
globalThis.AI_ACTION_CALL_LIMITS = {literature_extraction: 512};
let authorizations = [];
let executes = [];
globalThis.authorizePreparedAIAction = async (_domain, _scope, request, detail) => {
  authorizations.push({request, detail});
  return authorizations.length === 2 && globalThis.cancelSecond ? null : {action_id: `a${authorizations.length}`, consent_nonce: `n${authorizations.length}`};
};
globalThis.AutoResearchDesktopPorts = {executePreparedAIAction: async (_scope, action, nonce) => {
  executes.push({action, nonce});
  return executes.length === 1 ? {
    schema_version: "literature-extraction-stage-summary-v1", job_token: "opaque-token", stage: "coverage_gap",
    paper: {title: "Paper", doi: null}, call_count: 2, max_token_budget: 32000,
    sending_scope: {pdf_page_count: 8, page_block_count: 4, branch_count: 2, focus_count: 1},
    possible_charges: true, requires_confirmation: true, expires_at: 1, transient: true, persistence_allowed: false,
  } : {
    schema_version: "literature-extraction-commit-result-v1", status: "completed", paper: {title: "Paper", doi: null},
    candidate_count: 3, published_item_count: 2, existing_item_count: 1, manual_review_count: 0,
    visual_evidence_ready: false, idempotent: false,
  };
}};
eval(source.slice(start, end));
(async () => {
  literatureWorkflowGeneration = 1;
  globalThis.cancelSecond = true;
  let result = await runPreparedLiteratureWorkflow({paper_id: 7, force_rescan: false}, 1);
  assert.deepStrictEqual(result, {cancelled: true});
  assert.strictEqual(executes.length, 1);
  assert.deepStrictEqual(authorizations[1].request, {job_token: "opaque-token"});
  assert(authorizations[1].detail.includes("32,000 tokens"));
  assert(!document.querySelector("#literature-ai-stage").textContent.includes("opaque-token"));

  authorizations = []; executes = []; globalThis.cancelSecond = false; literatureWorkflowGeneration = 2;
  result = await runPreparedLiteratureWorkflow({paper_id: 7, force_rescan: true}, 2);
  assert.strictEqual(result.commit.schema_version, "literature-extraction-commit-result-v1");
  assert.strictEqual(executes.length, 2);
})().catch(error => { console.error(error); process.exit(1); });
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
