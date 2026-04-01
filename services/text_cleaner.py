import re
import unicodedata
from datetime import datetime as _dt
from typing import List, Dict


# ---------------------------------------------------------------------------
# Domain classification
# ---------------------------------------------------------------------------

QUESTION_DOMAINS = {"fidelity.com", "fmr.com"}
ANSWER_DOMAINS = {"oracle.com"}


def classify_sender(from_line: str) -> str:
    from_line = from_line.lower()
    for domain in QUESTION_DOMAINS:
        if domain in from_line:
            return "question"
    for domain in ANSWER_DOMAINS:
        if domain in from_line:
            return "answer"
    return "unknown"


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_M  = re.MULTILINE
_I  = re.IGNORECASE
_MI = re.MULTILINE | re.IGNORECASE
_ID = re.IGNORECASE | re.DOTALL

_HEADER_LINES = re.compile(
    r"^>*\s*(?:from|sent|to|cc|bcc|date|reply-to)\s*:.*$", _MI
)

_PAGE_MARKERS = [
    re.compile(r"\[Page\s+\d+(?:\s+of\s+\d+)?\]", _I),
    re.compile(r"^\s*-?\s*page\s+\d+\s*(?:of\s+\d+)?\s*-?\s*$", _MI),
    re.compile(r"^\s*-\s*\d+\s*-\s*$", _M),
    re.compile(r"^\s*\d+\s*$", _M),
]

_DISCLAIMERS = re.compile(
    r"confidential\s*[-\u2013]\s*(?:internal|oracle\s+internal)[^\n]*"
    r"|this\s+message\s+is\s+from\s+an\s+external\s+send\w*[^\n]*"
    r"|this\s+e-?mail\s+and\s+any\s+attachments.*?(?=\n\n|$)"
    r"|the\s+information\s+contained\s+in\s+this\s+e-?mail.*?(?=\n\n|$)"
    r"|please\s+consider\s+the\s+environment.*?(?=\n\n|$)"
    r"|confidentiality\s+notice.*?(?=\n\n|$)"
    r"|this\s+message\s+is\s+intended\s+only\s+for.*?(?=\n\n|$)"
    r"|if\s+you\s+(?:have\s+received|are\s+not\s+the\s+intended).*?(?=\n\n|$)"
    r"|privileged?\s+and\s+confidential.*?(?=\n\n|$)"
    r"|external\s+email.*?(?=\n\n|$)"
    r"|notice\s*:\s*this\s+email\s+is\s+from\s+an\s+external[^\n]*",
    _ID,
)

_SIGNATURES = [
    re.compile(r"^--\s*$", _MI),
    re.compile(r"^_{4,}\s*$", _M),
    # Name + 0-3 title/company lines + contact line
    re.compile(
        r"^[A-Z][a-zA-Z .'\-]{1,50}\n(?:[A-Z][a-zA-Z .,'&/\-]{0,60}\n){0,3}"
        r".*?(?:mobile|tel|ph|cell|fax|calendar|check my calendar|@)[^\n]*$",
        re.MULTILINE | re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"^\s*(?:mobile|telephone|tel|ph|cell|fax)\s*[-\u2013:]\s*[\d\s().+|]+\s*$", _MI),
    re.compile(r"^\s*(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}\s*$", _M),
    re.compile(r"^\s*[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\s*$", _M),
    re.compile(r"^\s*\([A-Z]{2,5}(?:[+\-]\d{1,2})?\)\s*$", _M),
    re.compile(r"^\s*check\s+my\s+calendar\s*$", _MI),
    # Greetings
    re.compile(r"^(?:hi|hello|dear|good\s+(?:morning|afternoon|evening))\b[^\n]{0,60}$", _MI),
    # Sign-offs
    re.compile(r"^(?:thanks?(?:\s+you)?|best\s+regards?|regards?|cheers|sincerely|kind\s+regards?)[,.]?\s*$", _MI),
]

_THREAD_NOISE = re.compile(
    r"begin forwarded message:|\[cid:[^\]]*\]|<mailto:[^>]*>|<https?://[^>]*>", _I
)


# ---------------------------------------------------------------------------
# Date / subject helpers
# ---------------------------------------------------------------------------

_DATE_FORMATS = [
    "%B %d, %Y at %I:%M %p", "%B %d, %Y at %I:%M:%S %p",
    "%b %d, %Y at %I:%M %p", "%B %d, %Y", "%b %d, %Y",
    "%m/%d/%Y", "%Y-%m-%d",
]


def parse_header_date(date_str: str) -> str:
    date_str = re.sub(r"\s+", " ", date_str.strip())
    date_str = re.sub(r"\s+[A-Z]{2,5}([+-]\d{1,2})?$", "", date_str).strip()
    for fmt in _DATE_FORMATS:
        try:
            return _dt.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _clean_subject(subject: str) -> str:
    prev = None
    while prev != subject:
        prev = subject
        subject = re.sub(r"(?i)^subject\s*:\s*", "", subject).strip()
        subject = re.sub(r"(?i)^(fw|fwd|re)\s*:\s*", "", subject).strip()
        subject = re.sub(r"(?i)^\[ext(ernal)?\]\s*:?\s*", "", subject).strip()
        subject = re.sub(r"^[:\-\u2013|]+\s*", "", subject).strip()
    sr_match = re.search(r"\d+-\d{7,}", subject)
    sr_fallback = sr_match.group(0) if sr_match else ""
    subject = re.sub(r"^\d+-\d+\s*[-\u2013]?\s*", "", subject).strip()
    subject = re.sub(r"^[:\-\u2013|]+\s*", "", subject).strip()
    return subject[:180] if subject else sr_fallback or ""


# ---------------------------------------------------------------------------
# Core cleaner — regex only, no LLM
# ---------------------------------------------------------------------------

def clean_block(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)

    for p in _PAGE_MARKERS:
        text = p.sub("", text)

    text = _HEADER_LINES.sub("", text)
    text = _DISCLAIMERS.sub("", text)

    for p in _SIGNATURES:
        text = p.sub("", text)

    text = _THREAD_NOISE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return text.strip()


def clean_email_text(text: str) -> str:
    return clean_block(unicodedata.normalize("NFKD", text))


# ---------------------------------------------------------------------------
# Block splitter
# ---------------------------------------------------------------------------

def split_into_blocks(raw_text: str, **kwargs) -> List[Dict[str, str]]:
    """
    Split raw email thread into sender-tagged blocks.
    kwargs accepted but ignored — previously accepted client/model for LLM
    cleaning which has been removed to reduce API costs.
    """
    text = unicodedata.normalize("NFKD", raw_text)
    block_pattern = re.compile(r"^(?:>*\s*)(?=from\s*:)", re.IGNORECASE | re.MULTILINE)
    raw_blocks = [b.strip() for b in block_pattern.split(text) if b.strip()]

    blocks = []
    for block in raw_blocks:
        from_m  = re.search(r"from\s*:\s*(.+)",    block, re.IGNORECASE)
        subj_m  = re.search(r"subject\s*:\s*(.+)", block, re.IGNORECASE)
        date_m  = re.search(r"date\s*:\s*(.+)",    block, re.IGNORECASE)

        from_line = from_m.group(1).strip()  if from_m  else ""
        subject   = _clean_subject(subj_m.group(1).strip() if subj_m else "")
        date      = parse_header_date(date_m.group(1)) if date_m else ""
        role      = classify_sender(from_line)
        cleaned   = clean_block(block)

        if cleaned:
            blocks.append({"role": role, "subject": subject, "date": date, "text": cleaned})

    return blocks