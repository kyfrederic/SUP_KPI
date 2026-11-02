# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

SUP_KPI is a log-error supervision tool for a fleet of Linux VMs: an **agent** on each server extracts error lines from application log files and pushes them to a **central** server, which stores them in SQLite and serves a single web page (dashboard, daily report, pattern management).

The owner is not a developer: user-facing text (UI, README, agent/central log messages, error messages returned by the API) is in **French** and written in very simple language. Keep it that way.

## Hard constraint: no dependencies

Everything is **Python 3 standard library only** (`http.server`, `sqlite3`, `urllib`, `json`) and the web page uses **no external JS/CSS/CDN** (charts are hand-drawn SVG in `app.js`). It must install on locked-down servers without `pip` or internet access. Do not add third-party packages or CDN links.

## Commands

```sh
python3 -m unittest discover tests                                  # all tests
python3 -m unittest tests.test_central.StoreTest.test_daily_report  # a single test
sh scripts/demo.sh            # local demo on http://localhost:8099 (seeded history + 2 live fake agents)
python3 central/server.py --config /nonexistent --port 8099 --db /tmp/x.db   # run central with defaults
python3 agent/agent.py --config <agent.conf> --once                  # single agent pass
```

No build step and no linter configured. `scripts/demo.sh` writes into `.demo/` (gitignored).

## Architecture

**Data flow:** agent `GET /api/patterns` → scans new log lines → `POST /api/ingest` (sent every cycle even with zero errors; it doubles as the heartbeat) → `Store` in `central/server.py` → JSON APIs consumed by `central/static/app.js`.

**Agent (`agent/agent.py`)**
- `LogReader` keeps per-file `{inode, offset}` in `state_dir/state.json`. On the very first run (`state.initialized` false) files start at EOF unless `read_from_start`; files first seen later start at 0 (assumed rotated/new). Inode change or shrink ⇒ restart at 0. Only complete lines are consumed (partial trailing line re-read next cycle).
- `Matcher`: plain-text patterns are case-insensitive substring matches; `is_regex` patterns are used as written (case-sensitive). The first matching pattern's text is recorded on the error.
- Failed sends are buffered in `pending.json` (capped by `max_pending`); last known patterns cached in `patterns.json` for when the central is down.
- Error `ts` is the time the agent read the line, not a timestamp parsed from the log.

**Central (`central/server.py`)**
- `Store` holds all DB logic; `Handler` is a thin router (`do_GET/POST/DELETE`) that maps `ValueError` to HTTP 400. SQLite connection per call, WAL mode, writes serialized by `_write_lock`.
- Tables: `errors`, `patterns` (seeded with defaults when empty), `agents` (last heartbeat per server), `applications` (every server/app ever reported — so apps with zero errors still appear), `app_days` (days an app checked in — distinguishes "0 errors" from "no news" in the daily report).
- `signature()` normalizes lines (masks numbers, UUIDs, hex, IPs) to group similar errors for "top messages".
- Status levels: server/app colour from errors in the last hour vs `hour_warn`/`hour_crit`; `silent` if no heartbeat within `silent_after_seconds`. Daily report uses `day_warn`/`day_crit` and `nodata`. Thresholds live in `DEFAULT_CONFIG` and are overridable via `central.conf` (JSON).
- Time bucketing for charts and day boundaries use the **central server's local timezone** (`local_offset`, `day_bounds`).
- Hourly background thread purges data older than `retention_days`.
- No authentication on the web page or the API (owner's explicit choice; internal network only).

**Web UI (`central/static/`)**: one page, three tabs (`dashboard`, `daily`, `patterns`), vanilla JS IIFE with a single `state` object; auto-refresh every 30 s. Colours are CSS custom properties on `:root` with dark-mode overrides; status is always shown as icon + label, not colour alone.

**Deployment (primary): GitLab CI + Ansible, no root.** `.gitlab-ci.yml` runs tests (Python 3.7 and 3.12) and `ansible:lint`, then exposes manual jobs `deploy:horsprod` / `deploy:prod` (prod only on the default branch) and `rollback:*`. Everything runs as user `webadmin` (home `/usr/local/web`) with **systemd --user** units; requires `loginctl enable-linger webadmin` once per server (checked by the `common` role).
- `deploy/ansible/site.yml` runs on `central:agents`; `sup_kpi_services` (derived from group membership) decides which of the roles `central` / `agent` apply. Roles: `common` (checks + dirs; `checks.yml` is reused by `rollback.yml`), `central`, `agent`, `activate` (switches `current` symlink, owns all handlers, health checks, prunes old releases).
- Layout on hosts: `~/sup-kpi/releases/<CI_COMMIT_SHORT_SHA>/`, `current` symlink, `conf/` (templated from inventory vars), `data/` (central DB, survives releases), `state/` (agent).
- All shared defaults live in `roles/common/defaults/main.yml`; per-env overrides in `inventories/<env>/group_vars/all.yml`, per-host apps in `host_vars/<host>.yml` (`sup_kpi_applications`).
- **Pinned to ansible-core 2.17** because it is the last release supporting Python 3.7 on managed hosts. Use only `ansible.builtin` modules (no Galaxy collections). `ansible-lint` (profile `production`) must pass: `cd deploy/ansible && ANSIBLE_CONFIG=$PWD/ansible.cfg ansible-lint`.

**Deployment (manual, root):** `central/install_central.sh` and `agent/install_agent.sh` install to `/opt/sup-kpi` with system-wide systemd units.

## Tests

`tests/` uses `unittest` and imports modules by inserting `agent/` or `central/` into `sys.path`. `Store` methods accept a `now=` argument — use it rather than mocking time.
