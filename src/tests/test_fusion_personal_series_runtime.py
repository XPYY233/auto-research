from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


WEB = Path(__file__).resolve().parents[1] / "auto_research" / "evidence" / "web"
FIXTURE = r"""
const seriesDTO=(uid='table-a',index=0)=>({
 schema_version:'personal-series-plot-v1',source_id:'private-lab',entity_uid:uid,series_index:index,
 name:index?'另一序列':'硬度随剂量',x_column:'dose',y_column:'hardness',uncertainty_column:'error',
 x_unit:'dpa',y_unit:'GPa',uncertainty_unit:'GPa',total_rows:123,valid_points:121,missing_rows:1,invalid_rows:1,
 uncertainty_missing_rows:1,uncertainty_invalid_rows:1,
 points:Array.from({length:123},(_,i)=>({row:i+1,x:i,y:i===49||i===70?null:3.2,x_text:String(i),y_text:i===49?'':i===70?'NaN':'3.200',uncertainty:i===5||i===7?null:.05,uncertainty_text:i===5?'':i===7?'-1':'0.050',status:i===49?'missing':i===70?'invalid':'valid'}))
});
const expected={sourceId:'private-lab',entityUid:'table-a',seriesIndex:0};
"""

RESTORE_FIXTURE = r"""
class RestoreElement {
 constructor(){this.dataset={};this.hidden=false;this.scrollTop=0;this.textContent='';this.attrs={};this.listeners={};this._html='';this.style={setProperty(){}};this.classList={add(){},remove(){},toggle(){},contains(){return false}}}
 set innerHTML(value){this._html=value;this.seriesPanel=null;const match=value.match(/data-personal-series-tab="([^"]+)"/);if(match){this.seriesPanel=new RestoreElement();this.seriesPanel.dataset.personalSeriesTab=match[1]}}
 get innerHTML(){return this._html}setAttribute(k,v){this.attrs[k]=String(v)}removeAttribute(k){delete this.attrs[k]}
 querySelector(){return null}querySelectorAll(){return []}addEventListener(k,fn){(this.listeners[k]??=[]).push(fn)}contains(){return false}focus(){focusCalls++}getBoundingClientRect(){return {width:1280,left:0}}
}
let ids={},panels=[],focusCalls=0;const memory=new Map(),calls=[],pending=[];
globalThis.localStorage={getItem:k=>memory.get(k)||null,setItem:(k,v)=>memory.set(k,v)};
globalThis.innerWidth=1280;globalThis.addEventListener=()=>{};
globalThis.fetch=(url,options={})=>{calls.push([String(url),options]);return new Promise(resolve=>pending.push(raw=>resolve({ok:true,headers:{get:()=>null},json:async()=>raw})))};
function boot(){
 ids={};for(const name of ['primary-editor-surface','secondary-editor-surface','secondary-editor-body','primary-document-body','evidence-detail','evidence-detail-body','detail-heading','detail-identity','context-title','breadcrumb','primary-tab-label','detail-tab-label','status-context','inspector-title','inspector-body'])ids['#fusion-'+name]=new RestoreElement();
 panels=['personal','search','settings'].map(view=>{const el=new RestoreElement();el.dataset.viewPanel=view;return el});
 globalThis.document={readyState:'loading',activeElement:null,documentElement:{dataset:{},style:{setProperty(){}}},body:{dataset:{}},querySelector:s=>ids[s]||null,querySelectorAll:s=>s==='[data-view-panel]'?panels:s==='[data-personal-series-tab]'?[ids['#fusion-evidence-detail-body'].seriesPanel].filter(Boolean):[],addEventListener(){}};
 for(const file of ['document_tab_store.js','pane_layout_controller.js','workspace_layout_controller.js','fusion_review.js'])eval(fs.readFileSync(WEB+'/'+file,'utf8'));
 const api=globalThis.AutoResearchFusion;api.state.view='personal';return api;
}
const identity=uid=>({sourceScope:'private',sourceId:'private-lab',entityType:'table',entityUid:uid});
const rawRow=uid=>({entity_type:'table',source_scope:'private',source_id:'private-lab',entity_uid:uid,display_name:'真实表 '+uid});
const rawPage=uid=>({schema_version:'personal-table-page-v1',source_id:'private-lab',entity_uid:uid,title:'真实表 '+uid,sheet_name:'数据',columns:[{name:'dose',role:'independent',meaning:'剂量',unit:'dpa'},{name:'hardness',role:'dependent',meaning:'硬度',unit:'GPa'},{name:'error',role:'uncertainty',meaning:'误差',unit:'GPa'}],conditions:{温度:'300 K'},series:[{name:'硬度随剂量',x_column:'dose',y_column:'hardness',uncertainty_column:'error'}],page:1,page_size:50,total:123,has_next:true,rows:[{dose:'0',hardness:'3.200',error:'0.050'}]});
function openTable(api,uid,{loaded=true}={}){const row=api.publicEvidence(rawRow(uid)),page=api.publicPersonalTablePage(rawPage(uid),api.privateTableIdentity(row),1);return api.documentTabs.open({tabId:'personal-table:'+uid,kind:'personal-table',ownerView:'personal',title:'真实表 '+uid,identity:identity(uid),...(loaded?{payload:{row,page,status:'ready'}}:{})},{groupId:'primary',pin:true})}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
"""


