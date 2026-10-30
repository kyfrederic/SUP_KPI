import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))
import agent  # noqa: E402


class MatcherTest(unittest.TestCase):
    def test_plain_text_is_case_insensitive(self):
        m = agent.Matcher([{"text": "error"}])
        self.assertEqual(m.match("2026 ERROR boom"), "error")
        self.assertIsNone(m.match("all good"))

    def test_regex_and_disabled(self):
        m = agent.Matcher([{"text": r"\bKO\b", "is_regex": True}, {"text": "WARN", "enabled": False}])
        self.assertEqual(m.match("transfert KO"), r"\bKO\b")
        self.assertIsNone(m.match("token kotlin"))
        self.assertIsNone(m.match("WARN disque"))

    def test_invalid_regex_is_ignored(self):
        m = agent.Matcher([{"text": "([", "is_regex": True}, {"text": "FATAL"}])
        self.assertEqual(m.match("FATAL x"), "FATAL")


class ReaderTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "app.log")

    def write(self, text, mode="a"):
        with open(self.path, mode) as fh:
            fh.write(text)

    def test_first_run_starts_at_end_then_reads_new_lines(self):
        self.write("old ERROR\n")
        r = agent.LogReader({})
        self.assertEqual(r.read_new_lines(self.path), [])
        r.state["initialized"] = True
        self.write("new ERROR\npartial")
        self.assertEqual(r.read_new_lines(self.path), ["new ERROR"])
        self.write(" line\n")
        self.assertEqual(r.read_new_lines(self.path), ["partial line"])

    def test_read_from_start(self):
        self.write("a\nb\n")
        r = agent.LogReader({}, read_from_start=True)
        self.assertEqual(r.read_new_lines(self.path), ["a", "b"])

    def test_rotation_truncate_and_replace(self):
        self.write("a\nb\n")
        r = agent.LogReader({}, read_from_start=True)
        r.read_new_lines(self.path)
        self.write("c\n", mode="w")  # fichier vidé puis réécrit
        self.assertEqual(r.read_new_lines(self.path), ["c"])
        os.rename(self.path, self.path + ".1")
        self.write("d\n", mode="w")  # nouveau fichier (nouvel inode)
        self.assertEqual(r.read_new_lines(self.path), ["d"])

    def test_new_file_after_init_is_read_from_start(self):
        r = agent.LogReader({"initialized": True})
        self.write("x ERROR\n")
        self.assertEqual(r.read_new_lines(self.path), ["x ERROR"])

    def test_scan_collects_matches(self):
        os.makedirs(os.path.join(self.dir, "sub"))
        with open(os.path.join(self.dir, "sub", "b.log"), "w") as fh:
            fh.write("ok\nFATAL crash\n")
        self.write("INFO\nERROR one\n")
        config = {"applications": [{"name": "app1", "log_dir": self.dir, "file_pattern": "**/*.log"}]}
        r = agent.LogReader({}, read_from_start=True)
        errs = agent.scan(config, r, agent.Matcher([{"text": "ERROR"}, {"text": "FATAL"}]), now=1)
        self.assertEqual(sorted(e["line"] for e in errs), ["ERROR one", "FATAL crash"])
        self.assertTrue(r.state["initialized"])


if __name__ == "__main__":
    unittest.main()
