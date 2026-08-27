from __future__ import annotations

from .routing import RouteRegistry, RouteSpec


DESKTOP = frozenset({"desktop"})
DESKTOP_AND_MAINTENANCE = frozenset({"desktop", "maintenance"})
SEARCH_MODES = frozenset({"desktop", "maintenance", "search_only_compat"})

PACKAGE_BYTES = 8_192
CREDENTIAL_BYTES = 8_192
SETTINGS_BYTES = 32_768
HISTORY_BYTES = 3_000_000
RESEARCH_MEMORY_BYTES = 64_000
EVIDENCE_CHAT_HISTORY_BYTES = 512 * 1024
TABLE_STRUCTURE_BYTES = 64 * 1024
PERSONAL_BYTES = 512 * 1024
LIBRARIAN_BYTES = 256_000
AI_ACTION_BYTES = 256 * 1024
WORKSPACE_JSON_BYTES = 1_000_000
PDF_BYTES = 80 * 1024 * 1024


def _get(
    route_id: str,
    path: str,
    controller: str,
    *,
    modes: frozenset[str] = DESKTOP_AND_MAINTENANCE,
) -> RouteSpec:
    return RouteSpec(route_id, "GET", controller, 0, False, False, modes, path=path)


def _get_pattern(
    route_id: str,
    pattern: str,
    controller: str,
    *,
    modes: frozenset[str] = DESKTOP_AND_MAINTENANCE,
) -> RouteSpec:
    return RouteSpec(
        route_id, "GET", controller, 0, False, False, modes, pattern=pattern
    )


def _post(
    route_id: str,
    path: str,
    controller: str,
    cap: int,
    *,
    mutation: bool = True,
    modes: frozenset[str] = DESKTOP_AND_MAINTENANCE,
) -> RouteSpec:
    return RouteSpec(
        route_id,
        "POST",
        controller,
        cap,
        mutation,
        mutation,
        modes,
        path=path,
    )


def _post_pattern(
    route_id: str,
    pattern: str,
    controller: str,
    cap: int,
    *,
    mutation: bool = True,
    modes: frozenset[str] = DESKTOP_AND_MAINTENANCE,
) -> RouteSpec:
    return RouteSpec(
        route_id,
        "POST",
        controller,
        cap,
        mutation,
        mutation,
        modes,
        pattern=pattern,
    )


def _patch(
    route_id: str,
    path: str,
    controller: str,
    cap: int,
    *,
    modes: frozenset[str] = DESKTOP_AND_MAINTENANCE,
) -> RouteSpec:
    return RouteSpec(
        route_id,
        "PATCH",
        controller,
        cap,
        True,
        True,
        modes,
        path=path,
    )


