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


if __name__ == "__main__":
    unittest.main()
