"""
BehaviorSpec — Compiled behavior specification for the AI Participant.

This is the machine-readable contract produced by the BehaviorCompiler
from a user's natural-language behavior card. It drives:
  1. Gate checks (should the engine analyze this cycle?)
  2. Agent tool generation (what categories of output exist?)
  3. Post-filters (drop events that don't match the behavior)
  4. Auto-tuning (pin/dismiss → threshold adjustments)
  5. Frontend rendering (category icons, labels, display hints)

The user never sees or edits this directly. They write markdown.
The compiler produces this. The engine consumes this.
"""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class OutputCategory(BaseModel):
    """
    A single output category defined by the user's behavior file.

    Example: a behavior that says "Track technical decisions" produces:
        OutputCategory(
            id="technical_decision",
            label="Technical Decision",
            icon="🔧",
            description="Any choice about architecture, tools, or approach",
            display_hint="card",
            priority_default="high",
            base_confidence=0.72,
        )
    """

    id: str = Field(
        ...,
        description="Snake_case identifier derived from the category label",
    )
    label: str = Field(
        ...,
        description="Human-readable label for display",
    )
    icon: str = Field(
        default="📌",
        description="Emoji icon for UI rendering",
    )
    description: str = Field(
        default="",
        description="Description from the behavior file (What to Track bullet)",
    )
    display_hint: str = Field(
        default="card",
        description="How the frontend should render this: card | banner | subtle | silent",
    )
    priority_default: str = Field(
        default="medium",
        description="Default priority for events of this category: low | medium | high | critical",
    )
    base_confidence: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        description="Minimum confidence to produce an event of this category",
    )

    # Auto-tuned at runtime (persisted per user style)
    tuned_confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Runtime-adjusted confidence threshold from auto-tuner",
    )
    tuned_cooldown_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        description="Runtime-adjusted cooldown for this category",
    )

    @property
    def effective_confidence(self) -> float:
        """Return tuned threshold if available, otherwise base."""
        if self.tuned_confidence is not None:
            return self.tuned_confidence
        return self.base_confidence


