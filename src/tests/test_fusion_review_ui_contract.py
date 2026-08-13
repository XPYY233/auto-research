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

    def test_physical_shell_geometry_and_one_runtime_owner(self) -> None:
        for marker in (
            "--fusion-titlebar:34px",
            "--fusion-activity:48px",
            "--fusion-context:244px",
            "--fusion-tabs:36px",
            "--fusion-inspector:340px",
            "--fusion-statusbar:22px",
        ):
            self.assertIn(marker, self.base_css + self.css)
        self.assertEqual(self.index.count('class="fusion-titlebar"'), 1)
        self.assertEqual(self.index.count('class="fusion-activity"'), 1)
        self.assertEqual(self.index.count('class="fusion-context"'), 1)
        self.assertEqual(self.index.count('class="fusion-editor"'), 1)
        self.assertEqual(self.index.count('class="fusion-inspector"'), 1)
        self.assertEqual(self.index.count('class="fusion-statusbar"'), 1)
        self.assertEqual(self.index.count('/static/fusion_review.js'), 1)
        for legacy_runtime in ("/static/app.js", "/static/workbench.js", "/static/desktop_product.js", "/static/package_center.js"):
            self.assertNotIn(legacy_runtime, self.index)

    def test_five_views_have_unique_ids_and_single_primary_navigation(self) -> None:
        parser = _IDs()
        parser.feed(self.index)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        navigation = re.search(r'<nav class="fusion-activity".*?</nav>', self.index, re.DOTALL)
        self.assertIsNotNone(navigation)
        for name in ("paper", "search", "personal", "package", "settings"):
            self.assertEqual(navigation.group(0).count(f'data-view="{name}"'), 1)
            self.assertEqual(self.index.count(f'data-view-panel="{name}"'), 1)
            self.assertEqual(self.index.count(f'data-context-view="{name}"'), 1)
        self.assertNotIn("appendChild", self.runtime)

    def test_experience_boundary_is_visible_and_every_future_action_is_disabled(self) -> None:
        boundary = "Fusion GUI体验版 · 文献只读 · 实验为合成示例"
        self.assertIn(boundary, self.index)
        buttons = re.findall(r"<button\b[^>]*data-fusion-disabled[^>]*>", self.index)
        self.assertGreaterEqual(len(buttons), 14)
        self.assertTrue(all("disabled" in button for button in buttons))
        for forbidden in ("XMLHttpRequest", "sendBeacon", "pywebview", 'method:"POST"', 'method:"DELETE"'):
            self.assertNotIn(forbidden, self.runtime)
        self.assertEqual(set(re.findall(r'"(/api/[^"`?]+)', self.runtime)), {
            "/api/search-papers", "/api/search-v2", "/api/desktop/settings", "/api/desktop/settings/preferences",
        })

    def test_real_literature_uses_bounded_gets_with_terminal_states_and_generation(self) -> None:
        for marker in (
            'method:"GET"', 'credentials:"same-origin"', 'cache:"no-store"',
            "literatureRequest", 'request!==state.literatureRequest',
            'data-literature-state="loading"', 'literatureState("empty"', 'literatureState("error"',
            "publicPaper", "publicEvidence", 'paper_ids=${encodeURIComponent(String(id))}',
        ):
            self.assertIn(marker, self.index + self.runtime)
        for fake_count in ("4,362", "4,356"):
            self.assertNotIn(fake_count, self.index)

    def test_desktop_settings_are_authoritative_and_cache_is_prepaint_only(self) -> None:
        for marker in (
            'schema_version!=="desktop-settings-v1"', "settingsRevision", "expected_revision",
            'method:"PATCH"', 'preferences:{appearance:{theme:state.theme,density:state.density}}',
            "loadSettings", "hydrateSettings", "外观保存失败；本次会话仍保留当前选择",
        ):
            self.assertIn(marker, self.runtime)
        self.assertIn("本地缓存只用于避免首屏闪烁", self.index)

    def test_command_palette_session_review_and_package_placeholders_are_honest(self) -> None:
        for marker in (
            'id="fusion-command-palette"',
            'data-command-view="settings"',
            'event.key===","',
            "openCommandPalette",
            "closeCommandPalette",
            'id="fusion-mark-reviewed"',
            'id="fusion-demo-suggestion"',
            "markReviewed",
            "showDemoSuggestion",
            "体验版未读取",
        ):
            self.assertIn(marker, self.index + self.runtime)
        self.assertNotIn("4,356", self.index)
        self.assertNotIn("<dd>60</dd>", self.index)

    def test_synthetic_table_is_explicit_interactive_and_not_production_data(self) -> None:
        for marker in (
            "W-Ta_nanoindentation_demo.csv",
            "syntheticSheets",
            'hardness: {',
            'metadata: {',
            'setAttribute("role","grid")',
            "aria-rowcount",
            "aria-colcount",
            "ArrowLeft",
            "ArrowRight",
            "ArrowUp",
            "ArrowDown",
            "Home",
            "End",
            "PageUp",
            "PageDown",
            "updateColumnDefinition",
            "本次会话已检查",
            "没有调用任何模型",
            "合成数据，不来自生产数据库",
        ):
            self.assertIn(marker, self.index + self.runtime)
        self.assertIn("position:sticky", self.css)
        self.assertIn("font-variant-numeric:tabular-nums", self.css)

    def test_responsive_drawers_bottom_navigation_focus_and_motion(self) -> None:
        for marker in (
            "@media(min-width:1280px)",
            "@media(min-width:900px) and (max-width:1279px)",
            "@media(min-width:640px) and (max-width:899px)",
            "@media(max-width:639px)",
            "@media(prefers-reduced-motion:reduce)",
            "focusReturn",
            'event.key==="Escape"',
            "aria-current",
            "aria-selected",
            'setAttribute("aria-controls"',
            ".fusion-tabs [role='tab']",
        ):
            self.assertIn(marker, self.base_css + self.css + self.runtime)

    def test_node_fake_dom_exercises_views_grid_drawer_theme_and_zero_fetch(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert');
class Classes{{constructor(){{this.s=new Set()}}toggle(k,v){{v?this.s.add(k):this.s.delete(k)}}add(k){{this.s.add(k)}}remove(k){{this.s.delete(k)}}contains(k){{return this.s.has(k)}}}}
class El{{constructor(dataset={{}}){{this.dataset=dataset;this.hidden=false;this.classList=new Classes();this.attrs={{}};this.listeners={{}};this.textContent='';this.innerHTML='';this.tabIndex=0;this.isConnected=true;}}
 addEventListener(k,f){{(this.listeners[k]??=[]).push(f)}} click(){{for(const f of this.listeners.click||[])f({{currentTarget:this,target:this}})}}
 setAttribute(k,v){{this.attrs[k]=String(v)}}removeAttribute(k){{delete this.attrs[k]}}focus(){{globalThis.focused=this}}querySelector(){{return null}}matches(s){{return s.includes('[data-cell]')&&this.dataset.cell==='1'}}}}
const panels=['paper','search','personal','package','settings'].map(viewPanel=>new El({{viewPanel}}));
const navs=['paper','search','personal','package','settings'].map(view=>new El({{view}}));
const contexts=['paper','search','personal','package','settings'].map(contextView=>new El({{contextView}}));
const themeButtons=['system','light','dark'].map(themeChoice=>new El({{themeChoice}}));
const densityButtons=['comfortable','compact'].map(densityChoice=>new El({{densityChoice}}));
const sheets=['hardness','metadata'].map(sheet=>new El({{sheet}}));
const ids={{
 '#fusion-editor':new El(),'#fusion-context-title':new El(),'#fusion-breadcrumb':new El(),'#fusion-primary-tab-label':new El(),'#fusion-status-context':new El(),
 '#fusion-inspector-title':new El(),'#fusion-inspector-body':new El(),'#fusion-context':new El(),'#fusion-inspector':new El(),'#fusion-sheet-summary':new El(),
 '#fusion-data-grid':new El(),'#fusion-command':new El(),'[data-close-all-drawers]':new El(),'#fusion-paper-catalog':new El(),'#fusion-literature-content':new El(),
 '#fusion-search-results':new El(),'#fusion-current-paper-state':new El(),'#fusion-current-paper-count':new El(),'#fusion-settings-status':new El(),
 '#fusion-count-item':new El(),'#fusion-count-finding':new El(),'#fusion-count-table':new El(),'#fusion-count-figure':new El()
}};
ids['#fusion-data-grid'].setAttribute=El.prototype.setAttribute;ids['#fusion-data-grid'].querySelector=()=>new El();
const all={{'[data-view-panel]':panels,'.fusion-nav[data-view]':navs,'[data-context-view]':contexts,'[data-theme-choice]':themeButtons,'[data-density-choice]':densityButtons,'[data-sheet]':sheets,'[data-open-drawer]':[],'[data-close-drawer]':[],'.fusion-result':[],'#fusion-data-grid [data-cell]':[],'#fusion-data-grid th[data-column]':[],'.fusion-sheet-tabs [data-sheet]':sheets}};
const requests=[];let settingsRevision=3;
globalThis.localStorage={{getItem:()=>null,setItem:()=>{{}}}};globalThis.fetch=async(url,options={{}})=>{{requests.push([String(url),options]);const headers={{get:name=>name==='X-Auto-Research-CSRF'?'csrf-test':null}};
 if(url==='/api/desktop/settings'&&(!options.method||options.method==='GET'))return{{ok:true,headers,json:async()=>({{schema_version:'desktop-settings-v1',revision:settingsRevision,appearance:{{theme:'dark',density:'compact'}},locale:{{selected:'zh-CN',supported:['zh-CN']}}}})}};
 if(url==='/api/desktop/settings/preferences'&&options.method==='PATCH')return{{ok:true,headers,json:async()=>({{schema_version:'desktop-settings-v1',revision:++settingsRevision,appearance:{{theme:'light',density:'compact'}},locale:{{selected:'zh-CN',supported:['zh-CN']}}}})}};
 if(url==='/api/search-papers')return{{ok:true,headers,json:async()=>[{{id:7,title:'Real paper',doi:'10.1/real',six_row_count:2,six_workflow_label:'已核验'}}]}};
 if(String(url).startsWith('/api/search-v2?'))return{{ok:true,headers,json:async()=>({{rows:[{{entity_type:'item',meaning:'温度',value_text:'300',unit:'°C',source_page:6,article_title:'Real paper'}}]}})}};
 throw new Error('network forbidden:'+url)}};globalThis.focused=null;
globalThis.document={{readyState:'loading',documentElement:{{dataset:{{}}}},body:{{dataset:{{view:'paper'}}}},querySelector:s=>ids[s]||null,querySelectorAll:s=>all[s]||[],addEventListener:(k,f)=>{{if(k==='DOMContentLoaded')globalThis.boot=f}}}};
eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));
(async()=>{{const api=globalThis.AutoResearchFusion;assert(api);await api.loadSettings();await api.loadLiterature();assert.equal(api.state.paper.title,'Real paper');assert.equal(api.state.evidence[0].value,'300');
 api.switchView('search');assert.equal(document.body.dataset.view,'search');assert.equal(panels.filter(x=>!x.hidden).length,1);
 api.switchView('personal');assert.equal(document.body.dataset.view,'personal');const before=document.body.dataset.view;await Promise.resolve().then(()=>api.renderSheet('metadata'));assert.equal(document.body.dataset.view,before);
 assert(api.applyAppearance('dark','compact'));assert.equal(document.documentElement.dataset.theme,'dark');assert.equal(document.documentElement.dataset.density,'compact');assert.equal(api.syntheticSheets.hardness.rows.length,12);
 api.openDrawer('inspector',navs[2]);assert(ids['#fusion-inspector'].classList.contains('drawer-open'));api.closeDrawers();assert(!ids['#fusion-inspector'].classList.contains('drawer-open'));assert.strictEqual(globalThis.focused,navs[2]);
 assert(requests.every(([url,options])=>['/api/search-papers','/api/desktop/settings','/api/desktop/settings/preferences'].includes(url)||url.startsWith('/api/search-v2?')));assert(requests.every(([url,options])=>(options.method||'GET')==='GET'||(url==='/api/desktop/settings/preferences'&&options.method==='PATCH')));
}})().catch(error=>{{console.error(error);process.exitCode=1}});
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
