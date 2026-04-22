from pathlib import Path
import json
import re
import uuid

from flask import Flask, flash, redirect, render_template, request, send_file, url_for

from config import Config
from services.object_store import ObjectStoreClient
from services.pdf_service import extract_text_from_pdf
from services.text_cleaner import clean_email_text
from services.update_recommender import UpdateRecommender
from services.excel_processor import process_excel_file
from services.docx_service import extract_text_from_docx
from services.workbook_service import WorkbookService
from services.roles_loader import load_roles_context


def _norm(t):
    return re.sub(r"\s+", " ", (t or "").lower().strip())


def _deduplicate_recommendations(recommendations: list) -> list:
    """Deduplicate across files in the same batch by SR / question / topic."""
    seen = {}
    deduped = []

    for rec in recommendations:
        sheet = rec.get("target_sheet", "")
        rv    = rec.get("row_values", {})
        rtype = rec.get("type", "addition")

        if sheet == "Q&A":
            key = ("Q&A", _norm(rv.get("Use Case Clarification", ""))[:120])
        elif sheet in ("ODB@AWS", "ODB@Azure"):
            sr    = _norm(rv.get("Oracle Tracking Request", ""))
            issue = _norm(rv.get("Issue", ""))
            key   = (sheet, sr) if sr else (sheet, issue[:80])
        elif sheet == "Enablement":
            key = ("Enablement", _norm(rv.get("Follow Up Enablement Topics", ""))[:80])
        else:
            key = (sheet, _norm(str(rv)[:80]))

        if key not in seen:
            seen[key] = len(deduped)
            deduped.append(rec)
        else:
            existing = deduped[seen[key]]
            # Prefer updates over additions; prefer higher confidence
            if rtype == "update" and existing.get("type") != "update":
                deduped[seen[key]] = rec
            elif rec.get("confidence") == "high" and existing.get("confidence") != "high":
                deduped[seen[key]] = rec

    return deduped


