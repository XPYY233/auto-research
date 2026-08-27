from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "auto_research" / "evidence" / "web"


class FusionPackageCenterRuntimeTests(unittest.TestCase):
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
