from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "src" / "auto_research" / "evidence" / "web" / "document_tab_store.js"


class DocumentTabStoreRuntimeTests(unittest.TestCase):
    def test_identity_only_persistence_split_merge_close_and_restore(self) -> None:
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
assert(store.setNarrow(true));assert.equal(store.snapshot().groups.length,1);assert.equal(store.snapshot().tabs.length,2);
const restored=new Store();assert.equal(restored.snapshot().tabs.length,2);assert.equal(restored.snapshot().groups.length,1);assert.equal(restored.snapshot().tabs[0].payload,undefined);
const closed=restored.close(paper.tabId);assert.equal(closed.tabId,paper.tabId);assert.equal(restored.snapshot().tabs.length,1);
assert.equal(restored.open({{tabId:'evil:x',kind:'evil',ownerView:'paper',title:'x',identity:{{paperId:'1'}}}}),null);
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
