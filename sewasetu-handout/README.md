# Sewa Setu - Old Age Pension Portal

Inherited Flask/Jinja pension application, PostgreSQL database, scheduled deemed-approval job and simulated SMS gateway. Supported baseline journeys include application, receipt, status login and staff decisions.

This is a local baseline, not a verified public release. Known authorization, recovery, eligibility and identity defects remain. Runtime now requires private environment configuration and rejects inherited defaults. Public deployment and the remaining fixes are pending.

## Local baseline verification

Use Linux/WSL as an unprivileged user, Python 3.11, and PostgreSQL 15 server/client binaries (`postgres`, `initdb`, `pg_ctl`, `psql`). Docker is not needed for these commands. A running database is not needed and is never used. Install development dependencies in a local environment:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r app/requirements-dev.txt
PG_BIN_DIR=/usr/lib/postgresql/15/bin .venv/bin/python -m pytest -q
```

Set `PG_BIN_DIR` to your PostgreSQL 15 bin directory. For relocated package binaries, also set `PG_SHARE_DIR` to their matching PostgreSQL 15 share directory. Other major versions are rejected. The harness creates a fresh private Unix-socket cluster, loads only app/schema.sql, uses synthetic citizens and a loopback-only SMS gateway, and stops/deletes its own database after the run. It accepts no external database URL. The test clock is fixed before intake closure for reproducibility; the production clock and deadline are unchanged.

Tests exercise public routes, anonymous login boundaries, OTP through the actual gateway proxy, form steps, a generated PDF upload, submission, receipt, citizen status and a staff decision. They characterize supported inherited behavior; selected fixes still require separately committed failing regressions before implementation.

## Supplied-data restore and baseline measurements

Keep the original supplied seed/seed.sql private and ignored. This command verifies its SHA-256 and restores it into another newly initialized temporary PostgreSQL 15 cluster, with no scheduler or public network listener:

```sh
PG_BIN_DIR=/usr/lib/postgresql/15/bin .venv/bin/python tests/restore_baseline.py \
  --seed seed/seed.sql \
  --sha256 0738bde4c5cba055475328d740106844d6640f77ea02b1b703e629dbf231f320
```

It compares every application's and account's PostgreSQL COPY fields against the source, checks statuses and sequence state, measures 20 local status-detail and uncached-PDF requests, captures aggregate query-plan information, and reconciles again after read-only profiling. Only counts, digests and timings are printed; original citizen responses and PDFs are not printed or retained. The owned cluster and temporary artifacts are removed on normal completion/failure. It cannot restore into an existing database. These measurements are local characterization, not load or capacity claims.

The seed, log archives and briefing audio are supplied privately, not recreated or downloaded. Never replay the seed over a nonempty database, run the scheduler during baseline measurement, or delete an inherited database volume.

## Controlled submission replay

Run only on the privately supplied input with the PostgreSQL 15/Python 3.11 setup above:

```sh
PG_BIN_DIR=/usr/lib/postgresql/15/bin .venv/bin/python tests/replay_submission.py \
  --seed seed/seed.sql \
  --sha256 0738bde4c5cba055475328d740106844d6640f77ea02b1b703e629dbf231f320
```

The command owns a new temporary restored cluster, a four-worker loopback Gunicorn instance and a local SMS fixture; it accepts no external target URL and starts no scheduler. Synthetic applicants complete the actual captcha/OTP/form/upload routes. All 100,002 original applications/accounts are reconciled before and after; only synthetic records are added to the disposable copy. Tools are ordinary development dependencies, not extra production services.

The identifier-free scenario retains 13 submission/detail events from a 159-request minute. Each phase compresses the 60-second shape by 12; the middle phase adds four concurrent retries per event. This is a controlled subset/stress replay, not the complete production workload or a capacity claim. Authentication/preparation time is reported separately. Output contains latency/completion/error/connection/resource aggregates and redacted query-plan shapes; correctness failures cause exit 1 independently of timing. The uncorrected portal is expected to fail. Stop its owned processes and remove only their temporary directories on interruption; never delete an inherited volume.

## Supplied Compose topology

The supplied docker-compose.yml defines app, PostgreSQL 15, gateway and scheduler services. App runtime uses app/config/app.ini; the gateway's internal port is 8025 and its console/inbox are proxied through /__gateway/ on the app. No additional gateway port needs public exposure. Inherited admin/status entry points are /admin and /status.

Container builds, the full Compose topology, production configuration, migrations, restart/recovery and public deployment remain unverified. There are no new migrations in this baseline. Do not start the inherited Compose topology against real/restored data for measurements: it starts the statutory scheduler and publishes the app with known default credentials. Use the disposable commands above.

## Current limitations

Baseline checks have run on Python 3.11 and PostgreSQL 15 outside Docker. Access protection and OTP-based legacy-account continuity are implemented and privately tested. Statutory scheduling, durable submission, inclusive uploads, pending corrections and faithful Indic identity remain pending. The public fake SMS inbox is an assessment simulation and cannot provide real SMS confidentiality.

Local planning/agent notes are excluded by the user's ignore policy. This omits agent configuration and prompt evidence required by BRIEF.md; regression tests remain trackable and are not omitted from the repository.

## Access configuration and migrations

Copy `.env.example` to a private operator environment and generate independent signing/OTP secrets and database passwords. Generate the staff password hash with Werkzeug `generate_password_hash`; keep the password and populated environment outside Git. There are no default working staff credentials. A production process requires an HTTPS public origin, secure cookies and a restricted PostgreSQL login. `TRUST_PROXY=1` is only for one trusted private reverse proxy.

Use the separate database-owner environment for these commands; do not inject migration credentials into web workers:

```sh
.venv/bin/python app/manage.py migrate
.venv/bin/python app/manage.py rotate-roles
```

`MIGRATION_DATABASE_URL` identifies the existing restored database. Migration runs additive versioned SQL under an advisory lock; it never loads seed data. Rotation requires independent `MIGRATION_DATABASE_PASSWORD` and `RUNTIME_DATABASE_PASSWORD` (at least 32 characters), actually changes the owner's password, and configures the restricted `portal_runtime` role. Update the private owner URL afterward; set web `DATABASE_URL` to the new runtime role. Existing volumes require this operator rotation: changing Compose variables alone does not rotate database passwords. Rotation was verified only on an owned disposable restored copy; an actual deployment still needs its own run and verification.

Citizen tracking and recovery use delivered purpose-bound OTPs, including inherited accounts; original account hashes remain unchanged. A verified contact can access associated applications. Shared-phone users share contact access. The public assessment gateway exposes OTPs and provides no real SMS confidentiality. Logout revokes server-side sessions; recovery revokes previous citizen sessions. Staff and citizen authentication rotate opaque cookie identifiers. All form POSTs require CSRF tokens. Session idle/absolute limits are 30 minutes/two hours; OTP lifetime is five minutes. Staff role and citizen ownership are checked before receipt cache access.

The bounded per-process pool defaults to three connections plus one overflow with a two-second wait. Budget workers and jobs within PostgreSQL limits. The scheduler remains opt-in until its separate repair and validation. Submission resilience, final upload/form design, Unicode receipts, operations and public verification are still pending.
