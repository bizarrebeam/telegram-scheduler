ALTER TABLE tasks
    ADD COLUMN deadline_date date,
    ADD COLUMN reminder_only boolean NOT NULL DEFAULT false,
    ADD COLUMN overdue_notified_at timestamptz;

UPDATE tasks
SET deadline_date = (deadline_at AT TIME ZONE 'Asia/Jakarta')::date
WHERE deadline_at IS NOT NULL;

CREATE TABLE deadline_reminders (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE CASCADE,
    event_date date NOT NULL,
    pending_date date,
    status text NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'approved', 'rejected',
                          'cancelled', 'pending_update')),
    google_event_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    approved_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX deadline_reminders_date_idx ON deadline_reminders (event_date);
CREATE INDEX deadline_reminders_status_idx ON deadline_reminders (status);

CREATE TRIGGER deadline_reminders_updated_at
    BEFORE UPDATE ON deadline_reminders
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
