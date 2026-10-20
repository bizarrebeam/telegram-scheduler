CREATE TABLE google_credentials (
    telegram_user_id bigint PRIMARY KEY,
    encrypted bytea NOT NULL,
    scopes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER google_credentials_updated_at
    BEFORE UPDATE ON google_credentials
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
