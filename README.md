# Sewa Setu

Old-age pension applications, OTP tracking/recovery, private receipts/documents, pending corrections and staff decisions. The inherited Flask/Jinja/PostgreSQL application and its routes are retained. Local CSS/progressive JavaScript supports a complete no-JavaScript journey. SQLAlchemy provides bounded PostgreSQL pools; a transactional outbox drives the required simulated SMS gateway. Indic receipts use fpdf2, HarfBuzz and bundled OFL-licensed Noto fonts.

The verified target is **localhost**, as requested by the operator. VM/public TLS deployment, off-host retention/alerts and assessment submission remain operator tasks. A local Docker Desktop/WSL installation does not provide continuous public availability or high availability.

New applications close at the exclusive instant **1 November 2026, 00:00 IST** (31 October is included). `SCHEME_DEADLINE` is configurable; no extension or assessor bypass is installed. Tracking, receipts, staff and `/__gateway/` remain available after closure.

## Ranked fixes and evidence

| Rank / citizen impact | Failing test commit | Passing fix commit | Retained checks |
| --- | --- | --- | --- |
| 1. Protect private records and staff decisions; prevent unauthorized reads/writes | `3631657` | `e3bbda3` | `tests/test_security.py`, `test_security_prerequisites.py` |
| 2. Restore overdue statutory decisions; preserve manual decisions and notify once | `1dbc891` | `1412a70` | `tests/test_deemed_approval.py`, `test_outbox.py` |
| 3. Accept each intent once despite PDF/SMS outages; keep tracking usable under retries | `46dbb9a` | `7c6327a` | `tests/test_submission.py`, `replay_submission.py` |
| 4. Let widows/shared-phone adults complete, resume and safely correct pending applications | `3155c13` | `81e8018` | `tests/test_citizen.py`, `browser_journey.py` |
| 5. Preserve accepted Indic names, bank strings and deterministic DOBs across all layers | `2df372f`, `0d765aa` | `bcc3af0` | `tests/test_identity.py` |

Additional infrastructure regressions precede their repairs: UTC-host IST birthday `3753a70`; stale session responses `3466907`; concurrent pool initialization `71de742`; slow-batch outbox leases `c09ed51` → `dd6d57f`. Shared validation made some date/length guards pass during rank 4; those cases are preservation tests, not fabricated later failures. Full history is retained.

An isolated 100,002-row restore reconciled original application/account COPY checksums and sequence before/after migrations and read-only profiling. A controlled 13-event local replay with four concurrent retries improved the burst from 3/20 correct responses and 9 applications for 5 intents to 20/20 and exactly 5 applications. Submission burst p95 was 55.147 → 39.491 ms; tracking 26.702 → 25.048 ms; peak observed DB connections 8 → 4. This small controlled scenario is not a production capacity claim. Later Unicode receipt profiling measured uncached PDF p95 135.498 ms across 20 local requests. Reproduce and report your own hardware/release results.

## Private inputs and fresh installation

Run from `sewasetu-handout/sewasetu-handout` on Linux/WSL with Docker Compose v2+, Python 3.11+, PostgreSQL 15 binaries for native tests, and GnuPG for backups. The original `seed/seed.sql`, log archive and briefing audio were supplied privately. They are ignored and excluded from images/ZIP; do not invent replacement citizen data. The seed SHA-256 is `0738bde4c5cba055475328d740106844d6640f77ea02b1b703e629dbf231f320`.

