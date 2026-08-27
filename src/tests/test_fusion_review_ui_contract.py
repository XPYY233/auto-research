from __future__ import annotations

import re
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "auto_research" / "evidence" / "web"


class _IDs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        value = dict(attrs).get("id")
        if value:
            self.ids.append(value)


class FusionReviewUIContractTests(unittest.TestCase):
    def test_research_memory_opt_in_and_evidence_chat_history_are_single_safe_ports(self) -> None:
        source = (WEB / "fusion_review.js").read_text(encoding="utf-8")
        index = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertEqual(index.count('id="fusion-librarian-use-memory"'), 1)
        self.assertIn("使用已确认研究记忆", index)
        self.assertNotIn('id="fusion-librarian-use-memory" type="checkbox" checked', index)
        self.assertEqual(source.count('evidenceChatHistory:"/api/desktop/evidence-chat-history"'), 1)
        self.assertIn("use_research_memory:q(\"#fusion-librarian-use-memory\")?.checked===true", source)
        self.assertIn("function librarianConversationEvidence()", source)
        self.assertIn("payload.conversation_evidence=conversationEvidence", source)
        self.assertIn("source_id:sourceId,entity_type:entityType,entity_uid:entityUid,bundle_ref:bundleRef", source)
        self.assertIn("本次使用 ${memoryCount} 条已核验记忆", source)
        self.assertIn('operation:"upsert",expected_revision,thread:{source_scope:', source)
        self.assertIn('operation:"delete",expected_revision,identity:{source_scope:', source)
        self.assertIn("evidence_chat_history_revision_conflict", source)
        self.assertIn("loadEvidenceChatHistory()", source)
        self.assertNotIn("memory_uid", source[source.index("function librarianRequest"):source.index("function librarianRefs")])

    def test_rejected_harness_output_is_presented_as_local_evidence_not_ai_success(self) -> None:
        source = (WEB / "fusion_review.js").read_text(encoding="utf-8")
        self.assertIn('result.summary_mode==="local_deterministic_after_harness_rejection"', source)
        self.assertIn("模型回答未采用", source)
        self.assertIn("没有采用模型正文，也没有再次调用模型", source)
        self.assertIn('localFallback?"warning":"success"', source)

    @classmethod
    def setUpClass(cls) -> None:
        cls.index = (WEB / "index.html").read_text(encoding="utf-8")
        cls.base_css = (WEB / "app.css").read_text(encoding="utf-8")
        cls.css = (WEB / "workbench.css").read_text(encoding="utf-8")
        cls.runtime = (WEB / "fusion_review.js").read_text(encoding="utf-8")
        cls.package_runtime = (WEB / "fusion_package_center.js").read_text(encoding="utf-8")
        cls.personal_runtime = (WEB / "fusion_personal_import.js").read_text(encoding="utf-8")
        cls.ai_experience = (WEB / "fusion_ai_experience.js").read_text(encoding="utf-8")

    def test_single_fusion_owner_and_script_order(self) -> None:
        parser = _IDs()
        parser.feed(self.index)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertEqual(self.index.count('/static/fusion_review.js'), 1)
        self.assertEqual(self.index.count('/static/ai_consent.js'), 1)
        self.assertEqual(self.index.count('/static/document_tab_store.js'), 1)
        self.assertEqual(self.index.count('/static/fusion_ai_experience.js'), 1)
        self.assertEqual(self.index.count('/static/fusion_package_center.js'), 1)
        self.assertEqual(self.index.count('/static/fusion_personal_import.js'), 1)
        self.assertLess(self.index.index('/static/ai_consent.js'), self.index.index('/static/fusion_review.js'))
        self.assertLess(self.index.index('/static/document_tab_store.js'), self.index.index('/static/fusion_review.js'))
        self.assertLess(self.index.index('/static/fusion_ai_experience.js'), self.index.index('/static/fusion_review.js'))
        self.assertLess(self.index.index('/static/fusion_package_center.js'), self.index.index('/static/fusion_review.js'))
        self.assertLess(self.index.index('/static/fusion_package_center.js'), self.index.index('/static/fusion_personal_import.js'))
        self.assertLess(self.index.index('/static/fusion_personal_import.js'), self.index.index('/static/fusion_review.js'))
        self.assertNotIn("fetch(", self.package_runtime)
        self.assertNotIn("DOMContentLoaded", self.package_runtime)
        self.assertNotIn("localStorage", self.package_runtime)
        for forbidden in ("fetch(", "DOMContentLoaded", "localStorage", "DocumentTabStore", "switchView("):
            self.assertNotIn(forbidden, self.personal_runtime)
        for legacy in ("/static/app.js", "/static/workbench.js", "/static/desktop_product.js", "/static/package_center.js"):
            self.assertNotIn(legacy, self.index)
        self.assertNotIn("appendChild", self.runtime)
        navigation = re.search(r'<nav class="fusion-activity".*?</nav>', self.index, re.DOTALL)
        self.assertIsNotNone(navigation)
        for name in ("paper", "search", "personal", "package", "settings"):
            self.assertEqual(navigation.group(0).count(f'data-view="{name}"'), 1)
            self.assertEqual(self.index.count(f'data-view-panel="{name}"'), 1)

    def test_personal_search_recovery_uses_the_single_fusion_request_authority(self) -> None:
        for element_id in (
            "fusion-personal-search-recovery",
            "fusion-personal-search-recovery-title",
            "fusion-personal-search-recovery-note",
            "fusion-personal-search-refresh",
        ):
            self.assertEqual(self.index.count(f'id="{element_id}"'), 1)
        self.assertEqual(
            self.runtime.count('personalSearchStatus:"/api/desktop/personal-imports/search-status"'),
            1,
        )
        self.assertEqual(
            self.runtime.count('personalSearchRefresh:"/api/desktop/personal-imports/search-refresh"'),
            1,
        )
        self.assertIn("path===ROUTES.personalSearchRefresh", self.runtime)
        self.assertIn("ROUTES.personalSearchStatus", self.runtime)
        self.assertIn('q("#fusion-personal-search-refresh")?.addEventListener', self.personal_runtime)
        self.assertIn("function publicPersonalSearchStatus", self.personal_runtime)
        self.assertIn("function loadPersonalSearchStatus", self.personal_runtime)
        self.assertIn("function refreshPersonalSearch", self.personal_runtime)
        self.assertIn('JSON.stringify({})', self.personal_runtime)
        self.assertNotIn("reviewed-import", self.personal_runtime[
            self.personal_runtime.index("async function refreshPersonalSearch"):
            self.personal_runtime.index("function personalStatus")
        ])

    def test_settings_view_reprojects_visible_category_after_closing_old_detail(self) -> None:
        switch_view = self.runtime[
            self.runtime.index("function switchView(") : self.runtime.index("function closeDrawers(")
        ]
        self.assertLess(switch_view.index("closeEvidenceDetail({focus:false})"), switch_view.index("renderDocumentTabs()"))
        self.assertIn('if(name==="settings")selectSettingsSection(state.settingsSection)', switch_view)
        self.assertEqual(self.index.count('data-settings-section="ai"'), 1)
        self.assertEqual(self.index.count('data-settings-panel="ai"'), 1)
        self.assertIn("AI 与 API 密钥", self.index)
        self.assertIn("fusion-ai-business-readiness", self.index)

    def test_appearance_cache_survives_fusion_startup_call(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert'),memory=new Map();
class Classes{{toggle(){{}}}}
const html={{dataset:{{theme:'system',density:'comfortable'}},style:{{setProperty(){{}}}}}};
globalThis.document={{readyState:'loading',documentElement:html,body:{{dataset:{{view:'paper'}}}},querySelector:()=>null,querySelectorAll:()=>[],addEventListener(){{}}}};
globalThis.localStorage={{getItem:key=>memory.get(key)||null,setItem:(key,value)=>memory.set(key,value)}};
eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));
const api=globalThis.AutoResearchFusion;
assert.equal(api.applyAppearance('dark','compact'),true);
const stored=JSON.parse(memory.get('auto-research-fusion-appearance-v1'));
assert.deepEqual(stored,{{version:1,theme:'dark',density:'compact'}});
assert.equal(html.dataset.theme,'dark');assert.equal(html.dataset.density,'compact');
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_ai_workflows_have_real_stage_feedback_and_chat_composers(self) -> None:
        for element_id in (
            "fusion-literature-progress",
            "fusion-personal-progress",
            "fusion-librarian-progress",
            "fusion-librarian-form",
            "fusion-librarian-new",
        ):
            self.assertEqual(self.index.count(f'id="{element_id}"'), 1)
        self.assertIn('data-evidence-ai-form', self.runtime)
        self.assertIn('data-evidence-ai-inline-progress', self.runtime)
        self.assertIn("fusion-ai-experience-v1", self.ai_experience)
        self.assertIn("AIExperienceController", self.ai_experience)
        self.assertIn("renderConversation", self.ai_experience)
        self.assertIn("activity(scope", self.ai_experience)
        self.assertIn("data-ai-activity", self.index)
        self.assertIn("不展示模型内部思维链", self.index)
        self.assertIn('aiRoute(scope,"execute-jobs")', self.runtime)
        self.assertIn("ai-execution-job-v1", self.runtime)
        self.assertIn("/api/desktop/ai/jobs/", self.runtime)
        self.assertIn("/api/desktop/ai/actions/literature_extraction/jobs", self.runtime)
        self.assertIn("literature-extraction-task-directory-v1", self.runtime)
        self.assertIn('aiProgress("literature_extraction"', self.runtime)
        self.assertIn('aiProgress("personal_suggestion"', self.personal_runtime)
        self.assertIn('aiProgress("librarian"', self.runtime)
        self.assertIn('aiProgress("selected_evidence_chat"', self.runtime)
        self.assertIn("Enter 发送 · Shift+Enter 换行", self.index)
        self.assertNotIn("setInterval(updateLibrarianProgress", self.runtime)

    def test_real_workflows_are_top_level_and_pdf_stays_in_workspace(self) -> None:
        for element_id in (
            "fusion-import-pdf", "fusion-start-extraction", "fusion-open-pdf",
            "fusion-run-precise-search", "fusion-open-librarian",
            "fusion-select-data-file", "fusion-personal-ai", "fusion-personal-confirm",
            "fusion-pdf-viewer", "fusion-close-pdf", "fusion-pdf-frame",
        ):
            self.assertEqual(self.index.count(f'id="{element_id}"'), 1)
        self.assertEqual(self.index.count('id="fusion-tab-more"'), 1)
        self.assertNotIn(">↶<", self.index)
        for action in ("toggle-context", "toggle-inspector", "toggle-primary", "toggle-secondary", "split-active", "reopen-closed"):
            self.assertEqual(self.index.count(f'data-command-action="{action}"'), 1)
        self.assertNotIn("globalThis.open", self.runtime)
        self.assertNotIn("_blank", self.runtime)
        self.assertIn("不离开工作台", self.index)
        self.assertIn("closeCurrentPDF", self.runtime)

    def test_four_evidence_types_share_one_central_detail_workspace(self) -> None:
        for element_id in (
            "fusion-evidence-detail", "fusion-evidence-detail-body", "fusion-detail-back",
            "fusion-detail-open-pdf", "fusion-detail-pdf", "fusion-detail-close-pdf",
            "fusion-detail-pdf-frame",
        ):
            self.assertEqual(self.index.count(f'id="{element_id}"'), 1)
        self.assertEqual(self.index.count('id="fusion-document-tabs-primary"'), 1)
        self.assertEqual(self.index.count('id="fusion-document-tabs-secondary"'), 1)
        self.assertIn('id="fusion-evidence-detail"', self.index)
        for marker in (
            'const EVIDENCE_LABELS=Object.freeze({item:', "function openEvidenceDetail(",
            "function closeEvidenceDetail(", "function renderEvidenceDetail(",
            'visualAsset:"/api/visual-assets"', 'federatedEvidence:"/api/desktop/federated-evidence"',
            "state.evidenceDetailRequest", "evidenceIdentity(state.evidenceDetail)!==identity",
        ):
            self.assertIn(marker, self.runtime)
        self.assertNotIn("#visual-dialog", self.runtime)
        self.assertNotIn("showModal()", self.runtime)

    def test_two_editor_groups_keep_identity_and_pdf_return_highlight_chain(self) -> None:
        for element_id in (
            "fusion-editor-group-content", "fusion-primary-editor-surface",
            "fusion-secondary-editor-surface", "fusion-secondary-editor-body",
            "fusion-split-tab", "fusion-reopen-tab", "fusion-secondary-move-primary",
            "fusion-detail-show-highlight", "fusion-detail-close-pdf-view",
            "fusion-detail-source-highlight", "fusion-detail-highlight-image",
        ):
            self.assertEqual(self.index.count(f'id="{element_id}"'), 1)
        for marker in (
            "function renderEditorSurfaces(", "function secondaryDocumentHTML(",
            'documentTabs?.reopenClosed()', 'documentTabs.move(active.tabId,"primary")',
            'data-document-group-id=', "const geometry=paneGeometry(snapshot)",
            'if(primaryCollapsed&&secondaryCollapsed)primaryCollapsed=false',
            'secondary.hidden=!geometry.hasSecondary||(geometry.narrow?snapshot.activeGroupId!=="secondary":geometry.secondaryCollapsed)',
            'openTab&&documentTabs&&paneViewport()>=900',
            'openSecondaryEvidence(row,state.view,{preview,pin})',
            '/api/six-data/${row.itemId}/source-view',
            '/api/six-data/${row.itemId}/source-highlight.png',
            "state.evidenceDetailReturn.scrollTop", "state.sourceHighlightRequest+=1",
        ):
            self.assertIn(marker, self.runtime)
        self.assertIn(".fusion-editor-group-content.split", self.css)
        self.assertIn(".fusion-secondary-editor", self.css)
        self.assertIn(".fusion-source-highlight", self.css)

    def test_three_accessible_pane_separators_share_one_layout_owner(self) -> None:
        for element_id, kind in (
            ("fusion-context-separator", "context"),
            ("fusion-editor-group-separator", "editor-groups"),
            ("fusion-inspector-separator", "inspector"),
        ):
            self.assertEqual(self.index.count(f'id="{element_id}"'), 1)
            tag = re.search(rf'<[^>]+id="{element_id}"[^>]*>', self.index)
            self.assertIsNotNone(tag)
            markup = tag.group(0)
            self.assertIn(f'data-pane-separator="{kind}"', markup)
            self.assertIn('role="separator"', markup)
            self.assertIn('aria-orientation="vertical"', markup)
            self.assertIn('tabindex="0"', markup)
        self.assertIn('/static/pane_layout_controller.js', self.index)
        controller = (WEB / "pane_layout_controller.js").read_text(encoding="utf-8")
        for marker in ('const SCHEMA_VERSION = "fusion-pane-layout-v2"', "const SIDE_SNAP = 48", "const EDITOR_SNAP = 96", "!(value.primaryCollapsed && value.secondaryCollapsed)"):
            self.assertIn(marker, controller)
        for marker in (
            "const PaneLayoutController=globalThis.AutoResearchPaneLayout?.PaneLayoutController",
            "function readPaneLayout(", "function persistPaneLayout(",
            "function startPaneDrag(", "function movePaneDrag(", "function endPaneDrag(",
            "setPointerCapture", "releasePointerCapture", "function handlePaneKey(",
            'addEventListener("dblclick"', 'addEventListener?.("resize",syncResponsivePaneLayout)',
            'function togglePane(', 'function splitActiveTab(', 'function reopenClosedTab(',
        ):
            self.assertIn(marker, self.runtime)
        self.assertIn(".fusion-pane-separator", self.css)
        self.assertIn("--fusion-context-size", self.css)
        self.assertIn("--fusion-primary-fr", self.css)
        self.assertIn('html[data-pane-resizing="true"]', self.css)
        self.assertIn("@container fusion-editor (max-width:1180px)", self.css)
        self.assertIn("@media(min-width:1200px) and (max-width:1599px)", self.css)
        self.assertIn('grid-template-areas:"activity context context-separator editor inspector-separator inspector"', self.css)
        self.assertIn('grid-template-areas:"activity context context-separator editor"', self.css)
        self.assertIn('grid-template-areas:"activity editor"', self.css)
        self.assertIn('grid-template-areas:"editor"', self.css)
        for marker in (
            ".fusion-activity { grid-area:activity; }",
            ".fusion-context { grid-area:context; }",
            ".fusion-context-separator { grid-area:context-separator; }",
            ".fusion-editor { grid-area:editor; }",
            ".fusion-inspector-separator { grid-area:inspector-separator; }",
            ".fusion-inspector { grid-area:inspector; }",
        ):
            self.assertIn(marker, self.css)
        self.assertNotIn(".fusion-toolbar .fusion-action-cluster { order:3;width:100%", self.css)
        self.assertNotIn(".fusion-toolbar .fusion-action-cluster { order:3;max-width:100%", self.css)
        self.assertNotIn(".fusion-toolbar .fusion-action-cluster,.fusion-detail-toolbar>div { width:100%", self.css)
        self.assertIn(".fusion-search-primary-row { display:grid;grid-template-columns:minmax(0,1fr) auto", self.css)
        self.assertNotIn(".fusion-search-primary-row { flex-wrap:wrap; }", self.css)
        self.assertNotIn("appendChild", self.runtime)

    def test_preview_tabs_pin_and_secondary_group_renders_complete_documents(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert'),memory=new Map();
globalThis.localStorage={{getItem:key=>memory.get(key)||null,setItem:(key,value)=>memory.set(key,value)}};
globalThis.document={{readyState:'loading',querySelector:()=>null,querySelectorAll:()=>[],addEventListener:()=>{{}}}};
eval(fs.readFileSync({str(WEB / 'document_tab_store.js')!r},'utf8'));
const Store=globalThis.AutoResearchDocumentTabs.DocumentTabStore,store=new Store();
const first=store.open({{tabId:'paper:paperId=1',kind:'paper',ownerView:'paper',title:'论文一',identity:{{paperId:'1'}},payload:{{body:'never persist'}}}},{{preview:true}});
const second=store.open({{tabId:'paper:paperId=2',kind:'paper',ownerView:'paper',title:'论文二',identity:{{paperId:'2'}}}},{{preview:true}});
assert.equal(store.snapshot().tabs.length,1);assert.equal(store.activeTab().tabId,second.tabId);assert.equal(store.activeTab().preview,true);
store.pin(second.tabId);store.open({{tabId:'paper:paperId=3',kind:'paper',ownerView:'paper',title:'论文三',identity:{{paperId:'3'}}}},{{preview:true}});
assert.equal(store.snapshot().tabs.length,2);assert.equal(store.snapshot().tabs.find(tab=>tab.tabId===second.tabId).pinned,true);
assert(store.split(second.tabId));assert(store.setNarrow(true));assert.equal(store.snapshot().groups.length,2);assert.equal(store.snapshot().tabs.find(tab=>tab.tabId===second.tabId).groupId,'secondary');
const persisted=memory.get('auto-research-workspace-layout-v1');assert(persisted.includes('workspace-layout-v2'));assert(!persisted.includes('never persist'));assert(!persisted.includes('payload'));
eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));const api=globalThis.AutoResearchFusion;
const evidence={{type:'finding',title:'硬度提高',findingText:'论文报告硬度显著提高。',excerpt:'原文片段',sourceScope:'workspace',itemId:7,paperId:2,page:4,articleTitle:'论文二',quantities:[],variables:{{}},materials:[],tags:[]}};
const paperHTML=api.secondaryDocumentHTML({{tabId:'paper:paperId=2',kind:'paper',title:'论文二',payload:{{paper:{{id:2,title:'论文二',firstAuthor:'作者甲',doi:'10.1/demo'}},evidence:[evidence],evidenceCount:1}}}});assert(paperHTML.includes('论文二'));assert(paperHTML.includes('硬度提高'));assert(paperHTML.includes('data-secondary-paper-evidence'));
const evidenceHTML=api.secondaryDocumentHTML({{tabId:'evidence:x',kind:'evidence',title:'硬度提高',payload:{{row:evidence,status:'ready'}}}});assert(evidenceHTML.includes('论文报告硬度显著提高'));assert(evidenceHTML.includes('原文片段'));assert(evidenceHTML.includes('查看原文'));
const pdfHTML=api.secondaryDocumentHTML({{tabId:'pdf:x',kind:'pdf',title:'论文二 PDF',payload:{{url:'/api/papers/2/pdf',returnTabId:'paper:paperId=2',page:4,highlight:{{kind:'text',url:'/api/six-data/7/source-highlight.png'}}}}}});assert(pdfHTML.includes('<iframe'));assert(pdfHTML.includes('返回来源标签'));assert(pdfHTML.includes('隐藏定位'));assert(pdfHTML.includes('/api/six-data/7/source-highlight.png'));
const tableHTML=api.secondaryDocumentHTML({{tabId:'personal-table:x',kind:'personal-table',title:'实验表',payload:{{row:{{title:'实验表'}},page:{{title:'实验表',sheetName:'Sheet1',page:2,pageSize:50,total:80,hasNext:false,columns:[{{name:'硬度',meaning:'纳米硬度',unit:'GPa'}}],rows:[{{硬度:'4.1'}}]}}}}}});assert(tableHTML.includes('4.1'));assert(tableHTML.includes('上一页'));assert(tableHTML.includes('第 2 页'));
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        for marker in (
            'event.detail>1', 'addEventListener("dblclick"',
                'pinDocumentIdentity("paper"', 'pinDocumentTab(detail.tab.tabId)',
            'function openSecondaryEvidence(', 'function loadSecondaryPersonalTablePage(',
            'page:Number(row.page)||1,highlight', 'data-secondary-pdf-highlight-toggle',
            'state.searchOffset=offset+raw.length', 'pageNumber=append?state.searchPage+1:1',
            'payload.cause_code||payload.code', 'error.stage=cleanText(detail?.stage',
            'error.nextAction=cleanText(detail?.next_action',
            'aiErrorCopy(error,"处理未完成；本机已有数据不受影响，可稍后重试。")',
            'personal_ai_context_changed:"实验表格上下文已变化，旧建议没有应用。"',
            'selected_evidence_source_changed:"当前证据来源已变化，本次问题没有发送。"',
            'ai_runtime_binding_stale:"AI 运行配置已变化，本次请求已安全停止。"',
            'refresh_personal_preview:"请在实验页重新选择当前工作表并刷新预览。"',
            'choose_personal_file:"请在实验页点击“选择 CSV / TSV / XLSX”重新选择文件。"',
            'refresh_evidence_detail:"请从当前结果重新打开证据详情后再提问。"',
            'return_to_search_results:"请返回搜索结果并选择仍可用的证据。"',
        ):
            self.assertIn(marker, self.runtime)
        self.assertNotIn('button.addEventListener("dblclick",()=>selectPaper', self.runtime)

    def test_ai_failure_lineage_has_specific_chinese_recovery_guidance(self) -> None:
        for code in (
            "personal_ai_context_changed", "personal_ai_already_suggested", "personal_ai_busy",
            "personal_ai_not_configured", "personal_ai_unavailable", "personal_ai_invalid_response",
            "personal_import_session_expired", "personal_import_session_invalid", "personal_tabular_changed",
            "personal_tabular_invalid", "personal_tabular_snapshot_unavailable", "personal_tabular_unavailable",
            "selected_evidence_source_changed", "selected_evidence_unavailable",
            "selected_evidence_workspace_unavailable", "ai_runtime_binding_stale",
        ):
            self.assertIn(f'{code}:"', self.runtime)
        for action in (
            "refresh_personal_preview", "open_existing_suggestion", "wait_for_task", "save_credential",
            "retry_same_request", "choose_personal_file", "refresh_evidence_detail",
            "return_to_search_results", "repair_workspace", "verify_connection",
        ):
            self.assertIn(f'{action}:"', self.runtime)
        self.assertIn("AI_RECOVERY_CODE_COPY[code]||AI_ERROR_CODE_COPY[code]||fallback", self.runtime)
        self.assertIn("AI_RECOVERY_ACTION_COPY[nextAction]||AI_ERROR_ACTION_COPY[nextAction]", self.runtime)

    def test_editor_groups_render_their_own_payload_and_visual_pdf_highlight(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert');
class Classes{{constructor(){{this.values=new Set()}}toggle(key,value){{value?this.values.add(key):this.values.delete(key)}}add(key){{this.values.add(key)}}remove(key){{this.values.delete(key)}}}}
class El{{constructor(){{this.hidden=false;this.disabled=false;this.textContent='';this.innerHTML='';this.src='';this.scrollTop=0;this.classList=new Classes();this.attrs={{}};this.listeners={{}};}}setAttribute(key,value){{this.attrs[key]=String(value)}}removeAttribute(key){{delete this.attrs[key];if(key==='src')this.src=''}}addEventListener(key,fn){{(this.listeners[key]??=[]).push(fn)}}focus(){{globalThis.focused=this}}closest(){{return null}}}}
const ids={{}};for(const id of ['fusion-editor-group-content','fusion-primary-editor-surface','fusion-secondary-editor-surface','fusion-secondary-editor-title','fusion-secondary-editor-body','fusion-evidence-detail','fusion-evidence-detail-body','fusion-detail-heading','fusion-detail-identity','fusion-detail-pdf','fusion-detail-pdf-frame','fusion-detail-pdf-title','fusion-detail-open-pdf','fusion-detail-close-pdf','fusion-detail-show-highlight','fusion-detail-source-highlight','fusion-detail-highlight-image','fusion-detail-highlight-status'])ids['#'+id]=new El();
const all={{'[data-view-panel]':[],'[data-secondary-return-tab]':[],'[data-secondary-pdf-highlight-toggle]':[],'[data-secondary-open-pdf]':[],'[data-secondary-open-paper-pdf]':[],'[data-secondary-paper-evidence]':[],'[data-secondary-table-page]':[]}};
globalThis.document={{readyState:'loading',querySelector:selector=>ids[selector]||null,querySelectorAll:selector=>all[selector]||[],addEventListener:()=>{{}}}};globalThis.localStorage={{getItem:()=>null,setItem:()=>{{}}}};
let timer=null;globalThis.setTimeout=fn=>{{timer=fn;return 1}};globalThis.clearTimeout=()=>{{timer=null}};
    eval(fs.readFileSync({str(WEB / 'document_tab_store.js')!r},'utf8'));eval(fs.readFileSync({str(WEB / 'fusion_pdf_controller.js')!r},'utf8'));eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));const api=globalThis.AutoResearchFusion,tabs=api.documentTabs;
