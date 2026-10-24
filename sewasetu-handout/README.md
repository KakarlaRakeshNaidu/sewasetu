# Sewa Setu - Old Age Pension Portal

Inherited Flask/Jinja pension application, PostgreSQL database, scheduled deemed-approval job and simulated SMS gateway. Supported baseline journeys include application, receipt, status login and staff decisions.

This is a local baseline, not a verified public release. Known authorization, recovery, eligibility and identity defects remain. Published inherited configuration values are exercise defaults; no real production secrets have been generated. Public deployment and security fixes are pending.

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

Baseline checks have run on Python 3.11 and PostgreSQL 15 outside Docker. Authorization, secure legacy-account continuity, statutory scheduling, durable submission, uploads, pending corrections and faithful Indic identity require subsequent tested work. The public fake SMS inbox is an assessment simulation and cannot provide real SMS confidentiality.

Local planning/agent notes are excluded by the user's ignore policy. This omits agent configuration and prompt evidence required by BRIEF.md; regression tests remain trackable and are not omitted from the repository.
