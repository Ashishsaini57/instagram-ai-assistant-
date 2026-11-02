import hashlib
import hmac

from app.security import compute_signature, is_valid_signature, is_valid_verify_token

BODY = b'{"object":"instagram"}'


def test_signature_matches_meta_algorithm():
    expected = "sha256=" + hmac.new(b"secret", BODY, hashlib.sha256).hexdigest()
    assert compute_signature(BODY, "secret") == expected
    assert is_valid_signature(BODY, expected, "secret")


def test_invalid_signatures_rejected():
    good = compute_signature(BODY, "secret")
    assert not is_valid_signature(BODY, good, "other-secret")
    assert not is_valid_signature(BODY + b" ", good, "secret")
    assert not is_valid_signature(BODY, None, "secret")
    assert not is_valid_signature(BODY, good.removeprefix("sha256="), "secret")
    assert not is_valid_signature(BODY, "sha1=" + good[7:], "secret")
    assert not is_valid_signature(BODY, good, None)
    assert not is_valid_signature(BODY, good, "")


def test_verify_token():
    assert is_valid_verify_token("abc", "abc")
    assert not is_valid_verify_token("abc", "abd")
    assert not is_valid_verify_token(None, "abc")
    assert not is_valid_verify_token("abc", None)
