#!/usr/bin/env python3
"""Poll AIHOT's Tibo reset feed and notify a Feishu group bot."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import error, request
from urllib.parse import urlparse


APP_DIR = Path(__file__).resolve().parent
AIHOT_URL = "https://aihot.news/api/v1/codex-resets"
AIHOT_PAGE = "https://aihot.news/codex-reset"
BEIJING = timezone(timedelta(hours=8))
MAX_RESPONSE_BYTES = 5_000_000
LOG = logging.getLogger("tibo_feishu")


def load_dotenv(path: Path) -> None:
    """Read simple KEY=VALUE lines without evaluating shell expressions."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value.startswith(("'", '"')):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "时间未提供"


def clean_text(value: object, limit: int = 800) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split()).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def safe_post_url(value: object) -> str:
    if not isinstance(value, str):
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in {"x.com", "twitter.com"}:
        return ""
    if "/status/" not in parsed.path:
        return ""
    return value


def validate_payload(data: object) -> dict:
    if not isinstance(data, dict):
        raise ValueError("AIHOT response is not an object")
    if not isinstance(data.get("activities"), list) or not isinstance(data.get("events"), list):
        raise ValueError("AIHOT response lacks activities or events")
    return data


def event_type_label(events: list[dict], event_ids: list[str]) -> str:
    types = {event.get("type") for event in events}
    if not types:
        types = {"reset_credit" if eid.startswith("banked-") else "direct_reset" for eid in event_ids if eid.startswith(("banked-", "reset-"))}
    if types == {"direct_reset"}:
        return "Codex 额度重置"
    if types == {"reset_credit"}:
        return "重置卡发放"
    if types == {"direct_reset", "reset_credit"}:
        return "额度重置与重置卡"
    return "Codex 重置动态"


def activity_message(activity: dict, events_by_id: dict[str, dict]) -> dict | None:
    if activity.get("kind") != "event_update" or activity.get("action") not in {"announce", "confirm"}:
        return None
    activity_id = activity.get("id")
    if not isinstance(activity_id, str) or not activity_id:
        return None
    event_ids = activity.get("eventIds")
    event_ids = [eid for eid in event_ids if isinstance(eid, str)] if isinstance(event_ids, list) else []
    events = [events_by_id[eid] for eid in event_ids if eid in events_by_id]
    stage = "确认" if activity["action"] == "confirm" else "预告"
    title = f"Tibo · {event_type_label(events, event_ids)}{stage}"

    post_excerpt = ""
    post_full = ""
    for event in events:
        for post in event.get("posts") or []:
            if isinstance(post, dict) and post.get("id") == activity_id:
                post_excerpt = clean_text(post.get("text"))
                post_full = clean_text(post.get("fullText"))
                break
        if post_excerpt:
            break
    body = post_excerpt or clean_text(activity.get("text")) or post_full
    if not body:
        body = "AIHOT 收录了这条重置动态，请查看原帖核对详情。"

    scopes = list(dict.fromkeys(clean_text(e.get("scope"), 120) for e in events if e.get("scope")))
    scope = "；".join(s for s in scopes if s)
    original_url = safe_post_url(activity.get("url"))
    return {
        "id": activity_id,
        "published_at": str(activity.get("publishedAt") or ""),
        "title": title,
        "stage": stage,
        "body": body,
        "scope": scope,
        "original_url": original_url,
        "aihot_url": AIHOT_PAGE,
    }


def messages_from_payload(data: dict) -> list[dict]:
    events = {e["id"]: e for e in data["events"] if isinstance(e, dict) and isinstance(e.get("id"), str)}
    messages = []
    seen = set()
    for item in data["activities"]:
        if not isinstance(item, dict):
            continue
        message = activity_message(item, events)
        if message and message["id"] not in seen:
            messages.append(message)
            seen.add(message["id"])
    return sorted(messages, key=lambda m: (m["published_at"], m["id"]))


def feishu_payload(message: dict, sign_secret: str = "", keyword: str = "Tibo") -> dict:
    lines = [
        f"Tibo · {keyword}",
        message["title"],
        f"北京时间：{parse_time(message['published_at'])}",
        f"阶段：{message['stage']}",
    ]
    if message["scope"]:
        lines.append(f"范围：{message['scope']}")
    lines.append(f"内容：{message['body']}")
    if message["original_url"]:
        lines.append(f"Tibo 原帖：{message['original_url']}")
    lines.append(f"AIHOT 日历：{message['aihot_url']}")
    lines.append("个人额度与重置卡以 Codex 内显示为准。")
    payload = {"msg_type": "text", "content": {"text": "\n".join(lines)}}
    if sign_secret:
        timestamp = str(int(time.time()))
        sign_input = f"{timestamp}\n{sign_secret}".encode("utf-8")
        payload["timestamp"] = timestamp
        payload["sign"] = base64.b64encode(hmac.new(sign_input, digestmod=hashlib.sha256).digest()).decode("ascii")
    return payload


