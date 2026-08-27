from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "src" / "auto_research" / "evidence" / "web" / "fusion_review.js"


class FusionEvidenceChatHistoryRuntimeTests(unittest.TestCase):
    def test_history_projection_cas_retry_and_librarian_memory_boolean(self) -> None:
        program = f"""
(async()=>{{
const fs=require('fs'),assert=require('assert');
const toggle={{checked:false}};
globalThis.document={{readyState:'loading',querySelector:s=>s==='#fusion-librarian-use-memory'?toggle:null,querySelectorAll:()=>[],addEventListener(){{}},documentElement:{{style:{{setProperty(){{}}}}}},body:{{dataset:{{}}}}}};
globalThis.localStorage={{getItem:()=>null,setItem(){{throw new Error('history must not use localStorage')}}}};
let revision=1,getCount=0,postBodies=[];
const thread=(answer='旧回答')=>({{schema_version:'evidence-chat-thread-v1',thread_uid:'ech_public',source_scope:'official',source_id:'official-v2',entity_type:'finding',entity_uid:'finding:1',title:'硬度结论',created_at:'2026-08-27T00:00:00Z',updated_at:'2026-08-27T00:00:00Z',messages:[{{role:'user',content:'旧问题'}},{{role:'assistant',content:answer,annotations:{{pages:[6],notes:['公开说明'],limitations:['只解释当前证据']}}}}]}});
const response=(payload,ok=true)=>({{ok,headers:{{get:()=>null}},json:async()=>payload}});
globalThis.fetch=async(url,options={{}})=>{{
  assert.equal(String(url),'/api/desktop/evidence-chat-history');
  if((options.method||'GET')==='GET'){{getCount+=1;return response({{schema_version:'evidence-chat-history-v1',revision,storage:'macos-preview-local-key-aes-256-gcm',threads:[thread()]}});}}
  const body=JSON.parse(options.body);postBodies.push(body);
  if(postBodies.length===1){{revision=2;return response({{code:'evidence_chat_history_revision_conflict',message:'conflict'}},false);}}
  assert.equal(body.expected_revision,2);revision=3;return response({{schema_version:'evidence-chat-history-v1',revision,storage:'macos-preview-local-key-aes-256-gcm',threads:[thread('新回答')]}});
}};
let source=fs.readFileSync({str(RUNTIME)!r},'utf8');
source=source.replace('globalThis.AutoResearchFusion=Object.freeze({{','globalThis.__historyTest={{loadEvidenceChatHistory,publicEvidenceHistory,queueEvidenceChatSave,librarianRequest,renderLibrarianPresentation,state}};globalThis.AutoResearchFusion=Object.freeze({{');
eval(source);const api=globalThis.__historyTest;
assert.equal(await api.loadEvidenceChatHistory(),1);assert.equal(api.state.evidenceChat.messages.length,0);assert.equal(api.state.evidenceChat.historyRevision,1);
const identity={{sourceScope:'official',sourceId:'official-v2',entityType:'finding',entityUid:'finding:1'}};
assert(api.queueEvidenceChatSave(identity,{{title:'硬度结论'}},[{{role:'user',content:'新问题'}},{{role:'assistant',content:'新回答',annotations:{{pages:[6],notes:['n'.repeat(1200)],limitations:['limit']}}}}]));
await api.state.evidenceChat.historySave;
assert.equal(getCount,2,'one initial GET and one conflict refresh');assert.equal(postBodies.length,2);assert.deepEqual(postBodies[1].thread.source_scope,'official');assert.deepEqual(postBodies[1].thread.entity_uid,'finding:1');assert.equal(postBodies[1].thread.messages[1].annotations.notes[0].length,1000);assert(!JSON.stringify(postBodies[1]).includes('path'));
let request=api.librarianRequest('问题');assert.equal(request.use_research_memory,false);assert(!('research_memories' in request));toggle.checked=true;request=api.librarianRequest('问题');assert.equal(request.use_research_memory,true);assert(!JSON.stringify(request).includes('已保存记忆正文'));
assert(!api.renderLibrarianPresentation({{research_memory_count:0}},[]).includes('本次使用'));
assert(api.renderLibrarianPresentation({{research_memory_count:2}},[]).includes('本次使用 2 条已核验记忆'));
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
"""
        result = subprocess.run(
            ["node", "-e", program],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_delete_is_current_identity_only_and_history_failure_is_non_blocking(self) -> None:
        source = RUNTIME.read_text(encoding="utf-8")
        self.assertIn('operation:"delete",expected_revision,identity:{source_scope:', source)
        self.assertNotIn('operation:"clear"', source[source.index("async function clearSelectedEvidenceThread"):source.index("function focusCurrentEvidenceFromChat")])
        self.assertIn("回答已完成，但本机历史保存暂不可用；你可以继续提问。", source)
        self.assertIn("queueEvidenceChatSave(identity,row", source)
        self.assertNotIn("localStorage", source[source.index("function storedEvidenceMessage"):source.index("function evidenceAnnotationsHTML")])


if __name__ == "__main__":
    unittest.main()
