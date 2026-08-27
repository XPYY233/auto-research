from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "auto_research" / "evidence" / "web"


class FusionOperationHistoryRuntimeTests(unittest.TestCase):
    def _run_node(self, body: str) -> None:
        program = f"""
const assert=require('assert'),fs=require('fs');
class El{{constructor(){{this.disabled=false;this.textContent='';this.innerHTML='';this.dataset={{}};this.listeners={{}};}}addEventListener(k,f){{(this.listeners[k]??=[]).push(f)}}}}
const nodes=new Map(),node=selector=>{{if(!nodes.has(selector))nodes.set(selector,new El());return nodes.get(selector)}};
globalThis.fetch=()=>{{throw new Error('autonomous fetch forbidden')}};
globalThis.localStorage={{getItem:()=>{{throw new Error('storage forbidden')}},setItem:()=>{{throw new Error('storage forbidden')}}}};
globalThis.document={{addEventListener:()=>{{throw new Error('lifecycle listener forbidden')}}}};
eval(fs.readFileSync({str(WEB / 'fusion_operation_history.js')!r},'utf8'));
const uid=n=>(String(n).repeat(64)).slice(0,64);
function entry(overrides={{}}){{return{{schema_version:'operation-history-entry-v1',operation_uid:uid(1),operation:'transfer_export',state:'completed',stage:'completed',progress:100,terminal:true,outcome:'exported',receipt_status:'stored',created_at:'2026-08-01T08:00:00Z',updated_at:'2026-08-01T08:01:00Z',expires_at:'2026-08-31T08:01:00Z',next_action:'none',error:null,...overrides}}}}
function snapshot(operations=[],revision=1){{return{{schema_version:'operation-history-v1',revision,storage:'mac-private-encrypted-v1',operations}}}}
function controller(request,extras={{}}){{return globalThis.AutoResearchFusionOperationHistory.createOperationHistoryController({{
 q:node,qa:()=>[],request,safeError:(code,message)=>Object.assign(new Error(message),{{code}}),cleanText:(value,limit=8000)=>String(value??'').trim().slice(0,limit),esc:value=>String(value??''),onCountChange:extras.onCountChange||(()=>{{}}),onReturnToWorkflow:extras.onReturnToWorkflow||(()=>{{}}),onReceiptStored:extras.onReceiptStored||(async()=>{{}}),
}})}}
{body}
"""
        result = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, timeout=8, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_strict_public_projection_and_no_autonomous_authority(self) -> None:
        self._run_node(
            """
const api=controller(async()=>snapshot());
const valid=api.publicSnapshot(snapshot([
 entry(),
 entry({operation_uid:uid(2),operation:'transfer_import',state:'queued',stage:'queued',progress:0,terminal:false,outcome:null,receipt_status:null,next_action:'none'}),
 entry({operation_uid:uid(3),state:'running',stage:'build_archive',progress:62,terminal:false,outcome:null,receipt_status:null,next_action:'none'}),
 entry({operation_uid:uid(4),state:'interrupted',stage:'build_archive',progress:62,terminal:true,outcome:null,receipt_status:null,next_action:'restart_operation'}),
 entry({operation_uid:uid(5),state:'failed',stage:'failed',progress:62,terminal:true,outcome:null,receipt_status:null,next_action:'restart_operation',error:{code:'package_write_failed',message:'写入未完成。',stage:'failed',retryable:true}}),
]));
assert(valid);assert.equal(valid.operations.length,5);assert.equal(valid.operations[0].uid,uid(1));
assert.equal(api.publicSnapshot({...snapshot([entry()]),job_id:'package_job_secret'}),null);
assert.equal(api.publicSnapshot(snapshot([{...entry(),result:{path:'/private/tmp/output.zip'}}])),null);
assert.equal(api.publicSnapshot(snapshot([entry({progress:true})])),null);
assert.equal(api.publicSnapshot(snapshot([entry({receipt_status:'pending',next_action:'none'})])),null);
assert.equal(api.publicSnapshot(snapshot([entry({operation:'transfer_import',receipt_status:'stored'})])),null);
assert.equal(api.publicSnapshot(snapshot([entry({created_at:'2026-08-02T08:00:00Z',updated_at:'2026-08-01T08:00:00Z'})])),null);
"""
        )

    def test_load_renders_five_states_and_has_terminal_error_state(self) -> None:
        self._run_node(
            """
let count=-1,calls=0;
const operations=[
 entry({operation_uid:uid(1),receipt_status:'pending',next_action:'retry_receipt'}),
 entry({operation_uid:uid(2),operation:'dataset_export',state:'interrupted',stage:'publish',progress:84,terminal:true,outcome:null,receipt_status:null,next_action:'restart_operation'}),
 entry({operation_uid:uid(3),operation:'transfer_import',state:'queued',stage:'queued',progress:0,terminal:false,outcome:null,receipt_status:null}),
 entry({operation_uid:uid(4),state:'running',stage:'build_archive',progress:62,terminal:false,outcome:null,receipt_status:null}),
 entry({operation_uid:uid(5),state:'failed',stage:'failed',progress:62,terminal:true,outcome:null,receipt_status:null,error:{code:'package_write_failed',message:'写入未完成。',stage:'failed',retryable:false}}),
];
const api=controller(async url=>{calls+=1;assert.equal(url,'/api/desktop/package-center/history');return snapshot(operations,7)},{onCountChange:value=>{count=value}});
(async()=>{await api.load();assert.equal(calls,1);assert.equal(count,5);const html=node('#fusion-package-history').innerHTML;for(const copy of ['等待开始','正在进行','已完成','未完成','已中断'])assert(html.includes(copy));assert(html.includes('文件已导出，回执待恢复，请勿重复导出'));assert(html.includes('恢复完成回执'));assert(html.includes('返回相应流程'));assert(!html.includes(uid(1)),'opaque operation uid must never enter DOM');
 const broken=controller(async()=>{throw new Error('/private/secret')});await broken.load();assert.equal(node('#fusion-package-history-status').dataset.kind,'error');assert(node('#fusion-package-history-status').textContent.includes('暂时不可用'));})().catch(error=>{console.error(error);process.exitCode=1});
"""
        )

    def test_delete_clear_and_revision_conflict_refresh(self) -> None:
        self._run_node(
            """
let calls=[],confirms=0;
globalThis.confirm=()=>{confirms+=1;return true};
const original=snapshot([entry()],4),empty=snapshot([],5);
const api=controller(async(url,options={})=>{calls.push([url,options.method||'GET',options.body]);if((options.method||'GET')==='GET')return original;const body=JSON.parse(options.body);if(body.operation==='delete')return empty;if(body.operation==='clear'){const error=new Error('conflict');error.code='operation_history_revision_conflict';throw error}throw new Error('unexpected')});
(async()=>{await api.load();assert(await api.deleteOperation(0));assert.deepEqual(JSON.parse(calls[1][2]),{operation:'delete',expected_revision:4,operation_uid:uid(1)});api.publicSnapshot(original);await api.load({force:true});assert.equal(api.bind(),true);assert.equal(api.bind(),false);assert.equal(node('#fusion-package-history-clear').listeners.click.length,1);assert.equal(await api.clear(),false);assert.equal(confirms,1);assert.equal(calls.filter(call=>call[1]==='GET').length,3,'CAS conflict must refresh exactly once');assert(node('#fusion-package-history-status').textContent.includes('已更新'));})().catch(error=>{console.error(error);process.exitCode=1});
"""
        )

    def test_pending_retry_never_reexports_and_interrupted_only_returns(self) -> None:
        self._run_node(
            """
let calls=[],stored=0,returned=[];
const pending=entry({receipt_status:'pending',next_action:'retry_receipt'}),interrupted=entry({operation_uid:uid(2),operation:'dataset_export',state:'interrupted',stage:'publish',progress:84,terminal:true,outcome:null,receipt_status:null,next_action:'restart_operation'});
const api=controller(async(url,options={})=>{calls.push([url,options.method||'GET',options.body]);if(!url.endsWith('/receipt-retry'))return snapshot([pending,interrupted],9);assert.deepEqual(JSON.parse(options.body),{expected_revision:9});return snapshot([entry(),interrupted],10)},{onReceiptStored:async()=>{stored+=1},onReturnToWorkflow:operation=>returned.push(operation)});
(async()=>{await api.load();assert(await api.retryReceipt(0));assert.equal(stored,1);assert.deepEqual(calls.map(call=>call[0]),['/api/desktop/package-center/history','/api/desktop/package-center/history/'+uid(1)+'/receipt-retry']);assert(!calls.some(call=>call[0].includes('dataset-export')||call[0].endsWith('/export')));assert(api.returnToWorkflow(1));assert.deepEqual(returned,['dataset_export']);assert.equal(calls.length,2,'return is navigation only');})().catch(error=>{console.error(error);process.exitCode=1});
"""
        )

    def test_index_uses_one_history_owner_before_package_controller(self) -> None:
        index = (WEB / "index.html").read_text(encoding="utf-8")
        source = (WEB / "fusion_operation_history.js").read_text(encoding="utf-8")
        css = (WEB / "workbench.css").read_text(encoding="utf-8")
        self.assertEqual(index.count('id="fusion-package-history"'), 1)
        self.assertEqual(index.count('id="fusion-package-history-clear"'), 1)
        self.assertLess(
            index.index('/static/fusion_operation_history.js'),
            index.index('/static/fusion_package_center.js'),
        )
        self.assertIn("createOperationHistoryController", source)
        for forbidden in ("fetch(", "DOMContentLoaded", "localStorage"):
            self.assertNotIn(forbidden, source)
        history_css = "\n".join(
            line for line in css.splitlines() if "operation-history" in line or "package-history" in line
        )
        self.assertNotRegex(history_css, r"#[0-9a-fA-F]{3,8}\b")


if __name__ == "__main__":
    unittest.main()
