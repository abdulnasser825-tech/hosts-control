import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import build_hosts as b  # noqa: E402
import edit_conf as e  # noqa: E402

SHIPPED = open(os.path.join(os.path.dirname(__file__), "..", "hosts.conf"), encoding="utf-8").read()
BASE = SHIPPED.replace("server1 =\n", "server1 = http://a.example:80\n").replace("server2 =\n", "server2 = http://b.example\n")


def vals(text):
    return b.parse_conf(text)


class EditTests(unittest.TestCase):
    def test_global_off_with_message_keeps_everything_else(self):
        new = e.apply(BASE, "global_off", "1", "صيانة")
        v = vals(new)
        self.assertEqual((v["global"], v["global_message"]), ("off", "صيانة"))
        old_lines, new_lines = BASE.splitlines(), new.splitlines()
        self.assertEqual(len(old_lines), len(new_lines))
        self.assertEqual(sum(1 for x, y in zip(old_lines, new_lines) if x != y), 2)   # سطرا global وglobal_message فقط
        self.assertEqual([l for l in old_lines if l.startswith("#")], [l for l in new_lines if l.startswith("#")])

    def test_global_off_without_message_keeps_existing_message(self):
        step1 = e.apply(BASE, "global_off", "1", "رسالة قديمة")
        step2 = e.apply(e.apply(step1, "global_on", "1", ""), "global_off", "1", "")
        self.assertEqual(vals(step2)["global_message"], "رسالة قديمة")

    def test_server_off_on_and_message(self):
        v = vals(e.apply(BASE, "server_off", "2", "نعود غداً"))
        self.assertEqual((v["server2_on"], v["server2_message"]), ("off", "نعود غداً"))
        v = vals(e.apply(e.apply(BASE, "server_off", "2", ""), "server_on", "2", ""))
        self.assertEqual(v["server2_on"], "on")

    def test_set_host_normalises_and_turns_on(self):
        off = e.apply(BASE, "server_off", "3", "")
        v = vals(e.apply(off, "set_host", "3", " c.example:8080/ "))
        self.assertEqual((v["server3"], v["server3_on"]), ("http://c.example:8080", "on"))

    def test_clear_host(self):
        self.assertEqual(vals(e.apply(BASE, "clear_host", "1", ""))["server1"], "")

    def test_idempotent(self):
        once = e.apply(BASE, "server_off", "1", "x")
        self.assertEqual(once, e.apply(once, "server_off", "1", "x"))

    def test_missing_key_is_appended(self):
        new = e.apply("server1 = http://a.example\n", "server_off", "1", "")
        self.assertIn("server1_on = off", new)

    def test_server1_key_does_not_touch_server1_on(self):
        new = e.apply(BASE, "set_host", "1", "http://z.example")
        self.assertEqual(vals(new)["server1_on"], "on")
        self.assertEqual(vals(new)["server1"], "http://z.example")

    def test_bad_input_raises_and_nothing_to_write(self):
        for args in (("set_host", "1", "http://u:p@a.example"), ("set_host", "1", ""), ("server_off", "4", ""),
                     ("global_off", "1", "سطر\nثان"), ("nope", "1", ""), ("global_off", "1", "x" * 201)):
            with self.assertRaises(b.ConfError, msg=args):
                e.apply(BASE, *args)

    def test_cli_writes_only_on_change(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "hosts.conf")
            open(p, "w", encoding="utf-8").write(BASE)
            self.assertEqual(e.main(["--conf", p, "--action", "global_off", "--value", "صيانة"]), 0)
            self.assertEqual(vals(open(p, encoding="utf-8").read())["global"], "off")
            before = open(p, encoding="utf-8").read()
            self.assertEqual(e.main(["--conf", p, "--action", "set_host", "--server", "2", "--value", "bad host"]), 1)
            self.assertEqual(open(p, encoding="utf-8").read(), before)   # فشل = لا تغيير

    def test_result_still_builds_signed_json(self):
        body, _ = b.build_json(vals(e.apply(BASE, "global_off", "1", "صيانة")), "k" * 32, 0, 1_800_000_000)
        self.assertIn('"hosts":["","",""]', body)


if __name__ == "__main__":
    unittest.main()
