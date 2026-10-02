"""Personal-data removal shared by the worker (new drafts) and the API (narrator edits before publishing)."""
import re

import boto3

comprehend = boto3.client("comprehend")

PII_TYPES = {"PHONE", "EMAIL", "ADDRESS", "BANK_ACCOUNT_NUMBER", "CREDIT_DEBIT_NUMBER", "SSN", "PASSPORT_NUMBER",
             "DRIVER_ID", "IP_ADDRESS", "URL", "USERNAME", "PASSWORD", "PIN"}


def clip_bytes(text, limit):
    return text.encode("utf-8")[:limit].decode("utf-8", "ignore")


def scrub(english_paragraphs, original_paragraphs):
    """Comprehend finds contact details and full names; remove them from both language versions."""
    english = " ".join(english_paragraphs)
    entities = comprehend.detect_pii_entities(Text=clip_bytes(english, 90000), LanguageCode="en")["Entities"]
    values = set()
    for e in entities:
        value = english[e["BeginOffset"]:e["EndOffset"]]
        if e["Type"] == "ADDRESS" and not re.search(r"\d", value):
            continue  # places like "India" or "Germany" are part of the story; only street addresses go
        if e["Type"] in PII_TYPES or (e["Type"] == "NAME" and len(value.split()) >= 2):
            values.add(value)

    def clean_list(items):
        out = []
        for p in items:
            for value in sorted(values, key=len, reverse=True):
                p = p.replace(value, "[removed]")
            out.append(p)
        return out

    return clean_list(english_paragraphs), clean_list(original_paragraphs), bool(values)
