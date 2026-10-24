BEGIN;
CREATE TABLE notification_outbox (
 id BIGSERIAL PRIMARY KEY, event_key TEXT NOT NULL UNIQUE,
 application_id INTEGER NOT NULL REFERENCES applications(id), mobile VARCHAR(15) NOT NULL,
 body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING', attempts INTEGER NOT NULL DEFAULT 0,
 gateway_id BIGINT, delivery_checks INTEGER NOT NULL DEFAULT 0, next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(), lease_until TIMESTAMPTZ,
 last_error TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), delivered_at TIMESTAMPTZ
);
CREATE INDEX notification_outbox_due ON notification_outbox(next_attempt_at,id) WHERE status IN ('PENDING','QUEUED');
COMMIT;
