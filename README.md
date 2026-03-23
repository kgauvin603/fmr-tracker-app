# FMR Technical Session Tracker Updater v2

This version keeps the Excel workbook as the system of record and upgrades the earlier scaffold with:

- real OCI Object Storage integration
- GPT-5.4 recommendation generation through the OpenAI API
- workbook-aware mapping tuned to the Fidelity tracker tabs
- additive workbook updates with downloadable `.xlsx` output
- modular Flask structure so `app.py` stays small

## Project layout

```text
fmr_tracker_app/
  app.py
  config.py
  .env.example
  requirements.txt
  services/
    object_store.py
    pdf_service.py
    text_cleaner.py
    update_recommender.py
    workbook_service.py
  templates/
    index.html
    review.html
```

## What changed in v2

### OCI Object Storage

`services/object_store.py` now supports real OCI uploads using either:

1. direct environment variables plus a private key file path
2. an OCI config file and profile
3. resource principals

If OCI credentials are incomplete, the app falls back to a local directory so the UI still runs.

### GPT-5.4 recommender

`services/update_recommender.py` now:

- sends workbook schema and sample rows to the model
- asks for strict JSON recommendations only
- maps recommendations to the real tracker tabs:
  - `ODB@AWS`
  - `Q&A`
  - `ODB@Azure`
  - `Enablement`
- falls back to workbook-specific heuristics if the API is unavailable

### Workbook handling

`services/workbook_service.py` preserves the workbook structure and formatting as much as possible and appends only new rows to the chosen worksheet.

## Important setup note for OCI

The values you provided are enough to wire the tenancy, user, fingerprint, region, namespace, bucket, and compartment.
You still need **one more item** for direct OCI API signing:

- `OCI_API_KEY_FILE=/absolute/path/to/your_private_key.pem`

Without a private key path, the OCI SDK cannot sign Object Storage requests.

If you already use `~/.oci/config`, set:

```bash
OCI_CONFIG_FILE=/home/youruser/.oci/config
OCI_CONFIG_PROFILE=DEFAULT
```

and you can omit the direct signer fields except the bucket and namespace.

## Local run

```bash
cd fmr_tracker_app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Place your workbook in the app folder or point `WORKBOOK_PATH` to the real location.

Then run:

```bash
python app.py
```

Open:

```text
http://127.0.0.1:8080
```

## Suggested `.env`

Use the values you supplied, plus `OPENAI_API_KEY` and either `OCI_API_KEY_FILE` or `OCI_CONFIG_FILE`.

```bash
FLASK_SECRET_KEY=replace-me
WORKBOOK_PATH=./Fidelity FMR Technnical Session Tracker.xlsx
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-5.4
OPENAI_RECOMMENDER_ENABLED=true

OCI_BUCKET_NAME=my-rag-db-OS
OCI_COMPARTMENT=ocid1.compartment.oc1..your_value
OCI_FINGERPRINT=your_fingerprint
OCI_NAMESPACE=your_namespace
OCI_REGION=us-ashburn-1
OCI_TENANCY_OCID=ocid1.tenancy.oc1..your_value
OCI_USER_OCID=ocid1.user.oc1..your_value
OCI_API_KEY_FILE=/absolute/path/to/oci_api_key.pem
```

## Current behavior

1. User uploads a PDF containing email threads.
2. App extracts and cleans the PDF text.
3. Cleaned text is written to OCI Object Storage when credentials are valid.
4. GPT-5.4 recommends new tracker rows in JSON.
5. User selects which rows to apply.
6. App appends those rows into a copy of the workbook.
7. Updated workbook is returned as a download.

## Design notes

- The workbook remains the master source of truth.
- CSV is not used for the system of record.
- The app is additive only: it appends rows and does not modify existing ones.
- Recommendation review remains in the UI before the workbook is changed.
