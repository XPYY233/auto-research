from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "auto_research" / "evidence" / "web"


class FusionPackageCenterRuntimeTests(unittest.TestCase):
    def _run_node(self, program: str) -> None:
        result = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, timeout=8, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_controller_uses_injected_ports_and_binds_once(self) -> None:
        program = f"""
const assert=require('assert'),fs=require('fs');
class El{{constructor(){{this.hidden=false;this.disabled=false;this.checked=false;this.textContent='';this.innerHTML='';this.value='';this.dataset={{}};this.listeners={{}};this.classList={{toggle(){{}}}};}}addEventListener(k,f){{(this.listeners[k]??=[]).push(f)}}setAttribute(){{}}closest(){{return this}}querySelector(){{return this}}focus(){{}}insertAdjacentHTML(){{}}}}
const nodes=new Map(),node=selector=>{{if(!nodes.has(selector))nodes.set(selector,new El());return nodes.get(selector)}};
let requests=[],projected=0,navigation=0;
globalThis.fetch=()=>{{throw new Error('second request wrapper forbidden')}};
globalThis.localStorage={{getItem:()=>{{throw new Error('storage forbidden')}},setItem:()=>{{throw new Error('storage forbidden')}}}};
globalThis.document={{addEventListener:()=>{{throw new Error('module lifecycle listener forbidden')}}}};
eval(fs.readFileSync({str(WEB / 'fusion_package_center.js')!r},'utf8'));
const state={{loaded:false,loading:false,official:null,center:null,jobs:new Map(),lastOfficialResult:null,literaturePlan:null,personalPlan:null,datasetPlan:null,datasetReceipt:null,userSelection:null,userInspection:null}};
const summary={{schema:'package-summary-v1',package_kind:'official_evidence',package_id:'official-main',package_version:'1.1',outcome:'activated',trusted_official:true,content_counts:{{papers:2,items:3,findings:4,tables:1,figures:1}},asset_counts:{{paper_pdfs:2,visual_assets:2}},next_action:{{view:'search',search_source:'official'}}}};
const request=async(url,options={{}})=>{{requests.push([url,options.method||'GET']);if(url==='/api/desktop/evidence-packages/import')return{{job_id:'package-job-12345678',stage:'completed',progress:100,terminal:true,result:summary}};if(url==='/api/desktop/evidence-packages')return{{active:true,repository_audited:true,package_id:'official-main',package_version:'1.1',installed_packages:[]}};if(url==='/api/desktop/package-center')return{{schema:'package-center-status-v1',official:{{current:null,installed_versions:[]}},capabilities:{{dataset_export:false}}}};if(url==='/api/desktop/package-center/receipts')return{{schema_version:'activity-receipts-v1',revision:0,storage:'mac-private-encrypted-v1',receipts:[]}};throw new Error('unexpected '+url)}};
const controller=globalThis.AutoResearchFusionPackage.createPackageCenterController({{
 state,q:node,qa:()=>[],request,safeError:(code,message)=>Object.assign(new Error(message),{{code}}),cleanText:(v,n=8000)=>String(v??'').trim().slice(0,n),esc:v=>String(v??''),setOperation(){{}},
 native:{{selectEvidencePackage:async()=>({{ok:true,cancelled:false,selection:{{selection_id:'selection-1234567890'}}}}),selectPackageDestination:async()=>null,selectDatasetDestination:async()=>null}},
 projectJob(){{projected+=1}},openOfficialSearch(){{navigation+=1}},getCurrentPaperId:()=>null,isActiveView:()=>true,
}});
assert.equal(controller.bind(),true);assert.equal(controller.bind(),false);
assert.equal(node('#fusion-package-official-select').listeners.click.length,1);
(async()=>{{await controller.importOfficialPackage();assert.deepEqual(requests.map(x=>x[0]),['/api/desktop/evidence-packages/import','/api/desktop/package-center/receipts','/api/desktop/evidence-packages','/api/desktop/package-center']);assert.equal(projected,1);assert.equal(state.lastOfficialResult.outcome,'activated');assert.equal(navigation,0);}})().catch(error=>{{console.error(error);process.exitCode=1}});
"""
        result = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, timeout=8, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cancelled_export_risk_confirmation_sends_zero_requests(self) -> None:
        program = f"""
const assert=require('assert'),fs=require('fs');
class El{{constructor(){{this.hidden=false;this.disabled=false;this.checked=false;this.textContent='';this.innerHTML='';this.value='';this.dataset={{}};this.listeners={{}};this.classList={{toggle(){{}}}};}}addEventListener(k,f){{(this.listeners[k]??=[]).push(f)}}setAttribute(){{}}closest(){{return this}}querySelector(){{return this}}focus(){{}}insertAdjacentHTML(){{}}}}
const nodes=new Map(),node=selector=>{{if(!nodes.has(selector))nodes.set(selector,new El());return nodes.get(selector)}};
let requests=[],destinationCalls=0,confirmCalls=0;
globalThis.confirm=()=>{{confirmCalls+=1;return false}};
eval(fs.readFileSync({str(WEB / 'fusion_package_center.js')!r},'utf8'));
const state={{loaded:false,loading:false,official:null,center:null,jobs:new Map(),lastOfficialResult:null,literaturePlan:null,personalPlan:{{plan_token:'personal-plan'}},datasetPlan:null,datasetReceipt:null,userSelection:null,userInspection:null}};
const controller=globalThis.AutoResearchFusionPackage.createPackageCenterController({{
 state,q:node,qa:()=>[],request:async(...args)=>{{requests.push(args);throw new Error('request forbidden after cancel')}},
 safeError:(code,message)=>Object.assign(new Error(message),{{code}}),cleanText:(v,n=8000)=>String(v??'').trim().slice(0,n),esc:v=>String(v??''),setOperation(){{}},
 native:{{selectEvidencePackage:async()=>null,selectPackageDestination:async()=>{{destinationCalls+=1;return{{destination_token:'forbidden'}}}},selectDatasetDestination:async()=>null}},
 projectJob(){{}},openOfficialSearch(){{}},getCurrentPaperId:()=>null,isActiveView:()=>true,
}});
(async()=>{{
 await controller.exportPlannedPackage('personal_experiments');
 assert.equal(confirmCalls,1);
 assert.equal(destinationCalls,0);
 assert.deepStrictEqual(requests,[]);
}})().catch(error=>{{console.error(error);process.exitCode=1}});
"""
        self._run_node(program)

    def test_user_package_import_requires_all_three_risk_acknowledgements(self) -> None:
        program = f"""
const assert=require('assert'),fs=require('fs');
class El{{constructor(){{this.hidden=false;this.disabled=false;this.checked=false;this.textContent='';this.innerHTML='';this.value='';this.dataset={{}};this.listeners={{}};this.classList={{toggle(){{}}}};}}addEventListener(k,f){{(this.listeners[k]??=[]).push(f)}}emit(k){{for(const f of this.listeners[k]||[])f({{target:this,preventDefault(){{}}}})}}setAttribute(){{}}closest(){{return this}}querySelector(){{return this}}focus(){{}}insertAdjacentHTML(){{}}}}
const nodes=new Map(),node=selector=>{{if(!nodes.has(selector))nodes.set(selector,new El());return nodes.get(selector)}};
let requests=[];
eval(fs.readFileSync({str(WEB / 'fusion_package_center.js')!r},'utf8'));
const state={{loaded:false,loading:false,official:null,center:null,jobs:new Map(),lastOfficialResult:null,literaturePlan:null,personalPlan:null,datasetPlan:null,datasetReceipt:null,userSelection:'selection-token',userInspection:null}};
const controller=globalThis.AutoResearchFusionPackage.createPackageCenterController({{
 state,q:node,qa:()=>[],request:async(...args)=>{{requests.push(args);throw new Error('import must remain gated')}},
 safeError:(code,message)=>Object.assign(new Error(message),{{code}}),cleanText:(v,n=8000)=>String(v??'').trim().slice(0,n),esc:v=>String(v??''),setOperation(){{}},
 native:{{selectEvidencePackage:async()=>null,selectPackageDestination:async()=>null,selectDatasetDestination:async()=>null}},
 projectJob(){{}},openOfficialSearch(){{}},getCurrentPaperId:()=>null,isActiveView:()=>true,
}});
controller.bind();
const sha=node('#fusion-package-user-sha');
const checksum=node('#fusion-package-checksum-ack');
const unencrypted=node('#fusion-package-unencrypted-ack');
const source=node('#fusion-package-source-ack');
const submit=node('#fusion-package-user-import');
sha.value='a'.repeat(64);
const cases=[
  [false,true,true,checksum],
  [true,false,true,unencrypted],
  [true,true,false,source],
];
for(const [checksumValue,unencryptedValue,sourceValue,changed] of cases){{
 checksum.checked=checksumValue;unencrypted.checked=unencryptedValue;source.checked=sourceValue;
 changed.emit('change');
 assert.equal(submit.disabled,true,'each acknowledgement must independently gate import');
}}
checksum.checked=true;unencrypted.checked=true;source.checked=true;source.emit('change');
assert.equal(submit.disabled,false,'all three acknowledgements unlock import');
assert.deepStrictEqual(requests,[],'acknowledgement checks alone never import');
"""
        self._run_node(program)

    def test_literature_pdf_rights_are_confirmed_one_paper_at_a_time(self) -> None:
        program = f"""
const assert=require('assert'),fs=require('fs');
class El{{constructor(){{this.hidden=false;this.disabled=false;this.checked=false;this.textContent='';this.innerHTML='';this.value='';this.dataset={{}};this.listeners={{}};this.classList={{toggle(){{}}}};}}addEventListener(k,f){{(this.listeners[k]??=[]).push(f)}}setAttribute(){{}}closest(){{return this}}querySelector(){{return this}}focus(){{}}insertAdjacentHTML(){{}}}}
const nodes=new Map(),node=selector=>{{if(!nodes.has(selector))nodes.set(selector,new El());return nodes.get(selector)}};
const paperA=new El(),paperB=new El();paperA.dataset.packageRightsPaper='paper-a';paperB.dataset.packageRightsPaper='paper-b';
let requests=[],destinationCalls=0;
globalThis.confirm=()=>true;
eval(fs.readFileSync({str(WEB / 'fusion_package_center.js')!r},'utf8'));
const state={{loaded:false,loading:false,official:null,center:null,jobs:new Map(),lastOfficialResult:null,literaturePlan:{{plan_token:'literature-plan',rights_requirements:[{{paper_uid:'paper-a',title:'Paper A'}},{{paper_uid:'paper-b',title:'Paper B'}}]}},personalPlan:null,datasetPlan:null,datasetReceipt:null,userSelection:null,userInspection:null}};
const request=async(url,options={{}})=>{{requests.push([url,options]);return{{job_id:'package-job-12345678',stage:'completed',progress:100,terminal:true,result:{{}}}}}};
const controller=globalThis.AutoResearchFusionPackage.createPackageCenterController({{
 state,q:node,qa:selector=>selector==='[data-package-rights-paper]'?[paperA,paperB]:[],request,
 safeError:(code,message)=>Object.assign(new Error(message),{{code}}),cleanText:(v,n=8000)=>String(v??'').trim().slice(0,n),esc:v=>String(v??''),setOperation(){{}},
 native:{{selectEvidencePackage:async()=>null,selectPackageDestination:async()=>{{destinationCalls+=1;return{{ok:true,cancelled:false,destination:{{destination_token:'destination-token'}}}}}},selectDatasetDestination:async()=>null}},
 projectJob(){{}},openOfficialSearch(){{}},getCurrentPaperId:()=>null,isActiveView:()=>true,
}});
(async()=>{{
 paperA.checked=true;paperB.checked=false;
 await controller.exportPlannedPackage('literature_collection');
 assert.equal(destinationCalls,0);
 assert.deepStrictEqual(requests,[],'one unchecked paper blocks the whole export before destination selection');
 paperB.checked=true;
 await controller.exportPlannedPackage('literature_collection');
 assert.equal(destinationCalls,1);
 assert.equal(requests.length,1);
 assert.equal(requests[0][0],'/api/desktop/package-center/export');
 const body=JSON.parse(requests[0][1].body);
 assert.deepStrictEqual(body.rights_confirmations,{{
   unencrypted_ack:true,
   unauthenticated_source_ack:true,
   internal_use_only_ack:true,
   paper_rights:{{
     'paper-a':{{allowed:true,basis:'用户逐篇确认具有课题组内部分享权限'}},
     'paper-b':{{allowed:true,basis:'用户逐篇确认具有课题组内部分享权限'}},
   }},
 }});
}})().catch(error=>{{console.error(error);process.exitCode=1}});
"""
        self._run_node(program)

    def test_module_has_no_autonomous_runtime_authorities(self) -> None:
        source = (WEB / "fusion_package_center.js").read_text(encoding="utf-8")
        self.assertIn("createPackageCenterController", source)
        self.assertNotIn("fetch(", source)
        self.assertNotIn("DOMContentLoaded", source)
        self.assertNotIn("localStorage", source)
        self.assertNotIn("switchView", source)
        self.assertNotIn("package_center.js", source)


if __name__ == "__main__":
    unittest.main()
