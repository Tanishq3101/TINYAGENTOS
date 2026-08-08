"""Smoke tests for infrastructure/security.py, validators.py, metrics.py."""

import time

import pytest


# --- security.py -----------------------------------------------------------

def test_security_manager_requires_explicit_key():
    from infrastructure.security import SecurityManager

    print("\n[test_security_manager_requires_explicit_key] constructing SecurityManager with empty key...")
    with pytest.raises(ValueError) as exc_info:
        SecurityManager(encryption_key="")
    print(f"[test_security_manager_requires_explicit_key] raised ValueError: {exc_info.value}")
    print("[test_security_manager_requires_explicit_key] PASSED")


def test_encrypt_decrypt_roundtrip():
    from cryptography.fernet import Fernet

    from infrastructure.security import SecurityManager

    print("\n[test_encrypt_decrypt_roundtrip] generating key and SecurityManager...")
    key = Fernet.generate_key().decode()
    mgr = SecurityManager(encryption_key=key)

    ciphertext = mgr.encrypt_sensitive_data("top secret")
    print(f"[test_encrypt_decrypt_roundtrip] ciphertext={ciphertext}")
    assert ciphertext != "top secret"

    plaintext = mgr.decrypt_sensitive_data(ciphertext)
    print(f"[test_encrypt_decrypt_roundtrip] decrypted={plaintext}")
    assert plaintext == "top secret"
    print("[test_encrypt_decrypt_roundtrip] PASSED")


def test_decrypt_with_wrong_key_raises():
    from cryptography.fernet import Fernet

    from infrastructure.security import SecurityManager

    print("\n[test_decrypt_with_wrong_key_raises] creating two managers with different keys...")
    mgr_a = SecurityManager(encryption_key=Fernet.generate_key().decode())
    mgr_b = SecurityManager(encryption_key=Fernet.generate_key().decode())

    ciphertext = mgr_a.encrypt_sensitive_data("hello")
    print(f"[test_decrypt_with_wrong_key_raises] ciphertext from mgr_a={ciphertext}")
    with pytest.raises(ValueError) as exc_info:
        mgr_b.decrypt_sensitive_data(ciphertext)
    print(f"[test_decrypt_with_wrong_key_raises] mgr_b raised ValueError: {exc_info.value}")
    print("[test_decrypt_with_wrong_key_raises] PASSED")


def test_api_key_hash_and_verify():
    from infrastructure.security import SecurityManager

    print("\n[test_api_key_hash_and_verify] generating API key and hash...")
    raw_key = SecurityManager.generate_api_key()
    stored_hash = SecurityManager.hash_api_key(raw_key)
    print(f"[test_api_key_hash_and_verify] raw_key={raw_key}")
    print(f"[test_api_key_hash_and_verify] stored_hash={stored_hash}")

    correct = SecurityManager.verify_api_key(raw_key, stored_hash)
    incorrect = SecurityManager.verify_api_key("wrong-key", stored_hash)
    print(f"[test_api_key_hash_and_verify] verify(correct key)={correct}")
    print(f"[test_api_key_hash_and_verify] verify(wrong key)={incorrect}")

    assert correct is True
    assert incorrect is False
    print("[test_api_key_hash_and_verify] PASSED")


def test_request_signature_roundtrip():
    from infrastructure.security import SecurityManager

    print("\n[test_request_signature_roundtrip] building HMAC signature for request body...")
    secret = "shared-secret"
    body = '{"task": "summarize"}'
    sig = SecurityManager.verify_request_signature  # noqa: F841 (kept for readability)

    import hashlib
    import hmac as hmac_module

    valid_sig = hmac_module.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    print(f"[test_request_signature_roundtrip] valid_sig={valid_sig}")

    valid_result = SecurityManager.verify_request_signature(body, valid_sig, secret)
    invalid_result = SecurityManager.verify_request_signature(body, "bad-sig", secret)
    print(f"[test_request_signature_roundtrip] verify(valid_sig)={valid_result}")
    print(f"[test_request_signature_roundtrip] verify('bad-sig')={invalid_result}")

    assert valid_result is True
    assert invalid_result is False
    print("[test_request_signature_roundtrip] PASSED")


# --- validators.py -----------------------------------------------------------

