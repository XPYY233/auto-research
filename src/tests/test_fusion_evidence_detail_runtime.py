from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "src" / "auto_research" / "evidence" / "web" / "fusion_review.js"


class FusionEvidenceDetailRuntimeTests(unittest.TestCase):
    def test_four_types_routes_identity_late_response_and_focus(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert');
class Classes{{constructor(){{this.s=new Set()}}toggle(k,v){{v?this.s.add(k):this.s.delete(k)}}add(k){{this.s.add(k)}}remove(k){{this.s.delete(k)}}contains(k){{return this.s.has(k)}}}}
class El{{constructor(dataset={{}}){{this.dataset=dataset;this.hidden=false;this.disabled=false;this.classList=new Classes();this.attrs={{}};this.listeners={{}};this.textContent='';this.innerHTML='';this.src='';this.tabIndex=0;this.isConnected=true;this.focused=false;}}addEventListener(k,f){{(this.listeners[k]??=[]).push(f)}}setAttribute(k,v){{this.attrs[k]=String(v)}}removeAttribute(k){{delete this.attrs[k];if(k==='src')this.src=''}}focus(){{this.focused=true;globalThis.focused=this}}closest(){{return this}}matches(){{return false}}querySelector(){{return null}}querySelectorAll(){{return []}}}}
const primaryTab=new El({{tab:'primary'}}),detailTab=new El({{tab:'detail'}}),panels=['paper','search','personal','package','settings'].map(viewPanel=>new El({{viewPanel}}));
const ids={{}};for(const id of ['fusion-evidence-detail','fusion-evidence-detail-body','fusion-detail-heading','fusion-detail-identity','fusion-detail-pdf','fusion-detail-pdf-frame','fusion-detail-pdf-title','fusion-detail-close-pdf','fusion-detail-open-pdf','fusion-detail-image','fusion-inspector-title','fusion-inspector-body'])ids['#'+id]=new El();
ids['[data-tab="detail"]']=detailTab;
const all={{'[data-view-panel]':panels,".fusion-tabs [role='tab']":[primaryTab,detailTab],'[data-evidence-index],[data-search-evidence-index]':[]}};
globalThis.document={{readyState:'loading',documentElement:{{dataset:{{}}}},body:{{dataset:{{view:'paper'}}}},querySelector:s=>ids[s]||null,querySelectorAll:s=>all[s]||[],addEventListener:()=>{{}}}};
globalThis.localStorage={{getItem:()=>null,setItem:()=>{{}}}};globalThis.confirm=()=>true;
const calls=[];let lateResolve=null;globalThis.fetch=async(url,options={{}})=>{{url=String(url);calls.push(url);const headers={{get:()=>null}};
 if(url==='/api/visual-assets/7')return{{ok:true,headers,json:async()=>({{id:7,asset_type:'figure',paper_id:3,label:'Figure 2',display_name:'W-Ta辐照缺陷形貌',caption:'Figure 2. Defect morphology.',context_explanation:'比较不同剂量下的缺陷形貌。',source_context:'原文图注与邻近正文。',page_start:4,physical_quantities:['缺陷密度'],variables:{{dose:'辐照剂量'}},materials:['W-Ta'],conditions_text:'300 K',methods_text:'TEM',tags:['TEM','辐照'],linked_item_count:2,image_url:'/api/visual-assets/7/image',article_title:'Real paper',doi:'10.1/example'}})}};
 if(url.startsWith('/api/desktop/federated-evidence?'))return{{ok:true,headers,json:async()=>({{entity_type:'table',source_scope:'official',source_id:'official-pack',entity_uid:'entity-table',label:'Table 3',display_name:'官方材料性能表',caption:'Table 3. Mechanical properties.',page_start:8,variables:{{hardness:'GPa'}},materials:['W'],conditions_text:'室温',methods_text:'纳米压痕',linked_item_count:3,article_title:'Official paper'}})}};
 if(url==='/api/visual-assets/9')return new Promise(resolve=>{{lateResolve=()=>resolve({{ok:true,headers,json:async()=>({{id:9,asset_type:'figure',paper_id:3,label:'Figure 9',display_name:'晚到标题',image_url:'/api/visual-assets/9/image'}})}})}});
 throw new Error('unexpected:'+url);
}};
eval(fs.readFileSync({str(RUNTIME)!r},'utf8'));const api=globalThis.AutoResearchFusion;
(async()=>{{
 const privateRaw={{entity_type:'table',source_scope:'private',source_id:'lab',entity_uid:'private-table',display_title:'私人实验表',variables:{{dose:'dpa'}},image_url:'/api/visual-assets/999/image',pdf_path:'/secret/paper.pdf',zotero_key:'SECRET'}};
 const privateRow=api.publicEvidence(privateRaw);assert.equal(privateRow.sourceScope,'private');assert.equal(privateRow.imageUrl,'');assert.equal(privateRow.assetId,null);assert(!JSON.stringify(privateRow).includes('/secret'));assert(!JSON.stringify(privateRow).includes('SECRET'));
 api.state.view='paper';document.body.dataset.view='paper';api.state.evidence=[api.publicEvidence({{entity_type:'item',item_id:4,paper_id:3,value_text:'4.2',unit:'GPa',meaning:'纳米硬度',source_excerpt:'原文数值',context_explanation:'室温压痕',article_title:'Real paper',source_page:2}})];
 const itemButton=new El();assert(await api.openEvidenceDetail(0,itemButton));assert(ids['#fusion-evidence-detail-body'].innerHTML.includes('4.2 GPa'));assert(ids['#fusion-evidence-detail-body'].innerHTML.includes('原文数值'));assert.equal(calls.length,0,'item detail must not fetch');assert(api.closeEvidenceDetail());assert.equal(globalThis.focused,itemButton);
 api.state.evidence=[api.publicEvidence({{entity_type:'finding',item_id:5,paper_id:3,finding_text:'硬度随温度升高而下降',meaning:'温度效应',source_excerpt:'decreased with temperature',article_title:'Real paper',source_page:5}})];assert(await api.openEvidenceDetail(0,new El()));assert(ids['#fusion-evidence-detail-body'].innerHTML.includes('硬度随温度升高而下降'));api.closeEvidenceDetail({{focus:false}});
 api.state.evidence=[api.publicEvidence({{entity_type:'figure',id:7,paper_id:3,label:'Figure 2',display_name:'形貌图',image_url:'/api/visual-assets/7/image'}})];assert(await api.openEvidenceDetail(0,new El()));assert(calls.includes('/api/visual-assets/7'));assert(ids['#fusion-evidence-detail-body'].innerHTML.includes('/api/visual-assets/7/image'));assert(ids['#fusion-evidence-detail-body'].innerHTML.includes('Defect morphology'));assert(ids['#fusion-evidence-detail-body'].innerHTML.includes('缺陷密度'));assert(api.openDetailPDF());assert.equal(ids['#fusion-detail-pdf-frame'].src,'/api/papers/3/pdf#page=4');assert(api.closeDetailPDF());api.closeEvidenceDetail({{focus:false}});
 api.state.view='search';document.body.dataset.view='search';api.state.searchResults=[api.publicEvidence({{entity_type:'table',source_scope:'official',source_id:'official-pack',entity_uid:'entity-table',display_title:'官方材料性能表'}})];assert(await api.openEvidenceDetail(0,new El()));assert(calls.some(url=>url.includes('/api/desktop/federated-evidence?source_scope=official')));assert(!calls.some(url=>url==='/api/visual-assets/999'));assert(ids['#fusion-evidence-detail-body'].innerHTML.includes('该资料源不提供原图'));assert(ids['#fusion-evidence-detail-body'].innerHTML.includes('Mechanical properties'));api.closeEvidenceDetail({{focus:false}});
 api.state.view='paper';document.body.dataset.view='paper';api.state.evidence=[api.publicEvidence({{entity_type:'figure',id:9,paper_id:3,label:'Figure 9',display_name:'原始选择',image_url:'/api/visual-assets/9/image'}})];const late=api.openEvidenceDetail(0,new El());assert(lateResolve);api.closeEvidenceDetail({{focus:false}});lateResolve();await late;assert(!ids['#fusion-evidence-detail-body'].innerHTML.includes('晚到标题'),'late detail must not replace closed workspace');
 assert(!calls.some(url=>url.includes('private-table')),'private detail was only projected and must not use a workspace route');
}})().catch(error=>{{console.error(error);process.exitCode=1}});
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
