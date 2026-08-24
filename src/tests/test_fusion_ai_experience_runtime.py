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
controller.update('literature_extraction',{{stage:'completed',state:'success'}});assert.equal(valueNode.textContent,'100%');assert.equal(progress.attrs['aria-busy'],'false');
const conversation=new El();controller.renderConversation(conversation,[{{role:'user',content:'<img src=x onerror=1>'}},{{role:'assistant',content:'只按文本显示'}}],{{assistantLabel:'证据 AI'}});
assert.equal(conversation.children.length,2);assert.equal(conversation.children[0].children[1].children[1].textContent,'<img src=x onerror=1>');assert.equal(conversation.children[1].children[1].children[0].textContent,'证据 AI');
"""
        result = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
