"""Story pipeline.

1. S3 "Object Created" under raw/ (EventBridge) -> start an Amazon Transcribe job (chosen or detected language).
2. Transcribe "Job State Change" (EventBridge) -> AI writing helper (faithful story in both languages);
   if the helper is unavailable, Amazon Translate + Comprehend build a plain draft instead. Comprehend removes
   personal data on both paths -> DRAFT, which the narrator reviews and edits.
{"action": "write"} (a written story) -> the same draft step without transcription.
3. {"action": "narrate"} (invoked by the API right after the narrator publishes; the story is already live)
   -> Polly narration in both languages (generative voices where available). A newer save wins.
The original recording and transcript file are deleted as soon as the draft exists, or the story fails.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from xml.sax.saxutils import escape

import boto3

from common import (
    DRAFT, FAILED, JOB_PREFIX, MEDIA_BUCKET, PROCESSING, TRANSCRIBING, UPLOADING,
    audio_key, job_name, narration_key, s3, set_fields, table, transcript_key,
)
from languages import LANGUAGES, language_name, translate_code, voice_for
from polish import PolishUnavailable, polish
from privacy import clip_bytes, scrub

transcribe = boto3.client("transcribe")
translate = boto3.client("translate")
comprehend = boto3.client("comprehend")
polly = boto3.client("polly")

MIN_WORDS = 25
MAX_WORDS = 1500  # about 8 minutes of speech; the app allows 5-minute recordings
NARRATION_PIECE = 400  # characters per Polly request: about one paragraph, so pieces run in parallel
NARRATION_WORKERS = 8  # Polly allows 8 generative requests per second (botocore retries if throttled)
HELPER_NOTICE = "Our AI writing helper was busy, so this is a direct translation. Please check the words."
WRITTEN_NOTICE = "Our AI writing helper was busy, so your words were kept as you wrote them and translated directly. Please check them."
MOOD_BY_SENTIMENT = {"POSITIVE": "heartwarming", "NEGATIVE": "bittersweet", "MIXED": "bittersweet", "NEUTRAL": "everyday"}
FILLERS = re.compile(r"\b(u+m+|u+h+|e+rm+|hm+|ä+h+m*|ö+h+m*)\b[,.]?\s*", re.I)
SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")
DETERMINERS = re.compile(r"^(a|an|the|my|our|his|her|their|this|that)\s+", re.I)
CONSENT = re.compile(r"\b(agree|consent|permission|allowed?|okay|ok)\b.*\b(share|shared|publish|post|use)", re.I)
GENERIC_PHRASES = {"story", "stories", "all day", "front", "time", "day", "way", "end", "thing", "things", "people", "lot", "today"}


def handler(event, _context):
    if event.get("action") == "narrate":
        narrate_story(event["id"], int(event["version"]))
    elif event.get("action") == "write":
        write_draft(event["id"])
    elif event.get("source") == "aws.s3":
        start_transcription(event["detail"]["object"]["key"])
    elif event.get("source") == "aws.transcribe":
        name = event["detail"]["TranscriptionJobName"]
        if name.startswith(JOB_PREFIX):
            finish_story(name[len(JOB_PREFIX):], event["detail"]["TranscriptionJobStatus"])


def claim(story_id, from_status, to_status):
    """Move a story to the next status exactly once; duplicate events lose the race and stop."""
    try:
        table.update_item(
            Key={"id": story_id},
            UpdateExpression="SET #s = :to",
            ConditionExpression="#s = :from",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":to": to_status, ":from": from_status},
        )
        return True
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False


# ---------- step 1: transcription ----------

def start_transcription(key):
    match = re.fullmatch(r"raw/([0-9a-f]{32})/audio\.(\w+)", key)
    if not match or not claim(match.group(1), UPLOADING, TRANSCRIBING):
        return
    story_id, ext = match.groups()
    item = table.get_item(Key={"id": story_id})["Item"]
    language = item.get("spokenLanguage")
    transcribe.start_transcription_job(
        TranscriptionJobName=job_name(story_id),
        Media={"MediaFileUri": f"s3://{MEDIA_BUCKET}/{key}"},
        MediaFormat=ext,
        OutputBucketName=MEDIA_BUCKET,
        OutputKey=transcript_key(story_id),
        # A narrator-chosen language is more accurate than detection; detection covers "not sure".
        **({"LanguageCode": language} if language else {"IdentifyLanguage": True}),
    )


# ---------- step 2: draft ----------

class StoryError(Exception):
    """A problem the narrator should see (bad audio, too short)."""


def finish_story(story_id, job_status):
    if not claim(story_id, TRANSCRIBING, PROCESSING):
        return
    item = table.get_item(Key={"id": story_id})["Item"]
    try:
        if job_status != "COMPLETED":
            raise StoryError("We couldn't understand this recording. Is it silent, very noisy or in an unusual format?")
        draft = build_draft(story_id, item)
    except StoryError as e:
        set_fields(story_id, status=FAILED, error=str(e))
        discard_recording(story_id, item)
        return
    except Exception as e:  # keep the story visible as FAILED; no automatic retries (cost guard)
        print(f"Processing {story_id} failed: {e!r}")
        set_fields(story_id, status=FAILED, error=f"Something went wrong while preparing your story ({type(e).__name__}).")
        discard_recording(story_id, item)
        return
    set_fields(story_id, status=DRAFT, **draft)
    discard_recording(story_id, item)


def discard_recording(story_id, item):
    """Privacy: the voice recording, transcript file and Transcribe job go as soon as the story is written or fails."""
    for delete in (lambda: s3.delete_object(Bucket=MEDIA_BUCKET, Key=audio_key(story_id, item["audioExt"])),
                   lambda: s3.delete_object(Bucket=MEDIA_BUCKET, Key=transcript_key(story_id)),
                   lambda: transcribe.delete_transcription_job(TranscriptionJobName=job_name(story_id))):
        try:
            delete()
        except Exception as e:  # e.g. the job never started; the 2-day cleanup rule is the backstop
            print(f"Cleanup for {story_id}: {e!r}")


def build_draft(story_id, item):
    transcript, lang = read_transcript(story_id)
    if len(clean(transcript).split()) < MIN_WORDS:
        raise StoryError("The recording was too short to make a story. Please speak for at least 30 seconds.")
    if len(transcript.split()) > MAX_WORDS:
        raise StoryError("This recording is too long. Please keep your story under 5 minutes.")
    is_english = lang.startswith("en")
    draft = {"transcript": transcript, "transcriptLanguage": lang,
             "originalLanguage": "English" if is_english else language_name(lang)}
    draft.update(polished_story(transcript, lang, item) or plain_story(transcript, lang, item))
    return finish_draft(draft, is_english)


def polished_story(transcript, lang, item, kind="spoken"):
    """The AI writing helper's version, or None (and a logged reason) when it is unavailable."""
    try:
        story = polish(transcript, language_name(lang), kind)
    except PolishUnavailable as e:
        print(f"HELPER_RESULT fallback:{e.reason} {e}")
        return None
    except Exception as e:  # the helper must never break the pipeline
        print(f"HELPER_RESULT fallback:error {e!r}")
        return None
    print("HELPER_RESULT polished")
    if item.get("narratorTitle"):
        story["title"] = item["narratorTitle"]
    return {**story, "polished": True, "notice": "", "helperNote": ""}


