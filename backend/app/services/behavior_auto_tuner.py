"""
BehaviorAutoTuner — Automatic behavior adjustment based on user actions.

When users pin or dismiss suggestions, this module automatically adjusts
confidence thresholds per category. No human tuning required.

Design principles:
  1. Asymmetric: Dismiss penalizes more than pin rewards (prevents noise)
  2. Bounded: Thresholds clamp to [0.50, 0.95]
  3. Per-category: Each category tunes independently
  4. Session decay: Adjustments decay toward baseline between sessions
  5. Suppression: Categories with many consecutive dismisses get suppressed
"""

import logging
from collections import defaultdict
from typing import Any, Dict

logger = logging.getLogger(__name__)

try:
    from ..schemas.behavior_spec import BehaviorSpec
except (ImportError, ValueError):
    from schemas.behavior_spec import BehaviorSpec


class BehaviorAutoTuner:
    """
    Automatically adjusts a BehaviorSpec's thresholds based on user feedback.

    Usage:
        tuner = BehaviorAutoTuner(spec)
        tuner.on_pin("technical_decision")    # User pinned → show more
        tuner.on_dismiss("open_question")     # User dismissed → show fewer
        tuner.persist()                       # Save adjustments to spec
    """

    # Tuning constants
    DISMISS_PENALTY = 0.05       # Raise threshold on dismiss
    PIN_REWARD = -0.03           # Lower threshold on pin
    IGNORE_PENALTY = 0.01        # Slight raise on ignore (no action for 10 min)
    THRESHOLD_MIN = 0.50         # Can't go below 50%
    THRESHOLD_MAX = 0.95         # Can't go above 95%
    SESSION_DECAY = 0.01         # Thresholds decay toward baseline between sessions

    # Cooldown tuning
    COOLDOWN_INCREASE_ON_DISMISS = 10   # Add 10s cooldown on dismiss
    COOLDOWN_DECREASE_ON_PIN = -5       # Reduce 5s cooldown on pin
    COOLDOWN_MIN = 15                   # Minimum cooldown seconds
    COOLDOWN_MAX = 300                  # Maximum cooldown seconds

    # Suppression
    SUPPRESS_AFTER_CONSECUTIVE_DISMISSES = 8
    UNSUPPRESS_AFTER_PINS = 3

    # Streak tracking
    STREAK_THRESHOLD = 5     # Consecutive same actions to trigger streak bonus

    def __init__(self, spec: BehaviorSpec):
        self._spec = spec

        # Action counters per category
        self._pin_counts: Dict[str, int] = defaultdict(int)
        self._dismiss_counts: Dict[str, int] = defaultdict(int)
        self._ignore_counts: Dict[str, int] = defaultdict(int)

        # Consecutive action tracking (resets on opposite action)
        self._consecutive_pins: Dict[str, int] = defaultdict(int)
        self._consecutive_dismisses: Dict[str, int] = defaultdict(int)

        # Unsuppress counters
        self._post_suppress_pins: Dict[str, int] = defaultdict(int)

        # Total events for observability
        self._total_actions = 0

    def on_pin(self, category_id: str) -> None:
        """User pinned a suggestion → lower threshold (show more of this)."""
        if not category_id:
            return

        self._total_actions += 1
        self._pin_counts[category_id] += 1
        self._consecutive_pins[category_id] += 1
        self._consecutive_dismisses[category_id] = 0  # Reset dismiss streak

        # Basic threshold adjustment
        delta = self.PIN_REWARD
        # Streak bonus: if user pins 5+ in a row, be more responsive
        if self._consecutive_pins[category_id] >= self.STREAK_THRESHOLD:
            delta *= 1.5  # Stronger reward

        self._adjust_threshold(category_id, delta)

        # Cooldown adjustment (streak bonus)
        if self._consecutive_pins[category_id] >= self.STREAK_THRESHOLD:
            self._adjust_cooldown(category_id, self.COOLDOWN_DECREASE_ON_PIN)

        # Un-suppress if enough pins
        if category_id in self._spec.suppressed_categories:
            self._post_suppress_pins[category_id] += 1
            if self._post_suppress_pins[category_id] >= self.UNSUPPRESS_AFTER_PINS:
                self._spec.suppressed_categories.remove(category_id)
                self._post_suppress_pins[category_id] = 0
                logger.info(
                    "[AutoTuner] Category unsuppressed: %s (pinned %d times after suppression)",
                    category_id,
                    self.UNSUPPRESS_AFTER_PINS,
                )

        logger.debug(
            "[AutoTuner] Pin: category=%s threshold=%.3f streak=%d",
            category_id,
            self._spec.get_effective_threshold(category_id),
            self._consecutive_pins[category_id],
        )

    def on_dismiss(self, category_id: str) -> None:
        """User dismissed a suggestion → raise threshold (show fewer)."""
        if not category_id:
            return

        self._total_actions += 1
        self._dismiss_counts[category_id] += 1
        self._consecutive_dismisses[category_id] += 1
        self._consecutive_pins[category_id] = 0  # Reset pin streak

        # Basic threshold adjustment
        delta = self.DISMISS_PENALTY
        # Streak penalty: if user dismisses 5+ in a row, be more aggressive
        if self._consecutive_dismisses[category_id] >= self.STREAK_THRESHOLD:
            delta *= 1.5  # Stronger penalty

        self._adjust_threshold(category_id, delta)

        # Cooldown increase (streak)
        if self._consecutive_dismisses[category_id] >= self.STREAK_THRESHOLD:
            self._adjust_cooldown(category_id, self.COOLDOWN_INCREASE_ON_DISMISS)

        # Suppression check
        if (
            self._consecutive_dismisses[category_id]
            >= self.SUPPRESS_AFTER_CONSECUTIVE_DISMISSES
            and category_id not in self._spec.suppressed_categories
        ):
            self._spec.suppressed_categories.append(category_id)
            logger.info(
                "[AutoTuner] Category suppressed: %s (dismissed %d consecutive times)",
                category_id,
                self._consecutive_dismisses[category_id],
            )

        logger.debug(
            "[AutoTuner] Dismiss: category=%s threshold=%.3f streak=%d suppressed=%s",
            category_id,
            self._spec.get_effective_threshold(category_id),
            self._consecutive_dismisses[category_id],
            category_id in self._spec.suppressed_categories,
        )

    def on_ignore(self, category_id: str) -> None:
        """Suggestion was not acted on for 10+ minutes → slight threshold raise."""
        if not category_id:
            return

        self._total_actions += 1
        self._ignore_counts[category_id] += 1

        # Gentle penalty (much less than dismiss)
        self._adjust_threshold(category_id, self.IGNORE_PENALTY)

        logger.debug(
            "[AutoTuner] Ignore: category=%s threshold=%.3f",
            category_id,
            self._spec.get_effective_threshold(category_id),
        )

    def start_session(self) -> None:
        """
        Called at the start of a new session.
        Decays all adjustments slightly toward baseline.
        """
        for category_id in list(self._spec.category_thresholds.keys()):
            current = self._spec.category_thresholds[category_id]
            cat = self._spec.get_category(category_id)
            baseline = cat.base_confidence if cat else self._spec.base_confidence

            if abs(current - baseline) < 0.005:
                # Close enough — snap to baseline
                del self._spec.category_thresholds[category_id]
                continue

            # Decay toward baseline
            if current > baseline:
                new = max(baseline, current - self.SESSION_DECAY)
            else:
                new = min(baseline, current + self.SESSION_DECAY)

            self._spec.category_thresholds[category_id] = round(new, 4)

        # Decay cooldown adjustments similarly
        for category_id in list(self._spec.category_cooldowns.keys()):
            current = self._spec.category_cooldowns[category_id]
            baseline = self._spec.suggestion_cooldown_seconds

            if abs(current - baseline) <= 5:
                del self._spec.category_cooldowns[category_id]
                continue

            if current > baseline:
                self._spec.category_cooldowns[category_id] = max(baseline, current - 5)
            else:
                self._spec.category_cooldowns[category_id] = min(baseline, current + 5)

        # Reset streaks
        self._consecutive_pins.clear()
        self._consecutive_dismisses.clear()

        logger.info(
            "[AutoTuner] Session started. Decayed thresholds=%d cooldowns=%d",
            len(self._spec.category_thresholds),
            len(self._spec.category_cooldowns),
        )

    def is_category_suppressed(self, category_id: str) -> bool:
        """Check if a category has been suppressed by auto-tuning."""
        return category_id in self._spec.suppressed_categories

    def get_effective_cooldown(self, category_id: str) -> int:
        """Get the effective cooldown seconds for a category."""
        return self._spec.category_cooldowns.get(
            category_id,
            self._spec.suggestion_cooldown_seconds,
        )

    def get_adjustments_summary(self) -> Dict[str, Any]:
        """Return current tuning state for observability."""
        return {
            "total_actions": self._total_actions,
            "pin_counts": dict(self._pin_counts),
            "dismiss_counts": dict(self._dismiss_counts),
            "ignore_counts": dict(self._ignore_counts),
            "threshold_adjustments": dict(self._spec.category_thresholds),
            "cooldown_adjustments": dict(self._spec.category_cooldowns),
            "suppressed_categories": list(self._spec.suppressed_categories),
        }

    def persist(self) -> BehaviorSpec:
        """
        Return the spec with all adjustments applied.
        Call this when you want to save the tuned state.
        """
        return self._spec

    # ── Internal ──────────────────────────────────────────────────────────

    def _adjust_threshold(self, category_id: str, delta: float) -> None:
        """Adjust a category's confidence threshold."""
        cat = self._spec.get_category(category_id)
        baseline = cat.base_confidence if cat else self._spec.base_confidence
        current = self._spec.category_thresholds.get(category_id, baseline)
        new = max(self.THRESHOLD_MIN, min(self.THRESHOLD_MAX, current + delta))
        self._spec.category_thresholds[category_id] = round(new, 4)

    def _adjust_cooldown(self, category_id: str, delta: int) -> None:
        """Adjust a category's cooldown seconds."""
        current = self._spec.category_cooldowns.get(
            category_id,
            self._spec.suggestion_cooldown_seconds,
        )
        new = max(self.COOLDOWN_MIN, min(self.COOLDOWN_MAX, current + delta))
        self._spec.category_cooldowns[category_id] = new