const row=(assetId,title,page)=>({{type:'table',title,label:title,caption:title+' caption',sourceScope:'workspace',assetId,paperId:56,page,articleTitle:'论文',imageUrl:'/api/visual-assets/'+assetId+'/image',bbox:[51,606,278,737],quantities:['硬度'],variables:{{column:'value'}},materials:['W'],tags:[]}});
const A=row(1357,'Table 2 · 纯bcc金属与MoNbTaVW基本性质对比表',7),B=row(1358,'Table 3 · 不同PKA类型级联缺陷统计表',8),C=row(1359,'Table 4 · 第三张表',9);
const a=tabs.open({{tabId:'evidence:a',kind:'evidence',ownerView:'paper',title:A.title,identity:{{sourceScope:'workspace',entityType:'table',entityUid:'1357'}},payload:{{row:A,status:'ready'}}}},{{pin:true}});
const c=tabs.open({{tabId:'evidence:c',kind:'evidence',ownerView:'paper',title:C.title,identity:{{sourceScope:'workspace',entityType:'table',entityUid:'1359'}},payload:{{row:C,status:'ready'}}}},{{pin:true}});tabs.activate(a.tabId);
const b=tabs.open({{tabId:'evidence:b',kind:'evidence',ownerView:'paper',title:B.title,identity:{{sourceScope:'workspace',entityType:'table',entityUid:'1358'}},payload:{{row:B,status:'ready'}}}},{{groupId:'secondary',pin:true}});
api.state.evidenceDetailOpen=true;api.renderEditorSurfaces();
assert(ids['#fusion-evidence-detail-body'].innerHTML.includes(A.title));assert(ids['#fusion-evidence-detail-body'].innerHTML.includes('/api/visual-assets/1357/image'));assert(!ids['#fusion-evidence-detail-body'].innerHTML.includes(B.title));
assert(ids['#fusion-secondary-editor-body'].innerHTML.includes(B.title));assert(ids['#fusion-secondary-editor-body'].innerHTML.includes('/api/visual-assets/1358/image'));assert(!ids['#fusion-secondary-editor-body'].innerHTML.includes(A.title));
const generation=tabs.beginRequest(b.tabId);tabs.completeRequest(b.tabId,generation,{{payload:{{row:{{...B,caption:'Table 3 late complete'}},status:'ready'}}}});api.renderEditorSurfaces();assert(ids['#fusion-evidence-detail-body'].innerHTML.includes(A.title));assert(ids['#fusion-secondary-editor-body'].innerHTML.includes('Table 3 late complete'));
tabs.activate(c.tabId);api.renderEditorSurfaces();assert(ids['#fusion-evidence-detail-body'].innerHTML.includes(C.title));assert(ids['#fusion-secondary-editor-body'].innerHTML.includes(B.title));
tabs.move(c.tabId,'secondary');assert.equal(tabs.activeTab('primary').tabId,a.tabId);tabs.move(c.tabId,'primary');assert.equal(tabs.activeTab('secondary').tabId,b.tabId);
const projected=api.publicEvidence({{asset_type:'table',id:1358,paper_id:56,page_start:8,bbox:[51.172,606.053,278.053,737.169],label:B.title,image_url:'/api/visual-assets/1358/image'}});assert.deepEqual(projected.bbox,[51.172,606.053,278.053,737.169]);
    tabs.move(a.tabId,'secondary');tabs.activate(b.tabId);tabs.move(b.tabId,'primary');assert.equal(tabs.activeTab('secondary').tabId,a.tabId);api.state.evidenceDetail=projected;api.state.evidenceDetailOpen=true;assert(api.openDetailPDF({{openTab:false}}));assert.equal(ids['#fusion-detail-pdf-frame'].src,'/api/papers/56/pdf#page=8&zoom=page-width');
