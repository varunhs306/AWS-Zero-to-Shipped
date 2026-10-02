"""Offline tests: no AWS account and no network needed (AWS clients are replaced with fakes).

Run:  python app/tests/test_offline.py      (or: python -m pytest app/tests)
"""
import io
import json
import os
import secrets
import sys
import urllib.error
import unittest.mock as um

os.environ.update(TABLE_NAME="t", MEDIA_BUCKET="b", AWS_DEFAULT_REGION="us-east-1", AI_KEY_PARAM="/k", AI_MODEL="m",
                  AI_ENDPOINT="https://example.invalid", ADMIN_PARAM="/a", ALERT_TOPIC="arn:t", WORKER_FUNCTION="w")
for name in ["boto3", "boto3.dynamodb", "boto3.dynamodb.conditions"]:
    sys.modules[name] = um.MagicMock()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import api  # noqa: E402
import polish  # noqa: E402
import worker  # noqa: E402


class ConditionFailed(Exception):
    pass


# ---------- AI writing helper: every failure becomes a clean fallback ----------

TRANSCRIPT = " ".join(["word"] * 60)
GOOD = {"originalTitle": "T", "originalParagraphs": [" ".join(["wort"] * 55)], "title": "Title",
        "paragraphs": ["An English story."], "mood": "funny"}


def model_reply(obj, finish="STOP"):
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return {"candidates": [{"finishReason": finish, "content": {"parts": [{"text": text}]}}]}


def http_error(code, body=""):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body.encode()))


def run_helper(responses):
    """Feed the helper a scripted sequence of model replies/errors; return (outcome, circuit trips)."""
    trips, seq = [], iter(responses)
    polish.circuit_open = lambda: False
    polish.trip = trips.append
    polish.api_key = lambda: "k"
    polish.time.sleep = lambda s: None

    def fake(_text, _language, _kind="spoken"):
        r = next(seq)
        if isinstance(r, Exception):
            raise r
        return r
    polish.call_model = fake
    try:
        return "polished:" + polish.polish(TRANSCRIPT, "German")["mood"], trips
    except polish.PolishUnavailable as e:
        return e.reason, trips


def test_helper_failure_modes():
    cases = [
        ("success", [model_reply(GOOD)], "polished:funny"),
        ("rate limited, then ok", [http_error(429, '{"retryDelay": "3s"}'), model_reply(GOOD)], "polished:funny"),
        ("service down 3 times", [http_error(503)] * 3, "unavailable"),
        ("daily quota used up", [http_error(429, "GenerateRequestsPerDay")], "quota"),
        ("billing problem", [http_error(402)], "billing"),
        ("bad key", [http_error(403)], "auth"),
        ("wrong model name", [http_error(404)], "config"),
        ("timeout, then ok", [TimeoutError(), model_reply(GOOD)], "polished:funny"),
        ("safety block", [{"promptFeedback": {"blockReason": "SAFETY"}}], "blocked"),
        ("cut-off output", [model_reply("{", "MAX_TOKENS")] * 2, "bad_output"),
        ("broken JSON, repaired", [model_reply("not json"), model_reply(GOOD)], "polished:funny"),
        ("story summarised away", [model_reply({**GOOD, "originalParagraphs": ["short"]})] * 2, "bad_output"),
        ("unknown mood", [model_reply({**GOOD, "mood": "weird"})], "polished:everyday"),
    ]
    for label, responses, expected in cases:
        got, _ = run_helper(responses)
        assert got == expected, f"{label}: expected {expected}, got {got}"
    # problems a person must fix pause the helper (and email the admin)
    for code, reason in [(402, "billing"), (403, "auth"), (404, "config")]:
        assert run_helper([http_error(code)])[1] == [reason]


# ---------- written stories ----------

def setup_worker_fakes():
    worker.privacy_calls = []
    sys.modules["privacy"].comprehend.detect_pii_entities.return_value = {"Entities": []}
    worker.comprehend.detect_sentiment.return_value = {"Sentiment": "POSITIVE"}
    worker.comprehend.detect_key_phrases.return_value = {"KeyPhrases": [{"Text": "the kitchen", "Score": .99, "BeginOffset": 0, "EndOffset": 11}]}
    worker.comprehend.detect_dominant_language.return_value = {"Languages": [{"LanguageCode": "de", "Score": .99}]}
    worker.translate.translate_text.side_effect = lambda **k: {"TranslatedText": "EN:" + k["Text"]}


