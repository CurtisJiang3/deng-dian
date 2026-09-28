import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tibo_feishu as notifier


def payload(include_new=False):
    events = [
        {
            "id": "event-1",
            "type": "direct_reset",
            "scope": "Plus、Pro",
            "posts": [{"id": "post-1", "fullText": "预计重置额度。"}],
        }
    ]
    activities = [
        {
            "id": "post-1",
            "kind": "event_update",
            "action": "announce",
            "publishedAt": "2026-09-27T05:00:00+08:00",
            "eventIds": ["event-1"],
            "text": None,
            "url": "https://x.com/thsottiaux/status/1",
        },
        {
            "id": "related-1",
            "kind": "related",
            "action": None,
            "publishedAt": "2026-09-27T05:01:00+08:00",
            "eventIds": ["event-1"],
            "text": "普通回复",
            "url": "https://x.com/thsottiaux/status/2",
        },
    ]
    if include_new:
        activities.append(
            {
                "id": "post-2",
                "kind": "event_update",
                "action": "confirm",
                "publishedAt": "2026-09-27T06:00:00+08:00",
                "eventIds": ["event-1"],
                "text": "额度已经重置。",
                "url": "https://x.com/thsottiaux/status/3",
            }
        )
    return {"schemaVersion": 1, "activities": activities, "events": events, "monitor": {"status": "healthy"}}


class NotifierTests(unittest.TestCase):
    def test_selects_updates_and_falls_back_to_post_translation(self):
        messages = notifier.messages_from_payload(payload())
        self.assertEqual([m["id"] for m in messages], ["post-1"])
        self.assertEqual(messages[0]["body"], "预计重置额度。")
        self.assertIn("预告", messages[0]["title"])
        self.assertEqual(messages[0]["original_url"], "https://x.com/thsottiaux/status/1")

    def test_baseline_failure_retry_and_no_duplicate_on_304(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = notifier.Store(Path(tmp) / "state.sqlite3")
            try:
                with patch.object(notifier, "fetch_aihot", side_effect=[(payload(), '"v1"'), (payload(True), '"v2"'), (None, '"v2"')]):
                    with patch.object(notifier, "send_feishu") as send:
                        notifier.one_cycle(store, "https://open.feishu.cn/open-apis/bot/v2/hook/test", "")
                        self.assertEqual(store.counts(), {"skipped": 1})
                        send.assert_not_called()

                        send.side_effect = notifier.FetchError("temporary failure")
                        notifier.one_cycle(store, "https://open.feishu.cn/open-apis/bot/v2/hook/test", "")
                        self.assertEqual(store.counts(), {"skipped": 1, "pending": 1})
                        send.assert_called_once()

                        store.conn.execute("UPDATE deliveries SET next_attempt_at=0 WHERE activity_id='post-2'")
                        store.conn.commit()
                        send.side_effect = None
                        notifier.one_cycle(store, "https://open.feishu.cn/open-apis/bot/v2/hook/test", "")
                        self.assertEqual(store.counts(), {"skipped": 1, "sent": 1})
                        self.assertEqual(send.call_count, 2)
            finally:
                store.close()

    def test_feishu_message_uses_plain_text_and_tibo_keyword(self):
        message = notifier.messages_from_payload(payload())[0]
        data = notifier.feishu_payload(message)
        self.assertEqual(data["msg_type"], "text")
        content = data["content"]["text"]
        self.assertIn("Tibo", content)
        self.assertIn(message["original_url"], content)


if __name__ == "__main__":
    unittest.main()