For a **new checkout only**:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r app/requirements-dev.txt
.venv/bin/python ops/configure.py
python3 ops/preflight.py
export RELEASE_SHA="$(git rev-parse HEAD)"
docker compose build app smsgw
docker compose up -d db
docker compose --profile operator run --rm migrate
docker compose --profile operator run --rm migrate python /app/manage.py rotate-roles
docker compose --profile scheduler up -d --wait
```

`configure.py` refuses to overwrite `.env`, asks for a new staff password and generates independent signing, OTP, owner and runtime database secrets. Keep the environment/password outside Git. `.env.example` lists the contract; Compose single quotes preserve password-hash dollar signs. The migration account is supplied only to the operator service, never app/jobs. `rotate-roles` actually changes DB role passwords, denies runtime administrative privileges and grants limited table rights. Changing environment values alone does not rotate an existing database role.

The database entrypoint loads schema/seed **once into a fresh volume**. Missing seed mounts fail rather than creating a substitute directory. Never replay the seed over a nonempty volume, reset an inherited/restored DB or run `docker compose down -v`. Existing deployments reuse the database/uploads/gateway volumes. Additive migrations run under an advisory lock; existing IDs, account hashes, strings and timestamps are preserved.

The automatic `docker-compose.override.yml` provides local HTTP at [127.0.0.1:8000](http://127.0.0.1:8000) with local cookies/proxy settings. It does not weaken the explicit production file. Login at `/admin` with the private configured staff credentials. Citizens verify a code through `/status` or `/status/reset`; inherited hashes are preserved and DOB alone is never sufficient. `/__gateway/` shows the simulation inbox; there is no second published gateway port.

## Daily management and releases

```sh
docker compose ps
docker compose logs -f app
docker compose stop
docker compose up -d
docker compose restart app
# Source is bundled in the image: restart alone does not install edits.
docker compose up -d --build app
```

Use `python3 ops/release.py` after reviewing/committing a clean working tree for a release identified by the actual Git SHA. It takes a current encrypted backup, builds pinned images, verifies baked source against committed blobs, applies migrations, starts healthy local services/scheduler, checks `/health/ready` against that SHA and saves only the public release identifier in private `.env`. It performs no Git mutations. `/health/live` tests the process; `/health/ready` checks database schema access, uploads and gateway. Both expose only status/release and create no session. Docker restarts failed containers; JSON logs rotate at 3 × 10 MB per service. Docker health status alone does not automatically restart a merely unhealthy process.

`app` and gateway run as UID 10001 with read-only image filesystems and private writable volumes. A secret-free one-shot init service sets volume ownership. Cron remains privileged to start jobs; job launchers drop to UID 10001 before business code. Runtime images contain source/templates/assets/fonts/licenses, pinned production requirements, schema/migrations and operations entry points; no tests, dev tools, seed, logs, credentials or backups.

## Tests and reproducible evidence

```sh
PG_BIN_DIR=/usr/lib/postgresql/15/bin .venv/bin/python -B -m pytest -q
PG_BIN_DIR=/usr/lib/postgresql/15/bin .venv/bin/python -B tests/restore_baseline.py \
  --seed seed/seed.sql --sha256 0738bde4c5cba055475328d740106844d6640f77ea02b1b703e629dbf231f320
PG_BIN_DIR=/usr/lib/postgresql/15/bin .venv/bin/python -B tests/replay_submission.py \
  --seed seed/seed.sql --sha256 0738bde4c5cba055475328d740106844d6640f77ea02b1b703e629dbf231f320
