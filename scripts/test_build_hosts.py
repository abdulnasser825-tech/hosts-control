import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import build_hosts as b  # noqa: E402

SECRET = "k" * 32


def conf(**kw):
    base = {"server1": "http://a.example:80", "server2": "b.example", "server3": "https://c.example:8080/"}
    base.update({k: v for k, v in kw.items()})
    return {k.replace("__", "_"): v for k, v in base.items()}


class BuildTests(unittest.TestCase):
    def build(self, vals, prev=0, now=1_800_000_000):
        body, _ = b.build_json(vals, SECRET, prev, now)
        return b.json.loads(body)

    def test_hosts_normalised_and_signed(self):
        d = self.build(conf())
        self.assertEqual(d["hosts"], ["http://a.example:80", "http://b.example", "https://c.example:8080"])
        self.assertTrue(b.verify(d, SECRET))
        d["hosts"][2] = "http://evil.example"
        self.assertFalse(b.verify(d, SECRET))

    def test_global_off_stops_everything_with_global_message(self):
        d = self.build(conf(**{"global": "off", "global_message": "صيانة", "server2_message": "خاص"}))
        self.assertEqual(d["hosts"], ["", "", ""])
        self.assertEqual(d["msgs"], ["صيانة", "صيانة", "صيانة"])
        self.assertTrue(b.verify(d, SECRET))

    def test_global_off_without_message_keeps_per_server_messages(self):
        d = self.build(conf(**{"global": "off", "server2_message": "خاص"}))
        self.assertEqual(d["msgs"], ["", "خاص", ""])

    def test_single_server_off_and_empty(self):
        d = self.build(conf(server2_on="off", server3="", server1_message="x"))
        self.assertEqual(d["hosts"], ["http://a.example:80", "", ""])

    def test_unsigned_layout_matches_app_canonical_order(self):
        d = self.build(conf(notice="N", notice_title="T", notice_button="B", whatsapp_number="966500000000",
                            whatsapp_days="45", notice_every_launch="on", app_version_code="12", min_version="12",
                            update_url="https://x.example/a.apk"))
        lines = b.canonical_lines(d)
        self.assertEqual(len(lines), 3 + 3 + 13)
        self.assertEqual(lines[6:10], ["N", "12", "https://x.example/a.apk", d["ts"]])
        self.assertEqual((lines[-3], lines[-2], lines[-1]), ("966500000000", "1", "45"))

    def test_ts_never_goes_backwards(self):
        self.assertEqual(self.build(conf(), prev=1_800_000_500)["ts"], "1800000501")
        self.assertEqual(self.build(conf(), prev=10)["ts"], "1800000000")

    def test_poisoned_prev_ts_ignored(self):
        self.assertEqual(self.build(conf(), prev=9_999_999_999)["ts"], "1800000000")

    def test_notice_until_is_end_of_day_ksa(self):
        d = self.build(conf(notice_until="2030-01-01"))
        self.assertEqual(datetime.fromtimestamp(d["notice_until"], timezone.utc).replace(tzinfo=None).isoformat(), "2030-01-01T20:59:59")

    def test_arabic_not_escaped_and_small(self):
        body, _ = b.build_json(conf(notice="مرحبا"), SECRET, 0, 1_800_000_000)
        self.assertIn("مرحبا", body)
        self.assertNotIn("\\u", body)

    def test_whatsapp_blank_number_means_off_flag_zero(self):
        self.assertEqual(self.build(conf())["wa_on"], 0)
        self.assertEqual(self.build(conf(whatsapp_number="966500000000"))["wa_on"], 1)
        self.assertEqual(self.build(conf(whatsapp_number="966500000000", whatsapp="off"))["wa_on"], 0)


class ValidationTests(unittest.TestCase):
    def bad(self, **kw):
        with self.assertRaises(b.ConfError):
            b.build_payload(conf(**kw))

    def test_rejects_credentials_query_spaces_port(self):
        self.bad(server1="http://u:p@a.example")
        self.bad(server1="http://a.example/get.php?username=a&password=b")
        self.bad(server1="http://a .example")
        self.bad(server1="http://a.example:99999")
        self.bad(server1="http://")

    def test_rejects_bad_numbers_and_urls(self):
        self.bad(min_version="5", app_version_code="4")
        self.bad(min_version="x")
        self.bad(whatsapp_number="123")
        self.bad(whatsapp_days="400")
        self.bad(update_url="http://x.example/a.apk")
        self.bad(notice_image="ftp://x")
        self.bad(notice_until="31-12-2030")
        self.bad(**{"global": "maybe"})
        self.bad(server1_message="x" * 201)

    def test_conf_parser_errors(self):
        for t in ("server1 http://a", "serverr1 = x", "global = on\nglobal = off"):
            with self.assertRaises(b.ConfError):
                b.parse_conf(t)
        self.assertEqual(b.parse_conf("\ufeff# c\n server1 = http://a.b/x=y \n"), {"server1": "http://a.b/x=y"})

    def test_shipped_conf_is_valid(self):
        path = os.path.join(os.path.dirname(__file__), "..", "hosts.conf")
        with open(path, encoding="utf-8") as f:
            b.build_payload(b.parse_conf(f.read()))


if __name__ == "__main__":
    unittest.main()
