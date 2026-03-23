import re

HEADER_PATTERNS = [
    r"^from:\s.*$",
    r"^sent:\s.*$",
    r"^to:\s.*$",
    r"^cc:\s.*$",
    r"^bcc:\s.*$",
    r"^subject:\s.*$",
]

DISCLAIMER_PATTERNS = [
    r"this e-mail and any attachments.*?(?=\n\n|$)",
    r"the information contained in this e-mail.*?(?=\n\n|$)",
    r"please consider the environment before printing.*?(?=\n\n|$)",
    r"external email.*?(?=\n\n|$)",
]

THREAD_NOISE_PATTERNS = [
    r"begin forwarded message:",
    r"\[cid:.*?\]",
    r"<mailto:.*?>",
    r"<https?://.*?>",
]


def clean_email_text(text: str) -> str:
    cleaned = text.replace("\r", "")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)

    for pattern in HEADER_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE | re.MULTILINE)

    for pattern in DISCLAIMER_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE | re.DOTALL)

    for pattern in THREAD_NOISE_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    return cleaned.strip()
