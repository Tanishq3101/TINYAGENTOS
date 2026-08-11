"""Smoke tests for infrastructure/auth.py (password hashing + JWT tokens)."""

import time

# --- password hashing -----------------------------------------------------


def test_hash_password_and_verify_correct_password():
    from infrastructure.auth import hash_password, verify_password

    print(
        "\n[test_hash_password_and_verify_correct_password] hashing 'correct-horse-battery-staple'..."
    )
    plain = "correct-horse-battery-staple"
    hashed = hash_password(plain)
    print(f"[test_hash_password_and_verify_correct_password] hashed={hashed}")

    assert hashed != plain

    result = verify_password(plain, hashed)
    print(f"[test_hash_password_and_verify_correct_password] verify(correct password)={result}")
    assert result is True
    print("[test_hash_password_and_verify_correct_password] PASSED")


def test_verify_password_rejects_wrong_password():
    from infrastructure.auth import hash_password, verify_password

    print(
        "\n[test_verify_password_rejects_wrong_password] hashing then verifying with a wrong password..."
    )
    hashed = hash_password("the-real-password")
    result = verify_password("not-the-real-password", hashed)
    print(f"[test_verify_password_rejects_wrong_password] verify(wrong password)={result}")
    assert result is False
    print("[test_verify_password_rejects_wrong_password] PASSED")


def test_hash_password_produces_different_hashes_for_same_input():
    from infrastructure.auth import hash_password, verify_password

    print(
        "\n[test_hash_password_produces_different_hashes_for_same_input] hashing the same password twice..."
    )
    hash_a = hash_password("same-password")
    hash_b = hash_password("same-password")
    print(f"[test_hash_password_produces_different_hashes_for_same_input] hash_a={hash_a}")
    print(f"[test_hash_password_produces_different_hashes_for_same_input] hash_b={hash_b}")

    # bcrypt salts each hash, so two hashes of the same password should differ...
    assert hash_a != hash_b
    # ...but both must still verify correctly against the original password.
    assert verify_password("same-password", hash_a) is True
    assert verify_password("same-password", hash_b) is True
    print("[test_hash_password_produces_different_hashes_for_same_input] PASSED")


# --- JWT token handling -----------------------------------------------------


def test_create_and_decode_access_token_roundtrip():
    from infrastructure.auth import create_access_token, decode_access_token

    print("\n[test_create_and_decode_access_token_roundtrip] creating token for sub='user-123'...")
    token = create_access_token({"sub": "user-123", "role": "admin"})
    print(f"[test_create_and_decode_access_token_roundtrip] token={token}")

    claims = decode_access_token(token)
    print(f"[test_create_and_decode_access_token_roundtrip] decoded claims={claims}")

    assert claims is not None
    assert claims["sub"] == "user-123"
    assert claims["role"] == "admin"
    assert "exp" in claims
    print("[test_create_and_decode_access_token_roundtrip] PASSED")


def test_create_access_token_does_not_mutate_input_dict():
    from infrastructure.auth import create_access_token

    print(
        "\n[test_create_access_token_does_not_mutate_input_dict] creating token from a caller-owned dict..."
    )
    original_data = {"sub": "user-456"}
    create_access_token(original_data)
    print(
        f"[test_create_access_token_does_not_mutate_input_dict] original_data after call={original_data}"
    )

    assert "exp" not in original_data
    assert original_data == {"sub": "user-456"}
    print("[test_create_access_token_does_not_mutate_input_dict] PASSED")


def test_decode_access_token_rejects_garbage_token():
    from infrastructure.auth import decode_access_token

    print("\n[test_decode_access_token_rejects_garbage_token] decoding a malformed token string...")
    result = decode_access_token("this-is-not-a-real-jwt")
    print(f"[test_decode_access_token_rejects_garbage_token] result={result}")
    assert result is None
    print("[test_decode_access_token_rejects_garbage_token] PASSED")


def test_decode_access_token_rejects_expired_token():
    from datetime import timedelta

    from infrastructure.auth import create_access_token, decode_access_token

    print(
        "\n[test_decode_access_token_rejects_expired_token] creating a token that expired 1 second ago..."
    )
    token = create_access_token({"sub": "user-789"}, expires_delta=timedelta(seconds=-1))
    time.sleep(0.05)  # make sure we're safely past expiry before decoding

    result = decode_access_token(token)
    print(f"[test_decode_access_token_rejects_expired_token] result={result}")
    assert result is None
    print("[test_decode_access_token_rejects_expired_token] PASSED")


def test_create_access_token_respects_custom_expires_delta():
    from datetime import datetime, timedelta, timezone

    from infrastructure.auth import create_access_token, decode_access_token

    print(
        "\n[test_create_access_token_respects_custom_expires_delta] creating token with a 5-minute custom expiry..."
    )
    before = datetime.now(timezone.utc)
    token = create_access_token({"sub": "user-custom-exp"}, expires_delta=timedelta(minutes=5))
    claims = decode_access_token(token)

    exp_timestamp = claims["exp"]
    exp_datetime = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
    delta_seconds = (exp_datetime - before).total_seconds()
    print(
        f"[test_create_access_token_respects_custom_expires_delta] exp is ~{delta_seconds:.1f}s from creation"
    )

    # Should land close to 300s (5 min) out, with generous slack for test runtime jitter.
    assert 290 <= delta_seconds <= 310
    print("[test_create_access_token_respects_custom_expires_delta] PASSED")
