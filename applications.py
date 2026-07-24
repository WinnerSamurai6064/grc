"""
Green Recruiters - Applications Router
Two categories of endpoints:
  - Public: application submission (no auth required)
  - CMS: listing, detail view, status update, export (auth required)
"""

import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, PlainTextResponse

import db
import storage
from auth import require_admin

router = APIRouter()


# ============================================================
# Public: Application Submission
# ============================================================

@router.post("/api/public/apply")
async def submit_application(
    full_name: str = Form(...),
    primary_email: str = Form(...),
    secondary_email: Optional[str] = Form(None),
    phone_country_code: str = Form(...),
    phone_number: str = Form(...),
    residence_country: str = Form(...),
    nationality: str = Form(...),
    applying_for: str = Form(...),
    linkedin_url: str = Form(...),
    telegram_country_code: Optional[str] = Form(None),
    telegram_number: Optional[str] = Form(None),
    resume: UploadFile = File(...),
):
    """Public endpoint. Accepts a completed application with resume upload."""

    if not full_name.strip() or not primary_email.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name and email are required.")

    if not linkedin_url.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "LinkedIn profile is required.")

    job_exists = db.fetch_one(
        "SELECT 1 FROM jobs WHERE title = %s AND active = TRUE", (applying_for,)
    )
    if job_exists is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid job selection.")

    try:
        saved = storage.save_resume(resume.file, resume.filename, resume.content_type)
    except storage.InvalidFileError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    db.execute(
        """
        INSERT INTO applications (
            full_name, primary_email, secondary_email,
            phone_country_code, phone_number,
            residence_country, nationality, applying_for, linkedin_url,
            telegram_country_code, telegram_number,
            resume_filename, resume_storage_key, resume_size
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        """,
        (
            full_name.strip(),
            primary_email.strip().lower(),
            secondary_email.strip().lower() if secondary_email else None,
            phone_country_code.strip(),
            phone_number.strip(),
            residence_country.strip(),
            nationality.strip(),
            applying_for.strip(),
            linkedin_url.strip(),
            telegram_country_code.strip() if telegram_country_code else None,
            telegram_number.strip() if telegram_number else None,
            saved["original_filename"],
            saved["storage_key"],
            saved["size_bytes"],
        ),
    )

    return {"message": "Application submitted successfully."}


@router.get("/api/public/jobs")
async def list_active_jobs():
    """Public endpoint. Returns active jobs for the applicant dropdown."""
    jobs = db.fetch_all(
        "SELECT title FROM jobs WHERE active = TRUE ORDER BY title ASC"
    )
    return [job["title"] for job in jobs]


# ============================================================
# CMS: Application Management (auth required)
# ============================================================

@router.get("/api/cms/applications")
async def cms_list_applications(admin_email: str = Depends(require_admin)):
    """Returns application cards, newest first."""
    return db.fetch_all(
        """
        SELECT id, full_name, applying_for, residence_country, status, submitted_at
        FROM applications
        ORDER BY submitted_at DESC
        """
    )


@router.get("/api/cms/applications/{application_id}")
async def cms_get_application(application_id: str, admin_email: str = Depends(require_admin)):
    """Returns every submitted field for a single application."""
    application = db.fetch_one(
        "SELECT * FROM applications WHERE id = %s", (application_id,)
    )
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found.")
    return application


@router.patch("/api/cms/applications/{application_id}/status")
async def cms_update_status(
    application_id: str,
    new_status: str = Form(...),
    admin_email: str = Depends(require_admin),
):
    """Updates the status field of an application."""
    updated = db.execute_returning(
        """
        UPDATE applications SET status = %s
        WHERE id = %s
        RETURNING id
        """,
        (new_status.strip(), application_id),
    )
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found.")
    return {"message": "Status updated."}


# ============================================================
# CMS: Documents (resumes from local disk storage)
# ============================================================

@router.get("/api/cms/documents")
async def cms_list_documents(admin_email: str = Depends(require_admin)):
    """Lists all uploaded resumes with their originating application."""
    return db.fetch_all(
        """
        SELECT id AS application_id, full_name, resume_filename,
               resume_storage_key, resume_size, submitted_at
        FROM applications
        WHERE resume_storage_key IS NOT NULL
        ORDER BY submitted_at DESC
        """
    )


@router.get("/api/cms/documents/{application_id}/download")
async def cms_download_document(application_id: str, admin_email: str = Depends(require_admin)):
    """Streams a resume file from local disk for viewing/download."""
    application = db.fetch_one(
        "SELECT resume_storage_key, resume_filename FROM applications WHERE id = %s",
        (application_id,),
    )
    if application is None or application["resume_storage_key"] is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found.")

    try:
        path = storage.resolve_path(application["resume_storage_key"])
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found.")

    return FileResponse(
        path,
        filename=application["resume_filename"],
        media_type="application/octet-stream",
    )


# ============================================================
# CMS: Export
# ============================================================

@router.get("/api/cms/export/json")
async def export_json(admin_email: str = Depends(require_admin)):
    """Exports all applications as a JSON file."""
    applications = db.fetch_all(
        "SELECT * FROM applications ORDER BY submitted_at DESC"
    )
    payload = json.dumps(applications, indent=2, default=str)
    return PlainTextResponse(
        payload,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=applications.json"},
    )


@router.get("/api/cms/export/txt")
async def export_txt(admin_email: str = Depends(require_admin)):
    """Exports all applications as a plain text file."""
    applications = db.fetch_all(
        "SELECT * FROM applications ORDER BY submitted_at DESC"
    )

    lines = []
    for app in applications:
        lines.append("-" * 60)
        for key, value in app.items():
            lines.append(f"{key}: {value}")
    text_output = "\n".join(lines)

    return PlainTextResponse(
        text_output,
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=applications.txt"},
    )
