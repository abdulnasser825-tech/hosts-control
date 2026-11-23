import http.server
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import build_hosts as b  # noqa: E402
import monitor as m  # noqa: E402


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        code = {"/ok": 200, "/nf": 404, "/err": 500}.get(self.path.rsplit("/player_api.php", 1)[0], 200)
        self.send_response(code)
        self.end_headers()

    def log_message(self, *a):
        pass


def conf(**kw):
    base = {"server1": "http://a.example", "server2": "http://b.example", "server3": "http://c.example"}
    base.update(kw)
    return {k: v for k, v in base.items()}


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def url(self, p):
        return f"http://127.0.0.1:{self.port}{p}"

    def test_ok_and_4xx_count_as_alive(self):
        self.assertEqual(m.probe(self.url("/ok"), attempts=1, delay=0)[0], True)
        self.assertEqual(m.probe(self.url("/nf"), attempts=1, delay=0), (True, "HTTP 404"))

    def test_5xx_is_down_and_retried(self):
        sleeps = []
        ok, why = m.probe(self.url("/err"), attempts=3, delay=7, sleep=sleeps.append)
        self.assertEqual((ok, why, sleeps), (False, "HTTP 500", [7, 7]))

    def test_refused_connection_is_down_and_hides_host(self):
        ok, why = m.probe("http://127.0.0.1:1", attempts=1, delay=0)
        self.assertFalse(ok)
        self.assertNotIn("127.0.0.1", why)

    def test_recovers_on_second_attempt(self):
        calls = []

        class R:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def opener(req, timeout):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("boom")
            return R()
        self.assertEqual(m.probe("http://x.example", attempts=3, delay=0, opener=opener, sleep=lambda s: None), (True, "HTTP 200"))


class DecisionTests(unittest.TestCase):
    def test_decide(self):
        iss = {"number": 5, "body": "x <!-- down:1,3 -->"}
        self.assertEqual(m.decide({}, None), "none")
        self.assertEqual(m.decide({2: "x"}, None), "create")
        self.assertEqual(m.decide({1: "x", 3: "y"}, iss), "none")        # لا تكرار للتنبيه نفسه
        self.assertEqual(m.decide({1: "x"}, iss), "update")
        self.assertEqual(m.decide({}, iss), "close")

    def test_texts_have_no_addresses(self):
        t = m.title_for({2: "HTTP 500", 1: "x"}) + m.body_for({2: "HTTP 500"})
        self.assertIn("سيرفر 1، سيرفر 2", t)
        self.assertNotIn("http://", t)
        self.assertIn("<!-- down:2 -->", t)


class TargetsTests(unittest.TestCase):
    def targets(self, **kw):
        return [s for s, _ in m.targets(conf(**kw))]

    def test_selection(self):
        self.assertEqual(self.targets(), [1, 2, 3])
        self.assertEqual(self.targets(server2_on="off"), [1, 3])
        self.assertEqual(self.targets(server3_monitor="off"), [1, 2])
        self.assertEqual(self.targets(**{"global": "off"}), [])
        self.assertEqual(self.targets(server1=""), [2, 3])


class RunTests(unittest.TestCase):
    def run_with(self, vals, down_slots, issues):
        calls = []

        def gh(args, check=True):
            calls.append(args[:2])
            return json.dumps(issues) if args[:2] == ["issue", "list"] else ""
        act, down = m.run(vals, gh, probe_fn=lambda base: (False, "HTTP 502") if any(base.endswith(f"{s}.example") for s in down_slots) else (True, "HTTP 200"))
        return act, down, calls

    def test_create_update_close_none(self):
        v = conf(server1="http://s1.example", server2="http://s2.example", server3="http://s3.example")
        act, down, calls = self.run_with(v, [2], [])
        self.assertEqual((act, list(down)), ("create", [2]))
        self.assertIn(["issue", "create"], calls)
        self.assertEqual(self.run_with(v, [2], [{"number": 1, "body": "<!-- down:2 -->"}])[0], "none")
        self.assertEqual(self.run_with(v, [2, 3], [{"number": 1, "body": "<!-- down:2 -->"}])[0], "update")
        act, _, calls = self.run_with(v, [], [{"number": 1, "body": "<!-- down:2 -->"}])
        self.assertEqual(act, "close")
        self.assertIn(["issue", "close"], calls)


if __name__ == "__main__":
    unittest.main()
