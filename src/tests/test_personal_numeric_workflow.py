"""Real parser/import/private-search/page chain on disposable numerical CSV."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from auto_research.evidence.federated_search import FederatedEvidenceSearch
from auto_research.personal.import_service import PersonalImportService
from auto_research.personal.private_repository import PrivateExperimentRepository
from auto_research.personal.table_detail import PersonalTableDetailService, PersonalTableError


class _Selection:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def snapshot(self, selection_id):
        assert selection_id == "personal_selection_numeric_123456"
        yield SimpleNamespace(path=self.path)

    def revoke(self, selection_id):
        assert selection_id == "personal_selection_numeric_123456"


class PersonalNumericWorkflowTests(unittest.TestCase):
    def test_confirmed_numeric_csv_survives_search_pagination_and_service_restart(self):
        with tempfile.TemporaryDirectory(prefix="personal-numeric-workflow-") as temporary:
            root = Path(temporary)
            path = root / "合成剂量硬度.csv"
            columns = ["Dose (dpa)", "Hardness [GPa]", "Uncertainty [GPa]"]
            rows = [
                [str(Decimal(i) / 100), "" if i == 49 else str(Decimal("3.200") + Decimal(i) / 1000), "0.050"]
                for i in range(123)
            ]
            path.write_text(
                ",".join(columns) + "\n" + "\n".join(",".join(row) for row in rows) + "\n", encoding="utf-8"
            )
            provider = _Selection(path)
            private_root = root / "isolated-private"
            service = PersonalImportService(data_root=private_root, selection_provider=provider)
            preview = service.preview("personal_selection_numeric_123456")
            import_id = preview.status.import_id
            self.assertEqual(preview.preview.sheets[0].row_count, 123)
            self.assertEqual(service.private_search_source().list_documents(), ())
            payload = {
                "sheet_index": 0,
                "project": {"name": "合成数值验收"},
                "sample": {"name": "SYNTHETIC-W-01", "material": "W"},
                "run": {"name": "合成硬度序列", "method": "合成纳米压痕", "conditions": {"温度": "300 K"}},
                "columns": [
                    {"source_name": name, "role": role, "role_confirmed": False,
                     "meaning": meaning, "meaning_confirmed": False, "unit": unit, "unit_confirmed": False}
                    for name, role, meaning, unit in zip(columns, ["independent", "dependent", "uncertainty"],
                        ["辐照剂量", "硬度", "硬度不确定度"], ["dpa", "GPa", "GPa"], strict=True)
                ],
                "series": [{"series_id": "hardness-dose", "name": "合成硬度随剂量",
                            "x_column": columns[0], "y_column": columns[1], "uncertainty_column": columns[2]}],
            }
            # Once previewed, the held copy is authoritative, not later changes to the selected file.
            path.write_text("changed after preview\n", encoding="utf-8")
            confirmed = service.import_reviewed(import_id, payload, reviewed=True)
            self.assertTrue(confirmed.indexable)
            self.assertEqual(service.import_reviewed(import_id, payload, reviewed=True), confirmed)
            action = service.confirmed_table_next_action(import_id).public_dict()
            search = FederatedEvidenceSearch([service.private_search_source()])
            hits = search.search("合成硬度", entity_types=["table"], source_scopes=["private"]).hits
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].document["entity_uid"], action["entity_uid"])
            self.assertEqual(search.search("", source_scopes=["official"]).total, 0)

            # Reopen services from disk, with no in-memory import session or original CSV dependency.
            reopened = PersonalImportService(data_root=private_root, selection_provider=provider)
            self.assertEqual(reopened.private_search_snapshot().content_fingerprint,
                             service.private_search_snapshot().content_fingerprint)
            table = PersonalTableDetailService(PrivateExperimentRepository(private_root))
            identity = {key: action[key] for key in ("source_id", "entity_uid")}
            pages = [table.get_page(**identity, page=i, page_size=50).public_dict() for i in range(1, 5)]
            self.assertEqual([len(page["rows"]) for page in pages], [50, 50, 23, 0])
            self.assertEqual([page["has_next"] for page in pages], [True, True, False, False])
            actual = [[row[name] for name in columns] for page in pages for row in page["rows"]]
            self.assertEqual(actual, rows, "all source values must survive, including an empty measurement")
            plot = table.get_series(**identity, series_index=0)
            self.assertEqual(plot["schema_version"], "personal-series-plot-v1")
            self.assertEqual(plot["total_rows"], 123, "plot is full series, not current 50-row page")
            self.assertEqual(plot["valid_points"], 122)
            self.assertEqual(plot["missing_rows"], 1)
            self.assertEqual(plot["invalid_rows"], 0)
            self.assertEqual(plot["points"][49]["status"], "missing")
            self.assertIsNone(plot["points"][49]["y"])
            self.assertEqual(plot["points"][-1]["row"], 123)
            self.assertEqual(plot["points"][-1]["y_text"], rows[-1][1])
            self.assertEqual(plot["points"][-1]["uncertainty_text"], "0.050")
            for page in pages:
                self.assertEqual(page["total"], 123)
                self.assertEqual(page["conditions"], {"温度": "300 K"})
                self.assertEqual([c["unit"] for c in page["columns"]], ["dpa", "GPa", "GPa"])
                self.assertEqual(page["series"][0]["uncertainty_column"], columns[2])
                self.assertEqual(page["series"][0]["x_column"], columns[0])
                self.assertEqual(page["series"][0]["y_column"], columns[1])
            serialized = json.dumps([action, pages, plot], ensure_ascii=False)
            for forbidden in (str(root), "run_id", "file_id", "project_id", "sample_id", "draft_id", "sha256"):
                self.assertNotIn(forbidden, serialized)
            web = Path(__file__).resolve().parents[1] / "auto_research" / "evidence" / "web"
            program = r"""
