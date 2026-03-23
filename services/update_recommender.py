from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Dict, List

from openai import OpenAI


SYSTEM_PROMPT = """
You generate ADDITIVE row recommendations for the Fidelity FMR Technical Session Tracker workbook.

Rules:
1. Return only valid JSON.
2. Recommend only new rows, not edits to existing rows.
3. Choose only one of these target sheets: ODB@AWS, Q&A, ODB@Azure, Enablement.
4. Favor ODB@AWS for issues, bugs, enhancements, SRs, patching, provisioning, status, operations, network, peering, backup, restore, billing, scaling, and technical blockers.
5. Favor Q&A for question-and-answer clarifications, limits, responsibilities, documentation clarifications, and follow-up discussion points.
6. Favor Enablement for requested workshops, deep dives, architecture reviews, one-pagers, operational education, or follow-up education topics.
7. Use ODB@Azure only when the text is clearly about Azure-specific Oracle Database topics.
8. Keep summaries concise and business-readable.
9. Prefer empty string over null for unmapped optional fields.
10. Confidence must be one of: high, medium, low.

Return JSON in this shape:
{
  "recommendations": [
    {
      "target_sheet": "ODB@AWS",
      "summary": "...",
      "confidence": "high",
      "reason": "...",
      "row_values": {
        "Date": "YYYY-MM-DD or empty string",
        "Issue": "...",
        "Status": "Not Resolved|Partially Resolved|Resolved|Complete|In Progress|empty string",
        "Priority": "Low|Medium|High|Critical|empty string",
        "Oracle Tracking Request": "SR number or empty string",
        "Type": "Technical Issue|Enhancement|Question|empty string",
        "Product": "...",
        "Contact": "...",
        "Description": "...",
        "Fidelity Comments": "...",
        "Oracle Progress/Comments": "..."
      },
      "source_excerpt": "..."
    }
  ]
}
""".strip()


