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
assert.strictEqual(prompts.length, 1);
assert.strictEqual(context.AutoResearchAIConsent.ensure("unknown"), false);
assert.strictEqual(prompts.length, 1);
"""
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_each_scope_requires_one_versioned_acceptance(self) -> None:
        completed = self._run(
            """
context.__accept = true;
for (const scope of ["librarian", "literature_extraction", "personal_suggestion"]) {
  assert.strictEqual(context.AutoResearchAIConsent.ensure(scope), true);
  assert.strictEqual(context.AutoResearchAIConsent.ensure(scope), true);
}
assert.strictEqual(prompts.length, 3);
const saved = JSON.parse(values.get("auto-research-ai-consent-v1"));
assert.strictEqual(saved.schema, "auto-research-ai-consent-v1");
assert.deepStrictEqual([...saved.accepted_scopes].sort(), ["librarian", "literature_extraction", "personal_suggestion"]);
for (const message of prompts) {
  assert(message.includes("DeepSeek"));
  assert(message.includes("可能产生少量 API 费用"));
  assert(message.includes("安全凭据存储"));
  assert(message.includes("不会发送本机文件路径"));
}
assert(prompts.some(message => message.includes("研究问题")));
assert(prompts.some(message => message.includes("当前论文 PDF")));
assert(prompts.some(message => message.includes("最多 5 行样例")));
"""
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


if __name__ == "__main__":
    unittest.main()