.venv/bin/python -m pip install -r tests/requirements-browser.txt
.venv/bin/python -m playwright install chromium
PG_BIN_DIR=/usr/lib/postgresql/15/bin .venv/bin/python -B tests/browser_journey.py
```

The final native core run passed **152 tests in 156.16 s** on Python3.12.3/PostgreSQL15.19; the actual Docker Python3.11 service also passed the retained localhost browser journey. Native tests own disposable PostgreSQL 15 Unix-socket clusters with no TCP listener; no SQLite substitute or real citizen fixtures. Use `PG_SHARE_DIR` only for relocated PostgreSQL packages. Restore/replay tools accept no external target and reconcile every original row/account/sequence; the scheduler is disabled during measurement. Tools include pytest, Playwright/Chromium, pypdf, PostgreSQL COPY/EXPLAIN and ordinary resource counters.

For the actual localhost Docker service, `tests/live_local_journey.py --admin-file PRIVATE_JSON --output PRIVATE_DIRECTORY` uses a private JSON object with `username`/`password`. It respects the real resend cooldown, keeps one labeled synthetic application and checks Indic DB/HTML/PDF, widow/no-JS/mobile, configured staff, inherited OTP continuity, owner correction/SLA and negative access. Keep generated files ignored/private.

To prove real cron without altering the host clock:

```sh
docker build -f tests/Dockerfile.scheduler-verify -t sewasetu-scheduler-verify:local tests
.venv/bin/python -B tests/verify_scheduler.py
```

This test-only image adds libfaketime to an isolated scheduler container; the real daemon/schedule/entrypoint run against a fresh synthetic DB on an internal Docker network with no published ports. The shim is absent from runtime images.

## Scheduler, outbox and historical data

Enable the scheduler with `docker compose --profile scheduler up -d`. Its installed daemon timezone is `/etc/localtime → Asia/Kolkata`; `0 2 * * *` invokes the statutory entry point. Every minute processes a bounded outbox batch; hourly cleanup removes expired sessions/OTP, bounded expired unsubmitted drafts and aged orphan proof. Accepted proof and prior proof retained in audit are protected. Cleanup does not delete accepted applications.

```sh
docker compose exec -T app python /app/scripts/deemed_approval.py --dry-run
docker compose logs --tail 100 scheduler
python3 ops/audit_data.py
# Private restored-data catch-up: backup + encrypted manifest + reconciliation
python3 ops/catchup.py --destination backups --passphrase-file secrets/backup-passphrase
# Optional bounded normal-worker backlog processing; never marks delivery itself
python3 ops/drain_outbox.py --max-seconds 300 --max-batches 200
```

The verified localhost catch-up transitioned exactly **4,871** eligible PENDING applications with matching audits, while original non-decision fields, accounts, sequence and every non-eligible decision reconciled unchanged. All4,873 resulting submission/correction/decision events were confirmed DELIVERED through the actual gateway; no pending/queued/failed events remained after bounded catch-up. One labeled synthetic application from live verification is retained. A first restored-data catch-up requires a private before/after row manifest, current backup and immutable-field/account/sequence reconciliation. The job locks overlap, recomputes cutoff per batch, changes only eligible PENDING rows, and commits decision/audit/unique outbox together. Already-decided rows stay untouched. Worker leases, retries/backoff and gateway event keys prevent duplicates. `QUEUED` is not `DELIVERED`; staff dashboard shows pending/queued/failed counts and sanitized errors. Investigate failures rather than marking them delivered. Correcting an erroneous automatic decision requires reviewing its exact private manifest/current row, a guarded compensating transaction/audit and cancellation of unsent events; a sent SMS cannot be unsent. Whole-DB restore is not normal decision rollback.

Read-only identity audit found 195 names exactly at the old 20-byte boundary and 42,825 DOBs with day/month both ≤12. These are patterns, not proof. No historical name/date was guessed or blanket-swapped, and password hashes are not independent DOB evidence. All 100,002 inherited documents are absent in the handout; the UI says so honestly.

## Backups, restore and rollback

Run `python3 ops/configure_backup.py` once to create a new private 0600 backup passphrase file (it refuses to overwrite one), and keep a separate recovery copy. Do not print it or put it in a command argument. Then:

```sh
python3 ops/backup.py --destination backups --passphrase-file secrets/backup-passphrase
python3 ops/check_backup.py --directory backups --max-age-hours 30
python3 ops/verify_restore.py --archive backups/SELECTED_SNAPSHOT.tar.gpg \
  --passphrase-file secrets/backup-passphrase