class FusionPersonalSeriesRuntimeTests(unittest.TestCase):
    def run_node(self, body: str) -> None:
        script = "const fs=require('fs'),assert=require('assert');\n"
        script += f"const WEB={str(WEB)!r};\n"
        script += "eval(fs.readFileSync(WEB+'/fusion_personal_series.js','utf8'));\n"
        script += FIXTURE + body
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_primary_loaded_page_returns_without_search_and_preserves_curve(self) -> None:
        self.run_node(RESTORE_FIXTURE + r"""
const api=boot(),tabs=api.documentTabs,a=openTable(api,'table-a');
api.state.searchResults=[];api.state.evidence=[];
const plot=globalThis.AutoResearchPersonalSeries.validate(seriesDTO(),expected);
tabs.update(a.tabId,{payload:{...tabs.activeTab().payload,personalPlot:{mode:'curve',status:'ready',seriesIndex:0,generation:1,plot}}});
tabs.rememberPresentation(a.tabId,{scrollTop:318});ids['#fusion-primary-editor-surface'].scrollTop=318;assert(api.activateDocumentTab(a.tabId));
assert.equal(ids['#fusion-evidence-detail'].hidden,false);assert(ids['#fusion-evidence-detail-body'].innerHTML.includes('完整原表 123 行'));
assert.equal(ids['#fusion-primary-editor-surface'].scrollTop,318);
const b=openTable(api,'table-b');api.activateDocumentTab(b.tabId);assert.equal(ids['#fusion-detail-heading'].textContent,'真实表 table-b');
api.switchView('search',{focus:false});assert.equal(ids['#fusion-evidence-detail'].hidden,true);
api.activateDocumentTab(a.tabId);assert.equal(api.state.view,'personal');assert.equal(ids['#fusion-detail-heading'].textContent,'真实表 table-a');
assert.equal(ids['#fusion-evidence-detail'].hidden,false);assert(ids['#fusion-evidence-detail-body'].innerHTML.includes('完整原表 123 行'));
assert.equal(ids['#fusion-primary-editor-surface'].scrollTop,318);assert.equal(calls.length,0,'reactivation must not depend on any search or refetch');
const panel=ids['#fusion-evidence-detail-body'].seriesPanel;assert(panel);for(const event of ['click','change','focusin','keydown'])assert.equal(panel.listeners[event].length,1,'primary controls bind after returning');
assert(!memory.get('auto-research-workspace-layout-v1').includes('personalPlot'));assert.equal(focusCalls,0);
""")

    def test_primary_restart_hydration_projects_arriving_page_and_binds_controls(self) -> None:
        self.run_node(RESTORE_FIXTURE + r"""
(async()=>{
let api=boot();const saved=openTable(api,'table-a');assert(!memory.get('auto-research-workspace-layout-v1').includes('3.200'));
api=boot();const tabs=api.documentTabs,restored=tabs.activeTab();assert.equal(restored.tabId,saved.tabId);assert.equal(restored.payload,undefined);
api.activateDocumentTab(restored.tabId);assert.equal(ids['#fusion-evidence-detail'].hidden,false);assert.equal(ids['#fusion-evidence-detail-body'].attrs['aria-busy'],'true');
assert.equal(calls.length,1);assert(calls[0][0].startsWith('/api/desktop/federated-evidence?'));
pending.shift()(rawRow('table-a'));await tick();assert.equal(calls.length,2);assert.equal(calls[1][0],'/api/desktop/personal-experiments/table?source_id=private-lab&entity_uid=table-a&page=1&page_size=50');
pending.shift()(rawPage('table-a'));await tick();
assert.equal(tabs.activeTab().payload.status,'ready');assert.equal(ids['#fusion-evidence-detail-body'].attrs['aria-busy'],'false');assert.equal(ids['#fusion-detail-heading'].textContent,'真实表 table-a');
assert(ids['#fusion-evidence-detail-body'].innerHTML.includes('3.200'));assert(ids['#fusion-evidence-detail-body'].innerHTML.includes('data-personal-view="curve"'));assert.equal(ids['#fusion-evidence-detail-body'].seriesPanel.listeners.click.length,1);
assert.equal(api.state.searchResults.length,0);assert.equal(api.state.evidence.length,0);assert.equal(focusCalls,0);
assert(!memory.get('auto-research-workspace-layout-v1').includes('3.200'));
})().catch(error=>{console.error(error);process.exitCode=1});
""")

    def test_primary_late_hydration_updates_original_tab_without_projection_theft(self) -> None:
        self.run_node(RESTORE_FIXTURE + r"""
(async()=>{
const api=boot(),tabs=api.documentTabs,a=openTable(api,'table-a',{loaded:false});
api.activateDocumentTab(a.tabId);pending.shift()(rawRow('table-a'));await tick();assert.equal(pending.length,1);
const b=openTable(api,'table-b');api.activateDocumentTab(b.tabId);const body=ids['#fusion-evidence-detail-body'].innerHTML,focusBefore=focusCalls;
pending.shift()(rawPage('table-a'));await tick();
assert.equal(tabs.activeTab().tabId,b.tabId);assert.equal(tabs.snapshot().tabs.find(t=>t.tabId===a.tabId).payload.page.title,'真实表 table-a');
assert.equal(ids['#fusion-evidence-detail-body'].innerHTML,body);assert.equal(ids['#fusion-detail-heading'].textContent,'真实表 table-b');assert.equal(focusCalls,focusBefore);
api.activateDocumentTab(a.tabId);assert.equal(ids['#fusion-detail-heading'].textContent,'真实表 table-a');assert.equal(calls.length,2);
const c=openTable(api,'table-c',{loaded:false});api.activateDocumentTab(c.tabId);pending.shift()(rawRow('table-c'));await tick();
api.switchView('search',{focus:false});pending.shift()(rawPage('table-c'));await tick();assert.equal(api.state.view,'search');assert.equal(ids['#fusion-evidence-detail'].hidden,true,'background hydration cannot reopen a detail in another module');
})().catch(error=>{console.error(error);process.exitCode=1});
""")

    def test_complete_series_strict_projection_gaps_error_bars_and_source_values(self) -> None:
        self.run_node(r"""
const renderer=globalThis.AutoResearchPersonalSeries;
assert.equal(renderer.schemaVersion,'personal-series-plot-v1');
const plot=renderer.validate(seriesDTO(),expected);assert(plot);assert.equal(plot.points.length,123);
assert.equal(plot.points[0].x,0);assert.equal(plot.points[49].y,null);assert.equal(plot.points[70].status,'invalid');
const geometry=renderer.geometry(plot);assert.deepEqual(geometry.segments.map(segment=>[segment[0].row,segment.at(-1).row]),[[1,49],[51,70],[72,123]],'gaps split original row order, without sorting or bridging');
const html=renderer.render(plot);assert.equal((html.match(/<polyline /g)||[]).length,3);assert.equal((html.match(/data-series-point=/g)||[]).length,121);
assert(!html.includes('data-series-error-row="6"'));assert(!html.includes('data-series-error-row="8"'));assert(html.includes('data-series-error-row="123"'));
for(const value of ['完整原表 123 行','有效 121','缺失 1','非法 1','0.050','3.200','data-series-source-row="123"','dpa','GPa','tabindex="0"','role="button"'])assert(html.includes(value),value);
assert(renderer.sourceText(plot,plot.points[0]).includes('3.200'));assert(!html.includes('NaN,'),'invalid cells must not become SVG coordinates');
const reject=mutate=>{const raw=seriesDTO();mutate(raw);assert.equal(renderer.validate(raw,expected),null)};
reject(raw=>raw.path='/private/secret');reject(raw=>raw.points[0].key='secret');reject(raw=>raw.source_id='another-source');reject(raw=>raw.entity_uid='another-table');reject(raw=>raw.series_index=1);
reject(raw=>raw.total_rows=124);reject(raw=>raw.points=raw.points.slice(0,50));reject(raw=>raw.total_rows=true);reject(raw=>raw.valid_points=122);reject(raw=>raw.points[1].row=1);
reject(raw=>raw.points[0].x=NaN);reject(raw=>raw.points[0].x=Infinity);reject(raw=>raw.points[0].x=true);reject(raw=>raw.points[0].x_text='1e-999');reject(raw=>raw.points[0].x_text='1000');
reject(raw=>raw.points[0].uncertainty=-.1);reject(raw=>raw.uncertainty_unit='MPa');reject(raw=>raw.points[49].y=0);
reject(raw=>{raw.total_rows=5001;raw.points=Array(5001).fill(raw.points[0])});
const empty=seriesDTO();empty.points=[];empty.total_rows=0;for(const key of ['valid_points','missing_rows','invalid_rows','uncertainty_missing_rows','uncertainty_invalid_rows'])empty[key]=0;assert(renderer.render(renderer.validate(empty,expected)).includes('该系列没有数据行'));
const invalid=seriesDTO();invalid.points=invalid.points.map((p,i)=>({...p,row:i+1,x:null,x_text:'bad',y:null,y_text:'bad',status:'invalid'}));invalid.valid_points=0;invalid.missing_rows=0;invalid.invalid_rows=123;const invalidPlot=renderer.validate(invalid,expected);assert(invalidPlot);assert(renderer.render(invalidPlot).includes('没有可绘制'));assert(!renderer.render(invalidPlot).includes('<svg'));
const unsafe=seriesDTO();unsafe.name='<img src=x onerror=alert(1)>';const safeHTML=renderer.render(renderer.validate(unsafe,expected));assert(!safeHTML.includes('<img'));assert(safeHTML.includes('&lt;img'));
""")

    def test_fusion_tab_series_generation_pagination_and_failure_states(self) -> None:
        self.run_node(r"""
(async()=>{
class Classes{toggle(){}add(){}remove(){}contains(){return false}}
class El{constructor(){this.dataset={};this.hidden=false;this.innerHTML='';this.textContent='';this.classList=new Classes();this.listeners={};this.attrs={}}setAttribute(k,v){this.attrs[k]=String(v)}removeAttribute(k){delete this.attrs[k]}querySelector(){return null}querySelectorAll(){return []}addEventListener(k,fn){(this.listeners[k]??=[]).push(fn)}focus(){throw Error('no focus stealing')}}
const ids={};for(const id of ['fusion-primary-editor-surface','fusion-secondary-editor-surface','fusion-inspector-title','fusion-inspector-body','fusion-secondary-editor-body'])ids['#'+id]=new El();
globalThis.document={readyState:'loading',documentElement:{dataset:{},style:{setProperty(){}}},body:{dataset:{}},querySelector:s=>ids[s]||null,querySelectorAll:()=>[],addEventListener(){}};
const stored=[];globalThis.localStorage={getItem:()=>null,setItem:(key,value)=>stored.push(value)};globalThis.innerWidth=1280;
for(const file of ['document_tab_store.js','pane_layout_controller.js','workspace_layout_controller.js','fusion_review.js'])eval(fs.readFileSync(WEB+'/'+file,'utf8'));
const api=globalThis.AutoResearchFusion,tabs=api.documentTabs;api.state.view='search';
const raw=seriesDTO(),makePage=uid=>({schemaVersion:'personal-table-page-v1',sourceId:'private-lab',entityUid:uid,title:uid,sheetName:'数据',columns:[{name:'dose',meaning:'剂量',unit:'dpa'},{name:'hardness',meaning:'硬度',unit:'GPa'},{name:'error',meaning:'误差',unit:'GPa'}],conditions:[],series:[{name:'硬度随剂量',xColumn:'dose',yColumn:'hardness',uncertaintyColumn:'error'},{name:'另一序列',xColumn:'dose',yColumn:'hardness',uncertaintyColumn:'error'}],page:1,pageSize:50,total:123,hasNext:true,rows:[{dose:'0',hardness:'3.200',error:'0.050'}]}),row=uid=>({type:'table',sourceScope:'private',sourceId:'private-lab',entityUid:uid,title:uid});
const open=(uid,groupId)=>tabs.open({tabId:'personal-table:'+uid,kind:'personal-table',ownerView:'search',title:uid,identity:{sourceScope:'private',sourceId:'private-lab',entityType:'table',entityUid:uid},payload:{row:row(uid),page:makePage(uid),status:'ready'}},{groupId,pin:true});
const a=open('table-a','primary'),b=open('table-b','secondary'),pending=[],calls=[];
globalThis.fetch=(url,options={})=>{calls.push([String(url),options]);return new Promise(resolve=>pending.push({url:String(url),resolve:payload=>resolve({ok:true,headers:{get:()=>null},json:async()=>payload}),reject:code=>resolve({ok:false,status:413,headers:{get:()=>null},json:async()=>({code,message:'do not leak /private/path'})})}))};
const first=api.setPersonalSeriesView(a.tabId,'curve',0);assert.equal(calls.length,1);assert.equal(calls[0][0],'/api/desktop/personal-experiments/series?source_id=private-lab&entity_uid=table-a&series_index=0');assert(!calls[0][0].includes('page='));
const newer=api.setPersonalSeriesView(a.tabId,'curve',1);assert.equal(calls.length,2);pending[1].resolve(seriesDTO('table-a',1));assert(await newer);pending[0].resolve(raw);assert.equal(await first,false);let tab=tabs.snapshot().tabs.find(t=>t.tabId===a.tabId);assert.equal(tab.payload.personalPlot.seriesIndex,1);assert.equal(tab.payload.personalPlot.plot.points.length,123);assert.equal(tabs.activeTab().tabId,b.tabId,'background completion stays in original tab');
const oldPlot=tab.payload.personalPlot;tabs.update(a.tabId,{payload:{...tab.payload,page:{...tab.payload.page,page:2,rows:[{dose:'50',hardness:'3.200',error:'0.050'}]}}});await api.setPersonalSeriesView(a.tabId,'data');await api.setPersonalSeriesView(a.tabId,'curve',1);tab=tabs.snapshot().tabs.find(t=>t.tabId===a.tabId);assert.strictEqual(tab.payload.personalPlot.plot,oldPlot.plot,'paging and data toggle preserve the complete series');assert.equal(calls.length,2,'cached complete series must not be fetched once per page');
for(const group of ['primary','secondary']){const html=api.secondaryDocumentHTML(tab,group);assert(html.includes('数据'));assert(html.includes('曲线'));assert(html.includes('完整原表 123 行'));assert(html.includes('data-personal-data hidden'));}
const over=api.setPersonalSeriesView(b.tabId,'curve',0);pending[2].reject('personal_series_too_large');assert.equal(await over,false);let failed=tabs.activeTab();assert.equal(failed.payload.personalPlot.status,'error');let html=api.secondaryDocumentHTML(failed);assert(html.includes('超过5000行'));assert(!html.includes('/private/path'));assert(!html.includes('data-personal-series-retry'));await api.setPersonalSeriesView(b.tabId,'data');assert(api.secondaryDocumentHTML(tabs.activeTab()).includes('data-personal-data>'));
const before=calls.length;assert.equal(await api.setPersonalSeriesView(b.tabId,'curve',200),false);assert.equal(calls.length,before);const missing=globalThis.AutoResearchPersonalSeries;delete globalThis.AutoResearchPersonalSeries;assert.equal(await api.setPersonalSeriesView(b.tabId,'curve',0,{retry:true}),false);assert.equal(calls.length,before);globalThis.AutoResearchPersonalSeries=missing;
const moved=api.setPersonalSeriesView(b.tabId,'curve',0,{retry:true});tabs.move(b.tabId,'primary');pending[3].resolve(seriesDTO('table-b'));assert.equal(await moved,false);assert.equal(tabs.snapshot().tabs.find(t=>t.tabId===b.tabId).payload.personalPlot.status,'error','moved pending tab terminates without applying an old group result');
const closed=api.setPersonalSeriesView(b.tabId,'curve',0,{retry:true});tabs.close(b.tabId);pending[4].resolve(seriesDTO('table-b'));assert.equal(await closed,false);assert(!tabs.snapshot().tabs.some(t=>t.tabId===b.tabId));
const original=open('reused-table','secondary'),oldRequest=api.setPersonalSeriesView(original.tabId,'curve',0),oldPending=pending.at(-1),oldGeneration=tabs.activeTab().payload.personalPlot.generation;
tabs.close(original.tabId);const reopened=open('reused-table','secondary');assert.equal(reopened.tabId,original.tabId,'same public identity recreates the deterministic tab ID');
const newRequest=api.setPersonalSeriesView(reopened.tabId,'curve',0),newPending=pending.at(-1);assert(tabs.activeTab().payload.personalPlot.generation>oldGeneration,'request nonce is not reset by a new tab lifetime');
newPending.resolve(seriesDTO('reused-table'));assert(await newRequest);const accepted=tabs.activeTab().payload.personalPlot.plot;
const stale=seriesDTO('reused-table');stale.points[0].y=9.9;stale.points[0].y_text='9.900';oldPending.resolve(stale);assert.equal(await oldRequest,false,'old lifetime response cannot match the reopened tab request');
assert.strictEqual(tabs.activeTab().payload.personalPlot.plot,accepted);assert.equal(accepted.points[0].y_text,'3.200');assert.equal(tabs.activeTab().payload.personalPlot.status,'ready');
assert(stored.every(value=>!value.includes('3.200')&&!value.includes('personalPlot')&&!value.includes('x_text')),'only tab identity is persisted');
})().catch(error=>{console.error(error);process.exitCode=1});
""")

    def test_point_keyboard_source_selection_and_single_binding(self) -> None:
        self.run_node(r"""
class Node{constructor(dataset={}){this.dataset=dataset;this.listeners={};this.attrs={};this.textContent=''}addEventListener(k,fn){(this.listeners[k]??=[]).push(fn)}setAttribute(k,v){this.attrs[k]=v}focus(){globalThis.focused=this}querySelector(){return null}querySelectorAll(){return []}}
const panel=new Node({personalSeriesTab:'personal-table:points'}),output=new Node(),points=[1,2,49,51,123].map(row=>new Node({seriesPoint:String(row)}));panel.querySelector=s=>s==='[data-series-selection]'?output:null;panel.querySelectorAll=s=>s==='[data-series-point]'?points:[];
globalThis.document={readyState:'loading',querySelector:()=>null,querySelectorAll:s=>s==='[data-personal-series-tab]'?[panel]:[],addEventListener(){}};globalThis.localStorage={getItem:()=>null,setItem(){}};
eval(fs.readFileSync(WEB+'/document_tab_store.js','utf8'));eval(fs.readFileSync(WEB+'/fusion_review.js','utf8'));
const api=globalThis.AutoResearchFusion,plot=globalThis.AutoResearchPersonalSeries.validate(seriesDTO(),expected);api.documentTabs.open({tabId:'personal-table:points',kind:'personal-table',ownerView:'search',title:'曲线',identity:{sourceScope:'private',sourceId:'private-lab',entityType:'table',entityUid:'table-a'},payload:{row:{type:'table',sourceScope:'private',sourceId:'private-lab',entityUid:'table-a'},personalPlot:{mode:'curve',status:'ready',seriesIndex:0,generation:1,plot}}},{pin:true});
api.bindPersonalSeriesViews();api.bindPersonalSeriesViews();for(const kind of ['click','change','focusin','keydown'])assert.equal(panel.listeners[kind].length,1);
panel.listeners.focusin[0]({target:points[0]});assert(output.textContent.includes('原始第 1 行'));assert(output.textContent.includes('3.200'));assert(output.textContent.includes('0.050'));
let prevented=false;panel.listeners.keydown[0]({target:points[2],key:'ArrowRight',preventDefault(){prevented=true}});assert(prevented);assert.equal(globalThis.focused,points[3],'keyboard skips the missing row without drawing across it');assert.equal(points[3].attrs.tabindex,'0');assert(output.textContent.includes('原始第 51 行'));
panel.listeners.keydown[0]({target:points[3],key:'End',preventDefault(){}});assert.equal(globalThis.focused,points[4]);
const source=new Node({seriesSourceRow:'50'});source.closest=()=>source;source.hasAttribute=()=>false;panel.listeners.click[0]({target:source});assert(output.textContent.includes('原始第 50 行'));assert(output.textContent.includes('缺失'));assert.equal(api.documentTabs.activeTab().payload.personalPlot.selectedRow,50);
""")

    def test_renderer_is_passive_and_styles_use_existing_theme_tokens(self) -> None:
        source = (WEB / "fusion_personal_series.js").read_text(encoding="utf-8")
        fusion = (WEB / "fusion_review.js").read_text(encoding="utf-8")
        css = (WEB / "workbench.css").read_text(encoding="utf-8")
        for forbidden in ("fetch(", "localStorage", "DOMContentLoaded", "appendChild", "http://", "https://"):
            self.assertNotIn(forbidden, source)
        self.assertIn('personalSeries:"/api/desktop/personal-experiments/series"', fusion)
        self.assertIn('ROUTES.personalTable,ROUTES.personalSeries,', fusion)
        self.assertIn('data-series-source-row=', source)
        self.assertIn('data-series-point=', source)
        self.assertIn('"focusin"', fusion)
        self.assertIn('"Home","End"', fusion)
        self.assertIn('panel.dataset.seriesBound==="true"', fusion)
        self.assertIn('.fusion-series-point:focus-visible', css)
        series_css = "\n".join(line for line in css.splitlines() if line.startswith('.fusion-series') or line.startswith('.fusion-personal-series-'))
        self.assertNotRegex(series_css, r"#[0-9a-fA-F]{3,8}\b")
        self.assertIn('var(--f-accent)', series_css)
