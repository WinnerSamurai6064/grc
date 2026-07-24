"""
Green Recruiters - CMS Router
Handles:
  - Authentication (request OTP / verify OTP)
  - CMS settings (site title, headings, text, contact info, hero image, favicon,
    home page logo/company image)
  - Job management (the dropdown shown on the public form)
  - Email panel (compose and send emails with attachments via SMTP)
  - Analytics (visit and application submission stats for analyst.html)
"""

import base64
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

import db
import storage
from auth import require_admin, request_otp, verify_otp
from smtp_service import send_cms_email

router = APIRouter()

ASSET_STORAGE_ROOT = Path(os.environ.get("CMS_ASSET_PATH", "/var/greenrecruiters/assets"))
ASSET_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/x-icon", "image/vnd.microsoft.icon"}
MAX_ASSET_SIZE_BYTES = 5 * 1024 * 1024


# ============================================================
# Authentication
# ============================================================

class OtpRequestBody(BaseModel):
    email: str


class OtpVerifyBody(BaseModel):
    email: str
    otp: str


@router.post("/api/cms/auth/request-otp")
async def cms_request_otp(body: OtpRequestBody):
    """
    Always returns the same generic message, regardless of whether
    the email belongs to an authorized administrator.
    """
    request_otp(body.email)
    return {"message": "If this email is authorized, a login code has been sent."}


@router.post("/api/cms/auth/verify-otp")
async def cms_verify_otp(body: OtpVerifyBody):
    """Verifies an OTP and returns a session token on success."""
    token = verify_otp(body.email, body.otp)
    return {"token": token, "token_type": "bearer"}


# ============================================================
# CMS Settings
# ============================================================

@router.get("/api/public/settings")
async def public_settings():
    """
    Public endpoint. Returns all CMS settings needed to render the
    application form (site title, headings, hero image, favicon, etc).
    """
    rows = db.fetch_all("SELECT setting_key, setting_value FROM cms_settings")
    return {row["setting_key"]: row["setting_value"] for row in rows}


@router.get("/api/cms/settings")
async def cms_get_settings(admin_email: str = Depends(require_admin)):
    """CMS endpoint. Same data as public/settings, but requires auth for editing context."""
    rows = db.fetch_all("SELECT setting_key, setting_value FROM cms_settings")
    return {row["setting_key"]: row["setting_value"] for row in rows}


@router.put("/api/cms/settings/{setting_key}")
async def cms_update_setting(
    setting_key: str,
    setting_value: str = Form(...),
    admin_email: str = Depends(require_admin),
):
    """Updates a single CMS setting. The frontend reflects this immediately."""
    db.execute(
        """
        INSERT INTO cms_settings (setting_key, setting_value, updated_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (setting_key)
        DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_at = NOW()
        """,
        (setting_key, setting_value),
    )
    return {"message": "Setting updated."}


@router.put("/api/cms/settings/{setting_key}/asset")
async def cms_upload_setting_asset(
    setting_key: str,
    asset: UploadFile = File(...),
    admin_email: str = Depends(require_admin),
):
    """
    Uploads an image asset (hero image or favicon) and stores the
    resulting public path as the setting's value.
    """
    contents = await asset.read()
    if len(contents) > MAX_ASSET_SIZE_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Asset exceeds 5 MB limit.")
    if asset.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported image type.")

    ext = Path(asset.filename).suffix.lower() or ".png"
    filename = f"{uuid.uuid4().hex}{ext}"
    destination = ASSET_STORAGE_ROOT / filename

    with open(destination, "wb") as out_file:
        out_file.write(contents)

    public_path = f"/assets/{filename}"

    db.execute(
        """
        INSERT INTO cms_settings (setting_key, setting_value, updated_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (setting_key)
        DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_at = NOW()
        """,
        (setting_key, public_path),
    )

    return {"message": "Asset uploaded.", "path": public_path}


# ============================================================
# Job Management
# ============================================================

@router.get("/api/cms/jobs")
async def cms_list_jobs(admin_email: str = Depends(require_admin)):
    """Lists all jobs, including inactive ones, for CMS management."""
    return db.fetch_all("SELECT * FROM jobs ORDER BY created_at DESC")


@router.post("/api/cms/jobs")
async def cms_create_job(title: str = Form(...), admin_email: str = Depends(require_admin)):
    """Creates a new job title shown in the applicant dropdown."""
    title = title.strip()
    if not title:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Job title is required.")

    db.execute(
        """
        INSERT INTO jobs (title) VALUES (%s)
        ON CONFLICT (title) DO NOTHING
        """,
        (title,),
    )
    return {"message": "Job created."}


