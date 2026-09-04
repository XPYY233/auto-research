from pathlib import Path
import subprocess
import unittest


WEB = Path(__file__).resolve().parents[1] / "auto_research/evidence/web"
HARNESS = r"""
const fs=require('fs'),assert=require('assert'),source=fs.readFileSync(process.argv[1],'utf8');
function section(start,end){const a=source.indexOf(start),b=source.indexOf(end,a);assert(a>=0&&b>a);return source.slice(a,b)}
const state={librarianBusy:false,librarianAction:0,view:'search',searchMode:'librarian',librarianMessages:[],librarianResults:[],librarianArticles:[],librarianSessions:[],librarianConversationId:null,librarianResearch:null,librarianHistoryBackend:'desktop-secure',aiSettings:{},literatureTask:{}};
const nodes=new Map(),q=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',checked:false,disabled:false});return nodes.get(id)};
const cleanText=(value,limit=8000)=>String(value??'').trim().slice(0,limit),newConversationId=()=> 'conversation-stable';
const LIBRARIAN_HISTORY_LIMIT=20,LIBRARIAN_HISTORY_RETENTION=30*24*60*60*1000;
const librarianSessionTitle=messages=>messages.find(m=>m.role==='user')?.content||'对话',publicEvidence=()=>null,storedLibrarianArticle=()=>null;
let persisted=[];function renderLibrarianHistory(){}function queueLibrarianHistorySave(){persisted=JSON.parse(JSON.stringify(state.librarianSessions))}
function renderLibrarianConversation(){}function renderResearchMemories(){}function aiProgress(){}function aiFailureStage(){return 'harness_execute'}function aiErrorCopy(_error,message){return message}
function harnessUnavailableMessage(error){return `${error.code} · 本次回答未完成`}
function renderLibrarianFinal(result){assert.equal(result.librarian_core_version,'librarian-v3');state.librarianMessages.push({role:'assistant',content:result.answer});saveCurrentLibrarianSession()}
const AI_SCOPES=new Set(['librarian']),AI_CALL_LIMITS={librarian:8},ROUTES={consent:'/api/desktop/ai/consents'};
function aiRoute(scope,action){assert(AI_SCOPES.has(scope));return `/api/desktop/ai/actions/${scope}/${action}`}
function publicAIReadiness(){return {providerConnection:{state:'ready'},harness:{state:'ready'},businesses:{librarian:{state:'ready'}}}}
async function ensureAIReadiness(){return true}async function loadAIContext(){return {provider_id:'deepseek'}}
function safeError(code,message,detail={}){return Object.assign(new Error(message),{code,stage:detail.stage,nextAction:detail.next_action})}
function rememberLiteratureJob(){}function projectAIActivity(){}
globalThis.AutoResearchAIConsent={disclosureVersions:{librarian:'disclosure-v1'},accepted:()=>true,remember:()=>true,disclosureSummary:()=>''};
let approve=true,mode='failed',actionNumber=0,onExecute=null;globalThis.confirm=()=>approve;const calls=[];
async function request(url,options={}){const body=options.body?JSON.parse(options.body):null;calls.push({url,body});
 if(url.endsWith('/prepare'))return {schema_version:'server-prepared-ai-action-v1',scope:'librarian',provider_id:'deepseek',disclosure_version:'disclosure-v1',action_id:`action-${++actionNumber}`,maximum_calls:1,model:'Flash',display:'回答问题'};
 if(url===ROUTES.consent)return {schema_version:'ai-consent-v1',scope:'librarian',nonce:`nonce-${body.action_id}`};
 if(url.endsWith('/execute-jobs')){assert.deepEqual(body,{action_id:`action-${actionNumber}`,consent_nonce:`nonce-action-${actionNumber}`});if(onExecute)onExecute();return {schema_version:'ai-execution-job-v1',scope:'librarian',job_id:'ai_job_'+'a'.repeat(24),events:[],status:mode==='failed'?'failed':'completed',error:{code:'harness_invalid',stage:'harness_execute',next_action:'retry_same_request'},result:{librarian_core_version:'librarian-v3',answer:'已核验回答'}}}
 throw Error('unexpected '+url);
}
eval(section('  function normalizeLibrarianSession(', '  function renderLibrarianHistory('));
eval(section('  function saveCurrentLibrarianSession(', '  async function loadLibrarianHistory('));
eval(section('  function librarianConversationEvidence(', '  function librarianRefs('));
eval(section('  async function preparedAuthorization(', '  function literaturePaperKey('));
eval(section('  async function executePrepared(', '  function applyLiteratureResult('));
eval(section('  async function submitLibrarian(', '  function cancelLibrarianRequest('));
"""


