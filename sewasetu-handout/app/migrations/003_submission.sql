BEGIN;
ALTER TABLE application_drafts ADD COLUMN submission_key TEXT;
UPDATE application_drafts SET submission_key=id;
ALTER TABLE application_drafts ALTER COLUMN submission_key SET NOT NULL;
ALTER TABLE application_drafts ADD CONSTRAINT draft_submission_key_unique UNIQUE(submission_key);
ALTER TABLE application_drafts ADD COLUMN submitted_application_id INTEGER REFERENCES applications(id);
ALTER TABLE applications ADD COLUMN receipt_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE applications ADD CONSTRAINT applications_number_unique UNIQUE(application_no);
-- Justified by restored-copy sequential scans for the two existing lookup/list shapes.
CREATE INDEX applications_mobile_submitted ON applications(mobile,submitted_at DESC,id DESC);
CREATE INDEX applications_status_submitted ON applications(status,submitted_at,id);
COMMIT;
