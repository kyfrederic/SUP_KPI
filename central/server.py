#!/usr/bin/env python3
"""SUP_KPI - serveur central.

Reçoit les lignes d'erreur envoyées par les agents, les range dans une base
SQLite et sert la page web (tableau de bord, bilan de la veille, patterns).

Uniquement la bibliothèque standard de Python 3 : rien à installer.

Lancement :
    python3 server.py --config central.conf
"""

import argparse
import csv
import io
import json
import mimetypes
import os
import re
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VERSION = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")

DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 8080,
    "db_path": os.path.join(HERE, "sup_kpi.db"),
    "retention_days": 30,
    # Voyants "temps réel" : nombre d'erreurs sur la dernière heure
    "hour_warn": 1,
    "hour_crit": 20,
    # Voyants du bilan de la veille : nombre d'erreurs sur la journée
    "day_warn": 1,
    "day_crit": 50,
    # Un agent est "muet" s'il n'a rien envoyé depuis ce nombre de secondes
    "silent_after_seconds": 300,
}

DEFAULT_PATTERNS = [
    ("ERROR", False),
    ("FATAL", False),
    ("CRITICAL", False),
    ("SEVERE", False),
    ("Exception", False),
    (r"\bKO\b", True),
]

PERIODS = {
    "1h": (3600, 300),           # durée, taille d'une barre du graphique
    "24h": (86400, 3600),
    "7d": (7 * 86400, 6 * 3600),
    "30d": (30 * 86400, 86400),
}

MAX_LINE_LENGTH = 2000
MAX_BATCH = 20000


def load_config(path):
    config = dict(DEFAULT_CONFIG)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            config.update(json.load(fh))
    return config


# --------------------------------------------------------------------------
# Regroupement des messages
# --------------------------------------------------------------------------

_SIGNATURE_RULES = [
    (re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"), "<uuid>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "<hex>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\d+"), "#"),
    (re.compile(r"\s+"), " "),
]


def signature(line):
    """Transforme une ligne en "famille" : les chiffres, dates, IDs... sont
    masqués pour que deux erreurs identiques à l'heure près soient comptées
    ensemble dans le top des messages."""
    sig = line
    for rx, repl in _SIGNATURE_RULES:
        sig = rx.sub(repl, sig)
    return sig.strip()[:300]


def local_offset(ts=None):
    return time.localtime(ts if ts is not None else time.time()).tm_gmtoff


def day_bounds(day):
    start = time.mktime(datetime(day.year, day.month, day.day).timetuple())
    nxt = day + timedelta(days=1)
    end = time.mktime(datetime(nxt.year, nxt.month, nxt.day).timetuple())
    return start, end


def level(count, warn, crit):
    if count >= crit:
        return "red"
    if count >= warn:
        return "orange"
    return "green"