def plain_story(transcript, lang, item):
    """Fallback draft from AWS services only: Amazon Translate for English, Comprehend for title and mood."""
    original = clean(transcript)
    is_english = lang.startswith("en")
    english, translated = (original, True) if is_english else translate_text(original, lang)
    english, original = drop_consent_sentence(english, original)
    sentiment = comprehend.detect_sentiment(Text=clip_bytes(english, 4500), LanguageCode="en")
    title = item.get("narratorTitle") or title_from(key_phrases(english)) or "A story worth keeping"
    story = {"title": title, "paragraphs": paragraphs(english), "originalParagraphs": paragraphs(original),
             "mood": MOOD_BY_SENTIMENT.get(sentiment["Sentiment"], "everyday"), "sentiment": sentiment["Sentiment"],
             "polished": False, "notice": HELPER_NOTICE, "helperNote": "AI writing helper unavailable: plain translation"}
    if not translated:
        story["helperNote"] += "; no automatic English translation for this language"
    if not is_english:
        story["originalTitle"] = translate_from_english(title, lang) if translated else title
    return story


def write_draft(story_id):
    """A written story: no recording, so straight to the AI writing helper (or Amazon Translate)."""
    item = table.get_item(Key={"id": story_id}).get("Item")
    if not item or item["status"] != PROCESSING:
        return
    try:
        draft = build_written_draft(item)
    except StoryError as e:
        set_fields(story_id, status=FAILED, error=str(e))
        return
    except Exception as e:
        print(f"Writing {story_id} failed: {e!r}")
        set_fields(story_id, status=FAILED, error=f"Something went wrong while preparing your story ({type(e).__name__}).")
        return
    set_fields(story_id, status=DRAFT, **draft)


