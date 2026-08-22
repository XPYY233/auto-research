from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "src" / "auto_research" / "evidence" / "web" / "app.js"


class LibrarianHistoryRetentionUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runtime = APP_JS.read_text(encoding="utf-8")

    def test_history_uses_twenty_session_thirty_day_fail_closed_policy(self) -> None:
        self.assertIn("const librarianHistoryLimit = 20;", self.runtime)
        self.assertIn("const librarianHistoryRetentionMs = 30 * 24 * 60 * 60 * 1000;", self.runtime)
        self.assertIn("timestamp <= now && timestamp >= oldest", self.runtime)
        self.assertIn(".sort((left, right) => right.timestamp - left.timestamp)", self.runtime)
        self.assertIn("let sessions = validLibrarianSessions(state.librarianSessions);", self.runtime)
        self.assertNotIn("const librarianHistoryLimit = 16;", self.runtime)

    def test_actual_retention_function_rejects_expired_future_and_invalid_sessions(self) -> None:
        constants = re.search(
            r"const librarianHistoryLimit = 20;\nconst librarianHistoryRetentionMs = .*?;",
            self.runtime,
        )
        functions = re.search(
            r"function librarianSessionTimestamp\(session\) \{.*?\n\}\n\nfunction validLibrarianSessions\(value, now = Date\.now\(\)\) \{.*?\n\}",
            self.runtime,
            re.DOTALL,
        )
        self.assertIsNotNone(constants)
        self.assertIsNotNone(functions)
        program = f"""
const assert=require('assert');
{constants.group(0)}
{functions.group(0)}
const now=Date.parse('2026-08-22T12:00:00.000Z');
const iso=milliseconds=>new Date(now-milliseconds).toISOString();
const sessions=[];
for(let index=0;index<25;index+=1) sessions.push({{id:`recent-${{index}}`,messages:[],updated_at:iso(index*1000)}});
sessions.push({{id:'created-only',messages:[],created_at:iso(2*24*60*60*1000)}});
sessions.push({{id:'expired',messages:[],updated_at:iso(31*24*60*60*1000)}});
sessions.push({{id:'future',messages:[],updated_at:new Date(now+1).toISOString()}});
sessions.push({{id:'invalid',messages:[],updated_at:'not-a-time'}});
sessions.push({{id:'missing-time',messages:[]}});
sessions.reverse();
const kept=validLibrarianSessions(sessions,now);
assert.equal(kept.length,20);
assert.deepEqual(kept.map(item=>item.id),Array.from({{length:20}},(_,index)=>`recent-${{index}}`));
assert(!kept.some(item=>['expired','future','invalid','missing-time'].includes(item.id)));
assert.deepEqual(validLibrarianSessions({{}},now),[]);
assert.deepEqual(validLibrarianSessions(sessions,Number.NaN),[]);
"""
        result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
