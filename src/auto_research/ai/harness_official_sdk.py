"""Pinned DeepSeek Harness SDK runtime behind an authenticated loopback broker.

The official runtime never receives a provider credential or arbitrary network
tool.  It talks to two one-job loopback endpoints instead:

* an OpenAI-compatible proxy backed by the consumed action's cumulative budget;
* a stateless MCP endpoint backed by :class:`HarnessToolGateway`.

No Harness session directory is configured, so the Developer Preview runtime
cannot persist plaintext model-visible events.  Product history remains owned
by the platform encrypted history service.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Mapping

from .business_actions import HarnessBudgetedBusinessAIClient
from .harness_contract import (
    CORDIS_RUNTIME_PROTOCOL_PIN,
    HARNESS_SDK_PROTOCOL_PIN,
    HarnessDependencyMetadata,
    HarnessDependencySet,
    HarnessError,
    HarnessJobV1,
    canonical_public,
)
from .harness_tools import HarnessToolGateway


RUNTIME_BINARY_SHA256 = "8f8014cc519e9c9df50c84714aed35ca417e8467b41401cf925fbb05341adf02"
RUNTIME_RG_SHA256 = "6ef40346bf31fcce79d9614c7745c198542925a0c7d4911e1ffe794c53392ac1"
RUNTIME_SPAWN_HELPER_SHA256 = "08bc83a084651d095645010d34783f94b9af443614747e953e66d6578027e999"
# PyInstaller re-signs the two small Mach-O helpers while assembling the App.
# These deterministic digests are for that exact pinned 0.1.1rc1 transformation;
# accepting them is limited to an in-bundle symlink below.
FROZEN_RUNTIME_RG_SHA256 = "f8c49e473e4cf61700e3808a5eb254b1662386bd40498c66e503638692144852"
FROZEN_RUNTIME_SPAWN_HELPER_SHA256 = "9d41c4cbfd7407963a4ba244ebe2cba189a47dca18eeb90d4659e9ece5187414"
MAX_PROVIDER_BODY_BYTES = 4 * 1024 * 1024
MAX_MCP_BODY_BYTES = 512 * 1024
MAX_FINAL_RESPONSE_BYTES = 1024 * 1024
_ENVIRONMENT_LOCK = threading.RLock()
_SAFE_ENVIRONMENT_KEYS = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "LOGNAME",
        "PATH",
        "SHELL",
        "TMPDIR",
        "TZ",
        "USER",
        "__CFBundleIdentifier",
    }
)
_PROVIDER_REQUEST_KEYS = frozenset(
    {
        "model",
        "messages",
        "tools",
        "tool_choice",
        "max_tokens",
        "max_completion_tokens",
        "temperature",
        "stream",
        "stream_options",
        "thinking",
        "reasoning_effort",
    }
)
_SAFE_TRACE_ENABLED = os.environ.get("AUTO_RESEARCH_AI_SAFE_TRACE") == "1"


def _safe_trace(event: str, **metadata: object) -> None:
    """Emit opt-in structural diagnostics without content, credentials, or paths."""

    if not _SAFE_TRACE_ENABLED:
        return
    safe: dict[str, object] = {"event": event}
    for key, value in metadata.items():
        if isinstance(value, (str, int, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, (list, tuple)) and all(
            isinstance(item, str) for item in value
        ):
            safe[key] = list(value)
    print(
        "AUTO_RESEARCH_AI_SAFE_TRACE "
        + json.dumps(safe, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        file=sys.stderr,
        flush=True,
    )


def safe_composition_metadata() -> dict[str, object]:
    return {
        "schema_version": "auto-research-cordis-composition-v1",
        "plugins": ["auto-research-domain-tools", "auto-research-model-port"],
        "capabilities": {
            "bash": False,
            "editor": False,
            "filesystem": False,
            "pty": False,
            "subagent": False,
            "arbitrary_network": False,
            "credential_access": False,
            "local_path_access": False,
        },
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_runtime_member(
    path: Path,
    expected_sha256: str,
    *,
    frozen_sha256: str | None = None,
) -> Path:
    """Resolve PyInstaller's signed in-bundle symlink, rejecting all others."""

    candidate = path
    try:
        expected = expected_sha256
        if path.is_symlink():
            if getattr(sys, "frozen", False) is not True:
                raise HarnessError("harness_dependency_mismatch")
            candidate = path.resolve(strict=True)
            contents = Path(sys.executable).resolve().parent.parent
            try:
                candidate.relative_to(contents)
            except ValueError as exc:
                raise HarnessError("harness_dependency_mismatch") from exc
            if frozen_sha256 is not None:
                expected = frozen_sha256
        if (
            not candidate.is_file()
            or candidate.is_symlink()
            or _sha256_file(candidate) != expected
        ):
            raise HarnessError("harness_dependency_mismatch")
    except HarnessError:
        raise
    except OSError as exc:
        raise HarnessError("harness_dependency_mismatch") from exc
    return candidate


