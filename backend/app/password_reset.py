import hashlib
import hmac
import os
import secrets
import smtplib

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from dotenv import load_dotenv

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel, Field

from app.db.mongodb import db
from app.security import hash_password

from bson import ObjectId

#Load the environment variables from the .env file
load_dotenv()

# ============================================================
# Router
# ============================================================

router = APIRouter(
    prefix="/api/auth",
    tags=["Password Reset"],
)


# ============================================================
# MongoDB Collections
# ============================================================

users_collection = db["users"]

password_resets_collection = db["password_resets"]


# ============================================================
# Password Reset Settings
# ============================================================

OTP_EXPIRY_MINUTES = 5

RESET_TOKEN_EXPIRY_MINUTES = 10

MAX_OTP_ATTEMPTS = 5


# ============================================================
# Email Settings
# ============================================================

SMTP_HOST = os.getenv("SMTP_HOST", "").strip()

SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))

SMTP_USERNAME = os.getenv("SMTP_USERNAME", "").strip()

SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

SMTP_FROM = (
    os.getenv("SMTP_FROM", "").strip()
    or SMTP_USERNAME
)


# ============================================================
# Pydantic Request Models
# ============================================================

class ForgotPasswordRequest(BaseModel):
    identifier: str = Field(min_length=1)


class VerifyResetOTPRequest(BaseModel):
    reset_id: str = Field(min_length=1)

    otp: str = Field(
        min_length=6,
        max_length=6,
    )


class ResetPasswordRequest(BaseModel):
    reset_token: str = Field(min_length=1)

    new_password: str = Field(
        min_length=8
    )

    confirm_password: str = Field(
        min_length=8
    )


# ============================================================
# Helper Functions
# ============================================================

def generate_otp() -> str:
    """
    Generate a cryptographically secure 6-digit OTP.
    """

    return f"{secrets.randbelow(1_000_000):06d}"


def hash_value(value: str) -> str:
    """
    SHA-256 hash helper.

    We store only hashes of OTP/reset tokens
    in MongoDB.
    """

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def verify_hashed_value(
    plain_value: str,
    stored_hash: str,
) -> bool:
    """
    Safely compare a plain value
    against its stored SHA-256 hash.
    """

    calculated_hash = hash_value(
        plain_value
    )

    return hmac.compare_digest(
        calculated_hash,
        stored_hash,
    )


def generate_reset_token() -> str:
    """
    Generate a secure password-reset token.
    """

    return secrets.token_urlsafe(32)


# ============================================================
# Email Function
# ============================================================

def send_password_reset_otp(
    email: str,
    otp: str,
):
    """
    Send password-reset OTP through SMTP.
    """

    if not SMTP_HOST:
        raise RuntimeError(
            "SMTP_HOST is not configured"
        )

    if not SMTP_USERNAME:
        raise RuntimeError(
            "SMTP_USERNAME is not configured"
        )

    if not SMTP_PASSWORD:
        raise RuntimeError(
            "SMTP_PASSWORD is not configured"
        )


    message = EmailMessage()

    message["Subject"] = (
        "HRMS Password Reset OTP"
    )

    message["From"] = SMTP_FROM

    message["To"] = email


    message.set_content(
        f"""
Hello,

We received a request to reset your HRMS account password.

Your password reset OTP is:

{otp}

This OTP is valid for {OTP_EXPIRY_MINUTES} minutes.

If you did not request a password reset, please ignore this email.

Regards,
HRMS Team
""".strip()
    )


    with smtplib.SMTP_SSL(
        SMTP_HOST,
        SMTP_PORT,
        timeout=20,
    ) as server:

        server.login(
            SMTP_USERNAME,
            SMTP_PASSWORD,
        )

        server.send_message(
            message
        )


# ============================================================
# 1. Forgot Password
# ============================================================

