"""Exercise the pure production review contract through its public interface."""
from pathlib import Path
import subprocess

WEB = Path(__file__).resolve().parents[1] / "auto_research/evidence/web"


def test_review_contract_projection_correction_and_bounds():
    program = r"""
const assert = require('node:assert/strict');
for (const name of ['document', 'localStorage', 'fetch']) {
  Object.defineProperty(globalThis, name, {get() {throw new Error('unexpected side effect '+name);}});
}
require(process.argv[1]);
const api = AutoResearchReviewQueueContract.create({
  cleanText: (value, max) => String(value ?? '').slice(0, max),
  reviewQueueRoute: '/api/desktop/review-queue',
  safeError: (code, message) => Object.assign(new Error(message), {code}),
  fieldLabel: key => key,
});
const token = 'rq_'+'A'.repeat(40);
const row = {
  entity_type:'figure', paper_uid:'paper_'+'a'.repeat(32), review_token:token,
  expires_at:1999999999, source_scope:'workspace', source_id:'workspace',
  paper:{title:'Title', doi:''}, candidate:{caption:'<caption>', tags:Array(40).fill('tag'),
    variables:{a:{b:{c:{d:'too deep'}}}}, image_path:'/private/a.png', reviewer:'secret'},
  alternate:null, scores:{agreement:0,factuality:70,completeness:100,evidence:100,overall:69.5},
  allowed_operations:{approve:{available:true},reject:{available:false},correct:{available:true}},
  preview:{image_url:'/api/desktop/review-queue/'+token+'/image'},
};
const dto = {schema_version:'review-queue-v1',source_scope:'workspace',source_id:'workspace',total:1,items:[row]};
const before = JSON.stringify(dto), projected = api.project(dto), item = projected.items[0];
assert.equal(JSON.stringify(dto), before, 'projection must not mutate source');
assert.equal(item.scores.overall, .695);
assert.equal(item.candidate.tags.length, 32);
assert.equal(item.candidate.variables.a.b.c.d, null);
assert.equal(item.candidate.image_path, undefined);
assert.equal(item.candidate.reviewer, undefined);
assert.equal(item.allowedOperations.reject, false);
assert.deepEqual(api.editableFields(item), ['caption', 'variables', 'tags']);
assert.deepEqual(api.correctionPayload(item, [
  {field:'caption',value:'Corrected',complex:false},
  {field:'tags',value:'["checked"]',complex:true},
]), {caption:'Corrected',tags:['checked']});
for (const entries of [[],[{field:'image_path',value:'x'}],[{field:'label',value:'not provided'}],
    [{field:'tags',value:'[',complex:true}]]) {
  assert.throws(() => api.correctionPayload(item, entries), {code:'review_queue_invalid'});
}
for (const entity_type of ['unknown', '__proto__', 'constructor', 'toString']) {
  assert.equal(api.project({...dto,items:[{...row,entity_type}]}), null);
}
assert.equal(api.project({...dto,schema_version:'future'}), null);
assert.equal(api.project({...dto,total:2}), null);
assert.equal(api.project({...dto,total:501,items:Array(501).fill(row)}), null);
assert.equal(api.project({...dto,total:500,items:Array(500).fill(row)}).items.length, 500);
for (const overall of [-1,101,NaN,Infinity,'70']) {
  assert.equal(api.project({...dto,items:[{...row,scores:{...row.scores,overall}}]}), null);
}
for (const image_url of ['https://example.org/image', '/api/desktop/review-queue/rq_other/image',
    '/api/desktop/review-queue/'+token+'/image?extra=true']) {
  assert.equal(api.project({...dto,items:[{...row,preview:{image_url}}]}), null);
}
assert.equal(api.project({...dto,items:[{...row,entity_type:'item'}]}), null);
assert.equal(api.project({...dto,items:[{...row,source_scope:'private'}]}), null);
assert.equal(api.project({...dto,items:[{...row,expires_at:Infinity}]}), null);
"""
    result = subprocess.run(
        ["node", "-e", program, str(WEB / "review_queue_contract.js")],
        text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
