from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from auto_research.evidence.literature_checkpoint_runtime import (
    LiteratureCheckpointCall,
    LiteratureCheckpointRuntime,
    LiteratureExecutionState,
    decode_execution_state,
    encode_execution_state,
)
from auto_research.evidence.literature_task_checkpoint import (
    LiteratureTaskCheckpointError,
    LiteratureTaskManifest,
)
from auto_research.evidence.literature_task_checkpoint_service import (
    LiteratureTaskCheckpointService,
)
from auto_research.evidence.literature_task_checkpoint_store import (
    SealedSQLiteLiteratureCheckpointStore,
)


class _Sealer:
    def seal(self, plaintext: bytes, *, associated_data: bytes) -> bytes:
        return associated_data + b"\0" + plaintext

    def open(self, ciphertext: bytes, *, associated_data: bytes) -> bytes:
        prefix = associated_data + b"\0"
        if not ciphertext.startswith(prefix):
            raise ValueError
        return ciphertext[len(prefix) :]


class LiteratureCheckpointRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.now = 100
        store = SealedSQLiteLiteratureCheckpointStore(
            data_root=self.root / "checkpoints",
            sealer=_Sealer(),
        )
        self.service = LiteratureTaskCheckpointService(
            store=store,
            clock=lambda: self.now,
        )
        self.runtime = LiteratureCheckpointRuntime(self.service)
        self.manifest = LiteratureTaskManifest(
            task_id="literature_task_abcdefghijklmnop",
            session_digest="1" * 64,
            provider_id="deepseek",
            runtime_revision=3,
            credential_generation=2,
            task_models=(("analysis", "model-a"), ("extraction", "model-b")),
            executor_id="literature_extraction_executor",
            executor_version="v1",
            pdf_snapshot_fingerprint="2" * 64,
            max_calls=4,
            max_tokens=400,
            issued_at=90,
            expires_at=1_000,
        )
        self.calls = (
            LiteratureCheckpointCall("initial_focus", "extraction", "3" * 64, 100),
            LiteratureCheckpointCall("initial_focus", "analysis", "4" * 64, 100),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_execution_state_round_trip_is_deterministic(self) -> None:
        state = LiteratureExecutionState(
            job_state=b"private-job-state",
            stage_fingerprint="5" * 64,
            receipt_offset=2,
            completed_results=({"b": 2, "a": 1},),
        )
        first = encode_execution_state(state)
        second = encode_execution_state(state)
        self.assertEqual(first, second)
        decoded = decode_execution_state(first)
        self.assertEqual(decoded.job_state, state.job_state)
        self.assertEqual(decoded.stage_fingerprint, state.stage_fingerprint)
        self.assertEqual(decoded.receipt_offset, 2)
        self.assertEqual(decoded.completed_results, ({"a": 1, "b": 2},))

    def test_stage_calls_are_persisted_and_not_replayed_after_reopen(self) -> None:
        checkpoint = self.runtime.start(
            manifest=self.manifest,
            job_state=b"job-v1",
            stage="initial_focus",
            stage_fingerprint="5" * 64,
        )
        checkpoint = self.runtime.recover(
            checkpoint.manifest.task_id,
            owner_id="worker-one",
        )
        invocations: list[int] = []
        checkpoint, results = self.runtime.execute_stage(
            checkpoint,
            owner_id="worker-one",
            stage_fingerprint="5" * 64,
            calls=self.calls,
            invoke=lambda index: invocations.append(index) or {"index": index},
        )
        self.assertEqual(invocations, [0, 1])
        self.assertEqual(results, ({"index": 0}, {"index": 1}))

        reopened = LiteratureCheckpointRuntime(self.service).recover(
            checkpoint.manifest.task_id,
            # The execution owner is an opaque task secret persisted inside
            # the sealed job envelope, so a restarted process presents the
            # same identity while CAS still prevents concurrent mutation.
            owner_id="worker-one",
        )
        replayed: list[int] = []
        reopened, results = self.runtime.execute_stage(
            reopened,
            owner_id="worker-one",
            stage_fingerprint="5" * 64,
            calls=self.calls,
            invoke=lambda index: replayed.append(index) or {"unexpected": index},
        )
        self.assertEqual(replayed, [])
        self.assertEqual(results, ({"index": 0}, {"index": 1}))
        self.assertEqual(reopened.spent_calls, 2)

    def test_prepare_recovery_restores_job_state_without_execution_lease(self) -> None:
        checkpoint = self.runtime.start(
            manifest=self.manifest,
            job_state=b"job-v1",
            stage="initial_focus",
            stage_fingerprint="5" * 64,
        )
        recovered, job_state = self.runtime.recover_job_state(
            checkpoint.manifest.task_id
        )
        self.assertEqual(job_state, b"job-v1")
        self.assertEqual(recovered.state, "authorized")
        self.assertIsNone(recovered.lease_owner_digest)
        self.assertIsNone(recovered.lease_expires_at)

    def test_provider_interruption_becomes_terminal_outcome_unknown(self) -> None:
        checkpoint = self.runtime.start(
            manifest=self.manifest,
            job_state=b"job-v1",
            stage="initial_focus",
            stage_fingerprint="5" * 64,
        )
        checkpoint = self.runtime.recover(
            checkpoint.manifest.task_id,
            owner_id="worker-one",
        )
        with self.assertRaises(LiteratureTaskCheckpointError) as caught:
            self.runtime.execute_stage(
                checkpoint,
                owner_id="worker-one",
                stage_fingerprint="5" * 64,
                calls=self.calls,
                invoke=lambda _index: (_ for _ in ()).throw(TimeoutError()),
            )
        self.assertEqual(caught.exception.code, "literature_call_outcome_unknown")
        with self.assertRaises(LiteratureTaskCheckpointError) as recovered:
            self.runtime.recover(
                checkpoint.manifest.task_id,
                owner_id="worker-two",
            )
        self.assertEqual(recovered.exception.code, "literature_call_outcome_unknown")

    def test_advance_resets_partial_results_but_preserves_receipt_budget(self) -> None:
        checkpoint = self.runtime.start(
            manifest=self.manifest,
            job_state=b"job-v1",
            stage="initial_focus",
            stage_fingerprint="5" * 64,
        )
        checkpoint = self.runtime.recover(
            checkpoint.manifest.task_id,
            owner_id="worker-one",
        )
        checkpoint, _ = self.runtime.execute_stage(
            checkpoint,
            owner_id="worker-one",
            stage_fingerprint="5" * 64,
            calls=self.calls,
            invoke=lambda index: {"index": index},
        )
        checkpoint = self.runtime.advance_stage(
            checkpoint,
            owner_id="worker-one",
            job_state=b"job-v2",
            stage="coverage_gap",
            stage_fingerprint="6" * 64,
        )
        state = decode_execution_state(checkpoint.private_payload)
        self.assertEqual(state.job_state, b"job-v2")
        self.assertEqual(state.receipt_offset, 2)
        self.assertEqual(state.completed_results, ())
        self.assertEqual(checkpoint.spent_calls, 2)

    def test_corrupt_result_state_is_rejected(self) -> None:
        state = LiteratureExecutionState(
            job_state=b"job",
            stage_fingerprint="5" * 64,
            receipt_offset=0,
            completed_results=({"digest": hashlib.sha256(b"x").hexdigest()},),
        )
        payload = bytearray(encode_execution_state(state))
        payload[8] ^= 1
        with self.assertRaises(LiteratureTaskCheckpointError) as caught:
            decode_execution_state(bytes(payload))
        self.assertEqual(caught.exception.code, "literature_checkpoint_corrupt")


if __name__ == "__main__":
    unittest.main()
