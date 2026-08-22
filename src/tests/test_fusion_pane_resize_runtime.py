from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "auto_research" / "evidence" / "web"


class FusionPaneResizeRuntimeTests(unittest.TestCase):
    def test_v2_snap_restore_pointer_keyboard_and_responsive_groups(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert'),memory=new Map();
class Classes{{constructor(){{this.values=new Set()}}add(v){{this.values.add(v)}}remove(v){{this.values.delete(v)}}toggle(v,on){{on?this.values.add(v):this.values.delete(v)}}contains(v){{return this.values.has(v)}}}}
class El{{constructor(kind=''){{this.dataset=kind?{{paneSeparator:kind}}:{{}};this.hidden=false;this.classList=new Classes();this.attrs={{}};this.listeners={{}};this.captured=null;this.innerHTML='';}}setAttribute(k,v){{this.attrs[k]=String(v)}}removeAttribute(k){{delete this.attrs[k]}}addEventListener(k,fn){{(this.listeners[k]??=[]).push(fn)}}setPointerCapture(id){{this.captured=id}}releasePointerCapture(id){{assert.equal(this.captured,id);this.captured=null}}focus(){{}}}}
const contextSep=new El('context'),groupSep=new El('editor-groups'),inspectorSep=new El('inspector'),context=new El(),inspector=new El(),primary=new El(),secondary=new El(),layout=new El(),tabGroups=new El(),html={{dataset:{{}},style:{{values:{{}},setProperty(k,v){{this.values[k]=v}}}}}},body={{dataset:{{view:'paper'}}}};
const ids={{'#fusion-context-separator':contextSep,'#fusion-editor-group-separator':groupSep,'#fusion-inspector-separator':inspectorSep,'#fusion-context':context,'#fusion-inspector':inspector,'#fusion-primary-editor-surface':primary,'#fusion-secondary-editor-surface':secondary,'#fusion-editor-group-content':layout,'#fusion-document-tab-groups':tabGroups}};
const byKind={{'[data-pane-separator="context"]':contextSep,'[data-pane-separator="editor-groups"]':groupSep,'[data-pane-separator="inspector"]':inspectorSep}};
globalThis.document={{readyState:'loading',documentElement:html,body,querySelector:s=>ids[s]||byKind[s]||null,querySelectorAll:s=>s==='[data-pane-separator]'?[contextSep,groupSep,inspectorSep]:[],addEventListener:()=>{{}}}};
globalThis.localStorage={{getItem:k=>memory.get(k)||null,setItem:(k,v)=>memory.set(k,v)}};globalThis.innerWidth=1600;
eval(fs.readFileSync({str(WEB / 'document_tab_store.js')!r},'utf8'));eval(fs.readFileSync({str(WEB / 'pane_layout_controller.js')!r},'utf8'));eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));
const api=globalThis.AutoResearchFusion,tabs=api.documentTabs,controller=api.paneController,prevent=()=>{{}};
tabs.open({{tabId:'evidence:a',kind:'evidence',ownerView:'paper',title:'A',identity:{{sourceScope:'workspace',entityType:'table',entityUid:'1'}}}},{{pin:true}});tabs.open({{tabId:'evidence:b',kind:'evidence',ownerView:'paper',title:'B',identity:{{sourceScope:'workspace',entityType:'table',entityUid:'2'}}}},{{groupId:'secondary',pin:true}});
api.bindPaneSeparators();let geometry=api.applyPaneLayout();assert.equal(groupSep.hidden,false);assert.equal(contextSep.attrs['aria-valuemin'],'0');assert.equal(inspectorSep.attrs['aria-valuemin'],'0');
contextSep.listeners.pointerdown[0]({{currentTarget:contextSep,button:0,pointerId:7,clientX:300,preventDefault:prevent}});contextSep.listeners.pointermove[0]({{pointerId:7,clientX:80,preventDefault:prevent}});contextSep.listeners.pointerup[0]({{pointerId:7,preventDefault:prevent}});assert.equal(api.paneLayout.context.collapsed,true);assert.equal(api.paneLayout.context.size,0);assert.equal(contextSep.hidden,true);
assert(api.togglePane('context'));assert.equal(api.paneLayout.context.collapsed,false);assert.equal(api.paneLayout.context.size,244);
inspectorSep.listeners.keydown[0]({{currentTarget:inspectorSep,key:'End',shiftKey:false,preventDefault:prevent}});assert.equal(api.paneLayout.inspector.collapsed,true);assert(api.togglePane('inspector'));assert.equal(api.paneLayout.inspector.size,340);
geometry=api.applyPaneLayout();groupSep.listeners.pointerdown[0]({{currentTarget:groupSep,button:0,pointerId:9,clientX:800,preventDefault:prevent}});groupSep.listeners.pointermove[0]({{pointerId:9,clientX:0,preventDefault:prevent}});groupSep.listeners.pointerup[0]({{pointerId:9,preventDefault:prevent}});assert.equal(api.paneLayout.editors.primaryCollapsed,true);assert.equal(primary.hidden,true);assert.equal(secondary.hidden,false);assert.equal(controller.collapseEditor('secondary'),false);
assert(api.togglePane('primary'),'primary toggle must restore');assert.equal(api.paneLayout.editors.primaryCollapsed,false,'primary expanded');assert(Math.abs(api.paneLayout.editors.split-.58)<.001,`split restored ${{api.paneLayout.editors.split}}`);
api.setPaneLayoutValue('editor-groups',1);assert.equal(api.paneLayout.editors.secondaryCollapsed,true);groupSep.listeners.dblclick[0]();assert.equal(api.paneLayout.editors.secondaryCollapsed,false);assert(Math.abs(api.paneLayout.editors.split-.58)<.001);
const saved=JSON.parse(memory.get('fusion-pane-layout-v2'));assert.equal(saved.schema_version,'fusion-pane-layout-v2');assert(!JSON.stringify(saved).includes('evidence:a'));assert.deepEqual(Object.keys(saved).sort(),['context','editors','inspector','schema_version']);
const stateRef=api.paneLayout;memory.set('fusion-pane-layout-v2',JSON.stringify({{schema_version:'fusion-pane-layout-v2',context:{{collapsed:true,size:0,lastExpanded:310}},inspector:{{collapsed:false,size:400,lastExpanded:400}},editors:{{primaryCollapsed:false,secondaryCollapsed:false,split:.4,lastSplit:.4}}}}));assert.equal(api.readPaneLayout(),true);assert.strictEqual(api.paneLayout,stateRef);assert.equal(api.paneLayout.context.lastExpanded,310);assert.equal(api.paneLayout.editors.split,.4);
globalThis.innerWidth=800;let responsive=api.syncResponsivePaneLayout();assert.equal(responsive.narrow,true);assert.equal(groupSep.hidden,true);assert.equal(tabs.snapshot().tabs.find(tab=>tab.tabId==='evidence:b').groupId,'secondary');
globalThis.innerWidth=1600;responsive=api.syncResponsivePaneLayout();assert.equal(responsive.narrow,false);assert.equal(groupSep.hidden,false);assert.equal(tabs.activeTab('primary').tabId,'evidence:a');assert.equal(tabs.activeTab('secondary').tabId,'evidence:b');
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
