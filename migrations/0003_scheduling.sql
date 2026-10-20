CREATE TABLE routines (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title text NOT NULL,
    description text,
    session_duration_minutes int NOT NULL CHECK (session_duration_minutes > 0),
    target_sessions_per_week int NOT NULL CHECK (target_sessions_per_week > 0),
    active_days smallint[] NOT NULL DEFAULT '{1,2,3,4,5}',
    available_from time,
    available_until time,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE tasks
    ADD CONSTRAINT tasks_routine_fk FOREIGN KEY (routine_id) REFERENCES routines(id);

CREATE TABLE task_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL REFERENCES tasks(id),
    planned_start_at timestamptz NOT NULL,
    planned_end_at timestamptz NOT NULL,
    actual_start_at timestamptz,
    actual_end_at timestamptz,
    planned_duration_minutes int NOT NULL CHECK (planned_duration_minutes > 0),
    additional_duration_minutes int NOT NULL DEFAULT 0 CHECK (additional_duration_minutes >= 0),
    status text NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'in_progress', 'completed', 'missed', 'cancelled', 'superseded')),
    calendar_event_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (planned_end_at > planned_start_at)
);

CREATE INDEX task_sessions_task_idx ON task_sessions (task_id);
CREATE INDEX task_sessions_status_idx ON task_sessions (status);

CREATE TRIGGER task_sessions_updated_at
    BEFORE UPDATE ON task_sessions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE schedule_proposals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL REFERENCES tasks(id),
    session_id uuid REFERENCES task_sessions(id),
    proposed_start_at timestamptz NOT NULL,
    proposed_end_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'superseded', 'failed')),
    expires_at timestamptz NOT NULL,
    approved_at timestamptz,
    rejected_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (proposed_end_at > proposed_start_at)
);

CREATE UNIQUE INDEX schedule_proposals_one_pending_per_task
    ON schedule_proposals (task_id) WHERE status = 'pending';
CREATE INDEX schedule_proposals_status_idx ON schedule_proposals (status, expires_at);

CREATE TRIGGER schedule_proposals_updated_at
    BEFORE UPDATE ON schedule_proposals
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE calendar_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_session_id uuid NOT NULL UNIQUE REFERENCES task_sessions(id),
    google_calendar_id text NOT NULL,
    google_event_id text NOT NULL UNIQUE,
    event_status text NOT NULL DEFAULT 'confirmed',
    last_synced_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER calendar_events_updated_at
    BEFORE UPDATE ON calendar_events
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE task_attachments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL REFERENCES tasks(id),
    telegram_file_id text NOT NULL,
    drive_file_id text,
    file_type text NOT NULL,
    original_filename text,
    private_file_url text,
    upload_status text NOT NULL DEFAULT 'stored_telegram',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX task_attachments_task_idx ON task_attachments (task_id);

CREATE TABLE reminder_state (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_session_id uuid NOT NULL REFERENCES task_sessions(id),
    reminder_type text NOT NULL
        CHECK (reminder_type IN ('session_start', 'pre_end', 'session_end', 'missed_start', 'missed_followup', 'proposal_expiry')),
    scheduled_at timestamptz NOT NULL,
    sent_at timestamptz,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sent', 'skipped', 'failed')),
    followup_sent_at timestamptz,
    response_received_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_session_id, reminder_type)
);

CREATE TRIGGER reminder_state_updated_at
    BEFORE UPDATE ON reminder_state
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE task_history (
    id bigserial PRIMARY KEY,
    task_id uuid REFERENCES tasks(id),
    session_id uuid REFERENCES task_sessions(id),
    event_type text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX task_history_task_idx ON task_history (task_id, created_at DESC);

CREATE TABLE job_queue (
    id bigserial PRIMARY KEY,
    kind text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    run_at timestamptz NOT NULL DEFAULT now(),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'done', 'failed')),
    attempts int NOT NULL DEFAULT 0,
    last_error text,
    idempotency_key text UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX job_queue_due_idx ON job_queue (status, run_at);

CREATE TRIGGER job_queue_updated_at
    BEFORE UPDATE ON job_queue
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
