"""Golden-set evals for summarization & RAG chat.

These tests **do hit real LLM providers** when ``LLM_EVAL_LIVE=1``.  Otherwise
they're skipped — this suite is intentionally not part of the per-PR runs
because:

  * model providers cost money and rate-limit
  * outputs are non-deterministic; we use rubric scoring, not equality

Run nightly via the dedicated workflow.  Failures go to the test report and
should be triaged like any other regression.

Scoring uses a simple keyword-rubric (deterministic, no LLM-as-judge) so we
don't introduce judge-model variance into the signal.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden_set.json"


def _live() -> bool:
    return os.getenv("LLM_EVAL_LIVE", "0") in {"1", "true", "yes"}


def _load_golden():
    return json.loads(GOLDEN.read_text())["meetings"]


def _score_keywords(text: str, expected: dict) -> tuple[float, list[str]]:
    text_lower = text.lower()
    must = expected.get("must_contain", [])
    forbid = expected.get("must_not_contain", [])
    hits = sum(1 for kw in must if kw.lower() in text_lower)
    misses = [kw for kw in must if kw.lower() not in text_lower]
    forbidden_hits = [kw for kw in forbid if kw.lower() in text_lower]
    score = (hits / max(len(must), 1)) - (0.2 * len(forbidden_hits))
    return max(0.0, min(1.0, score)), misses + forbidden_hits


@pytest.mark.llm_eval
@pytest.mark.parametrize("case", _load_golden(), ids=lambda c: c["id"])
def test_summary_meets_rubric(case):
    if not _live():
        pytest.skip("Set LLM_EVAL_LIVE=1 to run real LLM evals.")
    if "question" in case:
        pytest.skip("This case is for chat eval, not summarization.")

    from app.services.summarization import summarize_transcript  # type: ignore

    transcript = "\n".join(case["transcript"])
    summary = summarize_transcript(transcript)
    score, missing = _score_keywords(str(summary), case["expected"])

    assert score >= 0.7, (
        f"Summary scored {score:.2f} (<0.7). "
        f"Missing/forbidden keywords: {missing}. Output: {summary!r}"
    )


@pytest.mark.llm_eval
@pytest.mark.parametrize("case", _load_golden(), ids=lambda c: c["id"])
def test_chat_answer_meets_rubric(case):
    if not _live():
        pytest.skip("Set LLM_EVAL_LIVE=1 to run real LLM evals.")
    if "question" not in case:
        pytest.skip("This case is for summarization, not chat.")

    # Lazy import — service imports may be heavy
    from app.services.chat.service import ChatService  # type: ignore
    from app.db import DatabaseManager  # type: ignore

    svc = ChatService(DatabaseManager())
    transcript = "\n".join(case["transcript"])

    async def _run():
        chunks: list[str] = []
        gen = await svc.chat_about_meeting(
            meeting_id="eval-meeting",
            question=case["question"],
            context_text=transcript,
            model="gemini",
            model_name="gemini-2.0-flash",
        )
        async for chunk in gen:
            chunks.append(chunk)
        return "".join(chunks)

    import asyncio

    answer = asyncio.run(_run())
    score, missing = _score_keywords(answer, case["expected"])
    assert score >= 0.6, (
        f"Chat answer scored {score:.2f}. Missing: {missing}. Output: {answer!r}"
    )
