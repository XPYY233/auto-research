from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = (
    ROOT / "src" / "auto_research" / "evidence" / "web" / "fusion_pdf_controller.js"
)


class FusionPdfControllerRuntimeTests(unittest.TestCase):
    def test_zoom_resize_and_return_state_share_one_safe_controller(self) -> None:
        program = f"""
const fs=require('fs'),assert=require('assert');
class Observer{{constructor(callback){{this.callback=callback;this.target=null;this.closed=false}}observe(target){{this.target=target}}disconnect(){{this.closed=true}}}}
globalThis.ResizeObserver=Observer;
eval(fs.readFileSync({str(CONTROLLER)!r},'utf8'));
const Controller=globalThis.AutoResearchFusionPDF.FusionPdfController;
const controller=new Controller(),frame={{src:'',dataset:{{}},removeAttribute(name){{if(name==='src')this.src=''}}}},container={{}},focus={{isConnected:true}},highlight={{kind:'text'}};
let state=controller.open({{viewerId:'detail',frame,container,url:'/api/papers/7/pdf#ignored',page:4,returnTabId:'evidence:7',returnFocus:focus,scrollTop:321,highlight}});
assert.equal(frame.src,'/api/papers/7/pdf#page=4&zoom=page-width');
assert.equal(state.mode,'fit-width');assert.equal(state.page,4);assert.equal(state.returnTabId,'evidence:7');
controller.fitPage('detail');assert.equal(frame.src,'/api/papers/7/pdf#page=4&zoom=page-fit');
controller.zoomIn('detail');assert.equal(frame.src,'/api/papers/7/pdf#page=4&zoom=125');
controller.zoomOut('detail');assert.equal(frame.src,'/api/papers/7/pdf#page=4&zoom=100');
controller.setPage('detail',9);controller.reset('detail');assert.equal(frame.src,'/api/papers/7/pdf#page=9&zoom=100');
state=controller.close('detail');assert.equal(state.scrollTop,321);assert.equal(state.returnFocus,focus);assert.deepEqual(state.highlight,highlight);assert.equal(frame.src,'');
assert.equal(controller.open({{viewerId:'bad',frame,url:'https://example.com/paper.pdf'}}),null);
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