class FusionLibrarianRetryRuntimeTests(unittest.TestCase):
    def run_node(self, body):
        result = subprocess.run(
            ["node", "-e", HARNESS + body, str(WEB / "fusion_review.js")],
            capture_output=True, text=True, timeout=8,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_retry_keeps_visible_record_and_draft_but_not_outbound_failure(self):
        self.run_node(r"""
(async()=>{
q('#fusion-librarian-question').value='300°C 辐照温度与硬度';
await submitLibrarian();assert.equal(state.librarianBusy,false);assert.equal(state.librarianMessages.at(-1).status,'failed');assert(q('#fusion-librarian-status').textContent.includes('harness_invalid'));
assert.equal(q('#fusion-librarian-question').value,'300°C 辐照温度与硬度','failed execution preserves the retry draft');
assert.equal(persisted[0].messages.at(-1).status,'failed');
// Rehydrate from the exact encrypted-history payload, without losing the marker.
state.librarianMessages=normalizeLibrarianSession(persisted[0]).messages;mode='success';
await submitLibrarian();const prepares=calls.filter(c=>c.url.endsWith('/prepare'));
assert.equal(prepares.length,2);assert.equal(prepares[1].body.conversation_id,prepares[0].body.conversation_id);
assert(prepares[1].body.history.every(m=>!m.content.includes('harness_invalid')));assert.deepEqual(prepares[1].body.history,[{role:'user',content:'300°C 辐照温度与硬度'}]);
assert(state.librarianMessages.some(m=>m.status==='failed'),'failure stays visible in the local transcript');assert.equal(q('#fusion-librarian-question').value,'');
assert.equal(calls.filter(c=>c.url.endsWith('/execute-jobs')).length,2);assert(!calls.some(c=>c.url.endsWith('/execute')));
q('#fusion-librarian-question').value='保留取消草稿';approve=false;const before=calls.length;await submitLibrarian();assert.equal(q('#fusion-librarian-question').value,'保留取消草稿');assert.deepEqual(calls.slice(before).map(c=>c.url),['/api/desktop/ai/actions/librarian/prepare']);
approve=true;onExecute=()=>{q('#fusion-librarian-question').value='执行过程中编辑的新草稿'};await submitLibrarian();assert.equal(q('#fusion-librarian-question').value,'执行过程中编辑的新草稿');
})().catch(error=>{console.error(error);process.exitCode=1});
""")

    def test_outbound_history_limits_and_retired_state_exclusion_preserve_local_text(self):
        self.run_node(r"""
state.librarianResearch={state:{legacy:'secret-state'},token:'old-token'};
state.librarianMessages=Array.from({length:12},(_,i)=>({role:i%2?'assistant':'user',content:String(i)+'😀'.repeat(6500)}));
state.librarianMessages.push({role:'assistant',content:'harness_invalid · 旧失败记录'},{role:'assistant',content:'新失败记录',status:'failed'});
const original=JSON.stringify(state.librarianMessages),payload=librarianRequest('继续追问');
assert.equal(payload.history.length,8);assert(payload.history.every(m=>Array.from(m.content).length<=6000));assert(payload.history.every(m=>Object.keys(m).sort().join(',')==='content,role'));assert(payload.history.every(m=>!m.content.includes('失败记录')));
assert(!('research_state' in payload));assert(!('state_token' in payload));assert(!('research_token' in payload));assert(!('conversation_evidence' in payload));assert.equal(JSON.stringify(state.librarianMessages),original);
// JSON escaping is part of the real 256 KiB HTTP body budget, not just JS length.
state.librarianMessages=Array.from({length:8},()=>({role:'assistant',content:'x'+String.fromCharCode(1).repeat(5999)}));
const escaped=librarianRequest('问题');assert(new TextEncoder().encode(JSON.stringify(escaped)).length<=256*1024);assert(escaped.history.length<8);assert.equal(state.librarianMessages.length,8);
""")

    def test_short_successful_history_survives_and_failure_marker_is_local_only(self):
        self.run_node(r"""
state.librarianMessages=[{role:'user',content:'已完成的问题'},{role:'assistant',content:'已核验科学回答'},{role:'assistant',content:'本次 AI 请求未完成；精确检索和本机数据不受影响。'},{role:'assistant',content:'失败但不删除',status:'failed'}];
const payload=librarianRequest('下一问');assert.deepEqual(payload.history,[{role:'user',content:'已完成的问题'},{role:'assistant',content:'已核验科学回答'}]);
assert.equal(state.librarianMessages.length,4);assert(!JSON.stringify(payload).includes('status'));assert.equal(payload.use_research_memory,false);
""")