class FetchError(Exception):
    def __init__(self, message: str, retry_after: int = 0):
        super().__init__(message)
        self.retry_after = retry_after


def fetch_aihot(etag: str = "") -> tuple[dict | None, str]:
    headers = {"Accept": "application/json", "User-Agent": "TiboFeishuNotifier/1.0"}
    if etag:
        headers["If-None-Match"] = etag
    req = request.Request(AIHOT_URL, headers=headers)
    try:
        with request.urlopen(req, timeout=20) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise FetchError("AIHOT response exceeds size limit")
            data = validate_payload(json.loads(body))
            return data, response.headers.get("ETag", "")
    except error.HTTPError as exc:
        if exc.code == 304:
            return None, etag
        retry_after = 0
        if exc.code == 429:
            try:
                retry_after = int(exc.headers.get("Retry-After", "0"))
            except ValueError:
                pass
        raise FetchError(f"AIHOT HTTP {exc.code}", retry_after) from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        raise FetchError(f"AIHOT fetch failed: {type(exc).__name__}: {exc}") from exc


def send_feishu(webhook_url: str, sign_secret: str, message: dict, keyword: str = "Tibo") -> None:
    body = json.dumps(feishu_payload(message, sign_secret, keyword), ensure_ascii=False).encode("utf-8")
    req = request.Request(webhook_url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    try:
        with request.urlopen(req, timeout=20) as response:
            result = json.loads(response.read(100_000))
    except error.HTTPError as exc:
        raise FetchError(f"Feishu HTTP {exc.code}") from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise FetchError(f"Feishu send failed: {type(exc).__name__}") from exc
    if not isinstance(result, dict) or result.get("code", result.get("StatusCode")) != 0:
        code = result.get("code", result.get("StatusCode", "unknown")) if isinstance(result, dict) else "invalid JSON"
        detail = clean_text(result.get("msg", result.get("StatusMessage")), 120) if isinstance(result, dict) else ""
        raise FetchError(f"Feishu rejected message: code={code}, msg={detail}")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS deliveries (
                activity_id TEXT PRIMARY KEY,
                published_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('skipped','pending','sent')),
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at INTEGER NOT NULL DEFAULT 0,
                sent_at TEXT,
                last_error TEXT
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def get(self, key: str) -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else ""

    def set(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def baseline(self, messages: list[dict], etag: str) -> int:
        with self.conn:
            for msg in messages:
                self.conn.execute(
                    "INSERT OR IGNORE INTO deliveries(activity_id,published_at,payload,state) VALUES(?,?,?,'skipped')",
                    (msg["id"], msg["published_at"], json.dumps(msg, ensure_ascii=False)),
                )
            self.set("initialized", "1")
            self.set("etag", etag)
            self.set("last_fetch_at", now_iso())
        return len(messages)

    def ingest(self, messages: list[dict], etag: str) -> int:
        inserted = 0
        with self.conn:
            for msg in messages:
                cursor = self.conn.execute(
                    "INSERT OR IGNORE INTO deliveries(activity_id,published_at,payload,state) VALUES(?,?,?,'pending')",
                    (msg["id"], msg["published_at"], json.dumps(msg, ensure_ascii=False)),
                )
                inserted += cursor.rowcount
            self.set("etag", etag)
            self.set("last_fetch_at", now_iso())
        return inserted

    def mark_checked(self) -> None:
        with self.conn:
            self.set("last_fetch_at", now_iso())

    def due(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM deliveries WHERE state='pending' AND next_attempt_at<=? ORDER BY published_at,activity_id",
            (int(time.time()),),
        ).fetchall()

    def sent(self, activity_id: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE deliveries SET state='sent',sent_at=?,last_error=NULL WHERE activity_id=?", (now_iso(), activity_id))

    def failed(self, activity_id: str, attempts: int, detail: str, retry_after: int = 0) -> None:
        delay = max(retry_after, min(60 * 2 ** min(attempts, 6), 3600))
        with self.conn:
            self.conn.execute(
                "UPDATE deliveries SET attempts=?,next_attempt_at=?,last_error=? WHERE activity_id=?",
                (attempts, int(time.time()) + delay, detail[:300], activity_id),
            )

    def counts(self) -> dict[str, int]:
        return {row["state"]: row["n"] for row in self.conn.execute("SELECT state,COUNT(*) n FROM deliveries GROUP BY state")}


@contextmanager
def single_instance(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError("Another notifier process is running") from exc
    try:
        yield
    finally:
        handle.close()


def one_cycle(store: Store, webhook_url: str, sign_secret: str, baseline_only: bool = False, keyword: str = "Tibo") -> None:
    initialized = store.get("initialized") == "1"
    data, etag = fetch_aihot(store.get("etag") if initialized else "")
    if data is None:
        store.mark_checked()
        LOG.info("AIHOT unchanged (304)")
    else:
        messages = messages_from_payload(data)
        if not initialized:
            count = store.baseline(messages, etag)
            LOG.info("Baseline saved: %d historical activities; no notification sent", count)
        elif not baseline_only:
            count = store.ingest(messages, etag)
            LOG.info("AIHOT checked: %d relevant activities, %d new", len(messages), count)
        monitor = data.get("monitor")
        if isinstance(monitor, dict) and monitor.get("status") not in (None, "healthy"):
            LOG.warning("AIHOT monitor status: %s", monitor.get("status"))

    if baseline_only:
        return
    if not webhook_url:
        raise RuntimeError("FEISHU_WEBHOOK_URL is required for sending")
    for row in store.due():
        try:
            send_feishu(webhook_url, sign_secret, json.loads(row["payload"]), keyword)
        except FetchError as exc:
            store.failed(row["activity_id"], row["attempts"] + 1, str(exc), exc.retry_after)
            LOG.warning("Delivery failed for %s: %s", row["activity_id"], exc)
        else:
            store.sent(row["activity_id"])
            LOG.info("Delivered activity %s", row["activity_id"])


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv(APP_DIR / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "once", "run", "status", "preview", "test-webhook"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db_path = Path(os.environ.get("DB_PATH", str(APP_DIR / "data" / "tibo.sqlite3"))).expanduser().resolve()
    log_path = Path(os.environ.get("LOG_PATH", str(db_path.parent / "tibo.log"))).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=2, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(file_handler)
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
    sign_secret = os.environ.get("FEISHU_SIGN_SECRET", "").strip()
    keyword = os.environ.get("FEISHU_KEYWORD", "Tibo").strip() or "Tibo"
    poll_minutes = int(os.environ.get("POLL_MINUTES", "5"))
    if not 1 <= poll_minutes <= 1440:
        parser.error("POLL_MINUTES must be between 1 and 1440")
    if webhook_url:
        parsed = urlparse(webhook_url)
        if parsed.scheme != "https" or parsed.hostname not in {"open.feishu.cn", "open.larkoffice.com"}:
            parser.error("FEISHU_WEBHOOK_URL must be an HTTPS Feishu or Lark webhook")

    if args.command == "preview":
        data, _ = fetch_aihot()
        for msg in list(reversed(messages_from_payload(data)))[:5]:
            print(f"{parse_time(msg['published_at'])} | {msg['title']} | {msg['body'][:100]}")
        return 0
    if args.command == "test-webhook":
        if not webhook_url:
            parser.error("Set FEISHU_WEBHOOK_URL in .env before testing")
        send_feishu(
            webhook_url,
            sign_secret,
            {
                "published_at": now_iso(),
                "title": "Tibo · 连接测试",
                "stage": "测试",
                "scope": "",
                "body": "AIHOT → 飞书通知器连接成功。这是一条手动测试消息。",
                "original_url": "",
                "aihot_url": AIHOT_PAGE,
            },
            keyword,
        )
        print("Feishu test message sent")
        return 0

    store = Store(db_path)
    try:
        if args.command == "status":
            print(json.dumps({"initialized": store.get("initialized") == "1", "last_fetch_at": store.get("last_fetch_at"), "counts": store.counts()}, ensure_ascii=False, indent=2))
            return 0
        if args.command in {"once", "run"} and not webhook_url:
            parser.error("Set FEISHU_WEBHOOK_URL in .env before starting notifications")
        with single_instance(db_path.with_suffix(".lock")):
            if args.command == "init":
                if store.get("initialized") == "1":
                    LOG.info("Baseline already exists; no changes made")
                else:
                    one_cycle(store, "", "", baseline_only=True)
                return 0
            if args.command == "once":
                one_cycle(store, webhook_url, sign_secret, keyword=keyword)
                return 0
            while True:
                try:
                    one_cycle(store, webhook_url, sign_secret, keyword=keyword)
                    wait_seconds = poll_minutes * 60
                except FetchError as exc:
                    LOG.warning("Poll failed: %s", exc)
                    wait_seconds = max(poll_minutes * 60, exc.retry_after)
                except Exception:
                    LOG.exception("Poll failed unexpectedly")
                    wait_seconds = poll_minutes * 60
                time.sleep(wait_seconds)
    finally:
        store.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
    except (FetchError, RuntimeError) as exc:
        LOG.error("%s", exc)
        sys.exit(1)