WRITTEN = "Erster Absatz mit meinen eigenen Worten.\n\nZweiter Absatz, auch genau so."
AI_STORY = {"title": "T", "originalTitle": "OT", "paragraphs": ["one", "two"], "originalParagraphs": ["CHANGED"], "mood": "funny"}


def test_written_keep_my_words():
    setup_worker_fakes()
    worker.polish = lambda text, language, kind: dict(AI_STORY)
    draft = worker.build_written_draft({"transcript": WRITTEN, "writeMode": "keep"})
    assert draft["transcriptLanguage"] == "de-DE"  # detected
    assert draft["originalParagraphs"] == ["Erster Absatz mit meinen eigenen Worten.", "Zweiter Absatz, auch genau so."]
    assert draft["paragraphs"] == ["one", "two"]


def test_written_polish_uses_helper_text():
    setup_worker_fakes()
    worker.polish = lambda text, language, kind: dict(AI_STORY)
    draft = worker.build_written_draft({"transcript": WRITTEN, "writeMode": "polish", "spokenLanguage": "de-DE"})
    assert draft["originalParagraphs"] == ["CHANGED"]


def test_written_fallback_when_helper_unavailable():
    setup_worker_fakes()

    def down(text, language, kind):
        raise polish.PolishUnavailable("unavailable")
    worker.polish = down
    draft = worker.build_written_draft({"transcript": WRITTEN, "writeMode": "keep"})
    assert draft["polished"] is False and draft["notice"]
    assert draft["paragraphs"][0].startswith("EN:Erster")
    assert len(draft["originalParagraphs"]) == 2


# ---------- narration: the newest save wins ----------

def test_narration_versions():
    item = {"id": "s", "publishCount": 3, "voice": "female", "title": "T", "paragraphs": ["p"], "transcriptLanguage": "de-DE",
            "originalParagraphs": ["o"], "originalTitle": "OT", "narrationKey": "narration/s/en-2.mp3",
            "originalNarrationKey": "narration/s/orig-2.mp3"}
    worker.table.get_item.side_effect = None  # api and worker share one fake table; clear other tests' fakes
    worker.table.get_item.return_value = {"Item": dict(item)}
    worker.table.meta.client.exceptions.ConditionalCheckFailedException = ConditionFailed
    worker.narrate = lambda sid, jobs: {lang: f"narration/{sid}/{lang}.mp3" for lang, _, _ in jobs}
    deleted = []
    worker.s3.delete_object.side_effect = lambda Bucket, Key: deleted.append(Key)

    worker.table.update_item.side_effect = None
    worker.narrate_story("s", 3)  # current save: old audio is removed
    assert sorted(deleted) == ["narration/s/en-2.mp3", "narration/s/orig-2.mp3"]

    deleted.clear()
    worker.narrate_story("s", 2)  # an older save does nothing
    assert deleted == []

    worker.table.update_item.side_effect = ConditionFailed()
    worker.narrate_story("s", 3)  # a newer save won during narration: our files are discarded
    assert sorted(deleted) == ["narration/s/en-3.mp3", "narration/s/orig-3.mp3"]
    worker.table.update_item.side_effect = None


# ---------- account deletion ----------

def test_delete_account_needs_password_and_removes_everything():
    salt = secrets.token_bytes(16)
    db = {"user#ann": {"id": "user#ann", "username": "ann", "salt": salt.hex(), "passwordHash": api.hash_password("correct horse", salt)},
          "s1": {"id": "s1", "owner": "ann"}, "s3": {"id": "s3", "owner": "bob"},
          "session#a": {"id": "session#a", "username": "ann"}, "session#b": {"id": "session#b", "username": "bob"}}
    api.table.get_item.side_effect = lambda Key, **k: {"Item": db[Key["id"]]} if Key["id"] in db else {}
    api.table.delete_item.side_effect = lambda Key: db.pop(Key["id"], None)
    api.table.scan.side_effect = lambda **k: {"Items": [{"id": i} for i, v in db.items() if i.startswith("session#") and v.get("username") == "ann"]}
    api.query_index = lambda index, key, value: [v for v in list(db.values()) if v.get("owner") == value]
    api.delete_story_data = lambda item: db.pop(item["id"], None)
    try:
        api.delete_account("ann", {"password": "wrong"})
        raise AssertionError("wrong password must be refused")
    except api.HttpError as e:
        assert e.status == 403 and "user#ann" in db
    assert api.delete_account("ann", {"password": "correct horse"})["statusCode"] == 200
    assert sorted(db) == ["s3", "session#b"]  # only the other person's data is left


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as e:  # report every test, then exit non-zero
            failed += 1
            print(f"FAIL  {name}: {e!r}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
