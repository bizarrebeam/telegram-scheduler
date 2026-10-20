from cryptography.fernet import Fernet

from app.db import get_conn


def _fernet() -> Fernet:
    from app.config import get_settings

    key = get_settings().token_encryption_key
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set")
    return Fernet(key.encode())


def encrypt_text(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt_text(ciphertext: bytes) -> str:
    return _fernet().decrypt(ciphertext).decode()


def save_credentials_json(telegram_user_id: int, creds_json: str, scopes: str) -> None:
    encrypted = encrypt_text(creds_json)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO google_credentials (telegram_user_id, encrypted, scopes)
            VALUES (%s, %s, %s)
            ON CONFLICT (telegram_user_id) DO UPDATE
            SET encrypted = EXCLUDED.encrypted,
                scopes = EXCLUDED.scopes,
                updated_at = now()
            """,
            (telegram_user_id, encrypted, scopes),
        )


def load_credentials_json(telegram_user_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT encrypted FROM google_credentials WHERE telegram_user_id = %s",
            (telegram_user_id,),
        ).fetchone()
    if row is None:
        return None
    return decrypt_text(row[0])
