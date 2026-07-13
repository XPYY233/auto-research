from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

import fitz
import requests

from auto_research.ai.deepseek import (
    DeepSeekClient,
    DeepSeekNotConfigured,
    DeepSeekSettings,
    DeepSeekUnavailableError,
)
from auto_research.evidence.db import EvidenceDB
from auto_research.evidence.uploads import UploadService


def make_pdf(text: str, *, metadata_title: str = "") -> bytes:
    document = fitz.open()
    if metadata_title:
        document.set_metadata({"title": metadata_title})
    for page_number in range(2):
        page = document.new_page()
        for line_number in range(24):
            page.insert_text(
                (54, 55 + line_number * 25),
                f"{text} page {page_number + 1} line {line_number + 1}",
                fontsize=8,
            )
    raw = document.tobytes()
    document.close()
    return raw


class UploadWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = EvidenceDB(root / "evidence.sqlite")
        self.service = UploadService(self.db, root / "papers")

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_pdf_is_validated_stored_and_queued_for_deepseek(self):
        result = self.service.upload(
            make_pdf("Ion irradiation experiment at 300 C with measured hardness"),
            "experiment.pdf",
            title="Ion irradiation experiment",
            doi="https://doi.org/10.1234/TEST.1",
            year=2024,
            first_author="Chen",
        )
        self.assertEqual(result["outcome"], "accepted")
        self.assertEqual(result["doi"], "10.1234/test.1")
        paper = self.db.get_paper(result["paper_id"])
        self.assertTrue(Path(paper["pdf_path"]).is_file())
        self.assertEqual(paper["local_article_key"], f"UPL{result['paper_id']:06d}")
        self.assertEqual(len(paper["documents"]), 1)
        jobs = self.db.list_processing_jobs()
        self.assertEqual((jobs[0]["job_type"], jobs[0]["provider"]), ("extract", "deepseek"))

    def test_exact_file_is_not_stored_or_queued_twice(self):
        raw = make_pdf("Exact duplicate irradiation paper")
        first = self.service.upload(raw, "first.pdf", title="Exact duplicate irradiation paper")
        second = self.service.upload(raw, "again.pdf", title="Exact duplicate irradiation paper")
        self.assertEqual(second["outcome"], "duplicate")
        self.assertEqual(second["match_type"], "exact_file")
        self.assertEqual(second["paper_id"], first["paper_id"])
        self.assertEqual(len(self.db.get_paper(first["paper_id"])["documents"]), 1)
        self.assertEqual(len(self.db.list_processing_jobs()), 1)

    def test_same_doi_different_pdf_becomes_alternate_version_not_extraction(self):
        first = self.service.upload(
            make_pdf("Accepted manuscript irradiation conditions"), "accepted.pdf",
            title="Versioned paper", doi="10.1234/versioned",
        )
        second = self.service.upload(
            make_pdf("Final publication irradiation conditions with corrected layout"), "final.pdf",
            title="Versioned paper final", doi="10.1234/versioned",
        )
        self.assertEqual(second["match_type"], "same_doi")
        self.assertTrue(second["alternate_version_saved"])
        self.assertEqual(second["paper_id"], first["paper_id"])
        self.assertEqual(len(self.db.get_paper(first["paper_id"])["documents"]), 2)
        jobs = self.db.list_processing_jobs()
        self.assertEqual(sum(job["job_type"] == "extract" for job in jobs), 1)
        self.assertEqual(sum(job["job_type"] == "duplicate_review" for job in jobs), 1)

    def test_title_layer_detects_duplicate_without_doi(self):
        first = self.service.upload(
            make_pdf("First body"), "one.pdf", title="Irradiation effects in tungsten alloys", year=2023,
        )
        second = self.service.upload(
            make_pdf("A completely revised body"), "two.pdf",
            title="Irradiation effects in tungsten alloy", year=2023,
        )
        self.assertEqual(second["outcome"], "duplicate")
        self.assertEqual(second["match_type"], "same_title")
        self.assertEqual(second["paper_id"], first["paper_id"])

    def test_text_layer_detects_same_content_with_different_metadata(self):
        body = "Unique neutron irradiation paragraph with fluence temperature and defect loops"
        first = self.service.upload(make_pdf(body, metadata_title="Alpha"), "alpha.pdf", title="Alpha study")
        second = self.service.upload(make_pdf(body, metadata_title="Beta"), "beta.pdf", title="Unrelated display title")
        self.assertEqual(second["outcome"], "duplicate")
        self.assertEqual(second["match_type"], "same_text")
        self.assertEqual(second["paper_id"], first["paper_id"])

    def test_blank_pdf_enters_ocr_queue_and_invalid_file_is_rejected(self):
        blank = fitz.open()
        blank.new_page()
        raw = blank.tobytes()
        blank.close()
        result = self.service.upload(raw, "scan.pdf", title="Scanned irradiation paper")
        self.assertTrue(result["needs_ocr"])
        self.assertEqual(self.db.list_processing_jobs()[0]["job_type"], "ocr")
        with self.assertRaisesRegex(ValueError, "PDF"):
            self.service.upload(b"not a pdf", "fake.pdf")
        self.assertEqual(self.db.list_upload_events()[0]["outcome"], "rejected")


