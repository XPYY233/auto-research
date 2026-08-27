from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "auto_research" / "evidence" / "web"


class FusionTabHydrationRuntimeTests(unittest.TestCase):
    def test_secondary_tab_mounts_remain_stable_across_activation(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert');
class Mount{{constructor(){{this.dataset={{}};this.hidden=false;this.scrollTop=0;this.innerHTML='';this.className='';this.tabIndex=0;this.parent=null;}}remove(){{if(this.parent)this.parent.children=this.parent.children.filter(value=>value!==this)}}querySelector(){{return null}}}}
class Host{{constructor(){{this.children=[];this.innerHTML=''}}append(node){{node.parent=this;this.children.push(node)}}querySelectorAll(selector){{return selector==='[data-document-mount]'?this.children:[]}}querySelector(selector){{return selector==='[data-document-mount]:not([hidden])'?this.children.find(node=>!node.hidden)||null:null}}}}
const host=new Host(),memory=new Map();
globalThis.localStorage={{getItem:key=>memory.get(key)||null,setItem:(key,value)=>memory.set(key,value)}};
globalThis.document={{readyState:'loading',activeElement:null,documentElement:{{style:{{setProperty:()=>{{}}}},dataset:{{}}}},body:{{dataset:{{}}}},querySelector:selector=>selector==='#fusion-secondary-editor-body'?host:null,querySelectorAll:()=>[],addEventListener:()=>{{}},createElement:()=>new Mount()}};
globalThis.addEventListener=()=>{{}};globalThis.innerWidth=1440;
eval(fs.readFileSync({str(WEB / 'document_tab_store.js')!r},'utf8'));
eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));
const api=globalThis.AutoResearchFusion,tabs=api.documentTabs;
const row=(id,title)=>({{type:'finding',title,findingText:title,sourceScope:'workspace',itemId:id,paperId:1,page:1,quantities:[],variables:{{}},materials:[],tags:[]}});
const first=tabs.open({{tabId:'evidence:first',kind:'evidence',ownerView:'search',title:'第一条',identity:{{sourceScope:'workspace',entityType:'finding',entityUid:'1',paperId:'1'}},payload:{{row:row(1,'第一条'),status:'ready'}}}},{{groupId:'secondary',pin:true}});
const second=tabs.open({{tabId:'evidence:second',kind:'evidence',ownerView:'search',title:'第二条',identity:{{sourceScope:'workspace',entityType:'finding',entityUid:'2',paperId:'1'}},payload:{{row:row(2,'第二条'),status:'ready'}}}},{{groupId:'secondary',pin:true}});
tabs.activate(first.tabId);const firstMount=api.renderStableTabMount('secondary',tabs.activeTab('secondary'));firstMount.scrollTop=418;tabs.rememberPresentation(first.tabId,{{scrollTop:418}});
tabs.activate(second.tabId);const secondMount=api.renderStableTabMount('secondary',tabs.activeTab('secondary'));
assert.notEqual(firstMount,secondMount);assert.equal(host.children.length,2);assert.equal(firstMount.hidden,true);assert.equal(secondMount.hidden,false);
tabs.activate(first.tabId);const restored=api.renderStableTabMount('secondary',tabs.activeTab('secondary'));
assert.equal(restored,firstMount);assert.equal(restored.hidden,false);assert.equal(secondMount.hidden,true);assert.equal(restored.scrollTop,418);
tabs.close(second.tabId);api.renderStableTabMount('secondary',tabs.activeTab('secondary'));assert.equal(host.children.length,1);assert.equal(host.children[0],firstMount);
"""
        result = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, check=False, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_only_visible_active_tabs_hydrate_and_group_move_cannot_receive_stale_result(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert');
const layout={{
  schema_version:'workspace-layout-v2',active_group_id:'secondary',
  groups:[{{id:'primary',activeTabId:'paper:paperId=1'}},{{id:'secondary',activeTabId:'paper:paperId=2'}}],
  tabs:[
    {{tabId:'paper:paperId=1',kind:'paper',ownerView:'paper',title:'论文一',identity:{{paperId:'1'}},groupId:'primary',pinned:true}},
    {{tabId:'paper:paperId=2',kind:'paper',ownerView:'paper',title:'论文二',identity:{{paperId:'2'}},groupId:'secondary',pinned:true}},
    {{tabId:'paper:paperId=3',kind:'paper',ownerView:'paper',title:'论文三',identity:{{paperId:'3'}},groupId:'primary',pinned:true}}
  ]
}};
const memory=new Map([['auto-research-workspace-layout-v1',JSON.stringify(layout)]]);
globalThis.localStorage={{getItem:key=>memory.get(key)||null,setItem:(key,value)=>memory.set(key,value)}};
globalThis.document={{
  readyState:'loading',activeElement:null,
  documentElement:{{style:{{setProperty:()=>{{}}}},dataset:{{}}}},body:{{dataset:{{}}}},
  querySelector:()=>null,querySelectorAll:()=>[],addEventListener:()=>{{}},createElement:()=>({{}})
}};
globalThis.innerWidth=1440;globalThis.addEventListener=()=>{{}};
const requested=[];let deferredResolve=null;
globalThis.fetch=async url=>{{
  requested.push(String(url));
  if(String(url).includes('paper_ids=3'))await new Promise(resolve=>{{deferredResolve=resolve}});
  return {{ok:true,headers:{{get:()=>null}},json:async()=>({{rows:[],total:0}})}};
}};
eval(fs.readFileSync({str(WEB / 'document_tab_store.js')!r},'utf8'));
eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));
const api=globalThis.AutoResearchFusion,tabs=api.documentTabs;
api.state.papers=[
  {{id:1,title:'论文一',firstAuthor:'甲',doi:'',year:2024,material:'',count:0,status:'只读'}},
  {{id:2,title:'论文二',firstAuthor:'乙',doi:'',year:2024,material:'',count:0,status:'只读'}},
  {{id:3,title:'论文三',firstAuthor:'丙',doi:'',year:2024,material:'',count:0,status:'只读'}}
];
(async()=>{{
  const count=await api.hydrateActiveRestoredTabs();
  assert.equal(count,2);assert.equal(requested.length,2);
  assert.equal(tabs.snapshot().tabs.find(tab=>tab.identity.paperId==='1').payload.status,'ready');
  assert.equal(tabs.snapshot().tabs.find(tab=>tab.identity.paperId==='2').payload.status,'ready');
  assert.equal(tabs.snapshot().tabs.find(tab=>tab.identity.paperId==='3').payload,undefined);
  const third=tabs.snapshot().tabs.find(tab=>tab.identity.paperId==='3');
  const pending=api.hydrateDocumentTab(third);
  await new Promise(resolve=>setImmediate(resolve));assert(deferredResolve);
  assert(tabs.move(third.tabId,'secondary'));const activeBefore=tabs.activeTab().tabId;
  deferredResolve();assert.equal(await pending,false);
  const moved=tabs.snapshot().tabs.find(tab=>tab.tabId===third.tabId);
  assert.equal(moved.payload.hydration,'retry');assert.equal(tabs.activeTab().tabId,activeBefore);
  const official=tabs.open({{tabId:'package-job:official',kind:'package-job',ownerView:'package',title:'官方导入',identity:{{operation:'official',jobId:'package_job_12345678'}}}},{{activate:false,pin:true}});
  const transfer=tabs.open({{tabId:'package-job:transfer',kind:'package-job',ownerView:'package',title:'用户包导入',identity:{{operation:'transfer',jobId:'transfer_job_12345678'}}}},{{activate:false,pin:true}});
  assert.equal(await api.hydrateDocumentTab(official),true);assert.equal(await api.hydrateDocumentTab(transfer),true);
  assert(requested.some(url=>url==='/api/desktop/evidence-package-jobs/package_job_12345678'));
  assert(requested.some(url=>url==='/api/desktop/package-center/jobs/transfer_job_12345678'));
  assert.equal(memory.get('auto-research-workspace-layout-v1').includes('hydration'),false);
  assert.equal(memory.get('auto-research-workspace-layout-v1').includes('payload'),false);
}})().catch(error=>{{console.error(error);process.exitCode=1}});
"""
        result = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, check=False, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_declares_lazy_identity_only_restore_contract(self) -> None:
        runtime = (WEB / "fusion_review.js").read_text(encoding="utf-8")
        store = (WEB / "document_tab_store.js").read_text(encoding="utf-8")
        self.assertIn("function visibleActiveTabsForHydration()", runtime)
        self.assertIn("function hydrateActiveRestoredTabs()", runtime)
        self.assertIn('tab.payload===undefined||tab.payload?.hydration==="retry"', runtime)
        self.assertNotIn("hydrateRestoredTabs", runtime)
        self.assertIn("preserveGroup=false", runtime)
        self.assertIn("initialView=restoredActive?.ownerView||\"paper\"", runtime)
        self.assertIn(
            "const restorationPrerequisites=Promise.all([loadLiterature().then(async()=>{await loadLiteratureTaskDirectory();return reconnectLiteratureJob();}),loadLibrarianHistory(),loadEvidenceChatHistory()])",
            runtime,
        )
        self.assertIn(".then(()=>hydrateActiveRestoredTabs())", runtime)
        self.assertIn("tabs: this.tabs.map(({ tabId, kind, ownerView, title, identity, groupId, pinned })", store)
        self.assertNotIn("scrollTop, focusToken", store[store.index("persist()") : store.index("snapshot()")])


if __name__ == "__main__":
    unittest.main()
