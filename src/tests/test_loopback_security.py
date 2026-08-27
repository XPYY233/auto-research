from __future__ import annotations

import io
import subprocess
import unittest
from email.message import Message
from pathlib import Path

from auto_research.evidence.webapp import EvidenceHandler, serve


def synthetic_handler(*headers: tuple[str, str], body: bytes = b"") -> EvidenceHandler:
    handler = EvidenceHandler.__new__(EvidenceHandler)
    message = Message()
    for name, value in headers:
        message.add_header(name, value)
    handler.headers = message
    handler.rfile = io.BytesIO(body)
    return handler


class LoopbackRequestSecurityTests(unittest.TestCase):
    def test_editable_core_service_requires_explicit_development_opt_in(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "editable browser service is retired"):
            serve(host="127.0.0.1", port=0, read_only=False)

    def test_json_rejects_negative_and_duplicate_content_length(self) -> None:
        negative = synthetic_handler(("Content-Length", "-1"), body=b"{}")
        with self.assertRaisesRegex(ValueError, "Invalid Content-Length"):
            negative.read_json()

        duplicate = synthetic_handler(
            ("Content-Length", "2"),
            ("Content-Length", "2"),
            body=b"{}",
        )
        with self.assertRaisesRegex(ValueError, "Exactly one Content-Length"):
            duplicate.read_json()

    def test_json_rejects_transfer_encoding_and_conflicting_framing(self) -> None:
        chunked = synthetic_handler(
            ("Transfer-Encoding", "chunked"),
            ("Content-Length", "2"),
            body=b"{}",
        )
        with self.assertRaisesRegex(ValueError, "Transfer-Encoding"):
            chunked.read_json()

    def test_json_rejects_short_body(self) -> None:
        short = synthetic_handler(("Content-Length", "3"), body=b"{}")
        with self.assertRaisesRegex(ValueError, "Incomplete request body"):
            short.read_json()

        missing = synthetic_handler(body=b"")
        with self.assertRaisesRegex(ValueError, "Exactly one Content-Length"):
            missing.read_json()

    def test_json_accepts_one_exact_bounded_body(self) -> None:
        valid = synthetic_handler(("Content-Length", "11"), body=b'{"ok":true}')
        self.assertEqual(valid.read_json(), {"ok": True})

    def test_shared_frontend_attaches_desktop_csrf_to_mutations(self) -> None:
        fusion_js = (
            Path(__file__).resolve().parents[1]
            / "auto_research"
            / "evidence"
            / "web"
            / "fusion_review.js"
        )
        program = r'''
const fs=require("fs"),assert=require("assert"),source=fs.readFileSync(process.argv[1],"utf8");
const start=source.indexOf("  function isAllowedRoute"),end=source.indexOf("  async function readOnlyJSON",start);assert(start>0&&end>start);
const ROUTES=new Proxy({settings:"/api/desktop/settings",preferences:"/api/desktop/settings/preferences",personalSearchRefresh:"/api/desktop/personal-imports/search-refresh",packageHistory:"/api/desktop/package-center/history"},{get:(target,key)=>target[key]||`/unused/${String(key)}`});
const PERSONAL_IMPORT_ROWS_PATH=/a^/,CSRF_HEADER="X-Auto-Research-CSRF",state={csrfToken:""},cleanText=value=>String(value??"");
class TestHeaders{constructor(values={}){this.values={};for(const [key,value] of Object.entries(values))this.set(key,value);}set(key,value){this.values[String(key).toLowerCase()]=String(value);}get(key){return this.values[String(key).toLowerCase()]||null;}}
globalThis.Headers=TestHeaders;const calls=[];let rotation=0;
globalThis.fetch=async(url,options={})=>{calls.push({url:String(url),options});rotation+=1;return{ok:true,headers:{get:name=>name===CSRF_HEADER?`csrf-${rotation}`:null},json:async()=>({ok:true})};};
eval(source.slice(start,end));
(async()=>{
 await request(ROUTES.settings);assert.equal(state.csrfToken,"csrf-1");assert.equal(calls[0].options.headers.get(CSRF_HEADER),null);
 await request(ROUTES.personalSearchRefresh,{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});assert.equal(calls[1].options.headers.get(CSRF_HEADER),"csrf-1");
 await request(ROUTES.preferences,{method:"PATCH",headers:{"Content-Type":"application/json"},body:"{}"});assert.equal(calls[2].options.headers.get(CSRF_HEADER),"csrf-2");
 await request("/api/desktop/package-center/jobs/package_job_1234567890/receipt-retry",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});assert.equal(calls[3].options.headers.get(CSRF_HEADER),"csrf-3");
 await request(ROUTES.packageHistory);assert.equal(calls[4].options.headers.get(CSRF_HEADER),null);
 await request(ROUTES.packageHistory,{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});assert.equal(calls[5].options.headers.get(CSRF_HEADER),"csrf-5");
 await request("/api/desktop/package-center/history/"+"a".repeat(64)+"/receipt-retry",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});assert.equal(calls[6].options.headers.get(CSRF_HEADER),"csrf-6");
 await request("/api/desktop/ai/credentials/deepseek",{method:"DELETE"});assert.equal(calls[7].options.headers.get(CSRF_HEADER),"csrf-7");
 const before=calls.length;await assert.rejects(()=>request("/api/desktop/not-authorized"),error=>error.code==="fusion_route_blocked");assert.equal(calls.length,before);
 assert.deepEqual(calls.map(call=>call.options.method),["GET","POST","PATCH","POST","GET","POST","POST","DELETE"]);
})().catch(error=>{console.error(error);process.exit(1);});
'''
        result = subprocess.run(
            ["node", "-e", program, str(fusion_js)],
            text=True,
            capture_output=True,
            check=False,
            timeout=8,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
