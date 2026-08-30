from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .harness_contract import (
    HarnessDependencySet,
    HarnessError,
    HarnessEvidenceIdentity,
    HarnessJobV1,
    canonical_public,
    job_from_prepared_action,
    verify_cordis_composition,
)
from .harness_tools import AutoResearchHarnessBackend, HarnessToolGateway
from .prepared_actions import PreparedOutbound
from .business_actions import (
    BudgetedBusinessAIClient,
    HarnessBudgetedBusinessAIClient,
)


HARNESS_EXECUTION_RESULT_SCHEMA_VERSION = "harness-execution-result-v1"
MAX_ACTIVE_HARNESS_JOBS = 32


def _safe_trace(stage: str, exc: BaseException) -> None:
    """Emit only exception type and stage for opt-in release diagnostics."""

    if os.environ.get("AUTO_RESEARCH_AI_SAFE_TRACE") != "1":
        return
    traceback = exc.__traceback__
    while traceback is not None and traceback.tb_next is not None:
        traceback = traceback.tb_next
    location = (
        {
            "function": traceback.tb_frame.f_code.co_name,
            "line": traceback.tb_lineno,
        }
        if traceback is not None
        else {}
    )
    print(
        "AUTO_RESEARCH_AI_SAFE_TRACE "
        + json.dumps(
            {
                "event": "harness_runtime_exception",
                "stage": stage,
                "error_type": type(exc).__name__,
                **location,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stderr,
        flush=True,
    )


def _safe_rejection(reason: str) -> None:
    """Record a path-free projector rejection category for release QA."""

    if os.environ.get("AUTO_RESEARCH_AI_SAFE_TRACE") != "1":
        return
    print(
        "AUTO_RESEARCH_AI_SAFE_TRACE "
        + json.dumps(
            {
                "event": "harness_output_rejected",
                "stage": "output_project",
                "reason": reason,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stderr,
        flush=True,
    )


class HarnessClock(Protocol):
    def now(self) -> int: ...


class SystemHarnessClock:
    def now(self) -> int:
        return int(time.time())


class HarnessModelPort(Protocol):
    def request_json(self, messages: list[dict[str, str]], **kwargs: Any) -> Mapping[str, Any]: ...

    def request_tool_message(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> Mapping[str, Any]: ...


class DeepSeekHarnessRuntime(Protocol):
    def dependency_metadata(self) -> HarnessDependencySet: ...

    def composition_metadata(self) -> Mapping[str, Any]: ...

    def execute(
        self,
        *,
        job: HarnessJobV1,
        model: HarnessModelPort,
        tools: HarnessToolGateway,
        prompt: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class _StoredJob:
    job: HarnessJobV1
    expires_at: int


class HarnessJobStore:
    """Process-local one-shot state; never persisted to history or a database."""

    def __init__(
        self,
        *,
        clock: HarnessClock | None = None,
        capacity: int = MAX_ACTIVE_HARNESS_JOBS,
    ) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or not 1 <= capacity <= 32:
            raise ValueError("invalid harness job capacity")
        self._clock = clock or SystemHarnessClock()
        self._capacity = capacity
        self._lock = threading.Lock()
        self._jobs: dict[str, _StoredJob] = {}
        self._receipts: dict[str, int] = {}

    def register(self, job: HarnessJobV1) -> None:
        now = self._now()
        with self._lock:
            self._cleanup(now)
            if len(self._jobs) >= self._capacity:
                raise HarnessError("harness_job_store_full")
            if job.job_id in self._jobs or job.job_id in self._receipts:
                raise HarnessError("harness_invalid")
            self._jobs[job.job_id] = _StoredJob(job, job.session.expires_at)

    def claim(self, job_id: str) -> HarnessJobV1:
        now = self._now()
        with self._lock:
            self._cleanup(now)
            if job_id in self._receipts:
                raise HarnessError("harness_job_replayed")
            stored = self._jobs.get(job_id)
            if stored is None:
                raise HarnessError("harness_job_expired")
            if stored.job.state != "prepared":
                raise HarnessError("harness_job_replayed")
            running = stored.job.with_state("running")
            self._jobs[job_id] = _StoredJob(running, stored.expires_at)
            return running

    def finish(self, job_id: str, *, success: bool) -> None:
        now = self._now()
        with self._lock:
            stored = self._jobs.pop(job_id, None)
            if stored is None:
                raise HarnessError("harness_job_expired")
            self._receipts[job_id] = max(now + 1, stored.expires_at)

    def _now(self) -> int:
        value = self._clock.now()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise HarnessError("harness_runtime_unavailable")
        return value

    def _cleanup(self, now: int) -> None:
        self._jobs = {
            key: value for key, value in self._jobs.items() if now < value.expires_at
        }
        self._receipts = {
            key: value for key, value in self._receipts.items() if now < value
        }


class HarnessOutputProjector:
    _NUMBER = re.compile(
        r"(?<![A-Za-z0-9_])[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?![A-Za-z0-9_])"
    )
    _QUANTITY = re.compile(
        r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\s*"
        r"(?:%|°\s*[CF]|K|Pa|kPa|MPa|GPa|TPa|HV|HRC|nm|µm|μm|mm|cm|m|"
        r"eV|keV|MeV|J|mJ|W(?:\s*/\s*m(?:\s*K)?)?|g\s*/\s*cm(?:\^?3|³)|"
        r"kg\s*/\s*m(?:\^?3|³)|dpa|at\.?\s*%|wt\.?\s*%|s|min|(?-i:h))(?![A-Za-z])",
        re.IGNORECASE,
    )
    _COMPARISON = re.compile(
        r"(?:相比|比较|对比|高于|低于|大于|小于|超过|不及|相差|差异为|"
        r"增加|降低|提升|下降|变化(?:了|为)?|倍|比值|比例|范围|区间|"
        r"greater\s+than|less\s+than|higher\s+than|lower\s+than|versus|vs\.?|[<>≥≤])",
        re.IGNORECASE,
    )
    _RANGE_OR_RATIO = re.compile(
        r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\s*"
        r"(?:[-–—~～至到:：/])\s*"
        r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?",
        re.IGNORECASE,
    )
    _LIST_ORDINAL = re.compile(r"^\s*(?:[（(]?\d{1,2}[）).、:：])\s*")

    @staticmethod
    def _quantity_key(value: str) -> str:
        return re.sub(r"\s+", "", value).replace("μ", "µ").casefold()

    @staticmethod
    def _cited_seed_text(
        prompt: Mapping[str, Any] | None,
        refs: set[str],
    ) -> str:
        """Serialize cited seed rows after removing immutable wrappers.

        Production search snapshots deliberately use read-only mappings and
        tuples.  ``json.dumps`` cannot encode ``MappingProxyType`` directly,
        even though it implements ``Mapping``.  Canonicalizing first preserves
        the path/sensitive-field guard while giving the quantity validator a
        plain JSON-compatible structure.
        """

        if not isinstance(prompt, Mapping):
            return ""
        seed = prompt.get("seed_evidence")
        if not isinstance(seed, Sequence) or isinstance(
            seed, (str, bytes, bytearray)
        ):
            return ""
        cited_seed = [
            row
            for row in seed
            if isinstance(row, Mapping) and str(row.get("ref") or "") in refs
        ]
        public_seed = canonical_public(cited_seed, byte_cap=512 * 1024)
        return json.dumps(
            public_seed,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def _supported_context_quantities(
        cls,
        prompt: Mapping[str, Any] | None,
        refs: set[str],
    ) -> frozenset[str]:
        """Return exact hard-condition quantities backed by cited seed rows.

        A multi-paper answer may repeat a condition such as ``300 °C`` without
        gaining authority to invent hardness values, ranges, ratios or derived
        differences. Only a quantity that occurs both in the user's question
        and in at least one locally verified cited seed row is exempted from the
        cross-bundle new-number gate. Bare numbers are never exempted.
        """

        if not isinstance(prompt, Mapping):
            return frozenset()
        question = prompt.get("question")
        if (
            not isinstance(question, str)
        ):
            return frozenset()
        question_values = {
            cls._quantity_key(match.group(0))
            for match in cls._QUANTITY.finditer(question)
        }
        if not question_values:
            return frozenset()
        cited_text = cls._cited_seed_text(prompt, refs)
        cited_values = {
            cls._quantity_key(match.group(0))
            for match in cls._QUANTITY.finditer(cited_text)
        }
        return frozenset(question_values & cited_values)

    @classmethod
    def _supported_cited_quantities(
        cls,
        prompt: Mapping[str, Any] | None,
        refs: set[str],
    ) -> frozenset[str]:
        """Return unit-bearing values present in application-verified seed rows.

        Cross-bundle synthesis must not invent a new value, range, ratio or
        difference.  It may, however, quote an exact per-source value that the
        application already froze and whose R identity the model selected.
        The previous gate allowed only quantities repeated in the question;
        real Librarian answers were consequently rejected merely for quoting
        their cited evidence.  Identity and citation authority remain local:
        this helper never trusts a model-supplied value or reference.
        """

        if not isinstance(prompt, Mapping):
            return frozenset()
        cited_text = cls._cited_seed_text(prompt, refs)
        if not cited_text:
            return frozenset()
        return frozenset(
            cls._quantity_key(match.group(0))
            for match in cls._QUANTITY.finditer(cited_text)
        )

    @staticmethod
    def _claim_segments(value: object) -> list[str]:
        """Flatten report prose into sentence-sized validation segments."""

        values: list[str] = []

        def collect(item: object) -> None:
            if isinstance(item, str):
                values.extend(
                    part.strip()
                    for part in re.split(r"[。！？!?;；\n]+", item)
                    if part.strip()
                )
            elif isinstance(item, Mapping):
                for child in item.values():
                    collect(child)
            elif isinstance(item, Sequence) and not isinstance(
                item, (str, bytes, bytearray)
            ):
                for child in item:
                    collect(child)

        collect(value)
        return values

    @classmethod
    def _has_quantitative_comparison(
        cls,
        value: object,
        *,
        supported_context_quantities: frozenset[str] = frozenset(),
    ) -> bool:
        for claim in cls._claim_segments(value):
            text = re.sub(
                r"(?<![A-Za-z0-9_])R[1-9][0-9]{0,3}(?![0-9])", "", claim
            )
            # Provider prose sometimes numbers a JSON string as ``1. ...`` or
            # ``（2）...``.  A leading list ordinal is presentation, not a
            # scientific value.  Only the sentence prefix is removed; bare
            # numbers anywhere in the scientific claim remain fail-closed.
            text = cls._LIST_ORDINAL.sub("", text, count=1)
            # Ranges and ratios are derived comparison surfaces even when
            # their endpoint values separately occur in cited rows.
            if cls._RANGE_OR_RATIO.search(text):
                _safe_rejection("cross_bundle_range_or_ratio")
                return True
            supported_matches = [
                match
                for match in cls._QUANTITY.finditer(text)
                if cls._quantity_key(match.group(0))
                in supported_context_quantities
            ]
            if (
                cls._COMPARISON.search(text)
                and len(
                    {
                        cls._quantity_key(match.group(0))
                        for match in supported_matches
                    }
                )
                >= 2
            ):
                # Two cited values do not authorize the model to calculate or
                # assert a new cross-paper ordering/difference in one claim.
                _safe_rejection("cross_bundle_two_value_comparison")
                return True
            if supported_context_quantities:
                text = cls._QUANTITY.sub(
                    lambda match: (
                        ""
                        if cls._quantity_key(match.group(0))
                        in supported_context_quantities
                        else match.group(0)
                    ),
                    text,
                )
            # Exact cited quantities have been removed. Any remaining number
            # or unit-bearing value is unsupported and therefore rejected.
            if cls._QUANTITY.search(text):
                _safe_rejection("cross_bundle_unsupported_quantity")
                return True
            if cls._NUMBER.search(text):
                _safe_rejection("cross_bundle_unsupported_number")
                return True
        return False

    @classmethod
    def _has_cross_bundle_quantitative_claim(
        cls,
        value: object,
        *,
        prompt: Mapping[str, Any] | None,
        refs: set[str],
        ref_bundles: Mapping[str, str],
    ) -> bool:
        """Validate quantitative prose at the sentence's actual source scope.

        A complete Librarian answer can cite several incompatible evidence
        bundles while each sentence still reports only one paper's verified
        observation.  Treating the whole answer as one comparison rejected
        legitimate source-local conditions such as ``300 °C / 1 MeV / 1 dpa``.
        A sentence tied to one complete bundle may repeat exact values frozen
        in those cited seed rows; cross-bundle or unscoped sentences retain the
        stricter no-derived-comparison rule.
        """

        global_supported = (
            cls._supported_context_quantities(prompt, refs)
            | cls._supported_cited_quantities(prompt, refs)
        )
        safe_source_local: set[str] = set()
        deferred: list[tuple[str, set[str]]] = []

        def claim_key(claim: str) -> str:
            without_refs = re.sub(
                r"(?<![A-Za-z0-9_])R[1-9][0-9]{0,3}(?![0-9])", "", claim
            )
            return re.sub(r"\s+", "", without_refs).casefold()

        for claim in cls._claim_segments(value):
            claim_refs = {
                ref
                for ref in re.findall(
                    r"(?<![A-Za-z0-9_])R[1-9][0-9]{0,3}(?![0-9])", claim
                )
                if ref in refs
            }
            bundles = {str(ref_bundles.get(ref) or "") for ref in claim_refs}
            if claim_refs and "" not in bundles and len(bundles) == 1:
                supported = (
                    cls._supported_context_quantities(prompt, claim_refs)
                    | cls._supported_cited_quantities(prompt, claim_refs)
                )
                text = re.sub(
                    r"(?<![A-Za-z0-9_])R[1-9][0-9]{0,3}(?![0-9])", "", claim
                )
                text = cls._LIST_ORDINAL.sub("", text, count=1)
                text = cls._QUANTITY.sub(
                    lambda match: (
                        ""
                        if cls._quantity_key(match.group(0)) in supported
                        else match.group(0)
                    ),
                    text,
                )
                if cls._QUANTITY.search(text) or cls._NUMBER.search(text):
                    _safe_rejection("source_local_unsupported_number")
                    return True
                safe_source_local.add(claim_key(claim))
                continue
            deferred.append((claim, claim_refs))

        for claim, claim_refs in deferred:
            # ``answer`` and ``report.direct_conclusion`` commonly repeat the
            # same sentence, with the redundant report copy omitting its R
            # marker.  It carries no new authority and can reuse the already
            # verified source-local sentence.
            if not claim_refs and claim_key(claim) in safe_source_local:
                continue
            if cls._has_quantitative_comparison(
                claim,
                supported_context_quantities=global_supported,
            ):
                return True
        return False

    @staticmethod
    def librarian(
        raw: Mapping[str, Any],
        *,
        tools: HarnessToolGateway,
        prompt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            value = canonical_public(raw)
        except HarnessError as exc:
            _safe_rejection("public_shape")
            raise HarnessError("harness_output_invalid") from exc
        if not isinstance(value, dict):
            _safe_rejection("top_level_object")
            raise HarnessError("harness_output_invalid")
        report_value = value.get("report")
        report_value = report_value if isinstance(report_value, Mapping) else {}
        answer = value.get("answer")
        direct_conclusion = report_value.get("direct_conclusion")
        if not isinstance(answer, str) or not answer.strip():
            answer = direct_conclusion
        if not isinstance(answer, str) or not answer.strip():
            _safe_rejection("answer_missing")
            raise HarnessError("harness_output_invalid")
        if not isinstance(direct_conclusion, str) or not direct_conclusion.strip():
            direct_conclusion = answer
        database_gaps = report_value.get("database_gaps")
        if isinstance(database_gaps, Sequence) and not isinstance(
            database_gaps, (str, bytes, bytearray)
        ):
            database_gaps = "；".join(
                str(item).strip()
                for item in database_gaps
                if isinstance(item, str) and item.strip()
            )
        if not isinstance(database_gaps, str):
            database_gaps = ""
        followups_value = report_value.get("suggested_followups")
        if isinstance(followups_value, str):
            followups_value = [followups_value]
        suggested_followups = [
            item.strip()
            for item in (
                followups_value
                if isinstance(followups_value, Sequence)
                and not isinstance(followups_value, (str, bytes, bytearray))
                else ()
            )
            if isinstance(item, str) and item.strip()
        ][:16]
        report = {
            "direct_conclusion": direct_conclusion.strip(),
            "database_gaps": database_gaps.strip(),
            "suggested_followups": suggested_followups,
        }
        citations_value = value.get("citations")
        if not isinstance(citations_value, Sequence) or isinstance(
            citations_value, (str, bytes, bytearray)
        ) or len(citations_value) > 64:
            citations_value = ()
        refs: set[str] = set()
        for citation in citations_value:
            ref = (
                citation.get("ref")
                if isinstance(citation, Mapping)
                else citation
            )
            if not isinstance(ref, str):
                continue
            if ref in tools.verified_refs:
                refs.add(ref)
        rendered_claims = json.dumps(
            {"answer": answer, "report": report},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        mentioned_refs = set(
            re.findall(r"(?<![A-Za-z0-9_])R[1-9][0-9]{0,3}(?![0-9])", rendered_claims)
        )
        if not mentioned_refs.issubset(tools.verified_refs):
            _safe_rejection("unverified_text_reference")
            raise HarnessError("harness_output_invalid")
        # Providers occasionally return citations as a string array, or omit
        # the redundant list while citing R identities directly in prose.
        # The application already froze and verified those identities, so use
        # their safe intersection instead of rejecting an otherwise traceable
        # answer for representational drift.
        refs.update(mentioned_refs)
        if not refs:
            _safe_rejection("citation_missing")
            raise HarnessError("harness_output_invalid")
        ref_bundles = [str(tools.verified_ref_bundles.get(ref) or "") for ref in refs]
        bundles = set(ref_bundles)
        complete_bundle_identity = all(ref_bundles)
        if (not complete_bundle_identity or len(bundles) != 1) and HarnessOutputProjector._has_cross_bundle_quantitative_claim(
            {"answer": answer, "report": report},
            prompt=prompt,
            refs=refs,
            ref_bundles=tools.verified_ref_bundles,
        ):
            _safe_rejection("cross_bundle_quantitative_claim")
            raise HarnessError("harness_output_invalid")
        recommendations = []
        seen_papers: set[str] = set()
        recommended_value = value.get("recommended_articles")
        if not isinstance(recommended_value, Sequence) or isinstance(
            recommended_value, (str, bytes, bytearray)
        ):
            recommended_value = ()
        for article in recommended_value[:10]:
            if not isinstance(article, dict):
                continue
            paper_uid = article.get("paper_uid")
            if (
                not isinstance(paper_uid, str)
                or paper_uid not in tools.recommended_papers
                or paper_uid in seen_papers
            ):
                continue
            seen_papers.add(paper_uid)
            recommendations.append(
                {
                    "paper_uid": paper_uid,
                    "title": str(article.get("title") or "")[:1_000],
                    "doi": str(article.get("doi") or "")[:500],
                    "reason": str(article.get("reason") or "")[:2_000],
                }
            )
        # Model echoes are never identity authority.  Return only the fixed
        # schema and the intersection with application-verified references and
        # recommendations; untrusted extra keys are discarded.
        return {
            "schema_version": "librarian-harness-result-v1",
            "answer": answer.strip(),
            "report": {
                "direct_conclusion": report["direct_conclusion"],
                # These sections are reconstructed from locally verified seed
                # rows by the business projector. Model placeholder arrays are
                # not part of the scientific acceptance surface.
                "evidence_matrix": [],
                "related_evidence": [],
                "database_gaps": report["database_gaps"],
                "suggested_followups": report["suggested_followups"],
            },
            "citations": [{"ref": ref} for ref in sorted(refs)],
            "recommended_articles": recommendations,
            # A Librarian answer may cite several papers and therefore several
            # independently published bundles.  Those references remain useful
            # as a traceable evidence set, but they are *not* authority for a
            # quantitative cross-bundle comparison.  Expose a comparison bundle
            # only when every verified reference belongs to the same bundle;
            # never trust the model's echoed bundle list.
            "comparison_bundle_uids": sorted(bundles) if complete_bundle_identity and len(bundles) == 1 else [],
        }

    @staticmethod
    def selected_evidence(
        raw: Mapping[str, Any],
        *,
        job: HarnessJobV1,
    ) -> dict[str, Any]:
        try:
            value = canonical_public(raw)
        except HarnessError as exc:
            raise HarnessError("harness_output_invalid") from exc
        # Evidence identity is application-owned authority.  Asking a model to
        # echo it made otherwise valid answers fail when a provider omitted an
        # empty bundle_uid or copied descriptive evidence fields into entity.
        # The model now supplies prose only; the projector restores the exact
        # prepared-action identity and keeps related evidence empty unless a
        # future reviewed reference contract is introduced.
        if not isinstance(value, dict) or set(value) != {
            "schema_version", "answer", "limitations"
        }:
            raise HarnessError("harness_output_invalid")
        if (
            value.get("schema_version") != "selected-evidence-harness-model-v1"
            or not isinstance(value.get("answer"), str)
            or not value["answer"].strip()
            or not isinstance(value.get("limitations"), list)
            or len(value.get("limitations", [])) > 32
            or any(not isinstance(item, str) for item in value.get("limitations", []))
            or job.current_entity is None
        ):
            raise HarnessError("harness_output_invalid")
        return {
            "schema_version": "selected-evidence-harness-result-v1",
            "answer": value["answer"],
            "entity": job.current_entity.public_dict(),
            "related": [],
            "limitations": value["limitations"],
        }


class DeepSeekHarnessAdapter:
    """Fail-closed bridge used only inside an already consumed business action."""

    def __init__(
        self,
        *,
        runtime: DeepSeekHarnessRuntime,
        backend: AutoResearchHarnessBackend,
        store: HarnessJobStore | None = None,
    ) -> None:
        try:
            dependencies = runtime.dependency_metadata()
            if not isinstance(dependencies, HarnessDependencySet):
                raise HarnessError("harness_dependency_mismatch")
            dependencies.verify_production_protocols()
            verify_cordis_composition(runtime.composition_metadata())
        except HarnessError:
            raise
        except Exception as exc:
            raise HarnessError("harness_dependency_mismatch") from exc
        self._runtime = runtime
        self._backend = backend
        self._store = store or HarnessJobStore()

    def execute_consumed(
        self,
        *,
        action: PreparedOutbound,
        session_id: str,
        model: HarnessModelPort,
        evidence: Sequence[HarnessEvidenceIdentity],
        current_entity: HarnessEvidenceIdentity | None = None,
        allowed_neighbors: Sequence[HarnessEvidenceIdentity] = (),
        allow_source_view: bool = False,
        prompt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(
            model, (BudgetedBusinessAIClient, HarnessBudgetedBusinessAIClient)
        ):
            raise HarnessError("harness_invalid")
        job = job_from_prepared_action(
            action,
            session_id=session_id,
            evidence=evidence,
            current_entity=current_entity,
            allowed_neighbors=allowed_neighbors,
        )
        if job.session.scope == "librarian" and (
            job.task != "librarian_planning" or job.max_calls != 1 or job.max_tokens > 2_400
        ):
            # Defense in depth: the contract and production execution both
            # require one locally seeded Flash planning turn.
            raise HarnessError("harness_scope_unsupported")
        self._store.register(job)
        running = self._store.claim(job.job_id)
        tools = HarnessToolGateway(
            backend=self._backend,
            job=running,
            allow_source_view=allow_source_view,
        )
        try:
            if running.session.scope == "librarian":
                # The one-turn Librarian deliberately exposes no provider
                # tools.  Freeze citation and recommendation authority locally
                # before that single paid call, using the same bounded gateway
                # and immutable Search V2 candidate set used by the projector.
                seed = (prompt or {}).get("seed_evidence")
                refs = [f"R{index}" for index in range(1, len(running.evidence) + 1)]
                if (
                    not refs
                    or len(refs) > 64
                    or not isinstance(seed, Sequence)
                    or isinstance(seed, (str, bytes, bytearray))
                    or len(seed) != len(running.evidence)
                ):
                    raise HarnessError("harness_output_invalid")
                for ref, identity, row in zip(refs, running.evidence, seed, strict=True):
                    if not isinstance(row, Mapping) or any(
                        row.get(key) != expected
                        for key, expected in (
                            ("ref", ref),
                            ("source_scope", identity.source_scope),
                            ("source_id", identity.source_id),
                            ("entity_type", identity.entity_type),
                            ("entity_uid", identity.entity_uid),
                            ("bundle_uid", identity.bundle_uid),
                        )
                    ):
                        raise HarnessError("harness_output_invalid")
                tools.call("citation_verify", {"refs": refs})
                question = str((prompt or {}).get("question") or "").strip()
                if not question:
                    raise HarnessError("harness_output_invalid")
                tools.call("recommend_papers", {"question": question, "limit": 10})
            raw = self._runtime.execute(
                job=running,
                model=model,
                tools=tools,
                prompt=prompt,
            )
            if not isinstance(raw, Mapping):
                raise HarnessError("harness_output_invalid")
        except HarnessError:
            self._store.finish(job.job_id, success=False)
            raise
        except Exception as exc:
            self._store.finish(job.job_id, success=False)
            _safe_trace("runtime_execute", exc)
            raise HarnessError("harness_runtime_failed") from exc
        try:
            if running.session.scope == "librarian":
                public = HarnessOutputProjector.librarian(
                    raw,
                    tools=tools,
                    prompt=prompt,
                )
            elif running.session.scope == "selected_evidence_chat":
                public = HarnessOutputProjector.selected_evidence(raw, job=running)
            else:
                raise HarnessError("harness_scope_unsupported")
        except HarnessError:
            self._store.finish(job.job_id, success=False)
            raise
        except Exception as exc:
            self._store.finish(job.job_id, success=False)
            _safe_trace("output_project", exc)
            raise HarnessError("harness_output_invalid") from exc
        self._store.finish(job.job_id, success=True)
        return {
            **public,
            "harness": {
                "schema_version": HARNESS_EXECUTION_RESULT_SCHEMA_VERSION,
                "provider_id": running.session.provider_id,
                "scope": running.session.scope,
                "task": running.task,
                "model": running.model,
                "status": "completed",
            },
        }


__all__ = [
    "DeepSeekHarnessAdapter",
    "DeepSeekHarnessRuntime",
    "HARNESS_EXECUTION_RESULT_SCHEMA_VERSION",
    "HarnessClock",
    "HarnessJobStore",
    "HarnessModelPort",
    "HarnessOutputProjector",
]
