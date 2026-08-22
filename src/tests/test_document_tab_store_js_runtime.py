from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "src" / "auto_research" / "evidence" / "web" / "document_tab_store.js"


class DocumentTabStoreRuntimeTests(unittest.TestCase):
    def test_two_groups_survive_narrow_reopen_and_async_updates_stay_on_tab(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert');
const memory=new Map();globalThis.localStorage={{getItem:key=>memory.get(key)||null,setItem:(key,value)=>memory.set(key,value)}};
eval(fs.readFileSync({str(STORE)!r},'utf8'));
const Store=globalThis.AutoResearchDocumentTabs.DocumentTabStore,store=new Store();
const paper=store.open({{tabId:'paper:paperId=7',kind:'paper',ownerView:'paper',title:'真实论文',identity:{{paperId:'7'}},payload:{{secretBody:'never persist'}}}});
const evidence=store.open({{tabId:'evidence:sourceScope=workspace&entityUid=9',kind:'evidence',ownerView:'search',title:'硬度证据',identity:{{sourceScope:'workspace',entityType:'item',entityUid:'9'}},payload:{{excerpt:'private body'}}}});
assert.equal(store.snapshot().tabs.length,2);assert.equal(store.activeTab().tabId,evidence.tabId);
assert(store.split(evidence.tabId));assert.equal(store.snapshot().groups.length,2);assert.equal(store.activeGroupId,'secondary');
const saved=memory.get('auto-research-workspace-layout-v1');assert(saved.includes('workspace-layout-v1'));assert(!saved.includes('secretBody'));assert(!saved.includes('private body'));assert(!saved.includes('payload'));
const request=store.beginRequest(evidence.tabId);assert.equal(store.completeRequest(evidence.tabId,request,{{payload:{{resolved:'old tab only'}}}}).payload.resolved,'old tab only');assert.equal(store.completeRequest(evidence.tabId,request-1,{{payload:{{resolved:'late'}}}}),null);
assert(store.setNarrow(true));assert.equal(store.snapshot().groups.length,2);assert.equal(store.snapshot().tabs.length,2);assert.equal(store.snapshot().narrow,true);assert.equal(store.snapshot().tabs.find(tab=>tab.tabId===evidence.tabId).groupId,'secondary');
assert(store.focusGroup('primary'));assert.equal(store.snapshot().activeGroupId,'primary');
const closed=store.close(paper.tabId);assert.equal(closed.tabId,paper.tabId);assert.equal(store.snapshot().tabs.length,1);const reopened=store.reopenClosed();assert.equal(reopened.tabId,paper.tabId);assert.equal(store.snapshot().tabs.length,2);
assert(store.move(evidence.tabId,'primary'));assert.equal(store.snapshot().tabs.find(tab=>tab.tabId===evidence.tabId).groupId,'primary');
const restored=new Store();assert.equal(restored.snapshot().tabs.length,2);assert.equal(restored.snapshot().tabs[0].payload,undefined);
const review=store.open({{tabId:'review-candidate:paperUid=paper_0123456789abcdef0123456789abcdef&entityType=item&reviewOrdinal=0',kind:'review-candidate',ownerView:'paper',title:'待审核',identity:{{paperUid:'paper_0123456789abcdef0123456789abcdef',sourceScope:'workspace',sourceId:'workspace',entityType:'item',reviewOrdinal:'0'}},payload:{{reviewToken:'rq_abcdefghijklmnopqrstuvwxyzABCDEFGH',candidate:{{source_excerpt:'private candidate body'}}}}}});
assert(review);const reviewSaved=memory.get('auto-research-workspace-layout-v1');assert(reviewSaved.includes('review-candidate'));assert(reviewSaved.includes('paper_0123456789abcdef0123456789abcdef'));assert(!reviewSaved.includes('rq_abcdefghijklmnopqrstuvwxyzABCDEFGH'));assert(!reviewSaved.includes('private candidate body'));assert(!reviewSaved.includes('reviewToken'));
assert.equal(restored.open({{tabId:'evil:x',kind:'evil',ownerView:'paper',title:'x',identity:{{paperId:'1'}}}}),null);

const isolated=new Store({{storage:null}});
const a=isolated.open({{tabId:'evidence:sourceScope=workspace&entityUid=1357',kind:'evidence',ownerView:'paper',title:'Table 2',identity:{{sourceScope:'workspace',entityType:'table',entityUid:'1357'}},payload:{{row:{{title:'Table 2'}}}}}},{{pin:true}});
const c=isolated.open({{tabId:'evidence:sourceScope=workspace&entityUid=1359',kind:'evidence',ownerView:'paper',title:'Table 4',identity:{{sourceScope:'workspace',entityType:'table',entityUid:'1359'}},payload:{{row:{{title:'Table 4'}}}}}},{{pin:true}});
isolated.activate(a.tabId);
const b=isolated.open({{tabId:'evidence:sourceScope=workspace&entityUid=1358',kind:'evidence',ownerView:'paper',title:'Table 3',identity:{{sourceScope:'workspace',entityType:'table',entityUid:'1358'}},payload:{{row:{{title:'Table 3'}}}}}},{{groupId:'secondary',pin:true}});
assert.equal(isolated.activeTab('primary').tabId,a.tabId);assert.equal(isolated.activeTab('secondary').tabId,b.tabId);
const requestA=isolated.beginRequest(a.tabId),requestB=isolated.beginRequest(b.tabId);
assert.equal(isolated.completeRequest(b.tabId,requestB,{{payload:{{row:{{title:'Table 3 complete'}}}}}}).payload.row.title,'Table 3 complete');
assert.equal(isolated.activeTab('primary').payload.row.title,'Table 2');
assert.equal(isolated.completeRequest(a.tabId,requestA-1,{{payload:{{row:{{title:'late B in A'}}}}}}),null);
assert.equal(isolated.activeTab('primary').payload.row.title,'Table 2');
assert(isolated.move(a.tabId,'secondary'));assert.equal(isolated.activeTab('primary').tabId,c.tabId);assert.equal(isolated.activeTab('secondary').tabId,a.tabId);
assert(isolated.move(a.tabId,'primary'));assert.equal(isolated.activeTab('secondary').tabId,b.tabId);assert.equal(isolated.activeTab('primary').tabId,a.tabId);
const closedA=isolated.close(a.tabId);assert.equal(closedA.tabId,a.tabId);assert.equal(isolated.activeTab('primary').tabId,c.tabId);
const reopenedA=isolated.reopenClosed({{groupId:'primary'}});assert.equal(reopenedA.tabId,a.tabId);assert.equal(isolated.activeTab('primary').tabId,a.tabId);assert.equal(isolated.activeTab('secondary').tabId,b.tabId);
const previewOne=isolated.open({{tabId:'evidence:preview=one',kind:'evidence',ownerView:'search',title:'preview one',identity:{{sourceScope:'official',sourceId:'official',entityType:'finding',entityUid:'one'}},payload:{{row:{{title:'preview one'}}}}}},{{groupId:'secondary',preview:true}});
const stalePreviewGeneration=isolated.beginRequest(previewOne.tabId);
const previewTwo=isolated.open({{tabId:'evidence:preview=two',kind:'evidence',ownerView:'search',title:'preview two',identity:{{sourceScope:'official',sourceId:'official',entityType:'finding',entityUid:'two'}},payload:{{row:{{title:'preview two'}}}}}},{{groupId:'secondary',preview:true}});
assert.equal(isolated.snapshot().tabs.some(tab=>tab.tabId===previewOne.tabId),false);assert.equal(isolated.activeTab('secondary').tabId,previewTwo.tabId);assert.equal(isolated.completeRequest(previewOne.tabId,stalePreviewGeneration,{{payload:{{row:{{title:'late preview'}}}}}}),null);
isolated.pin(previewTwo.tabId);const previewThree=isolated.open({{tabId:'evidence:preview=three',kind:'evidence',ownerView:'search',title:'preview three',identity:{{sourceScope:'official',sourceId:'official',entityType:'finding',entityUid:'three'}},payload:{{row:{{title:'preview three'}}}}}},{{groupId:'secondary',preview:true}});assert(isolated.snapshot().tabs.some(tab=>tab.tabId===previewTwo.tabId));assert.equal(isolated.activeTab('secondary').tabId,previewThree.tabId);
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
