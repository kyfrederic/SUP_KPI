import os
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "central"))
import server  # noqa: E402


def ts_of(day, hour):
    return time.mktime(datetime(day.year, day.month, day.day, hour).timetuple())


class SignatureTest(unittest.TestCase):
    def test_numbers_and_ids_are_masked(self):
        a = server.signature("2026-10-30 10:00:01 ERROR user 123 from 10.0.0.1 id=deadbeefdeadbeef00")
        b = server.signature("2026-10-29 11:22:33 ERROR user 9 from 10.0.0.2 id=0123456789abcdef01")
        self.assertEqual(a, b)


class StoreTest(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.store = server.Store(self.db, {"silent_after_seconds": 300})

    def test_default_patterns_and_crud(self):
        texts = [p["text"] for p in self.store.list_patterns()]
        self.assertIn("ERROR", texts)
        pid = self.store.add_pattern("Timeout")
        with self.assertRaises(ValueError):
            self.store.add_pattern("Timeout")
        with self.assertRaises(ValueError):
            self.store.add_pattern("([", is_regex=True)
        self.store.set_pattern_enabled(pid, False)
        p = [p for p in self.store.list_patterns() if p["id"] == pid][0]
        self.assertFalse(p["enabled"])
        self.assertTrue(self.store.delete_pattern(pid))

    def test_kpi_and_status(self):
        now = time.time()
        self.store.ingest({"server": "s1", "applications": ["a", "b"], "errors": [
            {"ts": now - 10, "app": "a", "line": "ERROR x 1", "pattern": "ERROR"},
            {"ts": now - 20, "app": "a", "line": "ERROR x 2", "pattern": "ERROR"},
        ]}, now=now)
        self.store.ingest({"server": "s2", "applications": ["c"], "errors": []}, now=now - 1000)
        k = self.store.kpi("24h", now=now)
        self.assertEqual(k["total"], 2)
        self.assertEqual(k["top_messages"][0]["count"], 2)  # même famille
        status = {s["server"]: s["status"] for s in k["servers"]}
        self.assertEqual(status, {"s1": "orange", "s2": "silent"})
        self.assertEqual(sum(p["count"] for p in k["series"]), 2)
        self.assertEqual(len(self.store.errors(server="s1", now=now)), 2)
        self.assertEqual(len(self.store.errors(q="x 1", now=now)), 1)

    def test_daily_report(self):
        today = date.today()
        y, by = today - timedelta(days=1), today - timedelta(days=2)
        now = time.time()
        # Application connue depuis avant-hier
        self.store.ingest({"server": "s1", "applications": ["a", "b", "quiet"], "errors": []}, now=ts_of(by, 9))
        self.store.ingest({"server": "s1", "applications": ["a", "b"], "errors": [
            {"ts": ts_of(y, 8), "app": "a", "line": "ERROR one"},
            {"ts": ts_of(y, 9), "app": "a", "line": "ERROR one"},
            {"ts": ts_of(by, 9), "app": "a", "line": "ERROR old"},
        ]}, now=ts_of(y, 10))
        r = self.store.daily_report(y, now=now)
        rows = {row["app"]: row for row in r["rows"]}
        self.assertEqual(rows["a"]["count"], 2)
        self.assertEqual(rows["a"]["previous_count"], 1)
        self.assertEqual(rows["a"]["status"], "orange")
        self.assertEqual(rows["b"]["status"], "green")
        self.assertEqual(rows["quiet"]["status"], "nodata")
        self.assertEqual(r["summary"]["total"], 2)
        csv_text = self.store.daily_report_csv(y, now=now)
        self.assertIn("Pas de nouvelles", csv_text)

    def test_purge(self):
        now = time.time()
        self.store.ingest({"server": "s1", "errors": [{"ts": now - 5, "app": "a", "line": "ERROR"}]}, now=now)
        self.store.purge(now=now + 31 * 86400)
        self.assertEqual(self.store.kpi("30d", now=now)["total"], 0)


if __name__ == "__main__":
    unittest.main()
