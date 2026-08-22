from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "auto_research" / "evidence" / "web"


class FusionAISettingsRuntimeTests(unittest.TestCase):
    def test_readiness_custom_provider_and_bounded_verification(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert');
class Classes{{add(){{}}remove(){{}}toggle(){{}}contains(){{return false}}}}
class El{{constructor(dataset={{}}){{this.dataset=dataset;this.hidden=false;this.disabled=false;this.value='';this.textContent='';this.innerHTML='';this.classList=new Classes();this.attrs={{}};this.listeners={{}};}}addEventListener(k,f){{(this.listeners[k]??=[]).push(f)}}setAttribute(k,v){{this.attrs[k]=String(v)}}removeAttribute(k){{delete this.attrs[k]}}focus(){{globalThis.focused=this}}closest(){{return null}}}}
const names=['fusion-ai-settings-status','fusion-ai-provider','fusion-ai-models','fusion-ai-model-save','fusion-ai-key-save','fusion-ai-key-delete','fusion-ai-test','fusion-ai-credential-state','fusion-ai-test-plan','fusion-ai-key','fusion-ai-connection-state','fusion-ai-connection-reason','fusion-ai-harness-state','fusion-ai-harness-reason','fusion-ai-business-readiness','fusion-ai-custom-name','fusion-ai-custom-endpoint','fusion-ai-custom-save','fusion-ai-custom-delete','fusion-ai-custom-status','fusion-status-ai'];const ids={{}};for(const name of names)ids['#'+name]=new El();
const tasks=['extraction','analysis','librarian_planning','librarian_synthesis'].map(task=>new El({{fusionAiTask:task}})),customTasks=['extraction','analysis','librarian_planning','librarian_synthesis'].map(task=>new El({{fusionCustomTask:task}}));customTasks.forEach((input,index)=>input.value='custom-model-'+index);
globalThis.document={{readyState:'loading',documentElement:{{dataset:{{}},style:{{setProperty(){{}}}}}},body:{{dataset:{{view:'paper'}}}},querySelector:s=>ids[s]||null,querySelectorAll:s=>s==='[data-fusion-ai-task]'?tasks:s==='[data-fusion-custom-task]'?customTasks:[],addEventListener(){{}}}};globalThis.localStorage={{getItem:()=>null,setItem(){{}}}};globalThis.innerWidth=1440;
globalThis.AutoResearchAIConsent={{disclosureVersions:{{personal_suggestion:'personal-v1'}},ensure:()=>true,updateTrustedProviders:()=>true}};
const ready=state=>({{state,reason_code:state==='ready'?'ai_business_ready':'ai_business_verification_required',next_action:state==='ready'?'none':'test_business_capability'}});let personalState='verification_required',custom={{schema_version:'custom-ai-provider-v1',provider_id:'custom',revision:0,configured:false}},confirmValue=true,testFailure=false;globalThis.confirm=()=>confirmValue;const calls=[];
const response=payload=>({{ok:true,headers:{{get:()=>null}},json:async()=>payload}});
const failure=payload=>({{ok:false,status:422,headers:{{get:()=>null}},json:async()=>payload}});
globalThis.fetch=async(url,options={{}})=>{{url=String(url);calls.push([url,options.method||'GET',options.body]);
 if(url==='/api/desktop/ai/providers')return response({{schema_version:'ai-desktop-catalog-v1',providers:[{{provider_id:'deepseek',display_name:'DeepSeek',model_options:{{extraction:['e'],analysis:['a'],librarian_planning:['p'],librarian_synthesis:['s']}}}},{{provider_id:'custom',display_name:'Custom Lab',model_options:{{extraction:['ce'],analysis:['ca'],librarian_planning:['cp'],librarian_synthesis:['cs']}}}}],capability_test:{{provider_id:'deepseek',connection_maximum_model_calls:1,business_maximum_model_calls:{{literature_extraction:8,librarian:8,selected_evidence_chat:2,personal_suggestion:1}}}}}});
 if(url==='/api/desktop/ai/settings')return response({{schema_version:'ai-runtime-public-state-v1',provider_id:'deepseek',revision:7,task_models:{{extraction:'e',analysis:'a',librarian_planning:'p',librarian_synthesis:'s'}},readiness:{{schema_version:'ai-readiness-v1',provider_connection:{{state:'ready',reason_code:'ai_connection_ready',next_action:'none'}},harness:{{state:'ready',reason_code:'harness_runtime_ready',next_action:'none'}},businesses:{{literature_extraction:ready('ready'),librarian:ready('ready'),selected_evidence_chat:ready('ready'),personal_suggestion:ready(personalState)}}}}}});
 if(url==='/api/desktop/ai/custom-provider'&&(options.method||'GET')==='GET')return response(custom);
 if(url==='/api/desktop/ai/custom-provider'&&options.method==='POST'){{custom={{schema_version:'custom-ai-provider-v1',provider_id:'custom',revision:1,configured:true,display_name:'Lab Gateway',task_models:JSON.parse(options.body).task_models}};return response(custom);}}
 if(url==='/api/desktop/ai/custom-provider/1'&&options.method==='DELETE'){{custom={{schema_version:'custom-ai-provider-v1',provider_id:'custom',revision:2,configured:false}};return response(custom);}}
 if(url==='/api/desktop/ai/credentials/deepseek')return response({{schema_version:'ai-credential-status-v1',provider_id:'deepseek',configured:true,generation:2}});
 if(url==='/api/desktop/ai/providers/deepseek/test-actions'){{const body=JSON.parse(options.body);return response({{schema_version:'server-prepared-ai-action-v1',scope:'capability_test',provider_id:'deepseek',action_id:body.scope?'business-action':'connection-action',maximum_calls:body.scope?1:1,model:'e',display:'验证'}});}}
 if(url==='/api/desktop/ai/consents')return response({{schema_version:'ai-consent-v1',scope:'capability_test',nonce:'nonce'}});
 if(url==='/api/desktop/ai/providers/deepseek/test'){{if(testFailure)return failure({{schema_version:'desktop-ai-http-error-v1',code:'ai_runtime_verification_failed',cause_code:'ai_provider_response_invalid',message:'AI 提供商返回了空内容。',stage:'connection_verification',next_action:'check_provider_configuration',retryable:true}});personalState='ready';return response({{schema_version:'ai-capability-test-result-v1',status:'verified'}});}}
 if(url==='/api/desktop/ai/actions/personal_suggestion/prepare')return response({{schema_version:'server-prepared-ai-action-v1',scope:'personal_suggestion',provider_id:'deepseek',disclosure_version:'personal-v1',action_id:'personal-action',maximum_calls:1,model:'a',display:'实验预填'}});
 throw new Error('unexpected '+url);
}};
eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));const api=globalThis.AutoResearchFusion;
(async()=>{{
 assert(await api.loadAISettingsUI());assert.equal(ids['#fusion-ai-connection-state'].textContent,'可用');assert.equal(ids['#fusion-status-ai'].textContent,'AI 业务需验证');
 assert(await api.runAIVerification('personal_suggestion'));const businessPlan=calls.find(call=>call[0].endsWith('/test-actions')&&JSON.parse(call[2]).scope);assert.deepEqual(JSON.parse(businessPlan[2]),{{expected_revision:7,scope:'personal_suggestion'}});assert.equal(ids['#fusion-status-ai'].textContent,'AI 可用');
 ids['#fusion-ai-custom-name'].value='Lab Gateway';ids['#fusion-ai-custom-endpoint'].value='https://lab.example/v1/chat/completions';customTasks.forEach((input,index)=>input.value='custom-model-'+index);assert(await api.saveCustomProvider());const customPost=calls.find(call=>call[0]==='/api/desktop/ai/custom-provider'&&call[1]==='POST');assert.deepEqual(Object.keys(JSON.parse(customPost[2])).sort(),['chat_endpoint','display_name','expected_revision','task_models']);assert(!ids['#fusion-ai-custom-status'].textContent.includes('https://'));
 assert(await api.deleteCustomProvider());assert(calls.some(call=>call[0]==='/api/desktop/ai/custom-provider/1'&&call[1]==='DELETE'));
 personalState='verification_required';api.state.aiSettings.readiness.businesses.personal_suggestion=ready('verification_required');confirmValue=false;const before=calls.length,authorization=await api.preparedAuthorization('personal_suggestion',{{import_id:'personal_import_abcdefghijklmnop',sheet_index:0}});assert.equal(authorization,null);assert.equal(calls.slice(before).filter(call=>call[0].includes('/actions/personal_suggestion/prepare')).length,0,'cancelled readiness must send zero business prepare');assert(ids['#fusion-ai-settings-status'].textContent.includes('已取消验证'));
 confirmValue=true;testFailure=true;assert.equal(await api.runAIVerification(null),false);assert(ids['#fusion-ai-settings-status'].textContent.includes('AI 提供商返回了空内容。'));assert(ids['#fusion-ai-settings-status'].textContent.includes('核对提供商账户'));ids['#fusion-ai-key'].value='new-private-key';assert.equal(await api.saveAIKey(),false);assert(ids['#fusion-ai-settings-status'].textContent.includes('AI 提供商返回了空内容。'));
}})().catch(error=>{{console.error(error);process.exitCode=1}});
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
