#!/usr/bin/env python3
"""Remplit une base SUP_KPI avec 8 jours de fausses erreurs (pour la démo uniquement)."""
import os
import random
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "central"))
import server  # noqa: E402

FLEET = {
    "srv-web-01": ["facturation", "portail"],
    "srv-web-02": ["portail", "api-clients"],
    "srv-batch-01": ["batch-nuit", "reporting", "export-compta"],
    "srv-db-01": ["sauvegarde"],
}
MESSAGES = [
    ("ERROR", "ERROR [main] Connexion refusée vers la base clients id={n}"),
    ("ERROR", "ERROR Timeout après {n} ms sur l'appel au service paiement"),
    ("Exception", "java.lang.NullPointerException at com.app.Facture.calcul(Facture.java:{n})"),
    ("FATAL", "FATAL Plus de mémoire disponible (OutOfMemoryError), arrêt du processus {n}"),
    (r"\bKO\b", "Traitement du lot {n} : KO"),
    ("CRITICAL", "CRITICAL Disque /data plein à {n} %"),
]
WEIGHT = {"facturation": 6, "portail": 3, "api-clients": 4, "batch-nuit": 9, "reporting": 1,
          "export-compta": 2, "sauvegarde": 0.4}


def main(db_path):
    random.seed(42)
    store = server.Store(db_path)
    now = time.time()
    for days_ago in range(8, 0, -1):
        day = date.today() - timedelta(days=days_ago)
        start, _ = server.day_bounds(day)
        for srv, apps in FLEET.items():
            errors = []
            for app in apps:
                n = int(random.expovariate(1 / (WEIGHT[app] * 12)))
                if app == "reporting" and days_ago == 1:
                    n = 0
                for _ in range(n):
                    hour = random.choice([1, 2, 2, 3, 8, 9, 10, 11, 14, 15, 16, 22])
                    ts = start + hour * 3600 + random.randint(0, 3599)
                    pattern, msg = random.choice(MESSAGES[:3] if app != "batch-nuit" else MESSAGES)
                    errors.append({"ts": ts, "app": app, "pattern": pattern, "file": "/var/log/%s/%s.log" % (app, app),
                                   "line": time.strftime("%Y-%m-%d %H:%M:%S ", time.localtime(ts)) +
                                   msg.format(n=random.randint(1, 999))})
            if srv == "srv-db-01" and days_ago == 1:
                continue  # ce serveur n'a rien envoyé hier
            store.ingest({"server": srv, "applications": apps, "errors": errors}, now=start + 23 * 3600)
    print("Base de démo prête :", db_path)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "demo.db")
