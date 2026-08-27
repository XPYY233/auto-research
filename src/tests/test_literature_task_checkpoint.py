from __future__ import annotations

import ast
import hashlib
import hmac
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from auto_research.evidence.literature_task_checkpoint import (
    MAX_PRIVATE_PAYLOAD_BYTES,
    LiteratureTaskCheckpoint,
    LiteratureTaskCheckpointError,
    LiteratureTaskManifest,
)
from auto_research.evidence.literature_task_checkpoint_service import (
    LiteratureTaskCheckpointService,
)
from auto_research.evidence.literature_task_checkpoint_store import (
    SealedSQLiteLiteratureCheckpointStore,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class _TestSealer:
    """Authenticated test double; production encryption belongs to the platform."""

    def __init__(self, key: bytes = b"test-only-checkpoint-key") -> None:
        self._key = key

    def seal(self, plaintext: bytes, *, associated_data: bytes) -> bytes:
        stream = self._stream(associated_data, len(plaintext))
        ciphertext = bytes(left ^ right for left, right in zip(plaintext, stream, strict=True))
        tag = hmac.new(self._key, associated_data + ciphertext, hashlib.sha256).digest()
        return tag + ciphertext

    def open(self, ciphertext: bytes, *, associated_data: bytes) -> bytes:
        if len(ciphertext) < 32:
            raise ValueError("bad ciphertext")
        tag, body = ciphertext[:32], ciphertext[32:]
        expected = hmac.new(self._key, associated_data + body, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError("bad tag")
        stream = self._stream(associated_data, len(body))
        return bytes(left ^ right for left, right in zip(body, stream, strict=True))

    def _stream(self, associated_data: bytes, length: int) -> bytes:
        blocks: list[bytes] = []
        counter = 0
        while sum(len(block) for block in blocks) < length:
            blocks.append(
                hmac.new(
                    self._key,
                    associated_data + counter.to_bytes(8, "big"),
                    hashlib.sha256,
                ).digest()
            )
            counter += 1
        return b"".join(blocks)[:length]


class _Clock:
    def __init__(self, value: int = 1_000) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += seconds


def _manifest(*, max_calls: int = 4, max_tokens: int = 4_000) -> LiteratureTaskManifest:
    return LiteratureTaskManifest(
        task_id="task_ABCDEFGHIJKLMNOP",
        session_digest=_sha("session"),
        provider_id="deepseek",
        runtime_revision=7,
        credential_generation=3,
        task_models=(
            ("analysis", "deepseek-v4-flash"),
            ("extraction", "deepseek-v4-pro"),
        ),
        executor_id="literature_extraction_executor",
        executor_version="v1",
        pdf_snapshot_fingerprint=_sha("pdf snapshot"),
        max_calls=max_calls,
        max_tokens=max_tokens,
        issued_at=900,
        expires_at=10_000,
    )


class LiteratureTaskCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "checkpoints"
        self.clock = _Clock()
        self.sealer = _TestSealer()
        self.store = SealedSQLiteLiteratureCheckpointStore(
            data_root=self.root,
            sealer=self.sealer,
        )
        self.service = LiteratureTaskCheckpointService(store=self.store, clock=self.clock)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _create_and_acquire(self, *, manifest: LiteratureTaskManifest | None = None):
        created = self.service.create(
            manifest=manifest or _manifest(),
            private_payload=b'snapshot-ref:opaque; domain-state:{"stage":"initial_focus"}',
        )
        return self.service.acquire(
            created.manifest.task_id,
            expected_revision=created.revision,
            owner_id="worker-a",
        )

    def _plan_and_begin(self, checkpoint, *, max_tokens: int = 500):
        call_digest = _sha(f"call-{len(checkpoint.receipts) + 1}")
        planned = self.service.plan_call(
            checkpoint.manifest.task_id,
            expected_revision=checkpoint.revision,
            owner_id="worker-a",
            stage="initial_focus",
            task="extraction",
            call_digest=call_digest,
            max_tokens=max_tokens,
        )
        begun = self.service.begin_call(
            checkpoint.manifest.task_id,
            expected_revision=planned.revision,
            owner_id="worker-a",
            call_digest=call_digest,
        )
        return call_digest, begun

    def assert_error(self, code: str, callback) -> LiteratureTaskCheckpointError:
        with self.assertRaises(LiteratureTaskCheckpointError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        public = caught.exception.public_dict()
        self.assertNotIn(str(self.root), str(public))
        return caught.exception

    def test_round_trip_survives_store_reopen_and_body_is_sealed(self) -> None:
        checkpoint = self.service.create(
            manifest=_manifest(),
            private_payload=b"UNIQUE_PRIVATE_PDF_AND_PROMPT",
        )
        raw_db = (self.root / "literature-task-checkpoints-v1.sqlite").read_bytes()
        self.assertNotIn(b"UNIQUE_PRIVATE_PDF_AND_PROMPT", raw_db)
        self.assertNotIn(checkpoint.manifest.pdf_snapshot_fingerprint.encode(), raw_db)

        reopened = SealedSQLiteLiteratureCheckpointStore(data_root=self.root, sealer=self.sealer)
        loaded = reopened.load(checkpoint.manifest.task_id)
        self.assertEqual(loaded, checkpoint)
        self.assertEqual(loaded.private_payload, b"UNIQUE_PRIVATE_PDF_AND_PROMPT")

    def test_task_listing_is_bounded_recent_and_opaque(self) -> None:
        first = self.service.create(
            manifest=_manifest(),
            private_payload=b"first-private-state",
        )
        second_manifest = LiteratureTaskManifest(
            **{
                **_manifest().__dict__,
                "task_id": "task_QRSTUVWXYZabcdef",
                "issued_at": 901,
            }
        )
        self.clock.advance(1)
        second = self.service.create(
            manifest=second_manifest,
            private_payload=b"second-private-state",
        )

        self.assertEqual(self.store.list_task_ids(limit=1), (second.manifest.task_id,))
        self.assertEqual(
            self.store.list_task_ids(limit=2),
            (second.manifest.task_id, first.manifest.task_id),
        )
        with self.assertRaises(LiteratureTaskCheckpointError) as caught:
            self.store.list_task_ids(limit=0)
        self.assertEqual(caught.exception.code, "literature_checkpoint_invalid")

    def test_compare_and_swap_rejects_stale_writer(self) -> None:
        created = self.service.create(manifest=_manifest(), private_payload=b"state")
        acquired = self.service.acquire(
            created.manifest.task_id,
            expected_revision=0,
            owner_id="worker-a",
        )
        self.assertEqual(acquired.revision, 1)
        self.assert_error(
            "literature_checkpoint_conflict",
            lambda: self.service.acquire(
                created.manifest.task_id,
                expected_revision=0,
                owner_id="worker-b",
            ),
        )

    def test_single_flight_blocks_other_owner_until_clean_lease_expiry(self) -> None:
        acquired = self._create_and_acquire()
        self.assert_error(
            "literature_checkpoint_busy",
            lambda: self.service.acquire(
                acquired.manifest.task_id,
                expected_revision=acquired.revision,
                owner_id="worker-b",
            ),
        )
        self.clock.advance(301)
        recovered = self.service.recover(acquired.manifest.task_id)
        self.assertIsNone(recovered.lease_owner_digest)
        reacquired = self.service.acquire(
            acquired.manifest.task_id,
            expected_revision=recovered.revision,
            owner_id="worker-b",
        )
        self.assertEqual(reacquired.state, "running")

    def test_per_call_receipt_is_charged_before_call_and_completed_once(self) -> None:
        acquired = self._create_and_acquire()
        call_digest, begun = self._plan_and_begin(acquired)
        self.assertEqual((begun.spent_calls, begun.spent_tokens), (1, 500))
        self.assertEqual(begun.receipts[0].state, "in_flight")
        completed = self.service.complete_call(
            begun.manifest.task_id,
            expected_revision=begun.revision,
            owner_id="worker-a",
            call_digest=call_digest,
            result_digest=_sha("model result"),
            private_payload=b"validated intermediate result",
        )
        self.assertEqual(completed.receipts[0].state, "succeeded")
        self.assertEqual(completed.private_payload, b"validated intermediate result")
        self.assert_error(
            "literature_call_replayed",
            lambda: self.service.begin_call(
                completed.manifest.task_id,
                expected_revision=completed.revision,
                owner_id="worker-a",
                call_digest=call_digest,
            ),
        )

    def test_restart_with_in_flight_call_becomes_terminal_unknown(self) -> None:
        acquired = self._create_and_acquire()
        call_digest, begun = self._plan_and_begin(acquired)
        reopened_service = LiteratureTaskCheckpointService(
            store=SealedSQLiteLiteratureCheckpointStore(data_root=self.root, sealer=self.sealer),
            clock=self.clock,
        )
        self.assert_error(
            "literature_call_outcome_unknown",
            lambda: reopened_service.recover(begun.manifest.task_id),
        )
        persisted = reopened_service.load(begun.manifest.task_id)
        self.assertEqual(persisted.state, "outcome_unknown")
        self.assertEqual(persisted.receipts[0].state, "outcome_unknown")
        self.assertEqual(persisted.receipts[0].call_digest, call_digest)
        self.assertIsNone(persisted.lease_owner_digest)
        self.assert_error(
            "literature_call_outcome_unknown",
            lambda: reopened_service.acquire(
                persisted.manifest.task_id,
                expected_revision=persisted.revision,
                owner_id="worker-a",
            ),
        )

    def test_explicit_unknown_outcome_cannot_be_retried(self) -> None:
        acquired = self._create_and_acquire()
        call_digest, begun = self._plan_and_begin(acquired)
        unknown = self.service.mark_call_outcome_unknown(
            begun.manifest.task_id,
            expected_revision=begun.revision,
            owner_id="worker-a",
            call_digest=call_digest,
        )
        self.assertEqual(unknown.reason_code, "literature_call_outcome_unknown")
        self.assert_error(
            "literature_call_outcome_unknown",
            lambda: self.service.recover(unknown.manifest.task_id),
        )

    def test_budget_fails_before_receipt_or_charge(self) -> None:
        acquired = self._create_and_acquire(manifest=_manifest(max_calls=1, max_tokens=400))
        self.assert_error(
            "literature_checkpoint_budget_exhausted",
            lambda: self.service.plan_call(
                acquired.manifest.task_id,
                expected_revision=acquired.revision,
                owner_id="worker-a",
                stage="initial_focus",
                task="extraction",
                call_digest=_sha("too large"),
                max_tokens=401,
            ),
        )
        unchanged = self.service.load(acquired.manifest.task_id)
        self.assertEqual(unchanged.revision, acquired.revision)
        self.assertEqual(unchanged.receipts, ())
        self.assertEqual(unchanged.spent_calls, 0)

    def test_stage_and_final_completion_preserve_unspent_budget(self) -> None:
        acquired = self._create_and_acquire()
        call_digest, begun = self._plan_and_begin(acquired)
        completed = self.service.complete_call(
            begun.manifest.task_id,
            expected_revision=begun.revision,
            owner_id="worker-a",
            call_digest=call_digest,
            result_digest=_sha("result"),
            private_payload=b"intermediate",
        )
        validated = self.service.advance_stage(
            completed.manifest.task_id,
            expected_revision=completed.revision,
            owner_id="worker-a",
            stage="validated",
            private_payload=b"validated package",
        )
        final = self.service.complete_task(
            validated.manifest.task_id,
            expected_revision=validated.revision,
            owner_id="worker-a",
            private_payload=b"commit receipt",
        )
        self.assertEqual(final.state, "completed")
        self.assertEqual(final.spent_calls, 1)
        self.assertEqual(final.manifest.max_calls, 4)

    def test_tampered_ciphertext_fails_closed_without_storage_details(self) -> None:
        created = self.service.create(manifest=_manifest(), private_payload=b"state")
        db_path = self.root / "literature-task-checkpoints-v1.sqlite"
        with closing(sqlite3.connect(db_path)) as connection:
            sealed = bytearray(
                connection.execute(
                    "SELECT sealed FROM checkpoints WHERE task_id = ?",
                    (created.manifest.task_id,),
                ).fetchone()[0]
            )
            sealed[-1] ^= 0x01
            connection.execute(
                "UPDATE checkpoints SET sealed = ? WHERE task_id = ?",
                (bytes(sealed), created.manifest.task_id),
            )
            connection.commit()
        error = self.assert_error(
            "literature_checkpoint_corrupt",
            lambda: self.service.load(created.manifest.task_id),
        )
        self.assertNotIn("sqlite", str(error.public_dict()).lower())

    def test_public_status_excludes_private_and_backend_fields(self) -> None:
        checkpoint = self.service.create(
            manifest=_manifest(),
            private_payload=b"/Users/researcher/paper.pdf api_key=secret",
        )
        public = checkpoint.public_status()
        rendered = json_dump(public)
        self.assertNotIn("api_key=secret", repr(checkpoint))
        for forbidden in (
            "/Users/",
            "api_key",
            "credential",
            "generation",
            "fingerprint",
            "provider_id",
            "session_digest",
            "private_payload",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_invalid_manifest_and_unknown_task_are_rejected(self) -> None:
        with self.assertRaises(LiteratureTaskCheckpointError):
            LiteratureTaskManifest(
                **{
                    **_manifest().__dict__,
                    "task_models": (("analysis", "model-a"), ("other", "model-b")),
                }
            )
        acquired = self._create_and_acquire()
        self.assert_error(
            "literature_checkpoint_invalid",
            lambda: self.service.plan_call(
                acquired.manifest.task_id,
                expected_revision=acquired.revision,
                owner_id="worker-a",
                stage="initial_focus",
                task="unknown",
                call_digest=_sha("bad task"),
                max_tokens=10,
            ),
        )

    def test_module_size_dependency_direction_and_payload_cap(self) -> None:
        evidence_root = Path(__file__).parents[1] / "auto_research" / "evidence"
        module_paths = {
            "contract": evidence_root / "literature_task_checkpoint.py",
            "store": evidence_root / "literature_task_checkpoint_store.py",
            "service": evidence_root / "literature_task_checkpoint_service.py",
        }
        for path in module_paths.values():
            self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 450)

        imports: dict[str, set[str]] = {}
        for name, path in module_paths.items():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports[name] = {
                node.module or ""
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            }
        self.assertFalse(any("checkpoint_store" in item for item in imports["contract"]))
        self.assertFalse(any("checkpoint_service" in item for item in imports["contract"]))
        self.assertFalse(any("checkpoint_service" in item for item in imports["store"]))
        self.assertFalse(any("checkpoint_store" in item for item in imports["service"]))

        manifest = _manifest()
        with self.assertRaises(LiteratureTaskCheckpointError):
            LiteratureTaskCheckpoint(
                manifest=manifest,
                revision=0,
                state="authorized",
                stage="initial_focus",
                receipts=(),
                spent_calls=0,
                spent_tokens=0,
                updated_at=1_000,
                private_payload=b"x" * (MAX_PRIVATE_PAYLOAD_BYTES + 1),
            )

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unavailable")
    def test_symlink_data_root_fails_closed(self) -> None:
        target = Path(self.temporary.name) / "real-root"
        target.mkdir()
        link = Path(self.temporary.name) / "linked-root"
        link.symlink_to(target, target_is_directory=True)
        self.assert_error(
            "literature_checkpoint_store_unavailable",
            lambda: SealedSQLiteLiteratureCheckpointStore(data_root=link, sealer=self.sealer),
        )


def json_dump(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    unittest.main()
