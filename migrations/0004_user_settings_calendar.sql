ALTER TABLE user_settings
    ADD COLUMN google_calendar_id text NOT NULL DEFAULT 'primary';