def _process_text_file(raw_text: str, filename: str, request_id: str,
                       recommender, wb_context, object_store, app_config) -> tuple:
    """Shared processing for PDF and DOCX files. Returns (recommendations, cleaned_text, object_result)."""
    if not raw_text.strip():
        return [], "", {"uri": "", "mode": "local"}

    cleaned_text = clean_email_text(raw_text)
    text_filename = f"emails_{request_id}_{filename}.txt"
    local_text_path = Path(app_config["WORK_DIR"]) / text_filename
    local_text_path.write_text(cleaned_text, encoding="utf-8")

    object_result = object_store.save_text(
        object_name=text_filename,
        text=cleaned_text,
        metadata={"request_id": request_id, "source": filename},
    )

    recommendations = recommender.recommend_updates(
        raw_text=raw_text,
        workbook_context=wb_context,
    )
    return recommendations, cleaned_text, object_result


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["WORK_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["OBJECT_STORE_LOCAL_DIR"]).mkdir(parents=True, exist_ok=True)

    workbook_service = WorkbookService(app.config["WORKBOOK_PATH"])
    object_store     = ObjectStoreClient.from_config(app.config)
    roles_context    = load_roles_context(app.config["ROLES_PATH"])
    recommender      = UpdateRecommender(
        model_name=app.config["OPENAI_MODEL"],
        enabled=app.config["OPENAI_RECOMMENDER_ENABLED"],
        api_key=app.config["OPENAI_API_KEY"],
        roles_context=roles_context,
    )

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            sheet_summaries=workbook_service.sheet_summaries(),
            storage_mode=object_store.mode,
            workbook_name=Path(app.config["WORKBOOK_PATH"]).name,
            llm_enabled=app.config["OPENAI_RECOMMENDER_ENABLED"],
        )

    @app.post("/process")
    def process_files():
        files = [f for f in request.files.getlist("pdf_files") if f and f.filename]
        if not files:
            flash("Please upload at least one file.", "error")
            return redirect(url_for("index"))

        request_id          = str(uuid.uuid4())
        all_recommendations = []
        all_cleaned_texts   = []
        last_object_result  = {"uri": "", "mode": "local"}
        # Load workbook context once for the whole batch — not once per file
        wb_context = workbook_service.workbook_context()

        for file in files:
            filename  = file.filename
            file_path = Path(app.config["UPLOAD_DIR"]) / f"{request_id}_{filename}"
            file.save(file_path)
            ext = Path(filename).suffix.lower()

            try:
                if ext in (".xlsx", ".xls"):
                    recs = process_excel_file(
                        file_path=str(file_path),
                        client=recommender.client,
                        model=app.config["OPENAI_MODEL"],
                        workbook_context=wb_context,
                        roles_context=roles_context,
                    )
                    all_recommendations.extend(recs)
                    all_cleaned_texts.append(f"[Excel: {filename}]")

                elif ext == ".pdf":
                    raw_text = extract_text_from_pdf(file_path)
                    recs, cleaned, obj = _process_text_file(
                        raw_text, filename, request_id,
                        recommender, wb_context, object_store, app.config
                    )
                    if not raw_text.strip():
                        flash(f"No text found in {filename} — skipping.", "warning")
                        continue
                    all_recommendations.extend(recs)
                    all_cleaned_texts.append(cleaned)
                    last_object_result = obj

                elif ext == ".docx":
                    raw_text = extract_text_from_docx(file_path)
                    recs, cleaned, obj = _process_text_file(
                        raw_text, filename, request_id,
                        recommender, wb_context, object_store, app.config
                    )
                    if not raw_text.strip():
                        flash(f"No text found in {filename} — skipping.", "warning")
                        continue
                    all_recommendations.extend(recs)
                    all_cleaned_texts.append(cleaned)
                    last_object_result = obj

                else:
                    flash(f"Unsupported file type: {filename} — use PDF, Excel, or Word.", "warning")

            except Exception as e:
                flash(f"Error processing {filename}: {e}", "error")

        all_recommendations = _deduplicate_recommendations(all_recommendations)

        if not all_recommendations:
            flash("No recommendations could be generated.", "error")
            return redirect(url_for("index"))

        candidate_path = Path(app.config["WORK_DIR"]) / f"candidates_{request_id}.json"
        candidate_path.write_text(json.dumps(all_recommendations, indent=2), encoding="utf-8")

        return render_template(
            "review.html",
            request_id=request_id,
            candidates=all_recommendations,
            text_store_uri=last_object_result["uri"],
            storage_mode=last_object_result["mode"],
            text_preview="\n\n---\n\n".join(all_cleaned_texts)[:8000],
            workbook_stem=Path(app.config["WORKBOOK_PATH"]).stem,
        )

    @app.post("/apply")
    def apply_updates():
        request_id     = request.form.get("request_id", "").strip()
        candidate_path = Path(app.config["WORK_DIR"]) / f"candidates_{request_id}.json"
        if not candidate_path.exists():
            flash("Recommendations not found. Please process the file again.", "error")
            return redirect(url_for("index"))

        all_candidates = json.loads(candidate_path.read_text(encoding="utf-8"))
        selected_ids   = set(request.form.getlist("selected_updates"))
        selected       = [c for c in all_candidates if c["id"] in selected_ids]

        for c in selected:
            override = request.form.get(f"sheet_{c['id']}", "").strip()
            if override:
                c["target_sheet"] = override

        if not selected:
            flash("Select at least one recommendation to apply.", "error")
            return render_template(
                "review.html",
                request_id=request_id,
                candidates=all_candidates,
                text_store_uri=request.form.get("text_store_uri", ""),
                storage_mode=request.form.get("storage_mode", ""),
                text_preview=request.form.get("text_preview", ""),
            )

        output_path = workbook_service.apply_additions(
            selected, output_dir=app.config["WORK_DIR"]
        )
        return send_file(
            output_path,
            as_attachment=True,
            download_name=Path(output_path).name,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081, debug=True)