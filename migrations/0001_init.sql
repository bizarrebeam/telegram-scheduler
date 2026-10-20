CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE user_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_user_id bigint NOT NULL UNIQUE,
    timezone text NOT NULL DEFAULT 'Asia/Jakarta',
    weekday_start_time time NOT NULL DEFAULT '20:00',
    weekday_end_time time NOT NULL DEFAULT '23:00',
    weekend_enabled boolean NOT NULL DEFAULT false,
    weekend_availability jsonb,
    reminder_preferences jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (weekday_end_time > weekday_start_time)
);

CREATE TRIGGER user_settings_updated_at
    BEFORE UPDATE ON user_settings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE tasks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_task_id uuid REFERENCES tasks(id),
    title text NOT NULL,
    description text,
    category text NOT NULL DEFAULT 'other'
        CHECK (category IN ('work', 'learning', 'personal_admin', 'routine', 'leisure', 'other')),
    priority smallint NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
    deadline_at timestamptz,
    estimated_duration_minutes int NOT NULL CHECK (estimated_duration_minutes > 0),
    duration_source text NOT NULL DEFAULT 'estimate'
        CHECK (duration_source IN ('user', 'estimate', 'learned')),
    first_step text,
    status text NOT NULL DEFAULT 'captured'
        CHECK (status IN ('captured', 'pending', 'scheduled', 'in_progress', 'completed', 'cancelled', 'skipped')),
    routine_id uuid,
    source_telegram_message_id bigint,
    source_telegram_update_id bigint UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CHECK ((status = 'completed') = (completed_at IS NOT NULL))
);

CREATE INDEX tasks_status_idx ON tasks (status);
CREATE INDEX tasks_parent_idx ON tasks (parent_task_id);

CREATE TRIGGER tasks_updated_at
    BEFORE UPDATE ON tasks
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
