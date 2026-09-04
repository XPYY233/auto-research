from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace

from auto_research.product.dataset_bundle import DatasetBundleBuilder
from auto_research.product.dataset_export_service import DatasetExportCandidate, DatasetExportService
from auto_research.product.package_center import PackageJobService


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "auto_research" / "evidence" / "web"


class FusionDatasetExportRuntimeTests(unittest.TestCase):
    def test_interrupted_export_resumes_same_job_without_second_export(self) -> None:
        program = r"""
const assert=require('assert'),fs=require('fs');
eval(fs.readFileSync(RUNTIME_PATH,'utf8'));
class El {constructor(){this.checked=false;this.disabled=false;this.hidden=false;this.textContent='';this.innerHTML='';this.dataset={};this.handlers={};} addEventListener(n,f){this.handlers[n]=f;}}
const deferred=()=>{let resolve;const promise=new Promise(yes=>resolve=yes);return {resolve,promise};};
globalThis.setTimeout=callback=>{callback();return 0;};
async function run(mode){
 const nodes=new Map(),q=s=>{if(!nodes.has(s))nodes.set(s,new El());return nodes.get(s);};
 const id='dataset_job_original_1234',calls=[];let pickers=0,polls=0,resuming=false;const resumed=deferred();
 const running={job_id:id,operation:'dataset_export',stage:'running',terminal:false,progress:50};
 const state={jobs:new Map(),center:{capabilities:{dataset_export:true}}};
 const api=globalThis.AutoResearchFusionPackage.createPackageCenterController({state,q,qa:()=>[],
  async request(url,options={}){calls.push([url,options.method||'GET']);
   if(url.endsWith('/dataset-export'))return running;
   assert.equal(url,'/api/desktop/package-center/jobs/'+id);
   assert.equal(options.method,undefined,'resume only observes existing job');polls++;
   if(resuming)return resumed.promise;
   if(mode==='network'||mode==='failed')throw Error('temporary transport failure');
   if(mode==='identity')return {...running,job_id:'dataset_job_other_123456'};
   return running;
  },safeError:(code,message)=>Object.assign(new Error(message),{code}),cleanText:(v,n=1000)=>String(v||'').slice(0,n),esc:String,
  setOperation(){},projectJob(){},openOfficialSearch(){},getCurrentPaperId:()=>null,isActiveView:()=>true,
  native:{selectEvidencePackage(){},selectPackageDestination(){},async selectDatasetDestination(){pickers++;return {ok:true,destination:{destination_token:'destination_0123456789'}};}}
 });
 state.datasetPlan=api.publicDatasetPlan({schema_version:'dataset-export-plan-v1',plan_token:'dataset_plan_0123456789',include_private:false,binary_assets_included:false,record_count:1,entity_counts:{item:1,finding:0,table:0,figure:0},split_counts:{train:1,validation:0,test:0},missing_fields:{},rights_risks:[],unreviewed_count:0,rights_ack_required:false,unreviewed_ack_required:false},false);
 await api.exportDataset();
 assert.equal(polls,mode==='timeout'?600:1);assert.strictEqual(state.jobs.get(id),running,'failed observation preserves last server state');
 assert.equal(state.jobs.size,1,'mismatched job cannot enter current tasks');assert.equal(state.datasetReceipt,undefined);
 assert(q('#fusion-package-jobs').innerHTML.includes('继续查看原任务'));assert(q('#fusion-package-status').textContent.includes('不要重复导出'));
 assert(q('#fusion-dataset-export').disabled,'unknown outcome must keep new export blocked');
 await api.exportDataset();assert.equal(pickers,1);assert.equal(calls.filter(c=>c[1]==='POST').length,1);
 resuming=true;const resume=api.resumePackageJob(id);await Promise.resolve();
 assert.equal(await api.resumePackageJob(id),false,'resume itself is single flight');await api.exportDataset();assert.equal(pickers,1);
 if(mode==='failed')resumed.resolve({...running,terminal:true,stage:'failed',error:{code:'dataset_bundle_destination_exists',message:'目标文件已存在。'}});
 else resumed.resolve({...running,terminal:true,stage:'completed',progress:100,result:{schema_version:'dataset-bundle-v1',status:'published',binary_assets_included:false,record_count:1,checksum_code:'a'.repeat(12)}});
 assert.equal(await resume,mode!=='failed');assert(!q('#fusion-dataset-export').disabled,'authoritative terminal result releases export');
 assert(!q('#fusion-package-jobs').innerHTML.includes('data-package-job-resume'));
 if(mode!=='failed'){assert.equal(state.datasetReceipt.recordCount,1);assert(!q('#fusion-dataset-receipt').hidden);assert(q('#fusion-package-status').textContent.includes('导出完成'));}
 else{assert.equal(state.datasetReceipt,undefined);assert(q('#fusion-package-status').textContent.includes('目标文件已存在'));}
 assert.equal(calls.filter(c=>c[1]==='POST').length,1);assert.equal(await api.resumePackageJob(id),false);
}
(async()=>{for(const mode of ['timeout','network','identity','failed'])await run(mode);})().catch(e=>{console.error(e);process.exitCode=1;});
""".replace("RUNTIME_PATH", repr(str(WEB / "fusion_package_center.js")))
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_scope_races_and_export_single_flight_use_current_controller(self) -> None:
        program = r"""
const assert=require('assert'),fs=require('fs');
eval(fs.readFileSync(RUNTIME_PATH,'utf8'));
class El {
  constructor(){this.checked=false;this.disabled=false;this.hidden=false;this.textContent='';this.innerHTML='';this.dataset={};this.handlers={};}
  addEventListener(name,fn){this.handlers[name]=fn;}
  closest(){return null;}
}
const nodes=new Map(),q=selector=>{if(!nodes.has(selector))nodes.set(selector,new El());return nodes.get(selector);};
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const plans=[],pickers=[],starts=[],polls=[],calls=[];
const request=(url,options={})=>{
  calls.push({url,body:options.body?JSON.parse(options.body):null});
  const next=deferred();
  if(url.endsWith('/dataset-plan'))plans.push(next);
  else if(url.endsWith('/dataset-export'))starts.push(next);
  else if(url.includes('/package-center/jobs/'))polls.push(next);
  else throw Error('unexpected route '+url);
  return next.promise;
};
const state={jobs:new Map(),center:{capabilities:{dataset_export:true}}};
const api=globalThis.AutoResearchFusionPackage.createPackageCenterController({
  state,q,qa:()=>[],request,safeError:(code,message)=>Object.assign(new Error(message),{code}),
  cleanText:(value,max=1000)=>typeof value==='string'?value.trim().slice(0,max):'',esc:value=>String(value),
  setOperation(){},projectJob(){},openOfficialSearch(){},getCurrentPaperId:()=>null,isActiveView:()=>true,
  native:{selectEvidencePackage(){},selectPackageDestination(){},selectDatasetDestination(name){assert.equal(name,'Auto-Research-dataset.zip');const next=deferred();pickers.push(next);return next.promise;}}
});
assert.equal(api.bind(),true);assert.equal(api.bind(),false);
const scope=q('#fusion-dataset-include-private'),button=q('#fusion-dataset-export'),notice=q('#fusion-package-status');
const changeScope=value=>{scope.checked=value;scope.handlers.change();};
const raw=(includePrivate,token)=>({schema_version:'dataset-export-plan-v1',plan_token:token,include_private:includePrivate,binary_assets_included:false,record_count:1,entity_counts:{item:1,finding:0,table:0,figure:0},split_counts:{train:1,validation:0,test:0},missing_fields:{},rights_risks:['paper_rights:paper_1'],unreviewed_count:1,rights_ack_required:true,unreviewed_ack_required:true});
const acknowledge=()=>{for(const id of ['rights','unreviewed']){const node=q('#fusion-dataset-'+id+'-ack');node.checked=true;node.handlers.change();}};
const destination={ok:true,destination:{destination_token:'destination_0123456789'}};
const exportsMade=()=>calls.filter(value=>value.url.endsWith('/dataset-export')).length;
const flushUntil=async predicate=>{for(let i=0;i<40&&!predicate();i++)await new Promise(resolve=>setImmediate(resolve));assert(predicate(),'bounded asynchronous milestone not reached');};
(async()=>{
  changeScope(true);const old=api.planDataset();assert.equal(state.datasetPlan,null);
  changeScope(false);const changedNotice=notice.textContent;
  plans[0].resolve(raw(true,'dataset_plan_private_1234'));await old;
  assert.equal(state.datasetPlan,null);assert.equal(notice.textContent,changedNotice,'late private plan must not repaint UI');assert(button.disabled);
  const staleError=api.planDataset();changeScope(true);changeScope(false);const failedNotice=notice.textContent;
  plans[1].reject(Error('obsolete failure'));await staleError;assert.equal(notice.textContent,failedNotice,'stale error cannot overwrite new scope status');
  const first=api.planDataset(),latest=api.planDataset();plans[3].resolve(raw(false,'dataset_plan_latest_12345'));await latest;
  const current=state.datasetPlan,currentHTML=q('#fusion-dataset-metrics').innerHTML;
  plans[2].resolve(raw(false,'dataset_plan_older_123456'));await first;assert.strictEqual(state.datasetPlan,current);assert.equal(q('#fusion-dataset-metrics').innerHTML,currentHTML);
  acknowledge();assert(!button.disabled);
  const invalidated=api.exportDataset();assert(button.disabled);changeScope(true);changeScope(false);
  pickers[0].resolve(destination);await invalidated;assert.equal(exportsMade(),0,'picker result must not export an invalidated plan even after scope returns');assert.equal(state.datasetPlan,null);
  const regenerated=api.planDataset();plans[4].resolve(raw(false,'dataset_plan_retry_123456'));await regenerated;acknowledge();
  const retained=state.datasetPlan,cancelled=api.exportDataset();await api.exportDataset();assert.equal(pickers.length,2,'duplicate click cannot open another picker');
  acknowledge();assert(button.disabled,'risk events cannot unlock pending picker');pickers[1].resolve({cancelled:true});await cancelled;
  assert.strictEqual(state.datasetPlan,retained,'native cancellation retains reviewed plan');assert(!button.disabled);assert.equal(exportsMade(),0);
  const exported=api.exportDataset();assert.equal(pickers.length,3,'cancelled picker can be retried');pickers[2].resolve(destination);
  await flushUntil(()=>starts.length===1);acknowledge();await api.exportDataset();assert(button.disabled);assert.equal(pickers.length,3,'start request remains single flight');assert.equal(exportsMade(),1);
  assert.deepEqual(calls.find(value=>value.url.endsWith('/dataset-export')).body,{plan_token:retained.planToken,destination_token:'destination_0123456789',rights_acknowledged:true,unreviewed_acknowledged:true});
  const realTimeout=globalThis.setTimeout;globalThis.setTimeout=callback=>{callback();return 0;};
  starts[0].resolve({job_id:'dataset_job_0123456789',operation:'dataset_export',stage:'running',progress:20,terminal:false});
  await flushUntil(()=>polls.length===1);acknowledge();await api.exportDataset();assert(button.disabled);assert.equal(pickers.length,3,'polling remains single flight');
  polls[0].resolve({job_id:'dataset_job_0123456789',operation:'dataset_export',stage:'completed',progress:100,terminal:true,result:{schema_version:'dataset-bundle-v1',status:'published',binary_assets_included:false,record_count:1,checksum_code:'a'.repeat(12)}});
  await exported;globalThis.setTimeout=realTimeout;assert(!button.disabled);assert.equal(state.datasetReceipt.recordCount,1);assert.equal(exportsMade(),1);
})().catch(error=>{console.error(error);process.exitCode=1;});
""".replace("RUNTIME_PATH", repr(str(WEB / "fusion_package_center.js")))
        result = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, check=False, timeout=8
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_builder_and_service_plan_survives_renderer_with_corpus_risks(self) -> None:
        papers = [{"paper_uid": f"paper-{i}", "source_scope": "official"} for i in range(123)]
        records = [{
            "paper_uid": row["paper_uid"], "source_scope": "official",
            "source_id": "s" * 256, "entity_uid": f"entity-{i}-" + "e" * 240,
            "entity_type": "table", "quality_gate_status": "published",
            "asset_ref": {"asset_uid": f"asset-{i}", "media_type": "image/png", "included": False},
        } for i, row in enumerate(papers)]
        plan = DatasetBundleBuilder().plan(papers=papers, evidence=records)
        source = SimpleNamespace(plan=lambda **_: DatasetExportCandidate("a" * 64, plan))
        service = DatasetExportService(source=source, destination_resolver=None, publisher=None, jobs=PackageJobService())
        dto = service.plan(include_private=False)
        self.assertEqual(len(dto["rights_risks"]), 246)
        self.assertIn("paper.title", dto["missing_fields"])
        program = f"""
globalThis.document={{readyState:'loading',querySelector:()=>null,querySelectorAll:()=>[],addEventListener:()=>{{}}}};
eval(require('fs').readFileSync({str(WEB / 'fusion_package_center.js')!r},'utf8'));
eval(require('fs').readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));
const assert=require('assert'),raw=JSON.parse(require('fs').readFileSync(0,'utf8'));
const parse=globalThis.AutoResearchFusion.publicDatasetPlan,projected=parse(raw,false);
assert(projected);assert.equal(projected.recordCount,123);
assert.deepEqual(projected.risks,raw.rights_risks); // No 100-row cut or 500-character ID truncation.
assert.deepEqual(Object.fromEntries(projected.missing),raw.missing_fields);
assert.equal(projected.rightsAckRequired,true);
assert.equal(parse({{...raw,unreviewed_count:124}},false),null);
assert.equal(parse({{...raw,rights_risks:[...raw.rights_risks,raw.rights_risks[0]]}},false),null);
assert.equal(parse({{...raw,rights_risks:['paper_rights:'+ 'x'.repeat(257)]}},false),null);
assert.equal(parse({{...raw,missing_fields:{{'table.caption':124}}}},false),null);
"""
        result = subprocess.run(["node", "-e", program], input=json.dumps(dto), capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

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
class El{{constructor(){{this.hidden=false;this.disabled=false;this.checked=false;this.textContent='';this.innerHTML='';this.dataset={{}};this.handlers={{}};}}querySelector(){{return null}}addEventListener(type,fn){{this.handlers[type]=fn}}setAttribute(){{}}removeAttribute(){{}}}}
const names=['fusion-dataset-plan-button','fusion-dataset-availability','fusion-package-status','fusion-dataset-result','fusion-dataset-receipt','fusion-dataset-include-private','fusion-dataset-metrics','fusion-dataset-missing','fusion-dataset-risks','fusion-dataset-unreviewed-row','fusion-dataset-rights-row','fusion-dataset-unreviewed-ack','fusion-dataset-rights-ack','fusion-dataset-unreviewed-copy','fusion-dataset-export','fusion-dataset-receipt-name','fusion-dataset-receipt-metrics','fusion-dataset-receipt-note','fusion-package-jobs','fusion-package-context-jobs','fusion-inspector-title','fusion-inspector-body'];
const ids=Object.fromEntries(names.map(name=>['#'+name,new El()]));
ids['#fusion-dataset-risks-prev']=new El();ids['#fusion-dataset-risks-next']=new El();
globalThis.document={{readyState:'loading',querySelector:selector=>ids[selector]||null,querySelectorAll:()=>[],addEventListener:()=>{{}}}};
globalThis.localStorage={{getItem:()=>null,setItem:()=>{{throw new Error('must not persist dataset')}}}};
const calls=[];let riskPlan=false;
const response=payload=>({{ok:true,headers:{{get:()=>null}},json:async()=>payload}});
globalThis.fetch=async(url,options={{}})=>{{const body=options.body?JSON.parse(options.body):null;calls.push([String(url),options.method||'GET',body]);
 if(url==='/api/desktop/package-center/dataset-plan'){{const includePrivate=body.include_private;return response({{schema_version:'dataset-export-plan-v1',plan_token:includePrivate?'dataset_plan_private_1234':'dataset_plan_public_12345',include_private:includePrivate,binary_assets_included:false,entity_counts:{{item:4,finding:3,table:2,figure:1}},split_counts:{{train:8,validation:1,test:1}},missing_fields:includePrivate?{{'table.caption':1}}:{{}},unreviewed_count:includePrivate?2:0,rights_risks:includePrivate?Array.from({{length:123}},(_,i)=>'paper_rights:paper_'+i):[],record_count:10,rights_ack_required:includePrivate,unreviewed_ack_required:includePrivate}});}}
 if(url==='/api/desktop/package-center/dataset-export')return response({{schema:'package-job-v1',operation:'dataset_export',job_id:'dataset_job_0123456789',stage:'completed',progress:100,terminal:true,outcome:'exported',result:{{schema_version:'dataset-bundle-v1',status:'published',binary_assets_included:false,record_count:10,archive_size:2048,archive_sha256:'a'.repeat(64),checksum_code:'a'.repeat(12)}}}});
 throw new Error('unexpected:'+url);
}};
globalThis.pywebview={{api:{{select_dataset_export_destination:async name=>{{assert.equal(name,'Auto-Research-dataset.zip');return{{ok:true,cancelled:false,destination:{{destination_token:'dataset_destination_1234'}}}};}}}}}};
eval(fs.readFileSync({str(WEB / 'fusion_package_center.js')!r},'utf8'));
eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));
const api=globalThis.AutoResearchFusion;api.state.view='package';api.state.package.center={{capabilities:{{dataset_export:true}}}};
(async()=>{{await api.planDataset({{preventDefault(){{}}}});assert.deepEqual(calls[0],["/api/desktop/package-center/dataset-plan","POST",{{include_private:false}}]);assert(ids['#fusion-dataset-metrics'].innerHTML.includes('未审核'));assert.equal(ids['#fusion-dataset-export'].disabled,false);await api.exportDataset();assert.deepEqual(calls[1][2],{{plan_token:'dataset_plan_public_12345',destination_token:'dataset_destination_1234',rights_acknowledged:false,unreviewed_acknowledged:false}});assert.equal(ids['#fusion-dataset-receipt'].hidden,false);assert(ids['#fusion-dataset-receipt-metrics'].innerHTML.includes('aaaaaaaaaaaa'));
 ids['#fusion-dataset-include-private'].checked=true;await api.planDataset({{preventDefault(){{}}}});assert.deepEqual(calls[2][2],{{include_private:true}});assert.equal(ids['#fusion-dataset-unreviewed-row'].hidden,false);assert.equal(ids['#fusion-dataset-rights-row'].hidden,false);assert.equal(ids['#fusion-dataset-export'].disabled,true);ids['#fusion-dataset-unreviewed-ack'].checked=true;api.updateDatasetExportButton();assert.equal(ids['#fusion-dataset-export'].disabled,true);ids['#fusion-dataset-rights-ack'].checked=true;api.updateDatasetExportButton();assert.equal(ids['#fusion-dataset-export'].disabled,false);
 assert.equal((ids['#fusion-dataset-risks'].innerHTML.match(/<li>/g)||[]).length,50);
 ids['#fusion-dataset-risks-next'].handlers.click();assert(ids['#fusion-dataset-risks'].innerHTML.includes('51–100 / 123'));
 ids['#fusion-dataset-risks-next'].handlers.click();assert(ids['#fusion-dataset-risks'].innerHTML.includes('101–123 / 123'));
 assert.equal((ids['#fusion-dataset-risks'].innerHTML.match(/<li>/g)||[]).length,23);
 assert.equal(ids['#fusion-dataset-rights-ack'].checked,true);assert.equal(ids['#fusion-dataset-export'].disabled,false);
 const stalePage=ids['#fusion-dataset-risks-prev'].handlers.click;
 ids['#fusion-dataset-include-private'].checked=false;await api.planDataset({{preventDefault(){{}}}});
 const currentHtml=ids['#fusion-dataset-risks'].innerHTML;stalePage();assert.equal(ids['#fusion-dataset-risks'].innerHTML,currentHtml);
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