def _default_dependency_set() -> HarnessDependencySet:
    try:
        sdk_version = metadata.version(HARNESS_SDK_PROTOCOL_PIN[0])
        runtime_version = metadata.version(CORDIS_RUNTIME_PROTOCOL_PIN[0])
        pydantic_version = metadata.version("pydantic")
    except metadata.PackageNotFoundError as exc:
        raise HarnessError("harness_runtime_unavailable") from exc
    result = HarnessDependencySet(
        HarnessDependencyMetadata(
            HARNESS_SDK_PROTOCOL_PIN[0],
            sdk_version,
            HARNESS_SDK_PROTOCOL_PIN[2],
            HARNESS_SDK_PROTOCOL_PIN[3],
            HARNESS_SDK_PROTOCOL_PIN[4],
        ),
        HarnessDependencyMetadata(
            CORDIS_RUNTIME_PROTOCOL_PIN[0],
            runtime_version,
            CORDIS_RUNTIME_PROTOCOL_PIN[2],
            CORDIS_RUNTIME_PROTOCOL_PIN[3],
            CORDIS_RUNTIME_PROTOCOL_PIN[4],
        ),
        pydantic_version,
    )
    result.verify_production_protocols()
    return result


def _default_runtime_binary() -> str:
    try:
        from deepseek_harness_runtime import bundled_runtime_path

        binary = Path(bundled_runtime_path())
    except Exception as exc:
        raise HarnessError("harness_runtime_unavailable") from exc
    members = (
        (binary, RUNTIME_BINARY_SHA256, None),
        (Path(f"{binary}-rg"), RUNTIME_RG_SHA256, FROZEN_RUNTIME_RG_SHA256),
        (
            Path(f"{binary}-spawn-helper"),
            RUNTIME_SPAWN_HELPER_SHA256,
            FROZEN_RUNTIME_SPAWN_HELPER_SHA256,
        ),
    )
    verified = tuple(
        _verified_runtime_member(path, expected, frozen_sha256=frozen)
        for path, expected, frozen in members
    )
    return str(verified[0])


@contextmanager
def _scrubbed_environment(extra: Mapping[str, str]):
    """Limit the official SDK child's inherited environment during spawn."""

    with _ENVIRONMENT_LOCK:
        original = dict(os.environ)
        clean = {
            key: value
            for key, value in original.items()
            if key in _SAFE_ENVIRONMENT_KEYS and isinstance(value, str)
        }
        clean.update(extra)
        os.environ.clear()
        os.environ.update(clean)
        try:
            yield
        finally:
            os.environ.clear()
            os.environ.update(original)


