"""Website + API on one Lambda Function URL (pages from ./site, API under /api/).

Accounts: username + password (no email, open sign-up with an hourly cap), bearer session tokens.
Narrators review and edit their draft, then publish; signed-in users can report; 3 reports from accounts
older than a day hide a story until the admin restores or removes it (and the admin gets an email).
Removal reasons are visible only to the story's author.

Stories are spoken (uploaded audio) or written (text). Publishing makes a story live at once; the worker adds the
narration a few seconds later. Narrators can post anonymously ("a stranger"). Readers can write back: private text
postcards only the narrator sees.

Guardrails: per-account and site-wide story limits, sign-up cap per hour, password lockout, limits on
re-translating and re-publishing a story, length caps on what gets narrated, limits on write-backs, and
personal-data removal on stories, edits and write-backs.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr, Key

from common import (
    DRAFT, HIDDEN, MEDIA_BUCKET, PROCESSING, PUBLISHED, REMOVED, UPLOADING,
    audio_key, now_ms, photo_key, response, s3, set_fields, signed_url, table,
)
from languages import LANGUAGES, translate_code
from privacy import scrub

ADMIN_PARAM = os.environ["ADMIN_PARAM"]
ALERT_TOPIC = os.environ["ALERT_TOPIC"]
WORKER_FUNCTION = os.environ["WORKER_FUNCTION"]
MAX_TEXT = 12000
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "30"))
TOTAL_LIMIT = int(os.environ.get("TOTAL_LIMIT", "200"))
HIDE_AFTER_REPORTS = 3
TRUSTED_ACCOUNT_AGE_MS = 24 * 3600 * 1000  # reports from newer accounts are kept for the admin but don't hide stories
USER_DAILY_LIMIT = 3  # stories per account per day
USER_TOTAL_LIMIT = 10  # stories per account
SIGNUPS_PER_HOUR = 30
LOGIN_MAX_FAILS = 10  # wrong passwords before a username is locked
LOCK_SECONDS = 15 * 60
MAX_TRANSLATIONS = 10  # "Translate my changes" per story
MAX_PUBLISHES = 6  # first publish plus 5 saved changes (each one records the narration again)
NARRATED_TEXT_LIMIT = 5000  # characters per language that can be narrated (caps narration cost per save)
WRITTEN_MIN, WRITTEN_MAX = 80, 3000  # characters in a written story
ANONYMOUS_NAME = "a stranger"
REPLY_MAX = 500
REPLIES_PER_STORY = 3  # per reader
REPLIES_PER_DAY = 10  # per reader
SESSION_DAYS = 30
PBKDF2_ROUNDS = 120_000

MAX_AUDIO_BYTES = 15 * 1024 * 1024
MAX_PHOTO_BYTES = 5 * 1024 * 1024
AUDIO_TYPES = {  # content type -> file extension Transcribe understands
    "audio/webm": "webm", "video/webm": "webm", "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp3": "mp3",
    "audio/mp4": "mp4", "video/mp4": "mp4", "audio/x-m4a": "m4a", "audio/m4a": "m4a", "audio/aac": "m4a",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav", "audio/flac": "flac", "audio/x-flac": "flac",
}
PHOTO_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MOODS = {"heartwarming", "funny", "scary", "bittersweet", "everyday", "inspiring", "surprising"}
USERNAME = re.compile(r"[A-Za-z0-9_.-]{3,24}")

ssm = boto3.client("ssm")
translate = boto3.client("translate")
lambda_client = boto3.client("lambda")
sns = boto3.client("sns")
comprehend = boto3.client("comprehend")
_params = {}


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def param(name, decrypt=False):
    if name not in _params:
        _params[name] = ssm.get_parameter(Name=name, WithDecryption=decrypt)["Parameter"]["Value"]
    return _params[name]


SITE_DIR = os.path.join(os.path.dirname(__file__), "site")
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".ico": "image/x-icon"}
SECURITY_HEADERS = {
    "content-security-policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                               "img-src 'self' data: blob: https://*.amazonaws.com; media-src 'self' blob: https://*.amazonaws.com; "
                               "connect-src 'self' https://*.amazonaws.com; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "strict-transport-security": "max-age=31536000; includeSubDomains",
}


def handler(event, _context):
    method = event["requestContext"]["http"]["method"]
    parts = [p for p in event.get("rawPath", "/").split("/") if p]
    if parts[:1] != ["api"]:
        return static(method, parts)
    try:
        result = route(method, parts[1:], event)
    except HttpError as e:
        result = response(e.status, {"error": str(e)})
    result["headers"].update(SECURITY_HEADERS)
    return result


def static(method, parts):
    if parts[:1] == ["fonts"] and len(parts) == 2 and re.fullmatch(r"[a-z0-9-]+\.woff2", parts[1]):
        return font(method, parts[1])
    name = parts[0] if len(parts) == 1 else "index.html" if not parts else ""
    path = os.path.join(SITE_DIR, name)
    if method != "GET" or not re.fullmatch(r"[a-z]+\.(html|css|js|svg|ico)", name or "") or not os.path.isfile(path):
        return not_found()
    with open(path, encoding="utf-8") as f:
        body = f.read()
    return {"statusCode": 200, "body": body,
            "headers": {"content-type": CONTENT_TYPES[os.path.splitext(name)[1]], "cache-control": "public, max-age=300", **SECURITY_HEADERS}}


def font(method, name):
    """Fonts are self-hosted (no requests to third-party font services)."""
    path = os.path.join(SITE_DIR, "fonts", name)
    if method != "GET" or not os.path.isfile(path):
        return not_found()
    with open(path, "rb") as f:
        body = base64.b64encode(f.read()).decode("ascii")
    return {"statusCode": 200, "body": body, "isBase64Encoded": True,
            "headers": {"content-type": "font/woff2", "cache-control": "public, max-age=31536000, immutable", **SECURITY_HEADERS}}


def not_found():
    return {"statusCode": 404, "headers": {"content-type": "text/plain", **SECURITY_HEADERS}, "body": "Not found"}


def route(method, parts, event):
    if method == "POST" and parts == ["signup"]:
        return signup(body_json(event))
    if method == "POST" and parts == ["login"]:
        return login(body_json(event))
    if method == "POST" and parts == ["logout"]:
        return logout(event)
    if method == "GET" and parts == ["languages"]:
        return response(200, {"languages": [{"code": c, "name": n} for c, n in sorted(LANGUAGES.items(), key=lambda x: x[1])]})
    if method == "GET" and parts == ["stories"]:
        return response(200, {"stories": [card(i) for i in by_status(PUBLISHED)]})
    if method == "GET" and len(parts) == 2 and parts[0] == "stories":
        return get_story(parts[1])
    if method == "POST" and len(parts) == 3 and parts[0] == "stories" and parts[2] == "report":
        return report(user_of(event), parts[1], body_json(event))
    if method == "POST" and len(parts) == 3 and parts[0] == "stories" and parts[2] == "reply":
        return write_back(user_of(event), parts[1], body_json(event))
    if method == "POST" and parts == ["uploads"]:
        return create_upload(user_of(event), body_json(event))
    if method == "POST" and parts == ["written"]:
        return create_written(user_of(event), body_json(event))
    if method == "GET" and parts == ["me", "replies"]:
        return my_replies(user_of(event))
    if method == "POST" and parts == ["me", "replies", "read"]:
        return mark_replies_read(user_of(event), body_json(event))
    if method == "POST" and len(parts) == 4 and parts[:2] == ["me", "replies"] and parts[3] == "translate":
        return translate_reply(user_of(event), parts[2])
    if method == "DELETE" and len(parts) == 3 and parts[:2] == ["me", "replies"]:
        return delete_reply(user_of(event), parts[2])
    if method == "GET" and parts == ["me", "stories"]:
        return my_stories(user_of(event))
    if method == "GET" and len(parts) == 3 and parts[:2] == ["me", "stories"]:
        return my_draft(user_of(event), parts[2])
    if method == "POST" and len(parts) == 4 and parts[:2] == ["me", "stories"] and parts[3] == "translate":
        return retranslate(user_of(event), parts[2], body_json(event))
    if method == "POST" and len(parts) == 4 and parts[:2] == ["me", "stories"] and parts[3] == "publish":
        return publish_own(user_of(event), parts[2], body_json(event))
    if method == "DELETE" and len(parts) == 3 and parts[:2] == ["me", "stories"]:
        return delete_own(user_of(event), parts[2])
    if method == "POST" and parts == ["me", "delete"]:
        return delete_account(user_of(event), body_json(event))
    if parts[:1] == ["admin"]:
        require_admin(event)
        return admin_route(method, parts[1:], event)
    raise HttpError(404, "Not found")


def body_json(event):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise HttpError(400, "Invalid JSON body")


def get_story_item(story_id):
    if not re.fullmatch(r"[0-9a-f]{32}", story_id):
        raise HttpError(404, "Story not found")
    item = table.get_item(Key={"id": story_id}).get("Item")
    if not item:
        raise HttpError(404, "Story not found")
    return item


# ---------- accounts ----------

def bump(counter_id, limit, expires_in=None):
    """Add one to a counter unless it already reached the limit. Returns False when the limit is reached."""
    expression, values = "ADD #n :one", {":one": 1, ":limit": limit}
    if expires_in:
        expression += " SET expiresAt = if_not_exists(expiresAt, :exp)"
        values[":exp"] = int(time.time()) + expires_in
    try:
        table.update_item(Key={"id": counter_id}, UpdateExpression=expression,
                          ConditionExpression="attribute_not_exists(#n) OR #n < :limit",
                          ExpressionAttributeNames={"#n": "count"}, ExpressionAttributeValues=values)
        return True
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False


def bump_story(story_id, field, limit):
    """Per-story action counter (translations, publishes). Returns the new count, or 0 when the limit is reached."""
    try:
        result = table.update_item(Key={"id": story_id}, UpdateExpression="SET #f = if_not_exists(#f, :zero) + :one",
                                   ConditionExpression="attribute_not_exists(#f) OR #f < :limit",
                                   ExpressionAttributeNames={"#f": field},
                                   ExpressionAttributeValues={":zero": 0, ":one": 1, ":limit": limit}, ReturnValues="UPDATED_NEW")
        return int(result["Attributes"][field])
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return 0


def user_key(username):
    return {"id": "user#" + username.lower()}


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS).hex()


def new_session(username):
    token = secrets.token_urlsafe(32)
    table.put_item(Item={"id": "session#" + hashlib.sha256(token.encode()).hexdigest(), "username": username,
                         "expiresAt": int(time.time()) + SESSION_DAYS * 86400})
    return response(200, {"token": token, "username": username})


def signup(body):
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    if not USERNAME.fullmatch(username):
        raise HttpError(400, "Username: 3-24 characters, letters, numbers, dot, dash or underscore")
    if not 8 <= len(password) <= 200:
        raise HttpError(400, "Password must be at least 8 characters")
    if table.get_item(Key=user_key(username)).get("Item"):
        raise HttpError(409, "That username is taken")
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    if not bump(f"signups#{hour}", SIGNUPS_PER_HOUR, expires_in=7200):
        raise HttpError(429, "Lots of people are joining right now. Please try again in an hour.")
    salt = secrets.token_bytes(16)
    try:
        table.put_item(
            Item={**user_key(username), "username": username, "salt": salt.hex(),
                  "passwordHash": hash_password(password, salt), "createdAt": now_ms()},
            ConditionExpression="attribute_not_exists(id)",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        raise HttpError(409, "That username is taken")
    return new_session(username)


def lock_key(username):
    return {"id": "lock#" + username.lower()}


def login(body):
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    if not USERNAME.fullmatch(username):
        raise HttpError(401, "Wrong username or password")
    lock = table.get_item(Key=lock_key(username)).get("Item")
    if lock and lock["expiresAt"] <= time.time():
        table.delete_item(Key=lock_key(username))  # window over; auto-expiry can lag
        lock = None
    if lock and lock.get("fails", 0) >= LOGIN_MAX_FAILS:
        raise HttpError(429, "Too many wrong passwords. Please wait 15 minutes and try again.")
    user = table.get_item(Key=user_key(username)).get("Item")
    if not user or not hmac.compare_digest(hash_password(password, bytes.fromhex(user["salt"])), user["passwordHash"]):
        table.update_item(Key=lock_key(username), UpdateExpression="ADD fails :one SET expiresAt = if_not_exists(expiresAt, :exp)",
                          ExpressionAttributeValues={":one": 1, ":exp": int(time.time()) + LOCK_SECONDS})
        raise HttpError(401, "Wrong username or password")
    if lock:
        table.delete_item(Key=lock_key(username))
    return new_session(user["username"])


def token_of(event):
    header = (event.get("headers") or {}).get("authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else ""


def user_of(event):
    token = token_of(event)
    if not token:
        raise HttpError(401, "Please sign in")
    session = table.get_item(Key={"id": "session#" + hashlib.sha256(token.encode()).hexdigest()}).get("Item")
    if not session or session["expiresAt"] < time.time():
        raise HttpError(401, "Please sign in again")
    return session["username"]


def logout(event):
    token = token_of(event)
    if token:
        table.delete_item(Key={"id": "session#" + hashlib.sha256(token.encode()).hexdigest()})
    return response(200, {"ok": True})


# ---------- uploads ----------

def base_type(content_type):
    return (content_type or "").split(";")[0].strip().lower()


def create_upload(username, body):
    if body.get("consent") is not True:
        raise HttpError(400, "Consent is required")
    title = str(body.get("title", "")).strip()[:90]
    audio_type = base_type(body.get("audioType"))
    if audio_type not in AUDIO_TYPES:
        raise HttpError(400, "Unsupported audio format")
    photo_type = base_type(body.get("photoType")) if body.get("photoType") else None
    if photo_type and photo_type not in PHOTO_TYPES:
        raise HttpError(400, "Photo must be JPEG, PNG or WebP")
    language = body.get("language") or "auto"
    if language != "auto" and language not in LANGUAGES:
        raise HttpError(400, "Unknown language")
    voice = "male" if body.get("voice") == "male" else "female"
    reserve_story_slot(username)

    story_id = uuid.uuid4().hex
    audio_ext = AUDIO_TYPES[audio_type]
    item = {"id": story_id, "status": UPLOADING, "owner": username, "narratorName": username,
            "audioExt": audio_ext, "createdAt": now_ms(), "updatedAt": now_ms(), "reports": 0, "voice": voice,
            "anonymous": body.get("anonymous") is True}
    if language != "auto":
        item["spokenLanguage"] = language
    if title:
        item["narratorTitle"] = title
    if photo_type:
        item["photoKey"] = photo_key(story_id, PHOTO_TYPES[photo_type])
    table.put_item(Item=item)

    result = {"id": story_id, "audio": presigned_post(audio_key(story_id, audio_ext), audio_type, MAX_AUDIO_BYTES)}
    if photo_type:
        result["photo"] = presigned_post(item["photoKey"], photo_type, MAX_PHOTO_BYTES)
    return response(200, result)


def reserve_story_slot(username):
    """Per-account limits first, then the site-wide ones. Spoken and written stories share them."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not bump(f"ucount#{username.lower()}#{day}", USER_DAILY_LIMIT, expires_in=2 * 86400):
        raise HttpError(429, f"You can share up to {USER_DAILY_LIMIT} stories a day. Please come back tomorrow.")
    if not bump(f"ucount#{username.lower()}#total", USER_TOTAL_LIMIT):
        raise HttpError(429, f"Your account has reached the limit of {USER_TOTAL_LIMIT} stories.")
    if not reserve_quota():
        raise HttpError(429, "Story limit reached for today. Please try again tomorrow.")


