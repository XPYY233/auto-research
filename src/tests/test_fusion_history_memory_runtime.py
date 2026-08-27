from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "src" / "auto_research" / "evidence" / "web" / "fusion_review.js"


class FusionHistoryMemoryRuntimeTests(unittest.TestCase):
    def test_history_restore_save_search_and_user_approved_memory(self) -> None:
        program = f"""
(async()=>{{
const fs=require('fs'),assert=require('assert');
class El{{constructor(){{this.hidden=false;this.disabled=false;this.textContent='';this.innerHTML='';this.value='';this.title='';this.dataset={{}};this.listeners={{}};this.classList={{toggle(){{}},add(){{}},remove(){{}}}};}}addEventListener(name,fn){{this.listeners[name]=fn}}setAttribute(){{}}querySelectorAll(){{return []}}focus(){{}}}}
const ids={{}};for(const id of ['fusion-librarian-history','fusion-librarian-history-privacy','fusion-librarian-history-clear','fusion-librarian-stage','fusion-librarian-question','fusion-librarian-status','fusion-librarian-output','fusion-librarian-results-body','fusion-librarian-results-count','fusion-librarian-results','fusion-librarian-results-toggle','fusion-librarian-panel','fusion-research-memory-list','fusion-research-memory-status','fusion-research-memory-clear','fusion-librarian-remember','fusion-librarian-current-session'])ids['#'+id]=new El();
globalThis.document={{readyState:'loading',querySelector:s=>ids[s]||null,querySelectorAll:()=>[],addEventListener(){{}},createElement:()=>new El(),documentElement:{{dataset:{{}},style:{{setProperty(){{}}}}}},body:{{dataset:{{view:'search'}}}}}};let localHistoryWrites=0;globalThis.localStorage={{getItem:()=>null,setItem(){{localHistoryWrites+=1;}}}};let confirmations=[];globalThis.confirm=message=>{{confirmations.push(String(message));return true;}};
let savedHistory=null,historyCleared=false,memoryRevision=0,memoryItems=[];const headers={{get:()=>null}};
globalThis.fetch=async(url,options={{}})=>{{
 url=String(url);const method=options.method||'GET';
 if(url==='/api/desktop/librarian-history'&&method==='GET')return{{ok:true,headers,json:async()=>({{storage:'macos-preview-local-key-aes-256-gcm',sessions:[{{id:'old-session',title:'旧对话',created_at:'2026-08-24T01:00:00Z',updated_at:new Date().toISOString(),messages:[{{role:'user',content:'旧问题'}},{{role:'assistant',content:'旧回答'}}],results:[],articles:[]}}]}})}};
 if(url==='/api/desktop/librarian-history'&&method==='POST'){{const body=JSON.parse(options.body);if(body.action==='clear'){{assert.deepEqual(body,{{action:'clear'}});historyCleared=true;savedHistory=[];return{{ok:true,headers,json:async()=>({{cleared:true}})}};}}savedHistory=body.sessions;return{{ok:true,headers,json:async()=>({{ok:true}})}};}}
 if(url==='/api/desktop/research-memories'&&method==='GET')return{{ok:true,headers,json:async()=>({{schema_version:'research-memory-list-v1',revision:memoryRevision,storage:'macos-preview-local-key-aes-256-gcm',items:memoryItems}})}};
 if(url==='/api/desktop/research-memories'&&method==='POST'){{const body=JSON.parse(options.body);assert.equal(body.expected_revision,memoryRevision);if(body.action==='create'){{memoryRevision+=1;memoryItems=[{{schema_version:'research-memory-item-v1',memory_uid:'mem_runtime',title:body.item.title,content:body.item.content,source_refs:body.item.source_refs,approval:'user_approved',origin:'assistant_suggested',created_at:new Date().toISOString(),updated_at:new Date().toISOString()}}];}}return{{ok:true,headers,json:async()=>({{schema_version:'research-memory-list-v1',revision:memoryRevision,storage:'macos-preview-local-key-aes-256-gcm',items:memoryItems}})}};}}
 throw new Error('unexpected '+method+' '+url);
}};
eval(fs.readFileSync({str(RUNTIME)!r},'utf8'));const api=globalThis.AutoResearchFusion;
assert.equal(await api.loadLibrarianHistory(),1);assert(api.restoreLibrarianSession('old-session'));assert.equal(api.state.librarianMessages[0].content,'旧问题');
api.startNewLibrarianConversation({{save:false}});api.state.librarianConversationId='new-session';api.state.librarianMessages=[{{role:'user',content:'新问题'}},{{role:'assistant',content:'带引用的新回答'}}];api.state.librarianResults=[{{type:'finding',title:'硬度结论',sourceScope:'official',sourceId:'official-v2',entityUid:'finding:1',page:6,doi:'10.1000/example'}}];assert(api.saveCurrentLibrarianSession());await api.state.librarianHistorySave;assert(savedHistory.some(session=>session.id==='new-session'));
assert.equal(await api.loadResearchMemories(),0);api.state.researchMemoryCandidate={{conversation_id:'new-session',generation:1,title:'新问题',content:'带引用的新回答',source_refs:[{{source_scope:'official',source_id:'official-v2',entity_type:'finding',entity_uid:'finding:1',page:6,title:'硬度结论',doi:'10.1000/example'}}]}};api.renderResearchMemories();assert.equal(ids['#fusion-librarian-remember'].disabled,false);assert(await api.rememberCurrentResearch());assert.equal(api.state.researchMemories.length,1);assert.equal(api.state.researchMemories[0].approval,'user_approved');assert.equal(api.state.researchMemories[0].source_refs[0].entity_uid,'finding:1');assert.equal(api.state.researchMemories[0].content,'带引用的新回答');
api.state.researchMemoryCandidate={{conversation_id:'new-session',generation:1,title:'旧问题',content:'旧成功回答',source_refs:[{{source_scope:'official',source_id:'official-v2',entity_type:'finding',entity_uid:'finding:1',page:6,title:'硬度结论'}}]}};api.clearResearchMemoryCandidate();assert.equal(ids['#fusion-librarian-remember'].disabled,true);assert.equal(await api.rememberCurrentResearch(),false);
assert(await api.clearLibrarianHistory());assert.equal(historyCleared,true);assert.equal(api.state.librarianSessions.length,0);assert(ids['#fusion-librarian-history-privacy'].textContent.includes('专用加密密钥已删除'));assert(confirmations.some(message=>message.includes('研究记忆')&&message.includes('科学数据库')));assert.equal(localHistoryWrites,0,'secure history must not fall back to localStorage');
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
"""
        result = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_initialization_and_controls_are_wired_once(self) -> None:
        source = RUNTIME.read_text(encoding="utf-8")
        index = (RUNTIME.parent / "index.html").read_text(encoding="utf-8")
        for marker in (
            "loadLibrarianHistory()",
            "loadResearchMemories()",
            'q("#fusion-librarian-history-query")?.addEventListener("input"',
            'q("#fusion-librarian-history-clear")?.addEventListener("click"',
            'q("#fusion-librarian-remember")?.addEventListener("click"',
            'q("#fusion-research-memory-clear")?.addEventListener("click"',
            "saveCurrentLibrarianSession();renderResearchMemories();",
        ):
            self.assertIn(marker, source)
        for element_id in (
            "fusion-librarian-history-query",
            "fusion-librarian-history-clear",
            "fusion-librarian-remember",
            "fusion-research-memory-list",
            "fusion-research-memory-clear",
        ):
            self.assertEqual(index.count(f'id="{element_id}"'), 1)


if __name__ == "__main__":
    unittest.main()
