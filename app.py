#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import io
import mimetypes
import os
import secrets
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import yaml


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = APP_DIR / "data"
ENV_FILE = Path(os.getenv("CPA_PANEL_ENV_FILE", str(APP_DIR / ".env"))).expanduser()
SESSION_COOKIE = "cpa_panel_session"


def load_env_file(path: Path) -> None:
    """从本地 .env 文件补充环境变量。"""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    candidate = candidate.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def isoformat_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def unix_ts_to_iso(value: Any) -> str | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def mapping_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def decode_jwt_payload(token: str | None) -> dict[str, Any]:
    candidate = str(token or "").strip()
    if not candidate:
        return {}
    parts = candidate.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        data = json.loads(decoded.decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def codex_account_id_from_claims(claims: dict[str, Any]) -> str:
    auth_info = mapping_or_empty(claims.get("https://api.openai.com/auth"))
    return str(auth_info.get("chatgpt_account_id") or claims.get("chatgpt_account_id") or "").strip()


def mask_email(value: str | None) -> str:
    if not value or "@" not in value:
        return value or "-"
    local, domain = value.split("@", 1)
    if len(local) <= 3:
        local_masked = local[0] + "*" * max(1, len(local) - 1)
    else:
        local_masked = local[:2] + "*" * (len(local) - 4) + local[-2:]
    return f"{local_masked}@{domain}"


def datetime_to_epoch_ms(value: datetime | None) -> int:
    if value is None:
        return 0
    return int(value.timestamp() * 1000)


def infer_status(source: str, disabled: bool, expires_at: datetime | None) -> str:
    if source == "deactivated":
        return "deactivated"
    if disabled:
        return "disabled"
    if expires_at is None:
        return "unknown"
    now = utc_now()
    if expires_at <= now:
        return "expired"
    if expires_at <= now + timedelta(days=3):
        return "expiring_soon"
    return "active"


STATUS_LABELS = {
    "active": "正常",
    "expiring_soon": "即将过期",
    "expired": "已过期",
    "disabled": "已禁用",
    "deactivated": "已迁移停用",
    "unknown": "未知",
}

STATUS_ORDER = {
    "expiring_soon": 0,
    "expired": 1,
    "disabled": 2,
    "deactivated": 3,
    "unknown": 4,
    "active": 5,
}

QUOTA_ERROR_FILTER_LABELS = {
    "usage_limit_reached": "额度耗尽",
    "token_expired": "Token 过期",
}

CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_USAGE_HEADERS = {
    "Authorization": "Bearer $TOKEN$",
    "Content-Type": "application/json",
    "User-Agent": "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal",
}
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_TOKEN_REFRESH_SCOPE = "openid profile email"
AUTO_RECOVER_SUCCESS_STREAK_REQUIRED = 2


def safe_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def safe_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    candidate = value.strip().lower()
    if not candidate:
        return default
    if candidate in {"1", "true", "yes", "y", "on"}:
        return True
    if candidate in {"0", "false", "no", "n", "off"}:
        return False
    return default


def normalize_base_url(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = f"http://{raw}"
    raw = raw.rstrip("/")
    if raw.endswith("/v0/management"):
        raw = raw[: -len("/v0/management")]
    return raw.rstrip("/")


@dataclass
class Settings:
    host: str
    port: int
    admin_token: str
    generated_token: bool
    session_ttl_minutes: int
    cache_seconds: int
    auth_dir: Path
    deactivated_dir: Path
    cliproxy_config_path: Path
    meta_path: Path
    session_store_path: Path
    management_base_url: str
    management_key: str
    management_timeout_seconds: int
    manual_disabled_refresh_enabled: bool
    manual_disabled_refresh_interval_seconds: int
    manual_disabled_refresh_batch_size: int
    manual_disabled_refresh_startup_delay_seconds: int

    @classmethod
    def load(cls) -> "Settings":
        load_env_file(ENV_FILE)
        generated_token = False
        admin_token = os.getenv("CPA_PANEL_TOKEN", "").strip()
        if not admin_token:
            admin_token = secrets.token_urlsafe(18)
            generated_token = True

        data_dir=Path(os.getenv("CPA_PANEL_DATA_DIR", str(DATA_DIR)))

        settings = cls(
            host=os.getenv("CPA_PANEL_HOST", "127.0.0.1"),
            port=safe_int(os.getenv("CPA_PANEL_PORT"), 18660),
            admin_token=admin_token,
            generated_token=generated_token,
            session_ttl_minutes=safe_int(os.getenv("CPA_PANEL_SESSION_TTL_MINUTES"), 420),
            cache_seconds=safe_int(os.getenv("CPA_PANEL_CACHE_SECONDS"), 20),
            auth_dir=Path(os.getenv("CPA_PANEL_AUTH_DIR", "/root/.cli-proxy-api")),
            deactivated_dir=Path(os.getenv("CPA_PANEL_DEACTIVATED_DIR", "/root/account-deactivated")),
            cliproxy_config_path=Path(os.getenv("CPA_PANEL_CLIPROXY_CONFIG", "/root/cliproxyapi/config.yaml")),
            meta_path=Path(os.getenv("CPA_PANEL_META_PATH", str(data_dir / "account_meta.json"))),
            session_store_path=Path(os.getenv("CPA_PANEL_SESSION_STORE_PATH", str(data_dir / "sessions.json"))),
            management_base_url=normalize_base_url(os.getenv("CPA_MANAGEMENT_BASE_URL", "http://127.0.0.1:18317")),
            management_key=os.getenv("CPA_MANAGEMENT_KEY", "").strip(),
            management_timeout_seconds=safe_int(os.getenv("CPA_MANAGEMENT_TIMEOUT_SECONDS"), 12),
            manual_disabled_refresh_enabled=safe_bool(
                os.getenv("CPA_PANEL_MANUAL_DISABLED_REFRESH_ENABLED"),
                False,
            ),
            manual_disabled_refresh_interval_seconds=max(
                safe_int(os.getenv("CPA_PANEL_MANUAL_DISABLED_REFRESH_INTERVAL_SECONDS"), 600),
                60,
            ),
            manual_disabled_refresh_batch_size=max(
                safe_int(os.getenv("CPA_PANEL_MANUAL_DISABLED_REFRESH_BATCH_SIZE"), 50),
                0,
            ),
            manual_disabled_refresh_startup_delay_seconds=max(
                safe_int(os.getenv("CPA_PANEL_MANUAL_DISABLED_REFRESH_STARTUP_DELAY_SECONDS"), 45),
                0,
            ),
        )
        data_dir.mkdir(parents=True, exist_ok=True)
        settings.meta_path.parent.mkdir(parents=True, exist_ok=True)
        settings.session_store_path.parent.mkdir(parents=True, exist_ok=True)
        return settings


class ManagementClient:
    """对接 CLIProxyAPI Management API。"""

    def __init__(self, base_url: str, management_key: str, timeout_seconds: int) -> None:
        self.base_url = normalize_base_url(base_url)
        self.management_key = management_key.strip()
        self.timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.management_key)

    @property
    def management_root(self) -> str:
        return f"{self.base_url}/v0/management"

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("Management API 未配置")

        body = None
        headers = {
            "Authorization": f"Bearer {self.management_key}",
            "Accept": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(f"{self.management_root}{path}", data=body, headers=headers, method=method.upper())
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8", "ignore")
                if not raw:
                    return {}
                return json.loads(raw)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")
            raise RuntimeError(f"Management API {path} 返回 {exc.code}: {detail or exc.reason}") from exc
        except URLError as exc:
            raise RuntimeError(f"Management API 连接失败: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Management API {path} 返回了不可解析的 JSON") from exc

    def snapshot(self) -> dict[str, Any]:
        auth_status = self.request("GET", "/get-auth-status")
        auth_files = self.request("GET", "/auth-files")
        config = self.request("GET", "/config")

        usage: dict[str, Any] = {}
        latest_version_value = None
        warnings: list[str] = []

        try:
            usage = self.request("GET", "/usage")
        except RuntimeError as exc:
            warnings.append(str(exc))

        try:
            latest_version = self.request("GET", "/latest-version")
            latest_version_value = latest_version.get("latest-version") or latest_version.get("latest_version")
        except RuntimeError as exc:
            warnings.append(str(exc))

        return {
            "connected": True,
            "base_url": self.base_url,
            "auth_status": auth_status.get("status"),
            "auth_files": auth_files.get("files") or [],
            "config": config,
            "usage": usage,
            "latest_version": latest_version_value,
            "warnings": warnings,
        }

    def delete_auth_files(self, names: list[str]) -> dict[str, Any]:
        unique_names = []
        seen = set()
        for name in names:
            candidate = str(name or "").strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            unique_names.append(candidate)
        if not unique_names:
            return {"status": "ok", "deleted": 0, "files": [], "failed": []}
        return self.request("DELETE", "/auth-files", {"names": unique_names})

    def set_auth_file_status(self, name: str, disabled: bool) -> dict[str, Any]:
        candidate = str(name or "").strip()
        if not candidate:
            raise RuntimeError("缺少账号文件名，无法切换状态")
        return self.request("PATCH", "/auth-files/status", {"name": candidate, "disabled": bool(disabled)})

    def api_call(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/api-call", payload)


class SessionStore:
    """简单的内存会话存储。"""

    def __init__(self, ttl_minutes: int, store_path: Path) -> None:
        self.ttl = timedelta(minutes=ttl_minutes)
        self.store_path = store_path
        self._lock = threading.Lock()
        self._sessions: dict[str, datetime] = {}
        self._load()

    def _prune_locked(self) -> None:
        now = utc_now()
        expired = [sid for sid, expires_at in self._sessions.items() if expires_at <= now]
        for sid in expired:
            self._sessions.pop(sid, None)

    def _load(self) -> None:
        if not self.store_path.exists():
            return
        try:
            payload = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        raw_sessions = payload.get("sessions") if isinstance(payload, dict) else {}
        if not isinstance(raw_sessions, dict):
            return
        loaded: dict[str, datetime] = {}
        for sid, value in raw_sessions.items():
            candidate = parse_datetime(str(value or ""))
            if candidate is not None and candidate > utc_now():
                loaded[str(sid)] = candidate
        with self._lock:
            self._sessions = loaded
            self._prune_locked()
            self._save_locked()

    def _save_locked(self) -> None:
        payload = {
            "sessions": {sid: expires_at.isoformat() for sid, expires_at in self._sessions.items()},
        }
        temp_path = self.store_path.with_suffix(".tmp")
        try:
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self.store_path)
        except OSError:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def create(self) -> str:
        sid = secrets.token_urlsafe(24)
        with self._lock:
            self._prune_locked()
            self._sessions[sid] = utc_now() + self.ttl
            self._save_locked()
        return sid

    def valid(self, sid: str | None) -> bool:
        if not sid:
            return False
        with self._lock:
            self._prune_locked()
            expires_at = self._sessions.get(sid)
            if not expires_at:
                return False
            self._sessions[sid] = utc_now() + self.ttl
            self._save_locked()
            return True

    def revoke(self, sid: str | None) -> None:
        if not sid:
            return
        with self._lock:
            self._sessions.pop(sid, None)
            self._save_locked()


class AccountRepository:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.management = ManagementClient(
            settings.management_base_url,
            settings.management_key,
            settings.management_timeout_seconds,
        )
        self._lock = threading.Lock()
        self._cache: dict[str, Any] = {
            "updated_at": 0.0,
            "accounts": [],
            "summary": {},
            "config": {},
            "management": {},
        }
        self._quota_cache: dict[str, dict[str, Any]] = {}
        self._manual_disabled_refresh_lock = threading.Lock()
        self._disabled_unrefreshed_export_lock = threading.Lock()

    def _load_meta(self) -> dict[str, Any]:
        if not self.settings.meta_path.exists():
            return {"accounts": {}}
        try:
            return json.loads(self.settings.meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"accounts": {}}

    def _save_meta(self, payload: dict[str, Any]) -> None:
        self.settings.meta_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _meta_account_key(account: dict[str, Any]) -> str:
        for candidate in (
            account.get("key"),
            account.get("account_id"),
            account.get("email"),
        ):
            normalized = str(candidate or "").strip()
            if normalized:
                return normalized
        source_file = str(account.get("source_file") or "").strip()
        if source_file:
            return Path(source_file).stem
        return ""

    def _set_manual_disabled_marker(self, account: dict[str, Any], disabled: bool) -> None:
        meta_key = self._meta_account_key(account)
        if not meta_key:
            return

        meta_root = self._load_meta()
        accounts = meta_root.setdefault("accounts", {})
        current = dict(accounts.get(meta_key) or {})
        now_iso = utc_now().isoformat()

        if disabled:
            current["manual_disabled"] = True
            current["manual_disabled_at"] = now_iso
            current["auto_recover_success_streak"] = 0
            current["updated_at"] = now_iso
            accounts[meta_key] = current
            account["manual_disabled"] = True
            account["manual_disabled_at"] = now_iso
            account["auto_recover_success_streak"] = 0
            self._save_meta(meta_root)
            return

        current.pop("manual_disabled", None)
        current.pop("manual_disabled_at", None)
        current.pop("auto_recover_success_streak", None)
        account["manual_disabled"] = False
        account["manual_disabled_at"] = None
        account["auto_recover_success_streak"] = 0

        has_business_meta = any(
            (
                str(current.get("group", "")).strip(),
                str(current.get("owner", "")).strip(),
                str(current.get("note", "")).strip(),
                bool(current.get("tags")),
                bool(current.get("starred", False)),
            )
        )
        if has_business_meta:
            current["updated_at"] = now_iso
            accounts[meta_key] = current
        else:
            accounts.pop(meta_key, None)
        self._save_meta(meta_root)

    def _set_auto_recover_success_streak(self, account: dict[str, Any], count: int) -> None:
        normalized_count = max(count, 0)
        account["auto_recover_success_streak"] = normalized_count

        meta_key = self._meta_account_key(account)
        if not meta_key:
            return

        meta_root = self._load_meta()
        accounts = meta_root.setdefault("accounts", {})
        current = dict(accounts.get(meta_key) or {})
        now_iso = utc_now().isoformat()

        if normalized_count > 0:
            current["auto_recover_success_streak"] = normalized_count
            current["updated_at"] = now_iso
            accounts[meta_key] = current
            self._save_meta(meta_root)
            return

        current.pop("auto_recover_success_streak", None)
        has_persistent_meta = any(
            (
                str(current.get("group", "")).strip(),
                str(current.get("owner", "")).strip(),
                str(current.get("note", "")).strip(),
                bool(current.get("tags")),
                bool(current.get("starred", False)),
                bool(current.get("manual_disabled", False)),
                current.get("manual_disabled_at"),
            )
        )
        if has_persistent_meta:
            current["updated_at"] = now_iso
            accounts[meta_key] = current
        else:
            accounts.pop(meta_key, None)
        self._save_meta(meta_root)

    def _mark_token_expired_and_disable(self, account: dict[str, Any]) -> None:
        """Token 刷新失败时：写入 token_expired 到 quota cache + 禁用账号。"""
        refreshed_at = utc_now().isoformat()
        cache_key = self._account_cache_key(account)
        quota = {
            "provider": "codex",
            "status": "error",
            "email": str(account.get("email") or "").strip(),
            "plan_type": str(account.get("plan_type") or "").strip(),
            "rate_limit": None,
            "code_review_rate_limit": None,
            "additional_rate_limits": [],
            "credits": None,
            "error": {
                "type": "token_expired",
                "message": "Token 刷新失败，已标记过期并禁用",
            },
            "refreshed_at": refreshed_at,
        }
        if cache_key:
            self._quota_cache[cache_key] = quota
        self._apply_runtime_state(account)
        if not bool(account.get("disabled")):
            try:
                self._set_account_status(account, True)
                self._set_manual_disabled_marker(account, False)
            except RuntimeError:
                pass

    @staticmethod
    def _normalize_management_names(values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        names: list[str] = []
        seen: set[str] = set()
        for item in values:
            candidate = ""
            if isinstance(item, str):
                candidate = item.strip()
            elif isinstance(item, dict):
                candidate = str(item.get("name") or item.get("file") or item.get("id") or "").strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            names.append(candidate)
        return names

    @staticmethod
    def _normalize_management_failed(values: Any) -> list[dict[str, str]]:
        if not isinstance(values, list):
            return []
        failed: list[dict[str, str]] = []
        for item in values:
            if isinstance(item, str):
                name = item.strip()
                if name:
                    failed.append({"name": name, "reason": "删除失败"})
                continue
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("file") or item.get("id") or "").strip()
            reason = str(item.get("reason") or item.get("error") or item.get("message") or "删除失败").strip()
            if name:
                failed.append({"name": name, "reason": reason or "删除失败"})
        return failed

    def _remove_meta_entries(self, accounts_to_remove: list[dict[str, Any]]) -> None:
        if not accounts_to_remove:
            return
        meta_root = self._load_meta()
        accounts_meta = meta_root.setdefault("accounts", {})
        changed = False
        for item in accounts_to_remove:
            candidates = {
                str(item.get("key") or "").strip(),
                str(item.get("source_file") or "").strip(),
                Path(str(item.get("source_file") or "")).stem,
                str(item.get("email") or "").strip(),
                str(item.get("account_id") or "").strip(),
            }
            for candidate in candidates:
                if candidate and candidate in accounts_meta:
                    accounts_meta.pop(candidate, None)
                    changed = True
        if changed:
            self._save_meta(meta_root)

    def _remove_runtime_entries(self, accounts_to_remove: list[dict[str, Any]]) -> None:
        for item in accounts_to_remove:
            cache_key = self._account_cache_key(item)
            if cache_key:
                self._quota_cache.pop(cache_key, None)

    def _remove_accounts_from_cache(self, accounts_to_remove: list[dict[str, Any]]) -> None:
        if not accounts_to_remove:
            return
        remove_keys = {str(item.get("key") or "").strip() for item in accounts_to_remove if str(item.get("key") or "").strip()}
        remove_files = {
            str(item.get("source_file") or "").strip() for item in accounts_to_remove if str(item.get("source_file") or "").strip()
        }
        if not remove_keys and not remove_files:
            return

        with self._lock:
            cached_accounts = list(self._cache.get("accounts") or [])
            if not cached_accounts:
                return

            filtered_accounts = [
                item
                for item in cached_accounts
                if str(item.get("key") or "").strip() not in remove_keys
                and str(item.get("source_file") or "").strip() not in remove_files
            ]

            status_counts: dict[str, int] = {}
            group_counts: dict[str, int] = {}
            quota_error_counts: dict[str, int] = {}
            active_total = 0
            deactivated_total = 0

            for item in filtered_accounts:
                status = str(item.get("status") or "").strip()
                if status:
                    status_counts[status] = status_counts.get(status, 0) + 1
                group = str(item.get("group") or "").strip()
                if group:
                    group_counts[group] = group_counts.get(group, 0) + 1
                quota_error_type = str(item.get("quota_error_type") or "").strip()
                if quota_error_type:
                    quota_error_counts[quota_error_type] = quota_error_counts.get(quota_error_type, 0) + 1
                if status == "deactivated":
                    deactivated_total += 1
                else:
                    active_total += 1

            summary = dict(self._cache.get("summary") or {})
            summary.update(
                {
                    "total": len(filtered_accounts),
                    "active_total": active_total,
                    "deactivated_total": deactivated_total,
                    "status_counts": status_counts,
                    "group_counts": group_counts,
                    "quota_error_counts": quota_error_counts,
                    "last_scan_at": utc_now().isoformat(),
                }
            )

            self._cache = {
                **self._cache,
                "updated_at": time.time(),
                "accounts": filtered_accounts,
                "summary": summary,
            }

    @staticmethod
    def _account_cache_key(account: dict[str, Any]) -> str:
        for candidate in (
            account.get("source_file"),
            account.get("key"),
            account.get("email"),
            account.get("account_id"),
        ):
            value = str(candidate or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _normalize_quota_window(window: Any) -> dict[str, Any] | None:
        if not isinstance(window, dict):
            return None
        used_percent = window.get("used_percent")
        try:
            used_percent = float(used_percent)
        except (TypeError, ValueError):
            used_percent = None
        remaining_percent = None
        if used_percent is not None:
            remaining_percent = max(0.0, round(100.0 - used_percent, 2))
        return {
            "used_percent": used_percent,
            "remaining_percent": remaining_percent,
            "limit_window_seconds": window.get("limit_window_seconds"),
            "reset_after_seconds": window.get("reset_after_seconds"),
            "reset_at": window.get("reset_at"),
            "reset_at_iso": unix_ts_to_iso(window.get("reset_at")),
        }

    @classmethod
    def _quota_window_limit_reached(cls, window: Any) -> bool:
        if not isinstance(window, dict):
            return False

        used_percent = window.get("used_percent")
        try:
            used_percent = float(used_percent)
        except (TypeError, ValueError):
            used_percent = None

        remaining_percent = window.get("remaining_percent")
        try:
            remaining_percent = float(remaining_percent)
        except (TypeError, ValueError):
            remaining_percent = None

        if remaining_percent is not None and remaining_percent <= 0:
            return True
        if used_percent is not None and used_percent >= 100:
            return True
        return False

    @classmethod
    def _quota_bucket_limit_reached(cls, bucket: Any) -> bool:
        if not isinstance(bucket, dict):
            return False
        if bucket.get("allowed") is False:
            return True
        if bool(bucket.get("limit_reached")):
            return True
        if cls._quota_window_limit_reached(bucket.get("primary_window")):
            return True
        if cls._quota_window_limit_reached(bucket.get("secondary_window")):
            return True
        return False

    @classmethod
    def _normalize_quota_bucket(cls, payload: Any, label: str | None = None, key: str | None = None) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        return {
            "key": key or "",
            "label": label or key or "额度",
            "allowed": bool(payload.get("allowed", True)),
            "limit_reached": bool(payload.get("limit_reached", False)),
            "primary_window": cls._normalize_quota_window(payload.get("primary_window")),
            "secondary_window": cls._normalize_quota_window(payload.get("secondary_window")),
        }

    @classmethod
    def _normalize_additional_rate_limits(cls, payload: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            iterator = payload.items()
        elif isinstance(payload, list):
            iterator = [
                (
                    str(item.get("key") or item.get("id") or item.get("name") or f"limit_{index + 1}"),
                    item,
                )
                for index, item in enumerate(payload)
                if isinstance(item, dict)
            ]
        else:
            iterator = []

        for key, value in iterator:
            if not isinstance(value, dict):
                continue
            label = str(value.get("label") or value.get("name") or value.get("title") or key).strip() or key
            normalized = cls._normalize_quota_bucket(value, label=label, key=str(key))
            if normalized is not None:
                items.append(normalized)
        return items

    @classmethod
    def _normalize_codex_quota(cls, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise RuntimeError("额度接口返回为空")
        return {
            "provider": "codex",
            "email": str(payload.get("email") or "").strip(),
            "plan_type": str(payload.get("plan_type") or payload.get("planType") or "").strip(),
            "rate_limit": cls._normalize_quota_bucket(payload.get("rate_limit"), label="消息额度", key="rate_limit"),
            "code_review_rate_limit": cls._normalize_quota_bucket(
                payload.get("code_review_rate_limit"),
                label="Code Review 额度",
                key="code_review_rate_limit",
            ),
            "additional_rate_limits": cls._normalize_additional_rate_limits(payload.get("additional_rate_limits")),
            "credits": payload.get("credits") if isinstance(payload.get("credits"), dict) else None,
        }

    @staticmethod
    def _normalize_quota_error(payload: Any, status_code: int) -> dict[str, Any]:
        source = payload
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            source = payload.get("error")

        if isinstance(source, dict):
            message = str(source.get("message") or source.get("error") or source.get("detail") or "").strip()
            error_type = str(source.get("type") or source.get("code") or "request_failed").strip() or "request_failed"
            message_lower = message.lower()
            if error_type == "invalid_request_error" and (
                "authentication token" in message_lower and "invalidated" in message_lower
                or "signing in again" in message_lower
                or "sign in again" in message_lower
            ):
                error_type = "token_expired"
            plan_type = str(source.get("plan_type") or source.get("planType") or "").strip()
            resets_at = source.get("resets_at") or source.get("reset_at")
            resets_in_seconds = source.get("resets_in_seconds") or source.get("reset_after_seconds")
            return {
                "type": error_type,
                "message": message or f"额度接口返回 {status_code}",
                "plan_type": plan_type,
                "status_code": status_code,
                "resets_at": resets_at,
                "resets_at_iso": unix_ts_to_iso(resets_at),
                "resets_in_seconds": resets_in_seconds,
                "eligible_promo": source.get("eligible_promo"),
            }

        if isinstance(source, str):
            return {
                "type": "request_failed",
                "message": source.strip()[:500] or f"额度接口返回 {status_code}",
                "plan_type": "",
                "status_code": status_code,
                "resets_at": None,
                "resets_at_iso": None,
                "resets_in_seconds": None,
                "eligible_promo": None,
            }

        return {
            "type": "request_failed",
            "message": f"额度接口返回 {status_code}",
            "plan_type": "",
            "status_code": status_code,
            "resets_at": None,
            "resets_at_iso": None,
            "resets_in_seconds": None,
            "eligible_promo": None,
        }

    @staticmethod
    def _decode_management_body(payload: Any) -> Any:
        if isinstance(payload, (dict, list)):
            return payload
        if not isinstance(payload, str):
            return payload
        candidate = payload.strip()
        if not candidate:
            return {}
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return payload

    @staticmethod
    def _api_call_error_message(status_code: int, payload: Any) -> str:
        if isinstance(payload, dict):
            message = str(payload.get("message") or payload.get("error") or payload.get("detail") or "").strip()
            if message:
                return f"额度接口返回 {status_code}: {message}"
        if isinstance(payload, str):
            message = payload.strip()
            if message:
                return f"额度接口返回 {status_code}: {message[:200]}"
        return f"额度接口返回 {status_code}"

    def _apply_runtime_state(self, account: dict[str, Any]) -> None:
        quota = self._quota_cache.get(self._account_cache_key(account))
        account["quota"] = quota
        account["quota_updated_at"] = quota.get("refreshed_at") if quota else None
        account["quota_state"] = quota.get("status") if quota else ""
        account["quota_error"] = quota.get("error") if quota else None
        account["quota_error_type"] = ((quota.get("error") or {}).get("type")) if quota else ""
        if quota and quota.get("plan_type"):
            account["plan_type"] = quota["plan_type"]
        account["status_toggle_supported"] = bool(
            self.management.enabled and account.get("status") != "deactivated" and account.get("source_file")
        )
        account["quota_refresh_supported"] = bool(
            self.management.enabled
            and account.get("status") != "deactivated"
            and account.get("auth_index")
            and account.get("chatgpt_account_id")
        )

    def _read_local_config(self) -> dict[str, Any]:
        path = self.settings.cliproxy_config_path
        if not path.exists():
            return {
                "exists": False,
                "path": str(path),
                "message": "未找到 cliproxyapi 配置文件",
            }

        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            return {
                "exists": True,
                "path": str(path),
                "message": f"配置解析失败：{exc}",
            }
        return {
            "exists": True,
            "path": str(path),
            "config": config,
        }

    def _cliproxy_config_summary(self, management_snapshot: dict[str, Any] | None) -> dict[str, Any]:
        local_result = self._read_local_config()
        local_config = local_result.get("config") or {}
        remote = local_config.get("remote-management") or {}
        api_keys = local_config.get("api-keys") or []
        management_config = (management_snapshot or {}).get("config") or {}
        usage = (management_snapshot or {}).get("usage") or {}
        usage_root = usage.get("usage") or {}

        return {
            "exists": bool(local_result.get("exists")),
            "path": local_result.get("path") or str(self.settings.cliproxy_config_path),
            "message": local_result.get("message"),
            "host": local_config.get("host") or "0.0.0.0",
            "port": local_config.get("port"),
            "auth_dir": local_config.get("auth-dir"),
            "management_enabled": bool(remote.get("secret-key")),
            "allow_remote_management": bool(remote.get("allow-remote")),
            "control_panel_disabled": bool(remote.get("disable-control-panel")),
            "panel_github_repository": remote.get("panel-github-repository"),
            "api_key_count": len(api_keys),
            "routing_strategy": ((management_config.get("routing") or {}).get("strategy")) or ((local_config.get("routing") or {}).get("strategy")),
            "usage_statistics_enabled": bool(management_config.get("usage-statistics-enabled")),
            "request_log_enabled": bool(management_config.get("request-log")),
            "proxy_url_configured": bool(management_config.get("proxy-url")),
            "management_connected": bool((management_snapshot or {}).get("connected")),
            "management_auth_status": (management_snapshot or {}).get("auth_status") or "unknown",
            "management_base_url": self.management.base_url or "-",
            "latest_version": (management_snapshot or {}).get("latest_version") or "-",
            "live_total_requests": usage_root.get("total_requests"),
            "live_total_tokens": usage_root.get("total_tokens"),
        }

    def _scan_directory(self, directory: Path, source: str, meta_map: dict[str, Any]) -> list[dict[str, Any]]:
        if not directory.exists():
            return []

        records: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            token_payload = mapping_or_empty(raw.get("id_token"))
            try:
                stat_info = path.stat()
            except OSError:
                stat_info = None

            account_id = raw.get("account_id") or raw.get("email") or path.stem
            expires_at = parse_datetime(raw.get("expired"))
            last_refresh = parse_datetime(raw.get("last_refresh"))
            status = infer_status(source, bool(raw.get("disabled")), expires_at)
            modified_at = (
                datetime.fromtimestamp(stat_info.st_mtime, tz=timezone.utc)
                if stat_info is not None
                else parse_datetime(raw.get("modtime") or raw.get("modified") or raw.get("updated_at"))
            )
            meta = meta_map.get(account_id) or meta_map.get(path.stem) or {}
            days_remaining = None
            if expires_at is not None:
                days_remaining = round((expires_at - utc_now()).total_seconds() / 86400, 2)

            records.append(
                {
                    "key": account_id,
                    "source_file": path.name,
                    "source": source,
                    "source_label": "本地文件",
                    "status": status,
                    "status_label": STATUS_LABELS.get(status, status),
                    "type": raw.get("type", "unknown"),
                    "email": raw.get("email") or path.stem,
                    "email_masked": mask_email(raw.get("email") or path.stem),
                    "name": raw.get("name") or "",
                    "account_id": raw.get("account_id") or "",
                    "file_display_name": path.name,
                    "file_size_bytes": int(stat_info.st_size) if stat_info is not None else safe_int(str(raw.get("size") or "0"), 0),
                    "modified_at": isoformat_or_none(modified_at),
                    "modified_at_ms": datetime_to_epoch_ms(modified_at),
                    "disabled": bool(raw.get("disabled")),
                    "has_refresh_token": bool(str(raw.get("refresh_token") or "").strip()),
                    "expires_at": isoformat_or_none(expires_at),
                    "last_refresh": isoformat_or_none(last_refresh),
                    "days_remaining": days_remaining,
                    "group": meta.get("group", ""),
                    "owner": meta.get("owner", ""),
                    "tags": meta.get("tags", []),
                    "note": meta.get("note", ""),
                    "starred": bool(meta.get("starred", False)),
                    "manual_disabled": bool(meta.get("manual_disabled", False)),
                    "manual_disabled_at": meta.get("manual_disabled_at"),
                    "auto_recover_success_streak": safe_int(str(meta.get("auto_recover_success_streak") or "0"), 0),
                    "updated_at": meta.get("updated_at"),
                    "birthday": raw.get("birthday"),
                    "auth_index": "",
                    "plan_type": str(raw.get("plan_type") or token_payload.get("plan_type") or ""),
                    "chatgpt_account_id": str(raw.get("chatgpt_account_id") or token_payload.get("chatgpt_account_id") or ""),
                    "runtime_only": False,
                    "unavailable": False,
                    "status_message": "",
                    "request_count": 0,
                    "failure_count": 0,
                    "total_tokens": 0,
                    "last_request_at": None,
                    "models": [],
                    "quota": None,
                    "quota_updated_at": None,
                    "status_toggle_supported": False,
                    "quota_refresh_supported": False,
                }
            )
        return records

    def _build_usage_index(self, usage_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {"by_auth_index": {}, "by_source": {}}
        usage_root = usage_payload.get("usage") or {}
        apis = usage_root.get("apis") or {}
        for api_payload in apis.values():
            models = api_payload.get("models") or {}
            for model_name, model_payload in models.items():
                for detail in model_payload.get("details") or []:
                    total_tokens = ((detail.get("tokens") or {}).get("total_tokens")) or 0
                    source = str(detail.get("source") or "").strip().lower()
                    auth_index = str(detail.get("auth_index") or "").strip()
                    keys = []
                    if auth_index:
                        keys.append(("by_auth_index", auth_index))
                    if source:
                        keys.append(("by_source", source))
                    for bucket_name, key in keys:
                        bucket = result[bucket_name].setdefault(
                            key,
                            {
                                "request_count": 0,
                                "failure_count": 0,
                                "total_tokens": 0,
                                "last_request_at": None,
                                "models": set(),
                            },
                        )
                        bucket["request_count"] += 1
                        bucket["failure_count"] += 1 if detail.get("failed") else 0
                        bucket["total_tokens"] += total_tokens
                        bucket["models"].add(model_name)
                        timestamp = parse_datetime(detail.get("timestamp"))
                        if timestamp:
                            current = parse_datetime(bucket["last_request_at"])
                            if current is None or timestamp > current:
                                bucket["last_request_at"] = isoformat_or_none(timestamp)

        for bucket_name in ("by_auth_index", "by_source"):
            for item in result[bucket_name].values():
                item["models"] = sorted(item["models"])
        return result

    def _merge_management_accounts(
        self,
        local_accounts: list[dict[str, Any]],
        management_snapshot: dict[str, Any],
        meta_map: dict[str, Any],
    ) -> list[dict[str, Any]]:
        by_file = {item["source_file"]: item for item in local_accounts}
        by_email = {item["email"].lower(): item for item in local_accounts}
        usage_index = self._build_usage_index(management_snapshot.get("usage") or {})

        for remote in management_snapshot.get("auth_files") or []:
            token_payload = mapping_or_empty(remote.get("id_token"))
            name = str(remote.get("name") or remote.get("id") or "").strip()
            email = str(remote.get("email") or remote.get("account") or "").strip()
            local = by_file.get(name) or by_email.get(email.lower())
            if local is None:
                meta = meta_map.get(email) or meta_map.get(name) or {}
                local = {
                    "key": remote.get("id") or email or name,
                    "source_file": name or remote.get("id") or "",
                    "source": "management",
                    "source_label": "管理接口",
                    "status": "unknown",
                    "status_label": STATUS_LABELS.get("unknown"),
                    "type": remote.get("type", remote.get("provider", "unknown")),
                    "email": email or name,
                    "email_masked": mask_email(email or name),
                    "name": "",
                    "account_id": "",
                    "file_display_name": name or remote.get("id") or "",
                    "file_size_bytes": safe_int(str(remote.get("size") or "0"), 0),
                    "modified_at": None,
                    "modified_at_ms": 0,
                    "disabled": bool(remote.get("disabled")),
                    "expires_at": None,
                    "last_refresh": None,
                    "days_remaining": None,
                    "group": meta.get("group", ""),
                    "owner": meta.get("owner", ""),
                    "tags": meta.get("tags", []),
                    "note": meta.get("note", ""),
                    "starred": bool(meta.get("starred", False)),
                    "manual_disabled": bool(meta.get("manual_disabled", False)),
                    "manual_disabled_at": meta.get("manual_disabled_at"),
                    "auto_recover_success_streak": safe_int(str(meta.get("auto_recover_success_streak") or "0"), 0),
                    "updated_at": meta.get("updated_at"),
                    "birthday": None,
                    "auth_index": "",
                    "plan_type": "",
                    "chatgpt_account_id": "",
                    "runtime_only": False,
                    "unavailable": False,
                    "status_message": "",
                    "request_count": 0,
                    "failure_count": 0,
                    "total_tokens": 0,
                    "last_request_at": None,
                    "models": [],
                    "quota": None,
                    "quota_updated_at": None,
                    "status_toggle_supported": False,
                    "quota_refresh_supported": False,
                }
                local_accounts.append(local)

            usage = usage_index["by_auth_index"].get(str(remote.get("auth_index") or "").strip()) or usage_index["by_source"].get(email.lower())
            meta = meta_map.get(local.get("key")) or meta_map.get(email) or meta_map.get(name) or {}
            local["source"] = "management"
            local["source_label"] = "管理接口"
            local["type"] = remote.get("type", local.get("type", "unknown"))
            local["file_display_name"] = str(name or remote.get("id") or local.get("file_display_name") or "")
            local["file_size_bytes"] = safe_int(str(remote.get("size") or local.get("file_size_bytes") or "0"), 0)
            local["disabled"] = bool(remote.get("disabled"))
            local["manual_disabled"] = bool(meta.get("manual_disabled", False))
            local["manual_disabled_at"] = meta.get("manual_disabled_at")
            local["auto_recover_success_streak"] = safe_int(str(meta.get("auto_recover_success_streak") or "0"), 0)
            local["auth_index"] = str(remote.get("auth_index") or "")
            local["plan_type"] = str(token_payload.get("plan_type") or "")
            local["chatgpt_account_id"] = str(token_payload.get("chatgpt_account_id") or "")
            local["runtime_only"] = bool(remote.get("runtime_only"))
            local["unavailable"] = bool(remote.get("unavailable"))
            local["status_message"] = str(remote.get("status_message") or "")
            local["management_updated_at"] = remote.get("updated_at")
            modified_at = parse_datetime(remote.get("modtime") or remote.get("modified") or remote.get("updated_at"))
            local["modified_at"] = isoformat_or_none(modified_at) or local.get("modified_at")
            local["modified_at_ms"] = datetime_to_epoch_ms(modified_at) or local.get("modified_at_ms") or 0
            local["request_count"] = usage.get("request_count", 0) if usage else 0
            local["failure_count"] = usage.get("failure_count", 0) if usage else 0
            local["total_tokens"] = usage.get("total_tokens", 0) if usage else 0
            local["last_request_at"] = usage.get("last_request_at") if usage else None
            local["models"] = usage.get("models", []) if usage else []

            status = local["status"]
            remote_status = str(remote.get("status") or "").strip().lower()
            if remote.get("disabled"):
                status = "disabled"
            elif remote_status in STATUS_LABELS:
                status = remote_status
            elif status == "unknown" and not remote.get("unavailable"):
                status = "active"
            local["status"] = status
            local["status_label"] = STATUS_LABELS.get(status, status)

        return local_accounts

    def _management_snapshot(self) -> dict[str, Any]:
        if not self.management.enabled:
            return {
                "connected": False,
                "base_url": self.management.base_url,
                "error": "未配置 Management API 连接信息",
            }

        try:
            return self.management.snapshot()
        except RuntimeError as exc:
            return {
                "connected": False,
                "base_url": self.management.base_url,
                "error": str(exc),
            }

    def _build_payload(self) -> dict[str, Any]:
        meta_root = self._load_meta()
        meta_map = meta_root.get("accounts", {})
        start = time.perf_counter()
        management_snapshot = self._management_snapshot()
        active_accounts = self._scan_directory(self.settings.auth_dir, "active", meta_map)
        if management_snapshot.get("connected"):
            active_accounts = self._merge_management_accounts(active_accounts, management_snapshot, meta_map)
        deactivated_accounts = self._scan_directory(self.settings.deactivated_dir, "deactivated", meta_map)
        accounts = active_accounts + deactivated_accounts
        for item in accounts:
            self._apply_runtime_state(item)

        accounts.sort(
            key=lambda item: (
                0 if item["starred"] else 1,
                STATUS_ORDER.get(item["status"], 99),
                item["days_remaining"] if item["days_remaining"] is not None else 999999,
                item["email"].lower(),
            )
        )

        status_counts: dict[str, int] = {}
        groups: dict[str, int] = {}
        quota_error_counts: dict[str, int] = {}
        manual_disabled_count = 0
        exportable_problem_count = 0
        for item in accounts:
            status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
            if item["group"]:
                groups[item["group"]] = groups.get(item["group"], 0) + 1
            quota_error_type = str(item.get("quota_error_type") or "").strip()
            if quota_error_type:
                quota_error_counts[quota_error_type] = quota_error_counts.get(quota_error_type, 0) + 1
            if bool(item.get("disabled")) and bool(item.get("manual_disabled")):
                manual_disabled_count += 1
            if self._problem_account_export_reason(item):
                exportable_problem_count += 1

        summary = {
            "total": len(accounts),
            "active_total": len(active_accounts),
            "deactivated_total": len(deactivated_accounts),
            "status_counts": status_counts,
            "group_counts": groups,
            "quota_error_counts": quota_error_counts,
            "manual_disabled_total": manual_disabled_count,
            "disabled_unrefreshed_total": exportable_problem_count,
            "scan_duration_ms": round((time.perf_counter() - start) * 1000, 1),
            "last_scan_at": utc_now().isoformat(),
            "management_connected": bool(management_snapshot.get("connected")),
            "management_error": management_snapshot.get("error"),
            "management_auth_status": management_snapshot.get("auth_status"),
            "latest_version": management_snapshot.get("latest_version"),
            "live_total_requests": ((management_snapshot.get("usage") or {}).get("usage") or {}).get("total_requests"),
            "live_total_tokens": ((management_snapshot.get("usage") or {}).get("usage") or {}).get("total_tokens"),
            "live_success_count": ((management_snapshot.get("usage") or {}).get("usage") or {}).get("success_count"),
            "live_failure_count": ((management_snapshot.get("usage") or {}).get("usage") or {}).get("failure_count"),
        }
        return {
            "updated_at": time.time(),
            "accounts": accounts,
            "summary": summary,
            "config": self._cliproxy_config_summary(management_snapshot),
            "management": management_snapshot,
        }

    def payload(self, force: bool = False) -> dict[str, Any]:
        with self._lock:
            if force or time.time() - self._cache["updated_at"] >= self.settings.cache_seconds:
                self._cache = self._build_payload()
            return self._cache

    def overview(self, force: bool = False) -> dict[str, Any]:
        payload = self.payload(force=force)
        management = payload.get("management") or {}
        quota_error_counts = payload["summary"].get("quota_error_counts") or {}
        status_options = [{"value": key, "label": label} for key, label in STATUS_LABELS.items()]
        synthetic_status_values = ["usage_limit_reached", "token_expired"]
        seen_status_values = {item["value"] for item in status_options}
        for value in synthetic_status_values:
            if value in seen_status_values:
                continue
            status_options.append(
                {
                    "value": value,
                    "label": QUOTA_ERROR_FILTER_LABELS.get(value, value),
                    "count": quota_error_counts.get(value, 0),
                }
            )
        return {
            "summary": payload["summary"],
            "management": {
                "connected": bool(management.get("connected")),
                "base_url": management.get("base_url"),
                "auth_status": management.get("auth_status"),
                "error": management.get("error"),
                "latest_version": management.get("latest_version"),
            },
            "groups": [
                {"name": name, "count": count}
                for name, count in sorted(payload["summary"]["group_counts"].items(), key=lambda item: item[0].lower())
            ],
            "status_options": status_options,
            "quota_error_options": [
                {"value": name, "label": name, "count": count}
                for name, count in sorted(quota_error_counts.items(), key=lambda item: item[0].lower())
            ],
        }

    def _account_list_item(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "key": item.get("key"),
            "status": item.get("status"),
            "status_label": item.get("status_label"),
            "disabled": item.get("disabled"),
            "email": item.get("email"),
            "name": item.get("name"),
            "group": item.get("group"),
            "tags": item.get("tags") or [],
            "starred": bool(item.get("starred")),
            "manual_disabled": bool(item.get("manual_disabled")),
            "expires_at": item.get("expires_at"),
            "last_refresh": item.get("last_refresh"),
            "days_remaining": item.get("days_remaining"),
            "plan_type": item.get("plan_type"),
            "quota_state": item.get("quota_state"),
            "quota_error": item.get("quota_error"),
            "quota_error_type": item.get("quota_error_type"),
            "status_toggle_supported": bool(item.get("status_toggle_supported")),
            "quota_refresh_supported": bool(item.get("quota_refresh_supported")),
            "has_refresh_token": bool(item.get("has_refresh_token")),
        }

    def accounts(
        self,
        *,
        q: str = "",
        status: str = "",
        quota_error_type: str = "",
        group: str = "",
        page: int = 1,
        page_size: int = 50,
        sort_by: str = "",
        sort_order: str = "",
    ) -> dict[str, Any]:
        payload = self.payload()
        items = payload["accounts"]
        keyword = q.strip().lower()
        group_value = group.strip().lower()
        status_value = status.strip().lower()
        quota_error_value = quota_error_type.strip().lower()

        def matched(item: dict[str, Any]) -> bool:
            item_status = str(item.get("status") or "").strip().lower()
            item_quota_error = str(item.get("quota_error_type") or "").strip().lower()
            if status_value and status_value not in {item_status, item_quota_error}:
                return False
            if quota_error_value and item_quota_error != quota_error_value:
                return False
            if group_value and item["group"].lower() != group_value:
                return False
            if not keyword:
                return True
            haystack = " ".join(
                [
                    item.get("email", ""),
                    item.get("name", ""),
                    item.get("account_id", ""),
                    item.get("file_display_name", ""),
                    item.get("source_file", ""),
                    item.get("auth_index", ""),
                    item.get("group", ""),
                    " ".join(item.get("tags", [])),
                    item.get("owner", ""),
                    item.get("note", ""),
                    " ".join(item.get("models", [])),
                    item.get("plan_type", ""),
                    item.get("quota_state", "") or "",
                    item.get("quota_error_type", "") or "",
                ]
            ).lower()
            return keyword in haystack

        filtered = [item for item in items if matched(item)]

        # Apply user-requested sort if provided
        _valid_sort_fields = {"expires_at", "last_refresh"}
        _sort_field = sort_by.strip().lower() if sort_by else ""
        _sort_desc = sort_order.strip().lower() == "desc"
        if _sort_field in _valid_sort_fields:
            def _sort_key(item: dict[str, Any]) -> str:
                raw = item.get(_sort_field)
                if raw is None:
                    return ""
                return str(raw)
            filtered.sort(key=_sort_key, reverse=_sort_desc)

        total = len(filtered)
        page = max(page, 1)
        page_size = min(max(page_size, 10), 200)
        start = (page - 1) * page_size
        end = start + page_size

        return {
            "items": [self._account_list_item(item) for item in filtered[start:end]],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": max(1, (total + page_size - 1) // page_size),
        }

    def account_detail(self, key: str, force: bool = False) -> dict[str, Any] | None:
        items = self.payload(force=force)["accounts"]
        for item in items:
            if item["key"] == key:
                return item
        return None

    def update_meta(self, key: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        meta_root = self._load_meta()
        accounts = meta_root.setdefault("accounts", {})
        current = accounts.setdefault(key, {})
        current["group"] = str(payload.get("group", "")).strip()
        current["owner"] = str(payload.get("owner", "")).strip()
        current["note"] = str(payload.get("note", "")).strip()
        current["starred"] = bool(payload.get("starred", False))

        tags = payload.get("tags", [])
        if isinstance(tags, str):
            tags = [part.strip() for part in tags.split(",") if part.strip()]
        if not isinstance(tags, list):
            tags = []
        current["tags"] = [str(tag).strip() for tag in tags if str(tag).strip()]
        current["updated_at"] = utc_now().isoformat()
        self._save_meta(meta_root)

        refreshed = self.payload(force=True)
        for item in refreshed["accounts"]:
            if item["key"] == key:
                return item
        return None

    def delete_deactivated_accounts(self, keys: list[str]) -> dict[str, Any]:
        unique_keys: list[str] = []
        seen: set[str] = set()
        for key in keys:
            candidate = str(key or "").strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            unique_keys.append(candidate)

        if not unique_keys:
            return {
                "requested_count": 0,
                "deleted_count": 0,
                "failed_count": 0,
                "deleted": [],
                "failed": [],
            }

        current = self.payload(force=True)
        account_map = {item["key"]: item for item in current["accounts"]}
        pending_delete: list[dict[str, Any]] = []
        failed: list[dict[str, str]] = []

        for key in unique_keys:
            account = account_map.get(key)
            if account is None:
                failed.append({"key": key, "reason": "未找到账号"})
                continue
            if account.get("status") != "deactivated":
                failed.append({"key": key, "email": account.get("email", ""), "reason": "仅支持删除已迁移停用账号"})
                continue
            source_file = str(account.get("source_file") or "").strip()
            if not source_file:
                failed.append({"key": key, "email": account.get("email", ""), "reason": "缺少源文件名，无法删除"})
                continue
            target_path = self.settings.deactivated_dir / source_file
            if not target_path.exists():
                failed.append({"key": key, "email": account.get("email", ""), "reason": "停用归档文件不存在"})
                continue
            pending_delete.append(account)

        deleted_accounts: list[dict[str, Any]] = []
        if pending_delete:
            backup_root = self.settings.meta_path.parent / "deactivated-delete-backups"
            backup_root.mkdir(parents=True, exist_ok=True)
            batch_backup_dir = backup_root / f"batch_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            batch_backup_dir.mkdir(parents=True, exist_ok=True)
            for item in pending_delete:
                source_file = str(item["source_file"])
                source_path = self.settings.deactivated_dir / source_file
                backup_path = batch_backup_dir / source_file
                try:
                    shutil.copy2(source_path, backup_path)
                    source_path.unlink()
                    deleted_accounts.append(item)
                except OSError as exc:
                    failed.append({"key": item["key"], "email": item.get("email", ""), "reason": f"删除失败：{exc}"})

        if deleted_accounts:
            self._remove_meta_entries(deleted_accounts)
            self._remove_accounts_from_cache(deleted_accounts)
        deleted_payload = [
            {
                "key": item["key"],
                "email": item.get("email", ""),
                "source_file": item.get("source_file", ""),
            }
            for item in deleted_accounts
        ]

        return {
            "requested_count": len(unique_keys),
            "deleted_count": len(deleted_payload),
            "failed_count": len(failed),
            "deleted": deleted_payload,
            "failed": failed,
        }

    def delete_accounts(self, keys: list[str]) -> dict[str, Any]:
        unique_keys: list[str] = []
        seen: set[str] = set()
        for key in keys:
            candidate = str(key or "").strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            unique_keys.append(candidate)

        if not unique_keys:
            return {
                "requested_count": 0,
                "deleted_count": 0,
                "failed_count": 0,
                "deleted": [],
                "failed": [],
            }

        current = self.payload(force=True)
        account_map = {item["key"]: item for item in current["accounts"]}
        deactivated_keys: list[str] = []
        token_expired_accounts: list[dict[str, Any]] = []
        failed: list[dict[str, str]] = []

        disabled_accounts_to_delete: list[dict[str, Any]] = []

        for key in unique_keys:
            account = account_map.get(key)
            if account is None:
                failed.append({"key": key, "reason": "未找到账号"})
                continue
            if account.get("status") == "deactivated":
                deactivated_keys.append(key)
                continue
            if str(account.get("quota_error_type") or "").strip() == "token_expired":
                token_expired_accounts.append(account)
                continue
            if bool(account.get("disabled")):
                disabled_accounts_to_delete.append(account)
                continue
            failed.append(
                {
                    "key": key,
                    "email": account.get("email", ""),
                    "reason": "仅支持删除已禁用、已迁移停用或 Token 过期账号",
                }
            )

        deleted_accounts: list[dict[str, Any]] = []

        if deactivated_keys:
            deactivated_result = self.delete_deactivated_accounts(deactivated_keys)
            failed.extend(deactivated_result.get("failed") or [])
            deleted_by_key = {item["key"] for item in deactivated_result.get("deleted") or []}
            for key in deactivated_keys:
                account = account_map.get(key)
                if account is not None and key in deleted_by_key:
                    deleted_accounts.append(account)

        if token_expired_accounts:
            backup_root = self.settings.meta_path.parent / "token-expired-delete-backups"
            backup_root.mkdir(parents=True, exist_ok=True)
            batch_backup_dir = backup_root / f"batch_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            batch_backup_dir.mkdir(parents=True, exist_ok=True)

            request_names: list[str] = []
            account_by_name: dict[str, dict[str, Any]] = {}
            for account in token_expired_accounts:
                source_file = str(account.get("source_file") or "").strip()
                if not source_file:
                    failed.append({"key": account["key"], "email": account.get("email", ""), "reason": "缺少源文件名，无法删除"})
                    continue
                account_by_name[source_file] = account
                request_names.append(source_file)
                source_path = self.settings.auth_dir / source_file
                if source_path.exists():
                    try:
                        shutil.copy2(source_path, batch_backup_dir / source_file)
                    except OSError as exc:
                        failed.append({"key": account["key"], "email": account.get("email", ""), "reason": f"备份失败：{exc}"})

            if request_names:
                delete_result = self.management.delete_auth_files(request_names)
                deleted_names = self._normalize_management_names(delete_result.get("files"))
                deleted_names_set = set(deleted_names)
                failed_items = self._normalize_management_failed(delete_result.get("failed"))
                failed_name_set = {item.get("name", "") for item in failed_items}
                deleted_count = safe_int(str(delete_result.get("deleted") or "0"), 0)
                if not deleted_names_set and deleted_count == len(request_names) and not failed_items:
                    deleted_names_set = set(request_names)

                for name in deleted_names_set:
                    account = account_by_name.get(name)
                    if account is not None:
                        deleted_accounts.append(account)

                for item in failed_items:
                    name = item.get("name", "")
                    account = account_by_name.get(name)
                    failed.append(
                        {
                            "key": account.get("key", name) if account else name,
                            "email": account.get("email", "") if account else "",
                            "reason": item.get("reason") or "删除失败",
                        }
                    )

                for name in request_names:
                    if name in deleted_names_set or name in failed_name_set:
                        continue
                    account = account_by_name.get(name)
                    if account is not None:
                        failed.append({"key": account["key"], "email": account.get("email", ""), "reason": "删除结果未知，请稍后刷新确认"})

        if disabled_accounts_to_delete:
            backup_root = self.settings.meta_path.parent / "disabled-delete-backups"
            backup_root.mkdir(parents=True, exist_ok=True)
            batch_backup_dir = backup_root / f"batch_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            batch_backup_dir.mkdir(parents=True, exist_ok=True)

            disabled_request_names: list[str] = []
            disabled_account_by_name: dict[str, dict[str, Any]] = {}
            for account in disabled_accounts_to_delete:
                source_file = str(account.get("source_file") or "").strip()
                if not source_file:
                    failed.append({"key": account["key"], "email": account.get("email", ""), "reason": "缺少源文件名，无法删除"})
                    continue
                disabled_account_by_name[source_file] = account
                disabled_request_names.append(source_file)
                source_path = self.settings.auth_dir / source_file
                if source_path.exists():
                    try:
                        shutil.copy2(source_path, batch_backup_dir / source_file)
                    except OSError as exc:
                        failed.append({"key": account["key"], "email": account.get("email", ""), "reason": f"备份失败：{exc}"})

            if disabled_request_names:
                delete_result = self.management.delete_auth_files(disabled_request_names)
                deleted_names = self._normalize_management_names(delete_result.get("files"))
                deleted_names_set = set(deleted_names)
                failed_items = self._normalize_management_failed(delete_result.get("failed"))
                failed_name_set = {item.get("name", "") for item in failed_items}
                deleted_count = safe_int(str(delete_result.get("deleted") or "0"), 0)
                if not deleted_names_set and deleted_count == len(disabled_request_names) and not failed_items:
                    deleted_names_set = set(disabled_request_names)

                for name in deleted_names_set:
                    account = disabled_account_by_name.get(name)
                    if account is not None:
                        deleted_accounts.append(account)

                for item in failed_items:
                    name = item.get("name", "")
                    account = disabled_account_by_name.get(name)
                    failed.append(
                        {
                            "key": account.get("key", name) if account else name,
                            "email": account.get("email", "") if account else "",
                            "reason": item.get("reason") or "删除失败",
                        }
                    )

                for name in disabled_request_names:
                    if name in deleted_names_set or name in failed_name_set:
                        continue
                    account = disabled_account_by_name.get(name)
                    if account is not None:
                        failed.append({"key": account["key"], "email": account.get("email", ""), "reason": "删除结果未知，请稍后刷新确认"})

        if deleted_accounts:
            self._remove_meta_entries(deleted_accounts)
            self._remove_runtime_entries(deleted_accounts)
            self._remove_accounts_from_cache(deleted_accounts)
        deleted_payload = [
            {
                "key": item["key"],
                "email": item.get("email", ""),
                "source_file": item.get("source_file", ""),
            }
            for item in deleted_accounts
        ]

        return {
            "requested_count": len(unique_keys),
            "deleted_count": len(deleted_payload),
            "failed_count": len(failed),
            "deleted": deleted_payload,
            "failed": failed,
        }

    def delete_all_deactivated_accounts(self) -> dict[str, Any]:
        current = self.payload(force=True)
        keys = [item["key"] for item in current["accounts"] if item.get("status") == "deactivated"]
        result = self.delete_deactivated_accounts(keys)
        result["scope"] = "all_deactivated"
        result["available_count"] = len(keys)
        return result

    def _disabled_unrefreshed_accounts(self, *, force: bool = True) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        current = self.payload(force=force)
        candidates: list[dict[str, Any]] = []
        failed: list[dict[str, str]] = []
        for item in current["accounts"]:
            selection_reason = self._problem_account_export_reason(item)
            if not selection_reason:
                continue
            source_file = str(item.get("source_file") or "").strip()
            if not source_file:
                failed.append({"key": item["key"], "email": item.get("email", ""), "reason": "缺少源文件名，无法打包"})
                continue
            source_path = self.settings.auth_dir / source_file
            if not source_path.exists():
                failed.append({"key": item["key"], "email": item.get("email", ""), "reason": "源 JSON 文件不存在"})
                continue
            candidates.append({**item, "_source_path": source_path, "_selection_reason": selection_reason})
        return candidates, failed

    @staticmethod
    def _problem_account_export_reason(account: dict[str, Any]) -> str | None:
        if account.get("status") == "deactivated":
            return None
        if not str(account.get("source_file") or "").strip():
            return None

        quota_error_type = str(account.get("quota_error_type") or "").strip()
        if quota_error_type == "token_expired":
            return "token_expired"

        if str(account.get("status") or "").strip() == "expired":
            return "expired"

        if bool(account.get("disabled")) and not bool(account.get("has_refresh_token")):
            return "missing_refresh_token"

        return None

    def build_disabled_unrefreshed_archive(self) -> dict[str, Any]:
        with self._disabled_unrefreshed_export_lock:
            candidates, failed = self._disabled_unrefreshed_accounts(force=True)
            if not candidates:
                raise RuntimeError("当前没有符合条件的异常 JSON（缺少 refresh_token / Token 过期 / 已过期）")

            archive_time = utc_now()
            archive_name = f"disabled_problem_accounts_{archive_time.strftime('%Y%m%d_%H%M%S')}.zip"
            manifest_accounts: list[dict[str, Any]] = []
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                for account in candidates:
                    source_path = account["_source_path"]
                    archive.write(source_path, arcname=source_path.name)
                    manifest_accounts.append(
                        {
                            "key": account["key"],
                            "email": account.get("email", ""),
                            "source_file": source_path.name,
                            "disabled": bool(account.get("disabled")),
                            "has_refresh_token": bool(account.get("has_refresh_token")),
                            "last_refresh": account.get("last_refresh"),
                            "status": account.get("status", ""),
                            "quota_error_type": account.get("quota_error_type", ""),
                            "selection_reason": account.get("_selection_reason"),
                        }
                    )
                archive.writestr(
                    "manifest.json",
                    json.dumps(
                        {
                            "generated_at": archive_time.isoformat(),
                            "scope": "disabled_problem_auth_files",
                            "account_count": len(manifest_accounts),
                            "accounts": manifest_accounts,
                            "skipped": failed,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                )

            return {
                "archive_name": archive_name,
                "archive_bytes": buffer.getvalue(),
                "account_count": len(manifest_accounts),
                "accounts": candidates,
                "failed": failed,
            }

    def delete_disabled_unrefreshed_accounts_after_export(self, accounts: list[dict[str, Any]]) -> dict[str, Any]:
        with self._disabled_unrefreshed_export_lock:
            if not accounts:
                return {"deleted_count": 0, "failed_count": 0, "deleted": [], "failed": []}

            failed: list[dict[str, str]] = []
            deleted_accounts: list[dict[str, Any]] = []
            request_names: list[str] = []
            account_by_name: dict[str, dict[str, Any]] = {}

            for account in accounts:
                source_file = str(account.get("source_file") or "").strip()
                if not source_file:
                    failed.append({"key": account.get("key", ""), "email": account.get("email", ""), "reason": "缺少源文件名，无法删除"})
                    continue
                request_names.append(source_file)
                account_by_name[source_file] = account

            if request_names:
                if self.management.enabled:
                    delete_result = self.management.delete_auth_files(request_names)
                    deleted_names = set(self._normalize_management_names(delete_result.get("files")))
                    failed_items = self._normalize_management_failed(delete_result.get("failed"))
                    failed_name_set = {item.get("name", "") for item in failed_items}
                    deleted_count = safe_int(str(delete_result.get("deleted") or "0"), 0)
                    if not deleted_names and deleted_count == len(request_names) and not failed_items:
                        deleted_names = set(request_names)

                    for name in deleted_names:
                        account = account_by_name.get(name)
                        if account is not None:
                            deleted_accounts.append(account)

                    for item in failed_items:
                        name = item.get("name", "")
                        account = account_by_name.get(name)
                        failed.append(
                            {
                                "key": account.get("key", name) if account else name,
                                "email": account.get("email", "") if account else "",
                                "reason": item.get("reason") or "删除失败",
                            }
                        )

                    for name in request_names:
                        if name in deleted_names or name in failed_name_set:
                            continue
                        account = account_by_name.get(name)
                        if account is not None:
                            failed.append({"key": account["key"], "email": account.get("email", ""), "reason": "删除结果未知，请刷新后确认"})
                else:
                    for name in request_names:
                        account = account_by_name.get(name)
                        path = self.settings.auth_dir / name
                        try:
                            path.unlink()
                            if account is not None:
                                deleted_accounts.append(account)
                        except OSError as exc:
                            failed.append(
                                {
                                    "key": account.get("key", name) if account else name,
                                    "email": account.get("email", "") if account else "",
                                    "reason": f"删除失败：{exc}",
                                }
                            )

        if deleted_accounts:
            self._remove_meta_entries(deleted_accounts)
            self._remove_runtime_entries(deleted_accounts)
            self._remove_accounts_from_cache(deleted_accounts)

        return {
            "deleted_count": len(deleted_accounts),
            "failed_count": len(failed),
            "deleted": [
                {
                    "key": item["key"],
                    "email": item.get("email", ""),
                    "source_file": item.get("source_file", ""),
                }
                for item in deleted_accounts
            ],
            "failed": failed,
        }

    def _unique_keys(self, keys: list[str]) -> list[str]:
        unique_keys: list[str] = []
        seen: set[str] = set()
        for key in keys:
            candidate = str(key or "").strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            unique_keys.append(candidate)
        return unique_keys

    def _manual_disabled_refresh_candidates(self, *, force: bool = True, limit: int = 0) -> tuple[list[dict[str, Any]], int, int, str]:
        current = self.payload(force=force)
        disabled_candidates = [
            item
            for item in current["accounts"]
            if item.get("status") != "deactivated"
            and bool(item.get("disabled"))
            and bool(item.get("quota_refresh_supported"))
        ]
        tagged_candidates = [item for item in disabled_candidates if bool(item.get("manual_disabled"))]
        manual_disabled_total = len(tagged_candidates)
        eligible_disabled_total = len([item for item in disabled_candidates if bool(item.get("has_refresh_token"))])
        if tagged_candidates:
            candidates = tagged_candidates
            selection_mode = "manual_tagged_only"
        else:
            candidates = [item for item in disabled_candidates if bool(item.get("has_refresh_token"))]
            selection_mode = "legacy_disabled_fallback"
        candidates.sort(
            key=lambda item: (
                0 if item.get("manual_disabled") else 1,
                item.get("manual_disabled_at") or "",
                item.get("last_refresh") or "",
                item.get("email", "").lower(),
            )
        )
        if limit > 0:
            candidates = candidates[:limit]
        return candidates, manual_disabled_total, eligible_disabled_total, selection_mode

    def _set_account_status(self, account: dict[str, Any], disabled: bool) -> None:
        if account.get("status") == "deactivated":
            raise RuntimeError("已迁移停用账号不支持切换启用状态")
        source_file = str(account.get("source_file") or "").strip()
        if not source_file:
            raise RuntimeError("缺少账号文件名，无法切换状态")
        self.management.set_auth_file_status(source_file, disabled)

    def toggle_account_status(self, key: str, disabled: bool) -> dict[str, Any]:
        account = self.account_detail(key, force=True)
        if account is None:
            raise KeyError("未找到账号")
        self._set_account_status(account, disabled)
        self._set_manual_disabled_marker(account, disabled)
        updated = self.account_detail(key, force=True)
        if updated is None:
            raise RuntimeError("切换状态后未找到账号")
        return updated

    def batch_toggle_accounts(self, keys: list[str], disabled: bool) -> dict[str, Any]:
        unique_keys = self._unique_keys(keys)
        if not unique_keys:
            return {
                "requested_count": 0,
                "eligible_count": 0,
                "updated_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
                "updated": [],
                "skipped": [],
                "failed": [],
            }

        current = self.payload(force=True)
        account_map = {item["key"]: item for item in current["accounts"]}
        eligible_accounts: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []

        target_label = "停用" if disabled else "启用"
        for key in unique_keys:
            account = account_map.get(key)
            if account is None:
                failed.append({"key": key, "reason": "未找到账号"})
                continue
            if account.get("status") == "deactivated":
                failed.append({"key": key, "email": account.get("email", ""), "reason": "已迁移停用账号不支持切换状态"})
                continue
            if bool(account.get("disabled")) == bool(disabled):
                skipped.append({"key": key, "email": account.get("email", ""), "reason": f"账号当前已是{target_label}状态"})
                continue
            eligible_accounts.append(account)

        updated_keys: list[str] = []
        for account in eligible_accounts:
            try:
                self._set_account_status(account, disabled)
                self._set_manual_disabled_marker(account, disabled)
                updated_keys.append(account["key"])
            except RuntimeError as exc:
                failed.append({"key": account["key"], "email": account.get("email", ""), "reason": str(exc)})

        refreshed_map: dict[str, dict[str, Any]] = {}
        if updated_keys:
            refreshed = self.payload(force=True)
            refreshed_map = {item["key"]: item for item in refreshed["accounts"]}

        updated_items = []
        for key in updated_keys:
            account = refreshed_map.get(key) or account_map.get(key)
            if account is None:
                continue
            updated_items.append(
                {
                    "key": account["key"],
                    "email": account.get("email", ""),
                    "status": account.get("status", ""),
                    "status_label": account.get("status_label", ""),
                    "disabled": bool(account.get("disabled")),
                }
            )

        return {
            "requested_count": len(unique_keys),
            "eligible_count": len(eligible_accounts),
            "updated_count": len(updated_items),
            "skipped_count": len(skipped),
            "failed_count": len(failed),
            "updated": updated_items,
            "skipped": skipped,
            "failed": failed,
            "target_disabled": bool(disabled),
        }

    def _refresh_codex_quota_for_account(self, account: dict[str, Any]) -> dict[str, Any]:
        if account.get("status") == "deactivated":
            raise RuntimeError("已迁移停用账号不支持刷新额度")

        auth_index = str(account.get("auth_index") or "").strip()
        if not auth_index:
            raise RuntimeError("缺少 auth_index，无法刷新额度")

        chatgpt_account_id = str(account.get("chatgpt_account_id") or "").strip()
        if not chatgpt_account_id:
            raise RuntimeError("缺少 chatgpt_account_id，无法刷新额度")

        payload = {
            "authIndex": auth_index,
            "method": "GET",
            "url": CODEX_USAGE_URL,
            "header": {
                **CODEX_USAGE_HEADERS,
                "Chatgpt-Account-Id": chatgpt_account_id,
            },
        }
        result = self.management.api_call(payload)
        status_code = safe_int(str(result.get("status_code") or result.get("statusCode") or "0"), 0)
        body_payload = self._decode_management_body(result.get("body"))
        refreshed_at = utc_now().isoformat()
        cache_key = self._account_cache_key(account)
        if status_code < 200 or status_code >= 300:
            quota_error = self._normalize_quota_error(body_payload, status_code)
            quota = {
                "provider": "codex",
                "status": "error",
                "email": str(account.get("email") or "").strip(),
                "plan_type": quota_error.get("plan_type") or str(account.get("plan_type") or "").strip(),
                "rate_limit": None,
                "code_review_rate_limit": None,
                "additional_rate_limits": [],
                "credits": None,
                "error": quota_error,
                "refreshed_at": refreshed_at,
                "chatgpt_account_id": chatgpt_account_id,
                "auth_index": auth_index,
            }
            if cache_key:
                self._quota_cache[cache_key] = quota
            self._apply_runtime_state(account)
            if quota_error.get("type") == "token_expired" and not bool(account.get("disabled")):
                try:
                    self._set_account_status(account, True)
                    self._set_manual_disabled_marker(account, False)
                except RuntimeError:
                    pass
            refresh_result = {
                "ok": False,
                "type": quota_error.get("type"),
                "message": quota_error.get("message") or self._api_call_error_message(status_code, body_payload),
            }
            account["quota_refresh_result"] = refresh_result
            return refresh_result

        quota = self._normalize_codex_quota(body_payload)
        quota["refreshed_at"] = refreshed_at
        quota["chatgpt_account_id"] = chatgpt_account_id
        quota["auth_index"] = auth_index

        # Check all rate limit buckets for limit_reached
        limited_buckets: list[str] = []
        rate_limit = quota.get("rate_limit")
        if self._quota_bucket_limit_reached(rate_limit):
            limited_buckets.append(rate_limit.get("label") or "rate_limit")
        code_review_limit = quota.get("code_review_rate_limit")
        if self._quota_bucket_limit_reached(code_review_limit):
            limited_buckets.append(code_review_limit.get("label") or "code_review_rate_limit")
        for additional in (quota.get("additional_rate_limits") or []):
            if self._quota_bucket_limit_reached(additional):
                limited_buckets.append(additional.get("label") or additional.get("key") or "additional")

        if limited_buckets:
            quota["status"] = "error"
            quota["error"] = {
                "type": "usage_limit_reached",
                "message": f"账号仍有限流未重置: {', '.join(limited_buckets)}",
                "status_code": status_code,
            }
            if cache_key:
                self._quota_cache[cache_key] = quota
            self._apply_runtime_state(account)
            refresh_result = {
                "ok": False,
                "type": "usage_limit_reached",
                "message": quota["error"]["message"],
            }
            account["quota_refresh_result"] = refresh_result
            return refresh_result

        quota["status"] = "ok"
        quota["error"] = None
        if cache_key:
            self._quota_cache[cache_key] = quota

        self._apply_runtime_state(account)
        refresh_result = {
            "ok": True,
            "type": None,
            "message": "额度刷新成功",
        }
        account["quota_refresh_result"] = refresh_result
        return refresh_result

    def refresh_codex_quota(self, key: str) -> dict[str, Any]:
        account = self.account_detail(key, force=True)
        if account is None:
            raise KeyError("未找到账号")
        refresh_result = self._refresh_codex_quota_for_account(account)
        updated = self.account_detail(key, force=True)
        if updated is None:
            raise RuntimeError("刷新额度后未找到账号")
        updated["quota_refresh_result"] = refresh_result
        return updated

    def batch_refresh_disabled_accounts(
        self,
        keys: list[str],
        *,
        auto_recover_manual_disabled_only: bool = False,
        auto_recover_success_threshold: int = 1,
    ) -> dict[str, Any]:
        unique_keys = self._unique_keys(keys)
        if not unique_keys:
            return {
                "requested_count": 0,
                "eligible_count": 0,
                "checked_count": 0,
                "recovered_count": 0,
                "limited_count": 0,
                "blocked_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
                "recovered": [],
                "limited": [],
                "blocked": [],
                "skipped": [],
                "failed": [],
            }

        current = self.payload(force=True)
        account_map = {item["key"]: item for item in current["accounts"]}
        eligible_accounts: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        required_successes = max(auto_recover_success_threshold, 1)

        for key in unique_keys:
            account = account_map.get(key)
            if account is None:
                failed.append({"key": key, "reason": "未找到账号"})
                continue
            if account.get("status") == "deactivated":
                failed.append({"key": key, "email": account.get("email", ""), "reason": "已迁移停用账号不支持批量检查额度"})
                continue
            if not bool(account.get("quota_refresh_supported")):
                failed.append({"key": key, "email": account.get("email", ""), "reason": "缺少 auth_index 或 chatgpt_account_id，无法刷新额度"})
                continue
            eligible_accounts.append(account)

        recovered_keys: list[str] = []
        limited: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []

        for account in eligible_accounts:
            try:
                refresh_result = self._refresh_codex_quota_for_account(account)
            except RuntimeError as exc:
                if required_successes > 1:
                    self._set_auto_recover_success_streak(account, 0)
                failed.append({"key": account["key"], "email": account.get("email", ""), "reason": str(exc)})
                continue

            if refresh_result.get("ok"):
                if bool(account.get("disabled")):
                    if auto_recover_manual_disabled_only and not bool(account.get("manual_disabled")):
                        if required_successes > 1:
                            self._set_auto_recover_success_streak(account, 0)
                        skipped.append(
                            {
                                "key": account["key"],
                                "email": account.get("email", ""),
                                "reason": "仅允许自动恢复带 manual_disabled 标记的账号",
                            }
                        )
                        continue
                    if required_successes > 1:
                        next_success_streak = safe_int(str(account.get("auto_recover_success_streak") or "0"), 0) + 1
                        if next_success_streak < required_successes:
                            self._set_auto_recover_success_streak(account, next_success_streak)
                            skipped.append(
                                {
                                    "key": account["key"],
                                    "email": account.get("email", ""),
                                    "reason": f"额度连续正常确认中（{next_success_streak}/{required_successes}），本次暂不自动恢复",
                                }
                            )
                            continue
                        self._set_auto_recover_success_streak(account, required_successes)
                    try:
                        self._set_account_status(account, False)
                        self._set_manual_disabled_marker(account, False)
                        self._set_auto_recover_success_streak(account, 0)
                        recovered_keys.append(account["key"])
                    except RuntimeError as exc:
                        failed.append({"key": account["key"], "email": account.get("email", ""), "reason": f"额度正常但恢复启用失败：{exc}"})
                else:
                    if required_successes > 1:
                        self._set_auto_recover_success_streak(account, 0)
                    recovered_keys.append(account["key"])
                continue

            if required_successes > 1:
                self._set_auto_recover_success_streak(account, 0)
            quota_error = account.get("quota_error") or {}
            item = {
                "key": account["key"],
                "email": account.get("email", ""),
                "type": refresh_result.get("type") or quota_error.get("type") or "",
                "message": refresh_result.get("message") or quota_error.get("message") or "",
                "resets_at": quota_error.get("resets_at"),
                "resets_at_iso": quota_error.get("resets_at_iso"),
            }
            if item["type"] == "usage_limit_reached":
                limited.append(item)
            else:
                blocked.append(item)

        refreshed = self.payload(force=True)
        refreshed_map = {item["key"]: item for item in refreshed["accounts"]}

        recovered = []
        for key in recovered_keys:
            account = refreshed_map.get(key) or account_map.get(key)
            if account is None:
                continue
            recovered.append(
                {
                    "key": account["key"],
                    "email": account.get("email", ""),
                    "status": account.get("status", ""),
                    "status_label": account.get("status_label", ""),
                    "disabled": bool(account.get("disabled")),
                }
            )

        return {
            "requested_count": len(unique_keys),
            "eligible_count": len(eligible_accounts),
            "checked_count": len(eligible_accounts),
            "recovered_count": len(recovered),
            "limited_count": len(limited),
            "blocked_count": len(blocked),
            "skipped_count": len(skipped),
            "failed_count": len(failed),
            "recovered": recovered,
            "limited": limited,
            "blocked": blocked,
            "skipped": skipped,
            "failed": failed,
        }

    # ── 批量刷新 Token ──────────────────────────────────────────────

    def _auth_file_path_for_account(self, account: dict[str, Any]) -> Path | None:
        source_file = str(account.get("source_file") or "").strip()
        if not source_file:
            return None
        resolved = self.settings.auth_dir / source_file
        try:
            resolved = resolved.resolve(strict=False)
        except (OSError, ValueError):
            return None
        try:
            auth_dir_resolved = self.settings.auth_dir.resolve(strict=False)
        except (OSError, ValueError):
            return None
        if not str(resolved).startswith(str(auth_dir_resolved)):
            return None
        return resolved

    def _request_codex_token_refresh(self, refresh_token: str, auth_index: str = "") -> dict[str, Any]:
        form_data = urlencode({
            "client_id": CODEX_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": CODEX_TOKEN_REFRESH_SCOPE,
        })
        api_call_payload: dict[str, Any] = {
            "method": "POST",
            "url": CODEX_TOKEN_URL,
            "header": {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            "data": form_data,
        }
        if auth_index:
            api_call_payload["auth_index"] = auth_index

        try:
            resp = self.management.api_call(api_call_payload)
        except RuntimeError as exc:
            raise RuntimeError(f"Token 刷新请求失败: {exc}") from exc

        status_code = resp.get("status_code", 0)
        body_str = str(resp.get("body") or "")

        if status_code != 200:
            snippet = body_str[:200] if body_str else "(空)"
            raise RuntimeError(f"Token 刷新失败 (HTTP {status_code}): {snippet}")

        try:
            token_data = json.loads(body_str)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError(f"Token 响应解析失败: {exc}") from exc

        if not isinstance(token_data, dict) or not token_data.get("access_token"):
            error_msg = token_data.get("error_description") or token_data.get("error") or body_str[:200]
            raise RuntimeError(f"Token 刷新返回无效数据: {error_msg}")

        return token_data

    def _refresh_codex_token_for_account(self, account: dict[str, Any]) -> dict[str, Any]:
        file_path = self._auth_file_path_for_account(account)
        if file_path is None or not file_path.exists():
            raise RuntimeError("账号文件不存在或路径无效")

        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(f"读取账号文件失败: {exc}") from exc

        current_refresh_token = str(raw.get("refresh_token") or "").strip()
        if not current_refresh_token:
            raise RuntimeError("账号缺少 refresh_token，无法刷新")

        auth_index = str(account.get("auth_index") or "").strip()
        token_resp = self._request_codex_token_refresh(current_refresh_token, auth_index)

        new_access_token = str(token_resp.get("access_token") or "").strip()
        if not new_access_token:
            raise RuntimeError("刷新响应缺少 access_token")

        new_refresh_token = str(token_resp.get("refresh_token") or "").strip()
        new_id_token = str(token_resp.get("id_token") or "").strip()
        expires_in = safe_int(str(token_resp.get("expires_in") or "0"), 0)

        claims = decode_jwt_payload(new_id_token)
        account_id = codex_account_id_from_claims(claims) or str(raw.get("account_id") or "").strip()
        email = str(claims.get("email") or "").strip() or str(raw.get("email") or "").strip()

        now = utc_now()
        if expires_in > 0:
            from datetime import timedelta
            expire_time = now + timedelta(seconds=expires_in)
        else:
            expire_time = now

        raw["access_token"] = new_access_token
        if new_refresh_token:
            raw["refresh_token"] = new_refresh_token
        if new_id_token:
            raw["id_token"] = new_id_token
        if account_id:
            raw["account_id"] = account_id
        if email:
            raw["email"] = email
        raw["expired"] = expire_time.replace(microsecond=0).isoformat()
        raw["last_refresh"] = now.replace(microsecond=0).isoformat()
        raw["type"] = "codex"

        try:
            file_path.write_text(
                json.dumps(raw, ensure_ascii=False, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise RuntimeError(f"写入账号文件失败: {exc}") from exc

        return {
            "ok": True,
            "access_token_updated": True,
            "refresh_token_updated": bool(new_refresh_token),
            "id_token_updated": bool(new_id_token),
            "account_id": account_id,
            "email": email,
            "expires_at": raw["expired"],
        }

    def batch_refresh_tokens(self, keys: list[str]) -> dict[str, Any]:
        unique_keys = self._unique_keys(keys)
        if not unique_keys:
            return {
                "requested_count": 0,
                "eligible_count": 0,
                "refreshed_count": 0,
                "failed_count": 0,
                "skipped_count": 0,
                "refreshed": [],
                "failed": [],
                "skipped": [],
            }

        current = self.payload(force=True)
        account_map = {item["key"]: item for item in current["accounts"]}
        eligible_accounts: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []

        for key in unique_keys:
            account = account_map.get(key)
            if account is None:
                failed.append({"key": key, "reason": "未找到账号"})
                continue
            if account.get("status") == "deactivated":
                failed.append({"key": key, "email": account.get("email", ""), "reason": "已迁移停用账号不支持刷新 Token"})
                continue
            if not bool(account.get("has_refresh_token")):
                skipped.append({"key": key, "email": account.get("email", ""), "reason": "账号缺少 refresh_token"})
                continue
            eligible_accounts.append(account)

        refreshed_keys: list[str] = []

        for account in eligible_accounts:
            try:
                result = self._refresh_codex_token_for_account(account)
            except RuntimeError as exc:
                self._set_auto_recover_success_streak(account, 0)
                self._mark_token_expired_and_disable(account)
                failed.append({
                    "key": account["key"],
                    "email": account.get("email", ""),
                    "reason": f"Token 刷新失败: {exc}",
                    "auto_disabled": True,
                })
                continue
            except Exception as exc:
                self._set_auto_recover_success_streak(account, 0)
                self._mark_token_expired_and_disable(account)
                failed.append({
                    "key": account["key"],
                    "email": account.get("email", ""),
                    "reason": f"Token 刷新异常: {exc}",
                    "auto_disabled": True,
                })
                continue

            refreshed_keys.append(account["key"])

        refreshed_payload = self.payload(force=True)
        refreshed_map = {item["key"]: item for item in refreshed_payload["accounts"]}

        refreshed: list[dict[str, Any]] = []
        for key in refreshed_keys:
            account = refreshed_map.get(key) or account_map.get(key)
            if account is None:
                continue
            refreshed.append({
                "key": account["key"],
                "email": account.get("email", ""),
                "status": account.get("status", ""),
                "status_label": account.get("status_label", ""),
                "disabled": bool(account.get("disabled")),
            })

        return {
            "requested_count": len(unique_keys),
            "eligible_count": len(eligible_accounts),
            "refreshed_count": len(refreshed),
            "failed_count": len(failed),
            "skipped_count": len(skipped),
            "refreshed": refreshed,
            "failed": failed,
            "skipped": skipped,
        }

    def refresh_manual_disabled_accounts(self, limit: int = 0) -> dict[str, Any]:
        with self._manual_disabled_refresh_lock:
            candidates, manual_disabled_total, eligible_disabled_total, selection_mode = self._manual_disabled_refresh_candidates(force=True, limit=limit)
            result = self.batch_refresh_disabled_accounts(
                [item["key"] for item in candidates],
                auto_recover_manual_disabled_only=True,
                auto_recover_success_threshold=AUTO_RECOVER_SUCCESS_STREAK_REQUIRED,
            )
            result["manual_disabled_total"] = manual_disabled_total
            result["eligible_disabled_total"] = eligible_disabled_total
            result["selected_count"] = len(candidates)
            result["batch_size_limit"] = max(limit, 0)
            result["selection_mode"] = selection_mode
            result["auto_recover_success_threshold"] = AUTO_RECOVER_SUCCESS_STREAK_REQUIRED
            return result


class ManualDisabledRefreshWorker:
    """定时扫描“手动停用”的账号并尝试刷新额度，成功后自动恢复启用。"""

    def __init__(self, repo: AccountRepository, settings: Settings) -> None:
        self.repo = repo
        self.enabled = settings.manual_disabled_refresh_enabled
        self.interval_seconds = settings.manual_disabled_refresh_interval_seconds
        self.batch_size = settings.manual_disabled_refresh_batch_size
        self.startup_delay_seconds = settings.manual_disabled_refresh_startup_delay_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._run_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._state: dict[str, Any] = {
            "enabled": self.enabled,
            "running": False,
            "interval_seconds": self.interval_seconds,
            "batch_size": self.batch_size,
            "startup_delay_seconds": self.startup_delay_seconds,
            "last_started_at": None,
            "last_finished_at": None,
            "next_run_at": None,
            "last_error": None,
            "last_result": None,
        }

    def _result_summary(self, result: dict[str, Any] | None) -> dict[str, Any]:
        if not result:
            return {}

        def sample_items(items: list[dict[str, Any]] | None, keys: list[str], limit: int = 5) -> list[dict[str, Any]]:
            samples: list[dict[str, Any]] = []
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                sample: dict[str, Any] = {}
                for key in keys:
                    value = item.get(key)
                    if value is None or value == "":
                        continue
                    sample[key] = value
                if sample:
                    samples.append(sample)
                if len(samples) >= limit:
                    break
            return samples

        blocked_by_type: dict[str, int] = {}
        for item in result.get("blocked") or []:
            error_type = str(item.get("type") or "unknown").strip() or "unknown"
            blocked_by_type[error_type] = blocked_by_type.get(error_type, 0) + 1
        return {
            "selection_mode": result.get("selection_mode"),
            "manual_disabled_total": result.get("manual_disabled_total"),
            "eligible_disabled_total": result.get("eligible_disabled_total"),
            "selected_count": result.get("selected_count"),
            "checked_count": result.get("checked_count"),
            "recovered_count": result.get("recovered_count"),
            "limited_count": result.get("limited_count"),
            "blocked_count": result.get("blocked_count"),
            "skipped_count": result.get("skipped_count"),
            "failed_count": result.get("failed_count"),
            "trigger": result.get("trigger"),
            "blocked_by_type": blocked_by_type,
            "blocked_samples": sample_items(result.get("blocked"), ["email", "type", "message"]),
            "skipped_samples": sample_items(result.get("skipped"), ["email", "reason"]),
            "failed_samples": sample_items(result.get("failed"), ["email", "reason"]),
        }

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            data = json.loads(json.dumps(self._state, ensure_ascii=False))
        data["summary"] = self._result_summary(data.get("last_result"))
        return data

    def start(self) -> None:
        if not self.enabled:
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            name="manual-disabled-refresh-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _sleep(self, seconds: int) -> bool:
        if seconds <= 0:
            return self._stop_event.is_set()
        return self._stop_event.wait(seconds)

    def _run_loop(self) -> None:
        if self.startup_delay_seconds > 0:
            with self._state_lock:
                self._state["next_run_at"] = (utc_now() + timedelta(seconds=self.startup_delay_seconds)).isoformat()
            if self._sleep(self.startup_delay_seconds):
                return

        while not self._stop_event.is_set():
            self.run_once(trigger="scheduled")
            if self._stop_event.is_set():
                return
            with self._state_lock:
                self._state["next_run_at"] = (utc_now() + timedelta(seconds=self.interval_seconds)).isoformat()
            if self._sleep(self.interval_seconds):
                return

    def run_once(self, trigger: str = "manual") -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "reason": "worker_disabled"}
        if not self._run_lock.acquire(blocking=False):
            return {"ok": False, "reason": "worker_busy"}

        started_at = utc_now().isoformat()
        with self._state_lock:
            self._state["running"] = True
            self._state["last_started_at"] = started_at
            self._state["last_error"] = None

        try:
            result = self.repo.refresh_manual_disabled_accounts(limit=self.batch_size)
            result["trigger"] = trigger
            finished_at = utc_now().isoformat()
            with self._state_lock:
                self._state["running"] = False
                self._state["last_finished_at"] = finished_at
                self._state["last_result"] = result
            if result.get("selected_count") or result.get("recovered_count") or result.get("failed_count"):
                print(
                    "[CPA Panel] manual-disabled refresh "
                    f"selected={result.get('selected_count', 0)} "
                    f"recovered={result.get('recovered_count', 0)} "
                    f"limited={result.get('limited_count', 0)} "
                    f"blocked={result.get('blocked_count', 0)} "
                    f"skipped={result.get('skipped_count', 0)} "
                    f"failed={result.get('failed_count', 0)}",
                    flush=True,
                )
            return result
        except Exception as exc:  # noqa: BLE001
            finished_at = utc_now().isoformat()
            with self._state_lock:
                self._state["running"] = False
                self._state["last_finished_at"] = finished_at
                self._state["last_error"] = str(exc)
            print(f"[CPA Panel] manual-disabled refresh failed: {exc}", flush=True)
            raise
        finally:
            self._run_lock.release()

    def run_now(self, trigger: str = "manual_api") -> dict[str, Any]:
        self.run_once(trigger=trigger)
        return self.snapshot()


class PanelHandler(BaseHTTPRequestHandler):
    server_version = "CPACodexPanel/0.1"

    @property
    def settings(self) -> Settings:
        return self.server.settings  # type: ignore[attr-defined]

    @property
    def sessions(self) -> SessionStore:
        return self.server.sessions  # type: ignore[attr-defined]

    @property
    def repo(self) -> AccountRepository:
        return self.server.repo  # type: ignore[attr-defined]

    @property
    def disabled_refresh_worker(self) -> ManualDisabledRefreshWorker | None:
        return getattr(self.server, "disabled_refresh_worker", None)

    def _overview_payload(self, force: bool = False) -> dict[str, Any]:
        payload = self.repo.overview(force=force)
        worker = self.disabled_refresh_worker
        payload["disabled_refresh"] = worker.snapshot() if worker is not None else {"enabled": False}
        return payload

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._serve_file(STATIC_DIR / "index.html")
            return
        if parsed.path.startswith("/static/"):
            relative = parsed.path.removeprefix("/static/")
            self._serve_file(STATIC_DIR / relative)
            return
        if parsed.path == "/api/overview":
            if not self._require_auth():
                return
            self._send_json(self._overview_payload())
            return
        if parsed.path == "/api/disabled-refresh/latest-result":
            if not self._require_auth():
                return
            worker = self.disabled_refresh_worker
            if worker is None or not worker.enabled:
                self._send_json({"error": "disabled refresh worker 未启用"}, status=HTTPStatus.BAD_REQUEST)
                return
            snapshot = worker.snapshot()
            if not snapshot.get("last_result"):
                self._send_json({"error": "当前还没有 disabled refresh 结果"}, status=HTTPStatus.BAD_REQUEST)
                return
            body = json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8")
            self._send_bytes(
                body,
                content_type="application/json",
                filename="disabled-refresh-latest-result.json",
            )
            return
        if parsed.path == "/api/accounts":
            if not self._require_auth():
                return
            params = parse_qs(parsed.query)
            self._send_json(
                self.repo.accounts(
                    q=params.get("q", [""])[0],
                    status=params.get("status", [""])[0],
                    quota_error_type=params.get("quota_error_type", [""])[0],
                    group=params.get("group", [""])[0],
                    page=safe_int(params.get("page", ["1"])[0], 1),
                    page_size=safe_int(params.get("page_size", ["50"])[0], 50),
                    sort_by=params.get("sort_by", [""])[0],
                    sort_order=params.get("sort_order", [""])[0],
                )
            )
            return
        if parsed.path.startswith("/api/accounts/"):
            if not self._require_auth():
                return
            key = unquote(parsed.path.removeprefix("/api/accounts/"))
            detail = self.repo.account_detail(key, force=True)
            if detail is None:
                self._send_json({"error": "未找到账号"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json(detail)
            return

        self._send_json({"error": "接口不存在"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/login":
            payload = self._read_json()
            token = str(payload.get("token", ""))
            if not token or not secrets.compare_digest(token, self.settings.admin_token):
                self._send_json({"error": "口令错误"}, status=HTTPStatus.UNAUTHORIZED)
                return
            sid = self.sessions.create()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie", self._session_cookie_header(sid))
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8"))
            return

        if parsed.path == "/api/logout":
            sid = self._session_id()
            self.sessions.revoke(sid)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8"))
            return

        if parsed.path == "/api/refresh":
            if not self._require_auth():
                return
            self._send_json(self._overview_payload(force=True))
            return

        if parsed.path == "/api/disabled-refresh/run":
            if not self._require_auth():
                return
            worker = self.disabled_refresh_worker
            if worker is None or not worker.enabled:
                self._send_json({"error": "disabled refresh worker 未启用"}, status=HTTPStatus.BAD_REQUEST)
                return
            snapshot = worker.run_now(trigger="manual_api")
            self._send_json(snapshot)
            return

        if parsed.path == "/api/accounts/export-disabled-unrefreshed":
            if not self._require_auth():
                return
            try:
                package = self.repo.build_disabled_unrefreshed_archive()
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            delivered = self._send_bytes(
                package["archive_bytes"],
                content_type="application/zip",
                filename=package["archive_name"],
                extra_headers={
                    "X-Archive-Count": str(package["account_count"]),
                    "X-Archive-Skipped-Count": str(len(package.get("failed") or [])),
                },
            )
            if not delivered:
                return
            delete_result = self.repo.delete_disabled_unrefreshed_accounts_after_export(package["accounts"])
            print(
                "[CPA Panel] exported disabled-unrefreshed archive "
                f"packed={package['account_count']} "
                f"deleted={delete_result.get('deleted_count', 0)} "
                f"failed={delete_result.get('failed_count', 0)}",
                flush=True,
            )
            return

        if parsed.path.startswith("/api/accounts/") and parsed.path.endswith("/status"):
            if not self._require_auth():
                return
            key = unquote(parsed.path.removeprefix("/api/accounts/").removesuffix("/status"))
            payload = self._read_json()
            disabled = payload.get("disabled")
            if not isinstance(disabled, bool):
                self._send_json({"error": "disabled 必须为布尔值"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                updated = self.repo.toggle_account_status(key, disabled)
            except KeyError:
                self._send_json({"error": "未找到账号"}, status=HTTPStatus.NOT_FOUND)
                return
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
                return
            self._send_json(updated)
            return

        if parsed.path.startswith("/api/accounts/") and parsed.path.endswith("/quota-refresh"):
            if not self._require_auth():
                return
            key = unquote(parsed.path.removeprefix("/api/accounts/").removesuffix("/quota-refresh"))
            try:
                updated = self.repo.refresh_codex_quota(key)
            except KeyError:
                self._send_json({"error": "未找到账号"}, status=HTTPStatus.NOT_FOUND)
                return
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
                return
            self._send_json(updated)
            return

        if parsed.path == "/api/accounts/batch-status":
            if not self._require_auth():
                return
            payload = self._read_json()
            keys = payload.get("keys", [])
            disabled = payload.get("disabled")
            if not isinstance(keys, list):
                self._send_json({"error": "keys 必须为数组"}, status=HTTPStatus.BAD_REQUEST)
                return
            if not isinstance(disabled, bool):
                self._send_json({"error": "disabled 必须为布尔值"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                result = self.repo.batch_toggle_accounts(keys, disabled)
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
                return
            status = HTTPStatus.OK if result["updated_count"] > 0 else HTTPStatus.BAD_REQUEST
            self._send_json(result, status=status)
            return

        if parsed.path == "/api/accounts/batch-token-refresh":
            if not self._require_auth():
                return
            payload = self._read_json()
            keys = payload.get("keys", [])
            if not isinstance(keys, list):
                self._send_json({"error": "keys 必须为数组"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                result = self.repo.batch_refresh_tokens(keys)
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
                return
            status = HTTPStatus.OK if result["eligible_count"] > 0 or result["requested_count"] == 0 else HTTPStatus.BAD_REQUEST
            self._send_json(result, status=status)
            return

        if parsed.path == "/api/accounts/batch-refresh-recover":
            if not self._require_auth():
                return
            payload = self._read_json()
            keys = payload.get("keys", [])
            if not isinstance(keys, list):
                self._send_json({"error": "keys 必须为数组"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                result = self.repo.batch_refresh_disabled_accounts(keys)
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
                return
            status = HTTPStatus.OK if result["checked_count"] > 0 else HTTPStatus.BAD_REQUEST
            self._send_json(result, status=status)
            return

        if parsed.path == "/api/accounts/batch-delete":
            if not self._require_auth():
                return
            payload = self._read_json()
            keys = payload.get("keys", [])
            if not isinstance(keys, list):
                self._send_json({"error": "keys 必须为数组"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                result = self.repo.delete_accounts(keys)
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
                return
            status = HTTPStatus.OK if result["deleted_count"] > 0 else HTTPStatus.BAD_REQUEST
            self._send_json(result, status=status)
            return

        if parsed.path == "/api/accounts/delete-all-deactivated":
            if not self._require_auth():
                return
            try:
                result = self.repo.delete_all_deactivated_accounts()
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
                return
            status = HTTPStatus.OK if result["deleted_count"] > 0 or result["available_count"] == 0 else HTTPStatus.BAD_REQUEST
            self._send_json(result, status=status)
            return

        self._send_json({"error": "接口不存在"}, status=HTTPStatus.NOT_FOUND)

    def do_PATCH(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/accounts/") or not parsed.path.endswith("/meta"):
            self._send_json({"error": "接口不存在"}, status=HTTPStatus.NOT_FOUND)
            return
        if not self._require_auth():
            return
        key = unquote(parsed.path.removeprefix("/api/accounts/").removesuffix("/meta"))
        payload = self._read_json()
        updated = self.repo.update_meta(key, payload)
        if updated is None:
            self._send_json({"error": "未找到账号"}, status=HTTPStatus.NOT_FOUND)
            return
        self._send_json(updated)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/accounts/"):
            self._send_json({"error": "接口不存在"}, status=HTTPStatus.NOT_FOUND)
            return
        if not self._require_auth():
            return
        key = unquote(parsed.path.removeprefix("/api/accounts/"))
        if not key:
            self._send_json({"error": "缺少账号标识"}, status=HTTPStatus.BAD_REQUEST)
            return
        try:
            result = self.repo.delete_accounts([key])
        except RuntimeError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
            return
        if result["deleted_count"] > 0:
            self._send_json(result)
            return
        failed = result.get("failed") or []
        first_reason = failed[0].get("reason") if failed else "删除失败"
        status = HTTPStatus.NOT_FOUND if first_reason == "未找到账号" else HTTPStatus.BAD_REQUEST
        self._send_json({"error": first_reason, **result}, status=status)

    def _require_auth(self) -> bool:
        sid = self._session_id()
        if self.sessions.valid(sid):
            return True
        self._send_json({"error": "未登录"}, status=HTTPStatus.UNAUTHORIZED)
        return False

    def _session_id(self) -> str | None:
        cookie_header = self.headers.get("Cookie")
        if not cookie_header:
            return None
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def _session_cookie_header(self, sid: str) -> str:
        max_age = self.settings.session_ttl_minutes * 60
        expires_at = utc_now() + timedelta(seconds=max_age)
        expires_text = format_datetime(expires_at, usegmt=True)
        return f"{SESSION_COOKIE}={sid}; Path=/; Max-Age={max_age}; Expires={expires_text}; HttpOnly; SameSite=Strict"

    def _read_json(self) -> dict[str, Any]:
        length = safe_int(self.headers.get("Content-Length"), 0)
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"error": "文件不存在"}, status=HTTPStatus.NOT_FOUND)
            return
        content_type, _ = mimetypes.guess_type(path.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type or 'text/plain'}; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(path.read_bytes())

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(
        self,
        payload: bytes,
        *,
        content_type: str,
        filename: str | None = None,
        extra_headers: dict[str, str] | None = None,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> bool:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(payload)
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            print(f"[CPA Panel] download interrupted: {exc}", flush=True)
            return False

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        # 保持输出简洁，方便面板以 systemd 运行。
        return


def main() -> None:
    settings = Settings.load()
    repo = AccountRepository(settings)
    sessions = SessionStore(settings.session_ttl_minutes, settings.session_store_path)
    disabled_refresh_worker = ManualDisabledRefreshWorker(repo, settings)

    server = ThreadingHTTPServer((settings.host, settings.port), PanelHandler)
    server.settings = settings  # type: ignore[attr-defined]
    server.repo = repo  # type: ignore[attr-defined]
    server.sessions = sessions  # type: ignore[attr-defined]
    server.disabled_refresh_worker = disabled_refresh_worker  # type: ignore[attr-defined]

    print(f"[CPA Panel] listening on http://{settings.host}:{settings.port}", flush=True)
    print(f"[CPA Panel] auth dir: {settings.auth_dir}", flush=True)
    print(f"[CPA Panel] deactivated dir: {settings.deactivated_dir}", flush=True)
    if settings.manual_disabled_refresh_enabled:
        print(
            "[CPA Panel] manual-disabled refresh enabled: "
            f"interval={settings.manual_disabled_refresh_interval_seconds}s "
            f"batch_size={settings.manual_disabled_refresh_batch_size or 'all'} "
            f"startup_delay={settings.manual_disabled_refresh_startup_delay_seconds}s",
            flush=True,
        )
        disabled_refresh_worker.start()
    else:
        print("[CPA Panel] manual-disabled refresh disabled", flush=True)
    if settings.generated_token:
        print("[CPA Panel] 未检测到 CPA_PANEL_TOKEN，已临时生成管理员口令：", flush=True)
        print(settings.admin_token, flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        disabled_refresh_worker.stop()
        server.server_close()


if __name__ == "__main__":
    main()
