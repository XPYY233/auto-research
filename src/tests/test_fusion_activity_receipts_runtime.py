from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "auto_research" / "evidence" / "web"


class FusionActivityReceiptsRuntimeTests(unittest.TestCase):
    def _run_node(self, body: str) -> None:
        program = f"""
const assert=require('assert'),fs=require('fs');
class El{{constructor(){{this.hidden=false;this.disabled=false;this.checked=false;this.textContent='';this.innerHTML='';this.value='';this.dataset={{}};this.listeners={{}};this.classList={{toggle(){{}}}};}}addEventListener(k,f){{(this.listeners[k]??=[]).push(f)}}setAttribute(){{}}closest(){{return this}}querySelector(){{return this}}focus(){{}}insertAdjacentHTML(){{}}}}
const nodes=new Map(),node=selector=>{{if(!nodes.has(selector))nodes.set(selector,new El());return nodes.get(selector)}};
const receiptButtons=[];
const qa=selector=>selector==='[data-receipt-index]'?receiptButtons:[];
globalThis.fetch=()=>{{throw new Error('second request wrapper forbidden')}};
globalThis.localStorage={{getItem:()=>{{throw new Error('receipt storage forbidden')}},setItem:()=>{{throw new Error('receipt storage forbidden')}}}};
globalThis.document={{addEventListener:()=>{{throw new Error('module lifecycle listener forbidden')}}}};
eval(fs.readFileSync({str(WEB / 'fusion_package_center.js')!r},'utf8'));
const makeController=(request,state={{jobs:new Map()}})=>globalThis.AutoResearchFusionPackage.createPackageCenterController({{
 state,q:node,qa,request,safeError:(code,message)=>Object.assign(new Error(message),{{code}}),cleanText:(v,n=8000)=>String(v??'').trim().slice(0,n),esc:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;'),setOperation(){{}},
 native:{{selectEvidencePackage:async()=>({{cancelled:true}}),selectPackageDestination:async()=>({{cancelled:true}}),selectDatasetDestination:async()=>({{cancelled:true}})}},projectJob(){{}},openOfficialSearch(){{}},getCurrentPaperId:()=>null,isActiveView:()=>true,
}});
const transfer=(overrides={{}})=>({{schema_version:'activity-receipt-v1',receipt_uid:'a'.repeat(64),activity_type:'transfer_export',artifact_kind:'literature_collection',outcome:'completed',completed_at:'2026-08-28T10:00:00Z',expires_at:'2026-09-27T10:00:00Z',summary:{{checksum_code:'b'.repeat(12),file_count:3,total_bytes:2048}},...overrides}});
const dataset=(overrides={{}})=>({{schema_version:'activity-receipt-v1',receipt_uid:'c'.repeat(64),activity_type:'dataset_export',artifact_kind:'dataset_bundle',outcome:'completed',completed_at:'2026-08-28T11:00:00Z',expires_at:'2026-09-27T11:00:00Z',summary:{{checksum_code:'d'.repeat(12),record_count:10,entity_counts:{{item:4,finding:3,table:2,figure:1}},split_counts:{{train:8,validation:1,test:1}}}},...overrides}});
const snapshot=(receipts=[transfer()],revision=1)=>({{schema_version:'activity-receipts-v1',revision,storage:'mac-private-encrypted-v1',receipts}});
{body}
"""
        result = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, timeout=8, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_projection_is_strict_bounded_and_path_free(self) -> None:
        self._run_node(
            """
const controller=makeController(async()=>snapshot());
let value=controller.publicActivityReceiptSnapshot(snapshot([transfer(),dataset()]));
assert.equal(value.receipts.length,2);assert.equal(value.receipts[0].checksumCode,'b'.repeat(12));
assert.equal(JSON.stringify(value).includes('mac-private-encrypted-v1'),false);
assert.equal(controller.publicActivityReceiptSnapshot(snapshot([transfer({summary:{checksum_code:'b'.repeat(12),path:'/Users/private/export.aresearch'}})])),null);
assert.equal(controller.publicActivityReceiptSnapshot(snapshot([transfer({file_name:'secret.aresearch'})])),null);
assert.equal(controller.publicActivityReceiptSnapshot(snapshot([transfer({summary:{checksum_code:'b'.repeat(12),file_count:true}})])),null);
assert.equal(controller.publicActivityReceiptSnapshot(snapshot([dataset({summary:{checksum_code:'d'.repeat(12),record_count:10,entity_counts:{item:4,finding:3,table:2,figure:-1},split_counts:{train:8,validation:1,test:1}}})])),null);
assert.equal(controller.publicActivityReceiptSnapshot({...snapshot(),path:'/tmp/receipts.json'}),null);
assert.equal(controller.publicActivityReceiptSnapshot(snapshot([dataset({summary:{checksum_code:'d'.repeat(12),record_count:10,archive_size:4096,entity_counts:{item:4,finding:3,table:2,figure:1},split_counts:{train:8,validation:1,test:1}}})])).receipts[0].metrics.at(-1)[0],'大小');
"""
        )

    def test_load_delete_clear_and_conflict_refresh_use_one_authority(self) -> None:
        self._run_node(
            """
let calls=[],revision=3,conflict=false;
globalThis.confirm=()=>true;
const request=async(url,options={})=>{const body=options.body?JSON.parse(options.body):null;calls.push([url,options.method||'GET',body]);
 if((options.method||'GET')==='GET')return snapshot([transfer()],revision);
 if(conflict){conflict=false;throw Object.assign(new Error('canary path /tmp/private'),{code:'activity_receipt_revision_conflict'});}
 revision+=1;return snapshot(body.operation==='clear'?[]:[dataset()],revision);
};
const controller=makeController(request);
assert.equal(controller.bind(),true);assert.equal(controller.bind(),false);assert.equal(node('#fusion-package-receipts-clear').listeners.click.length,1);
(async()=>{await controller.loadActivityReceipts();assert.equal(node('#fusion-package-receipts').innerHTML.includes('/Users/'),false);assert.equal(node('#fusion-package-receipts').innerHTML.includes('a'.repeat(64)),false);assert(node('#fusion-package-receipts').innerHTML.includes('bbbbbbbbbbbb'));
 await controller.deleteActivityReceipt('a'.repeat(64));assert.deepEqual(calls[1][2],{operation:'delete',expected_revision:3,receipt_uid:'a'.repeat(64)});
 await controller.clearActivityReceipts();assert.deepEqual(calls[2][2],{operation:'clear',expected_revision:4,confirm_clear:true});
 revision=7;await controller.loadActivityReceipts({force:true});conflict=true;assert.equal(await controller.deleteActivityReceipt('a'.repeat(64)),false);assert.equal(calls.at(-1)[0],'/api/desktop/package-center/receipts');assert(node('#fusion-package-receipts-status').textContent.includes('现已刷新'));assert.equal(node('#fusion-package-receipts-status').textContent.includes('/tmp'),false);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        )

    def test_receipt_unavailable_does_not_break_package_center(self) -> None:
        self._run_node(
            """
const state={jobs:new Map(),loaded:false,loading:false};
const request=async url=>{if(url==='/api/desktop/package-center/receipts')throw Object.assign(new Error('/private/tmp/canary'),{code:'activity_receipt_store_unavailable'});if(url==='/api/desktop/evidence-packages')return{active:false,repository_audited:false,installed_packages:[]};if(url==='/api/desktop/package-center')return{schema:'package-center-status-v1',official:{current:null,installed_versions:[]},capabilities:{dataset_export:false}};throw new Error('unexpected '+url)};
const controller=makeController(request,state);
(async()=>{await controller.loadPackageCenter();assert.equal(state.loaded,true);assert.equal(state.receiptsStatus,'error');assert(node('#fusion-package-receipts-status').textContent.includes('已导出文件不受影响'));assert.equal(node('#fusion-package-receipts-status').textContent.includes('/private'),false);assert.equal(node('#fusion-package-status').textContent,'资料包状态已更新。');})().catch(error=>{console.error(error);process.exitCode=1});
"""
        )

    def test_static_dom_has_separate_task_and_receipt_owners(self) -> None:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        source = (WEB / "fusion_package_center.js").read_text(encoding="utf-8")
        self.assertEqual(html.count('id="fusion-package-jobs"'), 1)
        self.assertEqual(html.count('id="fusion-package-receipts"'), 1)
        self.assertEqual(html.count('id="fusion-package-receipts-clear"'), 1)
        self.assertIn("本次任务", html)
        self.assertIn("最近完成回执", html)
        self.assertNotIn("localStorage", source)
        self.assertNotIn("fetch(", source)

    def test_stored_refreshes_and_pending_never_restarts_export(self) -> None:
        self._run_node(
            """
const result={schema_version:'dataset-bundle-v1',status:'published',binary_assets_included:false,record_count:10,archive_sha256:'e'.repeat(64),checksum_code:'e'.repeat(12)};
async function exercise(receiptStatus){let calls=[];const state={jobs:new Map(),center:{capabilities:{dataset_export:true}},datasetPlan:{planToken:'dataset_plan_0123456789',includePrivate:false,rightsAckRequired:false,unreviewedAckRequired:false}};
 node('#fusion-dataset-export').disabled=false;node('#fusion-dataset-rights-ack').checked=false;node('#fusion-dataset-unreviewed-ack').checked=false;
 const request=async(url,options={})=>{calls.push([url,options.method||'GET']);if(url==='/api/desktop/package-center/dataset-export')return{job_id:'dataset-job-12345678',operation:'dataset_export',stage:'completed',progress:100,terminal:true,receipt_status:receiptStatus,result};if(url==='/api/desktop/package-center/receipts')return snapshot([],9);throw new Error('unexpected '+url)};
 const controller=globalThis.AutoResearchFusionPackage.createPackageCenterController({state,q:node,qa,request,safeError:(code,message)=>Object.assign(new Error(message),{code}),cleanText:(v,n=8000)=>String(v??'').slice(0,n),esc:v=>String(v??''),setOperation(){},native:{selectEvidencePackage:async()=>({cancelled:true}),selectPackageDestination:async()=>({cancelled:true}),selectDatasetDestination:async()=>({ok:true,cancelled:false,destination:{destination_token:'destination-token-1234'}})},projectJob(){},openOfficialSearch(){},getCurrentPaperId:()=>null,isActiveView:()=>true});
 await controller.exportDataset();return{calls,html:node('#fusion-package-jobs').innerHTML};}
(async()=>{const stored=await exercise('stored');assert.deepEqual(stored.calls.map(x=>x[0]),['/api/desktop/package-center/dataset-export','/api/desktop/package-center/receipts']);const pending=await exercise('pending');assert.deepEqual(pending.calls.map(x=>x[0]),['/api/desktop/package-center/dataset-export']);assert(pending.html.includes('文件已导出，回执待恢复'));assert(pending.html.includes('请勿重复导出'));})().catch(error=>{console.error(error);process.exitCode=1});
"""
        )


if __name__ == "__main__":
    unittest.main()
