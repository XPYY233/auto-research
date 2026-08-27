from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "auto_research" / "evidence" / "web"
RUNTIME = WEB / "fusion_review.js"
PERSONAL_RUNTIME = WEB / "fusion_personal_import.js"


class FusionPersonalImportRuntimeTests(unittest.TestCase):
    def test_real_personal_pages_are_validated_rendered_and_generation_bound(self) -> None:
        program = f"""
(async()=>{{
const fs=require('fs'),assert=require('assert');
class Classes{{toggle(){{}} add(){{}} remove(){{}}}}
class El{{constructor(){{this.hidden=false;this.disabled=false;this.textContent='';this._html='';this.value='';this.dataset={{}};this.attrs={{}};this.listeners={{}};this.classList=new Classes();this.tabIndex=0;this.isConnected=true;}}set innerHTML(value){{this._html=String(value)}}get innerHTML(){{return this._html}}addEventListener(name,fn){{(this.listeners[name]??=[]).push(fn)}}setAttribute(name,value){{this.attrs[name]=String(value)}}removeAttribute(name){{delete this.attrs[name]}}focus(){{globalThis.focused=this}}querySelector(){{return null}}querySelectorAll(){{return []}}}}
class Table extends El{{constructor(){{super();this.head=new El();this.body=new El();this.cells=[];this.headers=[];Object.defineProperty(this.head,'innerHTML',{{set:value=>{{this.head._html=String(value);this.headers=[...String(value).matchAll(/data-column=\"(\\d+)\"/g)].map(match=>{{const cell=new El();cell.dataset.column=match[1];return cell;}});}},get:()=>this.head._html}});Object.defineProperty(this.body,'innerHTML',{{set:value=>{{this.body._html=String(value);this.cells=[...String(value).matchAll(/data-cell data-row=\"(\\d+)\" data-column=\"(\\d+)\"/g)].map(match=>{{const cell=new El();cell.dataset.row=match[1];cell.dataset.column=match[2];return cell;}});}},get:()=>this.body._html}});}}querySelector(selector){{return selector==='thead'?this.head:selector==='tbody'?this.body:null}}}}
const table=new Table(),ids={{}};for(const id of ['fusion-data-grid','fusion-personal-grid-wrap','fusion-personal-page-controls','fusion-personal-page-status','fusion-personal-page-prev','fusion-personal-page-next','fusion-personal-confirm','fusion-personal-ai','fusion-sheet-summary','fusion-inspector-title','fusion-inspector-body'])ids['#'+id]=id==='fusion-data-grid'?table:new El();
globalThis.document={{readyState:'loading',documentElement:{{dataset:{{}},style:{{setProperty(){{}}}}}},body:{{dataset:{{view:'personal'}}}},querySelector(selector){{const match=selector.match(/^#fusion-data-grid \\[data-row=\"(\\d+)\"\\]\\[data-column=\"(\\d+)\"\\]$/);if(match)return table.cells.find(cell=>cell.dataset.row===match[1]&&cell.dataset.column===match[2])||null;return ids[selector]||null;}},querySelectorAll(selector){{if(selector==='#fusion-data-grid [data-cell]')return table.cells;if(selector==='#fusion-data-grid th[data-column]')return table.headers;return[];}},addEventListener(){{}},createElement:()=>new El()}};
globalThis.localStorage={{getItem:()=>null,setItem(){{}}}};
const headers={{get:()=>null}},pending=[];let late=false;
const page=(sheetIndex,pageNumber)=>{{let sheetName='Empty',totalRows=0,columns=['Time'],rows=[];if(sheetIndex===0){{sheetName='Sheet A';totalRows=51;columns=['Dose','Hardness'];rows=pageNumber===1?Array.from({{length:50}},(_,index)=>[String(index),String(index+100)]):[['50','150']];}}else if(sheetIndex===1){{sheetName='Sheet B';totalRows=1;rows=[['9']];}}return{{schema_version:'personal-tabular-page-v1',import_id:'personal_import_abcdefghijklmnop',sheet_index:sheetIndex,sheet_name:sheetName,page:pageNumber,page_size:50,total_rows:totalRows,has_next:sheetIndex===0&&pageNumber===1,columns,rows}};}};
globalThis.fetch=async url=>{{url=String(url);if(!late){{const match=url.match(/sheets\\/(\\d+)\\/rows\\?page=(\\d+)&page_size=50$/);return{{ok:true,headers,json:async()=>page(Number(match[1]),Number(match[2]))}};}}return new Promise(resolve=>pending.push({{url,resolve}}));}};
eval(fs.readFileSync({str(PERSONAL_RUNTIME)!r},'utf8'));eval(fs.readFileSync({str(RUNTIME)!r},'utf8'));const api=globalThis.AutoResearchFusion;
api.state.view='personal';api.state.personalStatus={{import_id:'personal_import_abcdefghijklmnop'}};api.state.personalPreview={{sheets:[{{sheet_name:'Sheet A',row_count:51,columns:[{{source_name:'Dose',data_type:'number',meaning:'剂量',unit:'dpa'}},{{source_name:'Hardness',data_type:'number',meaning:'硬度',unit:'GPa'}}]}},{{sheet_name:'Sheet B',row_count:1,columns:[{{source_name:'Time',data_type:'number',meaning:'时间',unit:'s'}}]}},{{sheet_name:'Empty',row_count:0,columns:[{{source_name:'Time',data_type:'number',meaning:'时间',unit:'s'}}]}}]}};api.state.personalSheetIndex=0;
assert(await api.loadPersonalPreviewPage(0,1));assert.equal(api.state.personalPreviewPage.rows.length,50);assert.equal(ids['#fusion-personal-page-status'].textContent,'第 1–50 行 / 共 51 行');assert.equal(ids['#fusion-personal-page-next'].disabled,false);assert(table.body.innerHTML.includes('<th scope="row">1</th>'));assert(!fs.readFileSync({str(PERSONAL_RUNTIME)!r},'utf8').includes('sample_rows'));
assert(await api.loadPersonalPreviewPage(0,2));assert.equal(api.state.personalPreviewPage.rows[0][1],'150');assert(table.body.innerHTML.includes('<th scope="row">51</th>'));assert(api.selectCell(0,1,{{focus:false}}));assert(ids['#fusion-inspector-body'].innerHTML.includes('第 51 行'));assert(ids['#fusion-inspector-body'].innerHTML.includes('150'));
const sheet=api.state.personalPreview.sheets[0],valid=page(0,1);assert(api.publicPersonalImportPage(valid,{{importId:'personal_import_abcdefghijklmnop',sheetIndex:0,sheet,page:1}}));assert.equal(api.publicPersonalImportPage({{...valid,total_rows:52}},{{importId:'personal_import_abcdefghijklmnop',sheetIndex:0,sheet,page:1}}),null);assert.equal(api.publicPersonalImportPage({{...valid,columns:['Hardness','Dose']}},{{importId:'personal_import_abcdefghijklmnop',sheetIndex:0,sheet,page:1}}),null);
api.state.personalSheetIndex=2;assert(await api.loadPersonalPreviewPage(2,1));assert.equal(api.state.personalPreviewPage.totalRows,0);assert.equal(ids['#fusion-personal-page-status'].dataset.state,'empty');assert.equal(ids['#fusion-personal-confirm'].disabled,true);assert(table.body.innerHTML.includes('当前工作表没有数据行'));
late=true;api.state.personalSheetIndex=0;const first=api.loadPersonalPreviewPage(0,1);api.state.personalSheetIndex=1;const second=api.loadPersonalPreviewPage(1,1);assert.equal(pending.length,2);pending[1].resolve({{ok:true,headers,json:async()=>page(1,1)}});assert(await second);pending[0].resolve({{ok:true,headers,json:async()=>page(0,1)}});assert.equal(await first,false);assert.equal(api.state.personalPreviewPage.sheetIndex,1);assert.equal(api.state.personalPreviewPage.rows[0][0],'9');
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

    def test_personal_page_dom_and_safe_route_contract(self) -> None:
        source = RUNTIME.read_text(encoding="utf-8")
        personal_source = PERSONAL_RUNTIME.read_text(encoding="utf-8")
        index = (WEB / "index.html").read_text(encoding="utf-8")
        for element_id in (
            "fusion-personal-page-controls",
            "fusion-personal-page-status",
            "fusion-personal-page-prev",
            "fusion-personal-page-next",
        ):
            self.assertEqual(index.count(f'id="{element_id}"'), 1)
        self.assertIn("本次确认只导入当前所选工作表", index)
        self.assertIn("PERSONAL_IMPORT_ROWS_PATH.test(path)", source)
        self.assertIn("page_size=50", personal_source)
        self.assertIn("publicPersonalImportPage", personal_source)
        self.assertIn("loadPersonalPreviewPage", personal_source)
        self.assertNotIn("sample_rows", personal_source)


if __name__ == "__main__":
    unittest.main()
