from __future__ import annotations

from .extractor import Extraction
from .models import UserProfile


class ProfileDistiller:
    """Promotes repeated explicit preferences, never one-off requests, to profile."""

    PROMOTABLE = {"color", "style", "occasion", "category"}

    def update(self, profile: UserProfile, extraction: Extraction) -> None:
        # Distil only preferences explicitly mentioned on this turn. Re-reading
        # session slots here would incorrectly promote a one-off constraint.
        for slot in extraction.slots:
            name = slot.name
            if name not in self.PROMOTABLE:
                continue
            observed = profile.observations.setdefault(name, {})
            key = str(slot.value)
            observed[key] = observed.get(key, 0) + 1
            if observed[key] >= 2:
                profile.stable_preferences[name] = [key]
                profile.preference_confidence[name] = min(0.9, 0.45 + observed[key] * 0.15)
        if profile.stable_preferences:
            profile.profile_summary = "; ".join(
                f"usually prefers {name}={','.join(values)}"
                for name, values in profile.stable_preferences.items()
            )
