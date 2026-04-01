from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from openai import OpenAI
from services.text_cleaner import split_into_blocks, clean_email_text


# ---------------------------------------------------------------------------
# Module-level compiled patterns — compiled once, reused on every call
# ---------------------------------------------------------------------------

_DATE_PATTERNS = [
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),         ["%Y-%m-%d"]),
    (re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),   ["%m/%d/%Y", "%m/%d/%y"]),
    (re.compile(
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
        r"[a-z]*\s+\d{1,2},\s+\d{4}\b", re.IGNORECASE
    ), ["%B %d, %Y", "%b %d, %Y"]),
]

_SR_PATTERN     = re.compile(r"\b\d-\d{10}\b|\bSR[- #:]?\s*[A-Za-z0-9-]+", re.IGNORECASE)
_NAME_PATTERN   = re.compile(
    r"\b(?:Keith|Jim|Amardeep|Sreeni|Julien|Catalin|Cataalin|Lucky|Sonali|Tammy)\b"
    r"(?:\s+[A-Z][a-z]+)?",
)
_URL_PATTERN    = re.compile(r'https?://\S+')
_WS_PATTERN     = re.compile(r'\s{2,}')
_NORM_PATTERN   = re.compile(r"\s+")
_QA_SPLIT       = re.compile(r"\n\s*(?:A[.:\-]\s*|Answer\s*:\s*|Response\s*:\s*|Oracle\s*:\s*|Resolution\s*:\s*)", re.IGNORECASE)
_INLINE_SPLIT   = re.compile(r"(?<=[.?\n])\s*(Yes[,.]|No[,.]|The answer is|This is because)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_date(date_str) -> str:
    if not date_str:
        return ""
    import datetime as _dt
    if isinstance(date_str, (_dt.datetime, _dt.date)):
        return f"{date_str.month}/{date_str.day}/{str(date_str.year)[2:]}"
    s = str(date_str).strip()[:19]
    for pattern, fmts in _DATE_PATTERNS:
        m = pattern.search(s)
        if m:
            for fmt in fmts:
                try:
                    dt = datetime.strptime(m.group(0), fmt)
                    return f"{dt.month}/{dt.day}/{str(dt.year)[2:]}"
                except ValueError:
                    continue
    return s


def _norm(text: str) -> str:
    return _NORM_PATTERN.sub(" ", (text or "").lower().strip())


# ---------------------------------------------------------------------------
# Prompts — kept concise to minimise input tokens
# ---------------------------------------------------------------------------

COMBINED_PROMPT = """
You process Fidelity FMR email threads. Blocks are tagged QUESTION (Fidelity), ANSWER (Oracle), or UNKNOWN.
Text is pre-cleaned. Ignore remaining greetings/sign-offs.

Return ONE JSON with qa_pairs and recommendations.

QA_PAIRS — one per distinct question:
- Merge duplicates (same Q in multiple blocks = one pair)
- question: question text only | answer: answer text only, no URLs
- oracle_doc_link: oracle.com URL from answer or "" | other_doc_link: non-oracle URL or "" (never both)
- responsibility_owner: assigned person/team or "" | next_step: action item or ""
- comment: noteworthy context not in answer, or ""
- cloud_provider: "AWS" only if the question is specifically about AWS infrastructure/behavior, "Azure" only if Azure-specific, otherwise "Oracle". Do NOT set AWS just because the product is called ODB@AWS — set Oracle if the question is about Oracle functionality running on AWS.
- Skip confirmed-SR content (goes to recommendations) and workshop/training requests (goes to recommendations)

RECOMMENDATIONS — ODB@AWS, ODB@Azure, Enablement only (NOT Q&A):
- Confirmed active SR → ODB@AWS (Azure-specific → ODB@Azure)
- Conditional SR ("will open if needed") → skip
- Workshop/deep dive/training → Enablement, ONE ROW PER TOPIC
- Status: Not Resolved|Partially Resolved|Resolved | Priority: Low|Medium|High | Type: Technical Issue|Enhancement
- Enablement fields: On-Site Date (find date near topic or use first_communicated_date), Requester (who asked), Follow Up Enablement Topics (concise title), Owner, Status (always ""), Scheduled Date, Business Units Invloved, Internal SR#, Customer SR#

Return JSON:
{"qa_pairs":[{"question":"","answer":"","oracle_doc_link":"","other_doc_link":"","responsibility_owner":"","next_step":"","comment":"","cloud_provider":"Oracle|AWS|Azure"}],
"recommendations":[{"target_sheet":"ODB@AWS","summary":"","confidence":"high|medium|low","reason":"","row_values":{"Date":"","Issue":"","Status":"","Priority":"","Oracle Tracking Request":"","Type":"","Product":"","Contact":"","Description":"","Fidelity Comments":"","Oracle Progress/Comments":""},"source_excerpt":""}]}
""".strip()

MATCH_PROMPT = """
Match new_items to existing_rows by SR number (strongest) or topic similarity.
Return only changed fields for matches. Omit no-match and no-change items.
{"matches":[{"item_index":0,"existing_row_index":12,"existing_sheet":"ODB@AWS","changed_fields":{"Status":"Resolved"}}]}
""".strip()


# ---------------------------------------------------------------------------
# Recommender
# ---------------------------------------------------------------------------

class UpdateRecommender:
    def __init__(self, model_name: str, enabled: bool = True, api_key: str = None):
        self.model_name = model_name
        self.enabled    = enabled
        self.api_key    = api_key
        self._client    = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(api_key=self.api_key) if self.api_key else OpenAI()
        return self._client

    def recommend_updates(self, raw_text: str, workbook_context: Dict) -> List[Dict]:
        if not self.enabled:
            return self._heuristic_recommendations(clean_email_text(raw_text))
        try:
            blocks        = split_into_blocks(raw_text)
            email_subject = next((b["subject"] for b in blocks if b.get("subject")), "")
            first_date    = next(iter(sorted(b["date"] for b in blocks if b.get("date"))), "")

            payload   = self._combined_extract(blocks, first_date)
            qa_rows   = [self._qa_row_from_pair(p, email_subject, first_date)
                         for p in self._merge_duplicate_pairs(payload.get("qa_pairs", []))]
            non_qa    = self._normalize_results(payload.get("recommendations", []), first_date)
            all_new   = qa_rows + non_qa

            existing  = self._flatten_existing(workbook_context)
            return self._apply_matches(all_new, existing) if existing and all_new \
                   else [{**r, "type": "addition"} for r in all_new]

        except Exception as e:
            import traceback
            print("ERROR in recommend_updates:", e)
            traceback.print_exc()
            return self._heuristic_recommendations(clean_email_text(raw_text))

    # ------------------------------------------------------------------
    # LLM Call 1: combined extraction
    # ------------------------------------------------------------------

    def _combined_extract(self, blocks: List[Dict], first_date: str) -> Dict:
        formatted = "\n\n".join(
            f"[Block {i+1} — {b['role'].upper()}]\n{b['text']}"
            for i, b in enumerate(blocks) if b["text"]
        )
        resp = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": COMBINED_PROMPT},
                {"role": "user",   "content": json.dumps({
                    "email_blocks": formatted[:28000],
                    "first_communicated_date": first_date,
                }, default=str)},
            ],
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)

    # ------------------------------------------------------------------
    # LLM Call 2: fuzzy match (SR matches handled in Python — free)
    # ------------------------------------------------------------------

    def _flatten_existing(self, workbook_context: Dict) -> List[Dict]:
        out = []
        for sheet, rows in workbook_context.get("all_rows", {}).items():
            for i, row in enumerate(rows):
                issue = (row.get("Issue") or row.get("Question Topics") or
                         row.get("Follow Up Enablement Topics") or "")
                if issue:
                    out.append({
                        "sheet": sheet, "row_index": i,
                        "sr":    _norm(str(row.get("Oracle Tracking Request") or row.get("Internal SR#") or "")),
                        "issue": issue,
                        "status": str(row.get("Status") or ""),
                        "values": row,
                    })
        return out

    def _apply_matches(self, items: List[Dict], existing: List[Dict]) -> List[Dict]:
        sr_index = {r["sr"]: r for r in existing if len(r["sr"]) > 3}
        result, needs_fuzzy = [], []

        for i, rec in enumerate(items):
            rec_sr = _norm(rec["row_values"].get("Oracle Tracking Request") or
                           rec["row_values"].get("Internal SR#") or "")
            if rec_sr and rec_sr in sr_index:
                ex = sr_index[rec_sr]
                changed = self._diff_fields(rec["row_values"], ex)
                if changed:
                    result.append({**rec, "type": "update",
                                   "existing_row_index": ex["row_index"],
                                   "existing_sheet": ex["sheet"],
                                   "row_values": changed,
                                   "summary": f"UPDATE: {rec['summary']}",
                                   "reason": f"SR match in {ex['sheet']}. Changed: {', '.join(changed)}"})
            else:
                issue = (rec["row_values"].get("Issue") or rec["row_values"].get("Question Topics") or
                         rec["row_values"].get("Follow Up Enablement Topics") or "")
                (needs_fuzzy if issue.strip() else result).append(
                    (i, rec) if issue.strip() else {**rec, "type": "addition"}
                )

        if needs_fuzzy:
            result.extend(self._fuzzy_match_with_llm(needs_fuzzy, existing))
        return result

    def _diff_fields(self, new_vals: Dict, existing_row: Dict) -> Dict:
        ex_vals = existing_row.get("values", {})
        skip    = {"Date", "On-Site Date"}
        return {k: v for k, v in new_vals.items()
                if k not in skip and v and _norm(str(v)) != _norm(str(ex_vals.get(k) or ""))}

    def _fuzzy_match_with_llm(self, items_to_match: List[Tuple], existing: List[Dict]) -> List[Dict]:
        text_existing = [r for r in existing if not r["sr"]][:80]
        if not text_existing:
            return [{**rec, "type": "addition"} for _, rec in items_to_match]

        new_summary = [{"index": i, "sheet": rec["target_sheet"],
                        "issue": str(rec["row_values"].get("Issue") or
                                     rec["row_values"].get("Question Topics") or
                                     rec["row_values"].get("Follow Up Enablement Topics") or ""),
                        "new_values": rec["row_values"]}
                       for i, rec in items_to_match]
        try:
            resp = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": MATCH_PROMPT},
                    {"role": "user",   "content": json.dumps(
                        {"new_items": new_summary, "existing_rows": text_existing}, default=str)},
                ],
                response_format={"type": "json_object"},
            )
            matches = json.loads(resp.choices[0].message.content).get("matches", [])
        except Exception as e:
            print("WARNING: fuzzy match failed:", e)
            return [{**rec, "type": "addition"} for _, rec in items_to_match]

        match_map = {m["item_index"]: m for m in matches}
        result = []
        for i, rec in items_to_match:
            m = match_map.get(i)
            if m and m.get("changed_fields"):
                result.append({**rec, "type": "update",
                               "existing_row_index": m["existing_row_index"],
                               "existing_sheet": m["existing_sheet"],
                               "row_values": m["changed_fields"],
                               "summary": f"UPDATE: {rec['summary']}",
                               "reason": f"Topic match in {m['existing_sheet']}. Changed: {', '.join(m['changed_fields'])}"})
            else:
                result.append({**rec, "type": "addition"})
        return result

    # ------------------------------------------------------------------
    # Q&A
    # ------------------------------------------------------------------

    def _merge_duplicate_pairs(self, pairs: List[Dict]) -> List[Dict]:
        merged, seen = [], []
        for pair in pairs:
            q  = (pair.get("question") or "").strip()
            a  = (pair.get("answer")   or "").strip()
            nq = _norm(q)
            idx = next((i for i, s in enumerate(seen)
                        if nq == s or (len(nq) > 30 and (nq in s or s in nq))), -1)
            if idx == -1:
                seen.append(nq)
                merged.append(dict(pair))
            else:
                ex = merged[idx]
                if len(q) > len(ex.get("question", "")):
                    ex["question"] = q
                ea = (ex.get("answer") or "").strip()
                if a and ea and _norm(a) != _norm(ea):
                    ex["answer"] = ea + "\n\n" + a
                elif a and not ea:
                    ex["answer"] = a
                for f in ("oracle_doc_link", "other_doc_link", "responsibility_owner", "next_step", "comment"):
                    if not ex.get(f) and pair.get(f):
                        ex[f] = pair[f]
        return merged

    def _qa_row_from_pair(self, pair: Dict, email_subject: str, first_date: str = "") -> Dict:
        question = (pair.get("question") or "").strip()
        answer   = (pair.get("answer")   or "").strip()

        oracle_link = (pair.get("oracle_doc_link") or "").strip()
        other_link  = (pair.get("other_doc_link")  or "").strip()

        if oracle_link and "oracle.com" not in oracle_link:
            other_link, oracle_link = other_link or oracle_link, ""
        if other_link and "oracle.com" in other_link:
            oracle_link, other_link = oracle_link or other_link, ""

        full_text = question + " " + answer
        for url in _URL_PATTERN.findall(full_text):
            url = url.rstrip(".,;)")
            if "oracle.com" in url and not oracle_link:
                oracle_link = url
            elif "oracle.com" not in url and not other_link:
                other_link = url

        answer_clean = _WS_PATTERN.sub(" ", _URL_PATTERN.sub("", answer)).strip()

        return {
            "id": str(uuid.uuid4()), "type": "addition", "target_sheet": "Q&A",
            "summary": question[:180] or "Q&A item",
            "confidence": "high" if answer else "medium",
            "reason": "Extracted from email thread.",
            "row_values": {
                "Date":                              _format_date(first_date),
                "Question Topics":                   email_subject or self._summary_from_chunk(question)[:120],
                "Use Case Clarification":            question[:1200],
                "Possible Workaround or Recommendation": answer_clean[:1200],
                "Oracle Documentation Link":         oracle_link,
                "Google Documentation Link":         other_link,
                "Responsibility Owner":              (pair.get("responsibility_owner") or "").strip() or self._extract_name(question),
                "Next Step":                         (pair.get("next_step")  or "").strip()[:500],
                "Comment":                           (pair.get("comment")    or "").strip()[:500],
                "Internal SR#":                      self._extract_sr(question),
                "Cloud Provider":                    (pair.get("cloud_provider") or "").strip() or self._extract_cloud_provider(full_text),
            },
            "source_excerpt": question[:1200],
        }

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    def _normalize_results(self, recommendations: List[Dict], first_date: str = "") -> List[Dict]:
        return [{
            "id": str(uuid.uuid4()), "type": "addition",
            "target_sheet": item.get("target_sheet") or "ODB@AWS",
            "summary":      item.get("summary", "Recommended addition"),
            "confidence":   item.get("confidence", "medium"),
            "reason":       item.get("reason", ""),
            "row_values":   self._normalize_row_values(
                                item.get("target_sheet") or "ODB@AWS",
                                item.get("row_values", {}), first_date),
            "source_excerpt": (item.get("source_excerpt") or "")[:1200],
        } for item in recommendations]

    def _normalize_row_values(self, target_sheet: str, row_values: Dict, first_date: str = "") -> Dict:
        n = {k: ("" if v is None else v) for k, v in row_values.items()}
        if target_sheet in ("ODB@AWS", "ODB@Azure"):
            n["Date"]   = _format_date(first_date) if first_date else _format_date(n.get("Date", ""))
            n.setdefault("Date", "")
            if n.get("Status")   not in {"Not Resolved", "Partially Resolved", "Resolved"}:
                n["Status"]   = "Not Resolved"
            if n.get("Priority") not in {"Low", "Medium", "High"}:
                n["Priority"] = "Medium"
            if n.get("Type")     not in {"Technical Issue", "Enhancement"}:
                n["Type"]     = "Technical Issue" if any(
                    t in (n.get("Type") or "").lower()
                    for t in ["issue", "bug", "error", "incident", "outage", "failure"]
                ) else "Enhancement"
        elif target_sheet == "Enablement":
            n["Status"] = ""
            if not n.get("On-Site Date") and first_date:
                n["On-Site Date"] = _format_date(first_date)
            elif n.get("On-Site Date"):
                n["On-Site Date"] = _format_date(n["On-Site Date"])
        return n

    # ------------------------------------------------------------------
    # Heuristic fallback (no LLM)
    # ------------------------------------------------------------------

    def _heuristic_recommendations(self, cleaned_text: str) -> List[Dict]:
        results = []
        for chunk in self._split_chunks(cleaned_text):
            lower = chunk.lower()
            if any(t in lower for t in ["question", "clarify", "what is", "who is responsible", "limit", "documentation"]):
                subject = self._extract_subject(cleaned_text)
                for qa_chunk in (self._split_qa_chunks(chunk) or [chunk]):
                    item = self._heuristic_qa_candidate(qa_chunk, subject)
                    if item:
                        results.append(item)
            else:
                item = self._heuristic_candidate(chunk)
                if isinstance(item, list):
                    results.extend(item)
                elif item:
                    results.append(item)
        return results

    def _split_chunks(self, text: str) -> List[str]:
        return [p.strip() for p in re.split(r"\n\s*(?:-{3,}|_{3,}|\*{3,})\s*\n", text) if len(p.strip()) > 60]

    def _split_qa_chunks(self, text: str) -> List[str]:
        return [p.strip() for p in re.split(r"\n\s*(?:\d+[.)]\s+|[•\-\*]\s+(?=[A-Z])|(?:[A-Z][A-Z\s]{3,}:))", text) if len(p.strip()) > 40]

    def _extract_subject(self, text: str) -> str:
        m = re.search(r"(?i)subject\s*:\s*(.+)", text)
        if m:
            return re.sub(r"(?i)^(fw|fwd|re)\s*:\s*", "", m.group(1)).strip()[:180]
        return next((l.strip()[:180] for l in text.splitlines() if len(l.strip()) > 10), "")

    def _heuristic_qa_candidate(self, chunk: str, email_subject: str) -> Optional[Dict]:
        question, answer = self._split_question_answer(chunk)
        return self._candidate("Q&A", self._summary_from_chunk(chunk), "medium", "Heuristic Q&A.", {
            "Question Topics": email_subject,
            "Use Case Clarification": question[:1200],
            "Possible Workaround or Recommendation": answer[:1200],
            "Oracle Documentation Link": "", "Google Documentation Link": "",
            "Responsibility Owner": self._extract_name(chunk),
            "Next Step": "", "Comment": "",
            "Internal SR#": self._extract_sr(chunk),
            "Cloud Provider": self._extract_cloud_provider(chunk),
        }, question[:1200])

    def _split_question_answer(self, chunk: str) -> Tuple[str, str]:
        m = _QA_SPLIT.search(chunk)
        if m:
            return chunk[:m.start()].strip(), chunk[m.end():].strip()
        m2 = _INLINE_SPLIT.search(chunk)
        if m2:
            return chunk[:m2.start()].strip(), chunk[m2.start():].strip()
        return chunk.strip(), ""

    def _heuristic_candidate(self, chunk: str) -> Optional[Dict]:
        lower   = chunk.lower()
        summary = self._summary_from_chunk(chunk)
        excerpt = chunk[:1200]
        sr      = self._extract_sr(chunk)

        if any(t in lower for t in ["deep dive", "one pager", "architecture review", "enablement", "workshop", "training"]):
            topic_chunks = [
                t.strip() for t in re.split(r"(?:,\s*|\band\b)", chunk)
                if len(t.strip()) > 15 and any(
                    kw in t.lower() for kw in ["deep dive", "one pager", "architecture",
                                                "enablement", "workshop", "training", "review", "session"]
                )
            ] or [chunk]
            block_date = _format_date(self._extract_date(chunk))
            rows = [self._candidate("Enablement", self._summary_from_chunk(tc), "medium", "Heuristic enablement.", {
                "On-Site Date":              _format_date(self._extract_date(tc)) or block_date,
                "Requester":                 self._extract_name(tc) or self._extract_name(chunk),
                "Follow Up Enablement Topics": self._summary_from_chunk(tc),
                "Owner": "", "Status": "", "Scheduled Date": "",
                "Business Units Invloved": "", "Internal SR#": sr, "Customer SR#": "",
            }, tc[:1200]) for tc in topic_chunks]
            return rows[0] if len(rows) == 1 else rows

        sheet = "ODB@Azure" if "azure" in lower else "ODB@AWS"
        return self._candidate(sheet, summary, "medium", f"Heuristic {sheet}.", {
            "Date":                    _format_date(self._extract_date(chunk)),
            "Issue":                   summary,
            "Status":                  self._extract_status(lower) or "Not Resolved",
            "Priority":                self._extract_priority(lower),
            "Oracle Tracking Request": sr,
            "Type":                    "Technical Issue" if sr or any(
                                           t in lower for t in ["issue", "error", "failure", "bug"]
                                       ) else "Enhancement",
            "Product":                 self._extract_product(lower),
            "Contact":                 self._extract_name(chunk),
            "Description":             excerpt,
            "Fidelity Comments": "", "Oracle Progress/Comments": "",
        }, excerpt)

    def _candidate(self, target_sheet, summary, confidence, reason, row_values, excerpt) -> Dict:
        return {"id": str(uuid.uuid4()), "type": "addition", "target_sheet": target_sheet,
                "summary": summary, "confidence": confidence, "reason": reason,
                "row_values": row_values, "source_excerpt": excerpt}

    def _summary_from_chunk(self, chunk: str) -> str:
        return _NORM_PATTERN.sub(" ", re.split(r"(?<=[.!?])\s+", chunk.strip())[0])[:180]

    def _extract_sr(self, text: str) -> str:
        m = _SR_PATTERN.search(text)
        return m.group(0).strip() if m else ""

    def _extract_name(self, text: str) -> str:
        m = _NAME_PATTERN.search(text)
        return m.group(0) if m else ""

    def _extract_date(self, text: str) -> str:
        for pattern, fmts in _DATE_PATTERNS:
            m = pattern.search(text)
            if m:
                for fmt in fmts:
                    try:
                        return datetime.strptime(m.group(0), fmt).strftime("%Y-%m-%d")
                    except ValueError:
                        continue
                return m.group(0)
        return ""

    def _extract_priority(self, lower: str) -> str:
        if any(t in lower for t in ["sev1", "critical", "urgent", "high priority"]): return "High"
        if any(t in lower for t in ["medium", "moderate"]):                          return "Medium"
        if any(t in lower for t in ["low", "nice to have"]):                         return "Low"
        return "Medium"

    def _extract_status(self, lower: str) -> str:
        if any(t in lower for t in ["resolved", "completed", "complete"]):           return "Resolved"
        if any(t in lower for t in ["partially resolved", "partial"]):               return "Partially Resolved"
        if any(t in lower for t in ["in progress", "working on", "pending update"]): return "In Progress"
        return ""

    def _extract_product(self, lower: str) -> str:
        if "azure"   in lower: return "Oracle Database@Azure"
        if "aws"     in lower: return "Oracle Database@AWS"
        if "exadata" in lower: return "Exadata"
        return ""

    def _extract_cloud_provider(self, text: str) -> str:
        """
        Detect cloud provider from context. Presence of AWS/Azure alone is not
        enough — the question must be specifically about that platform, not just
        mentioning ODB@AWS as the product being discussed.
        """
        lower = text.lower()
        # Strong signal: explicit platform-specific issue markers
        aws_signals   = ["odb@aws", "oracle database@aws", "aws-specific", "on aws", "in aws",
                         "aws environment", "aws instance", "aws region"]
        azure_signals = ["odb@azure", "oracle database@azure", "azure-specific", "on azure",
                         "in azure", "azure environment", "azure instance", "azure region"]
        if any(t in lower for t in aws_signals):   return "AWS"
        if any(t in lower for t in azure_signals): return "Azure"
        return "Oracle"