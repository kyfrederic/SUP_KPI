#!/usr/bin/env python3
"""SUP_KPI - agent (le "petit robot").

Tourne sur chaque serveur. Toutes les minutes :
  1. demande au central la liste des patterns à chercher ;
  2. lit seulement les NOUVELLES lignes des fichiers de log de chaque application ;
  3. garde les lignes qui contiennent un pattern ;
  4. les envoie au central (et envoie un "signe de vie" même s'il n'y a rien).

Uniquement la bibliothèque standard de Python 3 : rien à installer.

Lancement :
    python3 agent.py --config /etc/sup-kpi/agent.conf          # en continu
    python3 agent.py --config /etc/sup-kpi/agent.conf --once   # un seul passage (test)
"""

import argparse
import glob
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request

VERSION = "1.0.0"

DEFAULT_CONFIG = {
    "central_url": "http://127.0.0.1:8080",
    "server_name": "",
    "interval_seconds": 60,
    "state_dir": "/var/lib/sup-kpi-agent",
    "read_from_start": False,
    "max_bytes_per_file": 20 * 1024 * 1024,
    "max_pending": 50000,
    "applications": [],
}

SEND_CHUNK = 5000


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def load_config(path):
    config = dict(DEFAULT_CONFIG)
    with open(path, encoding="utf-8") as fh:
        config.update(json.load(fh))
    if not config.get("server_name"):
        config["server_name"] = socket.gethostname()
    config["central_url"] = config["central_url"].rstrip("/")
    for app in config["applications"]:
        if not app.get("name") or not app.get("log_dir"):
            raise ValueError("Chaque application doit avoir un 'name' et un 'log_dir'.")
    return config


# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------

class Matcher:
    """Texte simple : recherché sans tenir compte des majuscules/minuscules.
    Expression régulière (is_regex) : utilisée telle quelle."""

    def __init__(self, patterns):
        self.rules = []
        for p in patterns:
            if not p.get("enabled", True):
                continue
            text = p.get("text") or ""
            if not text:
                continue
            if p.get("is_regex"):
                try:
                    self.rules.append((text, re.compile(text)))
                except re.error:
                    log("Pattern ignoré (expression invalide) : %s" % text)
            else:
                self.rules.append((text, re.compile(re.escape(text), re.IGNORECASE)))

    def match(self, line):
        for text, rx in self.rules:
            if rx.search(line):
                return text
        return None


# --------------------------------------------------------------------------
# Lecture incrémentale des fichiers
# --------------------------------------------------------------------------

class LogReader:
    """Se souvient, pour chaque fichier, de l'endroit où il s'est arrêté de lire."""

    def __init__(self, state, read_from_start=False, max_bytes=20 * 1024 * 1024):
        self.state = state  # {"initialized": bool, "files": {path: {"inode", "offset"}}}
        self.state.setdefault("files", {})
        self.read_from_start = read_from_start
        self.max_bytes = max_bytes

    def read_new_lines(self, path):
        try:
            st = os.stat(path)
        except OSError:
            return []
        files = self.state["files"]
        info = files.get(path)
        if info is None:
            # Fichier jamais vu : au tout premier lancement on commence à la fin
            # (pour ne pas renvoyer tout l'historique), sinon c'est un nouveau
            # fichier (ex. après rotation) et on le lit depuis le début.
            start_at_end = not self.state.get("initialized") and not self.read_from_start
            info = {"inode": st.st_ino, "offset": st.st_size if start_at_end else 0}
        elif info["inode"] != st.st_ino or st.st_size < info["offset"]:
            # Le fichier a été remplacé ou vidé (rotation) : on repart du début
            info = {"inode": st.st_ino, "offset": 0}
        files[path] = info

        if st.st_size <= info["offset"]:
            return []
        try:
            with open(path, "rb") as fh:
                fh.seek(info["offset"])
                data = fh.read(self.max_bytes)
        except OSError as exc:
            log("Impossible de lire %s : %s" % (path, exc))
            return []
        # On ne garde que les lignes complètes ; la fin incomplète sera relue au prochain tour
        cut = data.rfind(b"\n")
        if cut < 0:
            if len(data) < self.max_bytes:
                return []
            cut = len(data) - 1
        info["offset"] += cut + 1
        return data[:cut + 1].decode("utf-8", errors="replace").splitlines()

    def forget_missing(self, existing):
        for path in list(self.state["files"]):
            if path not in existing:
                del self.state["files"][path]