class BehaviorSpec(BaseModel):
    """
    Compiled behavior specification — the machine-readable contract.

    Produced by BehaviorCompiler from the user's natural-language behavior card.
    Consumed by AIParticipantEngine at runtime.
    """

    # ── Identity ──────────────────────────────────────────────────────────
    name: str = Field(
        default="Meeting Assistant",
        description="Display name from the behavior file heading",
    )
    personality_prompt: str = Field(
        default="",
        description="Compiled 'Who You Are' section → injected as system prompt",
    )

    # ── Gate Rules (deterministic, no LLM needed) ─────────────────────────
    silence_mode: bool = Field(
        default=False,
        description="True = never produce suggestions, only update summary",
    )
    speak_triggers: List[str] = Field(
        default_factory=list,
        description="From 'When to Speak'. If non-empty, at least one must match.",
    )
    silence_triggers: List[str] = Field(
        default_factory=list,
        description="From 'When to Stay Silent'. If any match, skip analysis.",
    )
    topic_filters: List[str] = Field(
        default_factory=list,
        description="If non-empty, only analyze when transcript matches these topics.",
    )
    warmup_seconds: int = Field(
        default=0,
        ge=0,
        description="Wait this many seconds into the meeting before first analysis.",
    )

    # ── Output Categories (DYNAMIC — from 'What to Track') ──────────────
    output_categories: List[OutputCategory] = Field(
        default_factory=list,
        description="Dynamic event types the AI can produce. From 'What to Track'.",
    )
    ignore_topics: List[str] = Field(
        default_factory=list,
        description="From 'What to Ignore'. Topics to never produce events about.",
    )

    # ── Tone ──────────────────────────────────────────────────────────────
    tone_instruction: str = Field(
        default="",
        description="Compiled 'How to Sound' section → injected into system prompt.",
    )
    max_words_per_insight: int = Field(
        default=30,
        ge=5,
        le=200,
        description="Max words per event content. Enforced by post-filter.",
    )

    # ── Policy ────────────────────────────────────────────────────────────
    base_confidence: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        description="Global fallback confidence threshold.",
    )
    suggestion_cooldown_seconds: int = Field(
        default=60,
        ge=5,
        description="Minimum seconds between suggestions of the same category.",
    )
    intervention_cooldown_seconds: int = Field(
        default=120,
        ge=10,
        description="Minimum seconds between intervention cards.",
    )
    summary_visibility: str = Field(
        default="background",
        description="How the meeting summary is displayed: background | prominent",
    )

    # ── Auto-tuning State (persisted per user style) ──────────────────────
    category_thresholds: Dict[str, float] = Field(
        default_factory=dict,
        description="Runtime adjustments from auto-tuner: category_id → adjusted confidence.",
    )
    category_cooldowns: Dict[str, int] = Field(
        default_factory=dict,
        description="Runtime cooldown adjustments: category_id → adjusted cooldown seconds.",
    )
    suppressed_categories: List[str] = Field(
        default_factory=list,
        description="Categories suppressed by auto-tuner (too many dismisses).",
    )

    # ── Meta ──────────────────────────────────────────────────────────────
    source_markdown: str = Field(
        default="",
        description="Original user markdown for display/editing.",
    )
    compiled_at: str = Field(
        default="",
        description="ISO timestamp of when this spec was compiled.",
    )
    compiler_model: str = Field(
        default="",
        description="Which LLM model compiled this spec.",
    )
    format_version: int = Field(
        default=2,
        description="Schema version. v1 = old YAML-based, v2 = natural language.",
    )

    # ── Helper Methods ────────────────────────────────────────────────────

    def get_category(self, category_id: str) -> Optional[OutputCategory]:
        """Find an output category by ID."""
        for cat in self.output_categories:
            if cat.id == category_id:
                return cat
        return None

    def get_category_ids(self) -> List[str]:
        """Return list of all output category IDs."""
        return [cat.id for cat in self.output_categories]

    def get_effective_threshold(self, category_id: str) -> float:
        """
        Return the effective confidence threshold for a category,
        considering auto-tuning adjustments.
        """
        # Check auto-tuner override first
        if category_id in self.category_thresholds:
            return self.category_thresholds[category_id]
        # Then check category-specific base
        cat = self.get_category(category_id)
        if cat:
            return cat.effective_confidence
        # Global fallback
        return self.base_confidence

    def is_category_active(self, category_id: str) -> bool:
        """Check if a category is active (not suppressed by auto-tuner)."""
        return category_id not in self.suppressed_categories

    def get_frontend_sync_payload(self) -> dict:
        """
        Return the subset of the spec that the frontend needs at session start.
        Sent via WebSocket as behavior_spec_sync message.
        """
        return {
            "name": self.name,
            "output_categories": [
                {
                    "id": cat.id,
                    "label": cat.label,
                    "icon": cat.icon,
                    "description": cat.description,
                    "display_hint": cat.display_hint,
                    "priority_default": cat.priority_default,
                }
                for cat in self.output_categories
                if self.is_category_active(cat.id)
            ],
            "summary_visibility": self.summary_visibility,
            "suppressed_categories": list(self.suppressed_categories),
        }


# ── Default Spec ──────────────────────────────────────────────────────────

DEFAULT_OUTPUT_CATEGORIES = [
    OutputCategory(
        id="decision",
        label="Decision",
        icon="✅",
        description="Explicit agreements or commitments",
        display_hint="card",
        priority_default="high",
        base_confidence=0.50,
    ),
    OutputCategory(
        id="open_question",
        label="Open Question",
        icon="❓",
        description="Unresolved issues that need follow-up",
        display_hint="card",
        priority_default="medium",
        base_confidence=0.50,
    ),
    OutputCategory(
        id="action_item",
        label="Action Item",
        icon="📋",
        description="Tasks with or without owners",
        display_hint="card",
        priority_default="high",
        base_confidence=0.50,
    ),
    OutputCategory(
        id="key_insight",
        label="Key Insight",
        icon="💡",
        description="Important observations worth preserving",
        display_hint="subtle",
        priority_default="medium",
        base_confidence=0.50,
    ),
]


def build_default_spec() -> BehaviorSpec:
    """Build the default BehaviorSpec used when no behavior file is configured."""
    return BehaviorSpec(
        name="Meeting Assistant",
        personality_prompt=(
            "You are a helpful meeting assistant who quietly observes and "
            "surfaces the most important moments so nothing gets lost. "
            "Be neutral, concise, and evidence-based."
        ),
        speak_triggers=[
            "participants agree on something",
            "a question stays unresolved",
            "someone takes on a task",
            "discussion drifts from the meeting goal",
        ],
        silence_triggers=[
            "participants are actively debating",
            "casual conversation or small talk",
            "meeting just started and people are settling in",
        ],
        output_categories=list(DEFAULT_OUTPUT_CATEGORIES),
        tone_instruction="Neutral and concise. State facts, not opinions. One sentence per insight.",
        max_words_per_insight=30,
        warmup_seconds=0,
        base_confidence=0.70,
        summary_visibility="background",
        compiled_at=datetime.utcnow().isoformat(),
        compiler_model="default",
        format_version=2,
    )
