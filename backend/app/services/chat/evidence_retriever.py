"""
Evidence Retriever — Tiered evidence retrieval with topic-aware prioritization.

Retrieval priority:
1. Current Meeting — Topic Window (last ~2 min, highest priority)
2. Current Meeting — Full structured context + summary/notes
3. Linked Meetings — Summary/notes first, vector snippets second
4. Workspace/Global — Vector search across all meetings
5. Web — SerpAPI + Trafilatura + Gemini synthesis (last resort)

Each evidence tier is clearly labeled so the LLM knows the source.
"""

import logging
import os
import json
import asyncio
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field

from .intent_classifier import QueryScope

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────
MAX_LINKED_SUMMARY_CHARS = 4000  # Max chars from each linked meeting's summary
MAX_LINKED_SNIPPETS_PER_MEETING = 5
MAX_SNIPPET_CHARS = 2000
TOPIC_WINDOW_CHARS = 3000  # Last ~2 min of transcript


@dataclass
class EvidenceBundle:
    """All gathered evidence, organized by tier."""

    active_topic: Optional[str] = None
    topic_window: str = ""  # Most recent transcript (active discussion)
    current_meeting_context: str = ""  # Full meeting context
    current_meeting_summary: str = ""  # Summary/notes if available
    linked_meeting_evidence: str = ""  # Summaries + snippets from linked meetings
    workspace_evidence: str = ""  # Global vector search results
    web_evidence: str = ""  # Web search results
    web_search_status: str = ""  # Status message for web search
    sources_used: List[str] = field(default_factory=list)  # Track which tiers were used

    @property
    def has_meeting_evidence(self) -> bool:
        """Check if we have meaningful evidence from the meeting."""
        return bool(
            (self.topic_window and len(self.topic_window.strip()) > 30)
            or (
                self.current_meeting_context
                and len(self.current_meeting_context.strip()) > 30
            )
            or (
                self.current_meeting_summary
                and len(self.current_meeting_summary.strip()) > 30
            )
        )

    @property
    def has_linked_evidence(self) -> bool:
        return bool(
            self.linked_meeting_evidence
            and len(self.linked_meeting_evidence.strip()) > 30
        )

    def format_for_prompt(self) -> str:
        """Format all evidence tiers into a structured prompt section."""
        sections = []

        if self.topic_window:
            sections.append(
                f"=== ACTIVE DISCUSSION (most recent) ===\n{self.topic_window}\n"
            )

        if self.current_meeting_context:
            sections.append(
                f"=== CURRENT MEETING CONTEXT ===\n{self.current_meeting_context}\n"
            )

        if self.current_meeting_summary:
            sections.append(
                f"=== CURRENT MEETING NOTES/SUMMARY ===\n{self.current_meeting_summary}\n"
            )

        if self.linked_meeting_evidence:
            sections.append(
                f"=== LINKED MEETINGS CONTEXT ===\n{self.linked_meeting_evidence}\n"
            )

        if self.workspace_evidence:
            sections.append(
                f"=== WORKSPACE SEARCH RESULTS ===\n{self.workspace_evidence}\n"
            )

        if self.web_evidence:
            sections.append(
                f"=== EXTERNAL WEB CONTEXT ===\n{self.web_evidence}\n"
            )

        if not sections:
            return "(No context available)"

        return "\n".join(sections)


