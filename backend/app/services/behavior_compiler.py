"""
BehaviorCompiler — Converts natural-language behavior cards into BehaviorSpec.

This module handles the one-shot compilation from a user's markdown behavior
file into a structured BehaviorSpec that the engine uses at runtime.

Compilation happens ONCE (on save/upload), not in the hot path. The result
is cached alongside the user's style.

Strategy:
  1. Parse markdown into sections (Who You Are, When to Speak, etc.)
  2. Send sections to LLM with structured-output prompt
  3. LLM returns JSON matching BehaviorSpec schema
  4. Validate with Pydantic
  5. Fallback: regex-based extraction if LLM fails
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from ..schemas.behavior_spec import BehaviorSpec, OutputCategory, build_default_spec
except (ImportError, ValueError):
    from schemas.behavior_spec import BehaviorSpec, OutputCategory, build_default_spec


# ── Section Parser ────────────────────────────────────────────────────────

KNOWN_SECTIONS = {
    "who you are",
    "when to speak",
    "when to stay silent",
    "how to sound",
    "what to track",
    "what to ignore",
}


def parse_behavior_sections(markdown: str) -> Dict[str, str]:
    """
    Parse a behavior card markdown into sections keyed by heading text.

    Returns dict like:
        {
            "title": "My Engineering Advisor",
            "who you are": "You are a senior staff engineer...",
            "when to speak": "- When someone commits to...",
            ...
        }
    """
    text = (markdown or "").strip()
    if not text:
        return {}

    sections: Dict[str, str] = {}

    # Extract H1 title
    h1_match = re.match(r"^#\s+(.+)$", text, re.MULTILINE)
    if h1_match:
        sections["title"] = h1_match.group(1).strip()

    # Split by H2 headings
    h2_pattern = re.compile(r"^##\s+(.+)$", re.MULTILINE)
    h2_matches = list(h2_pattern.finditer(text))

    for i, match in enumerate(h2_matches):
        heading = match.group(1).strip().lower()
        start = match.end()
        end = h2_matches[i + 1].start() if i + 1 < len(h2_matches) else len(text)
        body = text[start:end].strip()
        sections[heading] = body

    return sections


def parse_bullet_items(text: str) -> List[str]:
    """Extract bullet items from a section body."""
    items: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Match - or * or numbered bullets
        cleaned = re.sub(r"^[-*]\s+", "", line)
        cleaned = re.sub(r"^\d+\.\s+", "", cleaned)
        if cleaned != line or line:  # was a bullet or just text
            items.append(cleaned.strip())
    return [item for item in items if item]


def parse_track_categories(text: str) -> List[Dict[str, str]]:
    """
    Parse 'What to Track' section into category definitions.

    Expected format:
        - 🔧 Technical Decisions: Any choice about architecture, tools, or approach
        - ⚠️ Technical Risks: Scalability, security, or reliability concerns

    Returns list of dicts with keys: icon, label, description
    """
    categories: List[Dict[str, str]] = []
    for item in parse_bullet_items(text):
        # Try to extract emoji icon at start
        icon = "📌"
        remaining = item

        # Check for leading emoji (common emoji ranges)
        emoji_match = re.match(
            r"^([\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2705\u274C\u2753\u2757\u26A0\u267B✅❓📋💡🔧⚠️👤🔄🎯⚡🔒📌🔍🚫📊📈🔔💬🏷️])\s*",
            remaining,
        )
        if emoji_match:
            icon = emoji_match.group(1)
            remaining = remaining[emoji_match.end():]

        # Split label: description
        if ":" in remaining:
            label, description = remaining.split(":", 1)
            label = label.strip()
            description = description.strip()
        else:
            label = remaining.strip()
            description = ""

        if label:
            categories.append({
                "icon": icon,
                "label": label,
                "description": description,
            })

    return categories


# ── LLM Compilation Prompt ────────────────────────────────────────────────

COMPILATION_PROMPT = """You are a behavior compiler. Convert the user's meeting AI behavior description into a structured JSON specification.

The user wrote a behavior card in natural language. Your job is to extract structured fields from it.

## User's Behavior Card:
{behavior_markdown}