def build_written_draft(item):
    text = item["transcript"]
    lang = item.get("spokenLanguage") or detect_language(text)
    is_english = lang.startswith("en")
    mode = item.get("writeMode", "keep")
    own = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    draft = {"transcriptLanguage": lang, "originalLanguage": "English" if is_english else language_name(lang)}
    story = polished_story(text, lang, item, kind=mode)
    if story and mode == "keep":
        story["originalParagraphs"] = own  # "Keep my words": exactly what they wrote
        if is_english:
            story["paragraphs"] = own
    draft.update(story or plain_written(own, lang, item))
    return finish_draft(draft, is_english)


def detect_language(text):
    found = comprehend.detect_dominant_language(Text=clip_bytes(text, 4500))["Languages"]
    if found:
        code = found[0]["LanguageCode"].lower()
        for candidate in LANGUAGES:  # main variant of each language comes first
            if candidate.lower() == code or candidate.split("-")[0].lower() == code:
                return candidate
    raise StoryError("We couldn't tell which language this is. Please choose your language and try again.")


def plain_written(own, lang, item):
    """Fallback for written stories: their paragraphs as written, Amazon Translate for English."""
    is_english = lang.startswith("en")
    pairs = [(p, True) for p in own] if is_english else [translate_text(p, lang) for p in own]
    english = [t for t, _ in pairs]
    translated = all(ok for _, ok in pairs)
    joined = " ".join(english)
    sentiment = comprehend.detect_sentiment(Text=clip_bytes(joined, 4500), LanguageCode="en")
    title = item.get("narratorTitle") or title_from(key_phrases(joined)) or "A story worth keeping"
    story = {"title": title, "paragraphs": english, "originalParagraphs": own,
             "mood": MOOD_BY_SENTIMENT.get(sentiment["Sentiment"], "everyday"), "sentiment": sentiment["Sentiment"],
             "polished": False, "notice": WRITTEN_NOTICE, "helperNote": "AI writing helper unavailable: plain translation"}
    if not translated:
        story["helperNote"] += "; no automatic English translation for this language"
    if not is_english:
        story["originalTitle"] = translate_from_english(title, lang) if translated else title
    return story


def finish_draft(draft, is_english):
    """Personal data is removed on every path, so privacy never depends on the AI behaving."""
    draft["paragraphs"], draft["originalParagraphs"], removed = scrub(draft["paragraphs"], draft["originalParagraphs"])
    if is_english:
        draft["originalParagraphs"] = []
        draft.pop("originalTitle", None)
    draft["teaser"] = teaser_from(" ".join(draft["paragraphs"]))
    draft["flags"] = ["Personal details were removed automatically"] if removed else []
    return draft


def read_transcript(story_id):
    with s3.get_object(Bucket=MEDIA_BUCKET, Key=transcript_key(story_id))["Body"] as body:
        data = json.loads(body.read())
    text = " ".join(t["transcript"] for t in data["results"]["transcripts"]).strip()
    job = transcribe.get_transcription_job(TranscriptionJobName=job_name(story_id))["TranscriptionJob"]
    return text, job.get("LanguageCode", "en-US")


def clean(text):
    text = FILLERS.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def sentences(text):
    return [s.strip() for s in SENTENCE_END.split(text) if s.strip()]


def paragraphs(text, per=3):
    parts = sentences(text)
    return [" ".join(parts[i:i + per]) for i in range(0, len(parts), per)]


def chunks_by_bytes(text, limit):
    current = ""
    for s in sentences(text):
        candidate = f"{current} {s}".strip()
        if current and len(candidate.encode("utf-8")) > limit:
            yield current
            current = s
        else:
            current = candidate
    if current:
        yield current


def translate_text(text, lang):
    """Returns (english, translated?). Languages Translate doesn't support keep the original text."""
    try:
        parts = [translate.translate_text(Text=c, SourceLanguageCode=translate_code(lang), TargetLanguageCode="en")["TranslatedText"]
                 for c in chunks_by_bytes(text, 4500)]
        return " ".join(parts), True
    except translate.exceptions.UnsupportedLanguagePairException:
        return text, False


def translate_from_english(text, lang):
    return translate.translate_text(Text=text, SourceLanguageCode="en", TargetLanguageCode=translate_code(lang))["TranslatedText"]


def drop_consent_sentence(english, original):
    """Narrators often start with "I agree to share my story" - keep that out of the story itself."""
    en, orig = sentences(english), sentences(original)
    if not en or not CONSENT.search(en[0]):
        return english, original
    return " ".join(en[1:]), " ".join(orig[1:])


def key_phrases(english):
    found = comprehend.detect_key_phrases(Text=clip_bytes(english, 90000), LanguageCode="en")["KeyPhrases"]
    seen, out = set(), []
    for p in sorted(found, key=lambda p: p["BeginOffset"]):
        text = DETERMINERS.sub("", p["Text"]).strip(" ,.")
        if (p["Score"] >= 0.9 and len(text) > 3 and "[removed]" not in text and text.lower() not in seen
                and text.lower() not in GENERIC_PHRASES):
            seen.add(text.lower())
            out.append(text)
    return out


