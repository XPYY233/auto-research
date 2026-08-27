from __future__ import annotations

import subprocess
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "src/auto_research/evidence/web/fusion_personal_import.js"


class FusionPersonalImportControllerRuntimeTests(unittest.TestCase):
    def test_controller_has_no_autonomous_authority_and_binds_once(self) -> None:
        source = RUNTIME.read_text(encoding="utf-8")
        for forbidden in (
            "fetch(", "DOMContentLoaded", "localStorage", "DocumentTabStore",
            "switchView(", "preparedAuthorization(", "executePrepared(",
        ):
            self.assertNotIn(forbidden, source)
        state_fields = set(re.findall(r"state\.([A-Za-z_$][A-Za-z0-9_$]*)", source))
        self.assertTrue(state_fields)
        self.assertTrue(all(name.startswith("personal") for name in state_fields), state_fields)

        program = f"""
const fs=require('fs'),assert=require('assert');
class El{{constructor(value=''){{this.value=value;this.textContent='';this.dataset={{}};this.disabled=false;this.listeners={{}};}}addEventListener(name,fn){{(this.listeners[name]??=[]).push(fn)}}querySelector(){{return null}}querySelectorAll(){{return[]}}}}
const ids={{}};for(const id of ['fusion-select-data-file','fusion-select-data-file-empty','fusion-personal-ai','fusion-personal-confirm','fusion-personal-series-add','fusion-run-conditions','fusion-run-note','fusion-personal-sheet','fusion-personal-page-prev','fusion-personal-page-next','fusion-personal-status','fusion-project-name','fusion-sample-name','fusion-sample-material','fusion-run-name','fusion-run-method'])ids['#'+id]=new El();
ids['#fusion-project-name'].value='项目';ids['#fusion-sample-name'].value='样品';ids['#fusion-run-name'].value='批次';ids['#fusion-run-method'].value='nanoindentation';ids['#fusion-run-conditions'].value='温度=300 K';
const role={{value:'independent'}},meaning={{value:'剂量'}},unit={{value:'dpa'}},column={{dataset:{{sourceName:'Dose'}},querySelector:s=>s.includes('role')?role:s.includes('meaning')?meaning:unit}};
const state={{personalAction:0,personalPageRequest:0,personalPreview:{{sheets:[{{sheet_name:'Sheet1',columns:[{{source_name:'Dose'}}]}}]}},personalPreviewPage:null,personalSeriesCounter:0,personalSheetIndex:0,personalStatus:{{revision:1}},personalSuggestion:null}};
let aiCalls=0;const safeError=(code,message)=>Object.assign(new Error(message),{{code}});
eval(fs.readFileSync({str(RUNTIME)!r},'utf8'));
const controller=globalThis.AutoResearchFusionPersonalImport.createPersonalImportController({{
 state,q:s=>ids[s]||null,qa:s=>s==='[data-personal-column]'?[column]:[],esc:String,cleanText:v=>String(v||''),safeError,
 request:async()=>{{throw new Error('network forbidden')}},native:{{selectPersonalFile:async()=>null}},authorizeAI:async()=>{{aiCalls+=1}},executeAI:async()=>{{aiCalls+=1}},
 aiProgress:()=>{{}},aiErrorCopy:()=>'',resetAI:()=>{{}},isPersonalActive:()=>true,setOperation:()=>{{}},selectCell:()=>true,projectInspector:()=>{{}},openImportedTable:async()=>false,confirmAction:()=>false,
}});
assert.equal(controller.bind(),true);assert.equal(controller.bind(),false);assert.equal(ids['#fusion-select-data-file'].listeners.click.length,1);assert.equal(ids['#fusion-personal-confirm'].listeners.click.length,1);
const draft=controller.collectPersonalDraft();assert.equal(draft.sheet_index,0);assert.deepEqual(draft.run.conditions,{{'温度':'300 K'}});assert.equal(aiCalls,0,'manual review must not depend on AI');assert(!JSON.stringify(draft).match(/path|api_key|sha256/i));
const valid={{entity_type:'table',entity_uid:'private:table:one',kind:'open_personal_table',label:'打开刚导入的表格',source_id:'private-lab',source_scope:'private'}};
assert(controller.publicPersonalImportNextAction(valid));assert.equal(controller.publicPersonalImportNextAction({{...valid,path:'/secret'}}),null);assert.equal(controller.publicPersonalImportNextAction({{...valid,source_scope:'workspace'}}),null);
"""
        result = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, timeout=8, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_search_recovery_is_visible_idempotent_and_package_independent(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert');
class El{{constructor(){{this.value='';this.textContent='';this.dataset={{}};this.disabled=false;this.hidden=false;this.listeners={{}};}}addEventListener(name,fn){{(this.listeners[name]??=[]).push(fn)}}querySelector(){{return null}}querySelectorAll(){{return[]}}}}
const nodes=new Map(),q=selector=>{{if(!nodes.has(selector))nodes.set(selector,new El());return nodes.get(selector)}};
const status=(state,ready,count)=>({{schema_version:'personal-search-readiness-v1',state,ready,document_count:count}});
let calls=[],refreshAttempts=0,confirmCalls=0,aiCalls=0;
globalThis.AutoResearchFusionPackage={{createPackageCenterController(){{throw new Error('package center unavailable')}}}};
try{{globalThis.AutoResearchFusionPackage.createPackageCenterController()}}catch(_error){{}}
eval(fs.readFileSync({str(RUNTIME)!r},'utf8'));
const state={{personalAction:0,personalPageRequest:0,personalSearchRequest:0,personalSearchStatus:null,personalSearchRefreshing:false,personalPreview:null,personalPreviewPage:null,personalSeriesCounter:0,personalSheetIndex:0,personalStatus:null,personalSuggestion:null}};
const safeError=(code,message)=>Object.assign(new Error(message),{{code}});
const request=async(url,options={{}})=>{{
 calls.push([url,options.method||'GET',options.body]);
 if(url==='/api/desktop/personal-imports/search-status')return status('retry_required',false,0);
 if(url==='/api/desktop/personal-imports/search-refresh'){{refreshAttempts+=1;if(refreshAttempts===1)throw safeError('personal_search_refresh_failed','retry');return status('ready',true,3)}}
 throw new Error('unexpected '+url);
}};
const controller=globalThis.AutoResearchFusionPersonalImport.createPersonalImportController({{
 state,q,qa:()=>[],esc:String,cleanText:v=>String(v??''),safeError,request,native:{{selectPersonalFile:async()=>null}},
 authorizeAI:async()=>{{aiCalls+=1}},executeAI:async()=>{{aiCalls+=1}},aiProgress:()=>{{}},aiErrorCopy:()=>'',resetAI:()=>{{}},isPersonalActive:()=>true,setOperation:()=>{{}},selectCell:()=>true,projectInspector:()=>{{}},openImportedTable:async()=>false,confirmAction:()=>{{confirmCalls+=1;return true}},
}});
assert.equal(controller.bind(),true);
(async()=>{{
 state.personalSearchStatus=controller.publicPersonalSearchStatus({{schema_version:'personal-search-readiness-v1',state:'not_checked',ready:false,document_count:0}});assert(state.personalSearchStatus);controller.renderPersonalSearchStatus();assert.equal(q('#fusion-personal-search-recovery').hidden,true,'not_checked is valid but must not invent a recovery failure');
 assert.equal(controller.publicPersonalSearchStatus({{schema_version:'personal-search-readiness-v1',state:'ready',ready:true,document_count:3,path:'/private/index.sqlite'}}),null);
 assert.equal(controller.publicPersonalSearchStatus({{schema_version:'personal-search-readiness-v1',state:'empty',ready:false,document_count:false}}),null);
 assert.equal(await controller.loadPersonalSearchStatus(),true);
 assert.equal(state.personalSearchStatus.state,'retry_required');
 assert.equal(q('#fusion-personal-search-recovery').hidden,false);
 assert.equal(q('#fusion-personal-search-refresh').disabled,false);
 assert.equal(await controller.refreshPersonalSearch(),false);
 assert.equal(state.personalSearchStatus.state,'retry_required');
 assert.equal(q('#fusion-personal-search-recovery').hidden,false,'failed refresh keeps the recovery action visible');
 assert.equal(await controller.refreshPersonalSearch(),true);
 assert.equal(state.personalSearchStatus.state,'ready');
 assert.equal(q('#fusion-personal-search-recovery').hidden,true);
 assert.deepStrictEqual(calls.map(call=>call.slice(0,2)),[
  ['/api/desktop/personal-imports/search-status','GET'],
  ['/api/desktop/personal-imports/search-refresh','POST'],
  ['/api/desktop/personal-imports/search-refresh','POST'],
 ]);
 assert.equal(calls[1][2],'{{}}');assert.equal(calls[2][2],'{{}}');
 assert(!calls.some(call=>call[0].includes('reviewed-import')));
 assert.equal(confirmCalls,0);assert.equal(aiCalls,0);
}})().catch(error=>{{console.error(error);process.exitCode=1}});
"""
        result = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, timeout=8, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_late_search_status_response_cannot_overwrite_newer_status(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert');
class El{{constructor(){{this.textContent='';this.dataset={{}};this.disabled=false;this.hidden=false;this.listeners={{}};}}addEventListener(name,fn){{(this.listeners[name]??=[]).push(fn)}}querySelector(){{return null}}querySelectorAll(){{return[]}}}}
const nodes=new Map(),q=selector=>{{if(!nodes.has(selector))nodes.set(selector,new El());return nodes.get(selector)}};
const pending=[];eval(fs.readFileSync({str(RUNTIME)!r},'utf8'));
const state={{personalAction:0,personalPageRequest:0,personalSearchRequest:0,personalSearchStatus:null,personalSearchRefreshing:false,personalPreview:null,personalPreviewPage:null,personalSeriesCounter:0,personalSheetIndex:0,personalStatus:null,personalSuggestion:null}};
const controller=globalThis.AutoResearchFusionPersonalImport.createPersonalImportController({{
 state,q,qa:()=>[],esc:String,cleanText:v=>String(v??''),safeError:(code,message)=>Object.assign(new Error(message),{{code}}),request:()=>new Promise(resolve=>pending.push(resolve)),native:{{selectPersonalFile:async()=>null}},
 authorizeAI:async()=>null,executeAI:async()=>null,aiProgress:()=>{{}},aiErrorCopy:()=>'',resetAI:()=>{{}},isPersonalActive:()=>false,setOperation:()=>{{}},selectCell:()=>true,projectInspector:()=>{{}},openImportedTable:async()=>false,confirmAction:()=>false,
}});
(async()=>{{
 const older=controller.loadPersonalSearchStatus(),newer=controller.loadPersonalSearchStatus();
 pending[1]({{schema_version:'personal-search-readiness-v1',state:'ready',ready:true,document_count:7}});assert.equal(await newer,true);
 pending[0]({{schema_version:'personal-search-readiness-v1',state:'stale',ready:true,document_count:3}});assert.equal(await older,false);
 assert.equal(state.personalSearchStatus.state,'ready');assert.equal(state.personalSearchStatus.documentCount,7);
 assert.equal(q('#fusion-personal-search-recovery').hidden,true);
}})().catch(error=>{{console.error(error);process.exitCode=1}});
"""
        result = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, timeout=8, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
