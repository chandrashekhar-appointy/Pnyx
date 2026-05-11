import asyncio
import json
import logging
import os
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Deque, Dict, List, Optional, Tuple

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.gemini import GeminiModel
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.groq import GroqModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.groq import GroqProvider
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.google_gla import GoogleGLAProvider

try:
    from ..schemas.ai_participant import (
        GuardrailAlert,
        GuardrailLLMOutput,
        GuardrailReason,
        HostInterventionCard,
        HostPolicyConfig,
        HostRoleMode,
        HostSuggestion,
        MeetingHostState,
    )
    from ..schemas.behavior_spec import BehaviorSpec, build_default_spec
    from .gemini_client import generate_content_text_async
    from .ai_participant_skills import (
        load_system_skill_templates,
        parse_skill_markdown,
    )
    from .behavior_compiler import compile_behavior
    from .behavior_auto_tuner import BehaviorAutoTuner
except (ImportError, ValueError):
    from schemas.ai_participant import (
        GuardrailAlert,
        GuardrailLLMOutput,
        GuardrailReason,
        HostInterventionCard,
        HostPolicyConfig,
        HostRoleMode,
        HostSuggestion,
        MeetingHostState,
    )
    from schemas.behavior_spec import BehaviorSpec, build_default_spec
    from services.gemini_client import generate_content_text_async
    from services.ai_participant_skills import (
        load_system_skill_templates,
        parse_skill_markdown,
    )
    from services.behavior_compiler import compile_behavior
    from services.behavior_auto_tuner import BehaviorAutoTuner

logger = logging.getLogger(__name__)

SYSTEM_HOST_SKILLS: Dict[str, str] = load_system_skill_templates()
CORE_EVENT_TYPES = {"decision_candidate", "open_discussion"}
BEHAVIOR_CATEGORY_ALIASES = {
    "decision_candidate": {"decision_candidate", "decision", "decisions"},
    "open_discussion": {
        "open_discussion",
        "open_question",
        "open_questions",
        "discussion",
        "discussions",
    },
    "follow_up_needed": {"follow_up_needed", "action_item", "action_items"},
    "risk_signal": {"risk_signal", "key_insight", "insight", "insights"},
    "ai_insight": {"ai_insight", "key_insight", "insight", "insights"},
}
DEFAULT_PROVIDER_MODELS = {
    "gemini": "gemini-3.1-pro",
    "openai": "gpt-5.4",
    "anthropic": "claude-3-5-sonnet-latest",
    "openrouter": "anthropic/claude-3.5-sonnet",
}
DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
DECISION_CUE_RE = re.compile(
    r"("
    r"\b(?:decide(?:d|s)?|decision|agreed?|finali[sz]ed?|approved?|confirmed?|"
    r"we(?:'ll| will)|let(?:'s| us) go with|ship(?:ping)?|proceed(?:ing)? with|"
    r"move forward with|lock(?:ed|ing)? in|chosen?|settled on)\b|"
    r"(?:निर्णय|फैसला|तय|फाइनल|सहमत|मंजूर|पक्का|लॉक|करेंगे|जाएंगे|जायेंगे)"
    r")",
    flags=re.IGNORECASE,
)
DECISION_META_RE = re.compile(
    r"(surface|surfacing|show|showing|display|दिख|नहीं दिख|नही दिख|नज़र|नजर)",
    flags=re.IGNORECASE,
)
UNRESOLVED_CUE_RE = re.compile(
    r"\b("
    r"question|unclear|not sure|need to check|need to confirm|follow up|follow-up|"
    r"pending|blocker|issue|risk|debate|discuss|open item|open question|"
    r"let's revisit|to be decided|tbd"
    r")\b",
    flags=re.IGNORECASE,
)


def _clean_env_value(raw: Optional[str], default: str = "") -> str:
    value = str(raw or "").strip()
    if not value:
        return default
    if " #" in value:
        value = value.split(" #", 1)[0].strip()
    return value or default


def _normalize_provider_name(raw: Optional[str], default: str = "gemini") -> str:
    provider = _clean_env_value(raw, default).lower()
    if provider == "claude":
        return "anthropic"
    return provider


@dataclass
class MeetingContext:
    meeting_id: str
    title: str = ""
    goal: str = ""
    description: str = ""
    agenda_text: str = ""
    participant_names: Optional[List[str]] = None


class RollingTranscriptBuffer:
    def __init__(self, window_seconds: int = 180, max_chars: int = 6000):
        self.window_seconds = window_seconds
        self.max_chars = max_chars
        self._items: Deque[Tuple[float, str]] = deque()
        self._char_count = 0

    def add(self, timestamp_seconds: float, text: str) -> None:
        clean_text = (text or "").strip()
        if not clean_text:
            return

        ts = float(timestamp_seconds)
        self._items.append((ts, clean_text))
        self._char_count += len(clean_text)
        self._prune(ts)

    def _prune(self, current_ts: float) -> None:
        window_start = current_ts - float(self.window_seconds)
        while self._items and self._items[0][0] < window_start:
            _, old_text = self._items.popleft()
            self._char_count -= len(old_text)

        while self._items and self._char_count > self.max_chars:
            _, old_text = self._items.popleft()
            self._char_count -= len(old_text)

    def is_empty(self) -> bool:
        return not self._items

    def get_duration_seconds(self) -> float:
        if len(self._items) < 2:
            return 0.0
        return max(0.0, self._items[-1][0] - self._items[0][0])

    def get_text(self) -> str:
        return "\n".join(item[1] for item in self._items)

    def get_recent_text(self, seconds: float) -> str:
        """Return text from the last N seconds of the buffer (for cue-word scanning)."""
        if not self._items:
            return ""
        cutoff = self._items[-1][0] - seconds
        return "\n".join(text for ts, text in self._items if ts >= cutoff)

    def get_last_gap_seconds(self) -> float:
        """Gap between the two most recent transcript items. 0 if <2 items."""
        if len(self._items) < 2:
            return 0.0
        return max(0.0, self._items[-1][0] - self._items[-2][0])

    def get_char_count(self) -> int:
        return max(0, self._char_count)


class GuardrailEvaluator:
    def __init__(self):
        self.min_confidence = float(os.getenv("AI_PARTICIPANT_MIN_CONFIDENCE", "0.45"))
        self.cooldown_seconds = int(os.getenv("AI_PARTICIPANT_COOLDOWN_SECONDS", "180"))
        self.decision_logs = (
            os.getenv("AI_PARTICIPANT_DECISION_LOGS", "true").strip().lower() == "true"
        )
        self.agenda_sustained_cycles = int(
            os.getenv("AI_PARTICIPANT_AGENDA_SUSTAINED_CYCLES", "1")
        )
        self.no_decision_threshold_seconds = int(
            os.getenv("AI_PARTICIPANT_NO_DECISION_SECONDS", "360")
        )
        self.unresolved_question_threshold_seconds = int(
            os.getenv("AI_PARTICIPANT_UNRESOLVED_QUESTION_SECONDS", "240")
        )

        self._agenda_deviation_streak = 0
        self._last_alert_signature = ""
        self._last_publish_at = 0.0
        self._metrics: Dict[str, Any] = {
            "evaluations": 0,
            "published": 0,
            "published_by_reason": {},
            "suppressed_no_intervention": 0,
            "suppressed_missing_fields": 0,
            "suppressed_low_confidence": 0,
            "suppressed_agenda_not_sustained": 0,
            "suppressed_no_decision_duration": 0,
            "suppressed_unresolved_question_duration": 0,
            "suppressed_cooldown": 0,
            "suppressed_duplicate": 0,
        }

    def evaluate(
        self,
        assessment: GuardrailLLMOutput,
        window_duration_seconds: float,
        now_ts: float,
    ) -> Optional[GuardrailAlert]:
        reason_value = assessment.reason.value if assessment.reason else None
        confidence = float(assessment.confidence or 0.0)

        def log_decision(decision: str, detail: str) -> None:
            if not self.decision_logs:
                return
            logger.info(
                "[AIParticipant][Decision] %s reason=%s confidence=%.2f window_duration=%.1fs detail=%s",
                decision,
                reason_value,
                confidence,
                window_duration_seconds,
                detail,
            )

        self._metrics["evaluations"] += 1
        if not assessment.intervention_required:
            self._agenda_deviation_streak = 0
            self._metrics["suppressed_no_intervention"] += 1
            log_decision("suppressed_no_intervention", "intervention_required=false")
            return None

        if not assessment.reason or not assessment.insight:
            self._metrics["suppressed_missing_fields"] += 1
            log_decision(
                "suppressed_missing_fields", "missing reason or insight in model output"
            )
            return None

        if confidence < self.min_confidence:
            self._metrics["suppressed_low_confidence"] += 1
            log_decision(
                "suppressed_low_confidence",
                f"confidence={confidence:.2f} < min_confidence={self.min_confidence:.2f}",
            )
            return None

        if assessment.reason == GuardrailReason.AGENDA_DEVIATION:
            self._agenda_deviation_streak += 1
            if self._agenda_deviation_streak < self.agenda_sustained_cycles:
                self._metrics["suppressed_agenda_not_sustained"] += 1
                log_decision(
                    "suppressed_agenda_not_sustained",
                    f"streak={self._agenda_deviation_streak} < required={self.agenda_sustained_cycles}",
                )
                return None
        else:
            self._agenda_deviation_streak = 0

        if (
            assessment.reason == GuardrailReason.NO_DECISION
            and window_duration_seconds < self.no_decision_threshold_seconds
        ):
            self._metrics["suppressed_no_decision_duration"] += 1
            log_decision(
                "suppressed_no_decision_duration",
                f"window={window_duration_seconds:.1f}s < threshold={self.no_decision_threshold_seconds}s",
            )
            return None

        if (
            assessment.reason == GuardrailReason.UNRESOLVED_QUESTION
            and window_duration_seconds < self.unresolved_question_threshold_seconds
        ):
            self._metrics["suppressed_unresolved_question_duration"] += 1
            log_decision(
                "suppressed_unresolved_question_duration",
                f"window={window_duration_seconds:.1f}s < threshold={self.unresolved_question_threshold_seconds}s",
            )
            return None

        insight = self._normalize_insight(assessment.insight)
        signature = self._signature(assessment.reason.value, insight)

        if now_ts - self._last_publish_at < self.cooldown_seconds:
            self._metrics["suppressed_cooldown"] += 1
            log_decision(
                "suppressed_cooldown",
                f"since_last={now_ts - self._last_publish_at:.1f}s < cooldown={self.cooldown_seconds}s",
            )
            return None

        if signature == self._last_alert_signature:
            self._metrics["suppressed_duplicate"] += 1
            log_decision(
                "suppressed_duplicate",
                "same reason+insight signature as previous alert",
            )
            return None

        self._last_alert_signature = signature
        self._last_publish_at = now_ts
        self._metrics["published"] += 1
        by_reason = self._metrics.setdefault("published_by_reason", {})
        by_reason[assessment.reason.value] = (
            int(by_reason.get(assessment.reason.value) or 0) + 1
        )

        return GuardrailAlert(
            id=str(uuid.uuid4()),
            reason=assessment.reason,
            insight=insight,
            confidence=round(confidence, 2),
            timestamp=datetime.utcnow().isoformat(),
        )

    @staticmethod
    def _signature(reason: str, insight: str) -> str:
        normalized = " ".join((insight or "").strip().lower().split())
        return f"{reason}:{normalized}"

    @staticmethod
    def _normalize_insight(insight: str) -> str:
        text = " ".join((insight or "").strip().split())
        words = text.split(" ")
        if len(words) <= 30:
            return text
        return " ".join(words[:30]).rstrip(" ,.;") + "."

    def get_metrics_snapshot(self) -> Dict[str, Any]:
        payload = dict(self._metrics)
        payload["published_by_reason"] = dict(
            self._metrics.get("published_by_reason") or {}
        )
        return payload


