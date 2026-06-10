"""
Intent Classifier — Active Topic Detection + Scope Classification.

Two responsibilities:
1. Detect the active discussion topic from the last ~2 minutes of transcript
2. Classify user queries into a retrieval scope (MEETING_ONLY, CROSS_MEETING, etc.)

The active topic anchors everything: when the topic is "MySQL vs Postgres latency"
and the user asks "what's the difference?", the classifier routes to MEETING_ONLY.
"""

import logging
import os
from enum import Enum
from typing import Optional
from dataclasses import dataclass

try:
    from ...model_config import GEMINI_DEFAULT_MODEL
except (ImportError, ValueError):
    try:
        from ..model_config import GEMINI_DEFAULT_MODEL
    except (ImportError, ValueError):
        GEMINI_DEFAULT_MODEL = os.getenv("GEMINI_DEFAULT_MODEL", "gemini-3.5-flash")


logger = logging.getLogger(__name__)


class QueryScope(str, Enum):
    """The retrieval scope for a user query."""

    MEETING_ONLY = "meeting_only"
    CROSS_MEETING = "cross_meeting"
    WORKSPACE_SEARCH = "workspace_search"
    EXTERNAL_WEB = "external_web"
    HYBRID = "hybrid"


@dataclass
class ClassificationResult:
    """Result of intent classification."""

    scope: QueryScope
    active_topic: Optional[str]  # Detected topic from recent transcript
    reformulated_query: str  # Cleaned-up query for retrieval
    force_web_query: Optional[str] = None  # If explicit web trigger, the search query


# ── Keyword lists ──────────────────────────────────────────────────────────

# Explicit web triggers — user MUST say one of these to force web mode
EXPLICIT_WEB_TRIGGERS = [
    "search on web",
    "search the web",
    "search web",
    "google for",
    "look up online",
    "find online",
    "web search",
]

# Cross-meeting keywords
CROSS_MEETING_KEYWORDS = [
    "search in linked meetings",
    "linked meetings",
    "linked meeting",
    "linked one",
    "search linked",
    "previous meeting",
    "last meeting",
    "other meeting",
    "earlier meeting",
    "compare",
    "comparison",
    "different from",
    "changed since",
    "previously discussed",
    "follow up",
    "follow-up",
    "what did we say",
    "what was said",
    "mentioned before",
    "discussed earlier",
]

# Global/workspace search keywords
GLOBAL_SEARCH_KEYWORDS = [
    "search all meetings",
    "search in all meetings",
    "search globally",
    "global search",
    "find in all meetings",
    "search across meetings",
    "search in meetings",
    "search meetings",
    "search all",
]