## Output Schema (return ONLY valid JSON, no markdown fences):
{{
  "name": "string — display name from the H1 heading",
  "personality_prompt": "string — compiled 'Who You Are' section as a system prompt",
  "silence_mode": false,
  "speak_triggers": ["string — each bullet from 'When to Speak'"],
  "silence_triggers": ["string — each bullet from 'When to Stay Silent'"],
  "topic_filters": ["string — topics the AI should focus on, empty if general"],
  "output_categories": [
    {{
      "id": "snake_case_id",
      "label": "Human Label",
      "icon": "emoji",
      "description": "from What to Track",
      "display_hint": "card or banner or subtle",
      "priority_default": "low or medium or high or critical",
      "base_confidence": 0.70
    }}
  ],
  "ignore_topics": ["string — from 'What to Ignore'"],
  "tone_instruction": "string — compiled 'How to Sound' as a concise instruction",
  "max_words_per_insight": 30,
  "base_confidence": 0.70,
  "suggestion_cooldown_seconds": 60,
  "intervention_cooldown_seconds": 120,
  "summary_visibility": "background",
  "warmup_seconds": 0
}}

## Rules:
1. If a section is missing, use sensible defaults.
2. For output_categories, derive from "What to Track". If missing, use these defaults:
   - decision (✅, high, card)
   - open_question (❓, medium, card)
   - action_item (📋, high, card)
   - key_insight (💡, medium, subtle)
