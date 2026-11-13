"""Security controls: API auth, CF Access JWT, HMAC approval gate, injection defense."""

import hashlib
import hmac
import html
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import ApprovalRequest

logger = get_logger(__name__)

_TAG_BOUNDARY_RE = re.compile(r"<\s*(/?)\s*(untrusted_external_data)\b([^>]*)>", re.IGNORECASE)


class ApprovalRequiredError(Exception):
    """Raised when an irreversible action lacks valid, unused Human-in-the-loop approval."""

    def __init__(
        self,
        message: str,
        *,
        approval_id: str | None = None,
        action_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.approval_id = approval_id
        self.action_type = action_type


def wrap_untrusted_data(source: str, content: str) -> str:
    """
    Wrap external/untrusted content inside isolated XML-like delimiter tags.

    Any attempt inside `content` to inject or prematurely close
    `<untrusted_external_data>` / `</untrusted_external_data>` is escaped.
    """
    safe_source = html.escape(source.strip() or "unknown", quote=True)

    def _neutralize_tag(match: re.Match[str]) -> str:
        slash = match.group(1) or ""
        tag_name = match.group(2)
        attrs = match.group(3) or ""
        return f"&lt;{slash}{tag_name}{html.escape(attrs, quote=False)}&gt;"

    sanitized_content = _TAG_BOUNDARY_RE.sub(_neutralize_tag, content)
    return (
        f'<untrusted_external_data source="{safe_source}">\n'
        f"{sanitized_content}\n"
        f"</untrusted_external_data>"
    )


def _verify_cloudflare_access_jwt(token: str, settings: Settings) -> dict[str, Any]:
    """Verify Cloudflare Access JWT using team JWKS endpoint."""
    if not settings.CF_ACCESS_TEAM_DOMAIN or not settings.CF_ACCESS_AUD:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cloudflare Access is misconfigured",
        )

    certs_url = f"https://{settings.CF_ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs"
    try:
        jwk_client = jwt.PyJWKClient(certs_url)
        signing_key = jwk_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.CF_ACCESS_AUD,
        )
        return claims
    except Exception as exc:
        logger.warning("Cloudflare Access JWT verification failed", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Cloudflare Access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def verify_api_auth(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """
    Enforce Bearer token or Cloudflare Access JWT on all /api/* endpoints.

    Raises HTTP 401 on missing or invalid credentials.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        provided_token = auth_header[len("Bearer ") :].strip()
        if provided_token and hmac.compare_digest(provided_token, settings.API_SECRET_TOKEN):
            return {"auth_method": "bearer"}

    if settings.CF_ACCESS_ENABLED:
        cf_jwt = request.headers.get("Cf-Access-Jwt-Assertion", "").strip()
        if cf_jwt:
            claims = _verify_cloudflare_access_jwt(cf_jwt, settings)
            return {"auth_method": "cf_access", "sub": claims.get("sub")}

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
        headers={"WWW-Authenticate": "Bearer"},
    )


class ApprovalService:
    """Physical execution lock using HMAC-SHA256 signed one-time approval tokens."""

    def __init__(self, hmac_secret: str, default_ttl_seconds: int = 600) -> None:
        if len(hmac_secret) < 16:
            raise ValueError("APPROVAL_HMAC_SECRET must be at least 16 characters")
        self._secret = hmac_secret.encode("utf-8")
        self._default_ttl = default_ttl_seconds

    @staticmethod
    def hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    def _sign_payload(self, approval_id: str, action_type: str, nonce: str, exp_ts: int) -> str:
        message = f"{approval_id}:{action_type}:{nonce}:{exp_ts}".encode("utf-8")
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def issue_token(self, approval_id: str, action_type: str, expires_at: datetime) -> str:
        """Generate a signed one-time token bound to approval_id, action_type, and expiry."""
        nonce = secrets.token_urlsafe(24)
        exp_ts = int(expires_at.timestamp())
        sig = self._sign_payload(approval_id, action_type, nonce, exp_ts)
        return f"{nonce}.{exp_ts}.{sig}"

    def verify_token_signature(
        self,
        raw_token: str,
        *,
        approval_id: str,
        action_type: str,
        expected_hash: str,
        now: datetime | None = None,
    ) -> None:
        """Verify HMAC signature, expiration timestamp, and stored SHA-256 digest."""
        if not raw_token or "." not in raw_token:
            raise ApprovalRequiredError(
                "Malformed approval token",
                approval_id=approval_id,
                action_type=action_type,
            )

        parts = raw_token.split(".")
        if len(parts) != 3:
            raise ApprovalRequiredError(
                "Invalid approval token format",
                approval_id=approval_id,
                action_type=action_type,
            )

        nonce, exp_str, provided_sig = parts
        try:
            exp_ts = int(exp_str)
        except ValueError as exc:
            raise ApprovalRequiredError(
                "Invalid approval token expiry",
                approval_id=approval_id,
                action_type=action_type,
            ) from exc

        current_time = now or datetime.now(timezone.utc)
        if int(current_time.timestamp()) > exp_ts:
            raise ApprovalRequiredError(
                "Approval token has expired",
                approval_id=approval_id,
                action_type=action_type,
            )

        expected_sig = self._sign_payload(approval_id, action_type, nonce, exp_ts)
        if not hmac.compare_digest(provided_sig, expected_sig):
            raise ApprovalRequiredError(
                "Approval token signature mismatch",
                approval_id=approval_id,
                action_type=action_type,
            )

        computed_hash = self.hash_token(raw_token)
        if not hmac.compare_digest(computed_hash, expected_hash):
            raise ApprovalRequiredError(
                "Approval token digest mismatch",
                approval_id=approval_id,
                action_type=action_type,
            )

    async def create_request(
        self,
        db: AsyncSession,
        *,
        action_type: str,
        payload: dict[str, Any],
        job_id: uuid.UUID | None = None,
        ttl_seconds: int | None = None,
    ) -> tuple[ApprovalRequest, str]:
        """Persist a pending ApprovalRequest and return (record, raw_one_time_token)."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl)
        approval_id = uuid.uuid4()

        raw_token = self.issue_token(str(approval_id), action_type, expires_at)
        token_hash = self.hash_token(raw_token)

        record = ApprovalRequest(
            id=approval_id,
            job_id=job_id,
            action_type=action_type,
            payload=payload,
            token_hash=token_hash,
            status="pending",
            expires_at=expires_at,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        return record, raw_token

    async def resolve_request(
        self,
        db: AsyncSession,
        *,
        approval_id: uuid.UUID,
        decision: Literal["approved", "rejected"],
        raw_token: str,
        now: datetime | None = None,
    ) -> ApprovalRequest:
        """
        Validate one-time token and resolve a pending ApprovalRequest.

        Blocks replay attacks, expired tokens, and tampered tokens.
        """
        result = await db.execute(
            select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise ApprovalRequiredError(
                "Approval request not found",
                approval_id=str(approval_id),
            )

        if record.status != "pending":
            raise ApprovalRequiredError(
                f"Approval request already resolved with status '{record.status}'",
                approval_id=str(approval_id),
                action_type=record.action_type,
            )

        current_time = now or datetime.now(timezone.utc)
        record_expires = record.expires_at
        if record_expires.tzinfo is None:
            record_expires = record_expires.replace(tzinfo=timezone.utc)

        if current_time > record_expires:
            record.status = "expired"
            record.resolved_at = current_time
            await db.commit()
            raise ApprovalRequiredError(
                "Approval request has expired",
                approval_id=str(approval_id),
                action_type=record.action_type,
            )

        self.verify_token_signature(
            raw_token,
            approval_id=str(record.id),
            action_type=record.action_type,
            expected_hash=record.token_hash,
            now=current_time,
        )

        record.status = decision
        record.resolved_at = current_time
        await db.commit()
        await db.refresh(record)
        return record

    async def assert_approved_for_execution(
        self,
        db: AsyncSession,
        *,
        approval_id: uuid.UUID,
        action_type: str,
        consume: bool = True,
    ) -> ApprovalRequest:
        """
        Physical gate checked immediately prior to executing any irreversible action.

        Raises ApprovalRequiredError unless status is 'approved' and unconsumed.
        """
        result = await db.execute(
            select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise ApprovalRequiredError(
                "Approval request not found; execution blocked",
                approval_id=str(approval_id),
                action_type=action_type,
            )

        if record.action_type != action_type:
            raise ApprovalRequiredError(
                "Approval action_type mismatch; execution blocked",
                approval_id=str(approval_id),
                action_type=action_type,
            )

        if record.status != "approved":
            raise ApprovalRequiredError(
                f"Action requires approved status (current: '{record.status}')",
                approval_id=str(approval_id),
                action_type=action_type,
            )

        if record.consumed_at is not None:
            raise ApprovalRequiredError(
                "Approval has already been consumed; replay blocked",
                approval_id=str(approval_id),
                action_type=action_type,
            )

        if consume:
            record.consumed_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(record)

        return record

