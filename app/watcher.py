from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from redis.asyncio import Redis

from .config import settings
from .redis_keys import ACTIVE_JOBS, PAUSE_FLAG, YTDLP_UPDATED_VERSION

log = logging.getLogger(__name__)
NEEDS_UPDATE_RE = re.compile(r"(available|newer|upgrade|update)", re.IGNORECASE)
NO_UPDATE_RE = re.compile(r"(up[- ]to[- ]date|latest version)", re.IGNORECASE)
YTDLP_BIN = Path(settings.ytdlp_bin)


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)


def _ensure_shared_binary() -> None:
    YTDLP_BIN.parent.mkdir(parents=True, exist_ok=True)
    if YTDLP_BIN.exists():
        return
    source = shutil.which("yt-dlp")
    if not source:
        raise RuntimeError("yt-dlp is not installed in the image")
    shutil.copy2(source, YTDLP_BIN)
    YTDLP_BIN.chmod(YTDLP_BIN.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _version() -> str:
    _ensure_shared_binary()
    proc = _run([str(YTDLP_BIN), "--version"])
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp version check failed: {proc.stdout.strip()}")
    return proc.stdout.strip()


import json
import urllib.request

def yt_dlp_update_needed() -> bool:
    _ensure_shared_binary()
    try:
        req = urllib.request.urlopen("https://pypi.org/pypi/yt-dlp/json", timeout=10)
        data = json.loads(req.read())
        latest_version = data["info"]["version"]
        current_version = _version().strip()
        
        def normalize_version(v: str) -> tuple:
            return tuple(int(x) for x in v.split('.') if x.isdigit())
            
        latest_tuple = normalize_version(latest_version)
        current_tuple = normalize_version(current_version)
        
        log.info("yt-dlp version check: current=%s pypi=%s", current_version, latest_version)
        if not latest_tuple or not current_tuple:
            return False
            
        return latest_tuple > current_tuple
    except Exception as exc:
        log.warning("failed to check pypi for yt-dlp update: %s", exc)
        return False


async def wait_for_idle(redis: Redis) -> None:
    while True:
        active = await redis.hlen(ACTIVE_JOBS)
        if active == 0:
            return
        log.info("waiting for %s active job(s) before yt-dlp update", active)
        await asyncio.sleep(settings.ytdlp_update_poll_seconds)


def update_ytdlp() -> str:
    _ensure_shared_binary()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "yt-dlp"
        proc = _run([
            "python",
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--target",
            tmp,
            "yt-dlp",
        ])
        log.info("yt-dlp pip update rc=%s output=%s", proc.returncode, proc.stdout.strip())
        if proc.returncode != 0:
            raise RuntimeError("yt-dlp update failed")
        if not target.exists():
            target = Path(tmp) / "bin" / "yt-dlp"
            if not target.exists():
                raise RuntimeError("updated yt-dlp executable was not created")
        shutil.move(str(target), str(YTDLP_BIN))
        YTDLP_BIN.chmod(YTDLP_BIN.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return _version()


async def main() -> None:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        _ensure_shared_binary()
        await redis.set(YTDLP_UPDATED_VERSION, _version())
        while True:
            try:
                if yt_dlp_update_needed():
                    await redis.set(PAUSE_FLAG, "yt-dlp-update")
                    await wait_for_idle(redis)
                    version = update_ytdlp()
                    await redis.set(YTDLP_UPDATED_VERSION, version)
                    await redis.delete(PAUSE_FLAG)
                    log.warning("yt-dlp updated to %s; new worker jobs will use shared binary", version)
                else:
                    await redis.delete(PAUSE_FLAG)
            except Exception:
                await redis.delete(PAUSE_FLAG)
                log.exception("watcher iteration failed")
            await asyncio.sleep(settings.ytdlp_update_interval_seconds)
    finally:
        await redis.delete(PAUSE_FLAG)
        await redis.aclose()


if __name__ == "__main__":
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass
    asyncio.run(main())
