#!/usr/bin/env python3
"""يعدّل hosts.conf بإجراء واحد (إيقاف/تشغيل/تغيير هوست) مع إبقاء التعليقات كما هي، ثم يتحقق من الملف الناتج
بنفس فحص النشر؛ عند أي خطأ لا يُكتب شيء. يُستعمل من workflow «control»."""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_hosts as b  # noqa: E402

ACTIONS = {
    "global_off": "إيقاف شامل لكل السيرفرات",
    "global_on": "إلغاء الإيقاف الشامل",
    "server_off": "إيقاف سيرفر",
    "server_on": "تشغيل سيرفر",
    "set_host": "تغيير عنوان سيرفر وتشغيله",
    "clear_host": "مسح عنوان سيرفر",
}
NEEDS_SERVER = {"server_off", "server_on", "set_host", "clear_host"}


def set_key(text, key, value):
    """يستبدل سطر المفتاح (غير المعلَّق) أو يضيفه في النهاية؛ بقية الأسطر والتعليقات كما هي."""
    lines = text.splitlines(keepends=True)
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=", re.I)
    new = f"{key} = {value}".rstrip() + "\n"
    for i, ln in enumerate(lines):
        if not ln.lstrip().startswith("#") and pat.match(ln):
            lines[i] = new
            return "".join(lines)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return "".join(lines) + new


def apply(text, action, server, value):
    value = (value or "").strip()
    if "\n" in value or "\r" in value:
        raise b.ConfError("القيمة سطر واحد فقط")
    if action not in ACTIONS:
        raise b.ConfError(f"إجراء غير معروف: {action}")
    n = str(server).strip()
    if action in NEEDS_SERVER and n not in ("1", "2", "3"):
        raise b.ConfError("رقم السيرفر يجب أن يكون 1 أو 2 أو 3")
    if action == "global_off":
        text = set_key(text, "global", "off")
        if value:
            text = set_key(text, "global_message", value)
    elif action == "global_on":
        text = set_key(text, "global", "on")
    elif action == "server_off":
        text = set_key(text, f"server{n}_on", "off")
        if value:
            text = set_key(text, f"server{n}_message", value)
    elif action == "server_on":
        text = set_key(text, f"server{n}_on", "on")
    elif action == "set_host":
        if not value:
            raise b.ConfError("اكتب العنوان الجديد في خانة القيمة")
        text = set_key(text, f"server{n}", b.host_value(value, f"server{n}"))
        text = set_key(text, f"server{n}_on", "on")
    elif action == "clear_host":
        text = set_key(text, f"server{n}", "")
    b.build_payload(b.parse_conf(text))   # نفس فحص النشر؛ يرفع ConfError إن صار الملف غير صالح
    return text


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--conf", default="hosts.conf")
    ap.add_argument("--action", required=True, choices=sorted(ACTIONS))
    ap.add_argument("--server", default="1")
    ap.add_argument("--value", default="")
    a = ap.parse_args(argv)
    try:
        with open(a.conf, encoding="utf-8", newline="") as f:
            old = f.read()
        new = apply(old, a.action, a.server, a.value)
        _, status = b.build_payload(b.parse_conf(new))
    except (b.ConfError, OSError) as e:
        print(f"::error::{e}")
        print(f"خطأ: {e}", file=sys.stderr)
        return 1
    changed = new != old
    if changed:
        with open(a.conf, "w", encoding="utf-8", newline="") as f:
            f.write(new)
    what = ACTIONS[a.action] + (f" {a.server}" if a.action in NEEDS_SERVER else "")
    print(("تم: " if changed else "لا تغيير (مطبَّق مسبقاً): ") + what)
    for s in status:
        print(s)
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write(f"### {what}\n" + ("سيُنشر خلال دقيقة تقريباً.\n" if changed else "لا تغيير: الإعداد مطابق.\n")
                    + "\n".join(f"- {s}" for s in status) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
