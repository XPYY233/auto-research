import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "src/auto_research/evidence/web/workspace_layout_controller.js"


class WorkspaceLayoutControllerRuntimeTest(unittest.TestCase):
    def test_projection_obeys_page_and_column_contract(self):
        program = textwrap.dedent(
            f"""
            const fs=require('fs'),vm=require('vm'),assert=require('assert');
            const context={{globalThis:null}};context.globalThis=context;
            vm.runInNewContext(fs.readFileSync({str(SCRIPT)!r},'utf8'),context);
            const layout=context.AutoResearchWorkspaceLayout;
            const widths=[1680,1440,1280,1024,900,720];
            for(const width of widths){{
              const result=layout.project({{width,view:'paper',hasSecondary:true,hasInspectorSelection:true}});
              assert(result.dockedColumns<=3);
              assert.equal(result.primary,'docked');
              assert(!(result.secondary==='docked'&&result.inspector==='docked'));
            }}
            let value=layout.project({{width:1680,view:'paper',hasSecondary:false,hasInspectorSelection:true}});
            assert.equal(value.context,'docked');assert.equal(value.inspector,'docked');
            value=layout.project({{width:1440,view:'search',hasSecondary:true,hasInspectorSelection:true}});
            assert.equal(value.context,'hidden');assert.equal(value.secondary,'docked');assert.equal(value.inspector,'drawer');
            value=layout.project({{width:1024,view:'search',hasSecondary:true,hasInspectorSelection:true}});
            assert.equal(value.context,'drawer');assert.equal(value.secondary,'single');assert.equal(value.inspector,'drawer');
            value=layout.project({{width:1680,view:'search',searchMode:'librarian',librarianResultsOpen:true,hasSecondary:true,hasInspectorSelection:true}});
            assert.equal(value.context,'docked');assert.equal(value.secondary,'hidden');assert.equal(value.inspector,'hidden');assert.equal(value.librarianResults,'docked');assert.equal(value.dockedColumns,3);
            value=layout.project({{width:1680,view:'search',searchMode:'librarian',librarianResultsOpen:false,hasSecondary:true,hasInspectorSelection:true}});
            assert.equal(value.context,'docked');assert.equal(value.secondary,'docked');assert.equal(value.inspector,'hidden');assert.equal(value.librarianResults,'hidden');assert.equal(value.dockedColumns,3);
            value=layout.project({{width:1280,view:'search',searchMode:'librarian',librarianResultsOpen:false,hasSecondary:true,hasInspectorSelection:true}});
            assert.equal(value.context,'hidden');assert.equal(value.secondary,'docked');assert.equal(value.inspector,'hidden');assert.equal(value.librarianResults,'hidden');
            for(const view of ['package','settings']){{
              value=layout.project({{width:1680,view,hasSecondary:true,hasInspectorSelection:true}});
              assert.equal(value.secondary,'hidden');assert.equal(value.inspector,'hidden');
            }}
            console.log('WORKSPACE_LAYOUT_OK');
            """
        )
        result = subprocess.run(["node", "-e", program], text=True, capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WORKSPACE_LAYOUT_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
