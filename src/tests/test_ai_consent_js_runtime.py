from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AI_CONSENT_JS = (
    PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web" / "ai_consent.js"
)


class AIConsentJavaScriptRuntimeTests(unittest.TestCase):
    def _run(self, assertions: str) -> subprocess.CompletedProcess[str]:
        script = rf"""
const fs = require("fs");
const assert = require("assert");
const vm = require("vm");
const values = new Map();
const prompts = [];
const context = vm.createContext({{
  console,
  localStorage: {{
    getItem(key) {{ return values.has(key) ? values.get(key) : null; }},
    setItem(key, value) {{ values.set(key, String(value)); }},
  }},
  confirm(message) {{ prompts.push(message); return context.__accept === true; }},
  __accept: false,
}});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
{assertions}
"""
        return subprocess.run(
            ["node", "-e", script, str(AI_CONSENT_JS)],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_cancel_does_not_persist_and_unknown_scope_fails_closed(self) -> None:
        completed = self._run(
            """
assert.strictEqual(context.AutoResearchAIConsent.ensure("personal_suggestion"), false);
assert.strictEqual(values.size, 0);
assert.strictEqual(prompts.length, 0);
assert.strictEqual(context.AutoResearchAIConsent.ensure("unknown"), false);
assert.strictEqual(prompts.length, 0);
"""
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_each_scope_requires_one_versioned_acceptance(self) -> None:
        completed = self._run(
            """
context.__accept = true;
context.AutoResearchAIConsent.updateTrustedProviders([{provider_id: "openai", display_name: "OpenAI"}]);
for (const scope of ["librarian", "literature_extraction", "personal_suggestion", "selected_evidence_chat"]) {
  const disclosure_version = context.AutoResearchAIConsent.disclosureVersions[scope];
  const info = {provider_id: "openai", label: "OpenAI", disclosure_version};
  assert.strictEqual(context.AutoResearchAIConsent.ensure(scope, info), true);
  assert.strictEqual(context.AutoResearchAIConsent.ensure(scope, info), true);
}
assert.strictEqual(prompts.length, 4);
const saved = JSON.parse(values.get("auto-research-ai-consent-v2"));
assert.strictEqual(saved.schema, "auto-research-ai-consent-v2");
assert.strictEqual(saved.accepted_disclosures.length, 4);
for (const message of prompts) {
  assert(message.includes("OpenAI"));
  assert(message.includes("可能产生少量 API 费用"));
  assert(message.includes("安全凭据存储"));
  assert(message.includes("不会发送本机文件路径"));
}
assert(prompts.some(message => message.includes("研究问题")));
assert(prompts.some(message => message.includes("当前论文 PDF")));
assert(prompts.some(message => message.includes("最多 5 行样例")));
assert(prompts.some(message => message.includes("当前选中的一条证据")));
const other = {provider_id: "openai", label: "伪造名称", disclosure_version: context.AutoResearchAIConsent.disclosureVersions.librarian};
assert.strictEqual(context.AutoResearchAIConsent.ensure("librarian", other), false);
"""
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_renderer_can_merge_disclosure_and_cost_confirmation(self) -> None:
        completed = self._run(
            """
context.AutoResearchAIConsent.updateTrustedProviders([{provider_id: "openai", display_name: "OpenAI"}]);
const scope = "personal_suggestion";
const info = {provider_id: "openai", label: "OpenAI", disclosure_version: context.AutoResearchAIConsent.disclosureVersions[scope]};
assert.strictEqual(context.AutoResearchAIConsent.accepted(scope, info), false);
const summary = context.AutoResearchAIConsent.disclosureSummary(scope, info);
assert(summary.includes("最多 5 行样例"));
assert(!summary.includes("是否继续"));
assert.strictEqual(context.AutoResearchAIConsent.remember(scope, info), true);
assert.strictEqual(context.AutoResearchAIConsent.accepted(scope, info), true);
assert.strictEqual(prompts.length, 0, "accepted/remember never open a second confirmation dialog");
assert.strictEqual(context.AutoResearchAIConsent.remember("unknown", info), false);
"""
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


if __name__ == "__main__":
    unittest.main()
