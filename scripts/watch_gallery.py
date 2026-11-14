#!/usr/bin/env python3
"""Watch Gallery_Dir in the foreground (Phase 2 tasks 3.13, Req 1.4, 1.7).

    backend/.venv/bin/python scripts/watch_gallery.py            # until Ctrl+C
    backend/.venv/bin/python scripts/watch_gallery.py --once     # check, wait, check, exit 0

Uses the same settings (process env > ``.env`` > defaults) and the same
SQLite file as the API server, without FastAPI. Running it next to a server
with GALLERY_WATCH_ENABLED=true is safe: each file version is processed once,
by whichever process wins the claim in the database.

Running this script means "watch", so GALLERY_WATCH_ENABLED does not need to
be true. ``--once`` checks twice, ``GALLERY_POLL_SECONDS`` apart, which is what
the Stable_File rule needs, then processes what became stable and exits 0. On
the very first run for a folder the first check only records the baseline.
Output lists file names and statuses only, never server paths.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.deps import build_container  # noqa: E402
from app.gallery.runtime import build_gallery_runtime  # noqa: E402
from app.main import (  # noqa: E402
    DEFAULT_ENV_FILE,
    install_logging,
    load_settings,
    log_config_issues,
    resolve_automation_paths,
    resolve_paths,
)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gallery_Dir를 감시해 새 사진을 수집합니다.")
    parser.add_argument("--once", action="store_true", help="두 번 확인하고 처리한 뒤 종료 (종료 코드 0)")
    parser.add_argument("--gallery-dir", type=Path, default=None, help="GALLERY_DIR 대신 쓸 폴더 (테스트용)")
    parser.add_argument(
        "--env-file", type=Path, default=DEFAULT_ENV_FILE, help="읽을 .env 파일 (기본: 저장소 루트 .env)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env_file = args.env_file if args.env_file and args.env_file.exists() else None
    config, issues = load_settings(env_file)
    install_logging(config)
    log_config_issues(issues)

    automation = replace(config.automation, gallery_watch_enabled=True)
    if args.gallery_dir is not None:
        automation = replace(automation, gallery_dir=args.gallery_dir)
    config = resolve_automation_paths(resolve_paths(replace(config, automation=automation)))
    if not config.automation.gallery_watch_enabled:
        print("USER_PHOTO_DIR 설정이 PHOTO_DIR 또는 GALLERY_DIR와 겹쳐 감시를 시작할 수 없습니다.", file=sys.stderr)
        return 2

    container = build_container(config)
    runtime = build_gallery_runtime(config, container)
    watcher = runtime.watcher
    assert watcher is not None
    stop = threading.Event()
    try:
        if args.once:
            results = [watcher.poll_once(stop)]
            time.sleep(watcher.poll_seconds + 0.05)
            results.append(watcher.poll_once(stop))
            for result in results:
                if result.baseline_rows is not None:
                    print(f"기준선 기록: {result.baseline_rows}개 파일")
                for file_name, status in result.processed:
                    print(f"{file_name}: {status}")
            if not results[-1].dir_available:
                print("갤러리 폴더를 사용할 수 없습니다.", file=sys.stderr)
        else:
            print("갤러리 감시를 시작합니다. 끝내려면 Ctrl+C를 누르세요.")
            watcher.run(stop)
    except KeyboardInterrupt:
        stop.set()
        print("갤러리 감시를 끝냅니다.")
    finally:
        container.engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