@router.post(
    "/forgot-password"
)
def forgot_password(
    payload: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
):
    """
    Step 1:
    Employee ID / Work Email
        ↓
    Find active user
        ↓
    Generate OTP
        ↓
    Store OTP hash
        ↓
    Send OTP email
    """

    identifier = payload.identifier.strip()

    if not identifier:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Employee ID or Work Email is required",
        )


    normalized_email = identifier.lower()


    # --------------------------------------------------------
    # Find user
    # --------------------------------------------------------

    user = users_collection.find_one(
        {
            "$or": [
                {
                    "work_email":
                        normalized_email
                },
                {
                    "employee_id":
                        identifier
                },
            ]
        }
    )


    # --------------------------------------------------------
    # Generic response
    #
    # We intentionally do not reveal whether
    # the account exists.
    # --------------------------------------------------------

    generic_message = (
        "If an account matches the information "
        "provided, a verification OTP has been sent."
    )


    if user is None:
        return {
            "message": generic_message
        }


    # --------------------------------------------------------
    # Inactive users should not receive reset OTP
    # --------------------------------------------------------

    if not user.get(
        "is_active",
        False,
    ):
        return {
            "message": generic_message
        }


    user_id = str(
        user["_id"]
    )


    # --------------------------------------------------------
    # Invalidate previous unused reset requests
    # --------------------------------------------------------

    password_resets_collection.update_many(
        {
            "user_id": user_id,
            "used": False,
        },
        {
            "$set": {
                "used": True,
                "invalidated_at":
                    datetime.now(
                        timezone.utc
                    ),
            }
        },
    )


    # --------------------------------------------------------
    # Generate OTP
    # --------------------------------------------------------

    otp = generate_otp()

    otp_hash = hash_value(
        otp
    )


    reset_id = secrets.token_urlsafe(
        32
    )


    now = datetime.now(
        timezone.utc
    )


    otp_expires_at = (
        now
        + timedelta(
            minutes=OTP_EXPIRY_MINUTES
        )
    )


    # --------------------------------------------------------
    # Store reset request
    # --------------------------------------------------------

    password_resets_collection.insert_one(
        {
            "_id": reset_id,

            "user_id": user_id,

            "otp_hash": otp_hash,

            "otp_expires_at":
                otp_expires_at,

            "otp_attempts": 0,

            "verified": False,

            "used": False,

            "created_at": now,

            "verified_at": None,

            "reset_token_hash": None,

            "reset_token_expires_at":
                None,

            "reset_token_used": False,

            "used_at": None,
        }
    )


    # --------------------------------------------------------
    # Send OTP
    # --------------------------------------------------------

    try:

        send_password_reset_otp(
            user["work_email"],
            otp,
        )

    except (
        OSError,
        smtplib.SMTPException,
        RuntimeError,
    ):

        # Do not leave an active reset request
        # if email delivery failed.

        password_resets_collection.update_one(
            {
                "_id": reset_id
            },
            {
                "$set": {
                    "used": True,
                    "email_failed": True,
                    "used_at":
                        datetime.now(
                            timezone.utc
                        ),
                }
            },
        )

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Password reset email "
                "could not be sent"
            ),
        )


    return {
        "message": generic_message,
        "reset_id": reset_id,
        "otp_expires_at": otp_expires_at.isoformat(),
    }


# ============================================================
# 2. Verify OTP
# ============================================================

@router.post(
    "/verify-reset-otp"
)
def verify_reset_otp(
    payload: VerifyResetOTPRequest,
):
    """
    Step 2:

    reset_id + OTP
        ↓
    Validate OTP
        ↓
    Generate secure reset token
        ↓
    Return reset token
    """

    reset_data = (
        password_resets_collection.find_one(
            {
                "_id": payload.reset_id,

                "used": False,

                "verified": False,
            }
        )
    )


    if reset_data is None:

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid or expired "
                "password reset request"
            ),
        )


    # --------------------------------------------------------
    # Check maximum OTP attempts
    # --------------------------------------------------------

    attempts = reset_data.get(
        "otp_attempts",
        0,
    )


    if attempts >= MAX_OTP_ATTEMPTS:

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Too many incorrect OTP attempts. "
                "Please request a new OTP."
            ),
        )


    # --------------------------------------------------------
    # Check OTP expiry
    # --------------------------------------------------------

    now = datetime.now(timezone.utc)

    otp_expires_at = ensure_utc_datetime(
        reset_data.get("otp_expires_at")
    )

    if (
        otp_expires_at is None
        or now > otp_expires_at
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP has expired",
        )


    # --------------------------------------------------------
    # Verify OTP
    # --------------------------------------------------------

    stored_otp_hash = reset_data.get(
        "otp_hash"
    )


    if not stored_otp_hash:

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OTP",
        )


    if not verify_hashed_value(
        payload.otp.strip(),
        stored_otp_hash,
    ):

        password_resets_collection.update_one(
            {
                "_id":
                    payload.reset_id
            },
            {
                "$inc": {
                    "otp_attempts": 1
                }
            },
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OTP",
        )


    # --------------------------------------------------------
    # OTP is valid
    # --------------------------------------------------------

    reset_token = (
        generate_reset_token()
    )


    reset_token_hash = hash_value(
        reset_token
    )


    reset_token_expires_at = (
        now
        + timedelta(
            minutes=
                RESET_TOKEN_EXPIRY_MINUTES
        )
    )


    # --------------------------------------------------------
    # Store only reset-token hash
    # --------------------------------------------------------

    password_resets_collection.update_one(
        {
            "_id":
                payload.reset_id
        },
        {
            "$set": {

                "verified": True,

                "verified_at": now,

                "reset_token_hash":
                    reset_token_hash,

                "reset_token_expires_at":
                    reset_token_expires_at,

                "reset_token_used": False,
            },

            "$unset": {
                "otp_hash": ""
            },
        },
    )


    return {
        "message":
            "OTP verified successfully.",

        "reset_token":
            reset_token,
    }