class _Bridge:
    def __init__(
        self,
        *,
        job: HarnessJobV1,
        model: HarnessBudgetedBusinessAIClient,
        tools: HarnessToolGateway,
    ) -> None:
        if not isinstance(model, HarnessBudgetedBusinessAIClient):
            raise HarnessError("harness_invalid")
        self.job = job
        self.model = model
        self.tools = tools
        self.token = secrets.token_urlsafe(32)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_Bridge":
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_GET(self) -> None:
                self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self) -> None:
                try:
                    if not bridge._authorized(self):
                        return bridge._error(self, HTTPStatus.FORBIDDEN)
                    if self.path == "/v1/chat/completions":
                        return bridge._provider(self)
                    if self.path == "/mcp":
                        return bridge._mcp(self)
                    return bridge._error(self, HTTPStatus.NOT_FOUND)
                except HarnessError as exc:
                    bridge._trace_handler_failure(self.path, exc)
                    return bridge._error(self, HTTPStatus.BAD_REQUEST)
                except Exception:
                    return bridge._error(self, HTTPStatus.INTERNAL_SERVER_ERROR)

        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            self._server.daemon_threads = True
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="auto-research-harness-loopback",
                daemon=True,
            )
            self._thread.start()
        except OSError as exc:
            raise HarnessError("harness_runtime_unavailable") from exc
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    @property
    def authority(self) -> str:
        if self._server is None:
            raise HarnessError("harness_runtime_unavailable")
        return f"127.0.0.1:{self._server.server_address[1]}"

    def _authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        hosts = handler.headers.get_all("Host") or []
        auth = handler.headers.get_all("Authorization") or []
        return (
            len(hosts) == 1
            and hosts[0] == self.authority
            and len(auth) == 1
            and secrets.compare_digest(auth[0], f"Bearer {self.token}")
            and not handler.headers.get_all("Transfer-Encoding")
        )

    @staticmethod
    def _body(handler: BaseHTTPRequestHandler, cap: int) -> dict[str, Any]:
        values = handler.headers.get_all("Content-Length") or []
        types = handler.headers.get_all("Content-Type") or []
        if (
            len(values) != 1
            or not values[0].isascii()
            or not values[0].isdigit()
            or len(types) != 1
            or types[0].split(";", 1)[0].strip().casefold() != "application/json"
        ):
            raise HarnessError("harness_invalid")
        length = int(values[0])
        if not 1 <= length <= cap:
            raise HarnessError("harness_invalid")
        raw = handler.rfile.read(length)
        if len(raw) != length:
            raise HarnessError("harness_invalid")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HarnessError("harness_invalid") from exc
        if not isinstance(value, dict):
            raise HarnessError("harness_invalid")
        return value

    @staticmethod
    def _json(handler: BaseHTTPRequestHandler, value: object, status=HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    @staticmethod
    def _error(handler: BaseHTTPRequestHandler, status: HTTPStatus) -> None:
        handler.close_connection = True
        _Bridge._json(
            handler,
            {"error": {"type": "harness_proxy_error", "message": "Harness 请求未获授权。"}},
            status,
        )

    def _provider(self, handler: BaseHTTPRequestHandler) -> None:
        body = self._body(handler, MAX_PROVIDER_BODY_BYTES)
        if set(body) - _PROVIDER_REQUEST_KEYS or body.get("model") != self.job.model:
            raise HarnessError("harness_invalid")
        messages = body.get("messages")
        tools = body.get("tools", [])
        tokens = body.get("max_completion_tokens", body.get("max_tokens"))
        temperature = body.get("temperature", 0.1)
        if (
            not isinstance(messages, list)
            or not messages
            or not isinstance(tools, list)
            or isinstance(tokens, bool)
            or not isinstance(tokens, int)
            or not isinstance(body.get("stream", False), bool)
        ):
            raise HarnessError("harness_invalid")
        try:
            message = self.model.request_tool_message(
                messages,
                tools,
                task=self.job.task,
                max_tokens=tokens,
                temperature=float(temperature),
            )
        except Exception as exc:
            raise HarnessError("harness_runtime_failed") from exc
        calls = message.get("tool_calls") or []
        for call in calls if isinstance(calls, list) else []:
            function = call.get("function") if isinstance(call, Mapping) else None
            name = function.get("name") if isinstance(function, Mapping) else None
            raw_arguments = (
                function.get("arguments") if isinstance(function, Mapping) else None
            )
            argument_keys: list[str] = []
            argument_types: list[str] = []
            if isinstance(raw_arguments, str):
                try:
                    parsed_arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    parsed_arguments = None
                if isinstance(parsed_arguments, Mapping):
                    argument_keys = sorted(str(key) for key in parsed_arguments)
                    argument_types = [
                        f"{key}:{type(parsed_arguments[key]).__name__}"
                        for key in argument_keys
                    ]
            _safe_trace(
                "provider_tool_call",
                tool=str(name or ""),
                argument_keys=argument_keys,
                argument_types=argument_types,
            )
        response = {
            "id": f"harness-{secrets.token_hex(12)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.job.model,
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
        }
        if body.get("stream") is True:
            return self._stream_completion(handler, response)
        self._json(handler, response)

    @staticmethod
    def _stream_completion(handler: BaseHTTPRequestHandler, response: Mapping[str, Any]) -> None:
        choice = response["choices"][0]
        message = choice["message"]
        chunks: list[dict[str, Any]] = [
            {
                "id": response["id"],
                "object": "chat.completion.chunk",
                "created": response["created"],
                "model": response["model"],
                "choices": [{"index": 0, "delta": {"role": "assistant", **({"content": message.get("content") or ""} if message.get("content") is not None else {})}, "finish_reason": None}],
            }
        ]
        if message.get("tool_calls"):
            chunks.append(
                {
                    "id": response["id"],
                    "object": "chat.completion.chunk",
                    "created": response["created"],
                    "model": response["model"],
                    "choices": [{"index": 0, "delta": {"tool_calls": message["tool_calls"]}, "finish_reason": None}],
                }
            )
        chunks.append(
            {
                "id": response["id"],
                "object": "chat.completion.chunk",
                "created": response["created"],
                "model": response["model"],
                "choices": [{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}],
            }
        )
        payload = b"".join(
            f"data: {json.dumps(chunk, ensure_ascii=False, separators=(',', ':'))}\n\n".encode("utf-8")
            for chunk in chunks
        ) + b"data: [DONE]\n\n"
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    def _mcp(self, handler: BaseHTTPRequestHandler) -> None:
        body = self._body(handler, MAX_MCP_BODY_BYTES)
        method = body.get("method")
        request_id = body.get("id")
        if body.get("jsonrpc") != "2.0" or not isinstance(method, str):
            raise HarnessError("harness_invalid")
        if method == "notifications/initialized" and request_id is None:
            handler.send_response(HTTPStatus.ACCEPTED)
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return
        if request_id is None or isinstance(request_id, (dict, list, bool)):
            raise HarnessError("harness_invalid")
        if method == "initialize":
            result: object = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "auto-research", "version": "1.1.0"},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            catalog = self.tools.public_catalog()
            result = {
                "tools": [
                    {
                        "name": item["name"],
                        "description": item["description"],
                        "inputSchema": item["input_schema"],
                    }
                    for item in catalog["tools"]
                ]
            }
        elif method == "tools/call":
            params = body.get("params")
            if not isinstance(params, Mapping) or set(params) - {"name", "arguments", "_meta"}:
                raise HarnessError("harness_tool_invalid")
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, Mapping):
                raise HarnessError("harness_tool_invalid")
            _safe_trace(
                "mcp_tool_call",
                tool=name,
                argument_keys=sorted(str(key) for key in arguments),
                argument_types=[
                    f"{key}:{type(arguments[key]).__name__}"
                    for key in sorted(arguments)
                ],
            )
            value = self.tools.call(name, arguments)
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                    }
                ],
                "structuredContent": value,
                "isError": False,
            }
        else:
            return self._json(
                handler,
                {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}},
            )
        self._json(handler, {"jsonrpc": "2.0", "id": request_id, "result": result})

    @staticmethod
    def _trace_handler_failure(path: str, exc: HarnessError) -> None:
        _safe_trace(
            "bridge_failure",
            route="provider" if path == "/v1/chat/completions" else "mcp",
            code=exc.code,
        )