def create_written(username, body):
    """A written story: no recording, so the worker goes straight to the AI writing helper."""
    if body.get("consent") is not True:
        raise HttpError(400, "Consent is required")
    text = str(body.get("text", "")).strip()
    if len(text) < WRITTEN_MIN:
        raise HttpError(400, "Please write a little more: at least a few sentences.")
    if len(text) > WRITTEN_MAX:
        raise HttpError(400, f"Please keep your story under {WRITTEN_MAX:,} characters.")
    language = body.get("language") or "auto"
    if language != "auto" and language not in LANGUAGES:
        raise HttpError(400, "Unknown language")
    reserve_story_slot(username)
    story_id = uuid.uuid4().hex
    item = {"id": story_id, "status": PROCESSING, "owner": username, "narratorName": username, "source": "written",
            "writeMode": "polish" if body.get("mode") == "polish" else "keep", "transcript": text,
            "createdAt": now_ms(), "updatedAt": now_ms(), "reports": 0,
            "voice": "male" if body.get("voice") == "male" else "female", "anonymous": body.get("anonymous") is True}
    if language != "auto":
        item["spokenLanguage"] = language
    title = str(body.get("title", "")).strip()[:90]
    if title:
        item["narratorTitle"] = title
    table.put_item(Item=item)
    lambda_client.invoke(FunctionName=WORKER_FUNCTION, InvocationType="Event",
                         Payload=json.dumps({"action": "write", "id": story_id}).encode())
    return response(200, {"id": story_id})