def test_task_input_sanitizes_whitespace_and_nulls():
    from infrastructure.validators import TaskInput

    print("\n[test_task_input_sanitizes_whitespace_and_nulls] constructing TaskInput with nulls/whitespace...")
    task = TaskInput(text="hello \x00  world\n\n  again")
    print(f"[test_task_input_sanitizes_whitespace_and_nulls] sanitized text={task.text!r}")
    assert task.text == "hello world again"
    print("[test_task_input_sanitizes_whitespace_and_nulls] PASSED")


def test_task_input_rejects_empty_text():
    from infrastructure.validators import TaskInput

    print("\n[test_task_input_rejects_empty_text] constructing TaskInput with empty text...")
    with pytest.raises(Exception) as exc_info:
        TaskInput(text="")
    print(f"[test_task_input_rejects_empty_text] raised: {exc_info.value}")
    print("[test_task_input_rejects_empty_text] PASSED")


def test_task_input_rejects_invalid_task_type():
    from infrastructure.validators import TaskInput

    print("\n[test_task_input_rejects_invalid_task_type] constructing TaskInput with task_type='not_a_real_type'...")
    with pytest.raises(Exception) as exc_info:
        TaskInput(text="hello", task_type="not_a_real_type")
    print(f"[test_task_input_rejects_invalid_task_type] raised: {exc_info.value}")
    print("[test_task_input_rejects_invalid_task_type] PASSED")


def test_task_input_priority_bounds():
    from infrastructure.validators import TaskInput

    print("\n[test_task_input_priority_bounds] constructing TaskInput with priority=11...")
    with pytest.raises(Exception) as exc_info:
        TaskInput(text="hello", priority=11)
    print(f"[test_task_input_priority_bounds] raised: {exc_info.value}")
    print("[test_task_input_priority_bounds] PASSED")


def test_pagination_defaults_and_bounds():
    from infrastructure.validators import PaginationParams

    print("\n[test_pagination_defaults_and_bounds] constructing PaginationParams with defaults...")
    default = PaginationParams()
    print(f"[test_pagination_defaults_and_bounds] default.page={default.page}, default.page_size={default.page_size}")
    assert default.page == 1
    assert default.page_size == 20

    print("[test_pagination_defaults_and_bounds] constructing PaginationParams with page_size=1000...")
    with pytest.raises(Exception) as exc_info:
        PaginationParams(page_size=1000)
    print(f"[test_pagination_defaults_and_bounds] raised: {exc_info.value}")
    print("[test_pagination_defaults_and_bounds] PASSED")


# --- metrics.py -----------------------------------------------------------

def test_metrics_collector_tracks_agent_execution():
    from infrastructure.metrics import MetricsCollector

    print("\n[test_metrics_collector_tracks_agent_execution] starting agent metrics for 'summarizer'...")
    collector = MetricsCollector()
    m = collector.start_agent_metrics("summarizer")
    time.sleep(0.01)
    m.finalize()
    print(f"[test_metrics_collector_tracks_agent_execution] execution_time_ms={m.execution_time_ms}")

    assert m.execution_time_ms is not None
    assert m.execution_time_ms > 0

    summary = collector.get_pipeline_summary()
    print(f"[test_metrics_collector_tracks_agent_execution] summary={summary}")
    assert summary["agent_count"] == 1
    assert summary["error_count"] == 0
    assert summary["agents"][0]["name"] == "summarizer"
    print("[test_metrics_collector_tracks_agent_execution] PASSED")


def test_metrics_collector_tracks_errors():
    from infrastructure.metrics import MetricsCollector

    print("\n[test_metrics_collector_tracks_errors] starting agent metrics for 'extractor' with error...")
    collector = MetricsCollector()
    m = collector.start_agent_metrics("extractor")
    m.finalize(error="timeout")

    summary = collector.get_pipeline_summary()
    print(f"[test_metrics_collector_tracks_errors] summary={summary}")
    assert summary["error_count"] == 1
    assert summary["agents"][0]["error"] == "timeout"
    print("[test_metrics_collector_tracks_errors] PASSED")


def test_metrics_collector_reset():
    from infrastructure.metrics import MetricsCollector

    print("\n[test_metrics_collector_reset] recording one metric then resetting...")
    collector = MetricsCollector()
    collector.start_agent_metrics("critic").finalize()
    print(f"[test_metrics_collector_reset] metrics before reset: {collector.metrics}")
    collector.reset()
    print(f"[test_metrics_collector_reset] metrics after reset: {collector.metrics}")

    assert collector.metrics == []
    print("[test_metrics_collector_reset] PASSED")