from __future__ import annotations

import difflib
import re
import unicodedata
from pathlib import Path
from typing import Any

import fitz


DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
TITLE_PREFIX_RE = re.compile(r"^.{1,80}?\s+[-–—]\s+(?:19|20)\d{2}\s+[-–—]\s+", re.I)
TITLE_STOPWORDS = {
    "a", "an", "and", "as", "at", "before", "by", "for", "from", "in",
    "into", "of", "on", "or", "the", "to", "under", "using", "with",
}


def normalize_doi(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", text)
    return text.rstrip(".,;:)]}>")


def _normalized_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("−", "-").replace("–", "-").replace("—", "-")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _title_tokens(value: Any) -> list[str]:
    text = TITLE_PREFIX_RE.sub("", str(value or "").strip())
    return [
        token for token in _normalized_text(text).split()
        if token not in TITLE_STOPWORDS and (len(token) > 1 or token.isdigit())
    ]


def _title_match_score(title: str, pdf_text: str, metadata_title: str = "") -> tuple[float, dict[str, float]]:
    expected_tokens = _title_tokens(title)
    if not expected_tokens:
        return 0.0, {"token_recall": 0.0, "ordered_similarity": 0.0, "metadata_similarity": 0.0}
    expected = " ".join(expected_tokens)
    haystack = _normalized_text(pdf_text)
    pdf_tokens = set(haystack.split())
    token_recall = sum(token in pdf_tokens for token in expected_tokens) / len(expected_tokens)
    exact_title = expected in haystack

    # The beginning of the PDF normally contains a cover page or article title.
    # Compare against short windows rather than against the whole document so a
    # reference-list title cannot accidentally dominate the identity score.
    ordered_similarity = 0.0
    words = haystack.split()[:1200]
    window_size = max(len(expected_tokens) + 8, 16)
    step = max(1, len(expected_tokens) // 3)
    for start in range(0, max(1, len(words) - window_size + 1), step):
        window = " ".join(words[start:start + window_size])
        ordered_similarity = max(
            ordered_similarity,
            difflib.SequenceMatcher(None, expected, window).ratio(),
        )
        if ordered_similarity >= 0.96:
            break

    metadata_tokens = _title_tokens(metadata_title)
    metadata_similarity = difflib.SequenceMatcher(
        None, expected, " ".join(metadata_tokens)
    ).ratio() if metadata_tokens else 0.0
    score = 1.0 if exact_title else max(
        token_recall * 0.82 + ordered_similarity * 0.18,
        metadata_similarity,
    )
    return round(min(score, 1.0), 3), {
        "token_recall": round(token_recall, 3),
        "ordered_similarity": round(ordered_similarity, 3),
        "metadata_similarity": round(metadata_similarity, 3),
    }


def recognize_pdf_identity(
    paper: dict[str, Any], pdf_path: Path | str, *, max_pages: int = 3
) -> dict[str, Any]:
    """Verify that an openable PDF belongs to the registered paper.

    This is deliberately local and deterministic.  A valid file is not enough:
    the DOI or title on the first pages must also agree with the database record.
    Registry verification remains a separate authenticity layer.
    """

    path = Path(pdf_path).expanduser()
    result: dict[str, Any] = {
        "status": "unreadable",
        "valid": False,
        "expected_doi": normalize_doi(paper.get("doi")) or None,
        "detected_dois": [],
        "doi_match": False,
        "doi_conflict": False,
        "title_match_score": 0.0,
        "title_match_details": {},
        "reason": "PDF cannot be read",
    }
    if not path.is_file():
        result["status"] = "missing"
        result["reason"] = "local PDF is missing"
        return result

    try:
        with fitz.open(path) as document:
            if document.page_count < 1 or not document.is_pdf:
                return result
            pages = [
                document[index].get_text("text")
                for index in range(min(document.page_count, max_pages))
            ]
            metadata_title = str((document.metadata or {}).get("title") or "")
    except Exception as exc:
        result["reason"] = f"PDF read failed: {type(exc).__name__}"
        return result

    text = "\n".join(pages)
    detected = sorted({normalize_doi(match.group(0)) for match in DOI_RE.finditer(text)})
    expected_doi = result["expected_doi"]
    doi_match = bool(expected_doi and expected_doi in detected)
    title_score, title_details = _title_match_score(
        str(paper.get("title") or ""), text, metadata_title
    )
    # Other DOIs may occur in cover-page suggestions or references.  They are
    # only a conflict when the registered DOI is absent and the title also fails.
    doi_conflict = bool(expected_doi and detected and not doi_match and title_score < 0.68)

    if doi_match:
        status, valid, reason = "verified_doi", True, "registered DOI found in PDF"
    elif title_score >= 0.86 and not doi_conflict:
        status, valid, reason = "verified_title", True, "registered title strongly matches PDF"
    elif title_score >= 0.68 and not doi_conflict:
        status, valid, reason = "likely_title_match", True, "registered title matches PDF with moderate confidence"
    elif doi_conflict:
        status, valid, reason = "doi_conflict", False, "PDF DOI and registered identity conflict"
    else:
        status, valid, reason = "identity_unresolved", False, "registered DOI/title was not confirmed in PDF"

    result.update({
        "status": status,
        "valid": valid,
        "detected_dois": detected,
        "doi_match": doi_match,
        "doi_conflict": doi_conflict,
        "title_match_score": title_score,
        "title_match_details": title_details,
        "metadata_title": metadata_title or None,
        "reason": reason,
    })
    return result
