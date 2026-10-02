"""Shared helpers for the Before I Leave Lambdas."""
import json
import os
import time
from decimal import Decimal

import boto3

TABLE_NAME = os.environ["TABLE_NAME"]
MEDIA_BUCKET = os.environ["MEDIA_BUCKET"]

table = boto3.resource("dynamodb").Table(TABLE_NAME)
s3 = boto3.client("s3")

# Story lifecycle
UPLOADING = "UPLOADING"
TRANSCRIBING = "TRANSCRIBING"
PROCESSING = "PROCESSING"
DRAFT = "DRAFT"  # ready for the narrator to review, edit and publish
PUBLISHED = "PUBLISHED"
HIDDEN = "HIDDEN"  # auto-hidden after repeated reports, waiting for admin review
REMOVED = "REMOVED"  # removed by admin; reason visible to the author only
FAILED = "FAILED"

JOB_PREFIX = "bil-"


def now_ms():
    return int(time.time() * 1000)


def audio_key(story_id, ext):
    return f"raw/{story_id}/audio.{ext}"


def photo_key(story_id, ext):
    return f"photos/{story_id}/photo.{ext}"


def transcript_key(story_id):
    return f"transcripts/{story_id}.json"


def narration_key(story_id, lang):
    return f"narration/{story_id}/{lang}.mp3"


def job_name(story_id):
    return JOB_PREFIX + story_id


def set_fields(story_id, **fields):
    names, values, sets = {}, {}, []
    for i, (k, v) in enumerate({**fields, "updatedAt": now_ms()}.items()):
        names[f"#k{i}"], values[f":v{i}"] = k, v
        sets.append(f"#k{i} = :v{i}")
    table.update_item(
        Key={"id": story_id},
        UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def signed_url(key, expires=3600):
    return s3.generate_presigned_url("get_object", Params={"Bucket": MEDIA_BUCKET, "Key": key}, ExpiresIn=expires)


def _json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o == int(o) else float(o)
    raise TypeError(type(o))


def response(status, body):
    # CORS headers come from the Function URL config only (duplicates break browsers).
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(body, default=_json_default),
    }