def presigned_post(key, content_type, max_bytes):
    return s3.generate_presigned_post(
        Bucket=MEDIA_BUCKET,
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, max_bytes]],
        ExpiresIn=900,
    )


def reserve_quota():
    """Count uploads per day and overall so a leaked invite code can't run up costs."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for counter_id, limit in ((f"counter#{day}", DAILY_LIMIT), ("counter#total", TOTAL_LIMIT)):
        try:
            table.update_item(
                Key={"id": counter_id},
                UpdateExpression="ADD #n :one",
                ConditionExpression="attribute_not_exists(#n) OR #n < :limit",
                ExpressionAttributeNames={"#n": "count"},
                ExpressionAttributeValues={":one": 1, ":limit": limit},
            )
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            return False
    return True


# ---------- reading ----------

def query_index(index, key_name, value):
    items, kwargs = [], {}
    while True:
        page = table.query(IndexName=index, KeyConditionExpression=Key(key_name).eq(value), ScanIndexForward=False, **kwargs)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def by_status(status):
    return query_index("byStatus", "status", status)


def card(item):
    return {
        "id": item["id"], "title": item.get("title"), "teaser": item.get("teaser"), "mood": item.get("mood"),
        "narratorName": ANONYMOUS_NAME if item.get("anonymous") else item.get("narratorName"),
        "publishedAt": item.get("publishedAt"),
        "originalLanguage": item.get("originalLanguage"),
        "photoUrl": signed_url(item["photoKey"]) if item.get("photoKey") else None,
    }


def full_story(item):
    return {
        **card(item),
        "paragraphs": item.get("paragraphs", []),
        "originalLanguage": item.get("originalLanguage"),
        "originalTitle": item.get("originalTitle"),
        "originalParagraphs": item.get("originalParagraphs", []),
        "narrationUrl": signed_url(item["narrationKey"]) if item.get("narrationKey") and narration_ready(item) else None,
        "originalNarrationUrl": signed_url(item["originalNarrationKey"]) if item.get("originalNarrationKey") and narration_ready(item) else None,
        "narrationPending": bool(item.get("narrationPending")),
    }


def narration_ready(item):
    """Old audio no longer matches the text while a new narration is recording (or after it failed)."""
    return not item.get("narrationPending") and not item.get("narrationError")


def get_story(story_id):
    item = get_story_item(story_id)
    if item.get("status") != PUBLISHED:
        raise HttpError(404, "Story not found")
    return response(200, full_story(item))


def my_stories(username):
    items = [i for i in query_index("byOwner", "owner", username) if not i["id"].startswith("reply#")]
    return response(200, {"stories": [
        {**card(i), "status": i["status"], "removalReason": i.get("removalReason"), "error": i.get("error"),
         "createdAt": i.get("createdAt"), "anonymous": bool(i.get("anonymous")),
         "narrationPending": bool(i.get("narrationPending")), "narrationError": i.get("narrationError") or ""} for i in items]})


def own_item(username, story_id):
    item = get_story_item(story_id)
    if item.get("owner") != username:
        raise HttpError(404, "Story not found")
    return item


def my_draft(username, story_id):
    item = own_item(username, story_id)
    return response(200, {**full_story(item), "status": item["status"], "error": item.get("error"),
                          "flags": item.get("flags", []), "transcriptLanguage": item.get("transcriptLanguage"),
                          "notice": item.get("notice") or "", "anonymous": bool(item.get("anonymous")),
                          "source": item.get("source", "spoken")})


def text_param(body, key, required=True):
    value = body.get(key)
    if not isinstance(value, str) or (required and not value.strip()):
        raise HttpError(400, f"Missing {key}")
    if len(value) > MAX_TEXT:
        raise HttpError(400, "That text is too long")
    return value.strip()


def split_paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def translate_to_english(text, lang):
    try:
        return translate.translate_text(Text=text, SourceLanguageCode=translate_code(lang), TargetLanguageCode="en")["TranslatedText"]
    except translate.exceptions.UnsupportedLanguagePairException:
        raise HttpError(400, "Automatic translation isn't available for this language. Please edit the English text yourself.")


def retranslate(username, story_id, body):
    """Narrator fixed their original text; translate it again, paragraph by paragraph."""
    item = own_item(username, story_id)
    lang = item.get("transcriptLanguage", "en-US")
    if lang.startswith("en"):
        raise HttpError(400, "This story is already in English")
    paras = split_paragraphs(text_param(body, "originalText"))
    if sum(len(p) for p in paras) > NARRATED_TEXT_LIMIT:
        raise HttpError(400, f"Please keep your story under {NARRATED_TEXT_LIMIT:,} characters.")
    if not bump_story(story_id, "translateCount", MAX_TRANSLATIONS):
        raise HttpError(429, "You've used all translations for this story. You can still edit the English text yourself.")
    english = [translate_to_english(p, lang) for p in paras]
    title = body.get("originalTitle")
    result = {"englishText": "\n\n".join(english)}
    if isinstance(title, str) and title.strip():
        result["title"] = translate_to_english(title.strip()[:200], lang)
    return response(200, result)


def publish_own(username, story_id, body):
    item = own_item(username, story_id)
    if item["status"] not in (DRAFT, PUBLISHED):
        raise HttpError(409, "This story can't be published right now")
    paragraphs_en = split_paragraphs(text_param(body, "englishText"))
    original_text = text_param(body, "originalText") if item.get("originalParagraphs") else ""
    if max(len(text_param(body, "englishText")), len(original_text)) > NARRATED_TEXT_LIMIT:
        raise HttpError(400, f"Please keep your story under {NARRATED_TEXT_LIMIT:,} characters in each language.")
    fields = {"title": text_param(body, "title")[:120], "paragraphs": paragraphs_en,
              "teaser": paragraphs_en[0][:157] + ("..." if len(paragraphs_en[0]) > 157 else "")}
    if item.get("originalParagraphs"):
        fields["originalParagraphs"] = split_paragraphs(text_param(body, "originalText"))
        fields["originalTitle"] = text_param(body, "originalTitle", required=False)[:120] or fields["title"]
    if body.get("mood") in MOODS:
        fields["mood"] = body["mood"]
    if body.get("voice") in ("female", "male"):
        fields["voice"] = body["voice"]
    if isinstance(body.get("anonymous"), bool):
        fields["anonymous"] = body["anonymous"]
    # The narrator may have typed new text: remove personal details again before anything is narrated or shown.
    original = [fields.get("originalTitle", "")] + fields.get("originalParagraphs", [])
    english, original, removed = scrub([fields["title"]] + paragraphs_en, original)
    fields["title"], fields["paragraphs"] = english[0], english[1:]
    fields["teaser"] = fields["paragraphs"][0][:157] + ("..." if len(fields["paragraphs"][0]) > 157 else "")
    if "originalParagraphs" in fields:
        fields["originalTitle"], fields["originalParagraphs"] = original[0], original[1:]
    version = bump_story(story_id, "publishCount", MAX_PUBLISHES)
    if not version:
        raise HttpError(429, "You've reached the limit of changes for this story.")
    if not item.get("publishedAt"):
        fields["publishedAt"] = now_ms()
    # Live right away; the worker adds the narration a few seconds later (the newest save wins).
    set_fields(story_id, status=PUBLISHED, error="", notice="", narrationPending=True, narrationError="", **fields)
    lambda_client.invoke(FunctionName=WORKER_FUNCTION, InvocationType="Event",
                         Payload=json.dumps({"action": "narrate", "id": story_id, "version": version}).encode())
    return response(200, {"ok": True, "removed": removed})


def delete_own(username, story_id):
    delete_story(own_item(username, story_id))
    return response(200, {"ok": True})


def delete_story(item):
    delete_story_data(item)
    for reply in query_index("byOwner", "owner", item["owner"]):
        if reply["id"].startswith("reply#") and reply.get("storyId") == item["id"]:
            table.delete_item(Key={"id": reply["id"]})


# ---------- write back: private postcards to the narrator ----------

def reply_id(rid):
    if not re.fullmatch(r"[0-9a-f]{32}", rid or ""):
        raise HttpError(404, "Postcard not found")
    return "reply#" + rid


def write_back(username, story_id, body):
    text = str(body.get("text", "")).strip()
    if len(text) < 3:
        raise HttpError(400, "Please write a few words first.")
    if len(text) > REPLY_MAX:
        raise HttpError(400, f"Please keep it under {REPLY_MAX} characters.")
    story = get_story_item(story_id)
    if story.get("status") != PUBLISHED or story["id"].startswith("reply#"):
        raise HttpError(404, "Story not found")
    if story.get("owner") == username:
        raise HttpError(409, "You can't write back to your own story.")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not bump(f"rcount#{username.lower()}#{story_id}", REPLIES_PER_STORY, expires_in=30 * 86400):
        raise HttpError(429, "You've already written back to this story a few times.")
    if not bump(f"rcount#{username.lower()}#{day}", REPLIES_PER_DAY, expires_in=2 * 86400):
        raise HttpError(429, "You've sent a lot of postcards today. Please try again tomorrow.")
    clean, _, removed = scrub([text], [])
    found = comprehend.detect_dominant_language(Text=text)["Languages"]
    table.put_item(Item={"id": "reply#" + uuid.uuid4().hex, "owner": story["owner"], "createdAt": now_ms(),
                         "storyId": story_id, "storyTitle": story.get("title", ""), "sender": username,
                         "anonymous": body.get("anonymous") is True, "text": clean[0], "read": False,
                         "lang": found[0]["LanguageCode"] if found else "und"})
    return response(200, {"ok": True, "removed": removed})


def my_replies(username):
    replies = [i for i in query_index("byOwner", "owner", username) if i["id"].startswith("reply#")]
    return response(200, {"unread": sum(1 for r in replies if not r.get("read")), "replies": [
        {"id": r["id"][len("reply#"):], "storyId": r.get("storyId"), "storyTitle": r.get("storyTitle"),
         "from": ANONYMOUS_NAME if r.get("anonymous") else r.get("sender"), "text": r.get("text"),
         "textEn": r.get("textEn"), "english": str(r.get("lang", "")).startswith("en"),
         "read": bool(r.get("read")), "createdAt": r.get("createdAt")} for r in replies]})


def own_reply(username, rid):
    item = table.get_item(Key={"id": reply_id(rid)}).get("Item")
    if not item or item.get("owner") != username:
        raise HttpError(404, "Postcard not found")
    return item


def mark_replies_read(username, body):
    for r in query_index("byOwner", "owner", username):
        if r["id"].startswith("reply#") and not r.get("read") and (not body.get("storyId") or r.get("storyId") == body["storyId"]):
            table.update_item(Key={"id": r["id"]}, UpdateExpression="SET #r = :t",
                              ExpressionAttributeNames={"#r": "read"}, ExpressionAttributeValues={":t": True})
    return response(200, {"ok": True})


def translate_reply(username, rid):
    item = own_reply(username, rid)
    if not item.get("textEn"):
        english = translate.translate_text(Text=item["text"], SourceLanguageCode="auto", TargetLanguageCode="en")["TranslatedText"]
        table.update_item(Key={"id": item["id"]}, UpdateExpression="SET textEn = :e", ExpressionAttributeValues={":e": english})
        item["textEn"] = english
    return response(200, {"textEn": item["textEn"]})


def delete_reply(username, rid):
    table.delete_item(Key={"id": own_reply(username, rid)["id"]})
    return response(200, {"ok": True})


def delete_account(username, body):
    """Right to erasure: the account, every story with its narration and photo, and every session."""
    user = table.get_item(Key=user_key(username)).get("Item")
    password = str(body.get("password", ""))
    if not user or not hmac.compare_digest(hash_password(password, bytes.fromhex(user["salt"])), user["passwordHash"]):
        raise HttpError(403, "Wrong password")  # not 401: a typo must not sign the person out
    for item in query_index("byOwner", "owner", username):
        delete_story_data(item)
    # Sessions, postcards they sent and their write-back counters aren't keyed by owner: scan (rare action, small table).
    kwargs = {"FilterExpression": (Attr("id").begins_with("session#") & Attr("username").eq(username))
                                  | (Attr("id").begins_with("reply#") & Attr("sender").eq(username))
                                  | Attr("id").begins_with(f"rcount#{username.lower()}#"),
              "ProjectionExpression": "id"}
    while True:
        page = table.scan(**kwargs)
        for s in page["Items"]:
            table.delete_item(Key={"id": s["id"]})
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for key in (f"ucount#{username.lower()}#total", f"ucount#{username.lower()}#{day}", lock_key(username)["id"]):
        table.delete_item(Key={"id": key})
    table.delete_item(Key=user_key(username))
    return response(200, {"ok": True})


def report(username, story_id, body):
    reason = str(body.get("reason", "")).strip()[:300]
    if len(reason) < 3:
        raise HttpError(400, "Please say briefly what's wrong")
    item = get_story_item(story_id)
    if item.get("status") != PUBLISHED:
        raise HttpError(404, "Story not found")
    reporter = table.get_item(Key=user_key(username)).get("Item") or {}
    trusted = now_ms() - int(reporter.get("createdAt", now_ms())) >= TRUSTED_ACCOUNT_AGE_MS
    try:
        result = table.update_item(
            Key={"id": story_id},
            UpdateExpression="ADD reports :one, reporters :me" + (", trustedReports :one" if trusted else "") +
                             " SET reportLog = list_append(if_not_exists(reportLog, :empty), :entry)",
            ConditionExpression="#s = :pub AND NOT contains(reporters, :name)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":one": 1, ":me": {username}, ":name": username, ":pub": PUBLISHED, ":empty": [],
                                       ":entry": [{"by": username, "reason": reason, "at": now_ms(), "newAccount": not trusted}]},
            ReturnValues="ALL_NEW",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return response(200, {"ok": True, "message": "Thanks, we already have your report."})
    if result["Attributes"].get("trustedReports", 0) >= HIDE_AFTER_REPORTS:
        try:
            table.update_item(Key={"id": story_id}, UpdateExpression="SET #s = :hidden",
                              ConditionExpression="#s = :pub", ExpressionAttributeNames={"#s": "status"},
                              ExpressionAttributeValues={":hidden": HIDDEN, ":pub": PUBLISHED})
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            pass  # someone else's report hid it a moment ago
        else:
            notify_hidden(result["Attributes"])
    return response(200, {"ok": True, "message": "Thanks for reporting. We'll take a look."})


def notify_hidden(item):
    reasons = "\n".join(f"- {r['reason']}" for r in item.get("reportLog", [])[-5:])
    try:
        sns.publish(TopicArn=ALERT_TOPIC, Subject="Before I Leave: a story was hidden after reports",
                    Message=f"\"{item.get('title', '')}\" (id {item['id']}) was hidden after {item.get('trustedReports', 0)} reports.\n\n"
                            f"Latest reasons:\n{reasons}\n\nReview it on the admin page: restore it or remove it with a reason.")
    except Exception as e:  # the story is hidden either way; the email is a convenience
        print(f"Hidden-story email failed: {e!r}")


# ---------- admin ----------

def require_admin(event):
    supplied = (event.get("headers") or {}).get("x-admin-key", "")
    if not supplied or not hmac.compare_digest(supplied, param(ADMIN_PARAM, decrypt=True)):
        raise HttpError(401, "Not authorized")


def admin_route(method, rest, event):
    if method == "GET" and rest == ["stories"]:
        view = (event.get("queryStringParameters") or {}).get("view", "REPORTED")
        if view == "REPORTED":
            items = by_status(HIDDEN) + [i for i in by_status(PUBLISHED) if i.get("reports", 0) > 0]
        else:
            items = by_status(view)
        return response(200, {"stories": [admin_view(i) for i in items]})
    if method == "POST" and len(rest) == 3 and rest[0] == "stories":
        return moderate(rest[1], rest[2], body_json(event))
    if method == "DELETE" and len(rest) == 2 and rest[0] == "stories":
        delete_story(get_story_item(rest[1]))
        return response(200, {"ok": True})
    raise HttpError(404, "Not found")


def admin_view(item):
    return {**full_story(item), "status": item["status"], "owner": item.get("owner"), "createdAt": item.get("createdAt"),
            "anonymous": bool(item.get("anonymous")), "source": item.get("source", "spoken"),
            "transcript": item.get("transcript"), "sentiment": item.get("sentiment"),
            "flags": item.get("flags", []) + ([item["helperNote"]] if item.get("helperNote") else []),
            "reports": item.get("reports", 0), "trustedReports": item.get("trustedReports", 0), "reportLog": item.get("reportLog", []),
            "removalReason": item.get("removalReason"), "error": item.get("error")}


def moderate(story_id, action, body):
    item = get_story_item(story_id)
    if action == "remove":
        reason = str(body.get("reason", "")).strip()[:500]
        if len(reason) < 3:
            raise HttpError(400, "Please give the author a reason")
        table.update_item(Key={"id": story_id}, UpdateExpression="SET #s = :s, removalReason = :r",
                          ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":s": REMOVED, ":r": reason})
    elif action == "restore":
        if item["status"] not in (HIDDEN, REMOVED):
            raise HttpError(400, "Only hidden or removed stories can be restored")
        table.update_item(Key={"id": story_id}, UpdateExpression="SET #s = :s, reports = :zero, trustedReports = :zero REMOVE removalReason, reporters, reportLog",
                          ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":s": PUBLISHED, ":zero": 0})
    elif action == "edit":
        edits = {k: body[k].strip()[:200] for k in ("title",) if isinstance(body.get(k), str) and body[k].strip()}
        if body.get("mood") in MOODS:
            edits["mood"] = body["mood"]
        if edits:
            names = {f"#e{i}": k for i, k in enumerate(edits)}
            values = {f":e{i}": v for i, v in enumerate(edits.values())}
            table.update_item(Key={"id": story_id}, UpdateExpression="SET " + ", ".join(f"#e{i} = :e{i}" for i in range(len(edits))),
                              ExpressionAttributeNames=names, ExpressionAttributeValues=values)
    else:
        raise HttpError(400, "Unknown action")
    return response(200, {"ok": True})


def delete_story_data(item):
    keys = [item.get(k) for k in ("photoKey", "narrationKey", "originalNarrationKey")]
    if item.get("audioExt"):
        keys.append(audio_key(item["id"], item["audioExt"]))
    for key in filter(None, keys):
        s3.delete_object(Bucket=MEDIA_BUCKET, Key=key)
    table.delete_item(Key={"id": item["id"]})