def list_files(app):
    patterns = app.get("file_pattern") or "*.log"
    if isinstance(patterns, str):
        patterns = [patterns]
    found = set()
    for pat in patterns:
        for path in glob.glob(os.path.join(app["log_dir"], pat), recursive=True):
            if os.path.isfile(path):
                found.add(os.path.abspath(path))
    return sorted(found)


def scan(config, reader, matcher, now=None):
    now = now if now is not None else time.time()
    errors = []
    seen = set()
    for app in config["applications"]:
        files = list_files(app)
        if not files and not os.path.isdir(app["log_dir"]):
            log("Répertoire introuvable pour %s : %s" % (app["name"], app["log_dir"]))
        for path in files:
            seen.add(path)
            for line in reader.read_new_lines(path):
                pattern = matcher.match(line)
                if pattern:
                    errors.append({"ts": now, "app": app["name"], "file": path, "pattern": pattern, "line": line[:2000]})
    reader.forget_missing(seen)
    reader.state["initialized"] = True
    return errors


# --------------------------------------------------------------------------
# Échanges avec le central
# --------------------------------------------------------------------------

def http_json(url, payload=None, timeout=30):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


class Agent:
    def __init__(self, config):
        self.config = config
        os.makedirs(config["state_dir"], exist_ok=True)
        self.state_path = os.path.join(config["state_dir"], "state.json")
        self.pending_path = os.path.join(config["state_dir"], "pending.json")
        self.patterns_path = os.path.join(config["state_dir"], "patterns.json")
        self.reader = LogReader(read_json(self.state_path, {}), config["read_from_start"],
                                config["max_bytes_per_file"])

    def fetch_patterns(self):
        try:
            patterns = http_json(self.config["central_url"] + "/api/patterns")["patterns"]
            write_json(self.patterns_path, patterns)
            return patterns
        except (OSError, ValueError, KeyError) as exc:
            log("Central injoignable pour les patterns (%s) : j'utilise la dernière liste connue." % exc)
            return read_json(self.patterns_path, [{"text": "ERROR"}, {"text": "FATAL"}, {"text": "KO"}])

    def send(self, errors):
        apps = [a["name"] for a in self.config["applications"]]
        base = {"server": self.config["server_name"], "version": VERSION, "applications": apps}
        chunks = [errors[i:i + SEND_CHUNK] for i in range(0, len(errors), SEND_CHUNK)] or [[]]
        sent = 0
        for chunk in chunks:
            try:
                http_json(self.config["central_url"] + "/api/ingest", dict(base, errors=chunk))
            except (OSError, ValueError) as exc:
                log("Envoi impossible (%s) : je garde %d ligne(s) pour plus tard." % (exc, len(errors) - sent))
                return errors[sent:]
            sent += len(chunk)
        return []

    def run_once(self):
        matcher = Matcher(self.fetch_patterns())
        new = scan(self.config, self.reader, matcher)
        write_json(self.state_path, self.reader.state)
        pending = read_json(self.pending_path, []) + new
        left = self.send(pending)
        if len(left) > self.config["max_pending"]:
            log("Tampon plein : %d ligne(s) anciennes abandonnées." % (len(left) - self.config["max_pending"]))
            left = left[-self.config["max_pending"]:]
        write_json(self.pending_path, left)
        log("%d nouvelle(s) erreur(s) trouvée(s), %d envoyée(s), %d en attente."
            % (len(new), len(pending) - len(left), len(left)))

    def run_forever(self):
        log("Agent SUP_KPI v%s démarré pour le serveur '%s' -> %s"
            % (VERSION, self.config["server_name"], self.config["central_url"]))
        while True:
            started = time.time()
            try:
                self.run_once()
            except Exception as exc:  # le robot ne doit jamais s'arrêter
                log("Erreur inattendue : %s" % exc)
            time.sleep(max(1, self.config["interval_seconds"] - (time.time() - started)))


def main():
    parser = argparse.ArgumentParser(description="SUP_KPI - agent")
    parser.add_argument("--config", default="/etc/sup-kpi/agent.conf")
    parser.add_argument("--once", action="store_true", help="un seul passage puis arrêt")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except (OSError, ValueError) as exc:
        print("Configuration illisible (%s) : %s" % (args.config, exc), file=sys.stderr)
        sys.exit(1)
    agent = Agent(config)
    if args.once:
        agent.run_once()
    else:
        agent.run_forever()


if __name__ == "__main__":
    main()
