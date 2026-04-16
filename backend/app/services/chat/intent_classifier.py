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
from typing import List, Dict, Optional
from dataclasses import dataclass

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

    async def _get_openai_key(self, user_email: Optional[str] = None) -> Optional[str]:
        """Get OpenAI API key from env or DB."""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            api_key = await self.db.get_api_key("openai", user_email=user_email)
        return api_key

    async def _get_gemini_key(self, user_email: Optional[str] = None) -> Optional[str]:
        """Get Gemini API key from env or DB."""
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            api_key = await self.db.get_api_key("gemini", user_email=user_email)
        return api_key

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

            api_key = await self._get_openai_key(user_email)
            if not api_key:
                # Fallback to Gemini if OpenAI isn't configured
                gemini_key = await self._get_gemini_key(user_email)
                if not gemini_key:
                    return None
                try:
                    from ..gemini_client import generate_content_text_async
                except (ImportError, ValueError):
                    from services.gemini_client import generate_content_text_async

                result = (
                    await generate_content_text_async(
                        api_key=gemini_key,
                        model="gemini-2.5-flash",
                        contents=prompt,
                        config={"temperature": 0.0, "max_output_tokens": 50},
                    )
                ).strip()
            else:
                from openai import AsyncOpenAI

                client = AsyncOpenAI(api_key=api_key)
                response = await client.chat.completions.create(
                    model="gpt-4o-mini",
                    temperature=0.0,
                    max_tokens=50,
                    messages=[{"role": "user", "content": prompt}],
                )
                result = (response.choices[0].message.content or "").strip()

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

            api_key = await self._get_openai_key(user_email)
            if not api_key:
                # Fallback to Gemini
                gemini_key = await self._get_gemini_key(user_email)
                if not gemini_key:
                    return QueryScope.MEETING_ONLY
                try:
                    from ..gemini_client import generate_content_text_async
                except (ImportError, ValueError):
                    from services.gemini_client import generate_content_text_async

                result = (
                    await generate_content_text_async(
                        api_key=gemini_key,
                        model="gemini-2.5-flash",
                        contents=classifier_prompt,
                        config={"temperature": 0.0, "max_output_tokens": 10},
                    )
                ).strip()
            else:
                try:
                    from openai import AsyncOpenAI
                except ImportError:
                    return QueryScope.MEETING_ONLY

                client = AsyncOpenAI(api_key=api_key)
                response = await client.chat.completions.create(
                    model="gpt-4o-mini",
                    temperature=0.0,
                    max_tokens=10,
                    messages=[{"role": "user", "content": classifier_prompt}],
                )
                result = (response.choices[0].message.content or "").strip()

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
