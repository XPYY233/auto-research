from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_settings
from .db import ResearchDB
from .discovery.themes import expand_query
from .discovery.providers import discover_all, enrich_unpaywall
from .acquisition.downloader import AcquisitionManager
from .parsing.pdf_parser import PDFParser
from .analysis.extractor import Analyzer
from .matrix.generator import MatrixGenerator
from .verification.authenticity import AuthenticityVerifier
from .zotero.client import ZoteroClient
from .browser.handoff import open_for_handoff
from .browser.playwright_acquirer import BrowserAcquirer
from .models import PaperState
from .paths import ensure_dirs, DB_PATH, DATA_DIR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="auto-research", description="Local Auto Research pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Initialize directories and SQLite database")

    p = sub.add_parser("discover", help="Discover candidate papers from open/official sources")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--no-unpaywall", action="store_true")

    p = sub.add_parser("list", help="List papers in the task database")
    p.add_argument("--state", action="append")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("acquire", help="Download legal/open PDF candidates")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("attach-pdf", help="Attach a manually downloaded PDF and resume")
    p.add_argument("paper_id", type=int)
    p.add_argument("pdf_path")

    p = sub.add_parser("verify", help="Verify paper authenticity via DOI/registry/provenance/PDF checks")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--include-all", action="store_true")

    p = sub.add_parser("parse", help="Parse downloaded PDFs")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("analyze", help="Generate research cards")
    p.add_argument("--limit", type=int, default=20)

    sub.add_parser("matrix", help="Generate review matrices")

    p = sub.add_parser("run", help="Run discover -> acquire -> parse -> analyze -> matrix")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("open", help="Open a paper landing page for human handoff")
    p.add_argument("paper_id", type=int)

    p = sub.add_parser("browser-acquire", help="Use a real headful browser session for one paper")
    p.add_argument("paper_id", type=int)
    p.add_argument("--wait", type=int, default=90)

    sub.add_parser("zotero-status", help="Probe local Zotero connector")
    sub.add_parser("zotero-export", help="Export RIS and notes index for Zotero import")

    sub.add_parser("evidence-init", help="Initialize the independent irradiation evidence database")
    sub.add_parser("evidence-seed", help="Select the 30-paper pilot and import the six-paper benchmark")
    sub.add_parser("evidence-select-pilot", help="Select a balanced 30-paper HEA/W irradiation pilot")
    sub.add_parser("evidence-import-sample", help="Import the six-paper/108-record benchmark as drafts")
    p = sub.add_parser("evidence-import-ai", help="Validate and import schema-constrained AI JSON as drafts")
    p.add_argument("paper_id", type=int)
    p.add_argument("json_path")
    p = sub.add_parser("evidence-prompt", help="Create a local, excerpt-only extraction packet for one paper")
    p.add_argument("paper_id", type=int)
    p.add_argument("--max-pages", type=int, default=8)
    p = sub.add_parser("evidence-prepare-pilot", help="Create excerpt-only extraction packets for all 30 pilot papers")
    p.add_argument("--max-pages", type=int, default=8)
    p = sub.add_parser("evidence-run-article", help="Run the local six-column extraction workflow for one article key")
    p.add_argument("article_key", help="Local article key, Zotero key, pilot code, or paper id")
    p.add_argument("--max-pages", type=int, default=8)
    p = sub.add_parser("evidence-export", help="Export evidence records to CSV")
    p.add_argument("--out")
    p.add_argument("--include-drafts", action="store_true")
    p.add_argument("--measured-only", action="store_true")
    p = sub.add_parser("evidence-serve", help="Start the local Chinese review/search interface")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    sub.add_parser("evidence-summary", help="Show evidence database counts")
    sub.add_parser("evidence-validate", help="Validate pilot balance and evidence publication gates")
    sub.add_parser("evidence-seed-target", help="Seed the six-column demo for the single target irradiation article")
    sub.add_parser("evidence-deepseek-status", help="Show redacted DeepSeek runtime configuration")
    sub.add_parser("evidence-deepseek-smoke-test", help="Send a minimal synthetic JSON connection check to DeepSeek")
    p = sub.add_parser("evidence-deepseek-extract", help="Run evidence-grounded DeepSeek extraction for one article key")
    p.add_argument("article_key")
    p.add_argument("--commit", action="store_true", help="Import verified candidates only when the paper has no six-column rows")
    p.add_argument("--max-pages", type=int)
    p.add_argument("--chunk-pages", type=int, default=2)

    args = parser.parse_args(argv)
    db = ResearchDB()
    settings = load_settings()

    if args.cmd == "init":
        ensure_dirs()
        db.init()
        print(f"Initialized Auto Research DB: {DB_PATH}")
        return 0

    if args.cmd == "discover":
        return cmd_discover(args.query, args.limit, not args.no_unpaywall, db, settings.sources, settings.themes)

    if args.cmd == "list":
        rows = db.list_papers(states=args.state, limit=args.limit)
        for r in rows:
            auth = r['authenticity_status'] or 'not_verified'
            auth_score = '' if r['authenticity_score'] is None else f"/{r['authenticity_score']:.2f}"
            print(f"#{r['id']:04d} [{r['state']}] auth={auth}{auth_score} score={r['relevance_score']:.1f} {r['title']} ({r['year'] or 'unknown'}) DOI={r['doi'] or '-'}")
        return 0

    if args.cmd == "acquire":
        ok, failed = AcquisitionManager(db).acquire_batch(args.limit)
        print(f"Acquisition complete: downloaded={ok}, blocked/failed={failed}")
        return 0

    if args.cmd == "attach-pdf":
        AcquisitionManager(db).attach_pdf(args.paper_id, Path(args.pdf_path))
        print(f"Attached PDF to paper #{args.paper_id}")
        return 0

    if args.cmd == "verify":
        ok, review = AuthenticityVerifier(db).verify_batch(args.limit, args.include_all)
        print(f"Authenticity verification complete: trusted={ok}, needs_review={review}")
        return 0

    if args.cmd == "parse":
        AuthenticityVerifier(db).verify_batch(args.limit, include_all=False)
        ok, failed = PDFParser(db).parse_batch(args.limit)
        print(f"Parsing complete: parsed={ok}, failed={failed}")
        return 0

    if args.cmd == "analyze":
        AuthenticityVerifier(db).verify_batch(args.limit, include_all=False)
        ok, failed = Analyzer(db).analyze_batch(args.limit)
        print(f"Analysis complete: analyzed={ok}, failed={failed}")
        return 0

    if args.cmd == "matrix":
        paths = MatrixGenerator(db).generate()
        for k, v in paths.items():
            print(f"{k}: {v}")
        return 0

    if args.cmd == "run":
        cmd_discover(args.query, args.limit, True, db, settings.sources, settings.themes)
        ok, failed = AcquisitionManager(db).acquire_batch(args.limit)
        print(f"Acquisition complete: downloaded={ok}, blocked/failed={failed}")
        vok, vreview = AuthenticityVerifier(db).verify_batch(args.limit, include_all=False)
        print(f"Authenticity verification complete: trusted={vok}, needs_review={vreview}")
        ok, failed = PDFParser(db).parse_batch(args.limit)
        print(f"Parsing complete: parsed={ok}, failed={failed}")
        ok, failed = Analyzer(db).analyze_batch(args.limit)
        print(f"Analysis complete: analyzed={ok}, failed={failed}")
        paths = MatrixGenerator(db).generate()
        for k, v in paths.items():
            print(f"{k}: {v}")
        return 0

    if args.cmd == "open":
        rows = db.list_papers(limit=100000)
        paper = next((r for r in rows if int(r["id"]) == args.paper_id), None)
        if not paper or not paper["url"]:
            raise SystemExit(f"No URL found for paper #{args.paper_id}")
        open_for_handoff(paper["url"])
        print(f"Opened paper #{args.paper_id}: {paper['title']}")
        return 0

    if args.cmd == "browser-acquire":
        rows = db.list_papers(limit=100000)
        paper = next((r for r in rows if int(r["id"]) == args.paper_id), None)
        if not paper or not paper["url"]:
            raise SystemExit(f"No URL found for paper #{args.paper_id}")
        result = BrowserAcquirer(DATA_DIR / "browser-profile", DATA_DIR / "downloads").open_and_wait_for_download(paper["url"], args.wait)
        if result.downloaded_path:
            AcquisitionManager(db).attach_pdf(args.paper_id, result.downloaded_path)
            print(f"Downloaded and attached: {result.downloaded_path}")
        else:
            with db.connect() as conn:
                db.set_state(conn, args.paper_id, result.state)
                db.event(conn, args.paper_id, "browser_handoff", result.message)
            print(result.message)
        return 0

    if args.cmd == "zotero-status":
        print(json.dumps(ZoteroClient(db=db).status(), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "zotero-export":
        z = ZoteroClient(db=db)
        print(f"RIS: {z.export_ris_batch()}")
        print(f"Notes: {z.export_notes()}")
        return 0

    if args.cmd.startswith("evidence-"):
        return cmd_evidence(args)

    return 1


def cmd_discover(query: str, limit: int, use_unpaywall: bool, db: ResearchDB, source_settings: dict, theme_settings: dict) -> int:
    db.init()
    expanded = expand_query(query, theme_settings)
    print(f"Expanded tags: {', '.join(expanded.tags) or 'none'}")
    print(f"Searching: {query}")
    candidates = discover_all(expanded, source_settings, limit=limit)
    inserted = 0
    for c in candidates:
        if use_unpaywall and c.doi:
            try:
                c.sources.extend(enrich_unpaywall(c.doi, source_settings))
            except Exception as e:
                print(f"[warn] unpaywall enrichment failed for {c.doi}: {e}")
        db.upsert_candidate(c)
        inserted += 1
    print(f"Discovered/upserted {inserted} candidates")
    return 0


def cmd_evidence(args) -> int:
    from .ai.deepseek import DeepSeekClient, DeepSeekSettings
    from .evidence.db import EvidenceDB, EVIDENCE_DB_PATH
    from .evidence.exporter import export_measurements
    from .evidence.importers import import_ai_result, import_legacy_sample
    from .evidence.pilot import select_pilot
    from .evidence.prompts import build_prompt_packet, prepare_pilot_packets

    evidence_db = EvidenceDB()
    if args.cmd == "evidence-deepseek-status":
        print(json.dumps(DeepSeekSettings.from_env().public_status(), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "evidence-deepseek-smoke-test":
        print(json.dumps(DeepSeekClient().smoke_test(), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "evidence-deepseek-extract":
        from .evidence.deepseek_extraction import DeepSeekEvidenceExtractor
        from .evidence.six_column import resolve_paper_selector

        paper_id = resolve_paper_selector(evidence_db, article_key=args.article_key)
        result = DeepSeekEvidenceExtractor(evidence_db).run(
            paper_id, commit=args.commit, max_pages=args.max_pages, chunk_pages=args.chunk_pages
        )
        summary = {key: result[key] for key in (
            "run_id", "paper", "provider", "model", "mode", "chunk_count",
            "candidate_count", "verified_count", "rejected_count", "duplicate_count",
            "comparison", "imported", "output_path",
        )}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "evidence-init":
        evidence_db.init()
        print(f"Initialized irradiation evidence DB: {EVIDENCE_DB_PATH}")
        return 0
    if args.cmd == "evidence-select-pilot":
        rows = select_pilot(evidence_db)
        print(f"Selected {len(rows)} verified local-PDF papers for the pilot")
        return 0
    if args.cmd == "evidence-import-sample":
        print(json.dumps(import_legacy_sample(evidence_db), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "evidence-seed":
        rows = select_pilot(evidence_db)
        imported = import_legacy_sample(evidence_db)
        print(json.dumps({"pilot_papers": len(rows), **imported, "database": str(EVIDENCE_DB_PATH)}, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "evidence-import-ai":
        print(json.dumps(import_ai_result(evidence_db, args.paper_id, Path(args.json_path)), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "evidence-prompt":
        print(build_prompt_packet(evidence_db, args.paper_id, args.max_pages))
        return 0
    if args.cmd == "evidence-prepare-pilot":
        result = prepare_pilot_packets(evidence_db, args.max_pages)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not result["failed"] else 2
    if args.cmd == "evidence-run-article":
        from .evidence.workflow import run_article_workflow
        result = run_article_workflow(evidence_db, article_key=args.article_key, max_pages=args.max_pages)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "evidence-export":
        path = Path(args.out).expanduser().resolve() if args.out else None
        evidence_type = "measured" if args.measured_only else None
        print(export_measurements(evidence_db, path, args.include_drafts, evidence_type))
        return 0
    if args.cmd == "evidence-serve":
        if args.host not in {"127.0.0.1", "localhost", "::1"}:
            raise SystemExit("Evidence UI is local-only; bind to 127.0.0.1, localhost, or ::1")
        from .evidence.webapp import serve
        serve(evidence_db, args.host, args.port)
        return 0
    if args.cmd == "evidence-summary":
        print(json.dumps(evidence_db.summary(), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "evidence-validate":
        from .evidence.validation import validate_database
        report = validate_database(evidence_db)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 2
    if args.cmd == "evidence-seed-target":
        from .evidence.six_column import seed_target_article
        print(json.dumps(seed_target_article(evidence_db), ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
