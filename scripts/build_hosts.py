#!/usr/bin/env python3
"""يقرأ hosts.conf ويبني hosts.json موقَّعاً بنفس صيغة تطبيق Spyro TV (HostRemote.canonical) وإضافة ووردبريس.

الاستعمال:
    python3 scripts/build_hosts.py --check          # فحص الملف فقط (لا يحتاج المفتاح)
    HOSTS_SECRET=... python3 scripts/build_hosts.py --out site

متغيرات البيئة:
    HOSTS_SECRET  المفتاح السري نفسه المضمَّن في التطبيق (HOSTS_SECRET في hosts.properties). إلزامي عند البناء.
    PREV_TS       آخر ts منشور (اختياري): يضمن ألا يتراجع ts أبداً، فالتطبيق يرفض ملفاً أقدم من آخر ملف قبله.
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

COUNT = 3
KSA = timezone(timedelta(hours=3))      # نهاية يوم «notice_until» بتوقيت السعودية
MAX_BYTES = 12 * 1024                    # حد التطبيق 16KB (HostRemote.MAX_BYTES)
FUTURE_SLACK_S = 86_400                  # كما في التطبيق: ts أبعد من يوم في المستقبل لا يُعتمد

ALLOWED = {
    "global", "global_message",
    *[f"server{i}{s}" for i in range(1, COUNT + 1) for s in ("", "_on", "_message", "_monitor")],
    "notice_title", "notice", "notice_button", "notice_url", "notice_image", "notice_until", "notice_every_launch",
    "min_version", "app_version_code", "update_url",
    "whatsapp", "whatsapp_number", "whatsapp_days",
}


class ConfError(Exception):
    pass


# ───────────────────────── قراءة الملف ─────────────────────────
def parse_conf(text):
    vals = {}
    for n, raw in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfError(f"السطر {n}: ينقصه علامة = ({line})")
        k, v = line.split("=", 1)
        k, v = k.strip().lower(), v.strip()
        if k not in ALLOWED:
            raise ConfError(f"السطر {n}: مفتاح غير معروف «{k}» (تأكد من الإملاء)")
        if k in vals:
            raise ConfError(f"السطر {n}: المفتاح «{k}» مكرر")
        vals[k] = v
    return vals


def flag(vals, key, default):
    v = vals.get(key, "").strip().lower()
    if v == "":
        return default
    if v in ("on", "yes", "true", "1"):
        return True
    if v in ("off", "no", "false", "0"):
        return False
    raise ConfError(f"«{key}» يقبل on أو off فقط (وجدت: {v})")


def text(vals, key, limit):
    v = vals.get(key, "").strip()
    if len(v) > limit:
        raise ConfError(f"«{key}» أطول من {limit} حرفاً ({len(v)})")
    return v


def https_url(vals, key, limit=300):
    v = vals.get(key, "").strip()
    if not v:
        return ""
    sp = urlsplit(v)
    if sp.scheme.lower() != "https" or not sp.hostname or len(v) > limit or re.search(r"\s", v):
        raise ConfError(f"«{key}» يجب أن يكون رابط https صحيحاً (حتى {limit} حرفاً) بلا مسافات")
    return v


def host_value(raw, key):
    v = raw.strip()
    if not v:
        return ""
    if re.search(r"\s", v):
        raise ConfError(f"«{key}»: العنوان فيه مسافة")
    if not re.match(r"(?i)^https?://", v):
        v = "http://" + v
    if any(c in v for c in "?#"):
        raise ConfError(f"«{key}»: ضع الهوست فقط، بلا ? أو # (وبلا username/password)")
    try:
        sp = urlsplit(v)
        port = sp.port
    except ValueError:
        raise ConfError(f"«{key}»: عنوان أو منفذ غير صالح")
    if not sp.hostname:
        raise ConfError(f"«{key}»: عنوان غير صالح")
    if "@" in sp.netloc:
        raise ConfError(f"«{key}»: لا تضع اسم مستخدم أو كلمة مرور في العنوان (الملف علني)")
    if port is not None and not (1 <= port <= 65535):
        raise ConfError(f"«{key}»: المنفذ خارج النطاق")
    return v.rstrip("/")


# ───────────────────────── البناء ─────────────────────────
def build_payload(vals):
    """يرجع (payload بلا ts/sig، حالة نصية للعرض)."""
    g_on = flag(vals, "global", True)
    g_msg = text(vals, "global_message", 200)
    hosts, msgs, status = [], [], []
    for i in range(1, COUNT + 1):
        url = host_value(vals.get(f"server{i}", ""), f"server{i}")
        on = flag(vals, f"server{i}_on", True)
        msg = text(vals, f"server{i}_message", 200)
        if not g_on:
            state, url, msg = "متوقف (الإيقاف الشامل)", "", (g_msg or msg)
        elif not on:
            state, url = "متوقف", ""
        elif not url:
            state = "متوقف (العنوان فارغ)"
        else:
            state = "يعمل"
        hosts.append(url)
        msgs.append(msg)
        status.append(f"سيرفر {i}: {state}")

    seen = {}
    for i, h in enumerate(hosts, 1):
        if h and h.lower() in seen:
            print(f"::warning::سيرفر {i} يكرّر عنوان سيرفر {seen[h.lower()]}")
        elif h:
            seen[h.lower()] = i

    notice = text(vals, "notice", 300)
    title = text(vals, "notice_title", 80)
    btn = text(vals, "notice_button", 30)
    nurl = https_url(vals, "notice_url")
    nimg = https_url(vals, "notice_image")
    until = 0
    d = vals.get("notice_until", "").strip()
    if d:
        try:
            end = datetime.strptime(d, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=KSA)
        except ValueError:
            raise ConfError("«notice_until» يجب أن يكون بصيغة YYYY-MM-DD (مثل 2026-12-31)")
        until = max(0, int(end.timestamp()))
    every = 1 if flag(vals, "notice_every_launch", False) else 0

    try:
        min_v = int(vals.get("min_version", "").strip() or 0)
        app_v = int(vals.get("app_version_code", "").strip() or 0)
    except ValueError:
        raise ConfError("«min_version» و«app_version_code» أرقام صحيحة")
    if min_v < 0:
        raise ConfError("«min_version» لا يكون سالباً")
    if min_v > app_v:
        raise ConfError(
            f"«min_version» ({min_v}) أعلى من «app_version_code» ({app_v}): رقم أعلى من آخر نسخة منشورة يحجب كل المستخدمين. "
            "إن كانت نسخة بهذا الرقم منشورة فعلاً فحدّث app_version_code أولاً."
        )
    upd = https_url(vals, "update_url")

    wa = re.sub(r"\D+", "", vals.get("whatsapp_number", ""))
    if wa and not (8 <= len(wa) <= 15):
        raise ConfError("«whatsapp_number» من 8 إلى 15 رقماً بالصيغة الدولية (مثل 9665XXXXXXXX)")
    try:
        days = int(vals.get("whatsapp_days", "").strip() or 30)
    except ValueError:
        raise ConfError("«whatsapp_days» رقم صحيح")
    if not 1 <= days <= 365:
        raise ConfError("«whatsapp_days» بين 1 و365")
    wa_on = 1 if (flag(vals, "whatsapp", True) and wa) else 0
    if not wa:
        print("::notice::whatsapp_number فارغ: التطبيق سيستعمل رقمه المضمَّن ويُبقي التجديد مفعَّلاً")

    payload = {
        "hosts": hosts, "msgs": msgs, "notice": notice, "min": min_v, "update_url": upd,
        "notice_title": title, "notice_btn": btn, "notice_url": nurl, "notice_img": nimg,
        "notice_until": until, "notice_every": every,
        "wa_number": wa, "wa_on": wa_on, "wa_days": days,
    }
    return payload, status


def canonical_lines(d):
    """يطابق HostRemote.canonical في التطبيق و nova_hosts_build_json في الإضافة تماماً."""
    return list(d["hosts"]) + list(d["msgs"]) + [
        d["notice"], str(d["min"]), d["update_url"], d["ts"],
        d["notice_title"], d["notice_btn"], d["notice_url"], d["notice_img"],
        str(d["notice_until"]), str(d["notice_every"]),
        d["wa_number"], str(d["wa_on"]), str(d["wa_days"]),
    ]


def sign(d, secret):
    return hmac.new(secret.encode("utf-8"), "\n".join(canonical_lines(d)).encode("utf-8"), hashlib.sha256).hexdigest()


def verify(d, secret):
    return hmac.compare_digest(sign(d, secret), str(d.get("sig", "")).strip().lower())


def next_ts(prev, now=None):
    now = int(time.time()) if now is None else now
    if prev > now + FUTURE_SLACK_S:
        print(f"::warning::PREV_TS ({prev}) في المستقبل البعيد؛ تجاهلته")
        prev = 0
    return max(now, prev + 1)


def build_json(vals, secret, prev_ts=0, now=None):
    payload, status = build_payload(vals)
    d = {**payload}
    d["ts"] = str(next_ts(prev_ts, now))
    d["sig"] = sign(d, secret)
    out = {k: d[k] for k in (
        "hosts", "msgs", "notice", "min", "update_url", "ts", "notice_title", "notice_btn", "notice_url",
        "notice_img", "notice_until", "notice_every", "wa_number", "wa_on", "wa_days", "sig")}
    body = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    if len(body.encode("utf-8")) > MAX_BYTES:
        raise ConfError(f"الملف الناتج أكبر من {MAX_BYTES // 1024}KB؛ اختصر الرسائل")
    # فحص ذاتي: كما سيقرؤه التطبيق (3 هوستات و3 رسائل، بلا أسطر جديدة، وتوقيع صحيح)
    chk = json.loads(body)
    assert len(chk["hosts"]) == COUNT and len(chk["msgs"]) == COUNT
    assert not any("\n" in s or "\r" in s for s in chk["hosts"] + chk["msgs"] + [chk["notice"], chk["update_url"]])
    assert verify(chk, secret)
    return body, status


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--conf", default="hosts.conf")
    ap.add_argument("--out", default="site")
    ap.add_argument("--check", action="store_true", help="فحص الملف فقط بلا توقيع")
    a = ap.parse_args(argv)
    try:
        with open(a.conf, encoding="utf-8") as f:
            vals = parse_conf(f.read())
        if a.check:
            _, status = build_payload(vals)
            body = None
        else:
            secret = os.environ.get("HOSTS_SECRET", "")
            if not secret:
                raise ConfError("السر HOSTS_SECRET غير مضبوط في Settings ← Secrets ← Actions")
            if secret != secret.strip():
                raise ConfError("HOSTS_SECRET فيه مسافة أو سطر زائد في أوله/آخره؛ أعد لصقه بلا زوائد")
            if len(secret) < 16:
                print("::warning::HOSTS_SECRET قصير (أقل من 16)؛ يُفضَّل 32 حرفاً عشوائياً")
            prev = int(os.environ.get("PREV_TS", "0") or 0)
            body, status = build_json(vals, secret, prev)
    except (ConfError, OSError, ValueError) as e:
        print(f"::error::{e}")
        print(f"خطأ: {e}", file=sys.stderr)
        return 1
    for s in status:
        print(s)
    if body is not None:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "hosts.json"), "w", encoding="utf-8", newline="") as f:
            f.write(body)
        for name, content in (("index.html", "<!doctype html><meta charset=utf-8>\n"), (".nojekyll", "")):
            with open(os.path.join(a.out, name), "w", encoding="utf-8") as f:
                f.write(content)
        summ = os.environ.get("GITHUB_STEP_SUMMARY")
        if summ:
            with open(summ, "a", encoding="utf-8") as f:
                f.write("### تم النشر\n" + "\n".join(f"- {s}" for s in status) + f"\n- ts: `{json.loads(body)['ts']}`\n")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