class IntentClassifier:
    """
    Classifies user queries by detecting the active topic and determining
    the retrieval scope.
    """

    def __init__(self, db):
        self.db = db
        try:
            from ..llm_gateway import LLMGateway
        except (ImportError, ValueError):
            from services.llm_gateway import LLMGateway
        # Gateway handles provider fallback. The "classify"/"topic" task chains
        # default to OpenAI-primary (gpt-4o-mini) -> Gemini, preserving this
        # classifier's historical behavior and cost profile.
        self._gateway = LLMGateway(db)

    async def detect_active_topic(
        self,
        context_text: str,
        user_email: Optional[str] = None,
    ) -> Optional[str]:
        """
        Detect the active discussion topic from the recent transcript.

        Uses the last ~2000 chars (roughly 2 minutes of speech) to identify
        what participants are currently talking about.

        Returns a short topic string (e.g., "MySQL vs PostgreSQL latency")
        or None if no clear topic is detected.
        """
        if not context_text or len(context_text.strip()) < 30:
            return None

        # Take the tail of the context (most recent discussion)
        recent_text = context_text[-2000:] if len(context_text) > 2000 else context_text

        try:
            prompt = f"""You are extracting the active discussion topic from a live meeting transcript.

Read the most recent part of the transcript below and identify what participants are currently discussing.

Transcript (most recent):
---
{recent_text}
---

Instructions:
- In 10 words or fewer, state the current discussion topic.
- Be specific. "MySQL vs PostgreSQL latency comparison" is better than "database discussion".
- If participants are debating or comparing options, name both options.
- If there is no clear topic or the text is too sparse, respond with exactly: NONE

Current discussion topic:"""

            result = (
                await self._gateway.generate(
                    task="topic",
                    prompt=prompt,
                    user_email=user_email,
                    temperature=0.0,
                    max_tokens=50,
                    model_overrides={"openai": "gpt-4o-mini"},
                )
            ).strip()

            if not result or result.upper() == "NONE" or len(result) > 100:
                logger.info("Active topic detection: no clear topic detected")
                return None

            logger.info(f"Active topic detected: '{result}'")
            return result

        except Exception as e:
            logger.warning(f"Active topic detection failed: {e}")
            return None

    async def classify(
        self,
        question: str,
        context_text: str,
        active_topic: Optional[str] = None,
        has_linked_meetings: bool = False,
        user_email: Optional[str] = None,
    ) -> ClassificationResult:
        """
        Classify a user query into a retrieval scope.

        Priority order:
        1. Explicit web triggers (user explicitly says "search on web")
        2. Global search triggers (user explicitly says "search all meetings")
        3. Cross-meeting triggers (keyword-based or user has linked meetings)
        4. LLM-based classification (topic-aware, defaults to MEETING)
        """
        question_lower = question.lower().strip()

        # ── 1. Explicit Web Triggers ──────────────────────────────────────
        for trigger in EXPLICIT_WEB_TRIGGERS:
            if trigger in question_lower:
                logger.info(f"Explicit web trigger detected: '{trigger}'")
                # Extract the actual search query
                search_query = question_lower
                for t in EXPLICIT_WEB_TRIGGERS:
                    search_query = search_query.replace(t, "")
                search_query = search_query.strip()
                if len(search_query) < 3:
                    search_query = question

                return ClassificationResult(
                    scope=QueryScope.EXTERNAL_WEB,
                    active_topic=active_topic,
                    reformulated_query=question,
                    force_web_query=search_query,
                )

        # ── 2. Global Search Triggers ─────────────────────────────────────
        for trigger in GLOBAL_SEARCH_KEYWORDS:
            if trigger in question_lower:
                logger.info(f"Global search trigger detected: '{trigger}'")
                return ClassificationResult(
                    scope=QueryScope.WORKSPACE_SEARCH,
                    active_topic=active_topic,
                    reformulated_query=question,
                )

        # ── 3. Cross-Meeting Triggers ─────────────────────────────────────
        for keyword in CROSS_MEETING_KEYWORDS:
            if keyword in question_lower:
                logger.info(f"Cross-meeting keyword detected: '{keyword}'")
                return ClassificationResult(
                    scope=QueryScope.CROSS_MEETING,
                    active_topic=active_topic,
                    reformulated_query=question,
                )

        # If user has linked meetings and the question seems comparative,
        # default to CROSS_MEETING
        if has_linked_meetings:
            return ClassificationResult(
                scope=QueryScope.CROSS_MEETING,
                active_topic=active_topic,
                reformulated_query=question,
            )

        # ── 4. LLM-based Classification (topic-aware) ────────────────────
        scope = await self._llm_classify(
            question, context_text, active_topic, user_email
        )

        return ClassificationResult(
            scope=scope,
            active_topic=active_topic,
            reformulated_query=question,
        )

    async def _llm_classify(
        self,
        question: str,
        context_text: str,
        active_topic: Optional[str],
        user_email: Optional[str] = None,
    ) -> QueryScope:
        """
        Use GPT-4o-mini (or Gemini fallback) to classify whether the question needs web search.

        Defaults to MEETING_ONLY. Only returns EXTERNAL_WEB if the question
        is clearly and unambiguously about external real-time information.
        """
        has_context = bool(context_text and len(context_text.strip()) > 50)

        try:
            topic_line = ""
            if active_topic:
                topic_line = f'Active discussion topic: "{active_topic}"'

            context_line = (
                f"Meeting context available: Yes, about: {context_text[:200]}"
                if has_context
                else "Meeting context available: No meeting context"
            )

            classifier_prompt = f"""You are a meeting copilot routing classifier.
{topic_line}
{context_line}

User question: "{question}"

Instructions:
1. First, check if the question is about the current meeting discussion (MEETING_ONLY).
2. If the user is asking for external information (like "what is the weather", "who is the CEO of Apple", "look up the current stock price"), output: EXTERNAL_WEB
3. If the user is asking about things discussed in the meeting, or if the active discussion topic matches the question context, output: MEETING_ONLY

Always default to MEETING_ONLY unless it is explicitly an external query.
Output ONLY the exact category name.

Category:"""

            result = (
                await self._gateway.generate(
                    task="classify",
                    prompt=classifier_prompt,
                    user_email=user_email,
                    temperature=0.0,
                    max_tokens=10,
                    model_overrides={"openai": "gpt-4o-mini"},
                )
            ).strip()

            if "EXTERNAL_WEB" in result.upper():
                logger.info(f"LLM routing to EXTERNAL_WEB for question: {question}")
                return QueryScope.EXTERNAL_WEB
            else:
                return QueryScope.MEETING_ONLY

        except Exception as e:
            logger.error(f"LLM classification failed: {e}")
            return QueryScope.MEETING_ONLY

        except Exception as e:
            logger.warning(f"LLM classifier failed: {e}")
            return QueryScope.MEETING_ONLY  # Safe default
