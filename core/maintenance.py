"""Scheduled cleanup and local recovery backups for persistent monitor state."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

from config import settings
from core.monitor_store import MonitorStore
from core.radar_store import RadarStore
from core.runtime import _record_fetch_success, _with_run_summary
from utils.notifier import log_info


class OffsiteBackupError(RuntimeError):
    """Raised when an explicitly enabled offsite recovery copy cannot complete."""


def _backup_database(source_path: str, backup_dir: str, now: datetime) -> Path:
    """Create a consistent SQLite backup without copying WAL files directly."""
    source = Path(source_path)
    destination_dir = Path(backup_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / f"monitor-{now.strftime('%Y%m%d')}.sqlite3"
    temporary = target.with_suffix(".sqlite3.tmp")

    with sqlite3.connect(source) as source_connection:
        with sqlite3.connect(temporary) as backup_connection:
            source_connection.backup(backup_connection)
    os.replace(temporary, target)
    return target


def _prune_old_backups(backup_dir: str, retention_days: int, now: datetime) -> int:
    """Delete only dated local recovery artifacts older than retention."""
    cutoff = now.timestamp() - max(1, retention_days) * 24 * 60 * 60
    removed = 0
    for pattern in ("monitor-*.sqlite3", "stock-news-state-*.zip"):
        for path in Path(backup_dir).glob(pattern):
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            removed += 1
    return removed


def _add_existing_file(
    archive: zipfile.ZipFile, source: Path, archive_name: str
) -> None:
    """Add a state file only when it exists; configuration and secrets stay out."""
    if source.is_file():
        archive.write(source, archive_name)


def _build_recovery_archive(database_backup: Path, backup_dir: str, now: datetime) -> Path:
    """Bundle the restorable state files around the consistent SQLite backup."""
    destination_dir = Path(backup_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    filename = f"stock-news-state-{now.strftime('%Y%m%d-%H%M%S')}.zip"
    target = destination_dir / filename
    temporary = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _add_existing_file(archive, database_backup, "monitor.db")
        _add_existing_file(archive, Path(settings.HISTORY_FILE), "history.csv")
        _add_existing_file(archive, Path(settings.PICK_FILE), "stock_pick.json")
        _add_existing_file(archive, Path(settings.METRICS_FILE), "runtime_metrics.json")
        _add_existing_file(archive, Path(settings.RUN_STATUS_FILE), "runtime_status.json")
        status_dir = Path(settings.RUN_STATUS_DIR)
        if status_dir.is_dir():
            for status_file in status_dir.rglob("*.json"):
                _add_existing_file(
                    archive,
                    status_file,
                    str(Path("runtime_status") / status_file.relative_to(status_dir)),
                )
    os.replace(temporary, target)
    return target


def _upload_offsite_backup(archive: Path) -> str | None:
    """Copy a recovery archive with rclone, failing maintenance if enabled fails."""
    if not settings.OFFSITE_BACKUP_ENABLED:
        return None
    target = settings.OFFSITE_BACKUP_RCLONE_TARGET.rstrip("/")
    if not target or ":" not in target:
        raise OffsiteBackupError("未配置有效的 OFFSITE_BACKUP_RCLONE_TARGET")
    if shutil.which("rclone") is None:
        raise OffsiteBackupError("未找到 rclone，无法执行已启用的异地备份")

    destination = f"{target}/{archive.name}"
    try:
        completed = subprocess.run(
            ["rclone", "copyto", str(archive), destination, "--retries", "2"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=settings.OFFSITE_BACKUP_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OffsiteBackupError(
            f"异地备份命令失败: {exc.__class__.__name__}"
        ) from exc
    if completed.returncode != 0:
        raise OffsiteBackupError(
            f"异地备份上传失败: rclone exit {completed.returncode}"
        )
    log_info(f"异地恢复备份已验证上传: {archive.name}")
    return destination


@_with_run_summary("maintenance")
def run_maintenance() -> None:
    """Prune stale monitor data and create one local recovery backup."""
    now = datetime.now(settings.SHA_TZ)
    monitor_store = MonitorStore(settings.MONITOR_DB_FILE)
    radar_store = RadarStore(settings.MONITOR_DB_FILE)
    monitor_store.initialize()
    radar_store.initialize()

    monitor_deleted = monitor_store.prune(now, settings.DB_RETENTION_DAYS)
    radar_deleted = radar_store.prune(now, settings.DB_RETENTION_DAYS)
    backup = _backup_database(settings.MONITOR_DB_FILE, settings.STATE_BACKUP_DIR, now)
    recovery_archive = _build_recovery_archive(
        backup, settings.STATE_BACKUP_DIR, now
    )
    offsite_destination = _upload_offsite_backup(recovery_archive)
    old_backups_deleted = _prune_old_backups(
        settings.STATE_BACKUP_DIR, settings.DB_BACKUP_RETENTION_DAYS, now
    )
    _record_fetch_success(True)
    log_info(
        "状态维护完成: "
        f"删除={monitor_deleted | radar_deleted}, backup={backup.name}, "
        f"recovery_archive={recovery_archive.name}, "
        f"offsite={'uploaded' if offsite_destination else 'disabled'}, "
        f"old_backups_deleted={old_backups_deleted}"
    )
