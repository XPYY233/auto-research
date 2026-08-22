from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import urllib.error
import urllib.request
import unittest
from unittest import mock

from auto_research.ai.business_actions import HarnessBudgetedBusinessAIClient
from auto_research.ai.harness_contract import (
    CORDIS_RUNTIME_PROTOCOL_PIN,
    HARNESS_SDK_PROTOCOL_PIN,
    HarnessDependencyMetadata,
    HarnessDependencySet,
    HarnessError,
)
from auto_research.ai.harness_official_sdk import (
    OfficialDeepSeekHarnessRuntime,
    _verified_runtime_member,
)
from auto_research.ai.harness_tools import HarnessToolGateway
from auto_research.ai.prepared_actions import PreparedOutbound

from src.tests.test_harness_tools import Backend, job


def dependencies():
    return HarnessDependencySet(
        HarnessDependencyMetadata.from_pin(HARNESS_SDK_PROTOCOL_PIN),
        HarnessDependencyMetadata.from_pin(CORDIS_RUNTIME_PROTOCOL_PIN),
        "2.12.0",
    )


def action():
    value = job()
    call = {
        "method": "json",
        "task": value.task,
        "messages": [{"role": "user", "content": "consent summary"}],
        "tools": [],
        "options": {"thinking": None, "temperature": 0.1},
        "max_tokens": 100,
    }
    return PreparedOutbound(
        action_id="action-1",
        session_digest=hashlib.sha256(b"session").hexdigest(),
        scope="librarian",
        provider_id="deepseek",
        runtime_revision=1,
        credential_generation=1,
        runtime_activation="legacy_compatible",
        runtime_task_models=((value.task, value.model),),
        task=value.task,
        task_models=((value.task, value.model),),
        models=(value.model,),
        executor_id="harness-test",
        executor_version="v1",
        estimated_calls=1,
        max_calls=2,
        max_tokens=2_000,
        outbound={"call_plan": [call]},
        outbound_digest=hashlib.sha256(b"outbound").hexdigest(),
        manifest_digest=hashlib.sha256(b"manifest").hexdigest(),
        units=(),
        byte_count=100,
        issued_at=10,
        expires_at=100,
    )


class RawClient:
    def __init__(self):
        self.calls = []

    def request_tool_message(self, messages, tools, **kwargs):
        self.calls.append((messages, tools, kwargs))
        return {"role": "assistant", "content": "bounded", "tool_calls": []}


def post(url, token, value):
    request = urllib.request.Request(
        url,
        data=json.dumps(value).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, response.read(), response.headers.get("Content-Type")


class FakeHarness:
    latest = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.__class__.latest = self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def run(self, prompt, *, session_id):
        token = self.kwargs["api_key"]
        mcp = self.kwargs["env"]["AUTO_RESEARCH_HARNESS_MCP_URL"]
        status, raw, _ = post(
            mcp,
            token,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            },
        )
        assert status == 200 and json.loads(raw)["result"]["serverInfo"]["name"] == "auto-research"
        post(
            mcp,
            token,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "citation_verify", "arguments": {"refs": ["R1"]}}},
        )
        post(
            mcp,
            token,
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "recommend_papers", "arguments": {"question": "硬度", "limit": 3}}},
        )
        tools = [{
            "type": "function",
            "function": {
                "name": "mcp__auto_research__exact_search",
                "description": "bounded",
                "parameters": {"type": "object", "additionalProperties": False},
            },
        }]
        status, raw, content_type = post(
            self.kwargs["base_url"] + "/chat/completions",
            token,
            {
                "model": self.kwargs["model"],
                "messages": [{"role": "user", "content": "derived"}],
                "tools": tools,
                "max_tokens": 500,
                "temperature": 0.1,
                "stream": False,
            },
        )
        assert status == 200 and content_type.startswith("application/json")
        assert json.loads(raw)["choices"][0]["message"]["content"] == "bounded"
        final = {
            "schema_version": "librarian-harness-result-v1",
            "answer": "有证据支持。",
            "report": {
                "direct_conclusion": "有证据支持。",
                "evidence_matrix": [{"ref": "R1"}],
                "related_evidence": [],
                "database_gaps": "仍需人工核验。",
                "suggested_followups": ["核对条件"],
            },
            "citations": [{"ref": "R1"}],
            "recommended_articles": [
                {"paper_uid": "paper-1", "title": "Paper", "doi": "", "reason": "相关"}
            ],
            "comparison_bundle_uids": ["bundle-1"],
        }
        assert "高亮" in prompt
        return SimpleNamespace(
            final_response=json.dumps(final, ensure_ascii=False),
            finish_reason="completed",
            session_root=None,
        )


class BudgetExhaustingHarness(FakeHarness):
    def run(self, _prompt, *, session_id):
        del session_id
        token = self.kwargs["api_key"]
        tools = [{
            "type": "function",
            "function": {
                "name": "mcp__auto_research__exact_search",
                "description": "bounded",
                "parameters": {"type": "object", "additionalProperties": False},
            },
        }]
        for index in range(3):
            try:
                post(
                    self.kwargs["base_url"] + "/chat/completions",
                    token,
                    {
                        "model": self.kwargs["model"],
                        "messages": [{"role": "user", "content": f"derived-{index}"}],
                        "tools": tools,
                        "max_tokens": self.kwargs["max_tokens"],
                        "temperature": 0.1,
                        "stream": False,
                    },
                )
            except urllib.error.HTTPError as exc:
                exc.close()
                raise
        raise AssertionError("budget gate did not stop the third provider request")