const fs=require('fs'),assert=require('assert'),input=JSON.parse(fs.readFileSync(0,'utf8'));
globalThis.document={readyState:'loading',querySelector:()=>null,querySelectorAll:()=>[],addEventListener(){}};
eval(fs.readFileSync(RUNTIME,'utf8'));
const api=globalThis.AutoResearchFusion,identity={sourceId:input.action.source_id,entityUid:input.action.entity_uid};
eval(fs.readFileSync(SERIES_RUNTIME,'utf8'));
const plot=AutoResearchPersonalSeries.validate(input.plot,{...identity,seriesIndex:0});
assert(plot,'real confirmed service projection accepted by renderer');
const geometry=AutoResearchPersonalSeries.geometry(plot);
assert.equal(geometry.segments.length,2,'missing row splits real series');
assert.deepEqual(geometry.segments.map(segment=>segment.length),[49,73]);
const chart=AutoResearchPersonalSeries.render(plot,123);
assert.equal((chart.match(/data-series-point=/g)||[]).length,122);
assert.equal((chart.match(/data-series-source-row=/g)||[]).length,123);
assert(chart.includes('原始第 123 行'));assert(chart.includes('3.322'));assert(chart.includes('0.050'));
assert(!chart.includes('NaN'));assert(!chart.includes('Infinity'));
for(const raw of input.pages){
 const page=api.publicPersonalTablePage(raw,identity,raw.page);assert(page,'real page contract accepted');
 assert.deepEqual(page.rows,raw.rows);
 const html=api.secondaryDocumentHTML({tabId:'personal-table:numeric',kind:'personal-table',title:raw.title,payload:{row:{title:raw.title},page}});
 assert(html.includes('300 K'));assert(html.includes('合成硬度随剂量'));assert(html.includes('Uncertainty [GPa]'));
 for(const row of raw.rows)for(const value of Object.values(row))if(value)assert(html.includes(value));
 if(!raw.rows.length)assert(html.includes('当前页没有数据'),'empty trailing page must not show reversed row range');
}
""".replace("SERIES_RUNTIME", repr(str(web / "fusion_personal_series.js"))).replace("RUNTIME", repr(str(web / "fusion_review.js")))
            result = subprocess.run(["node", "-e", program], input=json.dumps({"action": action, "pages": pages, "plot": plot}),
                                    text=True, capture_output=True, timeout=8)
            self.assertEqual(result.returncode, 0, result.stderr)
            with self.assertRaises(PersonalTableError):
                table.get_page(source_id=identity["source_id"], entity_uid="private:table:" + "0" * 32)
            self.assertEqual(list((private_root / ".import-staging").glob("*")), [])


if __name__ == "__main__":
    unittest.main()
