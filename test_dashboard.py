"""
Test runner for the dashboard checker — focuses on video recording.
Usage: python3 test_dashboard.py
"""
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))

from src.config import load_config
from src.checkers.dashboard import DashboardChecker

GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"
BOLD  = "\033[1m"

def ok(msg):  print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg): print(f"  {RED}✗{RESET} {msg}"); sys.exit(1)
def info(msg): print(f"    {msg}")


async def test_video_recording(target, videos_dir: Path):
    print(f"\n{BOLD}[1] Running checker for: {target.name}{RESET}")

    checker = DashboardChecker(target, screenshots_dir="screenshots")
    try:
        result = await checker.check()
    finally:
        await checker.close()

    info(f"status:     {result.status}")
    info(f"latency:    {result.latency_ms} ms")
    info(f"error:      {result.error_message or 'none'}")
    info(f"video_path: {result.screenshot_path or 'None'}")

    # --- Test 1: video path is returned ---
    print(f"\n{BOLD}[2] Assertions{RESET}")
    if result.screenshot_path is not None:
        ok("video_path is not None")
    else:
        fail("video_path is None — video was not recorded")

    # --- Test 2: file exists on disk ---
    video_file = Path(result.screenshot_path)
    if video_file.exists():
        ok(f"file exists: {video_file}")
    else:
        fail(f"file does not exist: {video_file}")

    # --- Test 3: extension is .webm ---
    if video_file.suffix == ".webm":
        ok("file extension is .webm")
    else:
        fail(f"unexpected extension: {video_file.suffix}")

    # --- Test 4: file is not empty ---
    size = video_file.stat().st_size
    if size > 1024:
        ok(f"file size is {size // 1024} KB (not empty)")
    else:
        fail(f"file is too small ({size} bytes) — recording may be corrupt")

    # --- Test 5: filename matches target name (stable, predictable) ---
    import re
    expected_stem = re.sub(r"[^\w\-]", "_", target.name)
    if video_file.stem == expected_stem:
        ok(f"filename is stable: {video_file.name}")
    else:
        fail(f"filename mismatch — got {video_file.name!r}, expected {expected_stem}.webm")

    # --- Test 6: file is inside the videos/ dir ---
    if video_file.parent == videos_dir:
        ok(f"stored in correct directory: {videos_dir}/")
    else:
        fail(f"wrong directory: {video_file.parent}")

    # --- Test 7: re-running overwrites (stable filename, no accumulation) ---
    print(f"\n{BOLD}[3] Re-run overwrites previous video{RESET}")
    mtime_before = video_file.stat().st_mtime

    checker2 = DashboardChecker(target, screenshots_dir="screenshots")
    try:
        await checker2.check()
    finally:
        await checker2.close()

    mtime_after = video_file.stat().st_mtime
    if mtime_after > mtime_before:
        ok("re-run overwrote the previous video (no file accumulation)")
    else:
        fail("re-run did NOT overwrite — old video still in place")

    # --- Summary ---
    print(f"\n{BOLD}Result:{RESET} video recorded at {GREEN}{video_file}{RESET}")
    print(f"  Open with:  open {video_file}")
    print(f"  Web URL:    http://localhost:8000/{video_file.as_posix()}")


async def main():
    config = load_config("config/config.yaml")

    if not config.dashboard_targets:
        print("No dashboard targets in config/config.yaml")
        return

    videos_dir = Path("videos")

    for target in config.dashboard_targets:
        print(f"\n{'='*60}")
        print(f"Target : {target.name}")
        print(f"URL    : {target.url}")
        print(f"Check  : {target.success_indicator.type} = {target.success_indicator.value!r}")
        print(f"{'='*60}")
        await test_video_recording(target, videos_dir)

    print(f"\n{GREEN}{BOLD}All tests passed.{RESET}\n")


asyncio.run(main())
