from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "auto_research" / "evidence" / "web"


class FusionControllerIsolationRuntimeTests(unittest.TestCase):
    def test_package_controller_failure_does_not_block_personal_controller(self) -> None:
        program = f"""
const assert=require('assert'),fs=require('fs');
class El{{constructor(){{this.hidden=false;this.disabled=false;this.textContent='';this.value='';this.dataset={{}};this.listeners={{}};}}addEventListener(k,f){{(this.listeners[k]??=[]).push(f)}}querySelector(){{return null}}querySelectorAll(){{return[]}}}}
const nodes=new Map(),q=selector=>{{if(!nodes.has(selector))nodes.set(selector,new El());return nodes.get(selector)}};
globalThis.fetch=()=>{{throw new Error('controllers must use the injected request port')}};
eval(fs.readFileSync({str(WEB / 'fusion_package_center.js')!r},'utf8'));
eval(fs.readFileSync({str(WEB / 'fusion_personal_import.js')!r},'utf8'));
assert.throws(()=>globalThis.AutoResearchFusionPackage.createPackageCenterController({{}}),/package_state_invalid/);
const state={{personalAction:0,personalPageRequest:0,personalSearchRequest:0,personalSearchStatus:null,personalSearchRefreshing:false,personalPreview:null,personalPreviewPage:null,personalSeriesCounter:0,personalSheetIndex:0,personalStatus:null,personalSuggestion:null}};
let requests=0;
const controller=globalThis.AutoResearchFusionPersonalImport.createPersonalImportController({{
 state,q,qa:()=>[],esc:String,cleanText:v=>String(v??''),safeError:(code,message)=>Object.assign(new Error(message),{{code}}),
 request:async url=>{{requests+=1;assert.equal(url,'/api/desktop/personal-imports/search-status');return{{schema_version:'personal-search-readiness-v1',state:'ready',ready:true,document_count:4}}}},
 native:{{selectPersonalFile:async()=>null}},authorizeAI:async()=>null,executeAI:async()=>null,aiProgress:()=>{{}},aiErrorCopy:()=>'',resetAI:()=>{{}},isPersonalActive:()=>true,setOperation:()=>{{}},selectCell:()=>true,projectInspector:()=>{{}},openImportedTable:async()=>false,confirmAction:()=>false,
}});
assert.equal(controller.bind(),true);assert.equal(controller.bind(),false);
(async()=>{{assert.equal(await controller.loadPersonalSearchStatus(),true);assert.equal(requests,1);assert.equal(state.personalSearchStatus.state,'ready');assert.equal(q('#fusion-personal-search-recovery').hidden,true);}})().catch(error=>{{console.error(error);process.exitCode=1}});
"""
        result = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, timeout=8, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
