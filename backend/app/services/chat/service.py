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

import logging
import os
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
    ):
        """
        Refine meeting notes based on user instruction and transcript context.
        """
        if not model or not model_name:
            model = "openai"
            model_name = "gpt-5.4"
        system_prompt = f"""You are an expert meeting notes editor.
Your task is to REFINE the Current Meeting Notes based strictly on the User Instruction and the provided Context (Transcript).

Context (Meeting Transcript):
---
{transcript_context[:30000]}
---

Guidelines:
1. You MUST start your response with a detailed bulleted list of changes made.
2. You MUST then output exactly: "|||SEPARATOR|||" (without quotes).
3. After the separator, provide the FULL updated notes content.
"""

        user_query = f"""Current Meeting Notes:
---
{notes}
---

User Instruction: {instruction}
"""

        return await self.stream_response(
            system_prompt, user_query, model, model_name, user_email
        )
