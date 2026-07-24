"""
Green Recruiters - Storage Layer
Handles resume/CV files on local disk (VPS filesystem).
Resumes are NEVER stored in PostgreSQL - only their storage key/filename/size.
"""

import os
import re
import uuid
from pathlib import Path
from typing import BinaryIO

STORAGE_ROOT = Path(os.environ.get("RESUME_STORAGE_PATH", "/var/greenrecruiters/resumes"))

ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx"}
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB

STORAGE_ROOT.mkdir(parents=True, exist_ok=True)


class InvalidFileError(Exception):
    """Raised when an uploaded file fails validation."""


def _safe_extension(original_filename: str) -> str:
    """Return the lowercase extension if it is on the allow-list, else raise."""
    ext = Path(original_filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise InvalidFileError(
            f"File type '{ext}' is not allowed. Accepted formats: PDF, DOC, DOCX."
        )
    return ext


def validate_upload(original_filename: str, content_type: str, size_bytes: int) -> str:
    """
    Validate an incoming upload before it is saved.
    Returns the validated file extension. Raises InvalidFileError otherwise.
    """
    if size_bytes > MAX_FILE_SIZE_BYTES:
        raise InvalidFileError("File exceeds the maximum size of 5 MB.")

    if content_type not in ALLOWED_CONTENT_TYPES:
        raise InvalidFileError(
            "File content type is not allowed. Accepted formats: PDF, DOC, DOCX."
        )

    return _safe_extension(original_filename)


def save_resume(file_obj: BinaryIO, original_filename: str, content_type: str) -> dict:
    """
    Persist an uploaded resume to disk under a generated storage key.
    Returns a dict with keys: storage_key, original_filename, size_bytes.
    """
    file_obj.seek(0, os.SEEK_END)
    size_bytes = file_obj.tell()
    file_obj.seek(0)

    ext = validate_upload(original_filename, content_type, size_bytes)

    storage_key = f"{uuid.uuid4().hex}{ext}"
    destination = STORAGE_ROOT / storage_key

    with open(destination, "wb") as out_file:
        out_file.write(file_obj.read())

    return {
        "storage_key": storage_key,
        "original_filename": original_filename,
        "size_bytes": size_bytes,
    }


def resolve_path(storage_key: str) -> Path:
    """
    Resolve a storage key to an absolute path, guarding against
    path traversal. Raises FileNotFoundError if the key is invalid
    or the file does not exist.
    """
    if not re.fullmatch(r"[a-f0-9]{32}\.(pdf|doc|docx)", storage_key):
        raise FileNotFoundError("Invalid storage key.")

    path = (STORAGE_ROOT / storage_key).resolve()

    if STORAGE_ROOT.resolve() not in path.parents:
        raise FileNotFoundError("Invalid storage key.")

    if not path.is_file():
        raise FileNotFoundError("Resume file not found.")

    return path


def delete_resume(storage_key: str) -> None:
    """Delete a resume file from disk. Silently ignores missing files."""
    try:
        path = resolve_path(storage_key)
        path.unlink()
    except FileNotFoundError:
        pass