setImmediate(()=>{{assert.equal(ids['#fusion-detail-show-highlight'].disabled,false);assert.equal(ids['#fusion-detail-source-highlight'].hidden,false);assert.equal(ids['#fusion-detail-highlight-image'].src,'/api/visual-assets/1358/image');assert.equal(api.state.sourceHighlight.sourceTabId,b.tabId);timer();assert.equal(ids['#fusion-detail-source-highlight'].hidden,true);assert.equal(ids['#fusion-detail-show-highlight'].textContent,'重新显示高亮');api.toggleSourceHighlight();setImmediate(()=>{{assert.equal(ids['#fusion-detail-source-highlight'].hidden,false);assert.equal(ids['#fusion-detail-highlight-image'].src,'/api/visual-assets/1358/image');assert.equal(tabs.activeTab('secondary').tabId,a.tabId);}});}});
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_workspace_search_pagination_uses_server_offset_not_projected_rows(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert');
class Classes{{toggle(){{}}add(){{}}remove(){{}}}}
class El{{constructor(){{this.hidden=false;this.disabled=false;this.value='dose';this.textContent='';this.innerHTML='';this.dataset={{}};this.classList=new Classes();this.attrs={{}};}}setAttribute(k,v){{this.attrs[k]=String(v)}}removeAttribute(k){{delete this.attrs[k]}}addEventListener(){{}}insertAdjacentHTML(_where,html){{this.innerHTML+=html}}focus(){{}}querySelector(){{return null}}querySelectorAll(){{return []}}}}
const ids={{}};for(const id of ['fusion-search-query','fusion-search-results','fusion-primary-tab-label','fusion-run-precise-search','fusion-open-librarian','fusion-inspector-title','fusion-inspector-body','fusion-evidence-ai','fusion-evidence-ai-reason','fusion-evidence-ai-question','fusion-evidence-ai-output','fusion-search-export-note','fusion-status-operation','fusion-count-item','fusion-count-finding','fusion-count-table','fusion-count-figure'])ids['#'+id]=new El();
const panels=[{{dataset:{{searchModePanel:'precise'}},hidden:false}},{{dataset:{{searchModePanel:'librarian'}},hidden:true}}];
globalThis.document={{readyState:'loading',querySelector:selector=>ids[selector]||null,querySelectorAll:selector=>selector==='[data-search-mode-panel]'?panels:[],addEventListener:()=>{{}}}};globalThis.localStorage={{getItem:()=>null,setItem:()=>{{}}}};
const urls=[];globalThis.fetch=async url=>{{url=String(url);urls.push(url);const offset=Number(new URL(url,'http://local').searchParams.get('offset')||0),rows=offset===0?[{{entity_type:'item',id:1,item_id:1,paper_id:1,meaning:'硬度'}},{{entity_type:'unsupported',id:2}}]:[{{entity_type:'finding',id:3,item_id:3,paper_id:1,finding_text:'结论'}}];return{{ok:true,headers:{{get:()=>null}},json:async()=>({{rows,total:3}})}};}};
eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));const api=globalThis.AutoResearchFusion;api.state.view='search';api.state.searchSource='workspace';
(async()=>{{await api.runPreciseSearch();assert.equal(api.state.searchResults.length,1);assert.equal(api.state.searchOffset,2);assert.equal(api.state.searchHasMore,true);assert.equal(api.state.selectionByView.search,null,'a fresh search must not silently open the inspector');assert.equal(api.state.selectedEvidence,null);await api.runPreciseSearch({{append:true}});assert.equal(api.state.searchResults.length,2);assert.equal(api.state.searchOffset,3);assert.equal(api.state.searchHasMore,false);assert(urls[1].includes('offset=2'));}})().catch(error=>{{console.error(error);process.exitCode=1;}});
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_tabs_async_and_inspector_states_are_partitioned(self) -> None:
        for marker in (
            "inspectorState:{paper:null,search:null,personal:null,package:null,settings:null}",
            "state.inspectorState[view]={kind:\"evidence\"",
            "state.inspectorState.personal={kind:\"cell\"",
            "state.inspectorState.package={kind:\"package-job\"",
            "state.inspectorState.settings={kind:\"settings\"",
            "documentTabs?.beginRequest(tab.tabId)",
            "documentTabs?.completeRequest(tab.tabId,tabRequest",
            'openDocumentTab("package-job"', 'payload:{job}',
            'payload:{section:"answer",html:answerHTML}',
            'payload:{section:"citations",html:citationsHTML}',
            'payload:{section:"recommendations",html:recommendationsHTML}',
        ):
            self.assertIn(marker, self.runtime)
        for forbidden in ("/api/context-chat", "/api/agents/librarian/chat"):
            self.assertNotIn(forbidden, self.runtime)
        self.assertNotIn('payload:{jobId:String(job.job_id),job}', self.runtime)
        self.assertNotIn('任务 ${esc(job.job_id', self.runtime)

    def test_catalog_scroll_and_server_side_four_type_filters(self) -> None:
        for marker in (
            "minmax(0,1fr)", "overflow:auto", "scrollbar-gutter:stable",
            'data-evidence-type="item"', 'data-evidence-type="finding"',
            'data-evidence-type="table"', 'data-evidence-type="figure"',
            'aria-pressed="true"', 'scrollIntoView?.({block:"nearest",inline:"nearest"})',
            '&types=${encodeURIComponent(types.join(","))}',
            '&entity_type=${encodeURIComponent(type)}',
        ):
            self.assertIn(marker, self.index + self.runtime + self.css)
        self.assertNotIn("state.evidence.slice(0,12)", self.runtime)
        self.assertIn("types.join(\",\")!==selectedEvidenceTypes().join(\",\")", self.runtime)

    def test_inspector_state_is_partitioned_by_view(self) -> None:
        for marker in (
            "selectionByView:{paper:null,search:null,personal:null,package:null,settings:null}",
            "const INSPECTOR_RENDERERS=Object.freeze({",
            'paper:()=>evidenceInspector("paper")',
            'search:()=>evidenceInspector("search")',
            "state.inspectorState[view]?.row",
        ):
            self.assertIn(marker, self.runtime)

    def test_detail_uses_public_fields_and_keeps_scientific_images_authoritative(self) -> None:
        for marker in (
            "physical_quantities", "variables", "materials", "conditions_text", "methods_text",
            "linked_item_count", "source_excerpt", "source_locator", "quality_gate_status",
            "本机 PyMuPDF", "不会生成替代图", "filter:none", "mix-blend-mode:normal",
        ):
            self.assertIn(marker, self.runtime + self.css)
        for forbidden in ("pdf_path", "image_path", "zotero_key", "local_article_key", "reviewer", "edit_note"):
            self.assertNotIn(f"raw?.{forbidden}", self.runtime)
        self.assertNotIn("appendChild", self.runtime)
        self.assertNotIn("_blank", self.index + self.runtime)

    def test_imported_literature_pdf_reuses_central_viewer(self) -> None:
        self.assertIn("查看原文", self.index)
        self.assertIn('federated-pdf?source_id=${encodeURIComponent(row.sourceId)}&paper_uid=${encodeURIComponent(row.paperUid)}', self.runtime)
        program = f"""
globalThis.document={{readyState:'loading',querySelector:()=>null,querySelectorAll:()=>[],addEventListener:()=>{{}}}};
globalThis.localStorage={{getItem:()=>null,setItem:()=>{{}}}};
eval(require('fs').readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));
const api=globalThis.AutoResearchFusion,assert=require('assert');
const literature=api.publicEvidence({{entity_type:'table',source_scope:'private',source_id:'lab / 甲',entity_uid:'table:1',paper_uid:'paper W?1',collection_kind:'literature_collection',pdf_available:true,display_title:'表格'}});
assert.equal(api.detailPDFURL(literature),'/api/desktop/federated-pdf?source_id=lab%20%2F%20%E7%94%B2&paper_uid=paper%20W%3F1');
const personal=api.publicEvidence({{entity_type:'table',source_scope:'private',source_id:'personal',entity_uid:'table:2',paper_uid:'paper-x',collection_kind:'personal_experiments',pdf_available:true,display_title:'私人表'}});
assert.equal(api.detailPDFURL(personal),'');
assert.equal(api.detailPDFURL({{sourceScope:'official',sourceId:'official',paperUid:'paper-x',pdfAvailable:true,collectionKind:'literature_collection'}}),'/api/desktop/federated-pdf?source_id=official&paper_uid=paper-x');
assert.equal(api.detailPDFURL({{sourceScope:'private',sourceId:'literature',paperUid:'paper-x',pdfAvailable:false,collectionKind:'literature_collection'}}),'');
assert.equal(api.detailPDFURL({{sourceScope:'workspace',paperId:7,page:3}}),'/api/papers/7/pdf');
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_active_fusion_copy_uses_product_language_and_honest_batch_exports(self) -> None:
        for forbidden in ("READ-ONLY", "LIVE PUBLIC SNAPSHOT", "只读 GET", "English", "计划支持", "系统安全存储", "系统安全凭据存储"):
            self.assertNotIn(forbidden, self.index + self.runtime)
        for marker in (
            "文献资料 · 当前资料库", "当前检索批量导出",
            "表格、图片及其他资料源请用单条证据导出或资料包",
            "英语界面尚未提供",
            "当前电脑的私有加密存储", "本机加密存储",
        ):
            self.assertIn(marker, self.index + self.runtime)
        self.assertEqual(self.runtime.count('item:"/api/six-export"'), 1)
        self.assertEqual(self.runtime.count('finding:"/api/qualitative-export"'), 1)
        self.assertNotIn('table:"/api/six-export"', self.runtime)
        self.assertNotIn('figure:"/api/six-export"', self.runtime)

    def test_search_sources_use_workspace_and_federated_routes(self) -> None:
        for source in ("workspace", "official", "private", "all"):
            self.assertEqual(self.index.count(f'data-search-source="{source}"'), 1)
        for marker in (
            'federatedSearch:"/api/desktop/federated-search"', 'source_scope=official',
            'source_scope=private', 'setSearchSource(button.dataset.searchSource',
            'page.results.map(hit=>hit?.document)', 'source!==state.searchSource',
        ):
            self.assertIn(marker, self.runtime)
        self.assertNotIn("官方资料库</span><b>0.9.2+", self.index)
        self.assertNotIn("我的实验</span><b>合成", self.index)

    def test_prepared_actions_reuse_versioned_disclosure_gate(self) -> None:
        for marker in (
            "globalThis.AutoResearchAIConsent", ".accepted(scope,disclosureContext)", ".remember(scope,disclosureContext)", "disclosureSummary(scope,disclosureContext)", "ai_consent_gate_unavailable",
            "updateTrustedProviders", "prepared.provider_id!==context.provider_id",
            "prepared.disclosure_version!==disclosureVersion",
            "literature-extraction-commit-result-v2", "一次授权覆盖四类提取、双分支质量核验、发布与索引",
            "librarian:8", "selected_evidence_chat:2", "personal_suggestion:2", "calls>AI_CALL_LIMITS[scope]",
            "librarianBusy:false", "if(state.librarianBusy)return", "harness_budget_exhausted",
            "本阶段最多调用 ${calls} 次", "harness_dependency_mismatch",
            "AI 执行环境版本不兼容", "文献证据源当前不可用",
        ):
            self.assertIn(marker, self.runtime)
        self.assertNotIn("consent:true", self.runtime)
        self.assertNotIn("validLiteratureStage", self.runtime)
        self.assertNotIn("stage<32", self.runtime)
        self.assertNotIn('localStorage.setItem("job_token', self.runtime)
        self.assertNotIn('localStorage.setItem("resume_token', self.runtime)
        self.assertNotIn("/api/agents/librarian/chat", self.runtime)
        self.assertNotIn("/api/context-chat", self.runtime)

    def test_literature_extraction_uses_one_task_authorization(self) -> None:
        start = self.runtime.index("async function runLiteratureExtraction()")
        end = self.runtime.index("function runPDFAction", start)
        workflow = self.runtime[start:end]
        self.assertEqual(
            workflow.count('preparedAuthorization("literature_extraction"'), 1
        )
        self.assertEqual(
            workflow.count('executePrepared("literature_extraction"'), 1
        )
        self.assertIn("domainRequest={job_token:recovery.resumeToken}", workflow)
        self.assertIn("正在免费检查论文、PDF 与可恢复任务", workflow)
        self.assertNotIn("ensureAIReadiness", workflow)
        self.assertNotIn("generation!==state.literatureAction", workflow)
        self.assertNotIn("for(", workflow)
        self.assertIn("applyLiteratureResult(result)", workflow)
        self.assertIn("没有执行文献提取模型调用", workflow)

    def test_literature_jobs_reconnect_without_reauthorization_and_use_full_lease(self) -> None:
        for marker in (
            "ai-execution-job-list-v1",
            'const job=listed.jobs[0]',
            'executePrepared("literature_extraction",null,{existingJob:job,literaturePaper:jobPaper})',
            "31*60*1000",
            "30*60*1000",
            "任务仍在后台继续，可离开此页并稍后返回查看",
            'if(previous==="paper")closeCurrentPDF',
            'if(name==="paper")void loadLiteratureTaskDirectory().then(()=>reconnectLiteratureJob())',
            "function literaturePaperKey(paper)",
            "后台文献处理完成",
        ):
            self.assertIn(marker, self.runtime)
        self.assertNotIn('if(previous==="paper"){state.literatureAction+=1', self.runtime)

    def test_literature_task_directory_is_ephemeral_and_user_driven(self) -> None:
        for marker in (
            'literatureTaskDirectory:"/api/desktop/ai/actions/literature_extraction/task-directory"',
            "function publicLiteratureTaskDirectory(raw)",
            'nextAction==="resume_extraction"',
            "▶ 恢复上次提取",
            "后端将从已保存检查点继续",
            "本机正在或已尝试零模型收尾",
            "上次调用结果无法确认",
            "literature-extraction-receipt-summary-v1",
            "旧任务没有可恢复的计数收据",
        ):
            self.assertIn(marker, self.runtime)
        self.assertNotIn('localStorage.setItem("resume', self.runtime)
        self.assertNotIn('localStorage.setItem("literatureTask', self.runtime)

    def test_literature_reconnect_runtime_preserves_paid_job_and_free_preflight(self) -> None:
        program = f"""
