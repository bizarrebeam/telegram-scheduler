import pytest

from app.config import get_settings


@pytest.fixture
def encryption_key(monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    yield key
    get_settings.cache_clear()


def test_encrypt_decrypt_roundtrip(encryption_key) -> None:
    from app.google.token_store import decrypt_text, encrypt_text

    payload = '{"refresh_token": "secret-value"}'
    ciphertext = encrypt_text(payload)
    assert isinstance(ciphertext, bytes)
    assert b"secret-value" not in ciphertext
    assert decrypt_text(ciphertext) == payload


def test_roundtrip_fails_with_wrong_key(encryption_key, monkeypatch) -> None:
    from cryptography.fernet import Fernet, InvalidToken

    from app.google.token_store import decrypt_text, encrypt_text

    ciphertext = encrypt_text("hello")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    with pytest.raises(InvalidToken):
        decrypt_text(ciphertext)
    get_settings.cache_clear()
