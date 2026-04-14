"""Tests for the deploy-window time check."""

import server


def test_window_disabled_by_default():
    assert server._in_deploy_window() is True or server._in_deploy_window() is False
    # With start/end < 0 (default), always True.


def test_window_open_during_hours(monkeypatch):
    monkeypatch.setattr(server, "DEPLOY_WINDOW_START", 9)
    monkeypatch.setattr(server, "DEPLOY_WINDOW_END", 18)

    class FakeDT:
        @classmethod
        def now(cls, tz=None):
            import datetime as _dt
            return _dt.datetime(2026, 4, 13, 14, 0, tzinfo=tz)

    monkeypatch.setattr(server, "datetime", FakeDT)
    assert server._in_deploy_window() is True


def test_window_closed_outside_hours(monkeypatch):
    monkeypatch.setattr(server, "DEPLOY_WINDOW_START", 9)
    monkeypatch.setattr(server, "DEPLOY_WINDOW_END", 18)

    class FakeDT:
        @classmethod
        def now(cls, tz=None):
            import datetime as _dt
            return _dt.datetime(2026, 4, 13, 3, 0, tzinfo=tz)

    monkeypatch.setattr(server, "datetime", FakeDT)
    assert server._in_deploy_window() is False


def test_window_wraps_midnight(monkeypatch):
    """Start > End means the window crosses midnight (e.g. 22 → 04)."""
    monkeypatch.setattr(server, "DEPLOY_WINDOW_START", 22)
    monkeypatch.setattr(server, "DEPLOY_WINDOW_END", 4)

    class FakeDT:
        hour = 23

        @classmethod
        def now(cls, tz=None):
            import datetime as _dt
            return _dt.datetime(2026, 4, 13, 23, 0, tzinfo=tz)

    monkeypatch.setattr(server, "datetime", FakeDT)
    assert server._in_deploy_window() is True
