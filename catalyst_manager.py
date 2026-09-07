"""CatalystManager: Tracks scheduled catalyst events and evaluates risk regimes.

Used by the daily audit agent, web dashboard, and market-making safety layers to
enforce pre-event de-risking (REDUCE_ONLY) and execution blackouts (HALTED).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

DEFAULT_CATALYSTS_FILE = Path(__file__).parent / "catalysts.json"


class CatalystPhase(str, Enum):
    NORMAL = "NORMAL"            # Far from event: normal quoting and reward farming
    REDUCE_ONLY = "REDUCE_ONLY"  # T-minus window: cancel BUYs, passive Maker SELL take-profit & merge only
    BLACKOUT = "BLACKOUT"        # Immediate event blackout: cancel 100% of orders to avoid sniping
    COOLOFF = "COOLOFF"          # Post-event window: wait for jump volatility to settle before re-entry


@dataclass
class CatalystEvent:
    id: str
    market_slug: str
    title: str
    event_date_utc: str
    reduce_only_hours: float = 72.0
    halt_before_minutes: float = 30.0
    cooloff_post_minutes: float = 60.0
    source: str = "Manual / Calendar"
    status: str = "ACTIVE"
    notes: str = ""

    @property
    def event_ts(self) -> float:
        try:
            dt = datetime.fromisoformat(self.event_date_utc.replace("Z", "+00:00"))
            return dt.timestamp()
        except Exception:
            return 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CatalystManager:
    def __init__(self, file_path: str | Path = DEFAULT_CATALYSTS_FILE) -> None:
        self.file_path = Path(file_path)
        self._events: list[CatalystEvent] = []
        self.reload()

    def reload(self) -> None:
        if not self.file_path.exists():
            self._events = []
            return
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            self._events = [CatalystEvent(**item) for item in raw_data if item.get("status") != "DELETED"]
        except Exception as e:
            print(f"[CatalystManager] Error loading {self.file_path}: {e}")
            self._events = []

    def save(self) -> None:
        try:
            data = [e.to_dict() for e in self._events]
            tmp_path = self.file_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, self.file_path)
        except Exception as e:
            print(f"[CatalystManager] Error saving {self.file_path}: {e}")

    def list_events(self, market_slug: str | None = None) -> list[CatalystEvent]:
        self.reload()
        if market_slug:
            return [e for e in self._events if e.market_slug == market_slug and e.status == "ACTIVE"]
        return [e for e in self._events if e.status == "ACTIVE"]

    def get_next_event(self, market_slug: str | None = None, now: float | None = None) -> CatalystEvent | None:
        if now is None:
            now = time.time()
        events = self.list_events(market_slug)
        # Filter events that haven't fully passed cooloff yet
        future_or_recent = []
        for e in events:
            cooloff_end = e.event_ts + (e.cooloff_post_minutes * 60.0)
            if cooloff_end > now:
                future_or_recent.append(e)

        if not future_or_recent:
            return None
        future_or_recent.sort(key=lambda x: x.event_ts)
        return future_or_recent[0]

    def evaluate_market(
        self, market_slug: str, now: float | None = None
    ) -> tuple[CatalystPhase, float | None, CatalystEvent | None, str]:
        """Evaluate a market's risk phase against the catalyst calendar.

        Returns:
            (phase, hours_to_event, event_obj, reason_string)
        """
        if now is None:
            now = time.time()

        next_event = self.get_next_event(market_slug, now)
        if not next_event:
            return CatalystPhase.NORMAL, None, None, "Nessun catalyst programmato"

        diff_seconds = next_event.event_ts - now
        hours_to_event = diff_seconds / 3600.0

        halt_window_s = next_event.halt_before_minutes * 60.0
        cooloff_window_s = next_event.cooloff_post_minutes * 60.0

        # Phase 1: Blackout (Within halt_before_minutes before, or while event is happening)
        if -cooloff_window_s <= diff_seconds <= halt_window_s:
            if diff_seconds > 0:
                mins_left = int(diff_seconds / 60)
                return (
                    CatalystPhase.BLACKOUT,
                    hours_to_event,
                    next_event,
                    f"BLACKOUT PREVENTIVO: Mancano {mins_left} min a '{next_event.title}'"
                )
            else:
                return (
                    CatalystPhase.COOLOFF,
                    hours_to_event,
                    next_event,
                    f"COOLOFF POST-EVENTO: '{next_event.title}' in corso/appena concluso"
                )

        # Phase 2: Reduce-Only (Within reduce_only_hours before event)
        reduce_only_s = next_event.reduce_only_hours * 3600.0
        if 0 < diff_seconds <= reduce_only_s:
            days_left = diff_seconds / 86400.0
            return (
                CatalystPhase.REDUCE_ONLY,
                hours_to_event,
                next_event,
                f"REDUCE_ONLY ATTIVO: Mancano {days_left:.1f} giorni a '{next_event.title}' (solo uscite)"
            )

        # Phase 3: Normal
        days_left = diff_seconds / 86400.0
        return (
            CatalystPhase.NORMAL,
            hours_to_event,
            next_event,
            f"Fase NORMALE: Mancano {days_left:.1f} giorni a '{next_event.title}'"
        )

    def add_or_update_event(self, event: CatalystEvent) -> None:
        self.reload()
        existing = [e for e in self._events if e.id == event.id]
        if existing:
            self._events = [e if e.id != event.id else event for e in self._events]
        else:
            self._events.append(event)
        self.save()


if __name__ == "__main__":
    mgr = CatalystManager()
    events = mgr.list_events()
    print(f"Loaded {len(events)} active events.")
    for ev in events:
        phase, hrs, next_ev, reason = mgr.evaluate_market(ev.market_slug)
        print(f"[{ev.market_slug}] -> {phase.value} ({hrs:.1f}h left): {reason}")
