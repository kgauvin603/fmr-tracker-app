# FMR Tracker App

A modular Flask application for processing PDF exports of email threads, extracting and cleaning the text, storing the cleaned text in OCI Object Storage, generating workbook update recommendations with OpenAI GPT, and selectively applying approved additions back into the Excel workbook system of record.

## Executive Summary

The FMR Tracker App was designed to streamline the conversion of technical email content into structured workbook updates while preserving the existing Excel workbook as the authoritative system of record.

The application provides a guided user workflow that:

1. accepts a PDF containing email threads
2. extracts and cleans the text
3. stores the cleaned text in OCI Object Storage
4. uses an OpenAI model to recommend workbook additions
5. presents those recommendations in a review interface
6. applies only the user-approved updates
7. returns an updated downloadable `.xlsx` workbook

The solution emphasizes:
- modular Python design
- Oracle-style visual presentation
- workbook-first data stewardship
- traceability through stored cleaned text artifacts
- user-controlled review before changes are committed

## Highlights

- Flask web UI with modular service-based architecture
- Oracle-inspired visual design using a refined muted enterprise palette
- Progress tracker during PDF processing
- PDF email thread ingestion and text cleanup
- OCI Object Storage integration for cleaned text persistence
- OpenAI-powered recommendation engine
- Multi-sheet Excel workbook update flow
- Review-and-apply model for user-controlled workbook updates
- Downloadable updated `.xlsx` workbook
- OEL9 deployment using `.venv`
- systemd-friendly runtime model

## Oracle-Styled UI Enhancement

A major enhancement in the current version is the modernized user interface inspired by Oracle-style enterprise application design principles.

### UI design goals

The user interface was enhanced to provide:

- a more polished and executive-friendly appearance
- a cleaner guided workflow for non-technical users
- stronger visual hierarchy
- a more refined enterprise feel suitable for internal demos and prototypes
- visible process feedback during long-running PDF analysis tasks

### UI characteristics

The updated interface includes:

- muted taupe, sand, sage, teal, slate, and clay color tones
- clean card-based layout
- more deliberate spacing and typography
- a guided upload and review workflow
- a staged processing overlay with progress feedback
- clearer separation between workbook metadata, workflow guidance, and review actions

### Why this matters

For a prototype that may be shown to architects, account teams, technical leaders, or customers, interface polish matters. The Oracle-styled refinement makes the application feel more intentional, trustworthy, and production-oriented even while the backend remains lightweight and modular.

## Solution Architecture

```mermaid
flowchart TD
    A[User uploads PDF of email threads] --> B[Flask Web Application]
    B --> C[PDF Extraction Service]
    C --> D[Text Cleaning Service]
    D --> E[OCI Object Storage]
    D --> F[Recommendation Engine]
    F --> G[Review Screen]
    G --> H[Workbook Update Service]
    H --> I[Updated Excel Workbook Download]

    B --> J[Workbook Summary / UI Layer]
    B --> K[Progress Overlay / Process Feedback]
