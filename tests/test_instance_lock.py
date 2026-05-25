"""Tests for core/instance_lock.py — advisory file locking."""

from __future__ import annotations

from pathlib import Path

from core.instance_lock import InstanceLock


def test_acquire_release(tmp_path: Path) -> None:
    import core.instance_lock as ilm

    orig = ilm._LOCK_PATH
    ilm._LOCK_PATH = tmp_path / "instance.lock"
    try:
        lock_a = InstanceLock()
        lock_b = InstanceLock()
        assert lock_a.acquire() is True
        # Second *different* instance should fail
        assert lock_b.acquire() is False
        lock_a.release()
        # After release lock_b can acquire
        assert lock_b.acquire() is True
        lock_b.release()
    finally:
        ilm._LOCK_PATH = orig


def test_double_release_does_not_raise(tmp_path: Path) -> None:
    lock = InstanceLock()
    import core.instance_lock as ilm

    orig = ilm._LOCK_PATH
    ilm._LOCK_PATH = tmp_path / "instance.lock"
    try:
        lock.acquire()
        lock.release()
        lock.release()  # second release — should be a no-op
    finally:
        ilm._LOCK_PATH = orig
