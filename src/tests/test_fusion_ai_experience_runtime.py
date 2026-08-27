from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "src" / "auto_research" / "evidence" / "web" / "fusion_ai_experience.js"


class FusionAIExperienceRuntimeTests(unittest.TestCase):
    def test_real_stage_progress_and_plain_text_conversation_projection(self) -> None:
        program = f"""
const assert=require('assert');
class Classes{{constructor(){{this.values=new Set();}}toggle(name,on){{on?this.values.add(name):this.values.delete(name);}}}}
class El{{
 constructor(){{this.children=[];this.dataset={{}};this.hidden=false;this.style={{}};this.textContent='';this.className='';this.attrs={{}};this.classList=new Classes();this.scrollTop=0;this.scrollHeight=100;}}
 append(...values){{this.children.push(...values);}}
 replaceChildren(...values){{this.children=[...values];}}
 setAttribute(name,value){{this.attrs[name]=String(value);}}
 querySelector(selector){{return this.map?.[selector]||null;}}
 querySelectorAll(selector){{return selector==='[data-ai-stage]'?(this.stages||[]):[];}}
}}
const label=new El(),detail=new El(),bar=new El(),valueNode=new El(),track=new El(),activity=new El(),elapsed=new El();bar.parentElement=track;
const stages=['prepare','initial_focus','coverage_verification','third_review','publishing'].map(name=>{{const node=new El();node.dataset.aiStage=name;return node;}});
const progress=new El();progress.map={{'[data-ai-progress-label]':label,'[data-ai-progress-detail]':detail,'[data-ai-progress-bar]':bar,'[data-ai-progress-value]':valueNode,'[data-ai-activity]':activity,'[data-ai-elapsed]':elapsed}};progress.stages=stages;
const document={{querySelector:selector=>selector==='#fusion-literature-progress'?progress:null,createElement:()=>new El()}};
globalThis.document=document;globalThis.requestAnimationFrame=callback=>callback();
eval(require('fs').readFileSync({str(RUNTIME)!r},'utf8'));
const Controller=globalThis.AutoResearchAIExperience.AIExperienceController,controller=new Controller(document);
assert(controller.begin('literature_extraction'));assert.equal(elapsed.textContent,'0 秒');
assert(controller.update('literature_extraction',{{stage:'coverage_verification',state:'running',detail:'真实来源页核验'}}));
assert.equal(label.textContent,'核对覆盖率与来源页');assert.equal(valueNode.textContent,'58%');assert.equal(bar.style.width,'58%');assert.equal(progress.attrs['aria-busy'],'true');assert.equal(detail.textContent,'真实来源页核验');
assert(stages.find(node=>node.dataset.aiStage==='coverage_verification').classList.values.has('active'));
assert(controller.activity('literature_extraction',{{schema_version:'ai-activity-event-v1',job_id:'ai_job_abcdefghijklmnopqrstuvwxyz',sequence:1,code:'harness_tool_started',stage:'tool',label:'正在调用只读证据工具',detail:'核验引用'}}));
assert.equal(activity.children.length,1);assert.equal(activity.children[0].children[1].children[0].textContent,'正在调用只读证据工具');assert.equal(activity.children[0].children[1].children[1].textContent,'核验引用');
controller.activity('literature_extraction',{{schema_version:'ai-activity-event-v1',job_id:'ai_job_abcdefghijklmnopqrstuvwxyz',sequence:1,code:'harness_tool_started',stage:'tool',label:'重复'}});assert.equal(activity.children.length,1);
assert(controller.activity('literature_extraction',{{schema_version:'ai-activity-event-v1',job_id:'ai_job_abcdefghijklmnopqrstuvwxyz',sequence:2,code:'literature_third_review',stage:'third_review',label:'处理低置信与冲突结果',detail:''}}));assert.equal(valueNode.textContent,'84%');assert.equal(label.textContent,'处理低置信与冲突结果');
controller.update('literature_extraction',{{stage:'completed',state:'success'}});assert.equal(valueNode.textContent,'100%');assert.equal(progress.attrs['aria-busy'],'false');
const conversation=new El();controller.renderConversation(conversation,[{{role:'user',content:'<img src=x onerror=1>'}},{{role:'assistant',content:'只按文本显示'}}],{{assistantLabel:'证据 AI'}});
assert.equal(conversation.children.length,2);assert.equal(conversation.children[0].children[1].children[1].textContent,'<img src=x onerror=1>');assert.equal(conversation.children[1].children[1].children[0].textContent,'证据 AI');
const emptyConversation=new El();controller.renderConversation(emptyConversation,[],{{emptyTitle:'围绕当前证据连续追问',emptyText:'选择证据后开始。'}});assert.equal(emptyConversation.children.length,1);assert.equal(emptyConversation.children[0].className,'fusion-chat-welcome');assert.equal(emptyConversation.children[0].children[0].textContent,'围绕当前证据连续追问');
"""
        result = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fusion_aggregates_repeated_safe_events_into_application_phases(self) -> None:
        fusion = ROOT / "src" / "auto_research" / "evidence" / "web" / "fusion_review.js"
        program = f"""
const assert=require('assert'),fs=require('fs');
class Classes{{constructor(){{this.values=new Set();}}toggle(name,on){{on?this.values.add(name):this.values.delete(name);}}add(name){{this.values.add(name)}}remove(name){{this.values.delete(name)}}}}
class El{{constructor(){{this.children=[];this.dataset={{}};this.hidden=false;this.style={{}};this.textContent='';this.className='';this.attrs={{}};this.scrollTop=0;this.scrollHeight=100;}}append(...values){{this.children.push(...values)}}replaceChildren(...values){{this.children=[...values]}}setAttribute(name,value){{this.attrs[name]=String(value)}}querySelector(selector){{return this.map?.[selector]||null}}querySelectorAll(){{return []}}}}
const activity=new El(),elapsed=new El(),label=new El(),detail=new El(),bar=new El(),valueNode=new El(),track=new El();bar.parentElement=track;const progress=new El();progress.map={{'[data-ai-activity]':activity,'[data-ai-elapsed]':elapsed,'[data-ai-progress-label]':label,'[data-ai-progress-detail]':detail,'[data-ai-progress-bar]':bar,'[data-ai-progress-value]':valueNode}};
globalThis.document={{readyState:'loading',documentElement:{{dataset:{{}},style:{{setProperty(){{}}}}}},body:{{dataset:{{}}}},querySelector:selector=>selector==='#fusion-librarian-progress'?progress:null,querySelectorAll:()=>[],createElement:()=>new El(),addEventListener(){{}}}};globalThis.localStorage={{getItem:()=>null,setItem(){{}}}};globalThis.requestAnimationFrame=callback=>callback();
eval(fs.readFileSync({str(RUNTIME)!r},'utf8'));eval(fs.readFileSync({str(fusion)!r},'utf8'));const api=globalThis.AutoResearchFusion,job='ai_job_abcdefghijklmnopqrstuvwxyz';
const event=(sequence,code,extra={{}})=>({{schema_version:'ai-activity-event-v1',job_id:job,sequence,code,stage:'safe',label:'底层事件',elapsed_ms:sequence*1000,...extra}});
for(const value of [event(1,'execution_started'),event(2,'provider_request_started',{{call_index:1,call_limit:8}}),event(3,'provider_response_received',{{call_index:1,call_limit:8}}),event(4,'harness_tool_started',{{tool:'exact_search'}}),event(5,'harness_tool_completed',{{tool:'exact_search'}}),event(6,'harness_tool_started',{{tool:'federated_search'}}),event(7,'harness_tool_completed',{{tool:'federated_search'}}),event(8,'harness_tool_started',{{tool:'citation_verify'}}),event(9,'harness_tool_completed',{{tool:'citation_verify'}}),event(10,'provider_request_started',{{call_index:2,call_limit:8}}),event(11,'provider_response_received',{{call_index:2,call_limit:8}}),event(12,'result_validating'),event(13,'execution_completed')])assert(api.projectAIActivity('librarian',value));
assert.equal(activity.children.length,5,'repeated transport/tool events collapse to five application phases');assert.deepEqual(activity.children.map(node=>node.dataset.aiActivityPhase),['understand','retrieve','verify','organize','complete']);const text=activity.children.map(node=>node.children[1].children.map(child=>child.textContent).join(' ')).join('\\n');for(const marker of ['理解问题','检索证据','核验引用','组织回答','完成','模型调用 2/8','只读工具 2 次'])assert(text.includes(marker),marker);for(const forbidden of ['模型已返回下一步','只读证据工具已返回','底层事件'])assert(!text.includes(forbidden),forbidden);
const failed='ai_job_failure_abcdefghijklmnop';assert(api.projectAIActivity('librarian',{{...event(1,'execution_started'),job_id:failed}}));assert(api.projectAIActivity('librarian',{{...event(2,'execution_failed'),job_id:failed}}));assert.equal(activity.children.length,1);const failureText=activity.children[0].children[1].children.map(child=>child.textContent).join(' ');assert.equal((failureText.match(/未完成/g)||[]).length,1,'failure is presented once');
"""
        result = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