class UpdateRecommender:
    def __init__(self, model_name: str, enabled: bool = True):
        self.model_name = model_name
        self.enabled = enabled

    def recommend_updates(self, cleaned_text: str, workbook_context: Dict[str, object]) -> List[Dict[str, object]]:
        llm_results = []
        if self.enabled:
            try:
                llm_results = self._recommend_with_openai(cleaned_text, workbook_context)
            except Exception:
                llm_results = []

        if llm_results:
            return self._normalize_results(llm_results)

        return self._heuristic_recommendations(cleaned_text)

    def _recommend_with_openai(self, cleaned_text: str, workbook_context: Dict[str, object]) -> List[Dict[str, object]]:
        client = OpenAI()
        user_prompt = {
            "workbook_context": workbook_context,
            "email_text": cleaned_text[:30000],
            "workbook_mapping_notes": {
                "ODB@AWS": [
                    "Use for issue tracking, enhancement requests, operational blockers, SRs, patching, provisioning, scaling, backup and recovery, network and peering topics, and status follow-up items."
                ],
                "Q&A": [
                    "Use for clarifications, documented answers, limits, responsibilities, recommendations, and next-step questions."
                ],
                "Enablement": [
                    "Use for deep dives, one-pagers, architecture reviews, enablement asks, workshops, and training follow-ups."
                ],
                "ODB@Azure": [
                    "Use only for Azure-specific topics."
                ],
            },
        }

        response = client.responses.create(
            model=self.model_name,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_prompt, default=str)},
            ],
            text={"format": {"type": "json_object"}},
        )
        payload = json.loads(response.output_text)
        return payload.get("recommendations", [])

    def _normalize_results(self, recommendations: List[Dict[str, object]]) -> List[Dict[str, object]]:
        normalized = []
        for item in recommendations:
            target_sheet = item.get("target_sheet") or "Q&A"
            row_values = item.get("row_values", {})
            normalized.append(
                {
                    "id": str(uuid.uuid4()),
                    "target_sheet": target_sheet,
                    "summary": item.get("summary", "Recommended addition"),
                    "confidence": item.get("confidence", "medium"),
                    "reason": item.get("reason", f"The email content appears relevant to {target_sheet}."),
                    "row_values": self._normalize_row_values(target_sheet, row_values),
                    "source_excerpt": (item.get("source_excerpt") or "")[:1200],
                }
            )
        return normalized

    def _normalize_row_values(self, target_sheet: str, row_values: Dict[str, object]) -> Dict[str, object]:
        normalized = {k: self._normalize_cell_value(v) for k, v in row_values.items()}

        if target_sheet == "ODB@AWS":
            normalized.setdefault("Status", "Not Resolved")
            normalized.setdefault("Priority", "Medium")
            normalized.setdefault("Type", "Enhancement")
        elif target_sheet == "Enablement":
            normalized.setdefault("Status", "")
        elif target_sheet == "Q&A":
            normalized.setdefault("Next Step", "Review and confirm with Oracle/Fidelity owners.")
        return normalized

    def _heuristic_recommendations(self, cleaned_text: str) -> List[Dict[str, object]]:
        chunks = self._split_chunks(cleaned_text)
        results = []
        for chunk in chunks:
            item = self._heuristic_candidate(chunk)
            if item:
                results.append(item)
        return results

    def _split_chunks(self, text: str) -> List[str]:
        parts = re.split(r"\n\s*(?:-{3,}|_{3,}|\*{3,})\s*\n", text)
        return [part.strip() for part in parts if len(part.strip()) > 60]

    def _heuristic_candidate(self, chunk: str) -> Dict[str, object] | None:
        lower = chunk.lower()
        summary = self._summary_from_chunk(chunk)
        excerpt = chunk[:1200]
        date_value = self._extract_date(chunk)
        sr_number = self._extract_sr(chunk)
        priority = self._extract_priority(lower)
        requester = self._extract_name(chunk)

        if any(token in lower for token in ["deep dive", "one pager", "architecture review", "enablement", "workshop", "training"]):
            row_values = {
                "On-Site Date": date_value,
                "Requester": requester,
                "Follow Up Enablement Topics": summary,
                "Owner": "",
                "Status": "",
                "Scheduled Date": "",
                "Business Units Invloved": "",
                "Internal SR#": sr_number,
                "Customer SR#": "",
            }
            return self._candidate("Enablement", summary, "medium", "The text looks like a follow-up enablement request.", row_values, excerpt)

        if any(token in lower for token in ["question", "clarify", "what is", "who is responsible", "limit", "documentation", "doc", "recommendation"]):
            row_values = {
                "Question Topics": summary,
                "Use Case Clarification": excerpt[:900],
                "Possible Workaround or Recommendation": "Review the uploaded email thread and document the confirmed answer.",
                "Google Documentation Link": "",
                "Oracle Documentation Link": "",
                "Responsibility Owner": requester,
                "Next Step": "Validate answer and assign owner.",
                "Comment": "Generated from uploaded email PDF.",
            }
            return self._candidate("Q&A", summary, "medium", "The text reads like a clarification or Q&A topic.", row_values, excerpt)

        sheet = "ODB@Azure" if "azure" in lower else "ODB@AWS"
        issue_type = "Technical Issue" if sr_number or any(token in lower for token in ["issue", "error", "failure", "bug", "incident", "outage"]) else "Enhancement"
        row_values = {
            "Date": date_value,
            "Issue": summary,
            "Status": self._extract_status(lower) or "Not Resolved",
            "Priority": priority,
            "Oracle Tracking Request": sr_number,
            "Type": issue_type,
            "Product": self._extract_product(lower),
            "Contact": requester,
            "Description": excerpt[:1200],
            "Fidelity Comments": "Generated from uploaded email PDF.",
            "Oracle Progress/Comments": "Pending review before workbook insertion.",
        }
        return self._candidate(sheet, summary, "medium", f"The text appears to fit the {sheet} issue tracker.", row_values, excerpt)

    def _candidate(self, target_sheet: str, summary: str, confidence: str, reason: str, row_values: Dict[str, object], excerpt: str):
        return {
            "id": str(uuid.uuid4()),
            "target_sheet": target_sheet,
            "summary": summary,
            "confidence": confidence,
            "reason": reason,
            "row_values": row_values,
            "source_excerpt": excerpt,
        }

    def _summary_from_chunk(self, chunk: str) -> str:
        sentence = re.split(r"(?<=[.!?])\s+", chunk.strip())[0]
        sentence = re.sub(r"\s+", " ", sentence)
        return sentence[:180]

    def _extract_sr(self, text: str) -> str:
        match = re.search(r"\b\d-\d{10}\b|\bSR[- #:]?\s*[A-Za-z0-9-]+", text, flags=re.IGNORECASE)
        return match.group(0).strip() if match else ""

    def _extract_priority(self, lower: str) -> str:
        if any(token in lower for token in ["sev1", "critical", "urgent", "high priority"]):
            return "High"
        if any(token in lower for token in ["medium", "moderate"]):
            return "Medium"
        if any(token in lower for token in ["low", "nice to have"]):
            return "Low"
        return "Medium"

    def _extract_status(self, lower: str) -> str:
        if any(token in lower for token in ["resolved", "completed", "complete"]):
            return "Resolved"
        if any(token in lower for token in ["partially resolved", "partial"]):
            return "Partially Resolved"
        if any(token in lower for token in ["in progress", "working on", "pending update"]):
            return "In Progress"
        return ""

    def _extract_date(self, text: str) -> str:
        patterns = [
            r"\b\d{4}-\d{2}-\d{2}\b",
            r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
            r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2},\s+\d{4}\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                value = match.group(0)
                for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
                    try:
                        return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
                    except ValueError:
                        continue
                return value
        return ""

    def _extract_name(self, text: str) -> str:
        match = re.search(r"\b(?:Keith|Jim|Amardeep|Sreeni|Julien|Catalin|Cataalin|Lucky|Sonali|Tammy)\b(?:\s+[A-Z][a-z]+)?", text)
        return match.group(0) if match else ""

    def _extract_product(self, lower: str) -> str:
        if "azure" in lower:
            return "Oracle Database@Azure"
        if "aws" in lower:
            return "Oracle Database@AWS"
        if "exadata" in lower:
            return "Exadata"
        return ""

    @staticmethod
    def _normalize_cell_value(value):
        if value is None:
            return ""
        return value
