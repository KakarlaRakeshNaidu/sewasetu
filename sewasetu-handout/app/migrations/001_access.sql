-- Additive access migration; inherited citizen values are unchanged.
BEGIN;
CREATE TABLE web_sessions (
 token_hash TEXT PRIMARY KEY, data JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL,
 touched_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL, revoked BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX web_sessions_expiry ON web_sessions(expires_at);
CREATE TABLE portal_challenges (
 id TEXT PRIMARY KEY, mobile VARCHAR(15) NOT NULL, purpose TEXT NOT NULL,
 code_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, consumed BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX portal_challenges_contact ON portal_challenges(mobile,created_at DESC);
CREATE TABLE application_drafts (
 id TEXT PRIMARY KEY, mobile VARCHAR(15) NOT NULL, data JSONB NOT NULL DEFAULT '{}',
 touched_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE application_audit (
 id BIGSERIAL PRIMARY KEY, application_id INTEGER NOT NULL REFERENCES applications(id),
 event TEXT NOT NULL, actor TEXT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL,
 changed_values JSONB NOT NULL DEFAULT '{}'
);
ALTER TABLE portal_users ALTER COLUMN password_hash TYPE VARCHAR(255);
COMMIT;