class OfficialHarnessSDKTests(unittest.TestCase):
    def test_frozen_bundle_accepts_only_verified_in_bundle_runtime_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            contents = Path(directory) / "Auto Research.app" / "Contents"
            executable = contents / "MacOS" / "Auto Research"
            target = contents / "Frameworks" / "runtime" / "dsh"
            link = contents / "Resources" / "runtime" / "dsh"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"launcher")
            target.parent.mkdir(parents=True)
            target.write_bytes(b"verified-runtime")
            link.parent.mkdir(parents=True)
            link.symlink_to(target)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            with (
                mock.patch("auto_research.ai.harness_official_sdk.sys.frozen", True, create=True),
                mock.patch("auto_research.ai.harness_official_sdk.sys.executable", str(executable)),
            ):
                self.assertEqual(_verified_runtime_member(link, digest), target.resolve())

            target.write_bytes(b"pyinstaller-resigned-runtime")
            frozen_digest = hashlib.sha256(target.read_bytes()).hexdigest()
            with (
                mock.patch("auto_research.ai.harness_official_sdk.sys.frozen", True, create=True),
                mock.patch("auto_research.ai.harness_official_sdk.sys.executable", str(executable)),
            ):
                self.assertEqual(
                    _verified_runtime_member(
                        link,
                        digest,
                        frozen_sha256=frozen_digest,
                    ),
                    target.resolve(),
                )

            with (
                mock.patch("auto_research.ai.harness_official_sdk.sys.frozen", True, create=True),
                mock.patch("auto_research.ai.harness_official_sdk.sys.executable", str(executable)),
            ):
                self.assertEqual(
                    _verified_runtime_member(
                        target,
                        digest,
                        frozen_sha256=frozen_digest,
                    ),
                    target.resolve(),
                )

            outside = Path(directory) / "outside-runtime"
            outside.write_bytes(target.read_bytes())
            link.unlink()
            link.symlink_to(outside)
            with (
                mock.patch("auto_research.ai.harness_official_sdk.sys.frozen", True, create=True),
                mock.patch("auto_research.ai.harness_official_sdk.sys.executable", str(executable)),
                self.assertRaises(HarnessError),
            ):
                _verified_runtime_member(
                    link,
                    digest,
                    frozen_sha256=frozen_digest,
                )

    def test_exact_runtime_uses_authenticated_proxy_and_mcp_without_real_key(self):
        prepared = action()
        raw = RawClient()
        model = HarnessBudgetedBusinessAIClient(client=raw, action=prepared)
        gateway = HarnessToolGateway(backend=Backend(), job=job(), allow_source_view=True)
        runtime = OfficialDeepSeekHarnessRuntime(
            cordis_path="config/auto-research-harness.runtime.cordis.yml",
            harness_factory=FakeHarness,
            dependency_resolver=dependencies,
            runtime_path_resolver=lambda: "/verified/runtime",
        )
        result = runtime.execute(
            job=job(),
            model=model,
            tools=gateway,
            prompt={"question": "硬度如何变化？", "instruction": "核对原文高亮"},
        )
        self.assertEqual(result["citations"], [{"ref": "R1"}])
        self.assertEqual(len(raw.calls), 1)
        self.assertEqual(model.remaining_calls, 1)
        self.assertEqual(FakeHarness.latest.kwargs["max_tokens"], 1_000)
        self.assertIn("第六轮必须输出最终 JSON", FakeHarness.latest.kwargs["env"]["AUTO_RESEARCH_HARNESS_SYSTEM_PROMPT"])
        self.assertNotIn("real-key", repr(FakeHarness.latest.kwargs))
        self.assertIsNone(FakeHarness.latest.kwargs["session_root"])

    def test_budget_exhaustion_preserves_stable_failure_code(self):
        prepared = action()
        raw = RawClient()
        model = HarnessBudgetedBusinessAIClient(client=raw, action=prepared)
        gateway = HarnessToolGateway(backend=Backend(), job=job(), allow_source_view=True)
        runtime = OfficialDeepSeekHarnessRuntime(
            cordis_path="config/auto-research-harness.runtime.cordis.yml",
            harness_factory=BudgetExhaustingHarness,
            dependency_resolver=dependencies,
            runtime_path_resolver=lambda: "/verified/runtime",
        )
        with self.assertRaises(HarnessError) as failed:
            runtime.execute(
                job=job(),
                model=model,
                tools=gateway,
                prompt={"question": "硬度如何变化？"},
            )
        self.assertEqual(failed.exception.code, "harness_budget_exhausted")
        self.assertEqual(len(raw.calls), 2)

    def test_checked_in_composition_has_only_four_runtime_rows(self):
        text = Path("config/auto-research-harness.runtime.cordis.yml").read_text()
        self.assertEqual(text.count("\n- id:"), 4)
        self.assertIn("@deepseek-ai/dsh-sdk-jsonrpc-server", text)
        self.assertIn("@deepseek-ai/dsh-llm-deepseek", text)
        self.assertIn("@deepseek-ai/dsh-agent-spine-demo", text)
        self.assertIn("@deepseek-ai/dsh-mcp-client", text)
        for forbidden in (
            "name: '@deepseek-ai/dsh-terminal",
            "name: '@deepseek-ai/dsh-fs-",
            "name: '@deepseek-ai/dsh-tool-bash",
            "name: '@deepseek-ai/dsh-tool-str-replace-editor",
            "name: '@deepseek-ai/dsh-subagent",
            "name: '@deepseek-ai/dsh-session-persistence-jsonl",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
