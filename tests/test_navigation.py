"""Tests for navigation decision logic."""

from __future__ import annotations

import datetime

import core.navigation as navigation
from core.navigation import NavigationController


class TestComputeDefaultFilter:
    def test_prefers_today_when_task_due_today(self, monkeypatch) -> None:
        class FixedDate(datetime.date):
            @classmethod
            def today(cls) -> datetime.date:
                return cls(2026, 5, 27)

        monkeypatch.setattr(navigation.datetime, "date", FixedDate)

        result = NavigationController._compute_default_filter(
            [{"deadline": "2026-05-27 14:30"}],
            start_week_on_sunday=False,
        )

        assert result == "today"

    def test_uses_week_when_no_today_task_but_due_this_week(self, monkeypatch) -> None:
        class FixedDate(datetime.date):
            @classmethod
            def today(cls) -> datetime.date:
                return cls(2026, 5, 27)

        monkeypatch.setattr(navigation.datetime, "date", FixedDate)

        result = NavigationController._compute_default_filter(
            [{"deadline": "2026-05-29"}],
            start_week_on_sunday=False,
        )

        assert result == "week"

    def test_uses_all_when_no_tasks_due_this_week(self, monkeypatch) -> None:
        class FixedDate(datetime.date):
            @classmethod
            def today(cls) -> datetime.date:
                return cls(2026, 5, 27)

        monkeypatch.setattr(navigation.datetime, "date", FixedDate)

        result = NavigationController._compute_default_filter(
            [{"deadline": "2026-06-10"}, {"deadline": None}],
            start_week_on_sunday=False,
        )

        assert result == "all"
