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
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = (WEB / "index.html").read_text(encoding="utf-8")
        cls.base_css = (WEB / "app.css").read_text(encoding="utf-8")
        cls.css = (WEB / "workbench.css").read_text(encoding="utf-8")
        cls.runtime = (WEB / "fusion_review.js").read_text(encoding="utf-8")

    def test_single_fusion_owner_and_script_order(self) -> None:
        parser = _IDs()
        parser.feed(self.index)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertEqual(self.index.count('/static/fusion_review.js'), 1)
        self.assertEqual(self.index.count('/static/ai_consent.js'), 1)
        self.assertLess(self.index.index('/static/ai_consent.js'), self.index.index('/static/fusion_review.js'))
        for legacy in ("/static/app.js", "/static/workbench.js", "/static/desktop_product.js", "/static/package_center.js"):
            self.assertNotIn(legacy, self.index)
        self.assertNotIn("appendChild", self.runtime)
        navigation = re.search(r'<nav class="fusion-activity".*?</nav>', self.index, re.DOTALL)
        self.assertIsNotNone(navigation)
        for name in ("paper", "search", "personal", "package", "settings"):
            self.assertEqual(navigation.group(0).count(f'data-view="{name}"'), 1)
            self.assertEqual(self.index.count(f'data-view-panel="{name}"'), 1)

    def test_real_workflows_are_top_level_and_pdf_stays_in_workspace(self) -> None:
        for element_id in (
            "fusion-import-pdf", "fusion-start-extraction", "fusion-open-pdf",
            "fusion-run-precise-search", "fusion-open-librarian",
            "fusion-select-data-file", "fusion-personal-ai", "fusion-personal-confirm",
            "fusion-pdf-viewer", "fusion-close-pdf", "fusion-pdf-frame",
        ):
            self.assertEqual(self.index.count(f'id="{element_id}"'), 1)
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
        detail_tab = re.search(r'<button[^>]+data-tab="detail"[^>]*>', self.index)
        self.assertIsNotNone(detail_tab)
        self.assertNotIn("data-fusion-disabled", detail_tab.group(0))
        self.assertIn('aria-controls="fusion-evidence-detail"', detail_tab.group(0))
        for marker in (
            'const EVIDENCE_LABELS=Object.freeze({item:', "function openEvidenceDetail(",
            "function closeEvidenceDetail(", "function renderEvidenceDetail(",
            'visualAsset:"/api/visual-assets"', 'federatedEvidence:"/api/desktop/federated-evidence"',
            "state.evidenceDetailRequest", "evidenceIdentity(state.evidenceDetail)!==identity",
        ):
            self.assertIn(marker, self.runtime)
        self.assertNotIn("#visual-dialog", self.runtime)
        self.assertNotIn("showModal()", self.runtime)

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
assert.equal(api.detailPDFURL({{sourceScope:'official',sourceId:'official',paperUid:'paper-x',pdfAvailable:true,collectionKind:'literature_collection'}}),'');
assert.equal(api.detailPDFURL({{sourceScope:'private',sourceId:'literature',paperUid:'paper-x',pdfAvailable:false,collectionKind:'literature_collection'}}),'');
assert.equal(api.detailPDFURL({{sourceScope:'workspace',paperId:7,page:3}}),'/api/papers/7/pdf#page=3');
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
            'source_scope=private', 'setSearchSource("private")',
            'page.results.map(hit=>hit?.document)', 'source!==state.searchSource',
        ):
            self.assertIn(marker, self.runtime)
        self.assertNotIn("官方资料库</span><b>0.9.2+", self.index)
        self.assertNotIn("我的实验</span><b>合成", self.index)

    def test_prepared_actions_reuse_versioned_disclosure_gate(self) -> None:
        for marker in (
            "globalThis.AutoResearchAIConsent.ensure", "ai_consent_gate_unavailable",
            "updateTrustedProviders", "prepared.provider_id!==context.provider_id",
            "prepared.disclosure_version!==disclosureVersion", "librarian-ai-stage-v1",
            "literature-extraction-stage-summary-v1", "literature-extraction-commit-result-v1",
        ):
            self.assertIn(marker, self.runtime)
        self.assertNotIn("consent:true", self.runtime)
        self.assertNotIn('localStorage.setItem("job_token', self.runtime)

    def test_personal_preview_review_and_single_import_are_real(self) -> None:
        for marker in (
            "select_personal_data_file", 'personalPreview:"/api/desktop/personal-imports/preview"',
            "personal-import-preview-v1", "personal-import-suggestion-v1", "/reviewed-import",
            "reviewed:true", "collectPersonalDraft", "受限文件检查", "一次确认", "indexable!==true",
            'row[column.source_name]', '中央顶部“导入 PDF”',
        ):
            self.assertIn(marker, self.index + self.runtime)
        self.assertNotIn('id="fusion-mark-reviewed"', self.index)
        self.assertNotIn('id="fusion-demo-suggestion"', self.index)
        self.assertNotIn("showDemoSuggestion", self.runtime)

    def test_provider_settings_use_trusted_catalog_and_secure_credentials(self) -> None:
        for marker in (
            "/api/desktop/ai/providers", "/api/desktop/ai/settings", "/api/desktop/ai/credentials/",
            "test-actions", "expected_revision", "task_models", "api_key", 'type="password"',
            "不接受自定义 URL", "不会回显",
        ):
            self.assertIn(marker, self.index + self.runtime)
        self.assertNotIn("base_url", self.runtime)
        self.assertNotIn("chat_endpoint", self.runtime)

    def test_package_center_has_complete_safe_sequences(self) -> None:
        for element_id in (
            "fusion-package-official-select", "fusion-package-installed",
            "fusion-package-literature-plan", "fusion-package-literature-export",
            "fusion-package-personal-plan", "fusion-package-personal-export",
            "fusion-package-user-select", "fusion-package-user-sha",
            "fusion-package-checksum-ack", "fusion-package-unencrypted-ack",
            "fusion-package-source-ack", "fusion-package-user-import",
            "fusion-package-jobs",
        ):
            self.assertEqual(self.index.count(f'id="{element_id}"'), 1)
        for marker in (
            'select_evidence_package', 'select_package_export_destination',
            '"/api/desktop/evidence-packages/import"',
            '"/api/desktop/evidence-packages/rollback"',
            '"/api/desktop/package-center/export-plan"',
            '"/api/desktop/package-center/export"',
            '"/api/desktop/package-center/inspect"',
            '"/api/desktop/package-center/import"',
            'expected_sha:', 'checksum_ack:true', 'keep_conflicts:',
            'unencrypted_ack:true', 'unauthenticated_source_ack:true',
            'internal_use_only_ack:true', 'paper_rights:paperRights',
            'plan.exceeds_size_limit===true', 'await waitPackageJob',
        ):
            self.assertIn(marker, self.runtime)
        export_body = re.search(
            r"async function exportPlannedPackage\(kind\)\{(?P<body>.*?)\n  function updateUserPackageImportButton",
            self.runtime,
            re.DOTALL,
        )
        self.assertIsNotNone(export_body)
        body = export_body.group("body")
        self.assertLess(body.index("plan_token"), body.index("choosePackageDestination"))
        self.assertLess(body.index("choosePackageDestination"), body.index("package-center/export"))
        self.assertIn("选择保存位置并导出", self.index)
        self.assertIn("2 GB", self.runtime)
        self.assertIn("另一条可信渠道", self.index + self.runtime)
        self.assertIn("未加密", self.index + self.runtime)
        self.assertIn("不认证发送者身份", self.index + self.runtime)

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

    def test_selected_evidence_ai_is_workspace_numeric_only(self) -> None:
        for element_id in (
            "fusion-evidence-ai-question", "fusion-evidence-ai",
            "fusion-evidence-ai-reason", "fusion-evidence-ai-output",
        ):
            self.assertEqual(self.index.count(f'id="{element_id}"'), 1)
        for marker in (
            'preparedAuthorization("selected_evidence_chat"',
            'executePrepared("selected_evidence_chat"',
            'entity_type:identity.entityType', 'entity_id:identity.entityId',
            'row.sourceScope!=="workspace"', 'Number.isSafeInteger(candidate)',
        ):
            self.assertIn(marker, self.runtime)
        program = f"""
globalThis.document={{readyState:'loading',querySelector:()=>null,querySelectorAll:()=>[],addEventListener:()=>{{}}}};
globalThis.localStorage={{getItem:()=>null,setItem:()=>{{}}}};
eval(require('fs').readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));
const api=globalThis.AutoResearchFusion,assert=require('assert');
api.state.view='search';
api.state.selectedEvidence={{type:'item',sourceScope:'workspace',itemId:17}};
assert.deepEqual(api.selectedEvidenceAIIdentity(),{{key:'item:17',entityType:'item',entityId:17}});
api.state.selectedEvidence={{type:'figure',sourceScope:'workspace',assetId:23}};
assert.equal(api.selectedEvidenceAIIdentity().entityId,23);
api.state.selectedEvidence={{type:'item',sourceScope:'official',itemId:17}};
assert.equal(api.selectedEvidenceAIIdentity(),null);
api.state.selectedEvidence={{type:'item',sourceScope:'private',itemId:17}};
assert.equal(api.selectedEvidenceAIIdentity(),null);
api.state.selectedEvidence={{type:'item',sourceScope:'workspace',itemId:'17'}};
assert.equal(api.selectedEvidenceAIIdentity(),null);
api.state.selectedEvidence={{type:'figure',sourceScope:'workspace',assetId:-1}};
assert.equal(api.selectedEvidenceAIIdentity(),null);
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
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
api.renderLibrarianFinal({{librarian_core_version:'librarian-v3',answer:'完整回答',research_state:null,state_token:'',results:[{{entity_type:'item',source_scope:'official',source_id:'official-v1',entity_uid:'official:item:1',meaning:'硬度变化',value_text:'4.1',unit:'GPa',article_title:'论文A',source_page:3,source_excerpt:'原文证据'}}],report:{{schema_version:'research-report-v1',direct_conclusion:{{status:'found',text:'直接结论正文',refs:['R1']}},evidence_matrix:[{{property:'硬度',result:'4.1 GPa',material:'W',conditions:'300 K',article_title:'论文A',source_page:3,refs:['R1']}}],related_evidence:[{{summary:'相关证据正文',relaxed_constraints:['温度'],refs:['R2']}}],database_gaps:['缺少剂量范围'],suggested_followups:['继续解释R1']}},recommended_articles:[{{article_title:'推荐论文',why_recommended:'满足硬条件',first_author:'A',year:2025,doi:'10.1/a',recommendation_level:'direct',supporting_refs:['R1'],coverage_warning:'缺少图片'}}],suggested_actions:[{{schema_version:'suggested-action-v1',answerable:true,text:'详细解释R1'}}]}},'问题');const html=ids['#fusion-librarian-output'].innerHTML;for(const text of ['完整回答','直接结论正文','证据矩阵','相关证据正文','缺少剂量范围','继续解释R1','原文证据','推荐论文','缺少图片','详细解释R1'])assert(html.includes(text),text);
}})().catch(error=>{{console.error(error);process.exitCode=1}});
"""
        result = subprocess.run(
            ["node", "-e", program],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_fusion_layout_accessibility_and_responsive_contract(self) -> None:
        for marker in (
            "--fusion-titlebar:34px", "--fusion-activity:48px", "--fusion-context:244px",
            "--fusion-tabs:36px", "--fusion-inspector:340px", "--fusion-statusbar:22px",
            "@media(min-width:1280px)", "@media(min-width:900px) and (max-width:1279px)",
            "@media(min-width:640px) and (max-width:899px)", "@media(max-width:639px)",
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
const ids={{}};for(const id of ['fusion-editor','fusion-context-title','fusion-breadcrumb','fusion-primary-tab-label','fusion-detail-tab-label','fusion-status-context','fusion-status-operation','fusion-inspector-title','fusion-inspector-body','fusion-context','fusion-inspector','fusion-search-results','fusion-search-query','fusion-run-precise-search','fusion-open-librarian','fusion-librarian-stage','fusion-librarian-status','fusion-literature-content','fusion-pdf-viewer','fusion-pdf-frame','fusion-pdf-title','fusion-close-pdf','fusion-open-pdf','fusion-start-extraction','fusion-literature-action-status','fusion-personal-status','fusion-personal-filename','fusion-personal-review','fusion-personal-sheet','fusion-personal-columns','fusion-project-name','fusion-sample-name','fusion-sample-material','fusion-run-name','fusion-run-method','fusion-personal-ai','fusion-personal-confirm','fusion-reviewed-state','fusion-review-context-state','fusion-ai-demo-context-state','fusion-sheet-summary','fusion-data-grid','fusion-ai-settings-status','fusion-ai-provider','fusion-ai-models','fusion-ai-model-save','fusion-ai-key-save','fusion-ai-key-delete','fusion-ai-test','fusion-ai-credential-state','fusion-ai-test-plan','fusion-ai-key'])ids['#'+id]=new El();
ids['[data-close-all-drawers]']=new El();
ids['.fusion-sheet-tabs']=new El();ids['[data-context-view="personal"] .fusion-tree-row.active span']=new El();
const all={{'[data-view-panel]':panels,'.fusion-nav[data-view]':navs,'[data-context-view]':contexts,'[data-search-mode-panel]':modes,'[data-search-source]':sources,'[data-evidence-index],[data-search-evidence-index]':[],'[data-search-evidence-index]':[],'[data-open-drawer]':[],'[data-personal-column]':[],'[data-context-view="personal"] [data-sheet]':[],'[data-fusion-ai-task]':[]}};
globalThis.document={{readyState:'loading',documentElement:{{dataset:{{}}}},body:{{dataset:{{view:'paper'}}}},querySelector:s=>ids[s]||null,querySelectorAll:s=>all[s]||[],addEventListener:()=>{{}}}};globalThis.localStorage={{getItem:()=>null,setItem:()=>{{}}}};globalThis.confirm=()=>true;
let pendingResolvers=[];const calls=[];globalThis.fetch=async(url,options={{}})=>{{url=String(url);calls.push([url,options.method||'GET',options.body]);const headers={{get:()=> 'csrf-next'}};
 if(url==='/api/desktop/ai/providers')return{{ok:true,headers,json:async()=>({{schema_version:'ai-desktop-catalog-v1',providers:[{{provider_id:'deepseek',display_name:'DeepSeek',model_options:{{}}}}],capability_test:{{provider_id:'deepseek',maximum_model_calls:2,unique_model_count:1}}}})}};
 if(url==='/api/desktop/ai/settings')return{{ok:true,headers,json:async()=>({{schema_version:'ai-runtime-public-state-v1',provider_id:'deepseek',revision:1,task_models:{{}}}})}};
 if(url==='/api/desktop/ai/credentials/deepseek'&&(options.method||'GET')==='GET')return{{ok:true,headers,json:async()=>({{schema_version:'ai-credential-status-v1',provider_id:'deepseek',configured:true}})}};
 if(url==='/api/desktop/ai/credentials/deepseek'&&options.method==='POST')return{{ok:true,headers,json:async()=>({{schema_version:'ai-credential-status-v1',provider_id:'deepseek',configured:true}})}};
 if(url==='/api/desktop/ai/actions/personal_suggestion/prepare')return{{ok:true,headers,json:async()=>({{schema_version:'server-prepared-ai-action-v1',scope:'personal_suggestion',provider_id:'deepseek',disclosure_version:'personal-suggestion-disclosure-v1',action_id:'action-1',maximum_calls:1,model:'deepseek-v4-pro',display:'实验预填'}})}};
 if(url==='/api/desktop/ai/consents')return{{ok:true,headers,json:async()=>({{schema_version:'ai-consent-v1',scope:'personal_suggestion',nonce:'nonce-1'}})}};
 if(url==='/api/desktop/ai/actions/personal_suggestion/execute')return{{ok:true,headers,json:async()=>({{schema_version:'personal-import-suggestion-v1',import_id:'personal_import_abcdefghijklmnop',project:{{name:'W-Ta'}},sample:{{name:'S1'}},run:{{name:'R1',method:'nanoindentation'}},columns:[],series:[]}})}};
 if(url==='/api/desktop/personal-imports/preview')return{{ok:true,headers,json:async()=>({{schema_version:'personal-import-preview-v1',status:{{schema_version:'personal-import-status-v1',import_id:'personal_import_abcdefghijklmnop',revision:null,indexable:false}},preview:{{schema_version:'personal-tabular-preview-v1',source_file:{{original_name:'real.csv'}},sheets:[{{sheet_name:'Sheet1',row_count:2,columns:[{{source_name:'Dose',data_type:'number',role:'independent',meaning:'剂量',unit:'dpa'}}],sample_rows:[{{Dose:1}},{{Dose:2}}]}}]}}}})}};
 if(url==='/api/desktop/personal-imports/personal_import_abcdefghijklmnop/reviewed-import')return{{ok:true,headers,json:async()=>({{schema_version:'personal-import-status-v1',import_id:'personal_import_abcdefghijklmnop',revision:1,indexable:true}})}};
 if(url.startsWith('/api/desktop/federated-search?'))return new Promise(resolve=>pendingResolvers.push(()=>resolve({{ok:true,headers,json:async()=>({{schema_version:'federated-search-page-v1',results:[{{document:{{entity_type:'table',source_scope:'private',source_id:'lab',entity_uid:'e1',display_title:'硬度表',source_excerpt:'真实私人实验'}}}}]}})}})));
 throw new Error('unexpected:'+url)}};
eval(fs.readFileSync({str(WEB / 'ai_consent.js')!r},'utf8'));eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));const api=globalThis.AutoResearchFusion;
(async()=>{{assert.equal(api.previewCellValue({{Dose:3}},{{source_name:'Dose'}},0),3);assert.equal(api.previewCellValue([4],{{source_name:'Dose'}},0),4);await api.loadAISettingsUI();ids['#fusion-ai-key'].value='sk-private-never-render';await api.saveAIKey();assert.equal(ids['#fusion-ai-key'].value,'');assert(!Object.values(ids).some(node=>node.textContent.includes('sk-private-never-render')));api.state.paper={{id:7,title:'Real paper'}};assert(api.openCurrentPDF());assert.equal(ids['#fusion-pdf-frame'].src,'/api/papers/7/pdf');assert.equal(ids['#fusion-literature-content'].hidden,true);assert(api.closeCurrentPDF());assert.equal(ids['#fusion-literature-content'].hidden,false);
 const auth=await api.preparedAuthorization('personal_suggestion',{{import_id:'personal_import_abcdefghijklmnop',sheet_index:0}});assert.equal(auth.actionId,'action-1');assert(calls.some(x=>x[0]==='/api/desktop/ai/consents'));
 api.switchView('search',{{focus:false}});api.setSearchSource('private');const late=api.runPreciseSearch();api.switchView('paper',{{focus:false}});pendingResolvers.shift()();await late;assert.equal(api.state.searchResults.length,0,'late result must not update inactive view');
 api.switchView('search',{{focus:false}});api.setSearchSource('private');const ready=api.runPreciseSearch();pendingResolvers.shift()();await ready;assert.equal(api.state.searchResults[0].title,'硬度表');assert(calls.some(x=>x[0].includes('source_scope=private')));
 globalThis.pywebview={{api:{{select_personal_data_file:async()=>({{ok:true,cancelled:false,selection:{{selection_id:'opaque-selection'}}}})}}}};api.switchView('personal',{{focus:false}});await api.choosePersonalFile();assert.equal(api.state.personalStatus.import_id,'personal_import_abcdefghijklmnop');await api.requestPersonalSuggestion();assert.equal(ids['#fusion-project-name'].value,'W-Ta');const imported=api.confirmPersonalImport();while(!pendingResolvers.length)await new Promise(resolve=>setImmediate(resolve));pendingResolvers.shift()();await imported;assert.equal(api.state.view,'search');assert.equal(api.state.searchSource,'private');assert(calls.some(x=>x[0].endsWith('/reviewed-import')));
 const before=calls.length;api.state.aiContext=null;globalThis.AutoResearchAIConsent=undefined;await api.preparedAuthorization('personal_suggestion',{{import_id:'personal_import_abcdefghijklmnop',sheet_index:0}}).then(()=>assert.fail('gate missing'),()=>{{}});assert.equal(calls.slice(before).filter(x=>x[0]==='/api/desktop/ai/consents').length,0);
}})().catch(error=>{{console.error(error);process.exitCode=1}});
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
