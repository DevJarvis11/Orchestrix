"""Dev-mode password hashing (scrypt, salted)."""

from orchestrix_core.db.repos.dev_logins import hash_password, verify_password


def test_hash_verifies_only_the_right_password() -> None:
    stored = hash_password("bob@123")
    assert "bob@123" not in stored
    assert verify_password("bob@123", stored)
    assert not verify_password("bob@124", stored)


def test_hashes_are_salted_and_garbage_is_rejected() -> None:
    assert hash_password("x") != hash_password("x")
    assert not verify_password("x", "not-a-hash")
