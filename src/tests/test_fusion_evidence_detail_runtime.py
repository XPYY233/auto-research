from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "auto_research" / "evidence" / "web"
RUNTIME = WEB / "fusion_review.js"


class FusionEvidenceDetailRuntimeTests(unittest.TestCase):
    def test_missing_workspace_visual_is_not_silently_a_text_summary(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert');
globalThis.document={{readyState:'loading',addEventListener(){{}},querySelector(){{return null}},querySelectorAll(){{return []}}}};
globalThis.addEventListener=()=>{{}};
eval(fs.readFileSync({str(RUNTIME)!r},'utf8'));
for(const type of ['table','figure']){{
 const row=AutoResearchFusion.publicEvidence({{entity_type:type,source_scope:'workspace',source_id:'workspace',entity_uid:'entity_'+type+'_'+'a'.repeat(32),label:type==='table'?'Table 4':'Figure 10',caption:'真实图注'}});
 for(const status of ['error','ready']){{
  const html=AutoResearchFusion.secondaryDocumentHTML({{tabId:'evidence:'+type,kind:'evidence',payload:{{row,status}}}});
  assert(html.includes(status==='error'?'原图详情未能载入':'当前资料源没有可用原图'));
  assert(!html.includes('<img '),'never invent an asset URL');
  if(status==='error')assert(!html.includes('正在读取结构化行列'),'detail failure must terminate grid loading too');
 }}
}}
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_four_types_routes_identity_late_response_and_focus(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert');
class Classes{{constructor(){{this.s=new Set()}}toggle(k,v){{v?this.s.add(k):this.s.delete(k)}}add(k){{this.s.add(k)}}remove(k){{this.s.delete(k)}}contains(k){{return this.s.has(k)}}}}
class El{{constructor(dataset={{}}){{this.dataset=dataset;this.hidden=false;this.disabled=false;this.classList=new Classes();this.attrs={{}};this.listeners={{}};this.textContent='';this.innerHTML='';this.src='';this.scrollTop=0;this.tabIndex=0;this.isConnected=true;this.focused=false;this.clientWidth=1440;}}addEventListener(k,f){{(this.listeners[k]??=[]).push(f)}}setAttribute(k,v){{this.attrs[k]=String(v)}}removeAttribute(k){{delete this.attrs[k];if(k==='src')this.src=''}}focus(){{this.focused=true;globalThis.focused=this}}closest(){{return this}}matches(){{return false}}querySelector(){{return null}}querySelectorAll(){{return []}}}}
const panels=['paper','search','personal','package','settings'].map(viewPanel=>new El({{viewPanel}})),ids={{}};
for(const id of ['fusion-shell','fusion-editor-group-content','fusion-primary-editor-surface','fusion-primary-document-body','fusion-secondary-editor-surface','fusion-secondary-editor-title','fusion-secondary-editor-body','fusion-editor-group-separator','fusion-document-tab-groups','fusion-document-tabs-primary','fusion-document-tabs-secondary','fusion-evidence-detail','fusion-evidence-detail-body','fusion-detail-heading','fusion-detail-identity','fusion-inspector-title','fusion-inspector-body'])ids['#'+id]=new El();
const all={{'[data-view-panel]':panels,'[data-evidence-chat]':[],'[data-pdf-action][data-pdf-viewer]':[]}};let tabNodes=[],closeNodes=[];
function rebuildTabNodes(){{tabNodes=[];closeNodes=[];for(const groupId of ['primary','secondary']){{const html=ids['#fusion-document-tabs-'+groupId].innerHTML||'',matches=[...html.matchAll(/data-document-tab-id="([^"]+)"/g)];for(const match of matches){{const tabId=match[1].replaceAll('&amp;','&'),tab=new El({{documentTabId:tabId}}),close=new El({{closeDocumentTab:tabId}});tab.parentElement={{id:'fusion-document-tabs-'+groupId}};tabNodes.push(tab);closeNodes.push(close);}}}}}}
globalThis.document={{readyState:'loading',activeElement:null,documentElement:{{dataset:{{}},style:{{setProperty:()=>{{}}}}}},body:{{dataset:{{view:'paper'}}}},querySelector:s=>ids[s]||null,querySelectorAll:s=>{{if(s==='[data-document-tab-id]'){{rebuildTabNodes();return tabNodes}}if(s==='[data-close-document-tab]')return closeNodes;return all[s]||[]}},addEventListener:()=>{{}}}};
globalThis.localStorage={{getItem:()=>null,setItem:()=>{{}}}};globalThis.confirm=()=>true;globalThis.innerWidth=1440;globalThis.addEventListener=()=>{{}};
const calls=[];let lateResolve=null;globalThis.fetch=async(url,options={{}})=>{{url=String(url);calls.push(url);const headers={{get:()=>null}};
 if(url==='/api/visual-assets/7')return{{ok:true,headers,json:async()=>({{id:7,asset_type:'figure',paper_id:3,label:'Figure 2',display_name:'W-Ta辐照缺陷形貌',caption:'Figure 2. Defect morphology.',context_explanation:'比较不同剂量下的缺陷形貌。',source_context:'原文图注与邻近正文。',page_start:4,physical_quantities:['缺陷密度'],variables:{{dose:'辐照剂量'}},materials:['W-Ta'],conditions_text:'300 K',methods_text:'TEM',tags:['TEM','辐照'],linked_item_count:2,image_url:'/api/visual-assets/7/image',article_title:'Real paper',doi:'10.1/example'}})}};
 if(url.startsWith('/api/desktop/federated-evidence?'))return{{ok:true,headers,json:async()=>({{entity_type:'table',source_scope:'official',source_id:'official-pack',entity_uid:'entity-table',collection_kind:'literature_collection',asset_available:true,label:'Table 3',display_name:'官方材料性能表',caption:'Table 3. Mechanical properties.',page_start:8,variables:{{hardness:'GPa'}},materials:['W'],conditions_text:'室温',methods_text:'纳米压痕',linked_item_count:3,article_title:'Official paper'}})}};
 if(url==='/api/visual-assets/9')return new Promise(resolve=>{{lateResolve=()=>resolve({{ok:true,headers,json:async()=>({{id:9,asset_type:'figure',paper_id:3,label:'Figure 9',display_name:'晚到标题',image_url:'/api/visual-assets/9/image'}})}})}});
 throw new Error('unexpected:'+url);
}};
const runtime=fs.readFileSync({str(RUNTIME)!r},'utf8');
eval(fs.readFileSync({str(WEB / 'document_tab_store.js')!r},'utf8'));eval(fs.readFileSync({str(WEB / 'fusion_pdf_controller.js')!r},'utf8'));eval(runtime);const api=globalThis.AutoResearchFusion,tabs=api.documentTabs,secondary=ids['#fusion-secondary-editor-body'];
function closeActiveEvidence({{focus=true}}={{}}){{const active=tabs.activeTab('secondary')||tabs.activeTab('primary');assert(active&&active.kind==='evidence');const button=closeNodes.find(value=>value.dataset.closeDocumentTab===active.tabId);assert(button?.listeners?.click?.length);if(!focus)active.payload.focusReturn=null;button.listeners.click.at(-1)({{stopPropagation:()=>{{}}}});return active;}}
(async()=>{{
 const privateRaw={{entity_type:'table',source_scope:'private',source_id:'lab',entity_uid:'private-table',collection_kind:'literature_collection',asset_available:true,display_title:'私人实验表',variables:{{dose:'dpa'}},image_url:'/api/visual-assets/999/image',pdf_path:'/secret/paper.pdf',zotero_key:'SECRET'}};
 const privateRow=api.publicEvidence(privateRaw);assert.equal(privateRow.sourceScope,'private');assert.equal(privateRow.imageUrl,'');assert.equal(privateRow.assetId,null);assert(!JSON.stringify(privateRow).includes('/secret'));assert(!JSON.stringify(privateRow).includes('SECRET'));
 api.state.view='paper';document.body.dataset.view='paper';api.state.evidence=[api.publicEvidence({{entity_type:'item',item_id:4,paper_id:3,value_text:'4.2',unit:'GPa',meaning:'纳米硬度',source_excerpt:'原文数值',context_explanation:'室温压痕',article_title:'Real paper',source_page:2}})];
 const itemButton=new El();assert(await api.openEvidenceDetail(0,itemButton));let tab=tabs.activeTab('secondary');assert.equal(tab.identity.sourceScope,'workspace');assert.equal(tab.identity.entityType,'item');assert.equal(tab.identity.entityUid,'4');assert.equal(tab.payload.focusReturn,itemButton);assert(secondary.innerHTML.includes('4.2 GPa'));assert(secondary.innerHTML.includes('原文数值'));assert.equal(calls.length,0,'item detail must not fetch');closeActiveEvidence();assert.equal(globalThis.focused,itemButton);
 api.state.evidence=[api.publicEvidence({{entity_type:'finding',item_id:5,paper_id:3,finding_text:'硬度随温度升高而下降',meaning:'温度效应',source_excerpt:'decreased with temperature',article_title:'Real paper',source_page:5}})];assert(await api.openEvidenceDetail(0,new El()));assert(secondary.innerHTML.includes('fusion-finding-summary'));assert(!secondary.innerHTML.includes('fusion-detail-measure'), 'a prose finding must not use the giant numeric value treatment');assert.equal((secondary.innerHTML.match(/硬度随温度升高而下降/g)||[]).length,1,'the finding body must not be repeated');closeActiveEvidence({{focus:false}});
 api.state.evidence=[api.publicEvidence({{entity_type:'figure',id:7,paper_id:3,label:'Figure 2',display_name:'形貌图',image_url:'/api/visual-assets/7/image'}})];assert(await api.openEvidenceDetail(0,new El()));assert(calls.includes('/api/visual-assets/7'));assert(secondary.innerHTML.includes('/api/visual-assets/7/image'));assert(secondary.innerHTML.includes('Defect morphology'));assert(secondary.innerHTML.includes('缺陷密度'));const sourceTab=tabs.activeTab('secondary');assert(api.openDetailPDF());const pdfTab=tabs.activeTab('secondary');assert.equal(pdfTab.kind,'pdf');assert.equal(pdfTab.payload.url,'/api/papers/3/pdf');assert.equal(pdfTab.payload.page,4);assert.equal(pdfTab.payload.returnTabId,sourceTab.tabId);assert(api.closeDetailPDF({{focus:false}}));assert.equal(tabs.activeTab('secondary').tabId,sourceTab.tabId);closeActiveEvidence({{focus:false}});
 api.state.view='search';document.body.dataset.view='search';api.state.searchResults=[api.publicEvidence({{entity_type:'table',source_scope:'official',source_id:'official-pack',entity_uid:'entity-table',display_title:'官方材料性能表'}})];assert(await api.openEvidenceDetail(0,new El()));tab=tabs.activeTab('secondary');assert.equal(tab.identity.sourceScope,'official');assert.equal(tab.identity.sourceId,'official-pack');assert.equal(tab.identity.entityUid,'entity-table');assert(calls.some(url=>url.includes('/api/desktop/federated-evidence?source_scope=official')));assert(!calls.some(url=>url==='/api/visual-assets/999'));assert(secondary.innerHTML.includes('/api/desktop/federated-asset?source_scope=official&amp;source_id=official-pack&amp;entity_uid=entity-table'));assert(secondary.innerHTML.includes('请以原始表格截图为准'));assert(secondary.innerHTML.includes('data-evidence-visual-error hidden'));assert(!secondary.innerHTML.includes('该资料源不提供原图'));assert(secondary.innerHTML.includes('Mechanical properties'));
 const caption=new El(),visualError=new El(),ready=new El(),failed=new El();visualError.hidden=true;failed.hidden=true;const figure={{querySelector:selector=>selector==='figcaption'?caption:selector==='[data-evidence-visual-error]'?visualError:null}},article={{querySelector:selector=>selector==='[data-table-structure-asset-ready]'?ready:selector==='[data-table-structure-asset-error]'?failed:null}},visualImage=new El();visualImage.closest=selector=>selector==='[data-evidence-visual]'?figure:selector==="[data-secondary-document-kind='evidence']"?article:null;all['[data-evidence-visual-image]']=[visualImage];api.bindEvidenceVisualFailures();assert.equal(visualImage.listeners.error.length,1);visualImage.listeners.error[0]();assert(visualImage.hidden);assert(caption.hidden);assert.equal(visualError.hidden,false);assert(ready.hidden);assert.equal(failed.hidden,false);closeActiveEvidence({{focus:false}});
 api.state.view='paper';document.body.dataset.view='paper';api.state.evidence=[api.publicEvidence({{entity_type:'figure',id:9,paper_id:3,label:'Figure 9',display_name:'原始选择',image_url:'/api/visual-assets/9/image'}})];const late=api.openEvidenceDetail(0,new El());assert(lateResolve);const lateTab=tabs.activeTab('secondary');tabs.close(lateTab.tabId);api.renderDocumentTabs();lateResolve();assert.equal(await late,false);assert(!secondary.innerHTML.includes('晚到标题'),'late detail must not replace a closed stable tab');
 assert(!calls.some(url=>url.includes('private-table')),'private detail was only projected and must not use a workspace route');
}})().catch(error=>{{console.error(error);process.exitCode=1}});
"""
        result = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, check=False, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