class OfficialDeepSeekHarnessRuntime:
    """Production runtime adapter for the exact audited SDK/runtime pair."""

    def __init__(
        self,
        *,
        cordis_path: str | os.PathLike[str],
        harness_factory: Callable[..., object] | None = None,
        dependency_resolver: Callable[[], HarnessDependencySet] | None = None,
        runtime_path_resolver: Callable[[], str] | None = None,
    ) -> None:
        self._cordis_path = Path(cordis_path)
        self._dependencies = dependency_resolver or _default_dependency_set
        self._runtime_path = runtime_path_resolver or _default_runtime_binary
        self._factory = harness_factory

    def dependency_metadata(self) -> HarnessDependencySet:
        return self._dependencies()

    @staticmethod
    def composition_metadata() -> Mapping[str, Any]:
        return safe_composition_metadata()

    def execute(
        self,
        *,
        job: HarnessJobV1,
        model: HarnessBudgetedBusinessAIClient,
        tools: HarnessToolGateway,
        prompt: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        self.dependency_metadata().verify_production_protocols()
        if not self._cordis_path.is_file() or self._cordis_path.is_symlink():
            raise HarnessError("harness_dependency_mismatch")
        runtime_bin = self._runtime_path()
        factory = self._factory
        if factory is None:
            try:
                from deepseek_harness import DeepSeekHarness
            except Exception as exc:
                raise HarnessError("harness_runtime_unavailable") from exc
            factory = DeepSeekHarness
        normalized_prompt = canonical_public(dict(prompt or {}), byte_cap=512 * 1024)
        if not isinstance(normalized_prompt, dict):
            raise HarnessError("harness_invalid")
        with _Bridge(job=job, model=model, tools=tools) as bridge:
            with tempfile.TemporaryDirectory(prefix="auto-research-harness-") as workspace:
                env = {
                    "AUTO_RESEARCH_HARNESS_PROXY_TOKEN": bridge.token,
                    "AUTO_RESEARCH_HARNESS_MCP_URL": f"http://{bridge.authority}/mcp",
                    "AUTO_RESEARCH_HARNESS_SYSTEM_PROMPT": _system_prompt(job.session.scope),
                    "DSH_MODEL": job.model,
                }
                try:
                    harness = factory(
                        provider="deepseek-official",
                        model=job.model,
                        max_tokens=min(job.max_tokens, 32_000),
                        cwd=workspace,
                        runtime_cwd=workspace,
                        session_root=None,
                        cordis=str(self._cordis_path.resolve()),
                        env=env,
                        runtime_bin=runtime_bin,
                        base_url=f"http://{bridge.authority}/v1",
                        api_key=bridge.token,
                        request_timeout_seconds=180,
                    )
                    with _scrubbed_environment(env), harness:
                        result = harness.run(
                            _user_prompt(job.session.scope, normalized_prompt),
                            session_id=job.session.session_id,
                        )
                except HarnessError:
                    raise
                except Exception as exc:
                    raise HarnessError("harness_runtime_failed") from exc
        final = getattr(result, "final_response", None)
        finish = getattr(result, "finish_reason", None)
        session_root = getattr(result, "session_root", None)
        if (
            not isinstance(final, str)
            or not final.strip()
            or len(final.encode("utf-8")) > MAX_FINAL_RESPONSE_BYTES
            or finish != "completed"
            or session_root not in {None, ""}
        ):
            raise HarnessError("harness_output_invalid")
        try:
            value = json.loads(final)
        except json.JSONDecodeError as exc:
            raise HarnessError("harness_output_invalid") from exc
        if not isinstance(value, dict):
            raise HarnessError("harness_output_invalid")
        return value


def _system_prompt(scope: str) -> str:
    common = (
        "你是 Auto Research 的只读科研证据代理。只能调用 auto_research MCP 工具；"
        "不得请求文件、终端、网络、私人实验、凭据或本机路径。所有定量陈述必须有已核验引用。"
        "最终仅输出严格 JSON 对象，不要 Markdown。"
    )
    if scope == "librarian":
        return common + "回答必须包含条件解释、检索过程、证据回答、引用卡片、相关文章建议和局限。"
    return common + "只解释当前实体及明确允许的相邻证据，不得跨证据包拼接定量结论。"


def _user_prompt(scope: str, payload: Mapping[str, Any]) -> str:
    if scope == "librarian":
        schema = {
            "schema_version": "librarian-harness-result-v1",
            "answer": "",
            "report": {
                "direct_conclusion": "",
                "evidence_matrix": [],
                "related_evidence": [],
                "database_gaps": "",
                "suggested_followups": [],
            },
            "citations": [{"ref": "R1"}],
            "recommended_articles": [
                {"paper_uid": "", "title": "", "doi": "", "reason": ""}
            ],
            "comparison_bundle_uids": [],
        }
    else:
        schema = {
            "schema_version": "selected-evidence-harness-result-v1",
            "answer": "",
            "entity": {},
            "related": [],
            "limitations": [],
        }
    return (
        "完成下面的受控科研任务。先用允许的工具核验，再严格按给定 JSON 结构输出。\n"
        f"任务输入：{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n"
        f"输出结构：{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
    )


__all__ = [
    "OfficialDeepSeekHarnessRuntime",
    "RUNTIME_BINARY_SHA256",
    "RUNTIME_RG_SHA256",
    "RUNTIME_SPAWN_HELPER_SHA256",
    "safe_composition_metadata",
]