3. Set silence_mode=true ONLY if the behavior explicitly says to never intervene (fly on the wall, silent observer, etc.)
4. base_confidence should be higher (0.78+) for selective/advisor behaviors, lower (0.65) for aggressive ones.
5. suggestion_cooldown_seconds should be lower for aggressive behaviors (30-45) and higher for selective ones (90-120).
6. display_hint: use "banner" for critical/urgent categories, "subtle" for low-priority observational ones, "card" for standard ones.
7. priority_default: align with the behavior's intensity.
8. For the personality_prompt, write it as if giving instructions to the AI: "You are a..."
9. max_words_per_insight: extract from tone guidance if mentioned (e.g., "max 2 sentences" → 30. "detailed" → 60).
10. Return ONLY the JSON object. No explanation. No markdown fences."""


# ── Compiler Class ────────────────────────────────────────────────────────

class BehaviorCompiler:
    """
    Compiles a natural-language behavior card into a BehaviorSpec.

    Usage:
        compiler = BehaviorCompiler(db=db_instance, user_email="user@example.com")
        spec = await compiler.compile("# My Advisor\n## Who You Are\n...")
    """

    def __init__(self, db: Any = None, user_email: str = ""):
        self.db = db
        self.user_email = user_email

    async def compile(
        self,
        behavior_markdown: str,
        use_llm: bool = True,
    ) -> Tuple[BehaviorSpec, bool]:
        """
        Compile a behavior card → BehaviorSpec.

        Returns (spec, used_llm).
        If LLM compilation fails, falls back to regex extraction.
        """
        text = (behavior_markdown or "").strip()
        if not text:
            return build_default_spec(), False

        # Always try regex first for sections
        sections = parse_behavior_sections(text)

        if use_llm:
            try:
                spec = await self._compile_with_llm(text, sections)
                if spec:
                    spec.source_markdown = text
                    spec.format_version = 2
                    return spec, True
            except Exception as e:
                logger.warning(
                    "[BehaviorCompiler] LLM compilation failed, using regex fallback: %s",
                    e,
                )

        # Regex fallback
        spec = self._compile_with_regex(text, sections)
        spec.source_markdown = text
        spec.format_version = 2
        return spec, False

    async def _compile_with_llm(
        self,
        markdown: str,
        sections: Dict[str, str],
    ) -> Optional[BehaviorSpec]:
        """Use an LLM to compile the behavior card."""
        api_key = await self._get_api_key()
        if not api_key:
            logger.info("[BehaviorCompiler] No API key available for compilation")
            return None

        provider = self._get_provider()
        model = self._get_model()
        prompt = COMPILATION_PROMPT.format(behavior_markdown=markdown[:8000])

        try:
            raw_json = await self._call_llm(
                provider=provider,
                model=model,
                api_key=api_key,
                prompt=prompt,
            )
            if not raw_json:
                return None

            parsed = self._extract_json(raw_json)
            if not parsed:
                return None

            spec = self._dict_to_spec(parsed)
            spec.compiled_at = datetime.utcnow().isoformat()
            spec.compiler_model = f"{provider}/{model}"
            return spec

        except Exception as e:
            logger.error("[BehaviorCompiler] LLM compilation error: %s", e, exc_info=True)
            return None

    def _compile_with_regex(
        self,
        markdown: str,
        sections: Dict[str, str],
    ) -> BehaviorSpec:
        """Regex-based fallback compilation."""
        spec = build_default_spec()

        # Name from title
        if "title" in sections:
            spec.name = sections["title"]

        # Personality from "Who You Are"
        who = sections.get("who you are", "")
        if who:
            spec.personality_prompt = who.strip()

        # Speak triggers
        speak = sections.get("when to speak", "")
        if speak:
            spec.speak_triggers = parse_bullet_items(speak)

        # Silence triggers
        silence = sections.get("when to stay silent", "")
        if silence:
            spec.silence_triggers = parse_bullet_items(silence)

        # Detect silence mode
        lower_text = markdown.lower()
        silence_keywords = [
            "fly on the wall",
            "silent observer",
            "never intervene",
            "never speak",
            "just listen",
            "only observe",
            "stay completely silent",
        ]
        if any(kw in lower_text for kw in silence_keywords):
            spec.silence_mode = True

        # Output categories from "What to Track"
        track = sections.get("what to track", "")
        if track:
            parsed_cats = parse_track_categories(track)
            categories: List[OutputCategory] = []
            for cat_dict in parsed_cats:
                cat_id = re.sub(r"[^a-z0-9]+", "_", cat_dict["label"].lower()).strip("_")
                categories.append(OutputCategory(
                    id=cat_id,
                    label=cat_dict["label"],
                    icon=cat_dict["icon"],
                    description=cat_dict["description"],
                    display_hint="card",
                    priority_default="medium",
                    base_confidence=0.70,
                ))
            if categories:
                spec.output_categories = categories

        # Ignore topics
        ignore = sections.get("what to ignore", "")
        if ignore:
            spec.ignore_topics = parse_bullet_items(ignore)

        # Tone
        tone = sections.get("how to sound", "")
        if tone:
            spec.tone_instruction = tone.strip()

        spec.compiled_at = datetime.utcnow().isoformat()
        spec.compiler_model = "regex_fallback"
        return spec

    def _dict_to_spec(self, data: Dict[str, Any]) -> BehaviorSpec:
        """Convert LLM JSON output to a validated BehaviorSpec."""
        # Parse output categories
        raw_cats = data.get("output_categories") or []
        categories: List[OutputCategory] = []
        for raw_cat in raw_cats:
            if not isinstance(raw_cat, dict):
                continue
            cat_id = str(raw_cat.get("id") or "").strip()
            label = str(raw_cat.get("label") or "").strip()
            if not cat_id or not label:
                continue
            categories.append(OutputCategory(
                id=cat_id,
                label=label,
                icon=str(raw_cat.get("icon") or "📌"),
                description=str(raw_cat.get("description") or ""),
                display_hint=str(raw_cat.get("display_hint") or "card"),
                priority_default=str(raw_cat.get("priority_default") or "medium"),
                base_confidence=max(0.5, min(0.95, float(raw_cat.get("base_confidence") or 0.70))),
            ))

        # Use defaults if LLM returned no categories
        if not categories:
            categories = list(build_default_spec().output_categories)

        spec = BehaviorSpec(
            name=str(data.get("name") or "Meeting Assistant"),
            personality_prompt=str(data.get("personality_prompt") or ""),
            silence_mode=bool(data.get("silence_mode", False)),
            speak_triggers=_str_list(data.get("speak_triggers")),
            silence_triggers=_str_list(data.get("silence_triggers")),
            topic_filters=_str_list(data.get("topic_filters")),
            warmup_seconds=max(0, int(data.get("warmup_seconds") or 0)),
            output_categories=categories,
            ignore_topics=_str_list(data.get("ignore_topics")),
            tone_instruction=str(data.get("tone_instruction") or ""),
            max_words_per_insight=max(5, min(200, int(data.get("max_words_per_insight") or 30))),
            base_confidence=max(0.5, min(0.95, float(data.get("base_confidence") or 0.70))),
            suggestion_cooldown_seconds=max(10, int(data.get("suggestion_cooldown_seconds") or 60)),
            intervention_cooldown_seconds=max(10, int(data.get("intervention_cooldown_seconds") or 120)),
            summary_visibility=str(data.get("summary_visibility") or "background"),
        )
        return spec

    async def _call_llm(
        self,
        provider: str,
        model: str,
        api_key: str,
        prompt: str,
    ) -> Optional[str]:
        """Call an LLM provider for compilation."""
        import asyncio

        if provider == "gemini":
            try:
                from .gemini_client import generate_content_text_async
            except (ImportError, ValueError):
                from services.gemini_client import generate_content_text_async

            return await asyncio.wait_for(
                generate_content_text_async(
                    api_key=api_key,
                    model=model,
                    contents=prompt,
                    config={"temperature": 0.1},
                ),
                timeout=15.0,
            )

        if provider == "openai":
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key)
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    temperature=0.1,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": "Return strict JSON only. No markdown."},
                        {"role": "user", "content": prompt},
                    ],
                ),
                timeout=15.0,
            )
            return response.choices[0].message.content or ""

        if provider == "anthropic":
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=api_key)
            response = await asyncio.wait_for(
                client.messages.create(
                    model=model,
                    max_tokens=2000,
                    temperature=0.1,
                    system="Return strict JSON only. No markdown.",
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=15.0,
            )
            parts: List[str] = []
            for block in getattr(response, "content", []) or []:
                text = getattr(block, "text", None)
                if text:
                    parts.append(text)
            return "".join(parts)

        logger.warning("[BehaviorCompiler] Unsupported provider: %s", provider)
        return None

    def _get_provider(self) -> str:
        raw = os.getenv("AI_PARTICIPANT_PROVIDER", "gemini").strip().lower()
        if raw == "claude":
            return "anthropic"
        return raw

    def _get_model(self) -> str:
        provider = self._get_provider()
        default_models = {
            "gemini": "gemini-2.5-flash",
            "openai": "gpt-4.1-mini",
            "anthropic": "claude-sonnet-4-20250514",
        }
        return os.getenv(
            "AI_PARTICIPANT_MODEL",
            default_models.get(provider, "gemini-2.5-flash"),
        ).strip()

    async def _get_api_key(self) -> Optional[str]:
        provider = self._get_provider()
        key = ""

        if provider == "gemini":
            key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
        elif provider == "openai":
            key = (os.getenv("OPENAI_API_KEY") or "").strip()
        elif provider == "anthropic":
            key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()

        if not key and self.db:
            try:
                provider_key = "claude" if provider == "anthropic" else provider
                key = (await self.db.get_api_key(provider_key, user_email=self.user_email)) or ""
            except Exception:
                pass

        return key.strip() or None

    @staticmethod
    def _extract_json(raw: str) -> Optional[Dict[str, Any]]:
        """Extract JSON from LLM response (handles fenced code blocks)."""
        text = (raw or "").strip()
        if not text:
            return None

        # Try direct parse
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass

        # Try fenced code block
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence_match:
            try:
                obj = json.loads(fence_match.group(1))
                return obj if isinstance(obj, dict) else None
            except Exception:
                pass

        # Try extracting first JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                obj = json.loads(text[start : end + 1])
                return obj if isinstance(obj, dict) else None
            except Exception:
                pass

        return None


def _str_list(value: Any) -> List[str]:
    """Convert a value to a list of strings."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


# ── Convenience Function ──────────────────────────────────────────────────

async def compile_behavior(
    markdown: str,
    db: Any = None,
    user_email: str = "",
    use_llm: bool = True,
) -> Tuple[BehaviorSpec, bool]:
    """
    Convenience function to compile a behavior card.

    Returns (spec, used_llm).
    """
    compiler = BehaviorCompiler(db=db, user_email=user_email)
    return await compiler.compile(markdown, use_llm=use_llm)
