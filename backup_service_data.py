#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import os
import socket
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
ENV_FILE = Path(os.getenv("CPA_BACKUP_ENV_FILE", os.getenv("CPA_PANEL_ENV_FILE", str(APP_DIR / ".env")))).expanduser()
DEFAULT_BACKUP_DIR = Path("/root/cpa-service-backups")


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def safe_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def parse_path_list(raw_value: str) -> list[Path]:
    results: list[Path] = []
    seen: set[str] = set()
    for part in raw_value.split(","):
        candidate = part.strip()
        if not candidate:
            continue
        normalized = str(Path(candidate).expanduser())
        if normalized in seen:
            continue
        seen.add(normalized)
        results.append(Path(normalized))
    return results


def build_default_include_paths() -> list[Path]:
    auth_dir = Path(os.getenv("CPA_PANEL_AUTH_DIR", "/root/.cli-proxy-api"))
    deactivated_dir = Path(os.getenv("CPA_PANEL_DEACTIVATED_DIR", "/root/account-deactivated"))
    cliproxy_config_path = Path(os.getenv("CPA_PANEL_CLIPROXY_CONFIG", "/root/cliproxyapi/config.yaml"))
    cliproxy_root = cliproxy_config_path.parent if cliproxy_config_path.name else Path("/root/cliproxyapi")
    return [
        auth_dir,
        deactivated_dir,
        APP_DIR,
        cliproxy_root,
    ]


def collect_include_paths() -> tuple[list[Path], list[str]]:
    raw_override = os.getenv("CPA_BACKUP_INCLUDE_PATHS", "").strip()
    candidates = parse_path_list(raw_override) if raw_override else build_default_include_paths()
    resolved: list[Path] = []
    seen: set[str] = set()
    missing: list[str] = []

    for path in candidates:
        normalized = path.expanduser()
        key = str(normalized)
        if key in seen:
            continue
        seen.add(key)
        if normalized.exists():
            resolved.append(normalized)
        else:
            missing.append(str(normalized))
    return resolved, missing


def arcname_for(path: Path) -> str:
    try:
        return str(path.relative_to("/root"))
    except ValueError:
        return str(path.relative_to("/"))


def next_archive_path(backup_dir: Path, prefix: str) -> tuple[Path, Path]:
    base_name = f"{prefix}_{datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')}"
    archive = backup_dir / f"{base_name}.tar.gz"
    manifest = backup_dir / f"{base_name}.json"
    index = 1
    while archive.exists() or manifest.exists():
        archive = backup_dir / f"{base_name}_{index}.tar.gz"
        manifest = backup_dir / f"{base_name}_{index}.json"
        index += 1
    return archive, manifest


def cleanup_expired_backups(backup_dir: Path, retention_days: int) -> list[str]:
    if retention_days <= 0:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed: list[str] = []
    for path in sorted(backup_dir.glob("cpa_full_*")):
        try:
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if modified_at >= cutoff:
            continue
        try:
            path.unlink()
            removed.append(str(path))
        except OSError:
            continue
    return removed


def main() -> int:
    load_env_file(ENV_FILE)

    backup_dir = Path(os.getenv("CPA_BACKUP_DIR", str(DEFAULT_BACKUP_DIR))).expanduser()
    retention_days = safe_int(os.getenv("CPA_BACKUP_RETENTION_DAYS"), 14)
    include_paths, missing_paths = collect_include_paths()
    backup_dir.mkdir(parents=True, exist_ok=True)

    lock_path = backup_dir / ".backup.lock"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[CPA Backup] 发现正在执行中的备份任务，跳过本次运行。")
            return 1

        archive_path, manifest_path = next_archive_path(backup_dir, "cpa_full")
        started_at = datetime.now(timezone.utc)

        with tarfile.open(archive_path, "w:gz") as tar:
            for path in include_paths:
                tar.add(path, arcname=arcname_for(path))

        finished_at = datetime.now(timezone.utc)
        cleanup_removed = cleanup_expired_backups(backup_dir, retention_days)

        manifest = {
            "created_at": finished_at.isoformat(),
            "started_at": started_at.isoformat(),
            "hostname": socket.gethostname(),
            "archive": str(archive_path),
            "archive_size_bytes": archive_path.stat().st_size,
            "backup_dir": str(backup_dir),
            "retention_days": retention_days,
            "included_paths": [str(path) for path in include_paths],
            "missing_paths": missing_paths,
            "cleanup_removed": cleanup_removed,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"[CPA Backup] 备份完成：{archive_path}")
        print(f"[CPA Backup] 归档大小：{archive_path.stat().st_size} bytes")
        if missing_paths:
            print(f"[CPA Backup] 以下路径不存在，已跳过：{', '.join(missing_paths)}")
        if cleanup_removed:
            print(f"[CPA Backup] 已清理过期备份 {len(cleanup_removed)} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
