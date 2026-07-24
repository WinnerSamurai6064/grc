"""
Green Recruiters - Authentication Layer
Passwordless OTP login for CMS administrators, plus session token
issuance/verification used to protect all CMS endpoints.

Flow:
  1. Admin submits email -> request_otp()
  2. If the email is authorized, an OTP is generated, hashed, stored,
     and emailed. If not authorized, the SAME response is returned
     (no information leakage about which emails are valid).
  3. Admin submits email + OTP -> verify_otp()
  4. On success, a signed session token is issued.
  5. require_admin() is used as a FastAPI dependency to protect routes.
"""

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Header, HTTPException, status

import db
from smtp_service import send_otp_email

OTP_LENGTH = 6
OTP_TTL_MINUTES = int(os.environ.get("OTP_TTL_MINUTES", "10"))

SESSION_SECRET = os.environ["SESSION_SECRET"]
SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "12"))
JWT_ALGORITHM = "HS256"


def _generate_otp() -> str:
    """Generate a numeric OTP of OTP_LENGTH digits."""
    return "".join(secrets.choice("0123456789") for _ in range(OTP_LENGTH))


def _hash_otp(otp: str, email: str) -> str:
    """Hash an OTP together with the email so hashes aren't reusable cross-account."""
    return hashlib.sha256(f"{email.lower()}:{otp}".encode()).hexdigest()


def _is_authorized_admin(email: str) -> bool:
    row = db.fetch_one(
        "SELECT 1 FROM authorized_admins WHERE email = %s AND active = TRUE",
        (email.lower(),),
    )
    return row is not None


def request_otp(email: str) -> None:
    """
    Handle an OTP request. Always behaves identically whether or not
    the email is authorized, to avoid leaking which emails exist.
    """
    email = email.strip().lower()

    if _is_authorized_admin(email):
        otp = _generate_otp()
        otp_hash = _hash_otp(otp, email)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MINUTES)

        db.execute(
            """
            INSERT INTO otp_codes (email, otp_hash, expires_at)
            VALUES (%s, %s, %s)
            """,
            (email, otp_hash, expires_at),
        )

        send_otp_email(email, otp, OTP_TTL_MINUTES)

    # Deliberately no branching in the response path below this point.
    # Caller always returns a generic "if this email is authorized..." message.


def verify_otp(email: str, otp: str) -> str:
    """
    Verify an OTP and, on success, return a signed session token.
    Raises HTTPException(401) on any failure, with a generic message.
    """
    email = email.strip().lower()
    otp = otp.strip()
    generic_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired code.",
    )

    otp_hash = _hash_otp(otp, email)

    row = db.fetch_one(
        """
        SELECT id FROM otp_codes
        WHERE email = %s
          AND otp_hash = %s
          AND used = FALSE
          AND expires_at > NOW()
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (email, otp_hash),
    )

    if row is None:
        raise generic_error

    if not _is_authorized_admin(email):
        raise generic_error

    db.execute("UPDATE otp_codes SET used = TRUE WHERE id = %s", (row["id"],))

    return _issue_session_token(email)


def _issue_session_token(email: str) -> str:
    payload = {
        "sub": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, SESSION_SECRET, algorithm=JWT_ALGORITHM)


def _decode_session_token(token: str) -> str:
    """Decode a session token and return the admin email. Raises on failure."""
    try:
        payload = jwt.decode(token, SESSION_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
        )
    return payload["sub"]


def require_admin(authorization: str = Header(default="")) -> str:
    """
    FastAPI dependency that protects CMS endpoints.
    Expects header: Authorization: Bearer <session_token>
    Returns the authenticated admin's email.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
        )

    token = authorization.removeprefix("Bearer ").strip()
    email = _decode_session_token(token)

    if not _is_authorized_admin(email):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
        )

    return email
