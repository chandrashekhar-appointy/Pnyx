"""
ChatService — Main orchestrator for the production-grade chat RAG pipeline.

Flow:
1. Reformulate query (resolve pronouns from conversation history)
2. Detect active topic (from last ~2 min of transcript)
3. Classify intent → scope (topic-aware)
4. Retrieve evidence (tiered, topic-prioritized)
5. Construct prompt with active topic and evidence tiers
6. Stream response

Same public interface as the old monolithic ChatService so all existing
callers (api/routers/chat.py, transcripts.py) work without changes.
"""

import json
import logging
import os
import re
from typing import List, Dict, Optional

from dotenv import load_dotenv

# LLM Providers
from openai import AsyncOpenAI
from groq import AsyncGroq
from anthropic import AsyncAnthropic

try:
    from ...db import DatabaseManager
    from ..gemini_client import (
        generate_content_text_async,
        stream_content_text_async,
    )
except (ImportError, ValueError):
    from db import DatabaseManager
    from services.gemini_client import (
        generate_content_text_async,
        stream_content_text_async,
    )

from .intent_classifier import IntentClassifier
from .evidence_retriever import EvidenceRetriever, EvidenceBundle

logger = logging.getLogger(__name__)
load_dotenv()


class ChatService:
    """Handles chat interactions with meeting context, cross-meeting search, and web search."""

    def __init__(self, db: DatabaseManager):
        self.db = db
        self.classifier = IntentClassifier(db)
        self.retriever = EvidenceRetriever(db)
        self.active_clients = []

    # ── Query Reformulation ───────────────────────────────────────────────

    async def _reformulate_query(
        self,
        question: str,
        history: List[Dict[str, str]],
        user_email: Optional[str] = None,
    ) -> str:
        """
        Reformulate the user's question into a standalone query using conversation history.
        Resolves pronouns like "it", "that", "these things" to their actual context.
        """
        if not history or len(history) == 0:
            return question

        try:
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            if not api_key:
                api_key = await self.db.get_api_key("gemini", user_email=user_email)
            if not api_key:
                return question

            recent_history = history[-6:] if len(history) > 6 else history
            history_text = ""
            for msg in recent_history:
                role = msg.get("role", "user")
                content = msg.get("content", "")[:300]
                history_text += f"{role.upper()}: {content}\n"

            prompt = f"""Given the conversation history, rewrite the last user question to be a concise, keyword-focused search query suitable for a search engine (like Google).
Resolve pronouns (it, that, they) and vague references.
If the user refers to "the previous question", replace it with the ACTUAL topic of the previous question.
Remove conversational filler like "can you tell me", "please search for", "how to do", etc., unless "how to" is part of the technical query.

Examples:
History: User: "How to fix deployment?" AI: "Use load balancing."
User: "Search web for that."
Result: "load balancing deployment fix"

History: User: "What is the price of BTC?" AI: "$95k."
User: "I meant search on web for the previous question."
Result: "current bitcoin price"

History: User: "can you tell me how to do proper load testing"
Result: "proper load testing guide best practices"

Do NOT answer the question. Just rewrite it as a search query.

History:
{history_text}

Last User Question: "{question}"

Search Query:"""

            reformulated = (
                await generate_content_text_async(
                    api_key=api_key,
                    model="gemini-2.5-flash",
                    contents=prompt,
                )
            ).strip()

            if not reformulated or len(reformulated) > len(question) * 4:
                return question

            logger.info(f"Query Reformulation: '{question}' -> '{reformulated}'")
            return reformulated

        except Exception as e:
            logger.warning(f"Query reformulation failed: {e}")
            return question

    # ── Main Chat Entry Point ─────────────────────────────────────────────

    async def chat_about_meeting(
        self,
        context: str,
        question: str,
        model: str,
        model_name: str,
        allowed_meeting_ids: Optional[List[str]] = None,
        history: Optional[List[Dict[str, str]]] = None,
        user_email: Optional[str] = None,
    ):
        """
        Ask a question about the meeting context with cross-meeting and web search.
        Returns a streaming response generator.
        """
        if not model or not model_name:
            model = "openai"
            model_name = "gpt-5.4"

        logger.info(f"Chat request: '{question}' using model {model}:{model_name}")

        # ── Step 1: Reformulate Query ─────────────────────────────────────
        reformulated_question = question
        if history and len(history) > 0:
            reformulated_question = await self._reformulate_query(
                question, history, user_email
            )
        logic_question = reformulated_question

        # ── Step 2: Detect Active Topic ───────────────────────────────────
        active_topic = await self.classifier.detect_active_topic(
            context_text=context,
            user_email=user_email,
        )

        # ── Step 3: Classify Intent ───────────────────────────────────────
        classification = await self.classifier.classify(
            question=logic_question,
            context_text=context[:1000] if context else "",
            active_topic=active_topic,
            has_linked_meetings=bool(
                allowed_meeting_ids and len(allowed_meeting_ids) > 0
            ),
            user_email=user_email,
        )
        logger.info(
            f"Classification: scope={classification.scope}, topic={classification.active_topic}"
        )

        # ── Step 4: Retrieve Evidence ─────────────────────────────────────
        evidence = await self.retriever.retrieve(
            scope=classification.scope,
            question=logic_question,
            context_text=context,
            active_topic=active_topic,
            allowed_meeting_ids=allowed_meeting_ids,
            force_web_query=classification.force_web_query,
            user_email=user_email,
        )

        # ── Step 5: Construct Prompt ──────────────────────────────────────
        history_text = ""
        if history and len(history) > 0:
            history_text = "\nConversation History:\n"
            selected_history = (
                history if len(history) <= 10 else history[:2] + history[-8:]
            )
            for msg in selected_history:
                role = msg.get("role", "user")
                content = msg.get("content", "")[:1000]
                history_text += f"{role.upper()}: {content}\n"

        topic_instruction = ""
        if active_topic:
            topic_instruction = f"""
ACTIVE DISCUSSION TOPIC: {active_topic}

The participants are currently discussing: {active_topic}
When they ask questions, they almost certainly relate to this topic.
Ground your answers in the context of this specific discussion."""

        evidence_formatted = evidence.format_for_prompt()
        sources_label = (
            ", ".join(evidence.sources_used) if evidence.sources_used else "none"
        )

        system_prompt = f"""You are an expert meeting assistant embedded IN the user's live meeting. You have context that no other AI has — the live transcript of their conversation.
{topic_instruction}

YOUR #1 JOB: Answer questions grounded in what participants are actually discussing.

WHEN PARTICIPANTS DEBATE SOMETHING (e.g., MySQL vs Postgres latency) AND ASK YOU A QUESTION, YOU MUST:
1. Reference what was said in the meeting ("Based on the discussion where...")
2. Provide your analysis in the CONTEXT of their specific debate
3. Help them make a DECISION, not just list generic facts
4. If the meeting transcript contains relevant arguments, synthesize and evaluate them — don't just repeat them
5. Add your own expert knowledge to COMPLEMENT (not replace) the meeting context

DO NOT give generic textbook answers. Always tie your response back to the specific discussion happening in the meeting.

CITATION RULES:
- When citing the current meeting, reference what was said naturally
- When citing a linked meeting, use its title: [Meeting Title] (Date)
- When citing web sources, use [Web Source]

STRICT RULES:
- If the answer is inferrable from meeting context, do NOT rely on web results
- If web context is provided, use it only to SUPPLEMENT meeting context, not replace it. Frame web facts in terms of the meeting debate.
- If you don't have enough meeting context, say "I don't have enough information from the meeting context to answer that" instead of speculating.

EVIDENCE SOURCES USED: {sources_label}

CONTEXTS:

{evidence_formatted}

{history_text}

USER QUESTION: {question}
"""

        # ── Step 6: Stream Response ───────────────────────────────────────
        async def response_wrapper(generator):
            if evidence.web_search_status:
                yield evidence.web_search_status
            async for chunk in generator:
                yield chunk

        try:
            if model == "groq":
                api_key = await self.db.get_api_key("groq", user_email=user_email)
                if not api_key:
                    api_key = os.getenv("GROQ_API_KEY")
                if not api_key:
                    raise ValueError("Groq API key not found.")

                client = AsyncGroq(api_key=api_key)
                completion_tokens = 4096
                if "8b" in model_name:
                    completion_tokens = 1024

                initial_stream = await client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": question},
                    ],
                    model=model_name,
                    max_tokens=completion_tokens,
                    stream=True,
                )

                async def stream_groq(stream_iter):
                    async for chunk in stream_iter:
                        content = chunk.choices[0].delta.content or ""
                        if content:
                            yield content

                return response_wrapper(stream_groq(initial_stream))

            elif model == "openai":
                api_key = await self.db.get_api_key("openai", user_email=user_email)
                if not api_key:
                    raise ValueError("OpenAI API key not found")

                client = AsyncOpenAI(api_key=api_key)
                initial_stream = await client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": question},
                    ],
                    model=model_name,
                    stream=True,
                )

                async def stream_openai(stream_iter):
                    async for chunk in stream_iter:
                        content = chunk.choices[0].delta.content or ""
                        if content:
                            yield content

                return response_wrapper(stream_openai(initial_stream))

            elif model == "claude":
                api_key = await self.db.get_api_key("claude", user_email=user_email)
                if not api_key:
                    raise ValueError("Anthropic API key not found")

                client = AsyncAnthropic(api_key=api_key)
                initial_stream = await client.messages.create(
                    max_tokens=1024,
                    system=system_prompt,
                    messages=[{"role": "user", "content": question}],
                    model=model_name,
                    stream=True,
                )

                async def stream_claude(stream_iter):
                    try:
                        async for text in stream_iter.text_stream:
                            yield text
                    except Exception as e:
                        yield f"Error: {str(e)}"

                return response_wrapper(stream_claude(initial_stream))

            elif model == "gemini":
                api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
                if not api_key:
                    api_key = await self.db.get_api_key("gemini", user_email=user_email)
                if not api_key:
                    raise ValueError("Gemini API key not found")

                generation_config = {
                    "temperature": 0.7,
                    "top_p": 0.95,
                    "top_k": 64,
                    "max_output_tokens": 8192,
                    "system_instruction": system_prompt,
                }

                async def stream_gemini():
                    try:
                        async for chunk_text in stream_content_text_async(
                            api_key=api_key,
                            model=model_name,
                            contents=question,
                            config=generation_config,
                        ):
                            yield chunk_text
                    except Exception as e:
                        logger.error(f"Gemini streaming error: {e}", exc_info=True)
                        yield f"\n\nError during Gemini response: {str(e)}"

                return response_wrapper(stream_gemini())

            else:
                raise ValueError(f"Unsupported chat model: {model}")

        except Exception as e:
            logger.error(f"Error in chat_about_meeting: {e}", exc_info=True)
            raise e

    # ── Web Search (public method for backward compat) ────────────────────

    async def search_web(self, query: str, user_email: Optional[str] = None) -> str:
        """
        Public web search method. Delegates to EvidenceRetriever.
        Kept for backward compatibility.
        """
        bundle = EvidenceBundle()
        await self.retriever._retrieve_web(query, bundle, user_email)
        return bundle.web_evidence or f"No search results found for '{query}'."

    # ── Generic Streaming (for refine_notes, etc.) ────────────────────────

    async def stream_response(
        self,
        system_prompt: str,
        user_query: str,
        model: str,
        model_name: str,
        user_email: Optional[str] = None,
    ):
        """
        Generic streaming response handler for different LLM providers.
        Used by refine_notes and other non-chat features.
        """
        if not model or not model_name:
            model = "openai"
            model_name = "gpt-5.4"
        try:
            if model == "groq":
                api_key = await self.db.get_api_key("groq", user_email=user_email)
                if not api_key:
                    api_key = os.getenv("GROQ_API_KEY")
                if not api_key:
                    raise ValueError("Groq API key not found.")

                client = AsyncGroq(api_key=api_key)
                initial_stream = await client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_query},
                    ],
                    model=model_name,
                    stream=True,
                )

                async def stream_groq(stream_iter):
                    async for chunk in stream_iter:
                        content = chunk.choices[0].delta.content or ""
                        if content:
                            yield content

                return stream_groq(initial_stream)

            elif model == "openai":
                api_key = await self.db.get_api_key("openai", user_email=user_email)
                if not api_key:
                    raise ValueError("OpenAI API key not found")

                client = AsyncOpenAI(api_key=api_key)
                initial_stream = await client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_query},
                    ],
                    model=model_name,
                    stream=True,
                )

                async def stream_openai(stream_iter):
                    async for chunk in stream_iter:
                        content = chunk.choices[0].delta.content or ""
                        if content:
                            yield content

                return stream_openai(initial_stream)

            elif model == "claude":
                api_key = await self.db.get_api_key("claude", user_email=user_email)
                if not api_key:
                    raise ValueError("Anthropic API key not found")

                client = AsyncAnthropic(api_key=api_key)
                initial_stream = await client.messages.create(
                    max_tokens=4096,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_query}],
                    model=model_name,
                    stream=True,
                )

                async def stream_claude(stream_iter):
                    async for text in stream_iter.text_stream:
                        yield text

                return stream_claude(initial_stream)

            elif model == "gemini":
                api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
                if not api_key:
                    api_key = await self.db.get_api_key("gemini", user_email=user_email)
                if not api_key:
                    raise ValueError("Gemini API key not found")

                async def stream_gemini():
                    async for chunk_text in stream_content_text_async(
                        api_key=api_key,
                        model=model_name,
                        contents=user_query,
                        config={"system_instruction": system_prompt},
                    ):
                        yield chunk_text

                return stream_gemini()

            else:
                raise ValueError(f"Unsupported model: {model}")

        except Exception as e:
            logger.error(f"Error in stream_response: {e}", exc_info=True)
            raise e

    # ── Refine Notes ──────────────────────────────────────────────────────

    async def refine_notes(
        self,
        notes: str,
        instruction: str,
        transcript_context: str,
        model: str,
        model_name: str,
        user_email: Optional[str] = None,
    ) -> Dict[str, object]:
        """
        Refine meeting notes using a PATCH-BASED contract:
          1. Backend parses the notes into addressable sections by markdown heading.
          2. Model receives the sections by ID + heading + content.
          3. Model returns ONLY the edits/inserts it wants to make — never the full doc.
          4. Backend deterministically rebuilds the document, leaving every section
             the model did not name byte-for-byte identical to the input.

        This makes the "rewrite-the-whole-doc" failure mode structurally impossible
        even on small/fast models, because the LLM physically cannot drop sections —
        if it doesn't reference a section_id, that section is copied verbatim.

        Returns: {"changes": [str, ...], "updated_document": str}
        """
        if not model or not model_name:
            model = "gemini"
            model_name = "gemini-2.5-flash"

        sections = self._split_notes_into_sections(notes)
        sections_block = self._format_sections_for_prompt(sections)

        system_prompt = f"""You are a precise meeting notes editor. The user's notes are pre-split into addressable sections. Your job is to return ONLY the targeted edits — the system will reassemble the document.

# Hard rules
- You CANNOT modify a section unless you reference it by section_id in `edits`.
- Sections you do not reference are kept BYTE-FOR-BYTE identical. This is enforced by the system, not you.
- Section headings cannot be changed (they are document anchors). To rename, you must delete + create.
- `new_content` for an edit is the BODY ONLY of that section. Do NOT include the heading line — the heading is preserved automatically.

# Operations available
1. **edits** — replace the body of an existing section. Use for "make X in points", "rewrite Y", "shorten Z".
2. **insertions** — add a brand-new section. Use for "add a section about X". Specify `after_section_id` (or null to insert at top).
3. **append_to_section** — append new content to the end of an existing section's body, leaving the original content intact. **Use this for "add more bullets to X", "add another item to Y"** — it is the safest way to extend a section.
4. **deletions** — remove an entire section. Only when user EXPLICITLY asks to delete.

# Decision guide
- "add more X to <section>" → `append_to_section` (not edits — append preserves existing content)
- "make <section> in points / rewrite <section> / fix typos in <section>" → `edits`
- "add a section about Y" → `insertions`
- "remove the <section>" → `deletions`
- If the user's instruction is vague, prefer `append_to_section` (safer than `edits`).

# Reference material (don't add new info unless asked)
Meeting Transcript (reference only):
---
{transcript_context[:30000]}
---

# Output format — STRICT JSON, NO PROSE, NO CODE FENCES
Respond with EXACTLY one JSON object:

{{
  "changes": [
    "<short user-facing bullet describing what you changed, e.g. 'Added 3 bullets to Next Steps section.'>"
  ],
  "edits": [
    {{ "section_id": <int>, "new_content": "<replacement body for this section, no heading>" }}
  ],
  "append_to_section": [
    {{ "section_id": <int>, "appended_content": "<content to append after the existing body>" }}
  ],
  "insertions": [
    {{ "heading": "<heading text>", "level": <2 or 3>, "content": "<body content>", "after_section_id": <int or null> }}
  ],
  "deletions": [ <int section_id>, ... ]
}}

Empty arrays for unused operations. `changes` should describe ONLY what you actually emitted; do not invent unchanged-section reassurances.
"""

        user_query = f"""User instruction: {instruction}

Current notes broken into sections:

{sections_block}

Reminder: return ONLY a JSON object with the targeted edits. Sections you don't reference will be preserved verbatim by the system."""

        raw_text = await self._refine_call_llm(
            system_prompt=system_prompt,
            user_query=user_query,
            model=model,
            model_name=model_name,
            user_email=user_email,
        )

        patch = self._parse_refine_patch_json(raw_text, num_sections=len(sections))

        updated_document = self._apply_patch_to_sections(sections, patch)

        changes = patch.get("changes") or []
        if not changes:
            changes = ["Updated your notes."]

        return {"changes": changes, "updated_document": updated_document}

    # ── Patch helpers ────────────────────────────────────────────────────

    @staticmethod
    def _split_notes_into_sections(notes: str) -> List[Dict[str, object]]:
        """
        Split markdown notes by # / ## / ### headings into addressable sections.
        Each section: {id, heading, level, prefix, body}.
        - id: integer index (0-based)
        - heading: text of heading (without # marks). Empty string for preamble.
        - level: 0 (preamble / no heading) or 1..6 (heading depth).
        - prefix: the exact heading line as it appeared (so we can rebuild verbatim),
                  empty for level 0.
        - body: everything after the heading line up to the next heading (or end),
                with trailing newlines trimmed.
        """
        lines = notes.split("\n")
        sections: List[Dict[str, object]] = []
        current = {"heading": "", "level": 0, "prefix": "", "body_lines": []}

        def commit_current():
            body = "\n".join(current["body_lines"]).rstrip("\n")
            sections.append(
                {
                    "id": len(sections),
                    "heading": current["heading"],
                    "level": current["level"],
                    "prefix": current["prefix"],
                    "body": body,
                }
            )

        heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
        for line in lines:
            m = heading_re.match(line)
            if m:
                # Close current section before starting new one
                if current["heading"] or any(s.strip() for s in current["body_lines"]):
                    commit_current()
                current = {
                    "heading": m.group(2).strip(),
                    "level": len(m.group(1)),
                    "prefix": line,
                    "body_lines": [],
                }
            else:
                current["body_lines"].append(line)
        if current["heading"] or any(s.strip() for s in current["body_lines"]):
            commit_current()

        # Edge case: empty notes
        if not sections:
            sections.append({"id": 0, "heading": "", "level": 0, "prefix": "", "body": ""})
        return sections

    @staticmethod
    def _format_sections_for_prompt(sections: List[Dict[str, object]]) -> str:
        out: List[str] = []
        for s in sections:
            heading_repr = s["heading"] or "(preamble — no heading)"
            level = s["level"]
            body = str(s["body"]).strip()
            if not body:
                body = "(empty)"
            out.append(
                f"--- SECTION_ID={s['id']} | level={level} | heading={heading_repr!r} ---\n{body}"
            )
        return "\n\n".join(out)

    @staticmethod
    def _parse_refine_patch_json(raw_text: str, num_sections: int) -> Dict[str, object]:
        text = (raw_text or "").strip()
        if not text:
            raise ValueError("Empty response from refine LLM")
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```\s*$", "", text)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise ValueError("Refine response was not valid JSON")
            data = json.loads(text[start : end + 1])

        def _list(key: str) -> list:
            val = data.get(key)
            return val if isinstance(val, list) else []

        # Defensive normalization & range-check
        edits = []
        for e in _list("edits"):
            if not isinstance(e, dict):
                continue
            sid = e.get("section_id")
            new_content = e.get("new_content")
            if isinstance(sid, int) and 0 <= sid < num_sections and isinstance(new_content, str):
                edits.append({"section_id": sid, "new_content": new_content})

        appends = []
        for a in _list("append_to_section"):
            if not isinstance(a, dict):
                continue
            sid = a.get("section_id")
            appended = a.get("appended_content")
            if isinstance(sid, int) and 0 <= sid < num_sections and isinstance(appended, str):
                appends.append({"section_id": sid, "appended_content": appended})

        insertions = []
        for ins in _list("insertions"):
            if not isinstance(ins, dict):
                continue
            heading = ins.get("heading")
            content = ins.get("content")
            level = ins.get("level")
            after = ins.get("after_section_id")
            if not isinstance(heading, str) or not isinstance(content, str):
                continue
            if not isinstance(level, int) or level < 1 or level > 6:
                level = 2
            if after is not None:
                if not isinstance(after, int) or after < -1 or after >= num_sections:
                    after = None
            insertions.append(
                {
                    "heading": heading.strip(),
                    "level": level,
                    "content": content,
                    "after_section_id": after,
                }
            )

        deletions = []
        for sid in _list("deletions"):
            if isinstance(sid, int) and 0 <= sid < num_sections:
                deletions.append(sid)

        changes = []
        for c in _list("changes"):
            c_str = str(c).strip()
            if c_str:
                changes.append(c_str)

        # Sanity: if model returned zero ops, we have no work to do
        if not edits and not appends and not insertions and not deletions:
            logger.warning(
                "[RefineNotes] Model returned no operations. Raw: %s",
                text[:500],
            )

        return {
            "changes": changes,
            "edits": edits,
            "append_to_section": appends,
            "insertions": insertions,
            "deletions": deletions,
        }

    @staticmethod
    def _apply_patch_to_sections(
        sections: List[Dict[str, object]], patch: Dict[str, object]
    ) -> str:
        """
        Deterministically apply the model's patch to the section list, then
        rebuild the markdown document. Sections never referenced are kept
        byte-for-byte identical.
        """
        # Work on a shallow copy so we don't mutate input
        sections = [dict(s) for s in sections]

        # 1. Apply edits (replace body)
        edits = patch.get("edits") or []
        if isinstance(edits, list):
            for e in edits:
                sid = e.get("section_id")
                if isinstance(sid, int) and 0 <= sid < len(sections):
                    sections[sid]["body"] = str(e.get("new_content") or "").rstrip("\n")

        # 2. Apply appends
        appends = patch.get("append_to_section") or []
        if isinstance(appends, list):
            for a in appends:
                sid = a.get("section_id")
                if isinstance(sid, int) and 0 <= sid < len(sections):
                    existing = str(sections[sid]["body"] or "").rstrip("\n")
                    appended = str(a.get("appended_content") or "").rstrip("\n")
                    if existing and appended:
                        sections[sid]["body"] = existing + "\n" + appended
                    elif appended:
                        sections[sid]["body"] = appended

        # 3. Apply deletions (mark; remove after we know indices stay valid for inserts)
        deletions = set(patch.get("deletions") or [])

        # 4. Build output preserving section order, with insertions placed after their anchor.
        # Group new insertions by their after_section_id (or None for top).
        insertions = patch.get("insertions") or []
        ins_by_anchor: Dict[object, List[Dict[str, object]]] = {}
        for ins in insertions:
            anchor = ins.get("after_section_id")
            key = anchor if isinstance(anchor, int) else None
            ins_by_anchor.setdefault(key, []).append(ins)

        out_chunks: List[str] = []

        def render_section(sec: Dict[str, object]) -> str:
            body = str(sec.get("body") or "").rstrip("\n")
            prefix = str(sec.get("prefix") or "")
            if prefix:
                return prefix + ("\n" + body if body else "")
            return body

        def render_insertion(ins: Dict[str, object]) -> str:
            level = int(ins.get("level") or 2)
            heading = str(ins.get("heading") or "").strip()
            content = str(ins.get("content") or "").rstrip("\n")
            heading_line = ("#" * level) + " " + heading if heading else ""
            if heading_line and content:
                return heading_line + "\n" + content
            return heading_line or content

        # Insertions anchored at "top" (after_section_id == None)
        for ins in ins_by_anchor.get(None, []):
            rendered = render_insertion(ins)
            if rendered:
                out_chunks.append(rendered)

        for sec in sections:
            sid = sec.get("id")
            if sid in deletions:
                continue
            rendered = render_section(sec)
            if rendered:
                out_chunks.append(rendered)
            # Insertions anchored after this section
            for ins in ins_by_anchor.get(sid, []):
                rendered_ins = render_insertion(ins)
                if rendered_ins:
                    out_chunks.append(rendered_ins)

        # Insertions anchored after a deleted/invalid section_id (-1 or stale)
        # were already filtered to None or valid IDs; nothing extra to do here.

        return "\n\n".join(chunk for chunk in out_chunks if chunk.strip())

    async def _refine_call_llm(
        self,
        system_prompt: str,
        user_query: str,
        model: str,
        model_name: str,
        user_email: Optional[str],
    ) -> str:
        """Non-streaming, JSON-mode LLM call for refine_notes."""
        if model == "gemini":
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            if not api_key:
                api_key = await self.db.get_api_key("gemini", user_email=user_email)
            if not api_key:
                raise ValueError("Gemini API key not found")

            text = await generate_content_text_async(
                api_key=api_key,
                model=model_name,
                contents=user_query,
                config={
                    "system_instruction": system_prompt,
                    "temperature": 0.2,
                    "response_mime_type": "application/json",
                },
            )
            return text or ""

        if model == "openai":
            api_key = await self.db.get_api_key("openai", user_email=user_email)
            if not api_key:
                raise ValueError("OpenAI API key not found")
            client = AsyncOpenAI(api_key=api_key)
            response = await client.chat.completions.create(
                model=model_name,
                temperature=0.2,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_query},
                ],
            )
            return response.choices[0].message.content or ""

        if model == "anthropic" or model == "claude":
            api_key = await self.db.get_api_key("claude", user_email=user_email)
            if not api_key:
                raise ValueError("Anthropic API key not found")
            client = AsyncAnthropic(api_key=api_key)
            response = await client.messages.create(
                model=model_name,
                max_tokens=8192,
                temperature=0.2,
                system=system_prompt
                + "\n\nReturn ONLY a JSON object. No prose, no code fences.",
                messages=[{"role": "user", "content": user_query}],
            )
            parts: List[str] = []
            for block in response.content:
                text = getattr(block, "text", None)
                if text:
                    parts.append(text)
            return "".join(parts)

        if model == "groq":
            api_key = await self.db.get_api_key("groq", user_email=user_email)
            if not api_key:
                api_key = os.getenv("GROQ_API_KEY")
            if not api_key:
                raise ValueError("Groq API key not found")
            client = AsyncGroq(api_key=api_key)
            response = await client.chat.completions.create(
                model=model_name,
                temperature=0.2,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_query},
                ],
            )
            return response.choices[0].message.content or ""

        raise ValueError(f"Unsupported model provider for refine: {model}")

