from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "auto_research" / "evidence" / "web"


class FusionDatasetExportRuntimeTests(unittest.TestCase):
    def test_dataset_plan_and_receipt_projection_are_strict_and_path_free(self) -> None:
        program = f"""
globalThis.document={{readyState:'loading',querySelector:()=>null,querySelectorAll:()=>[],addEventListener:()=>{{}}}};
globalThis.localStorage={{getItem:()=>null,setItem:()=>{{throw new Error('must not persist dataset')}}}};
eval(require('fs').readFileSync({str(WEB / 'fusion_package_center.js')!r},'utf8'));
eval(require('fs').readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));
const api=globalThis.AutoResearchFusion,assert=require('assert');
const plan={{schema_version:'dataset-export-plan-v1',plan_token:'dataset_plan_0123456789',include_private:false,binary_assets_included:false,entity_counts:{{item:4,finding:3,table:2,figure:1}},split_counts:{{train:8,validation:1,test:1}},missing_fields:{{'item.meaning':2}},unreviewed_count:0,rights_risks:[],record_count:10,rights_ack_required:false,unreviewed_ack_required:false}};
assert.equal(api.publicDatasetPlan(plan,false).recordCount,10);
assert.equal(api.publicDatasetPlan({{...plan,include_private:true}},false),null);
assert.equal(api.publicDatasetPlan({{...plan,entity_counts:{{...plan.entity_counts,item:5}}}},false),null);
assert.equal(api.publicDatasetPlan({{...plan,rights_risks:['/Users/private/data'],rights_ack_required:true}},false),null);
assert.equal(api.publicDatasetPlan({{...plan,missing_fields:{{api_key:1}}}},false),null);
const receipt={{schema_version:'dataset-bundle-v1',status:'published',binary_assets_included:false,record_count:10,archive_size:2048,archive_sha256:'a'.repeat(64),checksum_code:'a'.repeat(12)}};
assert.equal(api.publicDatasetReceipt(receipt).checksumCode,'a'.repeat(12));
assert.equal(api.publicDatasetReceipt({{...receipt,checksum_code:'b'.repeat(12)}}),null);
assert.equal(api.publicDatasetReceipt({{...receipt,file_name:'/tmp/data.zip'}}).fileName,'');
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dataset_plan_export_and_conditional_acknowledgements_use_real_ports(self) -> None:
        program = f"""
const assert=require('assert'),fs=require('fs');
class El{{constructor(){{this.hidden=false;this.disabled=false;this.checked=false;this.textContent='';this.innerHTML='';this.dataset={{}};}}querySelector(){{return null}}addEventListener(){{}}setAttribute(){{}}removeAttribute(){{}}}}
const names=['fusion-dataset-plan-button','fusion-dataset-availability','fusion-package-status','fusion-dataset-result','fusion-dataset-receipt','fusion-dataset-include-private','fusion-dataset-metrics','fusion-dataset-missing','fusion-dataset-risks','fusion-dataset-unreviewed-row','fusion-dataset-rights-row','fusion-dataset-unreviewed-ack','fusion-dataset-rights-ack','fusion-dataset-unreviewed-copy','fusion-dataset-export','fusion-dataset-receipt-name','fusion-dataset-receipt-metrics','fusion-dataset-receipt-note','fusion-package-jobs','fusion-package-context-jobs','fusion-inspector-title','fusion-inspector-body'];
const ids=Object.fromEntries(names.map(name=>['#'+name,new El()]));
globalThis.document={{readyState:'loading',querySelector:selector=>ids[selector]||null,querySelectorAll:()=>[],addEventListener:()=>{{}}}};
globalThis.localStorage={{getItem:()=>null,setItem:()=>{{throw new Error('must not persist dataset')}}}};
const calls=[];let riskPlan=false;
const response=payload=>({{ok:true,headers:{{get:()=>null}},json:async()=>payload}});
globalThis.fetch=async(url,options={{}})=>{{const body=options.body?JSON.parse(options.body):null;calls.push([String(url),options.method||'GET',body]);
 if(url==='/api/desktop/package-center/dataset-plan'){{const includePrivate=body.include_private;return response({{schema_version:'dataset-export-plan-v1',plan_token:includePrivate?'dataset_plan_private_1234':'dataset_plan_public_12345',include_private:includePrivate,binary_assets_included:false,entity_counts:{{item:4,finding:3,table:2,figure:1}},split_counts:{{train:8,validation:1,test:1}},missing_fields:includePrivate?{{'table.caption':1}}:{{}},unreviewed_count:includePrivate?2:0,rights_risks:includePrivate?['paper_rights:paper_1']:[],record_count:10,rights_ack_required:includePrivate,unreviewed_ack_required:includePrivate}});}}
 if(url==='/api/desktop/package-center/dataset-export')return response({{schema:'package-job-v1',operation:'dataset_export',job_id:'dataset_job_0123456789',stage:'completed',progress:100,terminal:true,outcome:'exported',result:{{schema_version:'dataset-bundle-v1',status:'published',binary_assets_included:false,record_count:10,archive_size:2048,archive_sha256:'a'.repeat(64),checksum_code:'a'.repeat(12)}}}});
 throw new Error('unexpected:'+url);
}};
globalThis.pywebview={{api:{{select_dataset_export_destination:async name=>{{assert.equal(name,'Auto-Research-dataset.zip');return{{ok:true,cancelled:false,destination:{{destination_token:'dataset_destination_1234'}}}};}}}}}};
eval(fs.readFileSync({str(WEB / 'fusion_package_center.js')!r},'utf8'));
eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));
const api=globalThis.AutoResearchFusion;api.state.view='package';api.state.package.center={{capabilities:{{dataset_export:true}}}};
(async()=>{{await api.planDataset({{preventDefault(){{}}}});assert.deepEqual(calls[0],["/api/desktop/package-center/dataset-plan","POST",{{include_private:false}}]);assert(ids['#fusion-dataset-metrics'].innerHTML.includes('未审核'));assert.equal(ids['#fusion-dataset-export'].disabled,false);await api.exportDataset();assert.deepEqual(calls[1][2],{{plan_token:'dataset_plan_public_12345',destination_token:'dataset_destination_1234',rights_acknowledged:false,unreviewed_acknowledged:false}});assert.equal(ids['#fusion-dataset-receipt'].hidden,false);assert(ids['#fusion-dataset-receipt-metrics'].innerHTML.includes('aaaaaaaaaaaa'));
 ids['#fusion-dataset-include-private'].checked=true;await api.planDataset({{preventDefault(){{}}}});assert.deepEqual(calls[2][2],{{include_private:true}});assert.equal(ids['#fusion-dataset-unreviewed-row'].hidden,false);assert.equal(ids['#fusion-dataset-rights-row'].hidden,false);assert.equal(ids['#fusion-dataset-export'].disabled,true);ids['#fusion-dataset-unreviewed-ack'].checked=true;api.updateDatasetExportButton();assert.equal(ids['#fusion-dataset-export'].disabled,true);ids['#fusion-dataset-rights-ack'].checked=true;api.updateDatasetExportButton();assert.equal(ids['#fusion-dataset-export'].disabled,false);
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