DEFAULT_DESKTOP_ROUTES: tuple[RouteSpec, ...] = (
    # Desktop state and credentials.
    _get("ui.mode", "/api/ui-mode", "readiness.ui_mode", modes=SEARCH_MODES),
    _get("desktop.readiness", "/api/desktop/readiness", "readiness.status"),
    _get("settings.get", "/api/desktop/settings", "settings.get"),
    _patch(
        "settings.preferences.patch",
        "/api/desktop/settings/preferences",
        "settings.patch_preferences",
        SETTINGS_BYTES,
    ),
    _get("ai.catalog", "/api/desktop/ai/providers", "ai.catalog"),
    _get("ai.settings.get", "/api/desktop/ai/settings", "ai.settings_get"),
    _patch(
        "ai.settings.patch",
        "/api/desktop/ai/settings",
        "ai.settings_patch",
        SETTINGS_BYTES,
    ),
    _get_pattern(
        "credential.provider.get",
        r"^/api/desktop/ai/credentials/(?P<provider_id>deepseek|openai)$",
        "credential.provider_status",
    ),
    _post_pattern(
        "credential.provider.save",
        r"^/api/desktop/ai/credentials/(?P<provider_id>deepseek|openai)$",
        "credential.provider_save",
        CREDENTIAL_BYTES,
    ),
    RouteSpec(
        "credential.provider.delete",
        "DELETE",
        "credential.provider_delete",
        0,
        True,
        True,
        DESKTOP_AND_MAINTENANCE,
        pattern=r"^/api/desktop/ai/credentials/(?P<provider_id>deepseek|openai)$",
    ),
    _post_pattern(
        "ai.provider.test_prepare",
        r"^/api/desktop/ai/providers/(?P<provider_id>deepseek|openai)/test-actions$",
        "ai.prepare_provider_test",
        CREDENTIAL_BYTES,
    ),
    _post_pattern(
        "ai.provider.test_execute",
        r"^/api/desktop/ai/providers/(?P<provider_id>deepseek|openai)/test$",
        "ai.execute_provider_test",
        CREDENTIAL_BYTES,
    ),
    _post(
        "ai.consent.issue",
        "/api/desktop/ai/consents",
        "ai.issue_consent",
        CREDENTIAL_BYTES,
    ),
    _post_pattern(
        "ai.business.prepare",
        r"^/api/desktop/ai/actions/(?P<scope>librarian|selected_evidence_chat|literature_extraction|personal_suggestion)/prepare$",
        "ai.prepare_business_action",
        AI_ACTION_BYTES,
    ),
    _post_pattern(
        "ai.business.execute",
        r"^/api/desktop/ai/actions/(?P<scope>librarian|selected_evidence_chat|literature_extraction|personal_suggestion)/execute$",
        "ai.execute_business_action",
        CREDENTIAL_BYTES,
    ),
    # Legacy DeepSeek-specific endpoints remain compatibility aliases while
    # both desktop shells migrate to the provider-neutral controllers above.
    _get("credential.deepseek.get", "/api/desktop/credentials/deepseek", "credential.status"),
    _post(
        "credential.deepseek.save",
        "/api/desktop/credentials/deepseek",
        "credential.save",
        CREDENTIAL_BYTES,
    ),
    RouteSpec(
        "credential.deepseek.delete",
        "DELETE",
        "credential.delete",
        0,
        True,
        True,
        DESKTOP_AND_MAINTENANCE,
        path="/api/desktop/credentials/deepseek",
    ),
    _get("history.librarian.get", "/api/desktop/librarian-history", "history.get"),
    _post(
        "history.librarian.save",
        "/api/desktop/librarian-history",
        "history.save",
        HISTORY_BYTES,
    ),
    _get(
        "research_memory.get",
        "/api/desktop/research-memories",
        "research_memory.get",
    ),
    _post(
        "research_memory.mutate",
        "/api/desktop/research-memories",
        "research_memory.mutate",
        RESEARCH_MEMORY_BYTES,
    ),
    _get(
        "evidence_chat_history.get",
        "/api/desktop/evidence-chat-history",
        "evidence_chat_history.get",
    ),
    _post(
        "evidence_chat_history.mutate",
        "/api/desktop/evidence-chat-history",
        "evidence_chat_history.mutate",
        EVIDENCE_CHAT_HISTORY_BYTES,
    ),
    _post(
        "table_structure.review",
        "/api/desktop/table-structures/reviews",
        "table_structure.review",
        TABLE_STRUCTURE_BYTES,
    ),
    # Official evidence package lifecycle.
    _get("package.status", "/api/desktop/evidence-packages", "package.status"),
    _post(
        "package.import",
        "/api/desktop/evidence-packages/import",
        "package.import",
        PACKAGE_BYTES,
    ),
    _post(
        "package.rollback",
        "/api/desktop/evidence-packages/rollback",
        "package.rollback",
        PACKAGE_BYTES,
    ),
    _get_pattern(
        "package.job.get",
        r"^/api/desktop/evidence-package-jobs/(?P<job_id>[A-Za-z0-9_-]{16,128})$",
        "package.job_get",
    ),
    # Unified official and user-transfer package center.
    _get(
        "package_center.status",
        "/api/desktop/package-center",
        "package_center.status",
    ),
    _post(
        "package_center.inspect",
        "/api/desktop/package-center/inspect",
        "package_center.inspect",
        PACKAGE_BYTES,
    ),
    _post(
        "package_center.export_plan",
        "/api/desktop/package-center/export-plan",
        "package_center.export_plan",
        PERSONAL_BYTES,
    ),
    _post(
        "package_center.export",
        "/api/desktop/package-center/export",
        "package_center.export",
        PERSONAL_BYTES,
    ),
    _post(
        "package_center.import",
        "/api/desktop/package-center/import",
        "package_center.import",
        PERSONAL_BYTES,
    ),
    _get_pattern(
        "package_center.job_get",
        r"^/api/desktop/package-center/jobs/(?P<job_id>[A-Za-z0-9_-]{16,128})$",
        "package_center.job_get",
    ),
    # Personal experiment import and private search refresh.
    _post(
        "personal.preview",
        "/api/desktop/personal-imports/preview",
        "personal.preview",
        PERSONAL_BYTES,
    ),
    _get(
        "personal.search_status",
        "/api/desktop/personal-imports/search-status",
        "personal.search_status",
    ),
    _post(
        "personal.search_refresh",
        "/api/desktop/personal-imports/search-refresh",
        "personal.search_refresh",
        PERSONAL_BYTES,
    ),
    _get_pattern(
        "personal.status",
        r"^/api/desktop/personal-imports/(?P<import_id>personal_import_[A-Za-z0-9_-]{16,96})$",
        "personal.status",
    ),
    _post_pattern(
        "personal.draft",
        r"^/api/desktop/personal-imports/(?P<import_id>personal_import_[A-Za-z0-9_-]{16,96})/draft$",
        "personal.draft",
        PERSONAL_BYTES,
    ),
    _post_pattern(
        "personal.confirm",
        r"^/api/desktop/personal-imports/(?P<import_id>personal_import_[A-Za-z0-9_-]{16,96})/confirm$",
        "personal.confirm",
        PERSONAL_BYTES,
    ),
    _post_pattern(
        "personal.ai_suggestion",
        r"^/api/desktop/personal-imports/(?P<import_id>personal_import_[A-Za-z0-9_-]{16,96})/ai-suggestion$",
        "personal.ai_suggestion",
        PERSONAL_BYTES,
    ),
    _post_pattern(
        "personal.reviewed_import",
        r"^/api/desktop/personal-imports/(?P<import_id>personal_import_[A-Za-z0-9_-]{16,96})/reviewed-import$",
        "personal.reviewed_import",
        PERSONAL_BYTES,
    ),
    # Read-only federated exact search.
    _get(
        "federated.search",
        "/api/desktop/federated-search",
        "federated.search",
        modes=SEARCH_MODES,
    ),
    _get(
        "federated.evidence",
        "/api/desktop/federated-evidence",
        "federated.evidence",
        modes=SEARCH_MODES,
    ),
    _get(
        "federated.pdf",
        "/api/desktop/federated-pdf",
        "federated.pdf",
        modes=SEARCH_MODES,
    ),
    # Librarian remains read-only; POST is a bounded query, not a mutation.
    _post(
        "librarian.chat",
        "/api/agents/librarian/chat",
        "librarian.chat",
        LIBRARIAN_BYTES,
        mutation=False,
        modes=SEARCH_MODES,
    ),
    _post(
        "librarian.research_brief",
        "/api/agents/librarian/research-brief.md",
        "librarian.research_brief",
        LIBRARIAN_BYTES,
        mutation=False,
        modes=SEARCH_MODES,
    ),
    _post(
        "evidence.context_chat",
        "/api/context-chat",
        "evidence.context_chat",
        LIBRARIAN_BYTES,
        mutation=False,
        modes=SEARCH_MODES,
    ),
    # Workspace read routes.  These retain the current URLs and DTO owners.
    _get("workspace.papers", "/api/papers", "workspace.papers"),
    _get("workspace.search_papers", "/api/search-papers", "workspace.search_papers", modes=SEARCH_MODES),
    _get("workspace.current_paper", "/api/current-paper", "workspace.current_paper"),
    _get("workspace.six_data", "/api/six-data", "workspace.six_data"),
    _get("workspace.six_search", "/api/six-search", "workspace.six_search", modes=SEARCH_MODES),
    _get("workspace.qualitative_search", "/api/qualitative-search", "workspace.qualitative_search", modes=SEARCH_MODES),
    _get("workspace.visual_search", "/api/visual-search", "workspace.visual_search", modes=SEARCH_MODES),
    _get("workspace.search_v2", "/api/search-v2", "workspace.search_v2", modes=SEARCH_MODES),
    _get("workspace.search_v2_status", "/api/search-v2/status", "workspace.search_v2_status", modes=SEARCH_MODES),
    _get("workspace.uploads", "/api/uploads", "workspace.uploads"),
    _get("workspace.processing_jobs", "/api/processing-jobs", "workspace.processing_jobs"),
    _get("workspace.ai_status", "/api/ai/status", "workspace.ai_status"),
    _get("workspace.experiment_profile", "/api/current-paper/experiment-profile", "workspace.experiment_profile"),
    _get("workspace.extraction", "/api/current-paper/extraction", "workspace.extraction"),
    _get("workspace.deepseek_run", "/api/current-paper/deepseek-run", "workspace.deepseek_run"),
    _get("workspace.quality_run", "/api/current-paper/quality-run", "workspace.quality_run"),
    _get("workspace.quality_candidates", "/api/current-paper/quality-candidates", "workspace.quality_candidates"),
    _get("workspace.evidence_audit", "/api/current-paper/evidence-audit", "workspace.evidence_audit"),
    _get("workspace.visual_assets", "/api/current-paper/visual-assets", "workspace.visual_assets"),
    _get_pattern(
        "workspace.paper",
        r"^/api/papers/(?P<paper_id>[1-9][0-9]*)$",
        "workspace.paper",
    ),
    _get_pattern(
        "workspace.paper_pdf",
        r"^/api/papers/(?P<paper_id>[1-9][0-9]*)/pdf$",
        "workspace.paper_pdf",
        modes=SEARCH_MODES,
    ),
    _get_pattern(
        "workspace.six_item",
        r"^/api/six-data/(?P<item_id>[1-9][0-9]*)$",
        "workspace.six_item",
    ),
    _get_pattern(
        "workspace.source_view",
        r"^/api/six-data/(?P<item_id>[1-9][0-9]*)/source-view$",
        "workspace.source_view",
        modes=SEARCH_MODES,
    ),
    _get_pattern(
        "workspace.visual_asset",
        r"^/api/visual-assets/(?P<asset_id>[1-9][0-9]*)$",
        "workspace.visual_asset",
        modes=SEARCH_MODES,
    ),
    _get_pattern(
        "workspace.visual_image",
        r"^/api/visual-assets/(?P<asset_id>[1-9][0-9]*)/image$",
        "workspace.visual_image",
        modes=SEARCH_MODES,
    ),
    # Workspace write/action routes.
    _post("workspace.upload_pdf", "/api/uploads/pdf", "workspace.upload_pdf", PDF_BYTES),
    _post("workspace.select_paper", "/api/current-paper", "workspace.select_paper", WORKSPACE_JSON_BYTES),
    _post("workspace.run_workflow", "/api/current-paper/run-workflow", "workspace.run_workflow", WORKSPACE_JSON_BYTES),
    _post_pattern(
        "workspace.confirm_item",
        r"^/api/six-data/(?P<item_id>[1-9][0-9]*)/confirm$",
        "workspace.confirm_item",
        WORKSPACE_JSON_BYTES,
    ),
    _post_pattern(
        "workspace.decide_item",
        r"^/api/six-data/(?P<item_id>[1-9][0-9]*)/decision$",
        "workspace.decide_item",
        WORKSPACE_JSON_BYTES,
    ),
    _post_pattern(
        "workspace.review_visual",
        r"^/api/visual-assets/(?P<asset_id>[1-9][0-9]*)/review$",
        "workspace.review_visual",
        WORKSPACE_JSON_BYTES,
    ),
)


def build_default_route_registry() -> RouteRegistry:
    return RouteRegistry(list(DEFAULT_DESKTOP_ROUTES))