const assert=require('assert'),fs=require('fs');
globalThis.document={{readyState:'loading',documentElement:{{dataset:{{}},style:{{setProperty(){{}}}}}},body:{{dataset:{{}}}},querySelector:()=>null,querySelectorAll:()=>[],addEventListener(){{}},createElement:()=>({{dataset:{{}},append(){{}},replaceChildren(){{}},setAttribute(){{}}}})}};
globalThis.localStorage={{getItem:()=>null,setItem(){{}}}};globalThis.addEventListener=()=>{{}};
const response=(body,ok=true)=>Promise.resolve({{ok,headers:{{get:()=>null}},json:async()=>body}});
const commit={{schema_version:'literature-extraction-commit-result-v2',status:'completed',paper:{{title:'W-Ta',doi:'10.1/example'}},visual_evidence_ready:true,visual_stage_status:'ready',candidate_count:3,published_item_count:2,existing_item_count:1,manual_review_count:0,table_candidate_count:1,figure_candidate_count:1,extraction_receipt:{{schema_version:'literature-extraction-receipt-v1'}},publication_receipt:{{schema_version:'literature-publication-receipt-v1'}},dataset_receipt:{{schema_version:'dataset-membership-receipt-v1'}},search_index:{{status:'refreshed'}}}};
const completed={{schema_version:'ai-execution-job-v1',scope:'literature_extraction',job_id:'ai_job_abcdefghijklmnopqrstuvwxyz',status:'completed',events:[],result:commit}};
let calls=[];globalThis.fetch=(url,options={{}})=>{{calls.push([String(url),String(options.method||'GET')]);if(String(url)==='/api/desktop/ai/actions/literature_extraction/task-directory')return response({{schema_version:'literature-extraction-task-directory-v1',tasks:[],startup_recovery:{{recovered:0,already_completed:0,skipped_or_blocked:0}},issues:[]}});if(String(url)==='/api/desktop/ai/actions/literature_extraction/jobs')return response({{schema_version:'ai-execution-job-list-v1',scope:'literature_extraction',jobs:[completed]}});if(String(url)==='/api/desktop/ai/actions/literature_extraction/prepare')return response({{code:'literature_pdf_missing',message:'PDF 不可用',next_action:'replace_valid_pdf'}},false);throw new Error('unexpected '+url);}};
eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));const api=globalThis.AutoResearchFusion;
(async()=>{{
 api.state.view='search';api.state.paper={{id:7,title:'W-Ta',doi:'10.1/example'}};const result=await api.reconnectLiteratureJob();assert.equal(result.status,'completed');assert.equal(api.state.view,'search','background completion must not steal navigation');assert.equal(api.state.literatureReceipt.status,'completed');assert.deepEqual(calls,[['/api/desktop/ai/actions/literature_extraction/task-directory','GET'],['/api/desktop/ai/actions/literature_extraction/jobs','GET']],'reconnect adopts latest job without prepare consent or execute');
 api.state.paper={{id:8,title:'Other paper',doi:'10.1/other'}};api.state.literatureReceipt=null;calls=[];await api.reconnectLiteratureJob();assert.equal(api.state.literatureReceipt,null,'a receipt must not attach to another paper');assert.deepEqual(calls,[['/api/desktop/ai/actions/literature_extraction/jobs','GET']]);
 const directory=api.publicLiteratureTaskDirectory({{schema_version:'literature-extraction-task-directory-v1',tasks:[{{resume_token:'resume_abcdefghijklmnop',state:'paused',stage:'coverage_gap',paper:{{title:'W-Ta',doi:'10.1/example'}},completed_calls:2,spent_calls:2,max_calls:8,updated_at:20,expires_at:200,next_action:'resume_extraction',receipt:null}}],startup_recovery:{{recovered:1,already_completed:0,skipped_or_blocked:0}},issues:[]}});assert(directory);api.state.paper={{id:7,title:'W-Ta',doi:'10.1/example'}};api.state.literatureTask.directory=directory;assert.equal(api.currentLiteratureRecovery().resumeToken,'resume_abcdefghijklmnop');assert.equal(JSON.stringify(globalThis.localStorage).includes('resume_'),false);
 const summary=api.publicLiteratureReceiptSummary({{schema_version:'literature-extraction-receipt-summary-v1',status:'saved_index_pending',candidate_count:9,published_item_count:6,existing_item_count:1,manual_review_count:2,table_candidate_count:3,figure_candidate_count:4,visual_evidence_ready:true,visual_stage_status:'ready',search_index:{{status:'pending',document_count:null}},dataset_partition:'validation',entity_uids:['secret'],path:'/private/a'}});assert(summary);assert.equal(summary.publishedItemCount,6);assert.equal(summary.searchIndex.documentCount,null);assert.equal(summary.entity_uids,undefined);assert.equal(summary.path,undefined);
 const noVisual=api.publicLiteratureReceiptSummary({{schema_version:'literature-extraction-receipt-summary-v1',status:'completed',candidate_count:2,published_item_count:2,existing_item_count:0,manual_review_count:0,table_candidate_count:0,figure_candidate_count:0,visual_evidence_ready:false,visual_stage_status:'not_found',search_index:{{status:'refreshed',document_count:2}},dataset_partition:'train'}});assert(noVisual);assert.equal(noVisual.visualEvidenceReady,false);assert.equal(noVisual.visualStageStatus,'not_found');
 calls=[];await api.preparedAuthorization('literature_extraction',{{paper_id:7,force_rescan:false}}).then(()=>assert.fail('bad PDF must fail before readiness'),error=>assert.equal(error.code,'literature_pdf_missing'));assert.deepEqual(calls,[['/api/desktop/ai/actions/literature_extraction/prepare','POST']]);
 const running={{schema_version:'ai-execution-job-v1',scope:'literature_extraction',job_id:'ai_job_running_abcdefghijklmnop',status:'running',events:[]}},originalNow=Date.now;let tick=0;Date.now=()=>tick++===0?0:1800001;const background=await api.executePrepared('literature_extraction',null,{{existingJob:running,deadlineMs:0}});Date.now=originalNow;assert.equal(background.schema_version,'ai-execution-background-v1');assert.equal(api.state.literatureTask.status,'running');
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
"""
        result = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, timeout=8, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_personal_preview_review_and_single_import_are_real(self) -> None:
        for marker in (
            "select_personal_data_file", 'personalPreview:"/api/desktop/personal-imports/preview"',
            "personal-import-preview-v1", "personal-import-suggestion-v1", "/reviewed-import",
            "reviewed: true", "collectPersonalDraft", "受限文件检查", "一次确认", "result.indexable !== true",
            "personal-tabular-page-v1", "PERSONAL_IMPORT_ROWS_PATH", "loadPersonalPreviewPage",
            "本次确认只导入当前所选工作表", '中央顶部“导入 PDF”',
            'id="fusion-personal-series-add"', 'id="fusion-personal-series-list"',
            'id="fusion-run-conditions"', 'id="fusion-run-note"',
            "addPersonalSeries", "removePersonalSeries", "refreshPersonalSeriesColumns",
            "parsePersonalConditions", "readPersonalSeriesEditor({strict: true})",
            "AI 不是必需项", "user_note",
        ):
            self.assertIn(marker, self.index + self.runtime + self.personal_runtime)
        self.assertNotIn("sample_rows", self.personal_runtime)
        collect = self.personal_runtime[
            self.personal_runtime.index("function collectPersonalDraft()") :
            self.personal_runtime.index("function publicPersonalImportNextAction(")
        ]
        self.assertNotIn("personalSuggestion?.series", collect)
        self.assertNotIn("path", collect.casefold())
        self.assertNotIn("api_key", collect.casefold())
        self.assertNotIn("sha256", collect.casefold())
        self.assertIn("draft.run.user_note = userNote", collect)
        self.assertIn("conditions = parsePersonalConditions", collect)
        self.assertIn("if (changed) resetPersonalSheetEditors()", self.personal_runtime)
        self.assertIn("列角色已改为忽略，相关序列引用已清空", self.personal_runtime)
        self.assertNotIn('id="fusion-mark-reviewed"', self.index)
        self.assertNotIn('id="fusion-demo-suggestion"', self.index)
        self.assertNotIn("showDemoSuggestion", self.personal_runtime)

    def test_manual_personal_series_and_conditions_runtime_contract(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert');
class El{{constructor(value=''){{this.value=value;this.textContent='';this.hidden=false;this.disabled=false;this.innerHTML='';this.dataset={{}};}}addEventListener(){{}}focus(){{}}querySelectorAll(){{return []}}}}
const ids={{}};for(const id of ['fusion-project-name','fusion-sample-name','fusion-sample-material','fusion-run-name','fusion-run-method','fusion-run-conditions','fusion-run-note','fusion-personal-series-list','fusion-personal-series-warning','fusion-personal-confirm','fusion-personal-status','fusion-status-operation'])ids['#'+id]=new El();
Object.assign(ids['#fusion-project-name'],{{value:'Project'}});Object.assign(ids['#fusion-sample-name'],{{value:'Sample'}});Object.assign(ids['#fusion-run-name'],{{value:'Run'}});Object.assign(ids['#fusion-run-method'],{{value:'nanoindentation'}});ids['#fusion-run-conditions'].value='温度=300 K\\n载荷=10 mN';ids['#fusion-run-note'].value='人工核验备注';
const control=(value='')=>({{value,addEventListener(){{}}}}),columns=[
 {{dataset:{{sourceName:'Dose'}},querySelector:s=>s.includes('role')?control('independent'):s.includes('meaning')?control('辐照剂量'):control('dpa')}},
 {{dataset:{{sourceName:'Hardness'}},querySelector:s=>s.includes('role')?control('dependent'):s.includes('meaning')?control('硬度'):control('GPa')}},
 {{dataset:{{sourceName:'Error'}},querySelector:s=>s.includes('role')?control('uncertainty'):s.includes('meaning')?control('误差'):control('GPa')}}
];
const values={{'[data-series-name]':control('硬度曲线'),'[data-series-x]':control('Dose'),'[data-series-y]':control('Hardness'),'[data-series-uncertainty]':control('Error'),'[data-series-description]':control('手工建立')}};
const seriesRow={{dataset:{{seriesId:'series-2'}},querySelector:s=>values[s]}};
const all={{'[data-personal-column]':columns,'[data-personal-series-row]':[seriesRow]}};
globalThis.document={{readyState:'loading',querySelector:s=>ids[s]||null,querySelectorAll:s=>all[s]||[],addEventListener(){{}},documentElement:{{dataset:{{}},style:{{setProperty(){{}}}}}},body:{{dataset:{{}}}}}};globalThis.localStorage={{getItem:()=>null,setItem(){{}}}};globalThis.addEventListener=()=>{{}};
eval(fs.readFileSync({str(WEB / 'fusion_personal_import.js')!r},'utf8'));eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));const api=globalThis.AutoResearchFusion;api.state.personalPreview={{sheets:[{{sheet_name:'Sheet1',columns:[{{source_name:'Dose'}},{{source_name:'Hardness'}},{{source_name:'Error'}}]}}]}};api.state.personalStatus={{revision:3}};
const draft=api.collectPersonalDraft();assert.deepEqual(draft.run.conditions,{{'温度':'300 K','载荷':'10 mN'}});assert.equal(draft.run.user_note,'人工核验备注');assert.equal(draft.series.length,1);assert.equal(draft.series[0].x_column,'Dose');assert.equal(draft.series[0].uncertainty_column,'Error');assert(!JSON.stringify(draft).match(/path|api_key|sha256/i));
api.state.personalSeriesCounter=1;assert.equal(api.addPersonalSeries(),true);assert(ids['#fusion-personal-series-list'].innerHTML.includes('data-series-id="series-3"'));assert.equal((ids['#fusion-personal-series-list'].innerHTML.match(/data-series-id="series-2"/g)||[]).length,1);
all['[data-personal-series-row]']=[seriesRow,{{dataset:{{seriesId:'series-2'}},querySelector:s=>values[s]}}];assert.throws(()=>api.collectPersonalDraft(),/标识重复/);all['[data-personal-series-row]']=[{{dataset:{{seriesId:''}},querySelector:s=>values[s]}}];assert.throws(()=>api.collectPersonalDraft(),/标识无效/);all['[data-personal-series-row]']=[seriesRow];
assert.throws(()=>api.parsePersonalConditions('温度=300 K\\n温度=500 K'),/重复/);assert.throws(()=>api.parsePersonalConditions('=300 K'),/不能为空/);
columns[2].querySelector=s=>s.includes('role')?control('ignore'):s.includes('meaning')?control('误差'):control('GPa');assert.equal(api.refreshPersonalSeriesColumns(),1);assert(ids['#fusion-personal-series-warning'].textContent.includes('引用已清空'));
"""
        result = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, timeout=8, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_personal_import_next_action_opens_exact_table_without_broad_search(self) -> None:
        for marker in (
            "publicPersonalImportNextAction", "openImportedPersonalTable",
            'raw.kind !== "open_personal_table"', 'raw.source_scope !== "private"',
            'raw.entity_type !== "table"', "loadSecondaryPersonalTablePage",
            "loadPrivateTablePage", "personal_next_action_unavailable",
            "personal_search_refresh_failed", "data-secondary-return-personal",
        ):
            self.assertIn(marker, self.runtime + self.personal_runtime)
        confirm = self.personal_runtime[
            self.personal_runtime.index("async function confirmPersonalImport()") :
            self.personal_runtime.index("function bind()")
        ]
        self.assertNotIn("runPreciseSearch", confirm)
        self.assertNotIn('switchView("search")', confirm)
        self.assertNotIn("title===", confirm)
        self.assertNotIn("sha256", confirm.casefold())
        self.assertNotIn("path", confirm.casefold())

    def test_provider_settings_use_runtime_readiness_and_secure_credentials(self) -> None:
        for marker in (
            "/api/desktop/ai/providers", "/api/desktop/ai/settings", "/api/desktop/ai/credentials/",
            "test-actions", "expected_revision", "task_models", "api_key", 'type="password"',
            "/api/desktop/ai/custom-provider", "ai-readiness-v1", "provider_connection",
            "businesses", "连接验证最多调用模型 1 次", "不会回显",
            "fusion-status-ai", "data-ai-readiness-scope", "高级提供商配置",
        ):
            self.assertIn(marker, self.index + self.runtime)
        self.assertNotIn("base_url", self.runtime)
        self.assertIn("chat_endpoint", self.runtime)
        self.assertNotIn("固定受信模型", self.index + self.runtime)
        self.assertNotIn("不接受自定义 URL", self.index + self.runtime)

    def test_package_center_has_complete_safe_sequences(self) -> None:
        for element_id in (
            "fusion-package-official-select", "fusion-package-installed",
            "fusion-package-official-result", "fusion-package-open-official-search",
            "fusion-package-literature-plan", "fusion-package-literature-export",
            "fusion-package-personal-plan", "fusion-package-personal-export",
            "fusion-package-user-select", "fusion-package-user-sha",
            "fusion-package-checksum-ack", "fusion-package-unencrypted-ack",
            "fusion-package-source-ack", "fusion-package-user-import",
            "fusion-package-dataset", "fusion-dataset-include-private",
            "fusion-dataset-plan", "fusion-dataset-plan-button",
            "fusion-dataset-result", "fusion-dataset-metrics",
            "fusion-dataset-unreviewed-ack", "fusion-dataset-rights-ack",
            "fusion-dataset-export", "fusion-dataset-receipt",
            "fusion-package-jobs",
            "fusion-package-history", "fusion-package-history-clear",
        ):
            self.assertEqual(self.index.count(f'id="{element_id}"'), 1)
        package_sources = self.runtime + self.package_runtime
        for marker in (
            'select_evidence_package', 'select_package_export_destination',
            '"/api/desktop/evidence-packages/import"',
            '"/api/desktop/evidence-packages/rollback"',
            '"/api/desktop/package-center/export-plan"',
            '"/api/desktop/package-center/export"',
            '"/api/desktop/package-center/inspect"',
            '"/api/desktop/package-center/import"',
            '"/api/desktop/package-center/dataset-plan"',
            '"/api/desktop/package-center/dataset-export"',
            'packageReceipts:"/api/desktop/package-center/receipts"',
            'packageHistory:"/api/desktop/package-center/history"',
            'select_dataset_export_destination', 'Auto-Research-dataset.zip',
            'include_private: includePrivate', 'rights_acknowledged:',
            'unreviewed_acknowledged:', 'dataset-export-plan-v1',
            'dataset-bundle-v1', 'binary_assets_included !== false',
            'expected_sha:', 'checksum_ack: true', 'keep_conflicts:',
            'unencrypted_ack: true', 'unauthenticated_source_ack: true',
            'internal_use_only_ack: true', 'paper_rights: paperRights',
            'plan.exceeds_size_limit === true', 'await waitPackageJob',
            'schema === "package-summary-v1"', 'raw.next_action',
            'next.search_source !== "official"', 'rememberOfficialPackageResult(completed)',
            'setSearchSource("official")',
        ):
            self.assertIn(marker, package_sources)
        self.assertGreaterEqual(
            self.runtime.count("ROUTES.packageReceipts"),
            3,
            "the fixed receipts route must be authorized for GET, POST, and read-only projection",
        )
        self.assertIn("前往搜索官方资料", self.index)
        export_body = re.search(
            r"async function exportPlannedPackage\(kind\) \{(?P<body>.*?)\n    function updateUserPackageImportButton",
            self.package_runtime,
            re.DOTALL,
        )
        self.assertIsNotNone(export_body)
        body = export_body.group("body")
        self.assertLess(body.index("plan_token"), body.index("choosePackageDestination"))
        self.assertLess(body.index("choosePackageDestination"), body.index("package-center/export"))
        self.assertIn("选择保存位置并导出", self.index)
        self.assertIn("2 GB", self.package_runtime)
        self.assertIn("另一条可信渠道", self.index + self.package_runtime)
        self.assertIn("未加密", self.index + self.package_runtime)
        self.assertIn("不认证发送者身份", self.index + self.package_runtime)
        self.assertIn("JSONL、Parquet 和数据卡", self.index)
        self.assertIn("PDF 和图片二进制不会复制进训练载荷", self.index)
        self.assertRegex(self.index, r'id="fusion-dataset-include-private"(?![^>]*checked)')
        program = f"""
const nodes={{}};for(const id of ['fusion-package-official-result','fusion-package-open-official-search','fusion-package-result-title','fusion-package-result-outcome','fusion-package-result-counts'])nodes['#'+id]={{hidden:false,textContent:'',innerHTML:''}};
globalThis.document={{readyState:'loading',querySelector:selector=>nodes[selector]||null,querySelectorAll:()=>[],addEventListener:()=>{{}}}};
let storageWrites=0;globalThis.localStorage={{getItem:()=>null,setItem:()=>{{storageWrites+=1}}}};
eval(require('fs').readFileSync({str(WEB / 'fusion_package_center.js')!r},'utf8'));
eval(require('fs').readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));
const api=globalThis.AutoResearchFusion,assert=require('assert');
const summary={{schema:'package-summary-v1',package_kind:'official_evidence',package_id:'official-main',package_version:'1.1',outcome:'activated',trusted_official:true,content_counts:{{paper_count:60,item_count:3142,finding_count:936,table_count:46,figure_count:232}},asset_counts:{{pdf_count:42,visual_asset_count:278}},next_action:{{view:'search',search_source:'official'}}}};
const result=api.publicOfficialPackageResult({{result:summary}});
assert.equal(result.outcome,'activated');assert.equal(result.nextAction.searchSource,'official');assert(result.counts.some(row=>row[0]==='PDF'&&row[1]===42));
assert.equal(api.publicOfficialPackageResult({{result:{{...summary,next_action:{{view:'search',search_source:'private'}}}}}}),null);
assert.equal(api.publicOfficialPackageResult({{result:{{...summary,trusted_official:false}}}}),null);
const fingerprint='a'.repeat(64),current={{active:true,repository_audited:true,can_search_offline:true,package_id:'official-main',package_version:'1.1',content_fingerprint:fingerprint}},content={{papers:60,entities:4369,items:3142,findings:936,tables:46,figures:245}},installed={{schema:'installed-official-package-v1',package_id:'official-main',package_version:'1.1',content_fingerprint:fingerprint,installed_at:'2026-08-27T00:00:00Z',active:true,audit_status:'ready',content_counts:content,asset_counts:{{paper_pdfs:59,visual_assets:291}},error_code:null}},center={{schema:'package-center-status-v1',official:{{current,installed_versions:[installed]}}}};
let active=api.publicActiveOfficialPackageSummary(center);assert(active);assert.equal(active.outcome,'current_active');assert.deepEqual(active.counts,[['论文',60],['测量',3142],['结论',936],['表格',46],['图片',245],['PDF',59],['视觉资产',291]]);
const v1={{...center,official:{{current,installed_versions:[{{...installed,asset_counts:{{}}}}]}}}};active=api.publicActiveOfficialPackageSummary(v1);assert(active);assert.equal(active.counts.some(row=>row[0]==='PDF'||row[0]==='视觉资产'),false,'v1 missing assets must not be rendered as zero');
assert.equal(api.publicActiveOfficialPackageSummary({{...center,official:{{current,installed_versions:[{{...installed,content_fingerprint:'b'.repeat(64)}}]}}}}),null);
assert.equal(api.publicActiveOfficialPackageSummary({{...center,official:{{current,installed_versions:[installed,{{...installed}}]}}}}),null);
for(const bad of [{{...installed,content_counts:{{...content,papers:-1}}}},{{...installed,content_counts:{{...content,papers:true}}}},{{...installed,content_counts:{{...content,entities:999}}}},{{...installed,content_counts:{{...content,path:'/private/a'}}}},{{...installed,asset_counts:{{paper_pdfs:59,visual_assets:291,path:'/private/a'}}}},{{...installed,install_path:'/private/a'}}])assert.equal(api.publicActiveOfficialPackageSummary({{...center,official:{{current,installed_versions:[bad]}}}}),null);
api.state.package.center=center;api.state.package.lastOfficialResult=null;api.renderOfficialPackageResult();assert.equal(nodes['#fusion-package-result-outcome'].textContent,'当前已启用');assert(nodes['#fusion-package-result-counts'].innerHTML.includes('>59</b>PDF'));assert.equal(nodes['#fusion-package-open-official-search'].hidden,false);
assert(api.rememberOfficialPackageResult({{result:summary}}));api.renderOfficialPackageResult();assert.equal(api.state.package.lastOfficialResult.packageId,'official-main');assert.equal(nodes['#fusion-package-result-outcome'].textContent,'已切换并启用','session result must take priority over persistent summary');assert.equal(storageWrites,0,'package summary must not use localStorage');assert(!JSON.stringify(api.state.package.lastOfficialResult).includes('/private/'));
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fixed_download_routes_and_workspace_gate(self) -> None:
        for element_id in (
            "fusion-paper-export-csv", "fusion-paper-export-xlsx",
            "fusion-search-item-csv", "fusion-search-item-xlsx",
            "fusion-search-finding-csv", "fusion-search-finding-xlsx",
            "fusion-detail-download-image",
        ):
            self.assertEqual(self.index.count(f'id="{element_id}"'), 1)
        for marker in (
            "/api/current-paper/export.${format}?paper_id=${paperId}",
            'item:"/api/six-export"', 'finding:"/api/qualitative-export"',
            "encodeURIComponent(String(query", "sourceScope===\"workspace\"",
            "计划已生成，但尚未创建文件", "选择保存位置并导出",
        ):
            self.assertIn(marker, self.index + self.runtime)
        program = f"""
globalThis.document={{readyState:'loading',querySelector:()=>null,querySelectorAll:()=>[],addEventListener:()=>{{}}}};
globalThis.localStorage={{getItem:()=>null,setItem:()=>{{}}}};
eval(require('fs').readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));
const api=globalThis.AutoResearchFusion,assert=require('assert');
assert.equal(api.currentPaperExportURL('csv',7),'/api/current-paper/export.csv?paper_id=7');
assert.equal(api.currentPaperExportURL('xlsx',7),'/api/current-paper/export.xlsx?paper_id=7');
assert.equal(api.currentPaperExportURL('csv',0),'');
assert.equal(api.currentPaperExportURL('csv','7'),'');
assert.equal(api.currentPaperExportURL('pdf',7),'');
assert.equal(api.workspaceSearchExportURL('item','csv','W Ta/He'),'/api/six-export.csv?q=W%20Ta%2FHe');
assert.equal(api.workspaceSearchExportURL('finding','xlsx','辐照 缺陷'),'/api/qualitative-export.xlsx?q=%E8%BE%90%E7%85%A7%20%E7%BC%BA%E9%99%B7');
assert.equal(api.workspaceSearchExportURL('official','csv','x'),'');
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_single_evidence_metadata_export_uses_public_identity_only(self) -> None:
        for element_id in ("fusion-detail-export-csv", "fusion-detail-export-xlsx"):
            self.assertEqual(self.index.count(f'id="{element_id}"'), 1)
        self.assertIn("证据元数据 CSV", self.index)
        self.assertIn("证据元数据 XLSX", self.index)
        self.assertNotIn("pdf_path", self.runtime)
        program = f"""
globalThis.document={{readyState:'loading',querySelector:()=>null,querySelectorAll:()=>[],addEventListener:()=>{{}}}};
globalThis.localStorage={{getItem:()=>null,setItem:()=>{{}}}};
eval(require('fs').readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));
const api=globalThis.AutoResearchFusion,assert=require('assert');
const expected='/api/desktop/evidence-export?source_scope=workspace&source_id=workspace&entity_type=item&entity_uid=17&format=csv';
assert.equal(api.evidenceExportURL({{sourceScope:'workspace',type:'item',itemId:17}},'csv'),expected);
assert(api.evidenceExportURL({{sourceScope:'workspace',type:'finding',itemId:19}},'xlsx').endsWith('entity_uid=19&format=xlsx'));
assert(api.evidenceExportURL({{sourceScope:'workspace',type:'table',assetId:23}},'csv').includes('entity_type=table&entity_uid=23'));
assert(api.evidenceExportURL({{sourceScope:'workspace',type:'figure',assetId:29}},'xlsx').includes('entity_type=figure&entity_uid=29'));
const official=api.evidenceExportURL({{sourceScope:'official',type:'table',sourceId:'official / 甲',entityUid:'table:W Ta?1'}},'csv');
assert(official.includes('source_id=official%20%2F%20%E7%94%B2'));
assert(official.includes('entity_uid=table%3AW%20Ta%3F1'));
const privateURL=api.evidenceExportURL({{sourceScope:'private',type:'table',sourceId:'private-main',entityUid:'sheet:1'}},'xlsx');
assert(privateURL.includes('source_scope=private'));
assert.equal(api.evidenceExportURL({{sourceScope:'workspace',type:'item',itemId:'17'}},'csv'),'');
assert.equal(api.evidenceExportURL({{sourceScope:'official',type:'table',sourceId:'',entityUid:'x'}},'csv'),'');
assert.equal(api.evidenceExportURL({{sourceScope:'private',type:'other',sourceId:'p',entityUid:'x'}},'csv'),'');
assert.equal(api.evidenceExportURL({{sourceScope:'private',type:'table',sourceId:'p',entityUid:'x',pdf_path:'/secret'}},'pdf'),'');
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_selected_evidence_ai_requires_stable_literature_identity(self) -> None:
        for element_id in (
            "fusion-evidence-ai-question", "fusion-evidence-ai",
            "fusion-evidence-ai-reason", "fusion-evidence-ai-output",
        ):
            self.assertEqual(self.index.count(f'id="{element_id}"'), 0)
        for marker in (
            'data-evidence-chat', 'data-evidence-ai-question',
            'preparedAuthorization("selected_evidence_chat"',
            'executePrepared("selected_evidence_chat"',
            'source_scope:identity.sourceScope', 'source_id:identity.sourceId',
            'entity_type:identity.entityType', 'entity_uid:identity.entityUid',
            '!["official","workspace"].includes(row.sourceScope)',
            '该证据来源暂不支持 Harness 解释',
        ):
            self.assertIn(marker, self.runtime)
        self.assertNotIn('entity_id:identity.entityId', self.runtime)
        program = f"""
globalThis.document={{readyState:'loading',querySelector:()=>null,querySelectorAll:()=>[],addEventListener:()=>{{}}}};
globalThis.localStorage={{getItem:()=>null,setItem:()=>{{}}}};
eval(require('fs').readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));
const api=globalThis.AutoResearchFusion,assert=require('assert');
api.state.view='search';
api.state.selectedEvidence={{type:'item',sourceScope:'official',sourceId:'official-main',entityUid:'item:17'}};
assert.equal(api.selectedEvidenceAIIdentity(),null,'list selection alone must not expose evidence AI');
assert.deepEqual(api.selectedEvidenceAIIdentity(api.state.selectedEvidence),{{key:'official:official-main:item:item:17',sourceScope:'official',sourceId:'official-main',entityType:'item',entityUid:'item:17'}});
api.state.selectedEvidence={{type:'figure',sourceScope:'official',sourceId:'official-main',entityUid:'figure:23'}};
assert.equal(api.selectedEvidenceAIIdentity(api.state.selectedEvidence).entityUid,'figure:23');
api.state.selectedEvidence={{type:'item',sourceScope:'workspace',itemId:17}};
assert.deepEqual(api.selectedEvidenceAIIdentity(api.state.selectedEvidence),{{key:'workspace:workspace:item:17',sourceScope:'workspace',sourceId:'workspace',entityType:'item',entityUid:'17'}});
api.state.selectedEvidence={{type:'item',sourceScope:'private',itemId:17}};
assert.equal(api.selectedEvidenceAIIdentity(api.state.selectedEvidence),null);
api.state.selectedEvidence={{type:'item',sourceScope:'official',sourceId:'official-main',entityUid:''}};
assert.equal(api.selectedEvidenceAIIdentity(api.state.selectedEvidence),null);
api.state.selectedEvidence={{type:'figure',sourceScope:'official',sourceId:'',entityUid:'figure:23'}};
assert.equal(api.selectedEvidenceAIIdentity(api.state.selectedEvidence),null);
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        authorization_program = f"""
globalThis.document={{readyState:'loading',querySelector:()=>null,querySelectorAll:()=>[],addEventListener:()=>{{}}}};
globalThis.localStorage={{getItem:()=>null,setItem:()=>{{}}}};
let confirmed='',confirmCalls=0,consentCalls=0,preparedCalls=2,rememberCalls=0;
globalThis.confirm=message=>{{confirmed=message;confirmCalls+=1;return true;}};
globalThis.AutoResearchAIConsent={{disclosureVersions:{{selected_evidence_chat:'selected-v1'}},accepted:()=>false,remember:()=>{{rememberCalls+=1;return true;}},disclosureSummary:()=>"解读当前选中证据\\n\\n本次发送一条官方证据。",updateTrustedProviders:()=>true}};
globalThis.fetch=async(url,options={{}})=>{{const headers={{get:()=>null}};
 if(url==='/api/desktop/ai/providers')return{{ok:true,headers,json:async()=>({{schema_version:'ai-desktop-catalog-v1',providers:[{{provider_id:'deepseek',display_name:'DeepSeek',model_options:{{}}}}],capability_test:{{provider_id:'deepseek',connection_maximum_model_calls:1,business_maximum_model_calls:{{selected_evidence_chat:2,librarian:8,literature_extraction:8,personal_suggestion:2}}}}}})}};
 if(url==='/api/desktop/ai/settings')return{{ok:true,headers,json:async()=>({{schema_version:'ai-runtime-public-state-v1',provider_id:'deepseek',revision:1,task_models:{{}},readiness:{{schema_version:'ai-readiness-v1',provider_connection:{{state:'ready',reason_code:'ai_connection_ready',next_action:'none'}},harness:{{state:'ready',reason_code:'harness_runtime_ready',next_action:'none'}},businesses:{{literature_extraction:{{state:'ready',reason_code:'ai_business_ready',next_action:'none'}},librarian:{{state:'ready',reason_code:'ai_business_ready',next_action:'none'}},selected_evidence_chat:{{state:'ready',reason_code:'ai_business_ready',next_action:'none'}},personal_suggestion:{{state:'ready',reason_code:'ai_business_ready',next_action:'none'}}}}}}}})}};
 if(url==='/api/desktop/ai/custom-provider')return{{ok:true,headers,json:async()=>({{schema_version:'custom-ai-provider-v1',provider_id:'custom',revision:0,configured:false}})}};
 if(url==='/api/desktop/ai/credentials/deepseek')return{{ok:true,headers,json:async()=>({{schema_version:'ai-credential-status-v1',provider_id:'deepseek',configured:true}})}};
 if(url==='/api/desktop/ai/actions/selected_evidence_chat/prepare')return{{ok:true,headers,json:async()=>({{schema_version:'server-prepared-ai-action-v1',scope:'selected_evidence_chat',provider_id:'deepseek',disclosure_version:'selected-v1',action_id:'action-selected',maximum_calls:preparedCalls,model:'deepseek-v4-pro',display:'官方证据解释'}})}};
 if(url==='/api/desktop/ai/consents'){{consentCalls+=1;return{{ok:true,headers,json:async()=>({{schema_version:'ai-consent-v1',scope:'selected_evidence_chat',nonce:'nonce-selected'}})}};}}
 throw new Error('unexpected:'+url);
}};
eval(require('fs').readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));
const api=globalThis.AutoResearchFusion,assert=require('assert');
(async()=>{{const body={{source_scope:'official',source_id:'official-main',entity_type:'item',entity_uid:'item:17',question:'解释',history:[]}};const authorization=await api.preparedAuthorization('selected_evidence_chat',body);assert.equal(authorization.actionId,'action-selected');assert(confirmed.includes('本次发送一条官方证据'));assert(confirmed.includes('本阶段最多调用 2 次'));assert.equal(confirmCalls,1,'first disclosure and model cost must share one confirmation');assert.equal(rememberCalls,1);assert.equal(consentCalls,1);preparedCalls=3;await api.preparedAuthorization('selected_evidence_chat',body).then(()=>assert.fail('cap must reject'),error=>assert.equal(error.code,'ai_prepared_action_invalid'));assert.equal(consentCalls,1);assert.equal(confirmCalls,1);}})().catch(error=>{{console.error(error);process.exitCode=1;}});
"""
        result = subprocess.run(["node", "-e", authorization_program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_personal_starts_empty_and_user_copy_is_release_neutral(self) -> None:
        for marker in (
            'id="fusion-personal-empty"', "尚未选择实验文件",
            "选择一份真实实验表格开始", 'id="fusion-select-data-file-empty"',
        ):
            self.assertIn(marker, self.index)
        self.assertNotIn("syntheticSheets", self.runtime)
        self.assertNotIn("W-Ta_nanoindentation_demo", self.index + self.runtime)
        self.assertNotIn('renderSheet("hardness")', self.runtime)
        for forbidden in (
            "体验版", "合成示例", "后续版本", "功能恢复",
            "测试连接", "能力测试", "受控测试连接", "0.9.2-preview",
        ):
            self.assertNotIn(forbidden, self.index + self.runtime)
        self.assertIn('uiMode:"/api/ui-mode"', self.runtime)
        self.assertIn("loadReleaseInfo", self.runtime)
        self.assertEqual(self.runtime.count("async function request("), 1)

    def test_private_table_rescan_and_full_librarian_runtime(self) -> None:
        program = f"""
(async()=>{{
const fs=require('fs'),assert=require('assert');
class Classes{{toggle(){{}}add(){{}}remove(){{}}contains(){{return false}}}}
class El{{constructor(){{this.hidden=false;this.disabled=false;this.dataset={{}};this.textContent='';this.innerHTML='';this.value='';this.attrs={{}};this.classList=new Classes();this.listeners={{}};this.isConnected=true;}}setAttribute(k,v){{this.attrs[k]=String(v)}}removeAttribute(k){{delete this.attrs[k]}}addEventListener(k,f){{this.listeners[k]=f}}focus(){{globalThis.focused=this}}querySelector(){{return null}}querySelectorAll(){{return []}}}}
const ids={{}};for(const id of ['fusion-evidence-detail-body','fusion-detail-heading','fusion-detail-identity','fusion-private-table-prev','fusion-private-table-next','fusion-inspector-title','fusion-inspector-body','fusion-evidence-ai','fusion-evidence-ai-reason','fusion-evidence-ai-question','fusion-evidence-ai-output','fusion-literature-action-status','fusion-status-operation','fusion-librarian-output','fusion-librarian-stage','fusion-librarian-status','fusion-librarian-question'])ids['#'+id]=new El();
globalThis.document={{readyState:'loading',querySelector:s=>ids[s]||null,querySelectorAll:()=>[],addEventListener:()=>{{}}}};globalThis.localStorage={{getItem:()=>null,setItem:()=>{{}}}};
const pending=new Map(),calls=[];globalThis.fetch=(url,options={{}})=>{{url=String(url);calls.push([url,options.method||'GET']);return new Promise(resolve=>{{const page=Number(new URL(url,'http://local').searchParams.get('page'));pending.set(page,payload=>resolve({{ok:true,headers:{{get:()=>null}},json:async()=>payload}}));}})}};
eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));const api=globalThis.AutoResearchFusion,row={{type:'table',title:'纳米压痕原始表',sourceScope:'private',sourceId:'private-public',entityUid:'private:table:opaque'}};
const dto=page=>({{schema_version:'personal-table-page-v1',source_id:'private-public',entity_uid:'private:table:opaque',title:'真实实验表',sheet_name:'数据',columns:[{{name:'剂量',role:'independent',data_type:'number',meaning:'辐照剂量',unit:'dpa'}},{{name:'硬度',role:'dependent',data_type:'number',meaning:'纳米硬度',unit:'GPa'}}],conditions:{{温度:'300 K'}},series:[{{name:'硬度曲线',x_column:'剂量',y_column:'硬度',uncertainty_column:null,description:'真实测量序列'}}],page,page_size:50,total:75,has_next:page===1,rows:[{{剂量:String((page-1)*50),硬度:'4.1'}}]}});
api.state.view='search';api.state.evidenceDetailOpen=true;api.state.evidenceDetail=row;
assert.deepEqual(api.privateTableIdentity(row),{{sourceId:'private-public',entityUid:'private:table:opaque',key:'private:private-public:private:table:opaque'}});assert.equal(api.privateTableIdentity({{...row,sourceScope:'official'}}),null);assert.equal(api.privateTableIdentity({{...row,type:'finding'}}),null);
const first=api.loadPrivateTablePage(row,1),second=api.loadPrivateTablePage(row,2);pending.get(2)(dto(2));await second;pending.get(1)(dto(1));await first;assert.equal(api.state.personalTablePage.page,2,'late page must not replace current page');assert(ids['#fusion-evidence-detail-body'].innerHTML.includes('硬度'));assert(ids['#fusion-evidence-detail-body'].innerHTML.includes('第 2 页'));assert(ids['#fusion-inspector-body'].innerHTML.includes('辐照剂量'));assert(ids['#fusion-inspector-body'].innerHTML.includes('300 K'));assert(ids['#fusion-inspector-body'].innerHTML.includes('硬度曲线'));assert(calls.some(([url])=>url.includes('source_id=private-public')&&url.includes('entity_uid=private%3Atable%3Aopaque')&&url.includes('page_size=50')));
const before=calls.length;api.state.paper={{id:9,title:'已扫描论文',requiresRescanConfirmation:true}};globalThis.confirm=()=>false;await api.runLiteratureExtraction();assert.equal(calls.length,before,'rescan cancellation must make zero prepare requests');assert(ids['#fusion-literature-action-status'].textContent.includes('没有准备或执行模型调用'));
api.renderLibrarianFinal({{librarian_core_version:'librarian-v3',answer:'完整回答',research_state:null,state_token:'',query_analysis:{{source_scopes:['official','workspace']}},intent:{{intent:'research_review'}},retrieval_policy:'review_map',candidate_count:7,cited_count:1,bundle_count:2,match_counts:{{direct:1,adjacent:2,expansion:4}},review_map:[{{theme:'主题一'}},{{theme:'主题二'}}],results:[{{entity_type:'item',source_scope:'official',source_id:'official-v1',entity_uid:'official:item:1',meaning:'硬度变化',value_text:'4.1',unit:'GPa',article_title:'论文A',source_page:3,source_excerpt:'原文证据'}}],report:{{schema_version:'research-report-v1',direct_conclusion:{{status:'found',text:'直接结论正文',refs:['R1']}},evidence_matrix:[{{property:'硬度',result:'4.1 GPa',material:'W',conditions:'300 K',article_title:'论文A',source_page:3,refs:['R1']}}],related_evidence:[{{summary:'相关证据正文',relaxed_constraints:['温度'],refs:['R2']}}],database_gaps:['缺少剂量范围'],suggested_followups:['继续解释R1']}},recommended_articles:[{{article_title:'推荐论文',why_recommended:'满足硬条件',first_author:'A',year:2025,doi:'10.1/a',recommendation_level:'direct',supporting_refs:['R1'],coverage_warning:'缺少图片',jump_evidence:{{entity_type:'item',source_scope:'official',source_id:'official-v1',entity_uid:'official:item:1',meaning:'硬度变化',article_title:'论文A',source_page:3,source_excerpt:'原文证据'}}}}],suggested_actions:[{{schema_version:'suggested-action-v1',answerable:true,text:'详细解释R1'}}]}},'问题');const html=ids['#fusion-librarian-output'].innerHTML;for(const text of ['完整回答','直接结论正文','证据矩阵','相关证据正文','缺少剂量范围','继续解释R1','原文证据','推荐论文','缺少图片','详细解释R1','官方资料库 + 本机文献工作区','主题综述','直接 1 · 相关 2 · 扩展 4','候选 7 · 引用 1 · 证据组 2 · 主题 2'])assert(html.includes(text),text);assert(!html.includes('官方全库'));assert(html.includes('data-librarian-evidence-link="0"'));assert(html.includes('data-librarian-article-link="0"'));
}})().catch(error=>{{console.error(error);process.exitCode=1}});
"""
        result = subprocess.run(
            ["node", "-e", program],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_librarian_result_rail_opens_secondary_evidence_and_keeps_chat_threads(self) -> None:
        for marker in (
            'data-librarian-result="${index}"',
            'button.addEventListener("click",()=>void openLibrarianEvidence(',
            'prepareLibrarianSpace("results")',
            'prepareLibrarianSpace("detail")',
            'state.evidenceChat.threads.set(identity',
            'state.evidenceChat.threads.get(identity.key)',
        ):
            self.assertIn(marker, self.runtime)
        program = f"""
const fs=require('fs'),assert=require('assert');
class Classes{{constructor(){{this.s=new Set()}}toggle(k,v){{v?this.s.add(k):this.s.delete(k)}}add(k){{this.s.add(k)}}remove(k){{this.s.delete(k)}}contains(k){{return this.s.has(k)}}}}
class El{{constructor(){{this.hidden=false;this.disabled=false;this.textContent='';this.innerHTML='';this.value='';this.dataset={{}};this.attrs={{}};this.classList=new Classes();this.listeners={{}};this.isConnected=true;}}setAttribute(k,v){{this.attrs[k]=String(v)}}removeAttribute(k){{delete this.attrs[k]}}addEventListener(k,fn){{(this.listeners[k]??=[]).push(fn)}}focus(){{globalThis.focused=this}}querySelector(){{return null}}querySelectorAll(){{return []}}closest(){{return null}}}}
const ids={{}};for(const id of ['fusion-librarian-panel','fusion-librarian-results','fusion-librarian-results-toggle','fusion-librarian-results-body','fusion-librarian-results-count','fusion-inspector-title','fusion-inspector-body','fusion-evidence-ai','fusion-evidence-ai-reason','fusion-evidence-ai-question','fusion-evidence-ai-output','fusion-editor-group-content','fusion-primary-editor-surface','fusion-secondary-editor-surface','fusion-secondary-editor-title','fusion-secondary-editor-body','fusion-editor-group-separator','fusion-document-tab-groups','fusion-document-tabs-primary','fusion-document-tabs-secondary','fusion-context','fusion-inspector','fusion-context-separator','fusion-inspector-separator'])ids['#'+id]=new El();
const evidenceQuestion=new El(),chatHost=new El();chatHost.dataset.evidenceTabId='evidence:sourceScope=official&sourceId=official-main&entityType=item&entityUid=item%3Aa';chatHost.querySelector=selector=>selector==='[data-evidence-ai-question]'?evidenceQuestion:null;ids['#fusion-secondary-editor-surface'].querySelectorAll=selector=>selector==='[data-evidence-chat]'?[chatHost]:[];
const groupPrimary=new El(),groupSecondary=new El(),html={{dataset:{{}},style:{{setProperty(){{}}}}}},body={{dataset:{{view:'search'}}}};
const singles={{'[data-document-group="primary"]':groupPrimary,'[data-document-group="secondary"]':groupSecondary}};
const all={{'[data-librarian-result]':[],'[data-librarian-article]':[],'[data-pane-toggle="context"]':[],'[data-pane-toggle="inspector"]':[],'[data-pane-toggle="primary"]':[],'[data-pane-toggle="secondary"]':[],'[data-secondary-return-tab]':[],'[data-secondary-pdf-highlight-toggle]':[],'[data-secondary-open-pdf]':[],'[data-secondary-open-paper-pdf]':[],'[data-secondary-paper-evidence]':[],'[data-secondary-table-page]':[],'[data-document-tab-id]':[],'[data-close-document-tab]':[]}};
globalThis.document={{readyState:'loading',documentElement:html,body,querySelector:s=>ids[s]||singles[s]||null,querySelectorAll:s=>all[s]||[],addEventListener(){{}}}};globalThis.localStorage={{getItem:()=>null,setItem(){{}}}};globalThis.innerWidth=1280;
eval(fs.readFileSync({str(WEB / 'document_tab_store.js')!r},'utf8'));eval(fs.readFileSync({str(WEB / 'pane_layout_controller.js')!r},'utf8'));eval(fs.readFileSync({str(WEB / 'workspace_layout_controller.js')!r},'utf8'));eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));const api=globalThis.AutoResearchFusion;
const row=(uid,title)=>({{type:'item',title,value:'4.1',unit:'GPa',meaning:title,sourceScope:'official',sourceId:'official-main',entityUid:uid,page:7,articleTitle:'论文 '+title,excerpt:'原文 '+title,quantities:[],variables:{{}},materials:[],conditions:'',methods:'',linkedItemCount:0,tags:[]}}),A=row('item:a','证据 A'),B=row('item:b','证据 B');
api.state.view='search';api.state.searchMode='librarian';api.state.librarianResults=[A,B];api.state.librarianArticles=[];api.state.librarianResultsOpen=false;api.documentTabs.open({{tabId:'evidence:background',kind:'evidence',ownerView:'search',title:'已有详情',identity:{{sourceScope:'official',sourceId:'official-main',entityType:'item',entityUid:'item:background'}},payload:{{row:A,status:'ready'}}}},{{groupId:'secondary',pin:true}});api.paneController.expandEditor('secondary',{{persist:false}});api.paneController.expandSide('inspector',{{persist:false}});let geometry=api.coordinateTaskLayout('librarian-chat');assert.equal(geometry.inspectorDocked,false,'chat without selected evidence releases inspector');assert.equal(api.paneLayout.inspector.collapsed,false,'task suppression preserves inspector preference');api.renderLibrarianResultNavigator();assert.equal(api.setLibrarianResultsOpen(true),true);assert.equal(ids['#fusion-librarian-results'].hidden,false);assert(ids['#fusion-librarian-panel'].classList.contains('results-open'));geometry=api.applyPaneLayout();assert.equal(geometry.inspectorDocked,false,'result rail releases inspector space');assert.equal(geometry.contextDocked,false,'result rail releases unrelated context');assert.equal(geometry.secondaryCollapsed,true,'result rail releases unrelated secondary editor');assert.equal(api.paneLayout.editors.secondaryCollapsed,false,'result rail does not close the stored secondary group');
(async()=>{{assert(await api.openLibrarianEvidence(0));let secondary=api.documentTabs.activeTab('secondary');assert(secondary);geometry=api.applyPaneLayout();assert.equal(geometry.secondaryCollapsed,false,'evidence detail restores secondary editor');assert.equal(geometry.contextDocked,false,'1280 gives the librarian evidence detail the available secondary column');assert.equal(geometry.inspectorDocked,false,'librarian never restores a global inspector');assert.equal(api.togglePane('inspector'),false,'librarian rejects the global inspector');geometry=api.applyPaneLayout();assert.equal(geometry.inspectorDocked,false);assert.equal(secondary.payload.row.title,'证据 A');assert.equal(api.state.selectedEvidence.title,'证据 A');assert.equal(api.selectedEvidenceAIIdentity().entityUid,'item:a');assert.equal(globalThis.focused,evidenceQuestion,'opening a cited result restores focus inside its concrete evidence detail');
 api.state.evidenceChat.messages=[{{role:'user',content:'问题 A'}},{{role:'assistant',content:'回答 A'}}];assert(await api.openLibrarianEvidence(1));secondary=api.documentTabs.activeTab('secondary');assert.equal(secondary.payload.row.title,'证据 B');assert.equal(api.state.evidenceChat.messages.length,0);api.state.evidenceChat.messages=[{{role:'user',content:'问题 B'}},{{role:'assistant',content:'回答 B'}}];assert(await api.openLibrarianEvidence(0));assert.deepEqual(api.state.evidenceChat.messages.map(x=>x.content),['问题 A','回答 A']);assert.deepEqual(api.state.evidenceChat.threads.get('official:official-main:item:item:b').map(x=>x.content),['问题 B','回答 B']);for(const width of [900,720]){{globalThis.innerWidth=width;api.syncResponsivePaneLayout();assert.equal(api.setLibrarianResultsOpen(true),true);assert.equal(ids['#fusion-librarian-results'].hidden,false);assert.equal(ids['#fusion-librarian-results-toggle'].attrs['aria-expanded'],'true');assert.equal(api.setLibrarianResultsOpen(false),false);assert.equal(ids['#fusion-librarian-results'].hidden,true);}}
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_selected_evidence_background_reply_returns_to_origin_thread(self) -> None:
        program = f"""
(async()=>{{
const fs=require('fs'),assert=require('assert');
class Classes{{constructor(){{this.s=new Set()}}toggle(k,v){{v?this.s.add(k):this.s.delete(k)}}add(k){{this.s.add(k)}}remove(k){{this.s.delete(k)}}contains(k){{return this.s.has(k)}}}}
class El{{constructor(){{this.hidden=false;this.disabled=false;this.textContent='';this.innerHTML='';this.value='';this.dataset={{}};this.attrs={{}};this.classList=new Classes();this.listeners={{}};this.isConnected=true;}}setAttribute(k,v){{this.attrs[k]=String(v)}}removeAttribute(k){{delete this.attrs[k]}}addEventListener(k,fn){{(this.listeners[k]??=[]).push(fn)}}focus(){{globalThis.focused=this}}querySelector(){{return null}}querySelectorAll(){{return []}}closest(){{return null}}}}
const ids={{}};for(const id of ['fusion-inspector-title','fusion-inspector-body','fusion-evidence-ai','fusion-evidence-ai-reason','fusion-evidence-ai-question','fusion-evidence-ai-context','fusion-evidence-ai-output','fusion-ai-provider','fusion-ai-models','fusion-ai-model-save','fusion-ai-key-save','fusion-ai-key-delete','fusion-ai-test','fusion-ai-test-plan','fusion-ai-custom-status','fusion-ai-custom-delete','fusion-ai-custom-name','fusion-ai-custom-endpoint','fusion-ai-credential-state','fusion-ai-settings-status','fusion-status-operation','fusion-status-ai','fusion-ai-connection-state','fusion-ai-connection-reason','fusion-ai-harness-state','fusion-ai-harness-reason','fusion-ai-business-readiness'])ids['#'+id]=new El();
const primarySurface=new El(),secondarySurface=new El(),chatHost=new El(),chat={{'[data-evidence-ai-question]':ids['#fusion-evidence-ai-question'],'[data-evidence-ai-send]':ids['#fusion-evidence-ai'],'[data-evidence-ai-reason]':ids['#fusion-evidence-ai-reason'],'[data-evidence-ai-context]':ids['#fusion-evidence-ai-context'],'[data-evidence-ai-output]':ids['#fusion-evidence-ai-output'],'[data-evidence-ai-inline-progress]':new El()}};chatHost.querySelector=selector=>chat[selector]||null;secondarySurface.querySelectorAll=selector=>selector==='[data-evidence-chat]'?[chatHost]:[];ids['#fusion-primary-editor-surface']=primarySurface;ids['#fusion-secondary-editor-surface']=secondarySurface;
globalThis.document={{readyState:'loading',documentElement:{{dataset:{{}},style:{{setProperty(){{}}}}}},body:{{dataset:{{view:'search'}}}},querySelector:s=>ids[s]||null,querySelectorAll:s=>s==='[data-evidence-chat]'?[chatHost]:[],addEventListener(){{}}}};globalThis.localStorage={{getItem:()=>null,setItem(){{}}}};globalThis.innerWidth=1280;globalThis.confirm=()=>true;
globalThis.AutoResearchAIConsent={{disclosureVersions:{{selected_evidence_chat:'selected-evidence-v1'}},accepted:()=>true,remember:()=>true,disclosureSummary:()=>'',updateTrustedProviders:()=>true}};
const ready={{state:'ready',reason_code:'ai_business_ready',next_action:'none',verified_until:null}},readiness={{schema_version:'ai-readiness-v1',provider_connection:ready,harness:ready,businesses:{{librarian:ready,literature_extraction:ready,personal_suggestion:ready,selected_evidence_chat:ready}}}};
const catalog={{schema_version:'ai-desktop-catalog-v1',providers:[{{provider_id:'deepseek',display_name:'DeepSeek',model_options:{{extraction:['extract'],analysis:['analysis'],librarian_planning:['plan'],librarian_synthesis:['synth']}}}}],capability_test:{{provider_id:'deepseek',connection_maximum_model_calls:1,business_maximum_model_calls:{{selected_evidence_chat:2}}}}}},settings={{schema_version:'ai-runtime-public-state-v1',provider_id:'deepseek',revision:1,task_models:{{}},readiness}},custom={{schema_version:'custom-ai-provider-v1',provider_id:'custom',configured:false,revision:0}},credential={{configured:true}};
let finishPaidJob=null,prepareCalls=0;const response=payload=>({{ok:true,headers:{{get:()=>null}},json:async()=>payload}});
globalThis.fetch=(url,options={{}})=>{{url=String(url);if(url==='/api/desktop/ai/providers')return Promise.resolve(response(catalog));if(url==='/api/desktop/ai/settings')return Promise.resolve(response(settings));if(url==='/api/desktop/ai/custom-provider')return Promise.resolve(response(custom));if(url==='/api/desktop/ai/credentials/deepseek')return Promise.resolve(response(credential));if(url==='/api/desktop/ai/actions/selected_evidence_chat/prepare'){{prepareCalls+=1;return Promise.resolve(response({{schema_version:'server-prepared-ai-action-v1',scope:'selected_evidence_chat',provider_id:'deepseek',disclosure_version:'selected-evidence-v1',action_id:'action_A_abcdefghijklmnopqrstuvwx',maximum_calls:1,model:'reasoner',display:'解释证据 A'}}));}}if(url==='/api/desktop/ai/consents')return Promise.resolve(response({{schema_version:'ai-consent-v1',scope:'selected_evidence_chat',nonce:'nonce_A_abcdefghijklmnopqrstuvwx'}}));if(url==='/api/desktop/ai/actions/selected_evidence_chat/execute-jobs')return new Promise(resolve=>{{finishPaidJob=()=>resolve(response({{schema_version:'ai-execution-job-v1',scope:'selected_evidence_chat',job_id:'ai_job_abcdefghijklmnopqrstuvwx',status:'completed',events:[],result:{{answer:'后台回答 A',entity:{{type:'item',id:'item:a'}},evidence_pages:[7],evidence_notes:['第 7 页给出该数值。'],limitations:['只解释当前证据。']}}}}));}});throw new Error('unexpected fetch '+url);}};
eval(fs.readFileSync({str(WEB / 'document_tab_store.js')!r},'utf8'));eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));const api=globalThis.AutoResearchFusion,row=(uid,title)=>({{type:'item',title,value:'4.1',unit:'GPa',meaning:title,sourceScope:'official',sourceId:'official-main',entityUid:uid,page:7,articleTitle:'论文 '+title,excerpt:'原文 '+title,quantities:[],variables:{{}},materials:[],conditions:'',methods:'',linkedItemCount:0,tags:[]}}),A=row('item:a','证据 A'),B=row('item:b','证据 B'),keyA='official:official-main:item:item:a';
api.state.view='search';api.state.searchResults=[A,B];api.selectEvidence(0,{{focus:false,view:'search'}});ids['#fusion-evidence-ai-question'].value='问题 A';const beforeList=prepareCalls;assert.equal(await api.submitSelectedEvidenceAI(),null,'list selection must not expose evidence AI');assert.equal(prepareCalls,beforeList,'list selection must make zero paid requests');
const tabA=api.documentTabs.open({{tabId:'evidence:a',kind:'evidence',ownerView:'search',title:'证据 A',identity:{{sourceScope:'official',sourceId:'official-main',entityType:'item',entityUid:'item:a'}},payload:{{row:A,evidenceIdentity:'official:official-main:item:a',status:'ready'}}}},{{groupId:'secondary',pin:true}});chatHost.dataset.evidenceTabId=tabA.tabId;api.renderEditorSurfaces();ids['#fusion-evidence-ai-question'].value='问题 A';const paid=api.submitSelectedEvidenceAI();for(let attempt=0;attempt<40&&!finishPaidJob;attempt+=1)await new Promise(resolve=>setImmediate(resolve));assert(finishPaidJob,'paid job must start from an active evidence detail within the bounded wait');assert.equal(api.state.evidenceChat.pending.has(keyA),true);assert.equal(api.state.evidenceChat.busy,true);
const keyB='official:official-main:item:item:b',tabB=api.documentTabs.open({{tabId:'evidence:b',kind:'evidence',ownerView:'search',title:'证据 B',identity:{{sourceScope:'official',sourceId:'official-main',entityType:'item',entityUid:'item:b'}},payload:{{row:B,evidenceIdentity:'official:official-main:item:b',status:'ready'}}}},{{groupId:'secondary',pin:true}});chatHost.dataset.evidenceTabId=tabB.tabId;api.renderEditorSurfaces();assert.equal(api.state.evidenceChat.identity,keyB);assert.equal(api.state.evidenceChat.busy,false,'background A must not mark current B busy');assert(ids['#fusion-evidence-ai-reason'].textContent.includes('另一条证据正在回答'));assert.equal(api.state.evidenceChat.messages.length,0,'B must not display A messages');ids['#fusion-evidence-ai-question'].value='问题 B';const beforeSecond=prepareCalls;assert.equal(await api.submitSelectedEvidenceAI(),null,'parallel paid request must be rejected');assert.equal(prepareCalls,beforeSecond,'parallel rejection must happen before prepare');
finishPaidJob();await paid;assert.equal(api.state.evidenceChat.busy,false);assert.equal(api.state.evidenceChat.pending.size,0);assert.equal(api.state.evidenceChat.messages.length,0,'completed A must not appear in current B');const threadA=api.state.evidenceChat.threads.get(keyA);assert.deepEqual(threadA.map(message=>message.content),['问题 A','后台回答 A']);assert.deepEqual(threadA[1].annotations.pages,[7]);assert.deepEqual(threadA[1].annotations.notes,['第 7 页给出该数值。']);assert.deepEqual(threadA[1].annotations.limitations,['只解释当前证据。']);await api.state.evidenceChat.historySave;api.state.evidenceChat.messages=[{{role:'user',content:'仅清除 B'}}];api.state.evidenceChat.threads.set(keyB,[{{role:'user',content:'仅清除 B'}}]);api.state.evidenceChat.historyBackend='desktop-secure';api.state.evidenceChat.historyThreads.set(keyB,{{messages:[]}});globalThis.fetch=async(url,options={{}})=>{{assert.equal(String(url),'/api/desktop/evidence-chat-history');assert.equal(JSON.parse(options.body).operation,'delete');return response({{schema_version:'evidence-chat-history-v1',revision:1,storage:'macos-preview-local-key-aes-256-gcm',threads:[]}});}};assert.equal(await api.clearSelectedEvidenceThread(),true);assert.equal(api.state.evidenceChat.messages.length,0);assert.equal(api.state.evidenceChat.threads.has(keyB),false);assert.equal(api.state.evidenceChat.threads.has(keyA),true,'clearing B must preserve A thread');
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_librarian_and_evidence_boundaries_use_theme_tokens(self) -> None:
        for marker in (
            'query_analysis?.source_scopes',
            'result?.match_counts',
            'result?.retrieval_policy',
            'data-librarian-evidence-link="${index}"',
            'data-librarian-article-link="${index}"',
            'data-evidence-ai-open-detail',
            'data-evidence-ai-open-pdf',
            'data-evidence-ai-clear',
            'clearSelectedEvidenceThread',
        ):
            self.assertIn(marker, self.runtime)
        self.assertNotIn("LIBRARIAN V3 · 官方全库", self.runtime)
        self.assertIn("@container fusion-editor (max-width:720px)", self.css)
        themed = "\n".join(
            line
            for line in self.css.splitlines()
            if any(
                selector in line
                for selector in (
                    ".fusion-librarian-panel",
                    ".fusion-librarian-results",
                    ".fusion-librarian-result",
                    ".fusion-evidence-chat",
                )
            )
        )
        self.assertTrue(themed)
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", themed))
        for token in ("var(--f-accent)", "var(--f-accent-line)", "var(--f-surface)", "var(--f-text)"):
            self.assertIn(token, themed)
        self.assertIn(':root[data-theme="dark"]', self.css)
        self.assertIn(':root[data-theme="system"]', self.css)
        self.assertIn("@media(prefers-color-scheme:dark)", self.css)
        for marker in (
            "--f-content-pad:clamp(20px,3vw,36px)",
            "--f-content-gap:18px", "--f-body-leading:1.72",
            "width:min(100%,840px)", 'data-density="comfortable"',
            "--f-content-pad:12px", "--f-body-leading:1.5",
        ):
            self.assertIn(marker, self.css)

    def test_ai_activity_and_chat_use_aggregated_task_projection(self) -> None:
        for element_id in ("fusion-librarian-output", "fusion-librarian-form"):
            self.assertEqual(self.index.count(f'id="{element_id}"'), 1)
        self.assertNotIn('id="fusion-evidence-ai-output"', self.index)
        self.assertNotIn("fusion-evidence-chat", self.index.split('<aside class="fusion-inspector"', 1)[-1])
        for marker in (
            'AI_APPLICATION_PHASES = Object.freeze({understand:"理解问题",retrieve:"检索证据",verify:"核验引用",organize:"组织回答",complete:"完成"})',
            "function projectAIActivity(", "node.dataset.aiActivityPhase=phase",
            'projectAIActivity(scope,{...event,job_id:job.job_id})',
            "function visibleEvidenceDetail()", "function activeEvidenceChatHost()",
            'qa("[data-evidence-ai-form]")',
            'hasInspectorSelection=Boolean(state.selectedEvidence)', 'workspaceLayout?.project',
            ".fusion-evidence-ai-output",
            ".fusion-evidence-chat .fusion-chat-composer", ".fusion-librarian-form",
        ):
            self.assertIn(marker, self.runtime + self.css)
        self.assertNotIn("aiExperience?.activity(scope,{...event,job_id:job.job_id})", self.runtime)
        self.assertIn('state==="error"?"请查看对话中的原因与处理建议":detail', self.runtime)

    def test_private_table_and_rescan_contracts_are_bounded(self) -> None:
        for marker in (
            'personalTable:"/api/desktop/personal-experiments/table"',
            "personal-table-page-v1", "page_size=50", "privateTableIdentity",
            "source_id=${encodeURIComponent(identity.sourceId)}",
            "entity_uid=${encodeURIComponent(identity.entityUid)}",
            'requiresRescanConfirmation:raw?.requires_rescan_confirmation===true',
            "会重新调用模型并可能产生 API 费用", "force_rescan:forceRescan",
            "renderRecommendedArticles", "renderSuggestedActions", "renderLibrarianReport",
        ):
            self.assertIn(marker, self.runtime)
        self.assertNotIn("raw?.run_id", self.runtime)
        self.assertNotIn("raw?.file_id", self.runtime)
        for forbidden in ("体验版", "合成示例", "测试连接", "能力测试", "后续版本", "功能恢复"):
            self.assertNotIn(forbidden, self.index + self.runtime)

    def test_workspace_table_structure_review_is_scoped_and_path_free(self) -> None:
        for marker in (
            'tableStructures:"/api/desktop/table-structures"',
            'tableStructureReviews:"/api/desktop/table-structures/reviews"',
            "include_unverified=1",
            'schema_version!=="table-structure-version-v1"',
            'data-table-structure-action="approve"',
            'data-table-structure-action="correct"',
            'data-table-structure-action="reject"',
            "在主栏审核",
            "结构 CSV",
            "结构 XLSX",
            "tableStructureRequests.get(tabId)!==generation",
            'body={entity_uid:String(row.assetId),expected_version:structure.version,operation,note:""}',
        ):
            self.assertIn(marker, self.runtime)
        for marker in (
            ".fusion-table-structure",
            ".fusion-table-structure-scroll",
            ".fusion-table-structure-grid input",
            "var(--f-accent)",
        ):
            self.assertIn(marker, self.css)
        review_body = self.runtime.split(
            'const body={entity_uid:String(row.assetId),expected_version:structure.version,operation,note:""}',
            1,
        )[1].split("const raw=await request", 1)[0]
        for forbidden in ("status", "cells", "fingerprint", "reviewer", "path"):
            self.assertNotIn(forbidden, review_body)

    def test_workspace_table_structure_runtime_keeps_tabs_and_late_results_isolated(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert');
globalThis.document={{readyState:'loading',documentElement:{{style:{{setProperty:()=>{{}}}},dataset:{{}}}},body:{{dataset:{{}}}},querySelector:()=>null,querySelectorAll:()=>[],addEventListener:()=>{{}}}};
globalThis.localStorage={{getItem:()=>null,setItem:()=>{{}}}};globalThis.innerWidth=1400;globalThis.addEventListener=()=>{{}};
eval(fs.readFileSync({str(WEB / 'document_tab_store.js')!r},'utf8'));
let source=fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8');
source=source.replace('globalThis.AutoResearchFusion=Object.freeze({{initialize','globalThis.AutoResearchFusion=Object.freeze({{publicTableStructure,tableStructureHTML,tableStructureExportURL,loadTableStructure,initialize');
const pending=[];globalThis.fetch=(url,options={{}})=>new Promise(resolve=>pending.push({{url:String(url),options,resolve}}));
eval(source);const api=globalThis.AutoResearchFusion,store=api.documentTabs;
const table=(id,title)=>({{type:'table',title,sourceScope:'workspace',sourceId:'',entityUid:'',assetId:id,itemId:null,paperId:56,imageUrl:`/api/visual-assets/${{id}}/image`,caption:'真实截图',materials:[],quantities:[],variables:{{}},tags:[],linkedItemCount:0}});
const raw=(id,version,status,cell)=>({{schema_version:'table-structure-version-v1',source_scope:'workspace',source_id:'workspace',entity_uid:String(id),entity_type:'table',version,status,reason_codes:status==='manual_review'?['ambiguous_header']:[],rows:[['列'],[cell]],cells:[{{secret_path:'/private/a'}}],content_fingerprint:'a'.repeat(64),reviewed_at:status==='verified'?'2026-08-27T12:00:00Z':null}});
const a=table(1358,'Table A'),b=table(1359,'Table B');
store.open({{tabId:'evidence:a',kind:'evidence',ownerView:'paper',title:'A',identity:{{sourceScope:'workspace',entityType:'table',entityUid:'1358'}},payload:{{row:a,status:'ready'}}}},{{groupId:'primary',pin:true}});
store.open({{tabId:'evidence:b',kind:'evidence',ownerView:'paper',title:'B',identity:{{sourceScope:'workspace',entityType:'table',entityUid:'1359'}},payload:{{row:b,status:'ready'}}}},{{groupId:'secondary',pin:true}});
(async()=>{{
 const a1=api.loadTableStructure('evidence:a',a),b1=api.loadTableStructure('evidence:b',b);
 assert.equal(pending[0].url,'/api/desktop/table-structures?entity_uid=1358&include_unverified=1');assert.equal(pending[1].url,'/api/desktop/table-structures?entity_uid=1359&include_unverified=1');
 const response=value=>({{ok:true,headers:{{get:()=>null}},json:async()=>value}});pending[1].resolve(response(raw(1359,1,'manual_review','B')));await b1;pending[0].resolve(response(raw(1358,1,'verified','A')));await a1;
 let tabs=store.snapshot().tabs;assert.equal(tabs.find(tab=>tab.tabId==='evidence:a').payload.tableStructure.rows[1][0],'A');assert.equal(tabs.find(tab=>tab.tabId==='evidence:b').payload.tableStructure.rows[1][0],'B');
 const old=api.loadTableStructure('evidence:a',a),newer=api.loadTableStructure('evidence:a',a),oldCall=pending[2],newCall=pending[3];newCall.resolve(response(raw(1358,3,'verified','new')));await newer;oldCall.resolve(response(raw(1358,2,'manual_review','old')));await old;
 tabs=store.snapshot().tabs;assert.equal(tabs.find(tab=>tab.tabId==='evidence:a').payload.tableStructure.version,3,'late response must not overwrite tab');assert.equal(tabs.find(tab=>tab.tabId==='evidence:b').payload.tableStructure.rows[1][0],'B');
 const projected=api.publicTableStructure(raw(1358,4,'verified','=1+1'),'1358');assert(projected);assert.equal(projected.cells,undefined);assert.equal(projected.content_fingerprint,undefined);const html=api.tableStructureHTML('evidence:a',a,{{tableStructure:projected,tableStructureState:'ready'}},true);assert(html.includes('结构 CSV'));assert(html.includes('=1+1'));assert(!html.includes('/private/'));assert.equal(api.tableStructureHTML('x',{{...a,sourceScope:'official'}},{{}},true),'');
}})().catch(error=>{{console.error(error);process.exitCode=1}});
"""
        result = subprocess.run(
            ["node", "-e", program],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_review_queue_uses_one_central_tab_and_no_token_dom(self) -> None:
        for element_id in ("fusion-open-review-queue", "fusion-review-queue-count"):
            self.assertEqual(self.index.count(f'id="{element_id}"'), 1)
        for marker in (
            'reviewQueue:"/api/desktop/review-queue"',
            'reviewActions:"/api/desktop/review-queue/actions"',
            'schema_version!=="review-queue-v1"',
            'schema_version!=="review-result-v1"',
            'data-review-action="approve"', 'data-review-action="reject"',
            'data-review-action="correct"', 'data-review-retry',
            'saved_index_pending', 'retry_same_request',
            'review_token:item.reviewToken', 'body:JSON.stringify(body)',
            'closeReviewDetail', 'review-candidate',
        ):
            self.assertIn(marker, self.runtime + self.index)
        self.assertNotIn('data-review-token', self.runtime + self.index)
        self.assertNotIn('localStorage.setItem("review', self.runtime)
        self.assertNotIn('localStorage.setItem("candidate', self.runtime)

    def test_saved_index_pending_has_a_real_idempotent_recovery_action(self) -> None:
        self.assertEqual(self.index.count('id="fusion-repair-search-index"'), 1)
        for marker in (
            'searchIndexRecovery:"/api/desktop/search-index/refresh"',
            "async function recoverSearchIndex()",
            'body:"{}"',
            'schema_version!=="search-index-recovery-v1"',
            "不会重新提取、发布或调用模型",
            "showSearchIndexRecovery(true)",
            'q("#fusion-repair-search-index")?.addEventListener',
        ):
            self.assertIn(marker, self.runtime + self.index)

    def test_completed_extraction_exposes_a_truthful_path_free_receipt(self) -> None:
        self.assertEqual(self.index.count('id="fusion-literature-receipt"'), 1)
        for marker in (
            "function renderLiteratureReceipt(result)",
            "双分支核验",
            "正式发布",
            "视觉证据",
            "人工审核",
            "搜索索引",
            "尚未生成 JSONL / Parquet 文件",
            "renderLiteratureReceipt(result)",
            "updateLiteratureReceiptIndex",
        ):
            self.assertIn(marker, self.runtime + self.index)
        receipt_renderer = self.runtime.split("function renderLiteratureReceipt(result)", 1)[1].split("function updateLiteratureReceiptIndex", 1)[0]
        for forbidden in ("source_fingerprint", "content_fingerprint", "entity_uids", "visual_asset_hashes"):
            self.assertNotIn(forbidden, receipt_renderer)

    def test_review_queue_public_projection_and_candidate_runtime(self) -> None:
        program = f"""
globalThis.document={{readyState:'loading',querySelector:()=>null,querySelectorAll:()=>[],addEventListener:()=>{{}}}};
globalThis.localStorage={{getItem:()=>null,setItem:()=>{{}}}};
eval(require('fs').readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));
const api=globalThis.AutoResearchFusion,assert=require('assert');
const token='rq_'+('A'.repeat(40)),paperUid='paper_0123456789abcdef0123456789abcdef';
const dto={{schema_version:'review-queue-v1',source_scope:'workspace',source_id:'workspace',paper_uid:null,total:1,items:[{{review_token:token,expires_at:1999999999,source_scope:'workspace',source_id:'workspace',paper_uid:paperUid,paper:{{title:'W-Ta 论文',doi:'10.1/example'}},entity_type:'item',candidate:{{value_text:'4.1',unit:'GPa',source_page:7,source_excerpt:'原文短摘录',paper_id:99,image_path:'/private/a.png',reviewer:'secret'}},alternate:{{value_text:'4.0',unit:'GPa',source_page:7,source_excerpt:'备选短摘录'}},conflict_reason:'数值不一致',scores:{{agreement:.5,factuality:.8,completeness:.7,evidence:.9,overall:.72}},allowed_operations:{{approve:{{available:true}},reject:{{available:true}},correct:{{available:true}},merge:{{available:false}},split:{{available:false}}}}}}]}};
const projected=api.publicReviewQueue(dto);assert(projected);assert.equal(projected.total,1);const item=projected.items[0];assert.equal(item.paperUid,paperUid);assert.equal(item.candidate.value_text,'4.1');assert.equal(item.candidate.source_page,7);assert.equal(item.candidate.paper_id,undefined);assert.equal(item.candidate.image_path,undefined);assert.equal(item.candidate.reviewer,undefined);
const html=api.reviewCandidateHTML(item);for(const text of ['主候选','备选','冲突原因','五项评分','原文页','原文短摘录','批准','不采用','确认纠正'])assert(html.includes(text),text);assert(!html.includes(token));assert(!html.includes('/private/a.png'));assert(!html.includes('secret'));
assert.equal(api.publicReviewQueue({{...dto,total:2}}),null);assert.equal(api.publicReviewQueue({{...dto,items:[{{...dto.items[0],entity_type:'unknown'}}]}}),null);assert.equal(api.publicReviewQueue({{...dto,items:[{{...dto.items[0],paper_uid:'paper_bad'}}]}}),null);
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_review_queue_safety_contract_is_bounded(self) -> None:
        for field in (
            "value_text", "finding_text", "caption", "source_page", "source_excerpt",
            "agreement", "factuality", "completeness", "evidence", "overall",
        ):
            self.assertIn(field, self.runtime)
        for marker in (
            "state.reviewQueue.pending.has(tabId)",
            "state.reviewQueue.request", "state.searchRequest+=1",
            "review_token_expired", "review_candidate_changed",
            'error?.nextAction==="refresh_queue"',
            'error?.stage?` · 阶段',
            "数据已保存，索引待恢复。",
        ):
            self.assertIn(marker, self.runtime)
        for forbidden in ("paper_id", "candidate_id", "image_path", "pdf_path", "reviewer"):
            self.assertNotIn(f'data-review-{forbidden}', self.runtime + self.index)

    def test_fusion_layout_accessibility_and_responsive_contract(self) -> None:
        for marker in (
            "--fusion-titlebar:34px", "--fusion-activity:48px", "--fusion-context:244px",
            "--fusion-tabs:36px", "--fusion-inspector:340px", "--fusion-statusbar:22px",
            "@media(min-width:1600px)", "@media(min-width:1200px) and (max-width:1599px)",
            "@media(min-width:640px) and (max-width:1199px)", "@media(max-width:639px)",
            "@media(prefers-reduced-motion:reduce)", "position:sticky", "font-variant-numeric:tabular-nums",
            'event.key==="Escape"', "focusReturn", "aria-selected", "aria-current",
        ):
            self.assertIn(marker, self.base_css + self.css + self.runtime + self.index)

    def test_node_runtime_gate_pdf_and_late_federated_response(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert');
class Classes{{constructor(){{this.s=new Set()}}toggle(k,v){{v?this.s.add(k):this.s.delete(k)}}add(k){{this.s.add(k)}}remove(k){{this.s.delete(k)}}contains(k){{return this.s.has(k)}}}}
class El{{constructor(dataset={{}}){{this.dataset=dataset;this.hidden=false;this.disabled=false;this.classList=new Classes();this.attrs={{}};this.listeners={{}};this.textContent='';this.innerHTML='';this.value='';this.src='';this.tabIndex=0;this.isConnected=true;}} addEventListener(k,f){{(this.listeners[k]??=[]).push(f)}} setAttribute(k,v){{this.attrs[k]=String(v)}} removeAttribute(k){{delete this.attrs[k]}} focus(){{globalThis.focused=this}} querySelector(){{return new El()}} querySelectorAll(){{return []}} matches(){{return false}}}}
const panels=['paper','search','personal','package','settings'].map(viewPanel=>new El({{viewPanel}})),navs=['paper','search','personal','package','settings'].map(view=>new El({{view}})),contexts=['paper','search','personal','package','settings'].map(contextView=>new El({{contextView}}));
const modes=['precise','librarian'].map(searchModePanel=>new El({{searchModePanel}})),sources=['workspace','official','private','all'].map(searchSource=>new El({{searchSource}}));
const ids={{}};for(const id of ['fusion-editor','fusion-context-title','fusion-breadcrumb','fusion-primary-tab-label','fusion-detail-tab-label','fusion-status-context','fusion-status-operation','fusion-inspector-title','fusion-inspector-body','fusion-context','fusion-inspector','fusion-search-results','fusion-search-query','fusion-run-precise-search','fusion-open-librarian','fusion-librarian-stage','fusion-librarian-status','fusion-literature-content','fusion-pdf-viewer','fusion-pdf-frame','fusion-pdf-title','fusion-close-pdf','fusion-open-pdf','fusion-start-extraction','fusion-literature-action-status','fusion-personal-status','fusion-personal-filename','fusion-personal-review','fusion-personal-sheet','fusion-personal-columns','fusion-project-name','fusion-sample-name','fusion-sample-material','fusion-run-name','fusion-run-method','fusion-run-conditions','fusion-run-note','fusion-personal-series-list','fusion-personal-series-warning','fusion-personal-series-add','fusion-personal-ai','fusion-personal-confirm','fusion-reviewed-state','fusion-review-context-state','fusion-ai-demo-context-state','fusion-sheet-summary','fusion-data-grid','fusion-personal-grid-wrap','fusion-personal-page-controls','fusion-personal-page-status','fusion-personal-page-prev','fusion-personal-page-next','fusion-personal-search-recovery','fusion-personal-search-recovery-title','fusion-personal-search-recovery-note','fusion-personal-search-refresh','fusion-ai-settings-status','fusion-ai-provider','fusion-ai-models','fusion-ai-model-save','fusion-ai-key-save','fusion-ai-key-delete','fusion-ai-test','fusion-ai-credential-state','fusion-ai-test-plan','fusion-ai-key','fusion-evidence-detail','fusion-evidence-detail-body','fusion-detail-heading','fusion-detail-identity','fusion-detail-pdf'])ids['#'+id]=new El();
ids['[data-close-all-drawers]']=new El();
ids['.fusion-sheet-tabs']=new El();ids['[data-context-view="personal"] .fusion-tree-row.active span']=new El();
const all={{'[data-view-panel]':panels,'.fusion-nav[data-view]':navs,'[data-context-view]':contexts,'[data-search-mode-panel]':modes,'[data-search-source]':sources,'[data-evidence-index],[data-search-evidence-index]':[],'[data-search-evidence-index]':[],'[data-open-drawer]':[],'[data-personal-column]':[],'[data-context-view="personal"] [data-sheet]':[],'[data-fusion-ai-task]':[]}};
globalThis.document={{readyState:'loading',documentElement:{{dataset:{{}}}},body:{{dataset:{{view:'paper'}}}},querySelector:s=>ids[s]||null,querySelectorAll:s=>all[s]||[],addEventListener:()=>{{}}}};globalThis.localStorage={{getItem:()=>null,setItem:()=>{{}}}};globalThis.confirm=()=>true;
globalThis.setTimeout=callback=>{{callback();return 0;}};
let pendingResolvers=[],reviewCount=0,refreshCount=0,failTable=false;const calls=[];globalThis.fetch=async(url,options={{}})=>{{url=String(url);calls.push([url,options.method||'GET',options.body]);const headers={{get:()=> 'csrf-next'}};
 if(url==='/api/desktop/ai/providers')return{{ok:true,headers,json:async()=>({{schema_version:'ai-desktop-catalog-v1',providers:[{{provider_id:'deepseek',display_name:'DeepSeek',model_options:{{}}}}],capability_test:{{provider_id:'deepseek',connection_maximum_model_calls:1,business_maximum_model_calls:{{literature_extraction:8,librarian:8,selected_evidence_chat:2,personal_suggestion:2}}}}}})}};
 if(url==='/api/desktop/ai/settings')return{{ok:true,headers,json:async()=>({{schema_version:'ai-runtime-public-state-v1',provider_id:'deepseek',revision:1,task_models:{{}},readiness:{{schema_version:'ai-readiness-v1',provider_connection:{{state:'ready',reason_code:'ai_connection_ready',next_action:'none'}},harness:{{state:'ready',reason_code:'harness_runtime_ready',next_action:'none'}},businesses:{{literature_extraction:{{state:'ready',reason_code:'ai_business_ready',next_action:'none'}},librarian:{{state:'ready',reason_code:'ai_business_ready',next_action:'none'}},selected_evidence_chat:{{state:'ready',reason_code:'ai_business_ready',next_action:'none'}},personal_suggestion:{{state:'ready',reason_code:'ai_business_ready',next_action:'none'}}}}}}}})}};
 if(url==='/api/desktop/ai/custom-provider')return{{ok:true,headers,json:async()=>({{schema_version:'custom-ai-provider-v1',provider_id:'custom',revision:0,configured:false}})}};
 if(url==='/api/desktop/ai/credentials/deepseek'&&(options.method||'GET')==='GET')return{{ok:true,headers,json:async()=>({{schema_version:'ai-credential-status-v1',provider_id:'deepseek',configured:true}})}};
 if(url==='/api/desktop/ai/credentials/deepseek'&&options.method==='POST')return{{ok:true,headers,json:async()=>({{schema_version:'ai-credential-status-v1',provider_id:'deepseek',configured:true}})}};
 if(url==='/api/desktop/ai/providers/deepseek/test-actions')return{{ok:true,headers,json:async()=>({{schema_version:'server-prepared-ai-action-v1',scope:'capability_test',provider_id:'deepseek',action_id:'action-test',maximum_calls:1,model:'deepseek-v4-pro',display:'连接验证'}})}};
 if(url==='/api/desktop/ai/providers/deepseek/test')return{{ok:true,headers,json:async()=>({{schema_version:'ai-capability-test-result-v1',status:'verified'}})}};
 if(url==='/api/desktop/ai/actions/personal_suggestion/prepare')return{{ok:true,headers,json:async()=>({{schema_version:'server-prepared-ai-action-v1',scope:'personal_suggestion',provider_id:'deepseek',disclosure_version:'personal-suggestion-disclosure-v1',action_id:'action-1',maximum_calls:1,model:'deepseek-v4-pro',display:'实验预填'}})}};
 if(url==='/api/desktop/ai/consents'){{const action=JSON.parse(options.body).action_id;return{{ok:true,headers,json:async()=>({{schema_version:'ai-consent-v1',scope:action==='action-test'?'capability_test':'personal_suggestion',nonce:'nonce-1'}})}};}}
 if(url==='/api/desktop/ai/actions/personal_suggestion/execute-jobs')return{{ok:true,headers,json:async()=>({{schema_version:'ai-execution-job-v1',job_id:'ai_job_abcdefghijklmnopqrstuvwxyz',scope:'personal_suggestion',status:'running',events:[]}})}};
 if(url==='/api/desktop/ai/jobs/ai_job_abcdefghijklmnopqrstuvwxyz')return{{ok:true,headers,json:async()=>({{schema_version:'ai-execution-job-v1',job_id:'ai_job_abcdefghijklmnopqrstuvwxyz',scope:'personal_suggestion',status:'completed',events:[],result:{{schema_version:'personal-import-suggestion-v1',import_id:'personal_import_abcdefghijklmnop',project:{{name:'W-Ta'}},sample:{{name:'S1'}},run:{{name:'R1',method:'nanoindentation'}},columns:[],series:[]}}}})}};
 if(url==='/api/desktop/personal-imports/preview')return{{ok:true,headers,json:async()=>({{schema_version:'personal-import-preview-v1',status:{{schema_version:'personal-import-status-v1',import_id:'personal_import_abcdefghijklmnop',revision:null,indexable:false}},preview:{{schema_version:'personal-tabular-preview-v1',source_file:{{original_name:'real.csv'}},sheets:[{{sheet_name:'Sheet1',row_count:2,columns:[{{source_name:'Dose',data_type:'number',role:'independent',meaning:'剂量',unit:'dpa'}}],sample_rows:[{{Dose:1}},{{Dose:2}}]}}]}}}})}};
 if(url==='/api/desktop/personal-imports/search-status')return{{ok:true,headers,json:async()=>({{schema_version:'personal-search-readiness-v1',state:'ready',ready:true,document_count:2}})}};
 if(url==='/api/desktop/personal-imports/search-refresh'){{refreshCount+=1;return{{ok:true,headers,json:async()=>({{schema_version:'personal-search-readiness-v1',state:'ready',ready:true,document_count:3}})}};}}
 if(url==='/api/desktop/personal-imports/personal_import_abcdefghijklmnop/sheets/0/rows?page=1&page_size=50')return{{ok:true,headers,json:async()=>({{schema_version:'personal-tabular-page-v1',import_id:'personal_import_abcdefghijklmnop',sheet_index:0,sheet_name:'Sheet1',page:1,page_size:50,total_rows:2,has_next:false,columns:['Dose'],rows:[['1'],['2']]}})}};
 if(url==='/api/desktop/personal-imports/personal_import_abcdefghijklmnop/reviewed-import'){{reviewCount+=1;if(reviewCount===3)return{{ok:false,headers,json:async()=>({{code:'personal_search_refresh_failed',message:'saved but index pending'}})}};const uid=reviewCount===1?'private:table:one':'private:table:two';return{{ok:true,headers,json:async()=>({{schema_version:'personal-import-status-v1',import_id:'personal_import_abcdefghijklmnop',revision:reviewCount,indexable:true,next_action:{{kind:'open_personal_table',source_scope:'private',source_id:'personal-lab',entity_type:'table',entity_uid:uid,label:'打开刚导入的表格'}}}})}};}}
 if(url.startsWith('/api/desktop/personal-experiments/table?')){{if(failTable)return{{ok:false,headers,json:async()=>({{code:'personal_table_unavailable',message:'unavailable'}})}};const params=new URL('http://local'+url).searchParams,uid=params.get('entity_uid');return{{ok:true,headers,json:async()=>({{schema_version:'personal-table-page-v1',source_id:'personal-lab',entity_uid:uid,title:'刚导入的真实表格',sheet_name:'Sheet1',columns:[{{name:'Dose',role:'independent',data_type:'number',meaning:'剂量',unit:'dpa'}}],conditions:{{}},series:[],page:1,page_size:50,total:2,has_next:false,rows:[{{Dose:'1'}},{{Dose:'2'}}]}})}};}}
 if(url.startsWith('/api/desktop/federated-search?'))return new Promise(resolve=>pendingResolvers.push(()=>resolve({{ok:true,headers,json:async()=>({{schema_version:'federated-search-page-v1',results:[{{document:{{entity_type:'table',source_scope:'private',source_id:'lab',entity_uid:'e1',display_title:'硬度表',source_excerpt:'真实私人实验'}}}}]}})}})));
 throw new Error('unexpected:'+url)}};
eval(fs.readFileSync({str(WEB / 'ai_consent.js')!r},'utf8'));eval(fs.readFileSync({str(WEB / 'document_tab_store.js')!r},'utf8'));eval(fs.readFileSync({str(WEB / 'pane_layout_controller.js')!r},'utf8'));eval(fs.readFileSync({str(WEB / 'workspace_layout_controller.js')!r},'utf8'));eval(fs.readFileSync({str(WEB / 'fusion_pdf_controller.js')!r},'utf8'));eval(fs.readFileSync({str(WEB / 'fusion_personal_import.js')!r},'utf8'));eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));const api=globalThis.AutoResearchFusion;
    (async()=>{{await api.loadAISettingsUI();ids['#fusion-ai-key'].value='sk-private-never-render';await api.saveAIKey();assert.equal(ids['#fusion-ai-key'].value,'');assert(!Object.values(ids).some(node=>node.textContent.includes('sk-private-never-render')));api.state.paper={{id:7,title:'Real paper'}};assert(api.openCurrentPDF());assert.equal(ids['#fusion-pdf-frame'].src,'/api/papers/7/pdf#page=1&zoom=page-width');assert.equal(ids['#fusion-literature-content'].hidden,true);assert(api.closeCurrentPDF());assert.equal(ids['#fusion-literature-content'].hidden,false);
 const auth=await api.preparedAuthorization('personal_suggestion',{{import_id:'personal_import_abcdefghijklmnop',sheet_index:0}});assert.equal(auth.actionId,'action-1');assert(calls.some(x=>x[0]==='/api/desktop/ai/consents'));
 api.switchView('search',{{focus:false}});api.setSearchSource('private');const late=api.runPreciseSearch();api.switchView('package',{{focus:false}});pendingResolvers.shift()();await late;assert.equal(api.state.view,'package');assert.equal(api.state.searchResults.length,0,'late private search must not replace the current package module');
 api.switchView('search',{{focus:false}});api.setSearchSource('private');const ready=api.runPreciseSearch();pendingResolvers.shift()();await ready;assert.equal(api.state.searchResults[0].title,'硬度表');assert(calls.some(x=>x[0].includes('source_scope=private')));
 globalThis.pywebview={{api:{{select_personal_data_file:async()=>({{ok:true,cancelled:false,selection:{{selection_id:'opaque-selection'}}}})}}}};api.switchView('personal',{{focus:false}});await api.choosePersonalFile();assert.equal(api.state.personalStatus.import_id,'personal_import_abcdefghijklmnop');await api.loadPersonalPreviewPage(0,1);await api.requestPersonalSuggestion();assert.equal(ids['#fusion-project-name'].value,'W-Ta');const broadBefore=calls.filter(x=>x[0].startsWith('/api/desktop/federated-search?')).length;await api.confirmPersonalImport();assert.equal(api.state.view,'personal');assert.equal(calls.filter(x=>x[0].startsWith('/api/desktop/federated-search?')).length,broadBefore,'confirmed import must not use broad private search');let personalTab=api.documentTabs.snapshot().tabs.find(tab=>tab.identity?.entityUid==='private:table:one');assert(personalTab&&personalTab.groupId==='primary'&&personalTab.pinned&&personalTab.payload.page.title==='刚导入的真实表格');assert.equal(personalTab.title,'刚导入的真实表格','validated page title must replace operation label');assert.equal(api.workspaceProjection().secondary,'hidden');assert(ids['#fusion-personal-status'].textContent.includes('已打开'));
 failTable=true;await api.confirmPersonalImport();assert.equal(ids['#fusion-reviewed-state'].textContent,'✓ 已核验并保存');assert(ids['#fusion-personal-status'].textContent.includes('数据已保存，但表格暂时无法打开'));assert(!ids['#fusion-personal-status'].textContent.includes('导入未完成'));
 failTable=false;await api.confirmPersonalImport();assert.equal(ids['#fusion-reviewed-state'].textContent,'✓ 已核验并保存');assert(ids['#fusion-personal-status'].textContent.includes('数据已保存，但搜索索引待恢复'));assert.equal(ids['#fusion-personal-search-recovery'].hidden,false);assert(!ids['#fusion-personal-status'].textContent.includes('导入未完成'));const reviewedBeforeRefresh=reviewCount;assert.equal(await api.refreshPersonalSearch(),true);assert.equal(refreshCount,1);assert.equal(reviewCount,reviewedBeforeRefresh,'search recovery must not repeat reviewed import');assert.equal(ids['#fusion-personal-search-recovery'].hidden,true);globalThis.innerWidth=1024;assert(await api.openImportedPersonalTable({{kind:'open_personal_table',source_scope:'private',source_id:'personal-lab',entity_type:'table',entity_uid:'private:table:one',label:'打开刚导入的表格'}},api.state.personalAction));assert.equal(api.workspaceProjection().secondary,'hidden');assert.equal(api.documentTabs.snapshot().activeGroupId,'primary');assert(api.documentTabs.snapshot().tabs.some(tab=>tab.identity?.entityUid==='private:table:one'));globalThis.innerWidth=720;assert(await api.openImportedPersonalTable({{kind:'open_personal_table',source_scope:'private',source_id:'personal-lab',entity_type:'table',entity_uid:'private:table:one',label:'打开刚导入的表格'}},api.state.personalAction));personalTab=api.documentTabs.snapshot().tabs.find(tab=>tab.identity?.entityUid==='private:table:one');assert.equal(personalTab.groupId,'primary');assert.equal(personalTab.title,'刚导入的真实表格');assert.equal(api.state.evidenceDetailOpen,true);assert.equal(api.closeEvidenceDetail({{focus:false}}),true);assert.equal(api.state.view,'personal');
 assert.equal(api.publicPersonalImportNextAction({{kind:'open_personal_table',source_scope:'private',source_id:'personal-lab',entity_type:'table',entity_uid:'private:table:one',label:'打开刚导入的表格',path:'/secret'}}),null);assert.equal(api.publicPersonalImportNextAction({{kind:'open_personal_table',source_scope:'private',source_id:'../secret',entity_type:'table',entity_uid:'private:table:one',label:'打开刚导入的表格'}}),null);assert(!JSON.stringify(api.state).includes('/secret'));assert(!JSON.stringify(api.documentTabs.snapshot()).includes('sha256'));
 const before=calls.length;api.state.aiContext=null;globalThis.AutoResearchAIConsent=undefined;assert.equal(await api.preparedAuthorization('personal_suggestion',{{import_id:'personal_import_abcdefghijklmnop',sheet_index:0}}),null,'missing disclosure gate must fail closed');assert.equal(calls.slice(before).filter(x=>x[0]==='/api/desktop/ai/consents').length,0);
}})().catch(error=>{{console.error(error);process.exitCode=1}});
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