class EvidenceRetriever:
    """
    Retrieves evidence in tiered priority order with topic-aware prioritization.
    """

    def __init__(self, db):
        self.db = db

    async def retrieve(
        self,
        scope: QueryScope,
        question: str,
        context_text: str,
        active_topic: Optional[str],
        allowed_meeting_ids: Optional[List[str]] = None,
        force_web_query: Optional[str] = None,
        user_email: Optional[str] = None,
    ) -> EvidenceBundle:
        """
        Retrieve evidence based on the classified scope.

        Evidence is gathered in priority order and each tier is clearly labeled.
        """
        bundle = EvidenceBundle(active_topic=active_topic)

        # ── Tier 1: Current Meeting (always) ─────────────────────────────
        self._extract_topic_window(context_text, bundle)
        bundle.current_meeting_context = context_text or ""
        if bundle.topic_window or bundle.current_meeting_context:
            bundle.sources_used.append("current_meeting")

        # ── Tier 2: Linked Meetings ──────────────────────────────────────
        if scope in (
            QueryScope.CROSS_MEETING,
            QueryScope.HYBRID,
        ) and allowed_meeting_ids:
            await self._retrieve_linked_meetings(
                question, allowed_meeting_ids, bundle, user_email
            )

        # ── Tier 3: Workspace/Global Search ──────────────────────────────
        if scope == QueryScope.WORKSPACE_SEARCH:
            await self._retrieve_workspace(question, bundle)

        # ── Tier 4: Web Search ───────────────────────────────────────────
        if scope == QueryScope.EXTERNAL_WEB:
            search_query = force_web_query or question
            await self._retrieve_web(search_query, bundle, user_email)
        elif scope == QueryScope.HYBRID:
            # For HYBRID: only escalate to web if meeting evidence is thin
            if not bundle.has_meeting_evidence and not bundle.has_linked_evidence:
                logger.info(
                    "HYBRID scope: meeting evidence is thin, escalating to web search"
                )
                await self._retrieve_web(question, bundle, user_email)
            else:
                logger.info(
                    "HYBRID scope: meeting evidence is sufficient, skipping web search"
                )

        return bundle

    def _extract_topic_window(self, context_text: str, bundle: EvidenceBundle):
        """
        Extract the most recent part of the transcript as the "topic window".
        This is the highest-priority evidence — what's being discussed RIGHT NOW.
        """
        if not context_text or len(context_text.strip()) < 30:
            return

        # Take the last TOPIC_WINDOW_CHARS chars as the active discussion
        if len(context_text) > TOPIC_WINDOW_CHARS:
            # Try to break at a line boundary
            window = context_text[-TOPIC_WINDOW_CHARS:]
            first_newline = window.find("\n")
            if first_newline > 0 and first_newline < 200:
                window = window[first_newline + 1 :]
            bundle.topic_window = window.strip()
        else:
            bundle.topic_window = context_text.strip()

    async def _retrieve_linked_meetings(
        self,
        question: str,
        meeting_ids: List[str],
        bundle: EvidenceBundle,
        user_email: Optional[str] = None,
    ):
        """
        Retrieve evidence from linked meetings.

        Priority: Summary/notes first (compact, structured), then vector snippets.
        Never dumps full transcripts.
        """
        logger.info(f"Retrieving linked meeting context for {len(meeting_ids)} meetings")
        linked_parts = []

        for meeting_id in meeting_ids:
            try:
                # 1. Try to get summary/notes first
                meeting_data = await self.db.get_meeting(meeting_id)
                if not meeting_data:
                    continue

                meeting_title = meeting_data.get("title", "Unknown Meeting")
                meeting_date = meeting_data.get("created_at", "Unknown Date")
                header = f"[{meeting_title}] ({meeting_date})"

                summary_text = await self._get_meeting_summary_text(meeting_id)
                if summary_text:
                    # Use structured summary (capped)
                    truncated = summary_text[:MAX_LINKED_SUMMARY_CHARS]
                    if len(summary_text) > MAX_LINKED_SUMMARY_CHARS:
                        truncated += "\n...[Summary truncated]"
                    linked_parts.append(f"\n{header}\n{truncated}")
                    continue  # Summary is enough, skip vector search for this meeting

                # 2. Fallback: vector search for relevant snippets
                snippets = await self._vector_search_meeting(
                    question, meeting_id, n_results=MAX_LINKED_SNIPPETS_PER_MEETING
                )
                if snippets:
                    linked_parts.append(f"\n{header}")
                    for i, snippet in enumerate(snippets, 1):
                        text = snippet.get("text", "")[:MAX_SNIPPET_CHARS]
                        similarity = snippet.get("similarity", 0)
                        linked_parts.append(
                            f"  Snippet {i} (relevance: {similarity:.2f}): {text}"
                        )
                    continue

                # 3. Last resort: grab first portion of transcript
                transcripts = meeting_data.get("transcripts", [])
                if transcripts:
                    brief = "\n".join(
                        [t.get("text", "") for t in transcripts[:20]]
                    )[:MAX_LINKED_SUMMARY_CHARS]
                    linked_parts.append(f"\n{header}\n{brief}\n...[Excerpt only]")

            except Exception as e:
                logger.warning(
                    f"Failed to retrieve linked meeting {meeting_id}: {e}"
                )

        if linked_parts:
            bundle.linked_meeting_evidence = "\n".join(linked_parts)
            bundle.sources_used.append("linked_meetings")

    async def _get_meeting_summary_text(self, meeting_id: str) -> Optional[str]:
        """
        Get the summary/notes markdown for a meeting from summary_processes.

        Returns the markdown text if available, None otherwise.
        """
        try:
            summary_data = await self.db.get_transcript_data(meeting_id)
            if not summary_data:
                return None

            status = (summary_data.get("status") or "").lower()
            if status != "completed":
                return None

            result = summary_data.get("result")
            if not result or not isinstance(result, dict):
                return None

            # Check for encrypted payload
            if result.get("_is_encrypted_payload"):
                return None

            # Try to get markdown first, then fall back to structured extraction
            markdown = result.get("markdown")
            if markdown and len(markdown.strip()) > 20:
                return markdown

            # Fallback: build text from structured sections
            parts = []
            meeting_name = result.get("MeetingName")
            if meeting_name:
                parts.append(f"Meeting: {meeting_name}")

            # Extract key sections
            for key in [
                "SessionSummary",
                "KeyItemsDecisions",
                "ImmediateActionItems",
                "CriticalDeadlines",
                "NextSteps",
            ]:
                section = result.get(key)
                if section and isinstance(section, dict):
                    title = section.get("title", key)
                    blocks = section.get("blocks", [])
                    if blocks:
                        parts.append(f"\n{title}:")
                        for block in blocks:
                            content = (
                                block.get("content", "")
                                if isinstance(block, dict)
                                else str(block)
                            )
                            if content:
                                parts.append(f"  - {content}")

            # Extract from MeetingNotes sections
            meeting_notes = result.get("MeetingNotes")
            if meeting_notes and isinstance(meeting_notes, dict):
                sections = meeting_notes.get("sections", [])
                for section in sections:
                    title = section.get("title", "")
                    blocks = section.get("blocks", [])
                    if blocks:
                        parts.append(f"\n{title}:")
                        for block in blocks:
                            content = (
                                block.get("content", "")
                                if isinstance(block, dict)
                                else str(block)
                            )
                            if content:
                                parts.append(f"  - {content}")

            if parts:
                return "\n".join(parts)

            return None

        except Exception as e:
            logger.warning(f"Failed to get summary for meeting {meeting_id}: {e}")
            return None

    async def _vector_search_meeting(
        self,
        query: str,
        meeting_id: str,
        n_results: int = 5,
    ) -> List[Dict[str, Any]]:
        """Vector search within a specific meeting's embeddings."""
        try:
            from app.vector_store import search_context, get_collection_stats
        except ImportError:
            try:
                from ...vector_store import search_context, get_collection_stats
            except (ImportError, ValueError):
                logger.debug("Vector store not available for linked meeting search")
                return []

        try:
            stats = get_collection_stats()
            if stats.get("status") and "available" in str(stats.get("status", "")):
                results = await search_context(
                    query=query,
                    n_results=n_results,
                    allowed_meeting_ids=[meeting_id],
                )
                return results or []
        except Exception as e:
            logger.debug(f"Vector search failed for meeting {meeting_id}: {e}")

        return []

    async def _retrieve_workspace(self, question: str, bundle: EvidenceBundle):
        """Perform a global vector search across all meetings."""
        try:
            from app.vector_store import search_context, get_collection_stats
        except ImportError:
            try:
                from ...vector_store import search_context, get_collection_stats
            except (ImportError, ValueError):
                logger.warning("Vector store not available for workspace search")
                return

        try:
            stats = get_collection_stats()
            if stats.get("status") and "available" in str(stats.get("status", "")):
                results = await search_context(
                    query=question,
                    n_results=20,
                    allowed_meeting_ids=None,  # Search all
                )
                if results:
                    parts = []
                    for r in results:
                        source = f"{r.get('meeting_title', 'Unknown')} ({r.get('meeting_date', 'Unknown')})"
                        text = r.get("text", "").strip()[:MAX_SNIPPET_CHARS]
                        similarity = r.get("similarity", 0)
                        parts.append(f"- [{source}] (relevance: {similarity:.2f}): {text}")
                    bundle.workspace_evidence = "\n".join(parts)
                    bundle.sources_used.append("workspace_search")
                    logger.info(f"Workspace search found {len(results)} results")
        except Exception as e:
            logger.warning(f"Workspace vector search failed: {e}")

    async def _retrieve_web(
        self,
        query: str,
        bundle: EvidenceBundle,
        user_email: Optional[str] = None,
    ):
        """
        Perform web search using SerpAPI + Trafilatura + Gemini synthesis.
        
        This is a last resort and only called when meeting evidence is insufficient
        or the user explicitly requested web search.
        """
        logger.info(f"Web search for: {query}")
        bundle.web_search_status = f"🔍 Searching web for: *{query}*...\n\n"

        try:
            import httpx
            import trafilatura

            # Step 1: Search Google via SerpAPI
            SERPAPI_KEY = os.getenv("SERPAPI_KEY")

            async def serpapi_search():
                try:
                    from serpapi import GoogleSearch

                    params = {
                        "q": query,
                        "api_key": SERPAPI_KEY,
                        "num": 5,
                        "hl": "en",
                        "gl": "us",
                    }

                    def do_search():
                        search = GoogleSearch(params)
                        results = search.get_dict()
                        return results.get("organic_results", [])

                    loop = asyncio.get_event_loop()
                    return await loop.run_in_executor(None, do_search)
                except Exception as e:
                    logger.error(f"SerpAPI search failed: {e}")
                    return []

            search_results = await serpapi_search()
            if not search_results:
                bundle.web_evidence = f"No search results found for '{query}'."
                return

            logger.info(f"SerpAPI found {len(search_results)} results")

            # Step 2: Crawl pages and extract content
            async def fetch_and_extract(url: str) -> dict:
                try:
                    async with httpx.AsyncClient(
                        timeout=10.0, follow_redirects=True
                    ) as client:
                        response = await client.get(
                            url,
                            headers={
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                            },
                        )
                        if response.status_code == 200:
                            html = response.text
                            text = trafilatura.extract(
                                html, include_comments=False, include_tables=True
                            )
                            if text and len(text) > 100:
                                return {
                                    "url": url,
                                    "content": text[:2000],
                                    "success": True,
                                }
                except Exception as e:
                    logger.warning(f"Failed to crawl {url}: {e}")
                return {"url": url, "content": "", "success": False}

            crawl_tasks = [
                fetch_and_extract(r.get("link", "")) for r in search_results[:4]
            ]
            crawled = await asyncio.gather(*crawl_tasks)

            sources = []
            for i, result in enumerate(crawled):
                if result["success"] and result["content"]:
                    title = search_results[i].get("title", "Unknown")
                    sources.append(
                        {
                            "title": title,
                            "url": result["url"],
                            "content": result["content"],
                        }
                    )

            if not sources:
                sources = [
                    {
                        "title": r.get("title", "Unknown"),
                        "url": r.get("link", ""),
                        "content": r.get("snippet", ""),
                    }
                    for r in search_results[:3]
                ]

            logger.info(f"Extracted content from {len(sources)} sources")

            # Step 3: Synthesize with Gemini
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            if not api_key:
                api_key = await self.db.get_api_key("gemini", user_email=user_email)

            if not api_key:
                bundle.web_evidence = "❌ Gemini API key not configured."
                return

            try:
                from ..gemini_client import generate_content_text_async
            except (ImportError, ValueError):
                from services.gemini_client import generate_content_text_async

            sources_text = ""
            for i, src in enumerate(sources, 1):
                sources_text += f"\n[Source {i}: {src['title']}]\nURL: {src['url']}\nContent:\n{src['content']}\n---\n"

            prompt = f"""You are a research assistant for a meeting copilot. Provide a concise, factual answer to the query based on the web sources provided.

Query: {query}

Web Sources:
{sources_text}

Instructions:
1. Answer the query directly using ONLY information from the provided sources
2. Do NOT use inline citations (e.g. [Source 1]) in the text.
3. If sources conflict, acknowledge both perspectives and note the discrepancy
4. Prioritize recent information and authoritative sources
5. If sources don't adequately answer the query, clearly state what's missing
6. Paraphrase information in your own words - do NOT copy text verbatim from sources
7. Keep the response concise and meeting-appropriate (aim for 150-300 words unless the query requires more detail)
8. Use formatting sparingly - only use bullet points if listing distinct items; otherwise use clear prose
9. If asked about current statistics or data, include the date/timeframe from the source

Format: Provide a direct answer followed by supporting details without inline citations."""

            response_text = await generate_content_text_async(
                api_key=api_key,
                model="gemini-2.5-flash",
                contents=prompt,
                config={"temperature": 0.3, "max_output_tokens": 2048},
            )

            if response_text:
                bundle.web_evidence = f"**🔍 Web Research Results:**\n\n{response_text}"
                bundle.sources_used.append("web_search")
            else:
                bundle.web_evidence = "Failed to generate summary from sources."

        except Exception as e:
            logger.error(f"Web search failed: {e}", exc_info=True)
            bundle.web_evidence = f"Web search failed: {str(e)}"
