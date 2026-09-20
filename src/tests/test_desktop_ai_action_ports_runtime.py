from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FUSION_JS = PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web" / "fusion_review.js"


class DesktopAIActionPortsRuntimeTests(unittest.TestCase):
    def test_fusion_prepared_authorization_uses_jobs_for_all_scopes(self) -> None:
        script = r'''
const fs=require("fs"),assert=require("assert"),source=fs.readFileSync(process.argv[1],"utf8");
const preparedStart=source.indexOf("  async function preparedAuthorization"),preparedEnd=source.indexOf("  function validAIExecutionJob",preparedStart),executeStart=source.indexOf("  async function executePrepared",preparedEnd),executeEnd=source.indexOf("  async function applyLiteratureResult",executeStart);assert(preparedStart>0&&preparedEnd>preparedStart&&executeStart>preparedEnd&&executeEnd>executeStart);
const AI_SCOPES=new Set(["librarian","literature_extraction","personal_suggestion","selected_evidence_chat"]),AI_CALL_LIMITS={librarian:8,literature_extraction:512,personal_suggestion:1,selected_evidence_chat:2},ROUTES={consent:"/api/desktop/ai/consents"},state={aiSettings:{readiness:{}},literatureTask:{}};
const cleanText=(value,limit=8000)=>String(value??"").trim().slice(0,limit);function safeError(code,message,detail={}){const error=new Error(message);error.code=code;error.stage=detail.stage;error.nextAction=detail.next_action;return error;}
function aiRoute(scope,action){if(!AI_SCOPES.has(scope)||!["prepare","execute","execute-jobs"].includes(action))throw safeError("ai_action_scope_invalid","unsupported");return `/api/desktop/ai/actions/${scope}/${action}`;}
function publicAIReadiness(){return{providerConnection:{state:"ready"},harness:{state:"ready"},businesses:{librarian:{state:"ready"}}};}async function ensureAIReadiness(scope){return AI_SCOPES.has(scope);}async function loadAIContext(){return{provider_id:"deepseek",label:"DeepSeek"};}
function validAIExecutionJob(job,scope){return job?.schema_version==="ai-execution-job-v1"&&job.scope===scope&&/^ai_job_[A-Za-z0-9_-]{24,160}$/.test(job.job_id)&&["queued","running","completed","failed"].includes(job.status)&&Array.isArray(job.events);}function rememberLiteratureJob(){}function projectAIActivity(){}
const calls=[],limits={librarian:2,literature_extraction:3,personal_suggestion:1,selected_evidence_chat:2};
async function request(url,options={}){const body=options.body?JSON.parse(options.body):null;calls.push({url,method:options.method||"GET",body});const scope=String(url).split("/")[5];if(url.endsWith("/prepare"))return{schema_version:"server-prepared-ai-action-v1",scope,provider_id:"deepseek",disclosure_version:`v-${scope}`,action_id:`action-${scope}`,maximum_calls:limits[scope],model:["model"],display:`display-${scope}`};if(url==="/api/desktop/ai/consents")return{schema_version:"ai-consent-v1",scope:String(body.action_id).slice(7),nonce:`nonce-${body.action_id}`};if(url.endsWith("/execute-jobs"))return{schema_version:"ai-execution-job-v1",scope,job_id:`ai_job_${scope.padEnd(24,"x")}`,status:"completed",events:[],result:{scope,ok:true}};throw new Error(`unexpected ${url}`);}
globalThis.AutoResearchAIConsent={disclosureVersions:Object.fromEntries([...AI_SCOPES].map(scope=>[scope,`v-${scope}`])),accepted:()=>true,disclosureSummary:()=>"",remember:()=>true};let approve=true;globalThis.confirm=()=>approve;
eval(source.slice(preparedStart,preparedEnd)+"\n"+source.slice(executeStart,executeEnd));
(async()=>{
 for(const scope of AI_SCOPES){const domain={scope,payload:`request-${scope}`},authorization=await preparedAuthorization(scope,domain);assert(authorization);const result=await executePrepared(scope,authorization);assert.deepEqual(result,{scope,ok:true});}
 for(const scope of AI_SCOPES){const scoped=calls.filter(call=>call.url.includes(`/actions/${scope}/`));assert.deepEqual(scoped.map(call=>call.url),[`/api/desktop/ai/actions/${scope}/prepare`,`/api/desktop/ai/actions/${scope}/execute-jobs`]);assert.deepEqual(scoped[0].body,{scope,payload:`request-${scope}`});assert.deepEqual(scoped[1].body,{action_id:`action-${scope}`,consent_nonce:`nonce-action-${scope}`});}
 assert.equal(calls.filter(call=>call.url==="/api/desktop/ai/consents").length,4);assert(!calls.some(call=>/\/execute$/.test(call.url)),"legacy synchronous execute route must remain unused");
 const beforeUnknown=calls.length;assert.equal(await preparedAuthorization("../../unknown",{}),null);await assert.rejects(()=>executePrepared("../../unknown",{actionId:"untrusted",nonce:"untrusted"}),error=>error.code==="ai_action_scope_invalid");assert.equal(calls.length,beforeUnknown,"unknown scope/action must not request");
 approve=false;const beforeCancel=calls.length,denied=await preparedAuthorization("personal_suggestion",{cancel:true});assert.equal(denied,null);const cancelled=calls.slice(beforeCancel);assert.deepEqual(cancelled.map(call=>call.url),["/api/desktop/ai/actions/personal_suggestion/prepare"],"cancel may prepare the public summary but must not issue consent or execute");
})().catch(error=>{console.error(error);process.exit(1);});
'''
        completed = subprocess.run(
            ["node", "-e", script, str(FUSION_JS)],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=8,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


if __name__ == "__main__":
    unittest.main()