class DeepSeekFrameworkTests(unittest.TestCase):
    def test_invalid_timeout_environment_uses_safe_bounded_default(self):
        with patch.dict(os.environ, {"DEEPSEEK_TIMEOUT_SECONDS": "not-a-number"}, clear=True), patch(
            "auto_research.ai.deepseek._read_project_keychain", return_value=None
        ):
            self.assertEqual(DeepSeekSettings.from_env().timeout_seconds, 180)
        with patch.dict(os.environ, {"DEEPSEEK_TIMEOUT_SECONDS": "99999"}, clear=True), patch(
            "auto_research.ai.deepseek._read_project_keychain", return_value=None
        ):
            self.assertEqual(DeepSeekSettings.from_env().timeout_seconds, 1800)

    def test_unconfigured_status_never_exposes_a_key(self):
        settings = DeepSeekSettings(api_key=None)
        status = settings.public_status()
        self.assertFalse(status["configured"])
        self.assertNotIn("api_key", status)
        with self.assertRaises(DeepSeekNotConfigured):
            DeepSeekClient(settings).request_json([
                {"role": "system", "content": "Return json."},
                {"role": "user", "content": "{}"},
            ])

    def test_json_control_character_is_recovered_without_changing_content(self):
        class Response:
            ok = True
            status_code = 200

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": '{"text":"line 1\nline 2"}'}}]}

        class Session:
            @staticmethod
            def post(*args, **kwargs):
                return Response()

        client = DeepSeekClient(DeepSeekSettings(api_key="fake"), session=Session())
        result = client.request_json([{"role": "user", "content": "Return json"}])
        self.assertEqual(result, {"text": "line 1\nline 2"})

    def test_invalid_json_is_retried_once(self):
        class Response:
            ok = True
            status_code = 200

            def __init__(self, content):
                self.content = content

            def json(self):
                return {"choices": [{"message": {"content": self.content}}]}

        class Session:
            calls = 0

            @classmethod
            def post(cls, *args, **kwargs):
                cls.calls += 1
                return Response("not-json" if cls.calls == 1 else '{"status":"ok"}')

        client = DeepSeekClient(DeepSeekSettings(api_key="fake"), session=Session())
        self.assertEqual(client.request_json([{"role": "user", "content": "Return json"}]), {"status": "ok"})
        self.assertEqual(Session.calls, 2)

    def test_transient_network_failure_is_retried_without_exposing_key(self):
        class Response:
            ok = True
            status_code = 200

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": '{"status":"ok"}'}}]}

        class Session:
            calls = 0

            @classmethod
            def post(cls, *args, **kwargs):
                cls.calls += 1
                if cls.calls == 1:
                    raise requests.Timeout("temporary timeout")
                return Response()

        client = DeepSeekClient(DeepSeekSettings(api_key="fake-secret"), session=Session())
        self.assertEqual(client.request_json([{"role": "user", "content": "Return json"}]), {"status": "ok"})
        self.assertEqual(Session.calls, 2)

    def test_http_429_is_retried_once(self):
        class Response:
            def __init__(self, status_code):
                self.status_code = status_code
                self.ok = status_code == 200

            def json(self):
                return {"choices": [{"message": {"content": '{"status":"ok"}'}}]}

        class Session:
            calls = 0

            @classmethod
            def post(cls, *args, **kwargs):
                cls.calls += 1
                return Response(429 if cls.calls == 1 else 200)

        client = DeepSeekClient(DeepSeekSettings(api_key="fake"), session=Session())
        self.assertEqual(client.request_json([{"role": "user", "content": "Return json"}]), {"status": "ok"})
        self.assertEqual(Session.calls, 2)

    def test_repeated_http_503_is_classified_as_provider_unavailable(self):
        class Response:
            ok = False
            status_code = 503

        class Session:
            calls = 0

            @classmethod
            def post(cls, *args, **kwargs):
                cls.calls += 1
                return Response()

        client = DeepSeekClient(DeepSeekSettings(api_key="fake"), session=Session())
        with self.assertRaises(DeepSeekUnavailableError):
            client.request_json([{"role": "user", "content": "Return json"}])
        self.assertEqual(Session.calls, 2)

    def test_project_keychain_is_used_without_exposing_secret(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "auto_research.ai.deepseek._read_project_keychain", return_value="fake-project-secret"
        ):
            settings = DeepSeekSettings.from_env()
        self.assertEqual(settings.api_key, "fake-project-secret")
        status = settings.public_status()
        self.assertTrue(status["configured"])
        self.assertEqual(status["credential_source"], "macOS Keychain:auto-research-deepseek")
        self.assertNotIn("api_key", status)


if __name__ == "__main__":
    unittest.main()