class AIParticipantEngine:
    def __init__(
        self,
        db,
        user_email: str,
        meeting_context: MeetingContext,
    ):
        self.db = db
        self.user_email = user_email
        self.meeting_context = meeting_context

        self.enabled = (
            _clean_env_value(
                os.getenv("AI_PARTICIPANT_ENABLED", "true"), "true"
            ).lower()
            == "true"
        )
        self.provider = _normalize_provider_name(
            os.getenv("AI_PARTICIPANT_PROVIDER", "openai"), "openai"
        )
        self.model_name = _clean_env_value(
            os.getenv(
                "AI_PARTICIPANT_MODEL",
                DEFAULT_PROVIDER_MODELS.get(
                    self.provider, DEFAULT_PROVIDER_MODELS["gemini"]
                ),
            ),
            DEFAULT_PROVIDER_MODELS.get(
                self.provider, DEFAULT_PROVIDER_MODELS["gemini"]
            ),
        )
        fallback_models = _clean_env_value(
            os.getenv("AI_PARTICIPANT_FALLBACK_MODELS", ""), ""
        )
        self.fallback_models = [
            m.strip() for m in fallback_models.split(",") if (m or "").strip()
        ]
        self.llm_timeout_seconds = float(
            os.getenv("AI_PARTICIPANT_LLM_TIMEOUT_SECONDS", "12")
        )
        # Heartbeat: max gap between any two analysis runs, used as a fallback
        # to keep the rolling summary fresh even when nothing "interesting" is
        # happening. Event-driven triggers usually fire much sooner than this.
        self.analysis_interval_seconds = int(
            os.getenv("AI_PARTICIPANT_ANALYSIS_INTERVAL_SECONDS", "25")
        )
        # Event-driven cadence ceiling: even if a trigger fires, don't run the
        # expensive analysis more often than this. Bounds LLM cost.
        self.analysis_cadence_ceiling_seconds = float(
            os.getenv("AI_PARTICIPANT_CADENCE_CEILING_SECONDS", "8")
        )
        # Speaker-pause threshold: a gap ≥ this between two transcript finals
        # often marks the end of a thought / decision and is a strong trigger.
        self.pause_trigger_seconds = float(
            os.getenv("AI_PARTICIPANT_PAUSE_TRIGGER_SECONDS", "2.0")
        )
        # Fast probe: cheap LLM (Gemini Flash by default) decides whether the
        # expensive agent should run this cycle. Disable to always run agent.
        self.fast_probe_enabled = (
            os.getenv("AI_PARTICIPANT_FAST_PROBE_ENABLED", "true").strip().lower()
            == "true"
        )
        self.fast_probe_model = os.getenv(
            "AI_PARTICIPANT_FAST_PROBE_MODEL", "gemini-2.5-flash"
        )
        self.fast_probe_timeout_seconds = float(
            os.getenv("AI_PARTICIPANT_FAST_PROBE_TIMEOUT_SECONDS", "4")
        )
        self.verbose_logs = (
            os.getenv("AI_PARTICIPANT_VERBOSE_LOGS", "false").strip().lower() == "true"
        )
        self.min_chars_before_analysis = int(
            os.getenv("AI_PARTICIPANT_MIN_WINDOW_CHARS", "0")
        )

        window_seconds = int(os.getenv("AI_PARTICIPANT_WINDOW_SECONDS", "180"))
        max_chars = int(os.getenv("AI_PARTICIPANT_MAX_WINDOW_CHARS", "6000"))

        self.buffer = RollingTranscriptBuffer(
            window_seconds=window_seconds,
            max_chars=max_chars,
        )
        self.evaluator = GuardrailEvaluator()

        self._last_analysis_at = 0.0
        self._lock = asyncio.Lock()
        self._provider_api_key: Optional[str] = None
        self._runtime_config_loaded = False
        self._missing_key_logged = False
        self._last_alert_summary = "None"

        self._host_event_last_published_at: Dict[str, float] = {}
        self._host_event_last_signature: Dict[str, str] = {}
        self._host_state = MeetingHostState(meeting_id=self.meeting_context.meeting_id)
        default_skill_markdown = os.getenv(
            "AI_HOST_DEFAULT_SKILL_MARKDOWN", ""
        ).strip() or SYSTEM_HOST_SKILLS.get("facilitator", "")
        self._active_skill_markdown = default_skill_markdown
        self._active_skill_definition = parse_skill_markdown(default_skill_markdown)
        self._host_policy = self._load_policy_from_skill(
            skill_text=default_skill_markdown,
            source="system",
        )
        self._host_policy_source = "system"
        self._temp_suggestions: List[HostSuggestion] = []

        # ── Behavior Spec (v2 dynamic system) ──
        self._behavior_spec: BehaviorSpec = build_default_spec()
        self._auto_tuner: BehaviorAutoTuner = BehaviorAutoTuner(self._behavior_spec)
        self._behavior_agent: Optional[Agent] = (
            None  # Dynamic agent, rebuilt when spec changes
        )
        self._session_start_time: float = time.time()

        self._last_heartbeat_at: float = 0.0
        self._stats: Dict[str, Any] = {
            "analysis_attempts": 0,
            "analysis_skipped_small_window": 0,
            "analysis_skipped_interval": 0,
            "trigger_pause": 0,
            "trigger_cue_word": 0,
            "trigger_ceiling": 0,
            "trigger_heartbeat": 0,
            "fast_probe_calls": 0,
            "fast_probe_interesting": 0,
            "fast_probe_skipped_uninteresting": 0,
            "fast_probe_failures": 0,
            "llm_calls": 0,
            "llm_failures": 0,
            "llm_timeouts": 0,
            "parse_failures": 0,
            "normalize_silent_fallbacks": 0,
            "assessment_none": 0,
            "model_fallbacks": 0,
            "last_model_used": self.model_name,
            "provider": self.provider,
            "last_assessment_intervention_required": None,
            "last_assessment_reason": None,
            "last_assessment_confidence": None,
            "last_analysis_at": None,
            "host_suggestions_emitted": 0,
            "host_interventions_emitted": 0,
            "host_suggestions_pinned": 0,
            "host_suggestions_dismissed": 0,
            "host_suggestions_suppressed": 0,
            "host_policy_source": self._host_policy_source,
        }

        # Initialize the Observer Agent
        self.agent = Agent(
            "openai:gpt-5.4",  # Placeholder, will be overriden in runtime
            deps_type=AIParticipantEngine,
            system_prompt=(
                "You are a meeting observer. Use tools to register decisions, discussions, and summary updates.\n"
                "**Hinglish/Multilingual Awareness**: The transcript often contains Hinglish (Hindi + English) code-switching. "
                "Extract decisions, action items, and insights even if they are expressed in mixed languages. Do NOT dismiss Hinglish discussion as noise or assume there is 'no active discussion'.\n"
                "CRITICAL: When calling `update_summary`, you MUST use rich Markdown formatting:\n"
                "- Use `###` headings for sections (Overview, Decisions, etc.)\n"
                "- Use `**` for bold emphasis\n"
                "- Use `- ` for bullet points\n"
                "Your goal is to make the meeting summary visually structured and professional."
            ),
        )

        @self.agent.tool
        async def add_decision(
            ctx: RunContext[AIParticipantEngine],
            title: str,
            content: str,
            confidence: float,
            priority: str = "medium",
        ) -> str:
            """Record a commitment or decision agreed upon by participants."""
            engine = ctx.deps
            title = await engine._ensure_english_text(title, preserve_markdown=False)
            content = await engine._ensure_english_text(
                content, preserve_markdown=False
            )
            if not content.strip():
                return "FAILURE: Decision content is empty."
            if engine._has_similar_host_item("decision_candidate", title, content):
                return "SKIP: This decision is already recorded."

            # We use the engine's existing building logic
            event = {
                "event_type": "decision_candidate",
                "title": title,
                "content": content,
                "confidence": confidence,
                "priority": priority,
            }
            suggestion = engine._build_host_suggestion(event)
            if suggestion:
                engine._temp_suggestions.append(suggestion)
                return f"SUCCESS: Recorded decision: {title}"
            return "FAILURE: Decision did not meet confidence threshold."

        @self.agent.tool
        async def add_discussion(
            ctx: RunContext[AIParticipantEngine],
            title: str,
            content: str,
            confidence: float,
            priority: str = "medium",
        ) -> str:
            """Record an unresolved question or active debate."""
            engine = ctx.deps
            title = await engine._ensure_english_text(title, preserve_markdown=False)
            content = await engine._ensure_english_text(
                content, preserve_markdown=False
            )
            if not content.strip():
                return "FAILURE: Discussion content is empty."
            if engine._has_similar_host_item("open_discussion", title, content):
                return "SKIP: This discussion/topic is already recorded."

            event = {
                "event_type": "open_discussion",
                "title": title,
                "content": content,
                "confidence": confidence,
                "priority": priority,
            }
            suggestion = engine._build_host_suggestion(event)
            if suggestion:
                engine._temp_suggestions.append(suggestion)
                return f"SUCCESS: Recorded discussion: {title}"
            return "FAILURE: Discussion did not meet confidence threshold."

        @self.agent.tool
        async def update_summary(
            ctx: RunContext[AIParticipantEngine], summary_markdown: str
        ) -> str:
            """
            Update the cumulative meeting summary.
            IMPORTANT: Use rich Markdown with:
            - `### Overview` (Level 3 headings for sections)
            - `### Decisions`
            - `### Next Steps`
            - `**Bold**` for emphasis
            - `- ` Bullet points for lists
            - Do NOT use `#` or `##` as they are too large for the side panel.
            """
            engine = ctx.deps
            summary_markdown = await engine._ensure_english_text(
                summary_markdown,
                preserve_markdown=True,
            )
            summary_markdown = summary_markdown.strip()
            if not summary_markdown or len(summary_markdown) < 20:
                return "FAILURE: Summary too short or empty."

            engine._host_state.meeting_summary = summary_markdown
            return "SUCCESS: Summary updated with rich formatting."

        @self.agent.tool
        async def add_action_item(
            ctx: RunContext[AIParticipantEngine],
            owner: str,
            task: str,
            due_date: Optional[str] = None,
        ) -> str:
            """Record a specific task assigned to a participant (Participant Action)."""
            engine = ctx.deps
            task = await engine._ensure_english_text(task, preserve_markdown=False)
            due_date = await engine._ensure_english_text(
                due_date or "", preserve_markdown=False
            )
            if not task.strip():
                return "FAILURE: Action item task is empty."
            if engine._has_similar_host_item(
                "follow_up_needed", f"Action for {owner}", task
            ):
                return f"SKIP: Action item '{task}' is already recorded."

            event = {
                "event_type": "follow_up_needed",
                "title": f"Action for {owner}",
                "content": f"**Task**: {task}\n**Due**: {due_date or 'TBD'}",
                "confidence": 0.9,
                "priority": "high",
                "metadata": {"owner": owner, "due_date": due_date},
            }
            suggestion = engine._build_host_suggestion(event)
            if suggestion:
                suggestion.status = "pinned"
                engine._host_state.pinned_items.insert(0, suggestion)
                engine._host_state.updated_at = datetime.utcnow().isoformat()
                return f"SUCCESS: Recorded action item for {owner}: {task}"
            return "FAILURE: Action item did not meet requirements."

        @self.agent.tool
        async def add_insight(
            ctx: RunContext[AIParticipantEngine],
            title: str,
            content: str,
            insight_type: str = "general",
            confidence: float = 0.8,
        ) -> str:
            """
            Record a strategic observation, risk, or participation insight.
            Use this for 'Guardrails' like agenda drift or engagement drops.
            """
            engine = ctx.deps
            title = await engine._ensure_english_text(title, preserve_markdown=False)
            content = await engine._ensure_english_text(
                content, preserve_markdown=False
            )
            if not content.strip():
                return "FAILURE: Insight content is empty."

            # Use the specified type or default
            event_type = engine._normalize_host_event_type(insight_type) or "ai_insight"
            if engine._has_similar_host_item(event_type, title, content):
                return "SKIP: This insight was recently shared."

            event = {
                "event_type": event_type,
                "title": title,
                "content": content,
                "confidence": confidence,
                "priority": "medium",
            }
            suggestion = engine._build_host_suggestion(event)
            if suggestion:
                suggestion.status = "pinned"
                engine._host_state.pinned_items.insert(0, suggestion)
                engine._host_state.updated_at = datetime.utcnow().isoformat()
                return f"SUCCESS: Recorded insight: {title}"
            return "FAILURE: Insight did not meet confidence threshold."

    async def _get_pydantic_ai_model(self):
        """Helper to return a pydantic-ai model instance based on current config."""
        await self.load_runtime_config()
        api_key = await self._get_provider_api_key()
        if not api_key:
            return None  # Fallback to default or error

        try:
            if self.provider == "gemini":
                return GeminiModel(
                    self.model_name, provider=GoogleGLAProvider(api_key=api_key)
                )
            elif self.provider == "openai":
                return OpenAIModel(
                    self.model_name, provider=OpenAIProvider(api_key=api_key)
                )
            elif self.provider == "anthropic":
                return AnthropicModel(
                    self.model_name, provider=AnthropicProvider(api_key=api_key)
                )
            elif self.provider == "groq":
                return GroqModel(
                    self.model_name, provider=GroqProvider(api_key=api_key)
                )
            elif self.provider == "openrouter":
                return OpenAIModel(
                    self.model_name,
                    provider=OpenAIProvider(
                        api_key=api_key, base_url="https://openrouter.ai/api/v1"
                    ),
                )
        except Exception as e:
            logger.error(f"[AIParticipant] Failed to initialize pydantic-ai model: {e}")
            return None
        return None

    async def load_runtime_config(self) -> None:
        if self._runtime_config_loaded:
            return

        env_provider = os.getenv("AI_PARTICIPANT_PROVIDER")
        env_model = os.getenv("AI_PARTICIPANT_MODEL")
        if env_provider or env_model:
            self.provider = _normalize_provider_name(env_provider, self.provider)
            if env_model:
                self.model_name = _clean_env_value(
                    env_model,
                    DEFAULT_PROVIDER_MODELS.get(
                        self.provider, DEFAULT_PROVIDER_MODELS["gemini"]
                    ),
                )
            self._stats["provider"] = self.provider
            self._stats["last_model_used"] = self.model_name
            self._runtime_config_loaded = True
            return

        try:
            config = await self.db.get_model_config()
        except Exception:
            config = None

        provider = _normalize_provider_name(
            (config or {}).get("provider"), self.provider
        )
        # OpenRouter should only be used for AI insights when explicitly enabled
        # via env. Otherwise stay on native providers/models.
        if provider == "openrouter":
            provider = self.provider
        if provider not in DEFAULT_PROVIDER_MODELS:
            provider = self.provider

        model_name = _clean_env_value(
            (config or {}).get("model"),
            DEFAULT_PROVIDER_MODELS.get(provider, DEFAULT_PROVIDER_MODELS["gemini"]),
        )

        self.provider = provider
        self.model_name = model_name
        self._stats["provider"] = self.provider
        self._stats["last_model_used"] = self.model_name
        self._runtime_config_loaded = True

    async def load_host_state(self, session_id: str) -> bool:
        """Restore meeting host state from database metadata."""
        try:
            session = await self.db.get_recording_session(session_id)
            if not session or not session.get("metadata"):
                return False

            metadata = session["metadata"]
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    return False

            if not isinstance(metadata, dict):
                return False

            state_data = metadata.get("ai_host_state")
            if not state_data:
                return False

            # Hydrate MeetingHostState
            if isinstance(state_data, str):
                try:
                    state_data = json.loads(state_data)
                except Exception:
                    return False

            if not isinstance(state_data, dict):
                return False

            # Safety: Ensure meeting_id matches to prevent cross-session leaks if IDs were recycled
            self._host_state = MeetingHostState.model_validate(state_data)
            self._host_state.meeting_id = self.meeting_context.meeting_id

            logger.info(
                "[AIParticipant] State restored for session=%s summary_len=%s pinned=%s suggestions=%s",
                session_id,
                len(self._host_state.meeting_summary or ""),
                len(self._host_state.pinned_items),
                len(self._host_state.suggested_items),
            )
            return True
        except Exception as e:
            logger.error("[AIParticipant] Failed to load host state: %s", e)
            return False

    async def ingest_transcript(
        self,
        text: str,
        transcript_time_seconds: Optional[float] = None,
    ) -> Optional[GuardrailAlert]:
        """Backward-compatible guardrail path."""
        if not self.enabled:
            return None

        now_ts = time.time()
        ts = (
            float(transcript_time_seconds)
            if transcript_time_seconds is not None
            else now_ts
        )
        self.buffer.add(ts, text)

        if (
            self.min_chars_before_analysis > 0
            and self.buffer.get_char_count() < self.min_chars_before_analysis
        ):
            self._stats["analysis_skipped_small_window"] += 1
            return None

        if now_ts - self._last_analysis_at < self.analysis_interval_seconds:
            self._stats["analysis_skipped_interval"] += 1
            return None

        async with self._lock:
            now_ts = time.time()
            if now_ts - self._last_analysis_at < self.analysis_interval_seconds:
                self._stats["analysis_skipped_interval"] += 1
                return None

            self._last_analysis_at = now_ts
            self._stats["analysis_attempts"] += 1
            self._stats["last_analysis_at"] = datetime.utcnow().isoformat()
            assessment = await self._reason_with_llm()
            if not assessment:
                self._stats["assessment_none"] += 1
                return None

            self._stats["last_assessment_intervention_required"] = bool(
                assessment.intervention_required
            )
            self._stats["last_assessment_reason"] = (
                assessment.reason.value if assessment.reason else None
            )
            self._stats["last_assessment_confidence"] = float(
                assessment.confidence or 0.0
            )

            alert = self.evaluator.evaluate(
                assessment=assessment,
                window_duration_seconds=self.buffer.get_duration_seconds(),
                now_ts=now_ts,
            )
            if alert:
                self._last_alert_summary = f"{alert.reason.value}: {alert.insight}"
            return alert

    async def ingest_transcript_host(
        self,
        text: str,
        transcript_time_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Active host path: suggestions + interventions + state delta."""
        payload: Dict[str, Any] = {
            "suggestions": [],
            "interventions": [],
            "state_delta": {},
            "policy_source": self._host_policy_source,
        }
        if not self.enabled:
            return payload

        now_ts = time.time()
        ts = (
            float(transcript_time_seconds)
            if transcript_time_seconds is not None
            else now_ts
        )
        self.buffer.add(ts, text)

        if (
            self.min_chars_before_analysis > 0
            and self.buffer.get_char_count() < self.min_chars_before_analysis
        ):
            self._stats["analysis_skipped_small_window"] += 1
            return payload

        # ── Event-driven trigger (replaces fixed-interval polling) ───────────
        should_attempt, trigger_reason = self._evaluate_analysis_trigger(now_ts)
        if not should_attempt:
            self._stats["analysis_skipped_interval"] += 1
            return payload

        async with self._lock:
            now_ts = time.time()
            # Re-check under lock to avoid double-running back-to-back triggers
            should_attempt, trigger_reason = self._evaluate_analysis_trigger(now_ts)
            if not should_attempt:
                self._stats["analysis_skipped_interval"] += 1
                return payload

            self._last_analysis_at = now_ts
            self._stats[f"trigger_{trigger_reason}"] = (
                int(self._stats.get(f"trigger_{trigger_reason}") or 0) + 1
            )
            self._stats["analysis_attempts"] += 1
            self._stats["last_analysis_at"] = datetime.utcnow().isoformat()
            self._stats["last_trigger_reason"] = trigger_reason

            # Behavior Gate: check if BehaviorSpec allows analysis this cycle
            transcript_text = self.buffer.get_text()
            should_analyze, gate_reason = self._should_analyze_this_cycle(
                transcript_text
            )
            if not should_analyze:
                self._stats["analysis_skipped_behavior_gate"] = (
                    int(self._stats.get("analysis_skipped_behavior_gate") or 0) + 1
                )
                self._stats["last_gate_skip_reason"] = gate_reason
                logger.info(
                    "[AIParticipant] Behavior gate BLOCKED: reason=%s window_chars=%d",
                    gate_reason,
                    len(transcript_text),
                )
                # Even in silence mode, run summary update via fallback
                if gate_reason == "silence_mode":
                    self._build_fallback_host_events(
                        transcript_window=transcript_text,
                        reason="silence_mode_summary_only",
                    )
                return payload

            # ── Fast probe gate ────────────────────────────────────────────
            # The probe exists to filter out noise on weak triggers (pause,
            # ceiling). Skip it for triggers we already trust:
            #   - "cue_word": regex already matched a decision/unresolved cue,
            #     running another LLM gate just adds latency + false negatives.
            #   - "heartbeat": exists specifically to refresh the rolling
            #     summary during quiet stretches; never gate that.
            run_full_agent = True
            probe_bypass_reasons = {"cue_word", "heartbeat"}
            if (
                trigger_reason not in probe_bypass_reasons
                and self.fast_probe_enabled
            ):
                interesting = await self._fast_probe_interesting(transcript_text)
                if not interesting:
                    run_full_agent = False
                    logger.info(
                        "[AIParticipant] Fast probe → not interesting "
                        "(trigger=%s, model=%s); skipping full agent",
                        trigger_reason,
                        self.fast_probe_model,
                    )

            self._temp_suggestions = []
            if run_full_agent:
                await self._reason_host_events()
                await self._supplement_host_events_from_heuristics(
                    self.buffer.get_text()
                )
                if trigger_reason == "heartbeat":
                    self._last_heartbeat_at = now_ts
            else:
                # Probe said no — still update the rolling summary cheaply via
                # the heuristic fallback so the side-panel summary doesn't
                # stagnate, but skip the expensive agent + tool calls.
                self._build_fallback_host_events(
                    transcript_window=transcript_text,
                    reason="fast_probe_skipped",
                )
                # Mark heartbeat consumed so we don't immediately re-fire.
                self._last_heartbeat_at = now_ts

            # Behavior Post-Filter: enforce behavior constraints on output
            if self._temp_suggestions and self._behavior_spec.output_categories:
                self._temp_suggestions = self._filter_by_behavior(
                    self._temp_suggestions
                )

            if (
                not self._temp_suggestions
                and not (self._host_state.meeting_summary or "").strip()
            ):
                # If agent did nothing, try heuristic fallback
                self._build_fallback_host_events(
                    transcript_window=self.buffer.get_text(),
                    reason="agent_noop",
                )

            for suggestion in self._temp_suggestions:
                # Deduplicate again just in case tools missed it or for race conditions
                if any(s.id == suggestion.id for s in self._host_state.suggested_items):
                    continue

                self._host_state.suggested_items.insert(0, suggestion)
                self._host_state.suggested_items = self._host_state.suggested_items[
                    : self._host_policy.max_suggestions_buffer
                ]
                self._host_state.counters["suggested"] = (
                    int(self._host_state.counters.get("suggested") or 0) + 1
                )
                self._host_state.updated_at = datetime.utcnow().isoformat()
                self._stats["host_suggestions_emitted"] += 1
                payload["suggestions"].append(suggestion.model_dump())

                card = self._build_intervention_from_suggestion(suggestion, now_ts)
                if card is not None:
                    self._host_state.intervention_history.insert(0, card)
                    self._host_state.intervention_history = (
                        self._host_state.intervention_history[
                            : self._host_policy.max_intervention_history
                        ]
                    )
                    self._host_state.counters["intervened"] = (
                        int(self._host_state.counters.get("intervened") or 0) + 1
                    )
                    self._host_state.updated_at = datetime.utcnow().isoformat()
                    self._stats["host_interventions_emitted"] += 1
                    payload["interventions"].append(card.model_dump())

            payload["state_delta"] = self.get_host_state_snapshot()
            return payload

    async def _reason_with_llm(self) -> Optional[GuardrailLLMOutput]:
        await self.load_runtime_config()
        api_key = await self._get_provider_api_key()
        if not api_key:
            return None

        transcript_window = self.buffer.get_text()
        if not transcript_window:
            return None

        prompt = self._build_prompt(transcript_window)
        raw_text, used_model = await self._call_llm_json(prompt)
        if raw_text is None:
            return None

        try:
            self._stats["last_model_used"] = used_model
            parsed = self._extract_json(raw_text)
            if not parsed:
                self._stats["parse_failures"] += 1
                return None
            normalized = self._normalize_model_payload(parsed)
            if not normalized:
                self._stats["normalize_silent_fallbacks"] += 1
                return None
            return GuardrailLLMOutput.model_validate(normalized)
        except Exception:
            self._stats["llm_failures"] += 1
            return None

    async def _reason_host_events(self) -> List[Dict[str, Any]]:
        model = await self._get_pydantic_ai_model()
        if not model:
            logger.warning(
                "[AIParticipant] Missing provider API key or model for provider=%s; using heuristic fallback",
                self.provider,
            )
            return self._build_fallback_host_events(
                transcript_window=self.buffer.get_text(),
                reason="missing_api_key_or_model",
            )

        transcript_window = self.buffer.get_text()
        if not transcript_window:
            return []

        try:
            self._stats["llm_calls"] += 1
            # Run the agentic observer
            # It will call tools like add_decision and update_summary
            await self.agent.run(
                self._build_host_prompt(transcript_window), deps=self, model=model
            )
            logger.info("[AIParticipant] Agent run complete.")
            # The tools have updated self._temp_suggestions, we don't need to return list of dicts anymore
            # but for internal consistency we return it.
            return [{"collected": True}] if self._temp_suggestions else []
        except Exception as e:
            self._stats["llm_failures"] += 1
            logger.error(
                f"[AIParticipant] Agent failed during host reasoning: {e}",
                exc_info=True,
            )
            return self._build_fallback_host_events(
                transcript_window=transcript_window,
                reason="agent_error",
            )

    async def _call_llm_json(self, prompt: str) -> Tuple[Optional[str], str]:
        model_candidates: List[str] = []
        for model in [self.model_name, *self.fallback_models]:
            if model and model not in model_candidates:
                model_candidates.append(model)

        used_model = self.model_name
        for idx, model in enumerate(model_candidates):
            try:
                self._stats["llm_calls"] += 1
                used_model = model
                raw_text = await asyncio.wait_for(
                    self._generate_llm_text(model=model, prompt=prompt),
                    timeout=self.llm_timeout_seconds,
                )
                if idx > 0:
                    self._stats["model_fallbacks"] += 1
                return raw_text, used_model
            except (asyncio.TimeoutError, TimeoutError):
                self._stats["llm_failures"] += 1
                self._stats["llm_timeouts"] += 1
                if idx == len(model_candidates) - 1:
                    return None, used_model
            except Exception:
                self._stats["llm_failures"] += 1
                if idx == len(model_candidates) - 1:
                    return None, used_model

        return None, used_model

    async def _generate_llm_text(self, model: str, prompt: str) -> str:
        await self.load_runtime_config()
        api_key = await self._get_provider_api_key()
        if not api_key:
            raise ValueError(f"{self.provider} API key not found")

        if self.provider == "gemini":
            return await generate_content_text_async(
                api_key=api_key,
                model=model,
                contents=prompt,
                config={"temperature": 0.1},
            )

        if self.provider == "openai":
            client = AsyncOpenAI(api_key=api_key)
            response = await client.chat.completions.create(
                model=model,
                temperature=0.1,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "Return strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
            )
            return response.choices[0].message.content or ""

        if self.provider == "anthropic":
            client = AsyncAnthropic(api_key=api_key)
            response = await client.messages.create(
                model=model,
                max_tokens=1200,
                temperature=0.1,
                system="Return strict JSON only.",
                messages=[{"role": "user", "content": prompt}],
            )
            parts: List[str] = []
            for block in getattr(response, "content", []) or []:
                text = getattr(block, "text", None)
                if text:
                    parts.append(text)
            return "".join(parts)

        if self.provider == "openrouter":
            client = AsyncOpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
                default_headers={
                    "HTTP-Referer": "https://meet.quexio.com",
                    "X-Title": "Pnyx AI Participant",
                },
            )
            response = await client.chat.completions.create(
                model=model,
                temperature=0.1,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "Return strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
            )
            return response.choices[0].message.content or ""

        raise ValueError(f"Unsupported AI participant provider: {self.provider}")

    async def _get_provider_api_key(self) -> Optional[str]:
        if self._provider_api_key:
            return self._provider_api_key

        key = ""
        if self.provider == "gemini":
            key = _clean_env_value(
                os.getenv("GEMINI_API_KEY", ""), ""
            ) or _clean_env_value(os.getenv("GOOGLE_API_KEY", ""), "")
            if not key:
                key = (
                    await self.db.get_api_key("gemini", user_email=self.user_email)
                ) or ""
        elif self.provider == "openai":
            key = _clean_env_value(os.getenv("OPENAI_API_KEY", ""), "")
            if not key:
                key = (
                    await self.db.get_api_key("openai", user_email=self.user_email)
                ) or ""
        elif self.provider == "anthropic":
            key = _clean_env_value(os.getenv("ANTHROPIC_API_KEY", ""), "")
            if not key:
                key = (
                    await self.db.get_api_key("claude", user_email=self.user_email)
                ) or ""
        elif self.provider == "openrouter":
            key = _clean_env_value(os.getenv("OPENROUTER_API_KEY", ""), "")
            if not key:
                key = (
                    await self.db.get_user_api_key(self.user_email, "openrouter")
                ) or ""

        key = key.strip()
        if not key and not self._missing_key_logged:
            logger.info(
                "[AIParticipant] %s API key not found for %s",
                self.provider,
                self.user_email,
            )
            self._missing_key_logged = True

        self._provider_api_key = key or None
        return self._provider_api_key

    async def _get_gemini_api_key(self) -> Optional[str]:
        # Backward-compat shim for older call sites and hot-reloaded containers.
        return await self._get_provider_api_key()

    def _build_prompt(self, transcript_window: str) -> str:
        title = self.meeting_context.title or ""
        goal = self.meeting_context.goal or ""
        description = self.meeting_context.description or ""
        agenda_text = self.meeting_context.agenda_text or ""
        participant_names = self.meeting_context.participant_names or []
        participant_line = (
            ", ".join(participant_names[:25]) if participant_names else "None"
        )
        current_summary = self._host_state.meeting_summary or "None"

        return f"""
You are a silent meeting observer. Stay silent unless a guardrail condition is detected.

Meeting Context:
- Title: {title}
- Goal: {goal}
- Description: {description}
- Agenda: {agenda_text}
- Participants: {participant_line}
- Rolling meeting summary so far:
{current_summary}
- Previous alert summary: {self._last_alert_summary}

Guardrail reasons:
- agenda_deviation
- no_decision
- unresolved_question
- missing_context_or_repeat

Rules:
- **Hinglish/Multilingual Awareness**: The transcript often contains Hinglish (Hindi + English) code-switching. Extract decisions, action items, and insights even if they are expressed in mixed languages.
- If any actionable commitment, decision, or important unresolved question is present in ANY language, set `intervention_required: true`. Do not dismiss mixed-language discussion as noise.
- Insight must be one professional, actionable sentence in English, even if the transcript includes Hindi or mixed-language discussion.
- Reason must be one of: agenda_deviation, no_decision, unresolved_question, missing_context_or_repeat.
- Return JSON only. No markdown.

Recent transcript window:
{transcript_window}
""".strip()

    def _build_host_prompt(self, transcript_window: str) -> str:
        title = self.meeting_context.title or ""
        goal = self.meeting_context.goal or ""
        description = self.meeting_context.description or ""
        agenda_text = self.meeting_context.agenda_text or ""
        participant_names = self.meeting_context.participant_names or []
        participant_line = (
            ", ".join(participant_names[:25]) if participant_names else "None"
        )

        policy = self._host_policy
        role = policy.role_mode.value
        skill_definition = self._active_skill_definition or {}
        skill_name = str(skill_definition.get("name") or role.title())
        skill_description = (
            str(skill_definition.get("description") or "").strip() or "None"
        )
        skill_role = str(skill_definition.get("role") or "").strip() or "None"
        skill_goals = skill_definition.get("goals") or []
        skill_rules = skill_definition.get("rules") or []
        allowed_custom_types = skill_definition.get("allowed_custom_event_types") or []

        # Include both title AND a content snippet so the LLM has enough context
        # to recognize when new transcript content restates an already-pinned
        # decision (rewording is the most common false-positive source).
        if self._host_state.pinned_items:
            pinned_block_lines: List[str] = []
            for item in self._host_state.pinned_items[:15]:
                snippet = " ".join(str(item.content or "").split())
                if len(snippet) > 200:
                    snippet = snippet[:197].rstrip() + "..."
                pinned_block_lines.append(
                    f"- [{item.event_type}] {item.title}: {snippet}"
                )
            pinned_line = "\n" + "\n".join(pinned_block_lines)
        else:
            pinned_line = "None"

        # Also surface recently-suggested (not yet pinned) items so the LLM
        # doesn't re-emit the same suggestion across consecutive cycles.
        if self._host_state.suggested_items:
            recent_block_lines: List[str] = []
            for item in self._host_state.suggested_items[:10]:
                snippet = " ".join(str(item.content or "").split())
                if len(snippet) > 160:
                    snippet = snippet[:157].rstrip() + "..."
                recent_block_lines.append(
                    f"- [{item.event_type}] {item.title}: {snippet}"
                )
            recent_suggestions_line = "\n" + "\n".join(recent_block_lines)
        else:
            recent_suggestions_line = "None"
        goals_block = (
            "\n".join(f"- {goal_item}" for goal_item in skill_goals)
            if skill_goals
            else "- None"
        )
        rules_block = (
            "\n".join(f"- {rule_item}" for rule_item in skill_rules)
            if skill_rules
            else "- None"
        )
        custom_types_block = (
            "\n".join(f"- {event_type}" for event_type in allowed_custom_types)
            if allowed_custom_types
            else "- None"
        )

        return f"""
You are an active AI Participant in this meeting. Generate event suggestions conservatively, based only on transcript and meeting context.

**Hinglish/Multilingual Awareness**: The transcript often contains Hinglish (Hindi + English) code-switching. Extract decisions, action items, and insights even if they are expressed in mixed languages. Do NOT dismiss mixed-language discussion as noise.

Meeting Context:
- Title: {title}
- Goal: {goal}
- Description: {description}
- Agenda: {agenda_text}
- Participants: {participant_line}
- Role Mode: {role}
- Rolling meeting summary so far:
{self._host_state.meeting_summary or "None"}
- Already Pinned Decisions/Topics (DO NOT re-suggest these, even reworded): {pinned_line}
- Recently Suggested (still in side panel — DO NOT re-emit): {recent_suggestions_line}
- Skill Name: {skill_name}
- Skill Description: {skill_description}

Skill Role:
{skill_role}

Skill Goals:
{goals_block}

Skill Rules:
{rules_block}

Reserved core event_type values:
- decision_candidate
- open_discussion

Allowed custom event_type values from the active skill:
{custom_types_block}

Rules:
- You are an active observer. DO NOT return a JSON object. Instead, use the provided tools to share insights and update the summary.
- **Hinglish/Multilingual Awareness**: The transcript often contains Hinglish (Hindi + English) code-switching. Extract decisions, action items, and insights even if they are expressed in mixed languages.
- Everything you send through tools must be in English only. Translate bits of Hindi or mixed-language discussion into clear, professional English before calling a tool.
- Include key decisions, unresolved discussions, risks, and concrete next steps when present. Do not skip a decision just because the wording is informal or multi-lingual.
- Treat the rolling meeting summary above as cumulative context from earlier parts of the meeting. Update it incrementally using the latest transcript window.
- Preserve still-relevant earlier decisions and open discussions unless the newest transcript clearly changes them.
- If no action is needed, simply finish your turn without calling any tools.
- Do NOT suggest events for topics or decisions that are already in the "Already Pinned Decisions/Topics" or "Recently Suggested" lists. This includes the same idea reworded, paraphrased, translated, or expressed at a different level of detail. If the new transcript only restates, confirms, or elaborates an already-pinned decision, do nothing — only emit a NEW item if the meeting has reached a clearly NEW decision/topic the lists do not yet cover.
- Call `add_decision` whenever participants make an explicit commitment, a clearly agreed choice, or a final resolution. Do not skip a real decision because the wording is informal.
- If a direction is "unclear", "conflicted", or "unresolved", DO NOT use `add_decision`. Instead, use `add_action_item` to record it as a follow-up or `add_discussion` to mark it as unresolved.
- Use `add_action_item` for specific tasks, unowned follow-ups, or resolving unclear points. These will be shown directly in the UI.
- Use `add_discussion` for unresolved questions/debates that need more airtime.
- Use `add_insight` for meta-observations (risks, participation, drift), especially if the discussion is deviating from the agenda or stalling without resolution.
- Do NOT call a tool twice if the content hasn't changed.

Recent transcript window:
{transcript_window}
""".strip()

    def _build_host_suggestion(self, event: Dict[str, Any]) -> Optional[HostSuggestion]:
        event_type = event.get("event_type")
        if not isinstance(event_type, str) or not event_type.strip():
            return None

        confidence = float(event.get("confidence") or 0.0)
        threshold = float(
            self._host_policy.event_threshold_overrides.get(event_type)
            or self._host_policy.min_confidence
        )
        if confidence < threshold:
            return None

        title = " ".join(str(event.get("title") or "").split()).strip()
        content = " ".join(str(event.get("content") or "").split()).strip()
        if not content:
            return None
        if self._should_suppress_meta_open_discussion(event_type, title, content):
            return None

        return HostSuggestion(
            id=str(uuid.uuid4()),
            event_type=event_type,
            title=title or event_type.replace("_", " ").title(),
            content=content,
            confidence=round(confidence, 2),
            timestamp=datetime.utcnow().isoformat(),
            source_excerpt=event.get("source_excerpt"),
            metadata={"priority": event.get("priority", "medium")},
        )

    @staticmethod
    def _normalize_compare_text(value: str) -> str:
        return " ".join(str(value or "").strip().lower().split())

    def _get_content_hash(self, event_type: str, content: str) -> str:
        """Create a stable hash for a suggestion's type and content."""
        normalized = self._normalize_compare_text(content)
        return f"{event_type}:{normalized}"

    # English stopwords stripped before token-overlap dedup; they swamp Jaccard
    # scores and make any two short sentences look "similar."
    _DEDUP_STOPWORDS = frozenset({
        "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "for",
        "with", "by", "at", "from", "as", "is", "are", "was", "were", "be", "been",
        "being", "this", "that", "these", "those", "it", "its", "we", "our", "us",
        "they", "them", "their", "i", "me", "my", "you", "your", "he", "she", "his",
        "her", "will", "would", "should", "could", "can", "may", "might", "do",
        "does", "did", "have", "has", "had", "not", "no", "yes", "so", "than",
        "then", "there", "here", "about", "into", "onto", "over", "out", "up",
        "down", "also", "very", "more", "most", "some", "any", "all", "such",
    })

    @classmethod
    def _significant_tokens(cls, text: str) -> set:
        normalized = cls._normalize_compare_text(text)
        return {
            tok for tok in normalized.split()
            if len(tok) > 2 and tok not in cls._DEDUP_STOPWORDS
        }

    @classmethod
    def _token_overlap(cls, a: str, b: str) -> float:
        tokens_a = cls._significant_tokens(a)
        tokens_b = cls._significant_tokens(b)
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = len(tokens_a & tokens_b)
        # Use min-length denominator (Szymkiewicz–Simpson) so a short new
        # suggestion that's fully covered by a long existing one still scores
        # as duplicate, even if the longer one has extra detail.
        denom = min(len(tokens_a), len(tokens_b))
        return intersection / denom if denom else 0.0

    def _has_similar_host_item(self, event_type: str, title: str, content: str) -> bool:
        candidate_title = self._normalize_compare_text(title)
        candidate_content = self._normalize_compare_text(content)
        if not candidate_content:
            return False

        # Check existing live items
        for item in (
            self._host_state.pinned_items
            + self._host_state.suggested_items
            + self._temp_suggestions
        ):
            if str(item.event_type or "") != str(event_type or ""):
                continue
            existing_title = self._normalize_compare_text(item.title)
            existing_content = self._normalize_compare_text(item.content)
            if candidate_content == existing_content:
                return True
            if candidate_title and candidate_title == existing_title:
                return True
            if existing_content and (
                candidate_content in existing_content
                or existing_content in candidate_content
            ):
                return True
            # Fuzzy: catch reworded / paraphrased / translated restatements.
            # Title similarity gets a lower bar (titles are short and often
            # share most significant tokens when they refer to the same idea).
            if self._token_overlap(content, item.content or "") >= 0.7:
                return True
            if candidate_title and existing_title and self._token_overlap(
                title, item.title or ""
            ) >= 0.6:
                return True

        # Check historical handled items (dismissed or pinned in the past)
        current_hash = self._get_content_hash(event_type, content)
        if current_hash in self._host_state.handled_content_hashes:
            return True

        return False

    def _build_intervention_from_suggestion(
        self,
        suggestion: HostSuggestion,
        now_ts: float,
    ) -> Optional[HostInterventionCard]:
        event_key = suggestion.event_type
        cooldown_seconds = int(self._host_policy.intervention_cooldown_seconds)
        last_ts = float(self._host_event_last_published_at.get(event_key) or 0.0)
        if (now_ts - last_ts) < cooldown_seconds:
            return None

        signature = self._suggestion_signature(suggestion)
        if self._host_event_last_signature.get(event_key) == signature:
            return None

        if not self._should_intervene(suggestion):
            return None

        self._host_event_last_published_at[event_key] = now_ts
        self._host_event_last_signature[event_key] = signature
        return HostInterventionCard(
            id=str(uuid.uuid4()),
            event_type=suggestion.event_type,
            headline=suggestion.title,
            body=suggestion.content,
            priority=str(suggestion.metadata.get("priority") or "medium"),
            confidence=suggestion.confidence,
            timestamp=datetime.utcnow().isoformat(),
            linked_suggestion_id=suggestion.id,
        )

    def _should_intervene(self, suggestion: HostSuggestion) -> bool:
        role = self._host_policy.role_mode
        confidence = float(suggestion.confidence)
        event_type = suggestion.event_type
        priority = str((suggestion.metadata or {}).get("priority") or "medium").lower()

        if role == HostRoleMode.ADVISOR:
            return event_type in CORE_EVENT_TYPES or priority == "high"

        if role == HostRoleMode.FACILITATOR:
            return confidence >= max(0.72, self._host_policy.min_confidence)

        return event_type in CORE_EVENT_TYPES or confidence >= max(
            0.65, self._host_policy.min_confidence - 0.03
        )

    @staticmethod
    def _suggestion_signature(suggestion: HostSuggestion) -> str:
        text = " ".join((suggestion.content or "").strip().lower().split())
        return f"{suggestion.event_type}:{text}"

    def _normalize_host_event_type(self, event_type_value: Any) -> Optional[str]:
        raw = str(event_type_value or "").strip().lower()
        if not raw:
            return None

        aliases = {
            "open_question": "open_discussion",
            "discussion_open": "open_discussion",
            "decision": "decision_candidate",
        }
        normalized = aliases.get(raw, raw)
        normalized = normalized.replace("-", "_").replace(" ", "_")
        normalized = re.sub(r"[^a-z0-9_]", "", normalized)
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        if not normalized:
            return None

        allowed_custom = set(
            self._active_skill_definition.get("allowed_custom_event_types") or []
        )
        if normalized in CORE_EVENT_TYPES or normalized in allowed_custom:
            return normalized
        return None

    @staticmethod
    def _should_suppress_meta_open_discussion(
        event_type: str, title: str, content: str
    ) -> bool:
        if event_type != "open_discussion":
            return False
        text = " ".join(f"{title} {content}".lower().split())
        blocked_markers = [
            "guard rail",
            "guardrail",
            "topic drift",
            "agenda misalignment",
            "does not clearly connect to the stated",
            "does not clearly connect to the agenda",
            "trigger discussion surfaced",
        ]
        return any(marker in text for marker in blocked_markers)

    def _build_fallback_host_events(
        self,
        transcript_window: str,
        reason: str,
    ) -> List[Dict[str, Any]]:
        summary_text = self._fallback_meeting_summary(transcript_window)
        if summary_text:
            self._host_state.meeting_summary = summary_text

        events = self._fallback_core_events(transcript_window)
        self._refresh_host_state_from_events(events)
        self._host_state.updated_at = datetime.utcnow().isoformat()
        self._stats["last_fallback_reason"] = reason
        logger.info(
            "[AIParticipant] Heuristic fallback applied reason=%s summary=%s events=%s",
            reason,
            bool(summary_text),
            len(events),
        )
        return events

    async def _ensure_english_text(
        self,
        text: str,
        preserve_markdown: bool = False,
    ) -> str:
        # BYPASS: Trust the primary LLM to handle language. Forced translation hangs Hinglish meetings.
        return str(text or "").strip()

    def _extract_candidate_host_events(
        self, transcript_window: str
    ) -> List[Dict[str, Any]]:
        lines = [
            " ".join(re.sub(r"^\[\d{2}:\d{2}\]\s*", "", line).split()).strip()
            for line in str(transcript_window or "").splitlines()
            if " ".join(re.sub(r"^\[\d{2}:\d{2}\]\s*", "", line).split()).strip()
        ]
        if not lines:
            return []

        events: List[Dict[str, Any]] = []
        for line in lines[-8:]:
            snippet = line.strip(" .,:;")
            if not snippet:
                continue
            if DECISION_CUE_RE.search(snippet) and not DECISION_META_RE.search(snippet):
                quote = snippet[:220]
                events.append(
                    {
                        "event_type": "decision_candidate",
                        "title": "Possible decision (auto-detected)",
                        "content": f"“{quote}”",
                        "confidence": 0.65,
                        "priority": "medium",
                        "source_excerpt": quote,
                    }
                )
                continue
            if UNRESOLVED_CUE_RE.search(snippet):
                quote = snippet[:220]
                events.append(
                    {
                        "event_type": "open_discussion",
                        "title": "Open discussion (auto-detected)",
                        "content": f"“{quote}”",
                        "confidence": 0.6,
                        "priority": "medium",
                        "source_excerpt": quote,
                    }
                )
        return events

    async def _supplement_host_events_from_heuristics(
        self,
        transcript_window: str,
    ) -> None:
        # Don't let regex heuristics overwrite AI-summarized output for an event
        # type the LLM already covered this cycle — the heuristic only stores raw
        # transcript quotes, which is exactly what we want to avoid in the UI.
        llm_event_types = {s.event_type for s in self._temp_suggestions}
        for event in self._extract_candidate_host_events(transcript_window):
            if event.get("event_type") in llm_event_types:
                continue
            suggestion = self._build_host_suggestion(event)
            if not suggestion:
                continue
            suggestion.title = await self._ensure_english_text(
                suggestion.title,
                preserve_markdown=False,
            )
            suggestion.content = await self._ensure_english_text(
                suggestion.content,
                preserve_markdown=False,
            )
            if self._has_similar_host_item(
                suggestion.event_type,
                suggestion.title,
                suggestion.content,
            ):
                continue
            self._temp_suggestions.append(suggestion)

    def _fallback_meeting_summary(self, transcript_window: str) -> str:
        lines = [
            " ".join(line.split()).strip()
            for line in str(transcript_window or "").splitlines()
            if " ".join(line.split()).strip()
        ]
        if not lines:
            return ""

        trimmed_lines = lines[-6:]
        stripped_segments: List[str] = []
        for line in trimmed_lines:
            clean = re.sub(r"^\[\d{2}:\d{2}\]\s*", "", line).strip()
            if clean:
                stripped_segments.append(clean.rstrip("."))

        if not stripped_segments:
            return ""

        bullets = [f"- {segment}." for segment in stripped_segments[:4]]
        return "### Discussion Snapshot\n" + "\n".join(bullets)

    def _fallback_core_events(self, transcript_window: str) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for event in self._extract_candidate_host_events(transcript_window):
            key = (
                str(event.get("event_type") or ""),
                self._normalize_compare_text(str(event.get("content") or "")),
            )
            if not key[1] or key in seen:
                continue
            seen.add(key)
            deduped.append(event)
        return deduped[:4]

    def _evaluate_analysis_trigger(self, now_ts: float) -> Tuple[bool, str]:
        """
        Event-driven trigger: should we attempt analysis right now?

        Returns (should_attempt, reason). Cadence floor is enforced via
        _last_analysis_at (bumped on every attempt, even probe-only) so the
        floor governs total cost regardless of probe outcome.

        Trigger priority (semantic strength first — strongest signal wins so
        downstream code can decide whether to bypass the fast-probe gate):
          - "cue_word": decision/unresolved cue matches in last 30s of transcript
          - "pause": gap between last two transcript items ≥ pause_trigger_seconds
          - "heartbeat": ≥ analysis_interval since last attempt (summary refresh)
          - "ceiling": ≥ 2 × cadence_ceiling since last attempt (force-probe)
          - "no_trigger" / "below_cadence_ceiling": skip
        """
        seconds_since_attempt = now_ts - self._last_analysis_at
        # Cadence floor — never run more often than this
        if seconds_since_attempt < self.analysis_cadence_ceiling_seconds:
            return False, "below_cadence_ceiling"

        # Cue word detection in recent slice — strongest semantic signal, so
        # check it FIRST. A pause that happens to coincide with a cue-bearing
        # transcript shouldn't get demoted to "pause" and risk a false NO from
        # the probe.
        recent = self.buffer.get_recent_text(seconds=30.0)
        if recent and (
            DECISION_CUE_RE.search(recent) or UNRESOLVED_CUE_RE.search(recent)
        ):
            return True, "cue_word"

        # Pause detection — weaker signal (could be filler / dead air)
        if self.buffer.get_last_gap_seconds() >= self.pause_trigger_seconds:
            return True, "pause"

        # Heartbeat — periodic refresh of the rolling summary
        if (now_ts - self._last_heartbeat_at) >= self.analysis_interval_seconds:
            return True, "heartbeat"

        # Cadence ceiling reached — probe even without other signals
        if seconds_since_attempt >= self.analysis_cadence_ceiling_seconds * 2:
            return True, "ceiling"

        return False, "no_trigger"

    async def _fast_probe_interesting(self, transcript_window: str) -> bool:
        """
        Cheap LLM call (Gemini Flash) gating the expensive agent.

        Returns True if the recent transcript contains anything worth a deeper
        look (decision, action, unresolved question, agenda drift, risk, etc.).
        Fails open: on error/timeout returns True so we never silently drop work.
        """
        if not self.fast_probe_enabled:
            return True
        if not transcript_window or not transcript_window.strip():
            return False

        try:
            from .gemini_client import generate_content_text_async
        except ImportError:
            return True

        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            try:
                api_key = await self.db.get_api_key(
                    "gemini", user_email=self.user_email
                )
            except Exception:
                api_key = None
        if not api_key:
            # No Gemini key available — bypass the probe rather than blocking.
            return True

        # Only consider the most recent slice — older content was already probed
        # in earlier cycles. Cap to keep latency and cost low.
        recent_slice = self.buffer.get_recent_text(seconds=45.0) or transcript_window
        recent_slice = recent_slice[-2500:]

        prompt = (
            "You are a permissive relevance gate for a meeting AI. Decide if "
            "the recent transcript contains ANY content worth deeper analysis.\n"
            "\n"
            "**MULTILINGUAL**: Transcript is often Hindi / Hinglish / mixed. "
            "Treat words like 'decide', 'करेंगे', 'जाएंगे', 'फाइनल', 'तय', "
            "'सहमत', 'agreed', 'will', 'next step', 'deploy', 'ship', 'block', "
            "'issue', 'risk', 'todo', 'action', 'follow up' as strong signals.\n"
            "\n"
            "Answer YES if ANY of these are present (even informally / partially):\n"
            "  - A decision, commitment, plan, or 'we will' statement\n"
            "  - An action item, owner, or due-date mention\n"
            "  - An open question, blocker, risk, or concern\n"
            "  - Off-agenda drift or the team getting stuck\n"
            "  - A concrete fact, metric, name, or date worth recording\n"
            "  - Substantive discussion of project/product/process work\n"
            "\n"
            "Answer NO ONLY if the transcript is purely greetings, jokes, "
            "personal chat, dead air, or unintelligible filler.\n"
            "\n"
            "**When in doubt, answer YES.** False positives are cheap; missing "
            "a real decision is expensive.\n"
            "\n"
            "Respond with EXACTLY one token: YES or NO. Nothing else.\n"
            "\n"
            "Transcript:\n"
            f"{recent_slice}"
        )

        self._stats["fast_probe_calls"] = (
            int(self._stats.get("fast_probe_calls") or 0) + 1
        )
        try:
            raw = await asyncio.wait_for(
                generate_content_text_async(
                    api_key=api_key,
                    model=self.fast_probe_model,
                    contents=prompt,
                    config={"temperature": 0.0, "max_output_tokens": 4},
                ),
                timeout=self.fast_probe_timeout_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError):
            self._stats["fast_probe_failures"] = (
                int(self._stats.get("fast_probe_failures") or 0) + 1
            )
            return True
        except Exception as exc:
            self._stats["fast_probe_failures"] = (
                int(self._stats.get("fast_probe_failures") or 0) + 1
            )
            logger.warning(
                "[AIParticipant] Fast probe error (%s); failing open: %s",
                self.fast_probe_model,
                exc,
            )
            return True

        verdict = (raw or "").strip().upper()
        interesting = verdict.startswith("YES")
        if interesting:
            self._stats["fast_probe_interesting"] = (
                int(self._stats.get("fast_probe_interesting") or 0) + 1
            )
        else:
            self._stats["fast_probe_skipped_uninteresting"] = (
                int(self._stats.get("fast_probe_skipped_uninteresting") or 0) + 1
            )
        return interesting

    def _refresh_host_state_from_events(self, events: List[Dict[str, Any]]) -> None:
        current_topic = ""
        unresolved_items: List[str] = []
        for event in events:
            content = " ".join(str(event.get("content") or "").split()).strip()
            if not content:
                continue
            if not current_topic:
                current_topic = " ".join(str(event.get("title") or "").split()).strip()
            if (
                event.get("event_type") == "open_discussion"
                and content not in unresolved_items
            ):
                unresolved_items.append(content)

        if current_topic:
            self._host_state.current_topic = current_topic
        if unresolved_items:
            self._host_state.unresolved_items = unresolved_items[:8]

    # ── Behavior System v2 Methods ────────────────────────────────────────

    def _should_analyze_this_cycle(self, transcript_window: str) -> Tuple[bool, str]:
        """
        Deterministic gate: should we run the LLM agent this cycle?
        Returns (should_run, skip_reason).
        Fast and cheap — no LLM call.

        HARD blocks (always skip): silence_mode, warmup, all_categories_suppressed
        SOFT signals (log but proceed): speak/silence triggers, topic filters
        Rationale: keyword matching fails for multilingual (Hindi/Hinglish) transcripts.
        The LLM agent should decide relevance, not a keyword matcher.
        """
        spec = self._behavior_spec
        if not spec or not spec.output_categories:
            return True, "no_spec"

        # ── HARD BLOCKS ──────────────────────────────────────────────────

        # 1. Silence mode — user explicitly wants summary-only
        if spec.silence_mode:
            return False, "silence_mode"

        # 2. Warmup — don't analyze too early
        elapsed = time.time() - self._session_start_time
        if elapsed < spec.warmup_seconds:
            return False, "warmup"

        # 3. All categories suppressed by auto-tuner
        active_cats = [
            c for c in spec.output_categories if spec.is_category_active(c.id)
        ]
        if not active_cats:
            return False, "all_categories_suppressed"

        # ── SOFT SIGNALS (proceed anyway, let LLM decide) ────────────────

        lower_window = transcript_window.lower()

        # 4. Silence triggers — advisory: log but don't block
        for trigger in spec.silence_triggers:
            if self._trigger_matches_text(lower_window, trigger):
                logger.info(
                    "[AIParticipant] Soft silence trigger matched: '%s' (proceeding anyway, LLM will decide)",
                    trigger,
                )
                # Don't block — let LLM decide
                break

        # 5. Speak triggers — advisory: if none match, still proceed
        #    Keyword matching fails for multilingual transcripts (Hindi/Hinglish),
        #    so we MUST NOT hard-block based on keyword mismatches.
        if spec.speak_triggers:
            matched = any(
                self._trigger_matches_text(lower_window, t) for t in spec.speak_triggers
            )
            if not matched:
                logger.info(
                    "[AIParticipant] No speak trigger matched (proceeding anyway — may be multilingual transcript)"
                )

        # 6. Topic filter — advisory
        if spec.topic_filters:
            topic_matched = any(
                self._trigger_matches_text(lower_window, topic)
                for topic in spec.topic_filters
            )
            if not topic_matched:
                logger.info(
                    "[AIParticipant] No topic filter matched (proceeding anyway)"
                )

        return True, "pass"

    @staticmethod
    def _trigger_matches_text(lower_text: str, trigger: str) -> bool:
        """
        Check if a trigger phrase matches the transcript text.
        Uses keyword-based matching — fast and deterministic.
        """
        trigger_lower = trigger.lower().strip()
        if not trigger_lower:
            return False

        # Direct substring match for short triggers
        if trigger_lower in lower_text:
            return True

        # Keyword match: all significant words must appear
        trigger_words = [
            w
            for w in trigger_lower.split()
            if len(w) > 3  # Skip short connecting words
        ]
        if len(trigger_words) >= 2:
            match_count = sum(1 for w in trigger_words if w in lower_text)
            # Require 60%+ keyword match
            if match_count / len(trigger_words) >= 0.6:
                return True

        return False

    def _filter_by_behavior(
        self, suggestions: List[HostSuggestion]
    ) -> List[HostSuggestion]:
        """
        Post-filter: remove suggestions that don't match the behavior spec.
        Applied after the agent produces events.
        """
        spec = self._behavior_spec
        if not spec or not spec.output_categories:
            return suggestions

        allowed_ids = set(spec.get_category_ids())
        filtered: List[HostSuggestion] = []

        for s in suggestions:
            # 1. Category must exist in the spec
            behavior_ids = BEHAVIOR_CATEGORY_ALIASES.get(s.event_type, {s.event_type})
            matched_category_id = next(
                (category_id for category_id in behavior_ids if category_id in allowed_ids),
                None,
            )
            if not matched_category_id:
                logger.info(
                    "[AIParticipant] Filter REJECTED '%s': category '%s' not in behavior spec",
                    s.title,
                    s.event_type,
                )
                self._stats["host_suggestions_suppressed"] = (
                    int(self._stats.get("host_suggestions_suppressed") or 0) + 1
                )
                continue

            # 2. Category must not be suppressed by auto-tuner
            if not spec.is_category_active(matched_category_id):
                logger.info(
                    "[AIParticipant] Filter REJECTED '%s': category '%s' suppressed by auto-tuner (negative feedback)",
                    s.title,
                    matched_category_id,
                )
                self._stats["host_suggestions_suppressed"] = (
                    int(self._stats.get("host_suggestions_suppressed") or 0) + 1
                )
                continue

            # 3. Confidence must meet threshold
            threshold = spec.get_effective_threshold(matched_category_id)
            if s.confidence < threshold:
                logger.info(
                    "[AIParticipant] Filter REJECTED '%s': confidence %.2f < threshold %.2f (category: %s)",
                    s.title,
                    s.confidence,
                    threshold,
                    matched_category_id,
                )
                continue

            # 4. Enforce word limit from behavior spec
            words = s.content.split()
            max_words = spec.max_words_per_insight * 2  # 2x buffer before truncation
            if len(words) > max_words:
                s.content = " ".join(words[: spec.max_words_per_insight]) + "."

            # 5. Check ignore topics
            if spec.ignore_topics:
                content_lower = s.content.lower()
                should_ignore = any(
                    topic.lower() in content_lower for topic in spec.ignore_topics
                )
                if should_ignore:
                    logger.info(
                        "[AIParticipant] Filter REJECTED '%s': matches ignore_topics",
                        s.title,
                    )
                    continue

            logger.info(
                "[AIParticipant] Filter PASSED: '%s' (type: %s, confidence: %.2f)",
                s.title,
                s.event_type,
                s.confidence,
            )
            filtered.append(s)

        return filtered

    def _build_behavior_host_prompt(self, transcript_window: str) -> str:
        """
        Build the host prompt using BehaviorSpec.
        This generates a dynamic prompt based on the user's behavior definition.
        """
        spec = self._behavior_spec
        title = self.meeting_context.title or ""
        goal = self.meeting_context.goal or ""
        agenda_text = self.meeting_context.agenda_text or ""
        participant_names = self.meeting_context.participant_names or []
        participant_line = (
            ", ".join(participant_names[:25]) if participant_names else "None"
        )
        current_summary = self._host_state.meeting_summary or "None"

        # Build the pinned items context
        pinned_context = ""
        if self._host_state.pinned_items:
            items = []
            for item in self._host_state.pinned_items[:10]:
                items.append(
                    f"- [{item.event_type}] {item.title}: {item.content[:120]}"
                )
            pinned_context = "\n".join(items) if items else "None"
        else:
            pinned_context = "None"

        # Build active categories instruction
        active_categories = [
            c for c in spec.output_categories if spec.is_category_active(c.id)
        ]
        categories_block = (
            "\n".join(
                f"- `{cat.id}` ({cat.label}): {cat.description}"
                for cat in active_categories
            )
            or "- No specific categories defined"
        )

        # Build ignore instruction
        ignore_block = ""
        if spec.ignore_topics:
            ignore_block = (
                "\n\nTopics to IGNORE (do NOT produce events about these):\n"
                + "\n".join(f"- {topic}" for topic in spec.ignore_topics)
            )

        return f"""
{spec.personality_prompt}

**Hinglish/Multilingual Awareness**: The transcript often contains Hinglish (Hindi + English) code-switching. Extract decisions, action items, and insights even if they are expressed in mixed languages. Do NOT dismiss Hinglish discussion as noise or assume there is 'no active discussion'.

Meeting Context:
- Title: {title}
- Goal: {goal}
- Agenda: {agenda_text}
- Participants: {participant_line}
- Rolling meeting summary so far:
{current_summary}

Already Pinned Items (do NOT repeat these):
{pinned_context}

Your output categories (use ONLY these when calling tools):
{categories_block}
{ignore_block}

Tone guidance:
{spec.tone_instruction or "Be concise, neutral, and evidence-based."}

Rules:
- You are an active observer. DO NOT return a JSON object. Instead, use the provided tools to share insights and update the summary.
- Update the meeting summary frequently using the `update_summary` tool.
- **CRITICAL**: In `update_summary`, use rich Markdown (Level 3 Headings `###`, Bold `**bold**`, and Lists `- `) so the UI looks structured.
- Everything you send through tools must be in English only.
- Use Level 3 headings (`### Overview`, `### Decisions`, etc.) for summary sections. Do NOT use `#` or `##`.
- If no action is needed, simply finish your turn without calling any tools.
- Do NOT suggest events for topics or decisions that are already in the pinned items list.
- Do NOT call a tool twice if the content has not changed.
- Max {spec.max_words_per_insight} words per event content.

Recent transcript window:
{transcript_window}
""".strip()

    async def apply_behavior_spec(
        self, behavior_markdown: str, source: str = "meeting"
    ) -> Optional[BehaviorSpec]:
        """
        Compile a behavior markdown and apply the resulting spec.
        This is the primary entry point for the v2 behavior system.
        Returns the compiled spec, or None if compilation fails.
        """
        text = (behavior_markdown or "").strip()
        if not text:
            return None

        try:
            spec, used_llm = await compile_behavior(
                markdown=text,
                db=self.db,
                user_email=self.user_email,
                use_llm=True,
            )
        except Exception as e:
            logger.error(
                "[AIParticipant] Behavior compilation failed: %s", e, exc_info=True
            )
            return None

        self._behavior_spec = spec
        self._auto_tuner = BehaviorAutoTuner(spec)
        self._stats["behavior_spec_applied"] = True
        self._stats["behavior_spec_name"] = spec.name
        self._stats["behavior_spec_categories"] = len(spec.output_categories)
        self._stats["behavior_compile_used_llm"] = used_llm

        logger.info(
            "[AIParticipant] Behavior spec applied: name=%s categories=%d silence_mode=%s "
            "speak_triggers=%d silence_triggers=%d used_llm=%s source=%s",
            spec.name,
            len(spec.output_categories),
            spec.silence_mode,
            len(spec.speak_triggers),
            len(spec.silence_triggers),
            used_llm,
            source,
        )
        return spec

    def _load_policy_from_skill(self, skill_text: str, source: str) -> HostPolicyConfig:
        policy = HostPolicyConfig(source=source)
        if not skill_text:
            return policy

        parsed = self._parse_simple_skill_text(skill_text)
        inferred = self._infer_policy_from_markdown(skill_text)
        for key, value in inferred.items():
            parsed.setdefault(key, value)

        role_raw = str(parsed.get("role_mode") or "").strip().lower()
        if role_raw in {"facilitator", "advisor", "chairperson"}:
            policy.role_mode = HostRoleMode(role_raw)

        for key in (
            "min_confidence",
            "suggestion_cooldown_seconds",
            "intervention_cooldown_seconds",
            "max_suggestions_buffer",
            "max_intervention_history",
            "max_pinned_items",
        ):
            if key in parsed:
                self._apply_policy_numeric(policy, key, parsed[key])

        if "allow_interruptions" in parsed:
            policy.allow_interruptions = str(
                parsed["allow_interruptions"]
            ).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }

        forbidden = parsed.get("forbidden_actions")
        if forbidden:
            policy.forbidden_actions = [
                v.strip() for v in str(forbidden).split(",") if v and v.strip()
            ]

        for key, raw_value in parsed.items():
            if not str(key).startswith("threshold_"):
                continue
            event_type = str(key).replace("threshold_", "", 1).strip().lower()
            event_type = self._normalize_host_event_type(event_type) or event_type
            try:
                val = float(raw_value)
                policy.event_threshold_overrides[event_type] = max(0.0, min(1.0, val))
            except Exception:
                continue

        return policy

    @staticmethod
    def _infer_policy_from_markdown(skill_text: str) -> Dict[str, Any]:
        inferred: Dict[str, Any] = {}
        text = str(skill_text or "")
        lower = text.lower()

        # Role inference from human-readable role/identity sections
        if any(
            token in lower
            for token in ["chairperson", "team lead", "tech lead", "engineering lead"]
        ):
            inferred["role_mode"] = "chairperson"
        elif any(token in lower for token in ["advisor", "consultant", "observer"]):
            inferred["role_mode"] = "advisor"
        elif any(token in lower for token in ["facilitator", "moderator", "host"]):
            inferred["role_mode"] = "facilitator"

        # Interaction style hints
        if "always ask clarifying questions" in lower:
            inferred.setdefault("min_confidence", "0.72")
            inferred.setdefault("threshold_open_discussion", "0.66")

        if any(
            token in lower
            for token in [
                "default to simplicity",
                "simplicity over clever",
                "simple over clever",
            ]
        ):
            inferred["forbidden_actions"] = (
                "overengineered_solutions, shame_participants, legal_advice"
            )

        if any(
            token in lower
            for token in [
                "direct and confident",
                "drive decisions",
                "time-box",
                "timebox",
            ]
        ):
            inferred.setdefault("intervention_cooldown_seconds", "90")
            inferred.setdefault("threshold_decision_candidate", "0.65")

        return inferred

    @staticmethod
    def _parse_simple_skill_text(skill_text: str) -> Dict[str, str]:
        parsed: Dict[str, str] = {}
        text = str(skill_text or "")

        # Prefer fenced code blocks (yaml/yml/toml/ini/txt) if present.
        # This allows users to paste markdown docs with an embedded config block.
        fence_blocks = re.findall(
            r"```(?:yaml|yml|toml|ini|txt)?\s*(.*?)\s*```", text, flags=re.DOTALL
        )
        candidate = fence_blocks[0] if fence_blocks else text

        for line in candidate.splitlines():
            item = line.strip()
            if not item:
                continue
            if item.startswith("#"):
                continue
            if item.startswith("- "):
                item = item[2:].strip()
            if item.startswith("* "):
                item = item[2:].strip()
            if ":" not in item:
                continue
            key, value = item.split(":", 1)
            key = key.strip().strip("`").lower()
            value = value.strip().strip("`")
            if not key:
                continue
            parsed[key] = value
        return parsed

    @staticmethod
    def _apply_policy_numeric(policy: HostPolicyConfig, key: str, value: Any) -> None:
        try:
            if key in {"min_confidence"}:
                setattr(policy, key, max(0.0, min(1.0, float(value))))
            else:
                setattr(policy, key, max(1, int(float(value))))
        except Exception:
            return

    def apply_host_skill_override(
        self, skill_markdown: str, source: str = "meeting"
    ) -> None:
        skill_text = (skill_markdown or "").strip()
        if not skill_text:
            return
        self._active_skill_markdown = skill_text
        self._active_skill_definition = parse_skill_markdown(skill_text)
        self._host_policy = self._load_policy_from_skill(skill_text, source=source)
        self._host_policy_source = source
        self._stats["host_policy_source"] = source

        # Also compile BehaviorSpec from the same markdown
        self._pending_behavior_compile = skill_text

    def set_host_template(self, template_name: str, source: str = "system") -> None:
        template_key = str(template_name or "").strip().lower()
        skill_text = SYSTEM_HOST_SKILLS.get(template_key)
        if not skill_text:
            return
        self._active_skill_markdown = skill_text
        self._active_skill_definition = parse_skill_markdown(skill_text)
        self._host_policy = self._load_policy_from_skill(skill_text, source=source)
        self._host_policy_source = source
        self._stats["host_policy_source"] = source

        # Also compile BehaviorSpec from the system template
        self._pending_behavior_compile = skill_text

    async def compile_pending_behavior(self) -> None:
        """
        Compile any pending behavior markdown set during skill application.
        Must be called after apply_host_skill_override or set_host_template.
        Separated because those methods are sync, compilation is async.
        """
        markdown = getattr(self, "_pending_behavior_compile", None)
        if not markdown:
            return
        self._pending_behavior_compile = None
        try:
            spec = await self.apply_behavior_spec(
                behavior_markdown=markdown, source=self._host_policy_source
            )
            if spec:
                logger.info(
                    "[AIParticipant] Behavior spec compiled from style: name=%s categories=%d",
                    spec.name,
                    len(spec.output_categories),
                )
        except Exception as e:
            logger.warning(
                "[AIParticipant] Background behavior compilation failed: %s", e
            )

    def get_behavior_sync_payload(self) -> dict:
        """Return the behavior spec sync payload for the frontend."""
        if not self._behavior_spec:
            return {}
        return self._behavior_spec.get_frontend_sync_payload()

    def pin_suggestion(
        self, suggestion_id: str, actor: Optional[str] = None
    ) -> Optional[HostSuggestion]:
        suggestion_id = str(suggestion_id or "").strip()
        if not suggestion_id:
            return None

        match = None
        remaining: List[HostSuggestion] = []
        for item in self._host_state.suggested_items:
            if item.id == suggestion_id and match is None:
                match = item
            else:
                remaining.append(item)

        if not match:
            for item in self._host_state.pinned_items:
                if item.id == suggestion_id:
                    return item
            return None

        match.status = "pinned"
        meta = dict(match.metadata or {})
        if actor:
            meta["pinned_by"] = actor
        meta["pinned_at"] = datetime.utcnow().isoformat()
        match.metadata = meta

        self._host_state.suggested_items = remaining
        self._host_state.pinned_items.insert(0, match)
        self._host_state.pinned_items = self._host_state.pinned_items[
            : self._host_policy.max_pinned_items
        ]

        # Add to handled history for deduplication
        content_hash = self._get_content_hash(match.event_type, match.content)
        if content_hash not in self._host_state.handled_content_hashes:
            self._host_state.handled_content_hashes.insert(0, content_hash)
            self._host_state.handled_content_hashes = (
                self._host_state.handled_content_hashes[:500]
            )

        self._host_state.counters["pinned"] = (
            int(self._host_state.counters.get("pinned") or 0) + 1
        )
        self._host_state.updated_at = datetime.utcnow().isoformat()
        self._stats["host_suggestions_pinned"] += 1

        # Auto-tuner: user found this valuable
        if self._auto_tuner and hasattr(match, "event_type"):
            self._auto_tuner.on_pin(match.event_type)

        return match

    def dismiss_suggestion(
        self, suggestion_id: str, actor: Optional[str] = None
    ) -> bool:
        suggestion_id = str(suggestion_id or "").strip()
        if not suggestion_id:
            return False

        match: Optional[HostSuggestion] = None
        remaining: List[HostSuggestion] = []
        for item in self._host_state.suggested_items:
            if item.id == suggestion_id and match is None:
                match = item
                continue
            remaining.append(item)

        if not match:
            return False

        self._host_state.suggested_items = remaining
        if suggestion_id not in self._host_state.dismissed_item_ids:
            self._host_state.dismissed_item_ids.insert(0, suggestion_id)
            self._host_state.dismissed_item_ids = self._host_state.dismissed_item_ids[
                :200
            ]

        # Add to handled history for deduplication
        content_hash = self._get_content_hash(match.event_type, match.content)
        if content_hash not in self._host_state.handled_content_hashes:
            self._host_state.handled_content_hashes.insert(0, content_hash)
            self._host_state.handled_content_hashes = (
                self._host_state.handled_content_hashes[:500]
            )

        self._host_state.counters["dismissed"] = (
            int(self._host_state.counters.get("dismissed") or 0) + 1
        )
        self._host_state.updated_at = datetime.utcnow().isoformat()
        self._stats["host_suggestions_dismissed"] += 1

        # Auto-tuner: user didn't want this
        if self._auto_tuner and hasattr(match, "event_type"):
            self._auto_tuner.on_dismiss(match.event_type)

        if actor:
            self._host_state.last_response_outcomes.insert(0, f"dismissed_by:{actor}")
            self._host_state.last_response_outcomes = (
                self._host_state.last_response_outcomes[:50]
            )
        return True

    def record_feedback(
        self, suggestion_id: str, feedback: str, actor: Optional[str] = None
    ) -> None:
        entry = f"feedback:{suggestion_id}:{feedback}"
        if actor:
            entry += f":{actor}"
        self._host_state.last_response_outcomes.insert(0, entry[:300])
        self._host_state.last_response_outcomes = (
            self._host_state.last_response_outcomes[:50]
        )
        self._host_state.updated_at = datetime.utcnow().isoformat()

    def get_host_state_snapshot(self) -> Dict[str, Any]:
        state = self._host_state.model_dump()
        state["policy_source"] = self._host_policy_source
        state["policy_role_mode"] = self._host_policy.role_mode.value
        return state

    @staticmethod
    def _normalize_reason(reason_value: str) -> Optional[str]:
        if not reason_value:
            return None
        raw = str(reason_value).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "agenda_deviation": "agenda_deviation",
            "no_decision": "no_decision",
            "long_discussion_without_decision": "no_decision",
            "unresolved_question": "unresolved_question",
            "important_unresolved_question": "unresolved_question",
            "missing_context_or_repeat": "missing_context_or_repeat",
            "missing_context": "missing_context_or_repeat",
            "repeated_topic": "missing_context_or_repeat",
        }
        return aliases.get(raw)

    @classmethod
    def _normalize_model_payload(cls, payload: Dict) -> Optional[Dict]:
        if not isinstance(payload, dict):
            return None

        intervention_required = bool(payload.get("intervention_required", False))
        if not intervention_required:
            return {"intervention_required": False}

        reason = cls._normalize_reason(payload.get("reason"))
        insight = " ".join(str(payload.get("insight") or "").split()).strip()
        confidence_raw = payload.get("confidence", 0.0)
        try:
            confidence = float(confidence_raw)
        except Exception:
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        if not reason or not insight:
            return {"intervention_required": False}

        return {
            "intervention_required": True,
            "reason": reason,
            "insight": insight,
            "confidence": confidence,
        }

    @staticmethod
    def _extract_json(raw_text: str) -> Optional[Dict]:
        text = (raw_text or "").strip()
        if not text:
            return None

        try:
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass

        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence_match:
            try:
                obj = json.loads(fence_match.group(1))
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None

        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                obj = json.loads(text[start : end + 1])
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None

        return None

    def get_stats_snapshot(self) -> Dict[str, Any]:
        payload = dict(self._stats)
        payload["window_char_count"] = self.buffer.get_char_count()
        payload["window_duration_seconds"] = round(
            self.buffer.get_duration_seconds(), 3
        )
        payload["evaluator"] = self.evaluator.get_metrics_snapshot()
        payload["provider"] = self.provider
        payload["model"] = self.model_name
        payload["active_skill"] = self._active_skill_definition
        payload["host_policy"] = self._host_policy.model_dump()
        payload["host_state"] = self.get_host_state_snapshot()
        suggested = int(payload.get("host_suggestions_emitted") or 0)
        pinned = int(payload.get("host_suggestions_pinned") or 0)
        dismissed = int(payload.get("host_suggestions_dismissed") or 0)
        payload["host_quality"] = {
            "pin_rate": round((pinned / suggested), 4) if suggested > 0 else 0.0,
            "dismiss_rate": round((dismissed / suggested), 4) if suggested > 0 else 0.0,
            "suggested": suggested,
            "pinned": pinned,
            "dismissed": dismissed,
        }

        # Behavior system v2 observability
        if self._behavior_spec:
            payload["behavior_spec"] = {
                "name": self._behavior_spec.name,
                "categories": len(self._behavior_spec.output_categories),
                "silence_mode": self._behavior_spec.silence_mode,
                "summary_visibility": self._behavior_spec.summary_visibility,
                "suppressed_categories": list(
                    self._behavior_spec.suppressed_categories
                ),
            }
        if self._auto_tuner:
            payload["auto_tuner"] = self._auto_tuner.get_adjustments_summary()

        return payload

    def apply_manual_context(
        self,
        goal: Optional[str] = None,
        agenda_text: Optional[str] = None,
        participant_names: Optional[List[str]] = None,
    ) -> None:
        if goal is not None:
            self.meeting_context.goal = (goal or "").strip()
        if agenda_text is not None:
            self.meeting_context.agenda_text = (agenda_text or "").strip()
        if participant_names is not None:
            cleaned: List[str] = []
            for name in participant_names or []:
                value = " ".join(str(name or "").split()).strip()
                if value and value not in cleaned:
                    cleaned.append(value)
            self.meeting_context.participant_names = cleaned