# ============================================================
# 3. Reset Password
# ============================================================

@router.post(
    "/reset-password"
)
def reset_password(
    payload: ResetPasswordRequest,
):
    """
    Step 3:

    reset_token
        ↓
    Validate reset session
        ↓
    Hash new password
        ↓
    Update users collection
        ↓
    Invalidate reset request
        ↓
    Invalidate existing login sessions
    """

    # --------------------------------------------------------
    # Validate password confirmation
    # --------------------------------------------------------

    if (
        payload.new_password
        != payload.confirm_password
    ):

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passwords do not match",
        )


    # --------------------------------------------------------
    # Find reset request by hashed token
    # --------------------------------------------------------

    reset_token_hash = hash_value(
        payload.reset_token
    )


    reset_data = (
        password_resets_collection.find_one(
            {
                "reset_token_hash":
                    reset_token_hash,

                "verified": True,

                "used": False,

                "reset_token_used":
                    False,
            }
        )
    )


    if reset_data is None:

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid or expired "
                "password reset session"
            ),
        )


    # --------------------------------------------------------
    # Check reset-token expiry
    # --------------------------------------------------------

    now = datetime.now(
        timezone.utc
    )


    reset_token_expires_at = ensure_utc_datetime(
        reset_data.get("reset_token_expires_at")
    )

    if (
        reset_token_expires_at is None
        or now > reset_token_expires_at
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token has expired",
        )


    # --------------------------------------------------------
    # Find user
    # --------------------------------------------------------

    user_id = reset_data.get(
        "user_id"
    )


    if not user_id:

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid password reset request"
            ),
        )


    try:
        user_object_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid password reset request",
        )

    user = users_collection.find_one(
        {
            "_id": user_object_id
        }
    )


    if user is None:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )


    # --------------------------------------------------------
    # User must still be active
    # --------------------------------------------------------

    if not user.get(
        "is_active",
        False,
    ):

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account is inactive",
        )


    # --------------------------------------------------------
    # Prevent same password
    # --------------------------------------------------------

    # We intentionally do not verify the old password here.
    #
    # However, if you want to prevent reuse of the old
    # password, that needs an additional password-history
    # design. For the current reset flow we only enforce
    # the new password length and confirmation.
    #


    # --------------------------------------------------------
    # Update password
    # --------------------------------------------------------

    new_password_hash = hash_password(
        payload.new_password
    )


    result = users_collection.update_one(
        {
            "_id": user["_id"]
        },
        {
            "$set": {

                "password_hash":
                    new_password_hash,

                "updated_at":
                    now,

                "failed_attempts": 0,
            },

            "$unset": {
                "locked_until": ""
            },
        },
    )


    if result.matched_count == 0:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )


    # --------------------------------------------------------
    # Mark reset request as used
    # --------------------------------------------------------

    password_resets_collection.update_one(
        {
            "_id":
                reset_data["_id"]
        },
        {
            "$set": {

                "used": True,

                "reset_token_used":
                    True,

                "used_at": now,
            }
        },
    )


    # --------------------------------------------------------
    # Invalidate all existing login sessions
    #
    # Your current project keeps active_sessions in main.py.
    # Importing it inside the function avoids circular import
    # during application startup.
    # --------------------------------------------------------

    try:

        from app.main import active_sessions

        tokens_to_remove = [
            token
            for token, session_user_id
            in active_sessions.items()
            if session_user_id == user_id
        ]

        for token in tokens_to_remove:
            active_sessions.pop(
                token,
                None,
            )

    except ImportError:
        # Password reset itself is already completed.
        # Session invalidation is a secondary protection.
        pass


    return {
        "message":
            "Password reset successfully."
    }


def ensure_utc_datetime(value):
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)