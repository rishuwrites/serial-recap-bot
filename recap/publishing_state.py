"""Atomic, per-show social publishing state kept separate from YouTube episode state."""
import datetime as dt
import json
import os
import tempfile
from contextlib import contextmanager

import config as S


def _path(show, base=None):
    root = base or S.BASE
    return os.path.join(root, "state", f"{show}_publishing.json")


@contextmanager
def _locked(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "a+b") as lock_file:
        if os.name == "nt":
            import msvcrt
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read(path):
    try:
        with open(path, encoding="utf-8") as state_file:
            data = json.load(state_file)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid publishing state format in {path}")
    return data


def _write(path, data):
    fd, temp_path = tempfile.mkstemp(prefix=".publishing-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as state_file:
            json.dump(data, state_file, ensure_ascii=False, indent=2)
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def get_entry(show, source_video_id, base=None):
    path = _path(show, base)
    with _locked(path):
        data = _read(path)
    entry = data.get(source_video_id, {})
    return entry if isinstance(entry, dict) else {}


def platform_status(show, source_video_id, platform, base=None):
    entry = get_entry(show, source_video_id, base)
    return entry.get("platforms", {}).get(platform, {}).get("status", "")


def claim(show, source_video_id, platform, *, episode="", base=None):
    path = _path(show, base)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with _locked(path):
        data = _read(path)
        entry = data.setdefault(source_video_id, {"source_video_id": source_video_id, "platforms": {}})
        entry.setdefault("platforms", {})
        previous = entry["platforms"].get(platform, {})
        if previous.get("status") in ("success", "in_progress", "uncertain"):
            return False
        entry["platforms"][platform] = {
            "status": "in_progress",
            "attempts": int(previous.get("attempts", 0)) + 1,
            "updated_at": now,
            "media_id": previous.get("media_id", ""),
            "error": "",
        }
        if episode:
            entry["source_episode"] = str(episode)
        entry["updated_at"] = now
        _write(path, data)
    return True


def update(show, source_video_id, platform, status, *, episode="", media_id="", error="", base=None):
    if status not in ("in_progress", "success", "failed", "uncertain"):
        raise ValueError(f"Unsupported publishing status: {status}")
    path = _path(show, base)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with _locked(path):
        data = _read(path)
        entry = data.setdefault(source_video_id, {"source_video_id": source_video_id, "platforms": {}})
        entry.setdefault("platforms", {})
        previous = entry["platforms"].get(platform, {})
        result = {
            "status": status,
            "attempts": int(previous.get("attempts", 0)) + (status == "in_progress"),
            "updated_at": now,
            "media_id": media_id or previous.get("media_id", ""),
            "error": str(error or "")[:500],
        }
        entry["platforms"][platform] = result
        if episode:
            entry["source_episode"] = str(episode)
        entry["updated_at"] = now
        _write(path, data)
    return result


def failed_platforms(show, platforms, base=None):
    path = _path(show, base)
    with _locked(path):
        data = _read(path)
    candidates = []
    for source_video_id, entry in data.items():
        states = entry.get("platforms", {}) if isinstance(entry, dict) else {}
        if any(states.get(platform, {}).get("status") == "failed" for platform in platforms):
            candidates.append((entry.get("updated_at", ""), source_video_id))
    return max(candidates, default=("", ""))[1]
