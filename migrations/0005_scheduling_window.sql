ALTER TABLE user_settings
    ALTER COLUMN weekday_start_time SET DEFAULT '08:00',
    ALTER COLUMN weekday_end_time SET DEFAULT '21:00',
    ALTER COLUMN weekend_enabled SET DEFAULT true;

UPDATE user_settings
SET weekday_start_time = '08:00',
    weekday_end_time = '21:00',
    weekend_enabled = true,
    weekend_availability = '{"start": "08:00", "end": "21:00"}'::jsonb;
