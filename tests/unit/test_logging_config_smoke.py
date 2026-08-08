"""Smoke tests for infrastructure/logging.py and infrastructure/config.py."""

import os

import pytest


def test_setup_logger_returns_same_instance_and_no_duplicate_handlers():
    from infrastructure.logging import setup_logger

    print("\n[test_setup_logger_returns_same_instance_and_no_duplicate_handlers] calling setup_logger() twice...")
    logger_a = setup_logger()
    handler_count_after_first = len(logger_a.handlers)
    logger_b = setup_logger()
    handler_count_after_second = len(logger_b.handlers)

    print(f"[test_setup_logger_returns_same_instance_and_no_duplicate_handlers] logger_a is logger_b: {logger_a is logger_b}")
    print(f"[test_setup_logger_returns_same_instance_and_no_duplicate_handlers] handlers after 1st call: {handler_count_after_first}, after 2nd call: {handler_count_after_second}")

    assert logger_a is logger_b  # module-level singleton
    assert handler_count_after_first == handler_count_after_second  # guard prevents duplicate handlers
    print("[test_setup_logger_returns_same_instance_and_no_duplicate_handlers] PASSED")


def test_logger_writes_to_log_file():
    from infrastructure.logging import log_info, logger

    # The RotatingFileHandler writes to logs/app.log relative to the cwd at
    # first import of infrastructure.logging (handlers are attached once,
    # at import time) — not something a per-test monkeypatch can redirect.
    log_file_path = None
    for handler in logger.handlers:
        if hasattr(handler, "baseFilename"):
            log_file_path = handler.baseFilename
            break

    print(f"\n[test_logger_writes_to_log_file] resolved log file path: {log_file_path}")
    assert log_file_path is not None
    assert os.path.exists(log_file_path)

    # The file accumulates across every run of this app (10MB rotating log),
    # so old sessions may contain bytes that aren't valid UTF-8 (e.g. from a
    # legacy console fallback path). Only read what THIS test appends, so
    # pre-existing history in the file can never fail this assertion.
    offset_before = os.path.getsize(log_file_path)
    print(f"[test_logger_writes_to_log_file] file size before write: {offset_before} bytes")

    unique_message = "smoke-test-marker-8f3d2a"
    log_info(unique_message, task_id="abc123")

    with open(log_file_path, "rb") as f:
        f.seek(offset_before)
        new_bytes = f.read()
    new_content = new_bytes.decode("utf-8", errors="replace")

    print(f"[test_logger_writes_to_log_file] newly appended content={new_content!r}")
    assert unique_message in new_content
    print("[test_logger_writes_to_log_file] PASSED")


def test_settings_rejects_missing_secret_key(monkeypatch):
    from infrastructure.config import Settings

    print("\n[test_settings_rejects_missing_secret_key] clearing SECRET_KEY env var...")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(Exception) as exc_info:
        Settings(_env_file=None)
    print(f"[test_settings_rejects_missing_secret_key] raised: {exc_info.value}")
    print("[test_settings_rejects_missing_secret_key] PASSED")


def test_settings_rejects_weak_secret_key(monkeypatch):
    from infrastructure.config import Settings

    print("\n[test_settings_rejects_weak_secret_key] setting a short/weak SECRET_KEY...")
    monkeypatch.setenv("SECRET_KEY", "too-short")
    with pytest.raises(Exception) as exc_info:
        Settings(_env_file=None)
    print(f"[test_settings_rejects_weak_secret_key] raised: {exc_info.value}")
    print("[test_settings_rejects_weak_secret_key] PASSED")


def test_settings_accepts_strong_secret_key(monkeypatch):
    from infrastructure.config import Settings

    print("\n[test_settings_accepts_strong_secret_key] setting a 64-char SECRET_KEY...")
    strong_secret = "x" * 64
    monkeypatch.setenv("SECRET_KEY", strong_secret)
    settings = Settings(_env_file=None)
    print(f"[test_settings_accepts_strong_secret_key] settings.SECRET_KEY matches input: {settings.SECRET_KEY == strong_secret}")
    print(f"[test_settings_accepts_strong_secret_key] settings.PORT={settings.PORT}")
    assert settings.SECRET_KEY == strong_secret
    assert settings.PORT == 8000  # default applied
    print("[test_settings_accepts_strong_secret_key] PASSED")