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

_AAD = b"mangaflow-provider-key-v1"


def _decode_master_key(settings: Settings) -> bytes:
    raw = settings.mangaflow_credential_master_key
    if not raw:
        raise HTTPException(
            status_code=503,
            detail="服务端尚未配置 MANGAFLOW_CREDENTIAL_MASTER_KEY，无法保存 API Key",
        )
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
) -> None:
    key.last_error_code = error_code
    if error_code in {"AUTHENTICATION", "PERMISSION"}:
        key.health_state = "DENIED"
        key.enabled = False
    elif error_code == "RATE_LIMIT":
        key.health_state = "COOLDOWN"
        key.cooldown_until = datetime.now(UTC) + timedelta(
            seconds=max(1, retry_after_seconds or 60)
        )
    else:
        key.health_state = "DEGRADED"
    db.commit()


def mark_key_success(db: Session, key: ProviderKey) -> None:
    key.health_state = "HEALTHY"
    key.cooldown_until = None
    key.last_error_code = None
    db.commit()
