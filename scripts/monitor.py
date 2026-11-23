#!/usr/bin/env python3
"""يفحص الهوستات المفعَّلة في hosts.conf (وصول فقط، بلا بيانات دخول) وينبّهك عبر GitHub Issue إن سقط أحدها.
التنبيه لا يحمل عناوين الهوستات (سيرفر N فقط). لا يوقف شيئاً تلقائياً: القرار لك (workflow «control»).

    python3 scripts/monitor.py            # داخل GitHub Actions (يحتاج gh و GH_TOKEN)
    python3 scripts/monitor.py --dry-run  # يفحص ويطبع القرار بلا إنشاء/تعديل issues
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_hosts as b  # noqa: E402

MARK = re.compile(r"<!-- down:([\d,]*) -->")
LABEL = "host-down"


def targets(vals):
    """[(رقم الخانة، العنوان)] للهوستات التي تعمل فعلاً (بعد الإيقاف الشامل وإيقاف الخانة) وغير المستثناة من الفحص."""
    payload, _ = b.build_payload(vals)
    return [(i, h) for i, h in enumerate(payload["hosts"], 1) if h and b.flag(vals, f"server{i}_monitor", True)]


def _why(exc, base):
    if isinstance(exc, (socket.timeout, TimeoutError)) or isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError)):
        return "انتهت المهلة"
    if isinstance(exc, urllib.error.URLError):
        r = str(exc.reason)
    else:
        r = f"{type(exc).__name__}: {exc}"
    host = urllib.request.urlparse(base).hostname or ""
    return (r.replace(host, "***") if host else r)[:80]


def probe(base, attempts=3, delay=20, timeout=10, opener=urllib.request.urlopen, sleep=time.sleep):
    """(يعمل؟، سبب). أي رد HTTP أقل من 500 = الخادم يستجيب؛ 5xx أو انقطاع/مهلة بعد كل المحاولات = لا يستجيب."""
    url = base.rstrip("/") + "/player_api.php"
    why = ""
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (hosts-monitor)"})
            with opener(req, timeout=timeout) as r:
                return True, f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            if e.code < 500:
                return True, f"HTTP {e.code}"
            why = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001 — أي فشل اتصال يُحتسب
            why = _why(e, base)
        if i < attempts - 1:
            sleep(delay)
    return False, why


def decide(down, issue):
    """down: {slot: reason}. issue: None أو {'number','body'}. يرجع 'none' | 'create' | 'update' | 'close'."""
    cur = sorted(down)
    prev = None
    if issue:
        m = MARK.search(issue.get("body") or "")
        prev = sorted(int(x) for x in m.group(1).split(",") if x) if m else []
    if not cur:
        return "close" if issue else "none"
    if not issue:
        return "create"
    return "none" if cur == prev else "update"


def title_for(down):
    return "🔴 هوست لا يستجيب: " + "، ".join(f"سيرفر {i}" for i in sorted(down))


def body_for(down):
    lines = ["تنبيه آلي من الفحص الدوري (بلا عناوين).", ""]
    lines += [f"- 🔴 سيرفر {i}: {why or 'لا يستجيب'}" for i, why in sorted(down.items())]
    lines += ["", "للإيقاف: Actions ← control ← server_off (أو global_off). قد يكون سبب الفشل حجب الهوست لعناوين GitHub؛ "
              "إن تكرر بلا سبب فاجعل `serverN_monitor = off` لذلك السيرفر.",
              f"<!-- down:{','.join(str(i) for i in sorted(down))} -->"]
    return "\n".join(lines)


def gh_cli(args, check=True):
    p = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError(f"gh {' '.join(args[:2])}: {p.stderr.strip()[:300]}")
    return p.stdout


def run(vals, gh, probe_fn=probe):
    down = {}
    for slot, base in targets(vals):
        ok, why = probe_fn(base)
        print(f"سيرفر {slot}: {'يعمل' if ok else 'لا يستجيب'} ({why})")
        if not ok:
            down[slot] = why
    issues = json.loads(gh(["issue", "list", "--label", LABEL, "--state", "open", "--json", "number,body", "--limit", "1"]) or "[]")
    issue = issues[0] if issues else None
    act = decide(down, issue)
    if act == "create":
        gh(["label", "create", LABEL, "--color", "D73A4A", "--description", "هوست لا يستجيب", "--force"], check=False)
        gh(["issue", "create", "--title", title_for(down), "--body", body_for(down), "--label", LABEL])
    elif act == "update":
        n = str(issue["number"])
        gh(["issue", "edit", n, "--title", title_for(down), "--body", body_for(down)])
        gh(["issue", "comment", n, "--body", "تغيّرت الحالة:\n" + body_for(down)])
    elif act == "close":
        gh(["issue", "close", str(issue["number"]), "--comment", "✅ لا مشاكل حالياً (أو لا هوست مفعَّل للفحص)."])
    print(f"القرار: {act}")
    return act, down


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--conf", default="hosts.conf")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        with open(a.conf, encoding="utf-8") as f:
            vals = b.parse_conf(f.read())
        gh = (lambda args, check=True: "[]" if args[:2] == ["issue", "list"] else print("gh", *args[:2], "(dry-run)") or "") if a.dry_run else gh_cli
        run(vals, gh)
    except (b.ConfError, OSError, RuntimeError) as e:
        print(f"::error::{e}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