# --------------------------------------------------------------------------
# Base de données
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS errors (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    server    TEXT NOT NULL,
    app       TEXT NOT NULL,
    file      TEXT,
    pattern   TEXT,
    line      TEXT NOT NULL,
    signature TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_errors_ts ON errors(ts);
CREATE INDEX IF NOT EXISTS idx_errors_server_app_ts ON errors(server, app, ts);

CREATE TABLE IF NOT EXISTS patterns (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    text     TEXT NOT NULL UNIQUE,
    is_regex INTEGER NOT NULL DEFAULT 0,
    enabled  INTEGER NOT NULL DEFAULT 1,
    created  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    server    TEXT PRIMARY KEY,
    last_seen REAL NOT NULL,
    version   TEXT,
    address   TEXT
);

CREATE TABLE IF NOT EXISTS applications (
    server     TEXT NOT NULL,
    app        TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL,
    PRIMARY KEY (server, app)
);

-- Jours où une application a donné signe de vie (pour le bilan de la veille)
CREATE TABLE IF NOT EXISTS app_days (
    server TEXT NOT NULL,
    app    TEXT NOT NULL,
    day    TEXT NOT NULL,
    PRIMARY KEY (server, app, day)
);
"""


class Store:
    def __init__(self, db_path, config=None):
        self.db_path = db_path
        self.config = dict(DEFAULT_CONFIG)
        if config:
            self.config.update(config)
        self._write_lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            if conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0] == 0:
                now = time.time()
                conn.executemany(
                    "INSERT INTO patterns(text, is_regex, enabled, created) VALUES (?, ?, 1, ?)",
                    [(text, int(rx), now) for text, rx in DEFAULT_PATTERNS],
                )

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ---------------------------------------------------------------- patterns

    def list_patterns(self):
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM patterns ORDER BY id").fetchall()
        return [
            {"id": r["id"], "text": r["text"], "is_regex": bool(r["is_regex"]), "enabled": bool(r["enabled"])}
            for r in rows
        ]

    def add_pattern(self, text, is_regex=False):
        text = (text or "").strip()
        if not text:
            raise ValueError("Le pattern est vide.")
        if is_regex:
            try:
                re.compile(text)
            except re.error as exc:
                raise ValueError("Expression régulière invalide : %s" % exc)
        with self._write_lock, self._connect() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO patterns(text, is_regex, enabled, created) VALUES (?, ?, 1, ?)",
                    (text, int(bool(is_regex)), time.time()),
                )
            except sqlite3.IntegrityError:
                raise ValueError("Ce pattern existe déjà.")
            return cur.lastrowid

    def set_pattern_enabled(self, pattern_id, enabled):
        with self._write_lock, self._connect() as conn:
            cur = conn.execute("UPDATE patterns SET enabled=? WHERE id=?", (int(bool(enabled)), pattern_id))
            return cur.rowcount > 0

    def delete_pattern(self, pattern_id):
        with self._write_lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM patterns WHERE id=?", (pattern_id,))
            return cur.rowcount > 0

    # ------------------------------------------------------------------ ingest

    def ingest(self, payload, address=None, now=None):
        now = now if now is not None else time.time()
        server = str(payload.get("server") or "").strip()
        if not server:
            raise ValueError("Le champ 'server' est obligatoire.")
        apps = [str(a).strip() for a in payload.get("applications") or [] if str(a).strip()]
        errors = payload.get("errors") or []
        if len(errors) > MAX_BATCH:
            raise ValueError("Trop de lignes dans un seul envoi (max %d)." % MAX_BATCH)

        oldest = now - self.config["retention_days"] * 86400
        rows = []
        days = set()
        today = date.fromtimestamp(now).isoformat()
        for app in apps:
            days.add((app, today))
        for err in errors:
            line = str(err.get("line") or "").rstrip("\r\n")[:MAX_LINE_LENGTH]
            app = str(err.get("app") or "").strip()
            if not line or not app:
                continue
            try:
                ts = float(err.get("ts") or now)
            except (TypeError, ValueError):
                ts = now
            if ts < oldest or ts > now + 3600:
                ts = now
            rows.append((ts, server, app, str(err.get("file") or ""), str(err.get("pattern") or ""), line, signature(line)))
            days.add((app, date.fromtimestamp(ts).isoformat()))

        with self._write_lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO agents(server, last_seen, version, address) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(server) DO UPDATE SET last_seen=excluded.last_seen, "
                "version=excluded.version, address=excluded.address",
                (server, now, str(payload.get("version") or ""), address),
            )
            for app in set(apps) | {r[2] for r in rows}:
                conn.execute(
                    "INSERT INTO applications(server, app, first_seen, last_seen) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(server, app) DO UPDATE SET last_seen=excluded.last_seen",
                    (server, app, now, now),
                )
            conn.executemany(
                "INSERT OR IGNORE INTO app_days(server, app, day) VALUES (?, ?, ?)",
                [(server, app, day) for app, day in days],
            )
            conn.executemany(
                "INSERT INTO errors(ts, server, app, file, pattern, line, signature) VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def purge(self, now=None):
        now = now if now is not None else time.time()
        limit = now - self.config["retention_days"] * 86400
        with self._write_lock, self._connect() as conn:
            conn.execute("DELETE FROM errors WHERE ts < ?", (limit,))
            conn.execute("DELETE FROM app_days WHERE day < ?", (date.fromtimestamp(limit).isoformat(),))

    # ------------------------------------------------------------------- query

    @staticmethod
    def _filters(server=None, app=None, pattern=None, q=None):
        clauses, params = [], []
        if server:
            clauses.append("server = ?")
            params.append(server)
        if app:
            clauses.append("app = ?")
            params.append(app)
        if pattern:
            clauses.append("pattern = ?")
            params.append(pattern)
        if q:
            clauses.append("line LIKE ?")
            params.append("%" + q + "%")
        return "".join(" AND " + c for c in clauses), params

    def kpi(self, period="24h", server=None, app=None, now=None):
        now = now if now is not None else time.time()
        span, bucket = PERIODS.get(period, PERIODS["24h"])
        start = now - span
        where, params = self._filters(server, app)
        cfg = self.config
        with self._connect() as conn:
            base = "FROM errors WHERE ts >= ?" + where
            bparams = [start] + params
            total = conn.execute("SELECT COUNT(*) " + base, bparams).fetchone()[0]
            prev_total = conn.execute(
                "SELECT COUNT(*) FROM errors WHERE ts >= ? AND ts < ?" + where, [start - span, start] + params
            ).fetchone()[0]
            by_server = conn.execute(
                "SELECT server AS name, COUNT(*) AS count " + base + " GROUP BY server ORDER BY count DESC", bparams
            ).fetchall()
            by_app = conn.execute(
                "SELECT server, app, COUNT(*) AS count " + base + " GROUP BY server, app ORDER BY count DESC LIMIT 15",
                bparams,
            ).fetchall()
            by_pattern = conn.execute(
                "SELECT pattern AS name, COUNT(*) AS count " + base + " GROUP BY pattern ORDER BY count DESC", bparams
            ).fetchall()
            top = conn.execute(
                "SELECT signature, COUNT(*) AS count, MAX(ts) AS last_ts, "
                "COUNT(DISTINCT server) AS servers, MAX(line) AS example, "
                "GROUP_CONCAT(DISTINCT app) AS apps " + base + " GROUP BY signature ORDER BY count DESC LIMIT 10",
                bparams,
            ).fetchall()

            off = local_offset(now)
            first_bucket = int((start + off) // bucket) * bucket - off
            raw = conn.execute(
                "SELECT CAST((ts + ?) / ? AS INTEGER) * ? - ? AS b, COUNT(*) AS c " + base + " GROUP BY b",
                [off, bucket, bucket, off] + bparams,
            ).fetchall()
            counts = {int(r["b"]): r["c"] for r in raw}
            series = []
            b = first_bucket
            while b <= now:
                series.append({"ts": b, "count": counts.get(b, 0)})
                b += bucket

            servers = self._server_status(conn, now)
            app_names = conn.execute(
                "SELECT server, app FROM applications ORDER BY server, app"
            ).fetchall()

        summary = {"ok": 0, "warn": 0, "crit": 0, "silent": 0}
        for s in servers:
            summary[{"green": "ok", "orange": "warn", "red": "crit", "silent": "silent"}[s["status"]]] += 1

        return {
            "period": period,
            "generated_at": now,
            "bucket_seconds": bucket,
            "total": total,
            "previous_total": prev_total,
            "thresholds": {k: cfg[k] for k in ("hour_warn", "hour_crit", "day_warn", "day_crit", "silent_after_seconds")},
            "by_server": [dict(r) for r in by_server],
            "by_app": [dict(r) for r in by_app],
            "by_pattern": [dict(r) for r in by_pattern],
            "top_messages": [dict(r) for r in top],
            "series": series,
            "servers": servers,
            "summary": summary,
            "applications": [dict(r) for r in app_names],
        }

    def _server_status(self, conn, now):
        cfg = self.config
        hour_ago = now - 3600
        day_ago = now - 86400
        agents = {r["server"]: r for r in conn.execute("SELECT * FROM agents").fetchall()}
        hour = dict(conn.execute(
            "SELECT server, COUNT(*) FROM errors WHERE ts >= ? GROUP BY server", (hour_ago,)
        ).fetchall())
        day = dict(conn.execute(
            "SELECT server, COUNT(*) FROM errors WHERE ts >= ? GROUP BY server", (day_ago,)
        ).fetchall())
        apps = {}
        for r in conn.execute("SELECT server, app FROM applications ORDER BY app").fetchall():
            apps.setdefault(r["server"], []).append(r["app"])
        app_hour = {}
        for r in conn.execute(
            "SELECT server, app, COUNT(*) AS c FROM errors WHERE ts >= ? GROUP BY server, app", (hour_ago,)
        ).fetchall():
            app_hour[(r["server"], r["app"])] = r["c"]

        result = []
        for name in sorted(set(agents) | set(apps)):
            agent = agents.get(name)
            last_seen = agent["last_seen"] if agent else None
            h = hour.get(name, 0)
            if last_seen is None or now - last_seen > cfg["silent_after_seconds"]:
                status = "silent"
            else:
                status = level(h, cfg["hour_warn"], cfg["hour_crit"])
            result.append({
                "server": name,
                "status": status,
                "last_seen": last_seen,
                "version": agent["version"] if agent else None,
                "errors_last_hour": h,
                "errors_last_24h": day.get(name, 0),
                "applications": [
                    {"app": a, "errors_last_hour": app_hour.get((name, a), 0),
                     "status": level(app_hour.get((name, a), 0), cfg["hour_warn"], cfg["hour_crit"])}
                    for a in apps.get(name, [])
                ],
            })
        return result

    def errors(self, period="24h", server=None, app=None, pattern=None, q=None, limit=200, now=None):
        now = now if now is not None else time.time()
        span, _ = PERIODS.get(period, PERIODS["24h"])
        where, params = self._filters(server, app, pattern, q)
        limit = max(1, min(int(limit), 2000))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, ts, server, app, file, pattern, line FROM errors WHERE ts >= ?" + where +
                " ORDER BY ts DESC, id DESC LIMIT ?",
                [now - span] + params + [limit],
            ).fetchall()
        return [dict(r) for r in rows]

    def daily_report(self, day=None, now=None):
        """État des lieux d'une journée (par défaut : hier) pour toutes les applications."""
        now = now if now is not None else time.time()
        if day is None:
            day = date.fromtimestamp(now) - timedelta(days=1)
        prev = day - timedelta(days=1)
        start, end = day_bounds(day)
        pstart, pend = day_bounds(prev)
        cfg = self.config
        with self._connect() as conn:
            known = conn.execute(
                "SELECT server, app FROM applications WHERE first_seen < ?", (end,)
            ).fetchall()
            stats = {
                (r["server"], r["app"]): r for r in conn.execute(
                    "SELECT server, app, COUNT(*) AS count, MIN(ts) AS first_ts, MAX(ts) AS last_ts "
                    "FROM errors WHERE ts >= ? AND ts < ? GROUP BY server, app", (start, end)
                ).fetchall()
            }
            prev_counts = {
                (r["server"], r["app"]): r["count"] for r in conn.execute(
                    "SELECT server, app, COUNT(*) AS count FROM errors WHERE ts >= ? AND ts < ? "
                    "GROUP BY server, app", (pstart, pend)
                ).fetchall()
            }
            tops = {}
            for r in conn.execute(
                "SELECT server, app, signature, COUNT(*) AS c, MAX(line) AS example FROM errors "
                "WHERE ts >= ? AND ts < ? GROUP BY server, app, signature ORDER BY c DESC", (start, end)
            ).fetchall():
                tops.setdefault((r["server"], r["app"]), r)
            seen = {
                (r["server"], r["app"]) for r in conn.execute(
                    "SELECT server, app FROM app_days WHERE day = ?", (day.isoformat(),)
                ).fetchall()
            }

        keys = sorted({(r["server"], r["app"]) for r in known} | set(stats))
        rows = []
        for key in keys:
            st = stats.get(key)
            count = st["count"] if st else 0
            pc = prev_counts.get(key, 0)
            if count == 0 and key not in seen:
                status = "nodata"
            else:
                status = level(count, cfg["day_warn"], cfg["day_crit"])
            top = tops.get(key)
            rows.append({
                "server": key[0],
                "app": key[1],
                "status": status,
                "count": count,
                "previous_count": pc,
                "delta": count - pc,
                "first_ts": st["first_ts"] if st else None,
                "last_ts": st["last_ts"] if st else None,
                "top_message": top["example"] if top else None,
                "top_message_count": top["c"] if top else 0,
            })
        order = {"red": 0, "orange": 1, "nodata": 2, "green": 3}
        rows.sort(key=lambda r: (order[r["status"]], -r["count"], r["server"], r["app"]))
        summary = {
            "total": sum(r["count"] for r in rows),
            "previous_total": sum(r["previous_count"] for r in rows),
            "applications": len(rows),
            "red": sum(1 for r in rows if r["status"] == "red"),
            "orange": sum(1 for r in rows if r["status"] == "orange"),
            "green": sum(1 for r in rows if r["status"] == "green"),
            "nodata": sum(1 for r in rows if r["status"] == "nodata"),
        }
        return {
            "date": day.isoformat(),
            "previous_date": prev.isoformat(),
            "thresholds": {"day_warn": cfg["day_warn"], "day_crit": cfg["day_crit"]},
            "summary": summary,
            "rows": rows,
        }

    def daily_report_csv(self, day=None, now=None):
        report = self.daily_report(day, now)
        labels = {"red": "Rouge", "orange": "Orange", "green": "Vert", "nodata": "Pas de nouvelles"}
        out = io.StringIO()
        writer = csv.writer(out, delimiter=";")
        writer.writerow(["Date", "Serveur", "Application", "Etat", "Erreurs", "Erreurs veille",
                         "Ecart", "Premiere erreur", "Derniere erreur", "Erreur la plus frequente", "Nb"])

        def hm(ts):
            return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else ""

        for r in report["rows"]:
            writer.writerow([report["date"], r["server"], r["app"], labels[r["status"]], r["count"],
                             r["previous_count"], r["delta"], hm(r["first_ts"]), hm(r["last_ts"]),
                             r["top_message"] or "", r["top_message_count"]])
        # BOM pour qu'Excel lise correctement les accents
        return "﻿" + out.getvalue()


# --------------------------------------------------------------------------
# Serveur HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "SUP_KPI/" + VERSION
    store = None  # injecté au démarrage

    def log_message(self, fmt, *args):
        if os.environ.get("SUP_KPI_ACCESS_LOG"):
            super().log_message(fmt, *args)

    def _send(self, status, body, content_type="application/json; charset=utf-8", headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status=200):
        self._send(status, json.dumps(data, ensure_ascii=False))

    def _error(self, status, message):
        self._json({"error": message}, status)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 50 * 1024 * 1024:
            raise ValueError("Envoi trop gros.")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _query(self):
        url = urlparse(self.path)
        return url.path, {k: v[-1] for k, v in parse_qs(url.query).items()}

    def do_GET(self):
        path, q = self._query()
        store = self.store
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/health":
                return self._json({"status": "ok", "version": VERSION})
            if path == "/api/patterns":
                return self._json({"patterns": store.list_patterns()})
            if path == "/api/kpi":
                return self._json(store.kpi(q.get("period", "24h"), q.get("server"), q.get("app")))
            if path == "/api/errors":
                return self._json({"errors": store.errors(
                    q.get("period", "24h"), q.get("server"), q.get("app"),
                    q.get("pattern"), q.get("q"), q.get("limit", 200))})
            if path in ("/api/daily-report", "/api/daily-report.csv"):
                day = date.fromisoformat(q["date"]) if q.get("date") else None
                if path.endswith(".csv"):
                    report_day = (day or (date.today() - timedelta(days=1))).isoformat()
                    return self._send(200, store.daily_report_csv(day), "text/csv; charset=utf-8", {
                        "Content-Disposition": 'attachment; filename="bilan-%s.csv"' % report_day})
                return self._json(store.daily_report(day))
            return self._error(404, "Page introuvable.")
        except ValueError as exc:
            return self._error(400, str(exc))

    def do_POST(self):
        path, _ = self._query()
        store = self.store
        try:
            if path == "/api/ingest":
                count = store.ingest(self._body(), address=self.client_address[0])
                return self._json({"status": "ok", "received": count})
            if path == "/api/patterns":
                body = self._body()
                pid = store.add_pattern(body.get("text"), bool(body.get("is_regex")))
                return self._json({"status": "ok", "id": pid}, 201)
            m = re.fullmatch(r"/api/patterns/(\d+)/(enable|disable)", path)
            if m:
                if not store.set_pattern_enabled(int(m.group(1)), m.group(2) == "enable"):
                    return self._error(404, "Pattern introuvable.")
                return self._json({"status": "ok"})
            return self._error(404, "Page introuvable.")
        except (ValueError, json.JSONDecodeError) as exc:
            return self._error(400, str(exc))

    def do_DELETE(self):
        path, _ = self._query()
        m = re.fullmatch(r"/api/patterns/(\d+)", path)
        if not m:
            return self._error(404, "Page introuvable.")
        if not self.store.delete_pattern(int(m.group(1))):
            return self._error(404, "Pattern introuvable.")
        return self._json({"status": "ok"})

    def _static(self, name):
        full = os.path.realpath(os.path.join(STATIC_DIR, name))
        if not full.startswith(os.path.realpath(STATIC_DIR) + os.sep) or not os.path.isfile(full):
            return self._error(404, "Fichier introuvable.")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            self._send(200, fh.read(), ctype)


def purge_loop(store, stop):
    while not stop.wait(3600):
        try:
            store.purge()
        except Exception as exc:  # pragma: no cover - simple filet de sécurité
            print("Purge impossible :", exc)


def main():
    parser = argparse.ArgumentParser(description="SUP_KPI - serveur central")
    parser.add_argument("--config", default=os.path.join(HERE, "central.conf"))
    parser.add_argument("--port", type=int)
    parser.add_argument("--db")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.port:
        config["port"] = args.port
    if args.db:
        config["db_path"] = args.db

    store = Store(config["db_path"], config)
    store.purge()
    Handler.store = store
    stop = threading.Event()
    threading.Thread(target=purge_loop, args=(store, stop), daemon=True).start()

    httpd = ThreadingHTTPServer((config["host"], config["port"]), Handler)
    print("SUP_KPI central v%s : ouvrez http://<ce-serveur>:%d dans votre navigateur" % (VERSION, config["port"]))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()


if __name__ == "__main__":
    main()