def title_from(phrases):
    picks = phrases[:3]
    if not picks:
        return None
    title = picks[0] if len(picks) == 1 else ", ".join(picks[:-1]) + " and " + picks[-1]
    title = title[:1].upper() + title[1:]
    return title if len(title) <= 70 else picks[0][:1].upper() + picks[0][1:]


def teaser_from(english):
    first = (sentences(english) or [english])[0]
    return first if len(first) <= 160 else first[:157].rsplit(" ", 1)[0] + "..."


# ---------- step 3: publish ----------

def narrate_story(story_id, version):
    """The story is already live; add (or replace) its narration. Only the newest save may set it."""
    item = table.get_item(Key={"id": story_id}).get("Item")
    if not item or int(item.get("publishCount", 0)) != version:
        return  # deleted, or a newer save is narrating
    preference = item.get("voice", "female")
    jobs = [(f"en-{version}", voice_for("en-US", preference), [item["title"]] + item["paragraphs"])]
    voice = voice_for(item.get("transcriptLanguage", "en-US"), preference)
    if item.get("originalParagraphs") and voice:
        jobs.append((f"orig-{version}", voice, [item.get("originalTitle") or item["title"]] + item["originalParagraphs"]))
    try:
        keys = narrate(story_id, jobs)
    except Exception as e:  # the story stays live without audio; the narrator can save again to retry
        print(f"NARRATION_FAILED {story_id}: {e!r}")
        update_if_current(story_id, version, "SET narrationPending = :f, narrationError = :e",
                          {":f": False, ":e": "Narration couldn't be recorded. Save the story again to try again."})
        return
    new = {"narrationKey": keys[f"en-{version}"]}
    if f"orig-{version}" in keys:
        new["originalNarrationKey"] = keys[f"orig-{version}"]
    expression = "SET narrationPending = :f, narrationError = :e, narrationKey = :n" + \
                 (", originalNarrationKey = :o" if "originalNarrationKey" in new else " REMOVE originalNarrationKey")
    values = {":f": False, ":e": "", ":n": new["narrationKey"]}
    if "originalNarrationKey" in new:
        values[":o"] = new["originalNarrationKey"]
    if update_if_current(story_id, version, expression, values):
        stale = {item.get("narrationKey"), item.get("originalNarrationKey")} - set(new.values()) - {None}
    else:
        stale = set(new.values())  # a newer save won the race: our files are not needed
    for key in stale:
        s3.delete_object(Bucket=MEDIA_BUCKET, Key=key)


def update_if_current(story_id, version, expression, values):
    try:
        table.update_item(Key={"id": story_id}, UpdateExpression=expression, ConditionExpression="publishCount = :v",
                          ExpressionAttributeValues={**values, ":v": version})
        return True
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False


def narrate(story_id, jobs):
    """Every piece of every language is narrated at the same time, then joined in order: one MP3 per language."""
    with ThreadPoolExecutor(NARRATION_WORKERS) as pool:
        pending = {lang: [pool.submit(synthesize, voice, ssml) for ssml in ssml_chunks(parts, NARRATION_PIECE)]
                   for lang, voice, parts in jobs}
        keys = {}
        for lang, pieces in pending.items():
            key = narration_key(story_id, lang)
            s3.put_object(Bucket=MEDIA_BUCKET, Key=key, Body=b"".join(p.result() for p in pieces), ContentType="audio/mpeg")
            keys[lang] = key
    return keys


def synthesize(voice, ssml):
    voice_id, engine, language_code = voice
    extra = {"LanguageCode": language_code} if language_code else {}
    return polly.synthesize_speech(Text=ssml, TextType="ssml", OutputFormat="mp3", VoiceId=voice_id, Engine=engine,
                                   **extra)["AudioStream"].read()


def ssml_chunks(parts, limit):
    """Paragraphs as <p> with a short pause between them (also across pieces), so it sounds like someone reading aloud."""
    groups, current, size = [], [], 0
    for part in parts:
        for piece in ([part] if len(part) <= limit else list(chunks_by_bytes(part, limit))):
            if current and size + len(piece) > limit:
                groups.append(current)
                current, size = [], 0
            current.append(f"<p>{escape(piece)}</p>")
            size += len(piece)
    if current:
        groups.append(current)
    pause = '<break time="500ms"/>'
    return ["<speak>" + pause.join(g) + (pause if i < len(groups) - 1 else "") + "</speak>" for i, g in enumerate(groups)]
