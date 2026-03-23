from pathlib import Path
import json
import uuid

from flask import Flask, flash, redirect, render_template, request, send_file, url_for

from config import Config
from services.object_store import ObjectStoreClient
from services.pdf_service import extract_text_from_pdf
from services.text_cleaner import clean_email_text
from services.update_recommender import UpdateRecommender
from services.workbook_service import WorkbookService


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["WORK_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["OBJECT_STORE_LOCAL_DIR"]).mkdir(parents=True, exist_ok=True)

    workbook_service = WorkbookService(app.config["WORKBOOK_PATH"])
    object_store = ObjectStoreClient.from_config(app.config)
    recommender = UpdateRecommender(
        model_name=app.config["OPENAI_MODEL"],
        enabled=app.config["OPENAI_RECOMMENDER_ENABLED"],
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
    def process_pdf():
        pdf = request.files.get("pdf_file")
        if not pdf or not pdf.filename:
            flash("Please upload a PDF file.", "error")
            return redirect(url_for("index"))

        request_id = str(uuid.uuid4())
        pdf_path = Path(app.config["UPLOAD_DIR"]) / f"{request_id}.pdf"
        pdf.save(pdf_path)

        raw_text = extract_text_from_pdf(pdf_path)
        if not raw_text.strip():
            flash("No extractable text was found in the PDF.", "error")
            return redirect(url_for("index"))

        cleaned_text = clean_email_text(raw_text)
        text_filename = f"emails_{request_id}.txt"
        local_text_path = Path(app.config["WORK_DIR"]) / text_filename
        local_text_path.write_text(cleaned_text, encoding="utf-8")

        object_result = object_store.save_text(
            object_name=text_filename,
            text=cleaned_text,
            metadata={"request_id": request_id, "source": pdf.filename},
        )

        recommendations = recommender.recommend_updates(
            cleaned_text=cleaned_text,
            workbook_context=workbook_service.workbook_context(),
        )

        candidate_path = Path(app.config["WORK_DIR"]) / f"candidates_{request_id}.json"
        candidate_path.write_text(json.dumps(recommendations, indent=2), encoding="utf-8")

        return render_template(
            "review.html",
            request_id=request_id,
            candidates=recommendations,
            text_store_uri=object_result["uri"],
            storage_mode=object_result["mode"],
            text_preview=cleaned_text[:8000],
        )

    @app.post("/apply")
    def apply_updates():
        request_id = request.form.get("request_id", "").strip()
        candidate_path = Path(app.config["WORK_DIR"]) / f"candidates_{request_id}.json"
        if not candidate_path.exists():
            flash("Pending recommendations were not found. Please process the PDF again.", "error")
            return redirect(url_for("index"))

        all_candidates = json.loads(candidate_path.read_text(encoding="utf-8"))
        selected_ids = set(request.form.getlist("selected_updates"))
        selected = [candidate for candidate in all_candidates if candidate["id"] in selected_ids]

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

        output_path = workbook_service.apply_additions(selected, output_dir=app.config["WORK_DIR"])
        return send_file(
            output_path,
            as_attachment=True,
            download_name=Path(output_path).name,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
