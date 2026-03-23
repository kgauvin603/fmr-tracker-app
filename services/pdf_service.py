from pathlib import Path

from pypdf import PdfReader



def extract_text_from_pdf(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    pages = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append(f"[Page {index}]\n{text.strip()}")
    return "\n\n".join(pages).strip()