@router.patch("/api/cms/jobs/{job_id}")
async def cms_update_job(
    job_id: int,
    active: bool = Form(...),
    admin_email: str = Depends(require_admin),
):
    """Activates or deactivates a job, controlling whether it appears publicly."""
    updated = db.execute_returning(
        "UPDATE jobs SET active = %s WHERE id = %s RETURNING id",
        (active, job_id),
    )
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")
    return {"message": "Job updated."}


@router.delete("/api/cms/jobs/{job_id}")
async def cms_delete_job(job_id: int, admin_email: str = Depends(require_admin)):
    """Permanently removes a job title."""
    db.execute("DELETE FROM jobs WHERE id = %s", (job_id,))
    return {"message": "Job deleted."}


# ============================================================
# Email Panel
# ============================================================

class EmailAttachment(BaseModel):
    filename: str
    content_base64: str
    content_type: Optional[str] = None


class SendEmailBody(BaseModel):
    to: list[str]
    cc: Optional[list[str]] = None
    bcc: Optional[list[str]] = None
    subject: str
    html_body: str
    attachments: Optional[list[EmailAttachment]] = None


@router.post("/api/cms/email/send")
async def cms_send_email(body: SendEmailBody, admin_email: str = Depends(require_admin)):
    """
    Sends an email from the CMS email panel.
    Attachments (including the company logo) are provided as base64
    strings from the frontend and decoded here before sending.
    """
    if not body.to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "At least one recipient is required.")

    decoded_attachments = []
    for attachment in body.attachments or []:
        try:
            decoded_attachments.append(
                {
                    "filename": attachment.filename,
                    "content": base64.b64decode(attachment.content_base64),
                    "content_type": attachment.content_type,
                }
            )
        except Exception:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid attachment: {attachment.filename}")

    send_cms_email(
        to_addresses=body.to,
        subject=body.subject,
        html_body=body.html_body,
        cc_addresses=body.cc,
        bcc_addresses=body.bcc,
        attachments=decoded_attachments,
    )

    return {"message": "Email sent."}


@router.get("/api/cms/email/log")
async def cms_email_log(admin_email: str = Depends(require_admin)):
    """Returns email send history for the CMS email panel."""
    return db.fetch_all(
        "SELECT * FROM email_log ORDER BY sent_at DESC LIMIT 200"
    )


# ============================================================
# Analytics (for analyst.html traffic dashboard)
# ============================================================

@router.get("/api/cms/analytics/overview")
async def cms_analytics_overview(admin_email: str = Depends(require_admin)):
    """
    Returns summary counters: total visits, visits per tracked path,
    total applications, and applications broken down by status.
    """
    total_visits = db.fetch_one("SELECT COUNT(*) AS count FROM page_views")
    visits_by_path = db.fetch_all(
        """
        SELECT path, COUNT(*) AS count
        FROM page_views
        GROUP BY path
        ORDER BY count DESC
        """
    )
    total_applications = db.fetch_one("SELECT COUNT(*) AS count FROM applications")
    applications_by_status = db.fetch_all(
        """
        SELECT status, COUNT(*) AS count
        FROM applications
        GROUP BY status
        ORDER BY count DESC
        """
    )

    return {
        "total_visits": total_visits["count"],
        "visits_by_path": visits_by_path,
        "total_applications": total_applications["count"],
        "applications_by_status": applications_by_status,
    }


@router.get("/api/cms/analytics/timeseries")
async def cms_analytics_timeseries(days: int = 30, admin_email: str = Depends(require_admin)):
    """
    Returns daily visit counts and daily application submission counts
    for the last N days (default 30), for charting.
    """
    if days < 1 or days > 365:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "days must be between 1 and 365.")

    visits_by_day = db.fetch_all(
        """
        SELECT DATE(viewed_at) AS day, COUNT(*) AS count
        FROM page_views
        WHERE viewed_at >= NOW() - (%s || ' days')::interval
        GROUP BY DATE(viewed_at)
        ORDER BY day ASC
        """,
        (days,),
    )

    applications_by_day = db.fetch_all(
        """
        SELECT DATE(submitted_at) AS day, COUNT(*) AS count
        FROM applications
        WHERE submitted_at >= NOW() - (%s || ' days')::interval
        GROUP BY DATE(submitted_at)
        ORDER BY day ASC
        """,
        (days,),
    )

    return {
        "visits_by_day": visits_by_day,
        "applications_by_day": applications_by_day,
    }
