#!/usr/bin/env python3
"""Send today's daily outfit brief once (design.md §18, Req 12.6).

Uses the server's settings (process environment > root ``.env`` > defaults)
and the same database, through the same builder as the API server
(``app.notifications.runtime.build_brief_runtime``):

- default: one **scheduled** run for today's date in APP_TIMEZONE. When that
  date already has a scheduled brief, nothing is sent, "이미 보냄" is printed
  and the exit code is 0. So cron/launchd can call it as often as they like
  and still get at most one brief a day (the database decides, Req 12.3).
- ``--force``: one **manual** brief, which never blocks the scheduled one.

The title and body of a sent brief are printed. NOTIFIER=log (the default)
needs no network. Exit code 1 when the brief could not be made.

Usage: backend/.venv/bin/python scripts/daily_brief.py [--force]
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.config import AppConfig  # noqa: E402
from app.deps import build_container  # noqa: E402
from app.main import (  # noqa: E402
    DEFAULT_ENV_FILE,
    install_logging,
    load_settings,
    log_config_issues,
    resolve_automation_paths,
    resolve_paths,
)
from app.notifications.runtime import build_brief_runtime  # noqa: E402

ALREADY_SENT = "이미 보냄"


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send today's WeatherWear daily brief once.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="make a manual brief even if today's scheduled brief was already sent",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    config: AppConfig | None = None,
    env_file: Path | None = DEFAULT_ENV_FILE,
    clock: Callable[[], datetime] | None = None,
) -> int:
    args = parse_args(argv)
    issues = []
    if config is None:
        config, issues = load_settings(env_file)
    install_logging(config)
    log_config_issues(issues)
    config = resolve_automation_paths(resolve_paths(config))

    container = build_container(config)
    try:
        runtime = build_brief_runtime(config, container, clock=clock)
        service = runtime.daily_brief
        try:
            if args.force:
                result = service.run("manual")
            else:
                result = service.run("scheduled", service.today())
        except Exception as exc:
            print(f"브리핑을 보내지 못했습니다 ({type(exc).__name__}).", file=sys.stderr)
            return 1
        if result.skipped:
            print(f"{ALREADY_SENT}: {result.brief_date.isoformat()} 예약 브리핑은 이미 처리되었습니다.")
            return 0
        assert result.notification is not None
        print(result.notification.title)
        print(result.notification.body)
        return 0
    finally:
        container.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