```

Backup briefly quiesces app/scheduler/gateway, takes a consistent custom-format PostgreSQL dump plus uploads/gateway, checks unchanged aggregates, writes file checksums and encrypts the archive with GnuPG AES-256 integrity protection. It resumes previously running services even on failure. Restore verification decrypts privately, validates paths/checksums and restores into a **new offline disposable container**, comparing records/accounts/sequence and runtime files; it never restores over the running database. Keep daily encrypted copies off-host with a separately held key; adopt 30 daily/4 weekly retention and monitor disk/freshness/failures. Local copies alone are not disaster recovery. Host `ops/sewasetu-backup.service`/`.timer` specify daily 03:15 IST with missed-run catch-up; review/install actual VM paths, enable the timer and connect failures/freshness to a real responsible operator. These VM/off-host arrangements are not claimed as deployed locally.

Keep the previous immutable image/release identifier before deploy. For a compatible additive schema, stop scheduler, set private `RELEASE_SHA` to the prior verified image tag, recreate app/scheduler with `--no-build`, check readiness/journeys and preserve the same DB/uploads/gateway volumes. Never restore a stale DB as the default image rollback: it would discard new writes. For an incompatible schema, rehearse a specific forward repair/compatibility plan first.

## VM deployment and submission checklist (operator-managed)

1. Provision/pay an owned VM and stable public hostname/IP; configure firewall/SSH, off-host backup destination and a named alert responder. Public ingress goes through Nginx only; DB/gateway stay private. A single VM is not high availability.
2. On the VM, use the private seed/environment and fresh-install procedure. Set `PUBLIC_BASE_URL=https://ACTUAL_HOST`, `PORTAL_HOST=ACTUAL_HOST`, valid TLS files under private `secrets/tls/`, and use **`docker compose -f docker-compose.yml --profile production --profile scheduler up -d --build --wait`**. The explicit file excludes the local HTTP override. Verify TLS chain, Secure/HttpOnly/SameSite cookies, fixed proxy headers, Nginx body/time limits, public release SHA, full synthetic journeys and admin/gateway from outside the VM. Test host reboot, actual scheduled execution, backup restore and compatible rollback. Configure external liveness/freshness alerts; keep the service paid/up until assessment completion, potentially 30 days.
3. Produce and verify the handover archive outside the repository with `python3 ops/package_handover.py --output /PRIVATE/OUTSIDE_REPO/sewasetu.zip`; it includes full `.git`, audits current active credentials/history and verifies unzip/history/clean status. Verify fresh local-clone setup with `python3 ops/verify_fresh_clone.py --admin-file secrets/admin-access.json` using the private credential JSON. It uses a uniquely named disposable Compose project with no published ports, the original private seed/environment, and no scheduler during baseline. Manually create a private GitHub repository, push the full unsquashed history and invite `harshnisar`. Personal-repository collaborators may receive write access; choose an organization repository/read role if read-only is required. Verify the invite and repository accessibility yourself; no confirmation is assumed. No repository, PR, remote, push or invitation is created by the application tools.
4. Supply repository URL, public portal URL, ZIP with full `.git`, working staff credentials privately, and a real face/screen Loom ≤3 minutes. Recheck links, public release SHA and credentials at submission. Do not submit localhost as a public URL.

Suggested human Loom outline: 0:00–0:25 evidence and inherited-data constraints; 0:25–1:45 the five ranked fixes above with short live security/receipt/correction/decision demonstrations; 1:45–2:25 red→green history and measured replay/restore proof; 2:25–2:55 deferred historical verification, simulated SMS confidentiality and deployment/availability limits. Record only demonstrations/results you have personally verified; this outline is not a recording.

## Honest limits and evidence gap

The mandated public fake gateway exposes delivered messages/codes and contacts; it cannot provide real SMS confidentiality. OTP proves control of a shared contact, not universal identity or bank ownership; all records associated with that verified contact are disclosed with explicit shared-phone guidance. No unique-mobile constraint excludes another adult. Self-service correction is PENDING-only, requires reviewed versioned changes/step-up for sensitive fields, preserves identity anchors/SLA and denies decided records; no verified assisted-correction service/phone was supplied. Name+age-proof facts and bank ownership are not independently verified by this portal. Browser checks cover mobile, keyboard, no-JS and 200%-equivalent reflow; they are not a full accessibility certification.

At the operator's explicit request, `/AGENTS.md` and `/docs/` remain ignored. **Their exclusion omits agent configuration/prompts and engineering evidence required by `BRIEF.md`.** Tests, migrations, operations, font licenses and unsquashed history remain trackable. No public VM, off-host alert/retention arrangement, GitHub access confirmation or authentic Loom is claimed until the operator supplies and verifies it.
