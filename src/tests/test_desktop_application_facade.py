from __future__ import annotations

import json
import unittest

from auto_research.desktop import (
    DEFAULT_DESKTOP_ROUTES,
    DesktopApplicationFacade,
    DesktopErrorDTO,
    DesktopFacadeError,
    RequestContext,
    RouteRegistry,
    RouteSpec,
    build_default_route_registry,
)


class RouteSpecTests(unittest.TestCase):
    def test_route_spec_rejects_illegal_method_and_body_cap(self) -> None:
        with self.assertRaisesRegex(ValueError, "method"):
            RouteSpec(
                "bad.method",
                "PUT",
                "bad.controller",
                10,
                True,
                True,
                frozenset({"desktop"}),
                path="/api/bad",
            )
        patch = RouteSpec(
            "settings.patch",
            "PATCH",
            "settings.patch",
            1_024,
            True,
            True,
            frozenset({"desktop"}),
            path="/api/desktop/settings/preferences",
        )
        self.assertEqual(patch.method, "PATCH")
        with self.assertRaisesRegex(ValueError, "GET"):
            RouteSpec(
                "bad.get_body",
                "GET",
                "bad.controller",
                1,
                False,
                False,
                frozenset({"desktop"}),
                path="/api/bad",
            )
        with self.assertRaisesRegex(ValueError, "positive"):
            RouteSpec(
                "bad.post_body",
                "POST",
                "bad.controller",
                0,
                True,
                True,
                frozenset({"desktop"}),
                path="/api/bad",
            )

    def test_mutation_must_require_csrf(self) -> None:
        with self.assertRaisesRegex(ValueError, "CSRF"):
            RouteSpec(
                "bad.csrf",
                "POST",
                "bad.controller",
                10,
                True,
                False,
                frozenset({"desktop"}),
                path="/api/bad",
            )

    def test_error_dto_is_path_free(self) -> None:
        error = DesktopErrorDTO("package_invalid", "资料包无效。", 400)
        payload = error.public_dict()
        self.assertEqual(
            payload,
            {"code": "package_invalid", "message": "资料包无效。", "retryable": False},
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("/Users/", serialized)
        self.assertNotIn("traceback", serialized.casefold())
        with self.assertRaisesRegex(ValueError, "local path"):
            DesktopErrorDTO("unsafe_error", "读取 /Users/private/key 失败", 500)


class RouteRegistryTests(unittest.TestCase):
    def test_duplicate_id_and_location_are_rejected(self) -> None:
        route = RouteSpec(
            "sample.get",
            "GET",
            "sample.get",
            0,
            False,
            False,
            frozenset({"desktop"}),
            path="/api/sample",
        )
        registry = RouteRegistry([route])
        with self.assertRaisesRegex(ValueError, "duplicate route id"):
            registry.add(route)
        with self.assertRaisesRegex(ValueError, "duplicate route"):
            registry.add(
                RouteSpec(
                    "sample.get_alias",
                    "GET",
                    "sample.alias",
                    0,
                    False,
                    False,
                    frozenset({"desktop"}),
                    path="/api/sample",
                )
            )

    def test_pattern_extracts_stable_named_parameters(self) -> None:
        registry = build_default_route_registry()
        spec, params = registry.resolve(
            "GET", "/api/desktop/evidence-package-jobs/job_1234567890123456"
        )
        self.assertEqual(spec.route_id, "package.job.get")
        self.assertEqual(params, {"job_id": "job_1234567890123456"})

    def test_wrong_method_has_stable_error(self) -> None:
        registry = build_default_route_registry()
        with self.assertRaises(DesktopFacadeError) as raised:
            registry.resolve("GET", "/api/desktop/evidence-packages/import")
        self.assertEqual(raised.exception.error.code, "desktop_method_not_allowed")
        self.assertEqual(raised.exception.error.http_status, 405)

    def test_default_catalog_has_unique_golden_contract(self) -> None:
        registry = build_default_route_registry()
        contract = registry.golden_contract()
        self.assertEqual(len(contract), len(DEFAULT_DESKTOP_ROUTES))
        self.assertEqual(
            len({row["route_id"] for row in contract}),
            len(DEFAULT_DESKTOP_ROUTES),
        )
        self.assertEqual(
            len({(row["method"], row["path"] or row["pattern"]) for row in contract}),
            len(DEFAULT_DESKTOP_ROUTES),
        )
        by_id = {row["route_id"]: row for row in contract}
        self.assertEqual(by_id["package.import"]["path"], "/api/desktop/evidence-packages/import")
        self.assertEqual(
            by_id["package_center.export"]["path"],
            "/api/desktop/package-center/export",
        )
        self.assertEqual(
            by_id["federated.pdf"]["path"],
            "/api/desktop/federated-pdf",
        )
        self.assertEqual(by_id["personal.preview"]["body_cap_bytes"], 512 * 1024)
        self.assertEqual(
            by_id["settings.preferences.patch"]["method"], "PATCH"
        )
        self.assertTrue(by_id["settings.preferences.patch"]["csrf_required"])
        self.assertEqual(
            by_id["credential.provider.get"]["pattern"],
            r"^/api/desktop/ai/credentials/(?P<provider_id>deepseek|openai)$",
        )
        self.assertTrue(by_id["ai.provider.test_execute"]["csrf_required"])
        self.assertEqual(
            by_id["ai.business.prepare"]["body_cap_bytes"],
            256 * 1024,
        )
        self.assertTrue(by_id["ai.business.execute"]["csrf_required"])
        self.assertEqual(
            by_id["table_structure.review"]["path"],
            "/api/desktop/table-structures/reviews",
        )
        self.assertEqual(by_id["table_structure.review"]["body_cap_bytes"], 64 * 1024)
        self.assertTrue(by_id["table_structure.review"]["mutation"])
        self.assertTrue(by_id["table_structure.review"]["csrf_required"])
        self.assertEqual(
            by_id["evidence_chat_history.get"]["path"],
            "/api/desktop/evidence-chat-history",
        )
        self.assertEqual(
            by_id["evidence_chat_history.mutate"]["body_cap_bytes"],
            512 * 1024,
        )
        self.assertTrue(by_id["evidence_chat_history.mutate"]["csrf_required"])
        self.assertFalse(by_id["librarian.chat"]["mutation"])
        self.assertTrue(by_id["workspace.upload_pdf"]["csrf_required"])

    def test_catalog_covers_required_controller_families(self) -> None:
        families = {route.controller.split(".", 1)[0] for route in DEFAULT_DESKTOP_ROUTES}
        self.assertTrue(
            {
                "package",
                "package_center",
                "personal",
                "federated",
                "readiness",
                "credential",
                "settings",
                "ai",
                "librarian",
                "workspace",
            }.issubset(families)
        )


class DesktopApplicationFacadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = build_default_route_registry()
        self.facade = DesktopApplicationFacade(self.registry)

    @staticmethod
    def request(
        method: str,
        path: str,
        *,
        mode: str = "desktop",
        body_size: int = 0,
        csrf: bool = False,
        authenticated: bool = True,
    ) -> RequestContext:
        return RequestContext(
            request_id="request-1",
            method=method,
            path=path,
            mode=mode,
            body_size=body_size,
            csrf_validated=csrf,
            session_authenticated=authenticated,
        )

    def test_dispatch_binds_route_and_path_parameters(self) -> None:
        captured: list[RequestContext] = []
        self.facade.register_controller(
            "workspace.paper", lambda request: captured.append(request) or {"ok": True}
        )
        result = self.facade.dispatch(self.request("GET", "/api/papers/42"))
        self.assertEqual(result, {"ok": True})
        self.assertEqual(captured[0].route_id, "workspace.paper")
        self.assertEqual(captured[0].path_params, {"paper_id": "42"})

    def test_mode_authorization_is_enforced_before_controller(self) -> None:
        called = False

        def controller(_request: RequestContext) -> dict[str, bool]:
            nonlocal called
            called = True
            return {"ok": True}

        self.facade.register_controller("package.status", controller)
        with self.assertRaises(DesktopFacadeError) as raised:
            self.facade.dispatch(
                self.request(
                    "GET",
                    "/api/desktop/evidence-packages",
                    mode="search_only_compat",
                )
            )
        self.assertEqual(raised.exception.error.code, "desktop_mode_forbidden")
        self.assertFalse(called)

    def test_session_csrf_and_body_cap_are_enforced(self) -> None:
        self.facade.register_controller("package.import", lambda _request: {"ok": True})
        with self.assertRaises(DesktopFacadeError) as session_error:
            self.facade.dispatch(
                self.request(
                    "POST",
                    "/api/desktop/evidence-packages/import",
                    body_size=10,
                    csrf=True,
                    authenticated=False,
                )
            )
        self.assertEqual(session_error.exception.error.code, "desktop_session_required")
        with self.assertRaises(DesktopFacadeError) as csrf_error:
            self.facade.dispatch(
                self.request(
                    "POST", "/api/desktop/evidence-packages/import", body_size=10
                )
            )
        self.assertEqual(csrf_error.exception.error.code, "desktop_csrf_required")
        with self.assertRaises(DesktopFacadeError) as size_error:
            self.facade.dispatch(
                self.request(
                    "POST",
                    "/api/desktop/evidence-packages/import",
                    body_size=8_193,
                    csrf=True,
                )
            )
        self.assertEqual(size_error.exception.error.code, "desktop_request_too_large")

    def test_missing_controller_and_unknown_exception_are_path_free(self) -> None:
        with self.assertRaises(DesktopFacadeError) as missing:
            self.facade.dispatch(self.request("GET", "/api/desktop/readiness"))
        self.assertEqual(missing.exception.error.code, "desktop_controller_unavailable")

        self.facade.register_controller(
            "readiness.status",
            lambda _request: (_ for _ in ()).throw(RuntimeError("/Users/private/key")),
        )
        with self.assertRaises(DesktopFacadeError) as failed:
            self.facade.dispatch(self.request("GET", "/api/desktop/readiness"))
        payload = failed.exception.error.public_dict()
        self.assertEqual(payload["code"], "desktop_request_failed")
        self.assertNotIn("/Users", json.dumps(payload))

    def test_non_mutating_librarian_post_does_not_require_csrf(self) -> None:
        self.facade.register_controller(
            "librarian.chat", lambda _request: {"answer": "ok"}
        )
        result = self.facade.dispatch(
            self.request("POST", "/api/agents/librarian/chat", body_size=100)
        )
        self.assertEqual(result, {"answer": "ok"})


if __name__ == "__main__":
    unittest.main()
