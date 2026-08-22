from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "auto_research" / "evidence" / "web"


class FusionPaneResizeRuntimeTests(unittest.TestCase):
    def test_pointer_keyboard_reset_persistence_and_responsive_split(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert'),memory=new Map();
class Classes{{constructor(){{this.values=new Set()}}add(value){{this.values.add(value)}}remove(value){{this.values.delete(value)}}toggle(value,on){{on?this.values.add(value):this.values.delete(value)}}contains(value){{return this.values.has(value)}}}}
class El{{constructor(kind=''){{this.dataset=kind?{{paneSeparator:kind}}:{{}};this.hidden=false;this.classList=new Classes();this.attrs={{}};this.listeners={{}};this.captured=null;}}setAttribute(key,value){{this.attrs[key]=String(value)}}addEventListener(key,fn){{(this.listeners[key]??=[]).push(fn)}}setPointerCapture(id){{this.captured=id}}releasePointerCapture(id){{assert.equal(this.captured,id);this.captured=null}}focus(){{}}}}
const context=new El('context'),groups=new El('editor-groups'),inspector=new El('inspector'),primary=new El(),secondary=new El(),layout=new El(),html={{dataset:{{}},style:{{values:{{}},setProperty(key,value){{this.values[key]=value}}}}}},body={{dataset:{{view:'paper'}}}};
const ids={{'#fusion-context-separator':context,'#fusion-editor-group-separator':groups,'#fusion-inspector-separator':inspector,'#fusion-primary-editor-surface':primary,'#fusion-secondary-editor-surface':secondary,'#fusion-editor-group-content':layout}};
const byKind={{'[data-pane-separator="context"]':context,'[data-pane-separator="editor-groups"]':groups,'[data-pane-separator="inspector"]':inspector}};
globalThis.document={{readyState:'loading',documentElement:html,body,querySelector:selector=>ids[selector]||byKind[selector]||null,querySelectorAll:selector=>selector==='[data-pane-separator]'?[context,groups,inspector]:[],addEventListener:()=>{{}}}};
globalThis.localStorage={{getItem:key=>memory.get(key)||null,setItem:(key,value)=>memory.set(key,value)}};globalThis.innerWidth=1600;
eval(fs.readFileSync({str(WEB / 'document_tab_store.js')!r},'utf8'));eval(fs.readFileSync({str(WEB / 'fusion_review.js')!r},'utf8'));const api=globalThis.AutoResearchFusion,tabs=api.documentTabs;
tabs.open({{tabId:'evidence:a',kind:'evidence',ownerView:'paper',title:'A',identity:{{sourceScope:'workspace',entityType:'table',entityUid:'1'}}}},{{pin:true}});tabs.open({{tabId:'evidence:b',kind:'evidence',ownerView:'paper',title:'B',identity:{{sourceScope:'workspace',entityType:'table',entityUid:'2'}}}},{{groupId:'secondary',pin:true}});
api.bindPaneSeparators();let geometry=api.applyPaneLayout();assert.equal(groups.hidden,false);assert(geometry.editorWidth>=725);assert.equal(context.attrs['aria-valuenow'],'244');assert.equal(inspector.attrs['aria-valuenow'],'340');
const prevent=()=>{{}};context.listeners.pointerdown[0]({{currentTarget:context,button:0,pointerId:7,clientX:300,preventDefault:prevent}});assert(context.classList.contains('dragging'));assert.equal(html.dataset.paneResizing,'true');context.listeners.pointermove[0]({{pointerId:7,clientX:400,preventDefault:prevent}});assert.equal(api.paneLayout.context,344);context.listeners.pointerup[0]({{pointerId:7,preventDefault:prevent}});assert.equal(context.captured,null);assert(!('paneResizing' in html.dataset));
let saved=JSON.parse(memory.get('fusion-pane-layout-v1'));assert.deepEqual(Object.keys(saved).sort(),['context','inspector','schema_version','split']);assert.equal(saved.context,344);assert(!JSON.stringify(saved).includes('evidence:a'));
context.listeners.keydown[0]({{currentTarget:context,key:'ArrowRight',shiftKey:true,preventDefault:prevent}});assert.equal(api.paneLayout.context,376);context.listeners.keydown[0]({{currentTarget:context,key:'Home',shiftKey:false,preventDefault:prevent}});assert.equal(api.paneLayout.context,180);context.listeners.dblclick[0]();assert.equal(api.paneLayout.context,244);
inspector.listeners.keydown[0]({{currentTarget:inspector,key:'ArrowLeft',shiftKey:false,preventDefault:prevent}});assert.equal(api.paneLayout.inspector,348);inspector.listeners.dblclick[0]();assert.equal(api.paneLayout.inspector,340);
inspector.listeners.pointerdown[0]({{currentTarget:inspector,button:0,pointerId:8,clientX:1200,preventDefault:prevent}});inspector.listeners.pointermove[0]({{pointerId:8,clientX:1160,preventDefault:prevent}});assert.equal(api.paneLayout.inspector,380);inspector.listeners.pointerup[0]({{pointerId:8,preventDefault:prevent}});inspector.listeners.dblclick[0]();
groups.listeners.keydown[0]({{currentTarget:groups,key:'End',shiftKey:false,preventDefault:prevent}});geometry=api.applyPaneLayout();assert.equal(Number(groups.attrs['aria-valuenow']),Math.round(geometry.splitBounds[1]*100));groups.listeners.dblclick[0]();assert.equal(api.paneLayout.split,.58);
groups.listeners.pointerdown[0]({{currentTarget:groups,button:0,pointerId:9,clientX:800,preventDefault:prevent}});groups.listeners.pointermove[0]({{pointerId:9,clientX:840,preventDefault:prevent}});assert(api.paneLayout.split>.58);groups.listeners.pointerup[0]({{pointerId:9,preventDefault:prevent}});groups.listeners.dblclick[0]();assert.equal(api.paneLayout.split,.58);
api.setPaneLayoutValue('context',999);assert.equal(api.paneLayout.context,480);api.setPaneLayoutValue('inspector',-1);assert.equal(api.paneLayout.inspector,260);api.setPaneLayoutValue('editor-groups',9);assert.equal(api.paneLayout.split,.7);api.setPaneLayoutValue('context',Number.NaN);assert.equal(api.paneLayout.context,180);api.resetPaneLayout();assert.deepEqual(api.paneLayout,{{context:244,inspector:340,split:.58}});
api.setPaneLayoutValue('context',400);memory.set('fusion-pane-layout-v1',JSON.stringify({{schema_version:'fusion-pane-layout-v1',context:244,inspector:340,split:.58,path:'/private/secret'}}));assert.equal(api.readPaneLayout(),false);assert.deepEqual(api.paneLayout,{{context:244,inspector:340,split:.58}});
globalThis.innerWidth=1000;let responsive=api.syncResponsivePaneLayout();assert.equal(responsive.narrow,true);assert.equal(groups.hidden,true);assert.equal(tabs.snapshot().tabs.find(tab=>tab.tabId==='evidence:b').groupId,'secondary');
globalThis.innerWidth=1600;responsive=api.syncResponsivePaneLayout();assert.equal(responsive.narrow,false);assert.equal(groups.hidden,false);assert.equal(tabs.activeTab('primary').tabId,'evidence:a');assert.equal(tabs.activeTab('secondary').tabId,'evidence:b');
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
