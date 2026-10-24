BEGIN;
ALTER TABLE application_drafts ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE application_drafts ADD COLUMN kind TEXT NOT NULL DEFAULT 'apply';
ALTER TABLE application_drafts ADD COLUMN application_id INTEGER REFERENCES applications(id);
ALTER TABLE application_drafts ADD COLUMN proof_at TIMESTAMPTZ;
CREATE INDEX application_drafts_owner ON application_drafts(mobile,touched_at DESC);
-- Remove redundant completed payloads; keep owned saved drafts and stable result keys.
UPDATE application_drafts SET data='{}' WHERE submitted_application_id IS NOT NULL;
UPDATE web_sessions SET data=data-'form_data'-'doc_path';
COMMIT;
