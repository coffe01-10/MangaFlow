from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ProviderKey
from app.services.provider_errors import MAX_RETRY_AFTER_SECONDS

_AAD = b"mangaflow-provider-key-v1"
_LOCAL_MASTER_KEY_FILENAME = ".provider-credential-master-key"


def _load_or_create_local_master_key(settings: Settings) -> str:
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    path = settings.storage_root / _LOCAL_MASTER_KEY_FILENAME
    try:
        return path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        generated = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return path.read_text(encoding="ascii").strip()
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(generated)
        return generated


def _decode_master_key(settings: Settings) -> bytes:
    raw = settings.mangaflow_credential_master_key
    if not raw:
        if settings.environment.lower() != "development":
            raise HTTPException(
                status_code=503,
                detail="服务端尚未配置 MANGAFLOW_CREDENTIAL_MASTER_KEY，无法保存 API Key",
            )
        raw = _load_or_create_local_master_key(settings)
    try:
        key = base64.urlsafe_b64decode(raw.encode("ascii"))
    except Exception as error:
        raise HTTPException(
            status_code=503, detail="供应商凭据主密钥格式无效"
        ) from error
    if len(key) != 32:
        raise HTTPException(
            status_code=503, detail="供应商凭据主密钥必须解码为 32 字节"
        )
    return key


def encrypt_secret(settings: Settings, secret: str) -> str:
    if not secret.strip():
        raise ValueError("API Key 不能为空")
    nonce = os.urandom(12)
    encrypted = AESGCM(_decode_master_key(settings)).encrypt(
        nonce, secret.encode("utf-8"), _AAD
    )
    payload = base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")
    return f"v1.{payload}"


def decrypt_secret(settings: Settings, token: str) -> str:
    version, separator, payload = token.partition(".")
    if separator != "." or version != "v1":
        raise RuntimeError("不支持的供应商凭据版本")
    try:
        raw = base64.urlsafe_b64decode(payload.encode("ascii"))
        value = AESGCM(_decode_master_key(settings)).decrypt(raw[:12], raw[12:], _AAD)
    except HTTPException:
        raise
    except Exception as error:
        raise RuntimeError("供应商凭据无法解密") from error
    return value.decode("utf-8")


def secret_hint(secret: str) -> str:
    stripped = secret.strip()
    if len(stripped) <= 4:
        return "••••"
    return f"••••{stripped[-4:]}"


@dataclass(frozen=True)
class SelectedProviderKey:
    row: ProviderKey
    secret: str


def select_provider_key(
    db: Session, settings: Settings, connection_id: str
) -> SelectedProviderKey:
    now = datetime.now(UTC)
    key = db.scalar(
        select(ProviderKey)
        .where(
            ProviderKey.connection_id == connection_id,
            ProviderKey.enabled.is_(True),
            or_(ProviderKey.cooldown_until.is_(None), ProviderKey.cooldown_until <= now),
        )
        .order_by(ProviderKey.last_used_at.is_not(None), ProviderKey.last_used_at, ProviderKey.id)
    )
    if key is None:
        raise HTTPException(status_code=409, detail="供应商连接没有可用的 API Key")
    secret = decrypt_secret(settings, key.encrypted_secret)
    key.last_used_at = now
    db.commit()
    return SelectedProviderKey(row=key, secret=secret)


def mark_key_failure(
    db: Session,
    key: ProviderKey,
    error_code: str,
    *,
    retry_after_seconds: int | None = None,
    degrade_only: bool = False,
) -> None:
    """Record a key failure; ``degrade_only`` for diagnostic surfaces.

    Read-only diagnostics (balance queries) reuse the generation key but
    their failures say nothing about the generation surface: a gateway may
    401/403 its billing endpoint for a perfectly valid generation key.
    Diagnostic failures must therefore never disable the key or start a
    cooldown — they only mark it DEGRADED with the error code.
    """

    key.last_error_code = error_code
    if degrade_only:
        key.health_state = "DEGRADED"
    elif error_code in {"AUTHENTICATION", "PERMISSION"}:
        key.health_state = "DENIED"
        key.enabled = False
    elif error_code == "RATE_LIMIT" and not degrade_only:
        key.health_state = "COOLDOWN"
        # Second clamp next to the arithmetic: parse sites may be added
        # elsewhere and a garbage provider hint must never overflow the
        # datetime arithmetic or outlive a sane cooldown window.
        cooldown = min(max(1, retry_after_seconds or 60), MAX_RETRY_AFTER_SECONDS)
        key.cooldown_until = datetime.now(UTC) + timedelta(seconds=cooldown)
    else:
        key.health_state = "DEGRADED"
    db.commit()


def mark_key_success(db: Session, key: ProviderKey) -> None:
    key.health_state = "HEALTHY"
    key.cooldown_until = None
    key.last_error_code = None
    db.commit()
