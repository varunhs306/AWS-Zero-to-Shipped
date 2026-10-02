"""Languages a narrator can pick, and the Polly voices used to narrate them.

Codes are Amazon Transcribe batch language codes (docs: transcribe/latest/dg/supported-languages.html).
Voices come from Polly DescribeVoices in us-east-1; generative where available, else neural or standard.
"""

# Transcribe code -> display name (the share page shows these; "auto" lets Transcribe detect)
LANGUAGES = {
    "en-US": "English (US)", "en-GB": "English (UK)", "en-IN": "English (India)", "en-AU": "English (Australia)",
    "de-DE": "German", "de-CH": "German (Swiss)", "fr-FR": "French", "fr-CA": "French (Canada)",
    "es-ES": "Spanish (Spain)", "es-MX": "Spanish (Mexico)", "es-US": "Spanish (US)", "it-IT": "Italian",
    "pt-BR": "Portuguese (Brazil)", "pt-PT": "Portuguese (Portugal)", "nl-NL": "Dutch", "pl-PL": "Polish",
    "cs-CZ": "Czech", "sk-SK": "Slovak", "hu-HU": "Hungarian", "ro-RO": "Romanian", "bg-BG": "Bulgarian",
    "hr-HR": "Croatian", "sr-RS": "Serbian", "el-GR": "Greek", "tr-TR": "Turkish", "ru-RU": "Russian",
    "uk-UA": "Ukrainian", "sv-SE": "Swedish", "da-DK": "Danish", "no-NO": "Norwegian", "fi-FI": "Finnish",
    "ca-ES": "Catalan", "ar-SA": "Arabic", "ar-AE": "Arabic (Gulf)", "he-IL": "Hebrew", "fa-IR": "Farsi",
    "hi-IN": "Hindi", "bn-IN": "Bengali", "ta-IN": "Tamil", "te-IN": "Telugu", "kn-IN": "Kannada",
    "ml-IN": "Malayalam", "mr-IN": "Marathi", "gu-IN": "Gujarati", "pa-IN": "Punjabi", "ne-NP": "Nepali",
    "zh-CN": "Chinese (Mandarin)", "zh-TW": "Chinese (Traditional)", "zh-HK": "Chinese (Cantonese)",
    "ja-JP": "Japanese", "ko-KR": "Korean", "vi-VN": "Vietnamese", "th-TH": "Thai", "id-ID": "Indonesian",
    "ms-MY": "Malay", "tl-PH": "Filipino", "sw-KE": "Swahili", "af-ZA": "Afrikaans", "zu-ZA": "Zulu",
}

# Polly voices: (voice id, engine, Polly LanguageCode for bilingual voices or None)
_G, _N, _S = "generative", "neural", "standard"
VOICES = {
    "en": {"female": ("Ruth", _G, None), "male": ("Matthew", _G, None)},
    "de-DE": {"female": ("Vicki", _G, None), "male": ("Daniel", _G, None)},
    "de-CH": {"female": ("Sabrina", _G, None), "male": ("Daniel", _G, None)},
    "fr-FR": {"female": ("Lea", _G, None), "male": ("Remi", _G, None)},
    "fr-CA": {"female": ("Gabrielle", _G, None), "male": ("Liam", _G, None)},
    "es-ES": {"female": ("Lucia", _G, None), "male": ("Sergio", _G, None)},
    "es-MX": {"female": ("Mia", _G, None), "male": ("Andres", _G, None)},
    "es-US": {"female": ("Lupe", _G, None), "male": ("Pedro", _G, None)},
    "it-IT": {"female": ("Bianca", _G, None), "male": ("Lorenzo", _G, None)},
    "pt-BR": {"female": ("Camila", _G, None), "male": ("Thiago", _N, None)},
    "pt-PT": {"female": ("Ines", _N, None), "male": ("Ines", _N, None)},
    "nl-NL": {"female": ("Laura", _G, None), "male": ("Laura", _G, None)},
    "pl-PL": {"female": ("Ola", _G, None), "male": ("Ola", _G, None)},
    "cs-CZ": {"female": ("Jitka", _N, None), "male": ("Jitka", _N, None)},
    "ro-RO": {"female": ("Carmen", _S, None), "male": ("Carmen", _S, None)},
    "tr-TR": {"female": ("Burcu", _N, None), "male": ("Burcu", _N, None)},
    "ru-RU": {"female": ("Tatyana", _S, None), "male": ("Maxim", _S, None)},
    "sv-SE": {"female": ("Elin", _N, None), "male": ("Elin", _N, None)},
    "da-DK": {"female": ("Sofie", _N, None), "male": ("Sofie", _N, None)},
    "no-NO": {"female": ("Ida", _N, None), "male": ("Ida", _N, None)},
    "fi-FI": {"female": ("Suvi", _N, None), "male": ("Suvi", _N, None)},
    "ca-ES": {"female": ("Arlet", _N, None), "male": ("Arlet", _N, None)},
    "ar-SA": {"female": ("Hala", _N, None), "male": ("Zayd", _N, None)},
    "ar-AE": {"female": ("Hala", _N, None), "male": ("Zayd", _N, None)},
    "hi-IN": {"female": ("Kajal", _G, "hi-IN"), "male": ("Kajal", _G, "hi-IN")},
    "zh-CN": {"female": ("Zhiyu", _N, None), "male": ("Zhiyu", _N, None)},
    "zh-HK": {"female": ("Hiujin", _N, None), "male": ("Hiujin", _N, None)},
    "ja-JP": {"female": ("Kazuha", _N, None), "male": ("Takumi", _N, None)},
    "ko-KR": {"female": ("Seoyeon", _G, None), "male": ("Seoyeon", _G, None)},
}
VOICE_BY_PREFIX = {"de": "de-DE", "fr": "fr-FR", "es": "es-ES", "pt": "pt-BR", "ar": "ar-SA", "zh": "zh-CN"}
TRANSLATE_FULL_CODES = {"fr-CA", "es-MX", "pt-PT", "zh-TW"}


def voice_for(lang, preference):
    """Polly voice for a Transcribe language code, or None if Polly can't speak it."""
    if lang.startswith("en"):
        options = VOICES["en"]
    else:
        options = VOICES.get(lang) or VOICES.get(VOICE_BY_PREFIX.get(lang.split("-")[0], ""))
    if not options:
        return None
    return options.get(preference) or options["female"]


def translate_code(lang):
    return lang if lang in TRANSLATE_FULL_CODES else lang.split("-")[0]


def language_name(code):
    if code in LANGUAGES:
        return LANGUAGES[code].split(" (")[0]
    prefix = code.split("-")[0]
    return next((name.split(" (")[0] for c, name in LANGUAGES.items() if c.startswith(prefix + "-")), code)
