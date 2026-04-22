import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "change-me")

    UPLOAD_DIR = os.getenv("UPLOAD_DIR", str(BASE_DIR / "runtime" / "uploads"))
    WORK_DIR = os.getenv("WORK_DIR", str(BASE_DIR / "runtime" / "work"))
    OBJECT_STORE_LOCAL_DIR = os.getenv(
        "OBJECT_STORE_LOCAL_DIR", str(BASE_DIR / "runtime" / "object_store_fallback")
    )

    WORKBOOK_PATH = os.getenv(
        "WORKBOOK_PATH",
        str(BASE_DIR / "Fidelity FMR Technnical Session Tracker.xlsx"),
    )

    ROLES_PATH = os.getenv("ROLES_PATH", str(BASE_DIR / "Roles.xlsx"))

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4")
    OPENAI_TIMEOUT_SECONDS = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "120"))
    OPENAI_RECOMMENDER_ENABLED = os.getenv("OPENAI_RECOMMENDER_ENABLED", "true").lower() in {"1", "true", "yes"}

    OCI_BUCKET_NAME = os.getenv("OCI_BUCKET_NAME")
    OCI_COMPARTMENT = os.getenv("OCI_COMPARTMENT")
    OCI_FINGERPRINT = os.getenv("OCI_FINGERPRINT")
    OCI_NAMESPACE = os.getenv("OCI_NAMESPACE")
    OCI_REGION = os.getenv("OCI_REGION", "us-ashburn-1")
    OCI_TENANCY_OCID = os.getenv("OCI_TENANCY_OCID")
    OCI_USER_OCID = os.getenv("OCI_USER_OCID")
    OCI_API_KEY_FILE = os.getenv("OCI_API_KEY_FILE")
    OCI_CONFIG_FILE = os.getenv("OCI_CONFIG_FILE")
    OCI_CONFIG_PROFILE = os.getenv("OCI_CONFIG_PROFILE", "DEFAULT")
    OCI_USE_RESOURCE_PRINCIPAL = os.getenv("OCI_USE_RESOURCE_PRINCIPAL", "false").lower() in {"1", "true", "yes"}
