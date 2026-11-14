# Implementation Plan: WeatherWear Automation (Phase 2)

## Overview

Phase 1 브랜치를 main(PR #6, 사진 분류 게이트)과 병합한 결과 위에서 갤러리 감시와 매일 브리핑을 더한다. 백엔드는 Python(FastAPI, SQLModel, Hypothesis, respx), 프론트엔드는 TypeScript(React, Vite, vitest, fast-check)다. 새 런타임 의존성은 `pywebpush`, `cryptography` 두 개다.

순서는 다음과 같다. ① 리드가 공유 계약을 만든다(설정, DB 테이블과 추가 업그레이드, Phase 1 공개 메서드 2개, API 스키마와 라우터 골격, 위치 서비스). ② 세 흐름이 서로 다른 파일에서 병렬로 진행한다(A 갤러리 감시, B 매일 브리핑과 알림, C 프론트엔드). ③ 리드가 컨테이너와 lifespan을 연결하고 통합 테스트와 README를 쓴다.

모든 테스트는 외부 네트워크 없이 돈다. Phase 1 `conftest.py`의 소켓 가드를 그대로 쓰고, Open-Meteo Forecast는 respx, Web Push는 가짜 Push_Sender, 시각은 주입한 시계로 대신한다. 감시와 스케줄러는 스레드 없이 `poll_once()`, `tick()`을 직접 불러 테스트한다.

## Tasks

- [ ] 1. 공유 계약과 Phase 1 접점 (리드)
  - [ ] 1.1 병합 결과 기준선 확인
    - Phase 1 + main 병합 브랜치에서 `cd backend && python -m pytest -q`, `cd frontend && npx vitest --run && npm run typecheck`가 통과하는지 확인하고 결과(통과 수, skip 수)를 기록한다. 실패하면 Phase 2 작업을 시작하지 않고 병합 담당에게 알린다
    - `VisionAnalysis`, `ScanDecision`, `photo_analyses`, `MIN_PHOTO_TYPE_CONFIDENCE = 0.70`, `UNKNOWN_ANALYSIS`가 design의 전제와 같은지 확인한다
    - _Requirements: 20.4, 20.5_

  - [ ] 1.2 Phase 2 설정 추가 (`backend/app/config.py`, `backend/app/main.py`의 `resolve_paths()`만, `.env.example`, `.gitignore`, `backend/tests/test_config.py`, `backend/tests/test_config_automation.py`)
    - `RawSettings`에 9개 필드, `AutomationConfig`(기본값 모두 꺼짐), `AppConfig.automation`(default_factory), `parse_bool()`, `parse_hhmm()`, `resolve_automation_config()` 구현. 이슈 메시지에는 변수 이름과 허용값만 넣는다
    - `resolve_paths()`: `gallery_dir`, `user_photo_dir`, `vapid_key_dir`를 저장소 루트 기준 절대 경로로 바꾸고, USER_PHOTO_DIR가 PHOTO_DIR·GALLERY_DIR와 같거나 조상·자손이면 이슈를 남기고 기본값을 쓴다
    - `.env.example`에 9개 변수(설명 포함, GALLERY_DIR·VAPID_KEY_DIR 설명 문구 포함). 결정 A1에 따라 `GALLERY_WATCH_ENABLED=true`, `DAILY_BRIEF_ENABLED=true`로 켜고 설명에 "코드 기본값은 false"를 적는다. 나머지는 기본값. `.gitignore`에 `data/user/`, `data/secrets/`
    - Phase 1 테스트 2개를 21개 / 백엔드 20개 기준으로 고치고, 예제 테스트 영역 (l)(`test_phase2_defaults`, `test_invalid_phase2_values_fall_back_with_issue`, `test_relative_paths_resolved_to_repo_root`, `test_gitignore_has_user_and_secrets`)을 작성한다
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6, 4.2, 4.5, 20.4_

  - [ ]* 1.3 Phase 2 설정 해석 property test 작성 (`backend/tests/test_config_automation_props.py`)
    - **Property 24: Phase 2 설정 해석**
    - **Validates: Requirements 18.1, 18.2, 18.9, 4.2**

  - [ ] 1.4 Phase 2 테이블과 추가 업그레이드 (`backend/app/db_automation.py`, `backend/app/db.py`, `backend/tests/test_db.py`, `backend/tests/test_db_automation.py`)
    - design ER대로 SQLModel 테이블 9개(`gallery_files`, `app_state`, `user_photos`, `photo_confirmations`(UNIQUE gallery_file_id, INSERT만), `user_location`, `push_subscriptions`, `notifications`, `notification_deliveries`, `daily_brief_runs`)와 CHECK·UNIQUE·인덱스, `daily_brief_runs`의 부분 고유 인덱스(`sqlite_where`)
    - `SCHEMA_VERSION = 3`, `REBUILD_BELOW_VERSION = 2`, `migrate_schema()`(v<2 백업 후 재생성, v==2 추가 후 3, v>3 유지 + WARNING). main의 시작 마이그레이션 순서는 그대로 둔다
    - `test_new_db_has_schema_version`을 3으로 고치고, 예제 테스트 영역 (k)를 작성한다(`test_v2_db_upgraded_to_v3_without_data_loss`, `test_v1_db_still_backed_up`, `test_newer_db_version_not_downgraded`, `test_existing_tables_unchanged`)
    - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5, 20.4_

  - [ ]* 1.5 DB 추가 업그레이드 property test 작성 (`backend/tests/test_db_automation_props.py`)
    - **Property 26: DB 추가 업그레이드는 보존적이고 멱등이다**
    - **Validates: Requirements 19.2, 19.6**

  - [ ] 1.6 Phase 1 공개 메서드 2개 꺼내기 (`backend/app/ingestion/service.py`, `backend/app/recommendation/service.py`, `backend/app/repository.py`, `backend/tests/test_ingest_file.py`)
    - `ImageIngestionService.ingest_file(path, *, processed_hashes=None, analysis_override=None, before_commit=None) -> Stored | Duplicate | NotOutfit | Failed`로 main의 `_process()` 본문을 옮기고 `scan()`이 이를 쓰게 한다. `Stored`에는 `create_outfit_bundle()`의 id를 담는다
    - `analysis_override`가 있으면 Vision·Photo_Gate·`save_photo_analysis`를 건너뛴다. `before_commit`은 `OutfitRepository.create_outfit_bundle(..., before_commit=None)`에 넘겨 같은 트랜잭션에서 부른다(uncertain 확정용, 기본값이면 동작 불변)
    - `RecommendationService.recommendations_for(today)`를 만들고 `today_recommendations()`가 이를 부르게 한다
    - 새 테스트는 결과 타입 4종과 outfit_id만 확인한다. 기존 Phase 1 수집·추천·통합 테스트는 고치지 않고 통과해야 완료다
    - _Requirements: 3.1, 11.1, 6.12, 6.13, 20.4_

  - [ ] 1.7 API 스키마, 라우터 골격, 오류 매핑, OpenAPI 재생성 (`backend/app/schemas_automation.py`, `backend/app/api/{gallery,location,notifications,push,daily_brief,automation}.py`, `backend/app/main.py`, `backend/app/deps.py`, `frontend/src/api/schema.d.ts`, `backend/tests/test_api_routes.py`, `backend/tests/test_api.py`)
    - design "REST API" 절의 요청·응답 모델 전부(비밀값 필드 없음). 6개 라우터는 `response_model`과 오류 응답만 선언하고 본문은 501을 낸다(각 흐름이 채운다)
    - `ROUTER_MODULES`에 6개 추가, RequestValidationError 핸들러에 `/api/location` → `INVALID_LOCATION`, `POST /api/push/subscriptions` → `PUSH_SUBSCRIPTION_INVALID` 매핑, MESSAGES에 Phase 2 오류 코드 9개 추가(`GALLERY_IMPORT_NOT_CONFIRMABLE` 포함, `confirm-outfit` 골격 포함)
    - `AppContainer`에 Phase 2 필드(기본 None)와 `get_*` 의존성 함수 추가
    - `scripts/export_openapi.py` + `openapi-typescript`로 `schema.d.ts` 재생성. `test_api_routes.py` ROUTER_MODULES 집합과 `test_api.py` `EXPECTED_OPERATIONS`(12개 추가) 갱신
    - _Requirements: 9.2, 15.5, 17.2, 20.4_

  - [ ] 1.8 LocationService와 위치 API (`backend/app/location.py`, `backend/app/api/location.py`, `backend/tests/test_location.py`)
    - `round_location()`(Decimal ROUND_HALF_UP 소수 둘째 자리), `validate_location()`(불리언·NaN·범위·음수 accuracy 거부), `LocationService.report/last/brief_location`(`user_location` id=1 UPSERT, 위경도 로그 없음)
    - `POST /api/location`(200 + Place_Label: 1.8에서는 `notifications/places.py`가 아직 없으므로 `place_label` 함수를 주입받는 형태로 두고 7.1에서 연결), `GET /api/location`
    - 테스트: `test_location_round_and_replace`, `test_location_invalid_422_keeps_previous`, `test_location_not_logged`, `test_brief_location_default_then_browser`
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

  - [ ]* 1.9 위치 property test 작성 (`backend/tests/test_location_props.py`)
    - **Property 14: Last_Location은 마지막 유효 보고다**
    - **Validates: Requirements 9.1, 9.2, 9.6**

- [ ] 2. Checkpoint - 공유 계약 확인
  - `cd backend && python -m pytest -q`(Phase 1 전체 + 1.x 테스트)와 `cd frontend && npm run typecheck`가 외부 연결 없이 통과하는지 확인한다. 질문이 생기면 사용자에게 묻는다.
  - _Requirements: 20.5_

- [ ] 3. 흐름 A: 갤러리 감시와 수집
  - [ ] 3.1 후보·안정 판정 순수 함수 (`backend/app/gallery/rules.py`, `backend/tests/test_gallery_rules.py`)
    - `is_gallery_candidate()`(Phase 1 `SUPPORTED_EXTENSIONS`와 `SIDECAR_SUFFIX` 재사용 + 숨김·`~`·임시 확장자·심볼릭 링크 제외), `Signature`, `Tracker`, `StableEvent`, `advance_trackers()`
    - 예제: `test_zero_byte_file_ignored`, `test_signature_change_resets_stability`, `test_ignored_names_never_read`, 이벤트 순서 (first_seen, name)
    - _Requirements: 2.1, 2.2, 2.3, 2.8, 1.6_

  - [ ]* 3.2 후보·안정 판정 property test 작성 (`backend/tests/test_gallery_rules_props.py`)
    - **Property 1: 갤러리 후보 판별**
    - **Validates: Requirements 2.1, 1.6**
    - **Property 2: 안정 판정이 참조 모델과 일치한다**
    - **Validates: Requirements 2.2, 2.3, 2.9**

  - [ ] 3.3 GalleryStore (`backend/app/gallery/store.py`, `backend/tests/test_gallery_store.py`)
    - `gallery_key()`, `baseline_done/record_baseline`(app_state 표시 + baseline 행, 한 트랜잭션), `upsert_version`(같은 서명 무시, 다른 서명은 새 버전), `due_rows`, `claim`(조건부 UPDATE, lease 10분), `finish`, `active_with_sha`, `reset_for_reanalyze`, `list_imports`(baseline 제외, updated_at DESC), `counts`, `user_photos` 함수 3개
    - 예제: `test_baseline_not_redone_for_same_gallery_dir`, `test_claim_once_between_two_stores`(서로 다른 engine 2개, 같은 파일), `test_expired_lease_reclaimed`, `test_reset_for_reanalyze_rules`
    - _Requirements: 2.4, 2.5, 1.7, 3.3, 6.8, 17.3_

  - [ ]* 3.4 기준선·재시작 property test 작성 (`backend/tests/test_gallery_store_props.py`)
    - **Property 3: 기준선과 재시작**
    - **Validates: Requirements 2.4, 2.5, 2.8**

  - [ ] 3.5 추정 촬영 정보 (`backend/app/gallery/estimate.py`, `backend/tests/test_gallery_estimate.py`)
    - `read_partial_exif()`(Phase 1 metadata.py의 DMS helper 재사용, 읽기만), `estimate_capture()`(결정 A3: EXIF 시각 → 파일 mtime(APP_TIMEZONE), 미래 mtime은 처리 시각), `estimated_sidecar_json()`(`time_source: file_mtime`)
    - 예제: `test_no_exif_uses_mtime_and_default_location`(`os.utime`으로 mtime 고정), `test_future_mtime_clamped_to_now`, `test_no_exif_uses_last_location`, `test_datetime_without_offset_uses_app_tz`, `test_valid_embedded_metadata_not_estimated`
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [ ]* 3.6 추정 촬영 정보 property test 작성 (`backend/tests/test_gallery_estimate_props.py`)
    - **Property 6: 추정 촬영 정보 우선순위**
    - **Validates: Requirements 5.1, 5.2, 5.6**
    - **Property 7: 추정 사이드카 round-trip**
    - **Validates: Requirements 5.3, 5.7**

  - [ ] 3.7 Fixture Vision overlay (`backend/app/providers/vision/fixture.py`, `backend/app/providers/vision/factory.py`, `backend/tests/test_vision_overlay.py`)
    - `FixtureVisionProvider(..., overlay_path=None)`: overlay(크기·mtime_ns 변화 시 다시 읽기, main `load_vision_fixture()` 재사용) → 데모 fixture → `UNKNOWN_ANALYSIS`. 실패 경고는 서명마다 1건
    - `build_vision_provider()`가 `cfg.automation.vision_overlay_path`를 넘긴다. 기존 `test_vision_fixture.py`는 고치지 않고 통과해야 한다
    - 예제: `test_fixture_unknown_hash_uncertain_without_overlay`, `test_overlay_reload_on_change`, `test_broken_overlay_ignored_with_one_warning`, `test_demo_scan_results_unchanged_with_overlay`
    - _Requirements: 6.3, 6.4, 4.6_

  - [ ]* 3.8 overlay property test 작성 (`backend/tests/test_vision_overlay_props.py`)
    - **Property 9: Fixture overlay 조회 순서**
    - **Validates: Requirements 6.3, 6.4**

  - [ ] 3.9 RecentWeatherProvider (`backend/app/gallery/weather_recent.py`, `backend/tests/test_weather_recent.py`)
    - `choose_weather_endpoint()`, `RecentWeatherProvider.get_weather()`(4.1의 `ForecastClient.get_hourly` + Phase 1 `map_archive_response`, 실패 시 Phase 1 `OpenMeteoWeatherProvider`)
    - 예제(respx): `test_recent_photo_uses_forecast_past_days`(URL·파라미터 확인), `test_old_photo_uses_archive`, `test_forecast_failure_falls_back_to_archive_then_fixture`, `test_fixture_mode_today_photo_gets_today_default`
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [ ]* 3.10 엔드포인트 선택 property test 작성 (`backend/tests/test_weather_recent_props.py`)
    - **Property 11: 촬영 시점 날씨 엔드포인트 선택**
    - **Validates: Requirements 7.1, 7.2, 7.6**

  - [ ] 3.11 GalleryImportService (`backend/app/gallery/processor.py`, `backend/tests/test_gallery_processor.py`)
    - `failure_transition()`과 `process()`를 design "GalleryImportService" 절의 순서대로 구현: 파일 재확인, SHA-256, 중복(outfits, 활성 행, `non_outfit` 판정), 바이트 그대로 복사(`.partial` → `os.replace`, 해시 확인, 이름 충돌 `<stem>_<sha8>`), `user_photos`, 추정 사이드카, `ingest_file()`, 결과별 정리(NotOutfit·Duplicate·최종 실패는 복사본·사이드카·`user_photos` 삭제)
    - 감시용 `ImageIngestionService`는 `photo_dir=user_photo_dir`, Vision은 API_Server와 같은 provider, 날씨는 `RecentWeatherProvider`(fixture 모드면 Phase 1 fixture)로 만든다. Photo_Dir·manifest·fixtures에는 쓰지 않는다
    - 예제: `test_outfit_imported_with_user_photo`, `test_non_outfit_skipped_copy_removed`, `test_uncertain_kept_out_and_listed`, `test_low_confidence_outfit_is_uncertain`, `test_known_non_outfit_hash_skips_vision`, `test_same_bytes_two_names_one_outfit`, `test_vision_timeout_retries_after_1_then_5_minutes`, `test_third_failure_is_final`, `test_invalid_format_not_retried`, `test_failed_attempt_leaves_no_rows`
    - _Requirements: 2.6, 2.7, 3.1, 3.2, 3.3, 3.4, 3.6, 4.1, 4.3, 4.6, 5.3, 6.1, 6.2, 6.5, 6.6, 6.7, 6.10_

  - [ ]* 3.12 수집 처리 property test 작성 (`backend/tests/test_gallery_processor_props.py`)
    - **Property 4: 해시당 Outfit은 1건이다**
    - **Validates: Requirements 2.6, 2.7, 1.7, 4.6**
    - **Property 5: 판정별 복사본 정리와 데모 데이터 보존**
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.7, 4.1**
    - **Property 8: 재시도 일정과 Vision 호출 상한**
    - **Validates: Requirements 6.1, 6.5, 6.6, 6.7, 6.10, 6.11**

  - [ ] 3.13 GalleryWatcher, CLI, 런타임 builder (`backend/app/gallery/watcher.py`, `backend/app/gallery/runtime.py`, `scripts/watch_gallery.py`, `backend/tests/test_gallery_watcher.py`)
    - `poll_once(stop)`(scandir → 사용 가능 여부 전환 시에만 WARNING → 기준선 → `advance_trackers` → `upsert_version` → `due_rows` → `claim` → `process` → `finish`, 행마다 stop 확인, 예외는 기록 후 계속), `run(stop)`, `status()`
    - `build_gallery_runtime(config, container)`: store, importer, watcher(감시 꺼짐이면 None)를 만든다. `deps.py`는 고치지 않는다(7.1에서 연결)
    - CLI: `--once`(poll → 대기 → poll, 종료 코드 0), `--gallery-dir`, Ctrl+C 처리
    - 예제: `test_first_poll_records_baseline_without_analysis`, `test_new_file_after_baseline_is_imported`, `test_file_added_while_stopped_is_processed_on_restart`, `test_growing_file_not_read_until_stable`, `test_hidden_tmp_then_rename_processed_once`, `test_missing_gallery_dir_warns_once`, `test_unexpected_exception_does_not_kill_watcher`, `test_watch_gallery_once_cli`
    - _Requirements: 1.1, 1.2, 1.4, 1.5, 1.6, 1.8, 2.8_

  - [ ] 3.14 사진 파일 위치 확장 (`backend/app/api/outfits.py`, `backend/tests/test_photo_paths.py`)
    - `resolve_outfit_photo()`: `user_photos`에 해시가 있으면 User_Photo_Dir, 없으면 Phase 1 `resolve_photo_path()`. 두 경우 모두 폴더 밖 경로를 거부한다
    - 예제: `test_user_photo_served_from_user_dir`, `test_demo_photo_still_served_from_photo_dir`, `test_photo_path_traversal_404`, `test_imported_outfit_listed_in_outfits_and_candidates`
    - _Requirements: 3.5, 3.6_

  - [ ]* 3.15 사진 위치 property test 작성 (`backend/tests/test_photo_paths_props.py`)
    - **Property 10: 사진 파일 위치 해석**
    - **Validates: Requirements 3.5**

  - [ ] 3.16 사진 처리 기록 API (`backend/app/api/gallery.py`, `backend/tests/test_api_gallery.py`)
    - `GET /api/gallery/imports?limit&status`(baseline 제외, 판정·capture_origin 포함, 경로 없음), `POST /api/gallery/imports/{id}/reanalyze`(202 / 404 / 409)
    - 예제: `test_gallery_imports_filters_and_order`, `test_imports_list_marks_estimated`, `test_reanalyze_uncertain_and_failed_only`, `test_imports_response_has_no_paths`
    - _Requirements: 17.3, 6.8, 5.5, 17.2_

  - [ ] 3.17 "방금 찍은 사진" 흉내 스크립트 (`backend/app/gallery/simulate.py`, `scripts/simulate_capture.py`, `backend/tests/test_simulate_capture.py`)
    - design "Capture_Simulator" 절의 helper와 `main(argv)`: 원본 선택(`--source` → `data/user/capture_pool/` `.last_used` 다음 → 플레이스홀더), 데모 사진 거부(종료 코드 2), 시각·위치 결정(`--at`, `--lat/--lon`, DB `user_location` 읽기 전용, 기본 위치), EXIF 기록, fixture 모드 overlay 선기록(`--tags`, pool `clothing_tags.json`, 플레이스홀더 고정 태그, `--no-fixture`), 숨김 임시 파일 → `os.replace`, 이름 충돌 `_2`, 파일 이름만 출력
    - 예제(tmp 디렉터리, 오프라인): `test_simulate_writes_exif_and_prints_name`, `test_simulate_refuses_demo_photo`, `test_simulate_placeholder_when_pool_empty`, `test_simulate_atomic_name_collision_suffix`, `test_simulate_overlay_written_before_publish`, `test_simulate_no_tags_prints_uncertain_notice`, `test_simulate_no_network`
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8_

  - [ ]* 3.18 흉내 스크립트 EXIF property test 작성 (`backend/tests/test_simulate_capture_props.py`)
    - **Property 13: 흉내 스크립트 EXIF round-trip**
    - **Validates: Requirements 8.5, 8.9**

  - [ ] 3.19 흐름 A 통합 예제 테스트 (`backend/tests/test_gallery_flow.py`)
    - 영역 (a)~(g)를 한 파일에서 실제 store·importer·watcher로 확인: `test_demo_dirs_unchanged_after_imports`(tmp에 Photo_Dir·manifest·fixtures 사본을 만들고 해시 비교), `test_demo_photo_bytes_in_gallery_is_duplicate`, `test_overlay_written_before_file_imports_as_outfit`(simulate → poll×2 → imported, fixture 모드), `test_watch_disabled_reads_nothing`, `test_demo_scan_weather_unchanged`
    - 스레드를 쓰지 않는다. `poll_once()`와 FakeClock만 쓴다
    - _Requirements: 20.2, 20.3, 4.1, 4.4, 1.3_

  - [ ] 3.20 uncertain 사진 "착장 맞음" 확정 (`backend/app/gallery/confirm.py`, `backend/app/api/gallery.py`의 confirm-outfit 경로, `backend/tests/test_gallery_confirm.py`)
    - `confirmed_analysis()`, `OutfitConfirmService.confirm()`: `uncertain` 행만 claim, Gallery_Dir 원본 재읽기와 SHA-256 비교, User_Photo_Dir 재복사(+내장 메타데이터 없으면 추정 사이드카), `ingest_file(copy, analysis_override=…, before_commit=…)`로 메타데이터 → 날씨 → 저장. `before_commit`에서 `photo_analyses` → `outfit` 변경과 `photo_confirmations` INSERT. 실패하면 복사본 정리 후 `uncertain`으로 되돌린다. Vision은 부르지 않는다
    - `POST /api/gallery/imports/{id}/confirm-outfit`: 200(imported, outfit_id) / 404 `GALLERY_IMPORT_NOT_FOUND` / 409 `GALLERY_IMPORT_NOT_CONFIRMABLE`(상태 또는 원본 문제)
    - 예제 영역 (m): `test_confirm_uncertain_creates_outfit_with_audit`, `test_confirm_does_not_call_vision`, `test_confirm_twice_conflict`, `test_confirm_source_missing_or_changed_409`, `test_confirm_storage_failure_rolls_back_all`, `test_confirmed_unknown_tags_listed_not_recommended`
    - _Requirements: 6.12, 6.13, 6.14, 6.15, 20.3_

  - [ ]* 3.21 확정 property test 작성 (`backend/tests/test_gallery_confirm_props.py`)
    - **Property 28: uncertain 확정은 Vision 없이 원자적으로 저장된다**
    - **Validates: Requirements 6.12, 6.13, 6.14**

- [ ] 4. 흐름 B: 매일 브리핑과 알림
  - [ ] 4.1 ForecastClient (`backend/app/providers/weather/forecast.py`, `backend/tests/test_forecast.py`)
    - 시작 전에 Open-Meteo Forecast 공식 문서로 `current`·`hourly`·`past_days`·`forecast_days`·변수 이름·단위를 확인하고, design과 다르면 design의 "근거와 가정" 표와 상수를 고친다
    - `build_current_params()`, `build_hourly_params()`, `map_current_response()`(Phase 1 `normalize_value()` 재사용), `ForecastClient.get_current/get_hourly`(10초, API 키 없음, 실패 → `ForecastError`)
    - 예제(respx): 서울 `current` 응답 고정 JSON 변환, 변수 누락 시 null, 2xx 아님·잘못된 JSON·타임아웃 → `ForecastError`
    - _Requirements: 10.1, 10.6, 7.1_

  - [ ]* 4.2 Forecast 변환 property test 작성 (`backend/tests/test_forecast_props.py`)
    - **Property 12: Forecast 응답 변환과 대체**
    - **Validates: Requirements 7.3, 10.1, 10.2, 10.6**

  - [ ] 4.3 CurrentWeatherService (`backend/app/notifications/current_weather.py`, `backend/tests/test_current_weather.py`)
    - 시나리오 → Forecast current(open_meteo일 때) → fixture `today_default`, 실패 시 WARNING. Phase 1 `TodayWeatherService`와 `/api/weather/today`는 고치지 않는다
    - 예제: `test_scenario_overrides_current_weather`, `test_live_current_weather_mapping`(respx), `test_live_failure_uses_today_default`, `test_fixture_mode_no_network`, `test_phase1_weather_today_unchanged`
    - _Requirements: 10.2, 10.3, 10.4, 10.5_

  - [ ] 4.4 Brief_Composer (`backend/app/notifications/{labels_ko,josa,places,compose}.py`, `backend/tests/test_brief_compose.py`)
    - `labels_ko`: design 표대로 UpperType·OuterType·LowerType 전체(`none`, `unknown` 제외)와 데모 fixture 색상 표기. 커버리지 테스트는 main의 `UPPER_TYPES`/`OUTER_TYPES`/`LOWER_TYPES`와 `data/demo/fixtures/vision.json`의 색상을 순회한다
    - `object_particle()`, `KNOWN_PLACES` 16곳 + `place_label()`(haversine 30km), `outfit_phrase()`, `format_temp()`, `compose_brief()`(본문 규칙, 제목 표시, 200자 제한)
    - 예제: `test_compose_matches_user_example`(입력: 서울, 14.5°C/55%, 1순위 14.4°C·knit·none·cream·comfortable → 사용자 결정 2의 문장과 정확히 같음), 투표 없음, cold 꼬리 문장, hot 꼬리 문장, fallback 문구, 데이터 부족, 습도 없음, "카멜 니트에 블레이저를", "현재 위치"
    - _Requirements: 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9, 11.10, 11.11, 11.12, 11.14, 11.15_

  - [ ]* 4.5 Brief_Composer property test 작성 (`backend/tests/test_brief_compose_props.py`)
    - **Property 15: 장소 이름**
    - **Validates: Requirements 11.11**
    - **Property 16: 조사 선택**
    - **Validates: Requirements 11.8, 11.15**
    - **Property 17: 착장 문구와 한국어 표기**
    - **Validates: Requirements 11.6, 11.7**
    - **Property 18: 브리핑 문장 규칙**
    - **Validates: Requirements 11.2, 11.3, 11.4, 11.5, 11.9, 11.10, 11.12, 11.14**

  - [ ] 4.6 Notification_Log와 Notifier (`backend/app/notifications/records.py`, `backend/app/notifications/notifier.py`, `backend/tests/test_notification_records.py`)
    - `NotificationLog.create/add_deliveries/list_recent/latest_for_date`, `Notifier` Protocol, `DeliveryResult`, `LogNotifier`(id·kind만 INFO, log `sent` 1건), `build_notifier()`(webpush는 4.9에서 연결할 자리)
    - 예제: 기록 먼저 저장 후 전달, Notifier 예외에도 기록 유지 + `failed(NOTIFIER_ERROR)`, 목록 정렬과 kind 필터, 전달 요약 집계
    - _Requirements: 14.1, 14.2, 14.4, 14.8_

  - [ ] 4.7 VapidKeyStore (`backend/app/notifications/vapid.py`, `backend/tests/test_vapid.py`)
    - `ensure()`(로드 → `O_CREAT|O_EXCL` 0600 생성 → 손상 시 `.invalid-<stamp>` 이름 변경 후 재생성과 `on_rotated` 콜백), 공개키 base64url, `secret_strings()`, `VapidKeyUnavailable`
    - 예제: `test_vapid_key_created_once_with_0600`, `test_corrupt_key_renamed_and_subscriptions_cleared`(콜백 호출 확인), `test_vapid_dir_unwritable_raises`, 키 값이 로그에 없음
    - _Requirements: 15.1, 15.2, 15.3, 15.10, 15.11, 18.7_

  - [ ]* 4.8 VAPID 키 property test 작성 (`backend/tests/test_vapid_props.py`)
    - **Property 22: VAPID 키 round-trip**
    - **Validates: Requirements 15.1, 15.2, 15.3, 15.11**

  - [ ] 4.9 Push 구독 저장소와 WebPushNotifier (`backend/app/notifications/push_store.py`, `backend/app/notifications/webpush.py`, `backend/tests/test_webpush.py`)
    - `validate_subscription()`, `PushSubscriptionStore`(endpoint upsert, delete, list, `delete_not_matching_key`, count), `PushSender` Protocol, `PyWebPushSender`(pywebpush, timeout 10초, 설치 버전 시그니처 확인), `classify_push_status()`, `WebPushNotifier`(payload 5필드 ≤ 3000바이트, TTL 12시간, `sub`=VAPID_SUBJECT, gone 삭제, 키 없으면 기록만)
    - 로그에는 구독 id와 endpoint 호스트 이름만 남긴다
    - 예제(가짜 sender): `test_410_deletes_subscription`, `test_404_deletes_subscription`, `test_500_keeps_subscription`, `test_payload_shape_ttl_subject`, `test_zero_subscriptions_records_only`, `test_duplicate_endpoint_upserts`
    - _Requirements: 14.3, 15.5, 15.7, 15.8, 18.7_

  - [ ]* 4.10 Web Push property test 작성 (`backend/tests/test_webpush_props.py`)
    - **Property 21: Web Push 전달 결과 분류**
    - **Validates: Requirements 15.7, 14.3**
    - **Property 23: 구독 검증과 upsert**
    - **Validates: Requirements 15.5**

  - [ ] 4.11 DailyBriefService, 스케줄러, CLI, 런타임 builder (`backend/app/notifications/daily_brief.py`, `backend/app/notifications/scheduler.py`, `backend/app/notifications/runtime.py`, `scripts/daily_brief.py`, `backend/tests/test_daily_brief.py`)
    - `DailyBriefRuns.claim/retry/finish`(부분 고유 인덱스 INSERT, failed 행 조건부 UPDATE), `DailyBriefService.run(trigger)`(non-blocking Lock → `DailyBriefInProgress`, 위치 → 현재 날씨 → `recommendations_for` → `compose_brief` → 기록 → 전달 → 결과)
    - `scheduled_decision()`, `next_run_at()`, `DailyBriefScheduler.tick/run`(30초, FakeClock 주입)
    - `build_brief_runtime(config, container)`: log, push_store, vapid_keys, notifier, service, scheduler(꺼짐이면 None). `deps.py`는 고치지 않는다(7.1에서 연결)
    - CLI `scripts/daily_brief.py [--force]`: 이미 보냈으면 "이미 보냄" 출력 후 0
    - 예제: `test_scheduled_once_per_day_across_restarts`, `test_start_after_catch_up_window_skips_today`, `test_manual_run_does_not_block_scheduled`, `test_failed_scheduled_run_retried_10_minutes_later`, `test_cli_daily_brief_idempotent`, `test_insufficient_data_brief_is_sent`, `test_skipped_and_uncertain_photos_not_in_brief`
    - _Requirements: 11.1, 11.9, 11.13, 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.8, 13.1, 6.9_

  - [ ]* 4.12 예약 실행 property test 작성 (`backend/tests/test_scheduler_props.py`)
    - **Property 19: 예약 실행은 날짜당 1회다**
    - **Validates: Requirements 12.1, 12.3, 12.4, 12.5, 12.7, 12.8**

  - [ ] 4.13 알림·푸시·브리핑 API (`backend/app/api/notifications.py`, `backend/app/api/push.py`, `backend/app/api/daily_brief.py`, `backend/tests/test_api_notifications.py`)
    - 1.7 골격의 본문 구현: `GET /api/notifications`, `POST /api/daily-brief/run`(409 `DAILY_BRIEF_IN_PROGRESS`, DAILY_BRIEF_ENABLED와 무관), `GET /api/push/vapid-public-key`(503 `VAPID_KEY_UNAVAILABLE`), `POST /api/push/subscriptions`(201/422), `DELETE /api/push/subscriptions/{id}`(204/404), `POST /api/push/test`(409 `WEB_PUSH_DISABLED`)
    - 테스트는 `app.dependency_overrides`로 4.x 서비스를 넣는다(7.1 연결 전에도 돈다)
    - 예제: `test_concurrent_run_returns_409`, `test_push_test_disabled_with_log_notifier`, `test_vapid_dir_unwritable_returns_503`, `test_public_key_only_in_response`, `test_subscription_delete_404`
    - _Requirements: 13.1, 13.2, 13.3, 14.5, 15.4, 15.6, 15.9, 15.10_

  - [ ]* 4.14 알림 기록·비밀값 property test 작성 (`backend/tests/test_notification_records_props.py`, `backend/tests/test_secrets_automation_props.py`)
    - **Property 20: 알림 기록 수와 정렬**
    - **Validates: Requirements 14.1, 14.5, 14.8**
    - **Property 25: 비밀값 비노출**
    - **Validates: Requirements 18.7, 15.4, 17.2**

  - [ ] 4.15 흐름 B 통합 예제 테스트 (`backend/tests/test_daily_brief_flow.py`)
    - `test_brief_with_fixture_demo_data`: fixture 모드에서 데모 데이터(189장) 스캔 → `run("manual")` → 본문이 병합된 `today_default`(main은 9.0°C/55%)와 그때 RecommendationService 1순위의 기온·태그·투표로 만든 기대 문장과 같고, 제목이 "오늘의 착장 브리핑 (fixture 날씨)"이다. 사용자 예시 문장과의 정확한 일치는 4.4의 `test_compose_matches_user_example`이 맡는다. 데모 사진이 없는 환경에서는 Phase 1 데모 테스트처럼 skip한다
    - `test_brief_without_vote`, `test_brief_cold_tail`, `test_brief_fallback_wording`, `test_brief_without_humidity`, `test_title_marks_fixture_and_scenario`를 실제 저장소·추천 경로로 확인한다(합성 Outfit 사용)
    - _Requirements: 20.3, 20.1, 11.2_

  - [ ] 4.16 Live 모드 홈 오늘 날씨 전환 (`backend/app/today_weather.py`, `backend/tests/test_today_weather_live.py`)
    - `TodayWeatherService(..., live_current=None)`: 시나리오가 없고 `live_current`가 있으면 그 결과(Current_Weather_Service, 실패 시 fixture 표시)를 쓴다. 기본값 `None`이면 Phase 1 동작 그대로라 Phase 1 `test_today_weather_service.py`는 고치지 않는다. 컨테이너 연결은 7.1
    - 예제 영역 (n): `test_live_home_weather_uses_last_location_forecast`(respx), `test_live_home_weather_fallback_marks_fixture`, `test_fixture_home_weather_unchanged`, `test_scenario_still_wins_in_live_mode`, `test_home_and_brief_same_weather`
    - _Requirements: 10.5, 10.7, 10.8, 20.3_

  - [ ]* 4.17 Live 홈 날씨 property test 작성 (`backend/tests/test_today_weather_live_props.py`)
    - **Property 29: 홈 오늘 날씨의 Live 전환**
    - **Validates: Requirements 10.5, 10.7, 10.8**

- [ ] 5. 흐름 C: 프론트엔드 (PR #10 UI 구조 기준)
  - [ ] 5.1 Service Worker와 manifest (`frontend/public/sw.js`, `frontend/public/manifest.webmanifest`, `frontend/index.html`, `frontend/src/sw.test.ts`)
    - design "Mobile_Web_Client" 절의 push / notificationclick 처리(tag `weatherwear-daily-brief`, 열린 창 focus + navigate, 없으면 openWindow), manifest(`display: standalone`) 연결
    - `sw.test.ts`: `self`를 흉내 내 `showNotification` 인자와 클릭 동작을 확인한다
    - _Requirements: 16.1_

  - [ ] 5.2 푸시·위치 모듈 (`frontend/src/lib/push.ts`, `frontend/src/lib/location.ts`)
    - `pushState()`, `urlBase64ToUint8Array()`, `enablePush()`(사용자 동작에서만 `requestPermission` → `register("/sw.js")` → 공개키 → `subscribe` → POST → localStorage), `disablePush()`
    - `shouldReportLocation()`, `reportLocation()`(enableHighAccuracy false, timeout 10초, maximumAge 30분, 거부·시간 초과 결과 반환)
    - 새 `VITE_` 변수 없음. secure context가 아니면 `unsupported`
    - _Requirements: 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.10_

  - [ ]* 5.3 푸시 상태·위치 재보고 property test 작성 (`frontend/src/lib/push.test.ts`)
    - **Property 27: 푸시 상태와 위치 재보고 판정 (프론트엔드)**
    - **Validates: Requirements 16.5, 16.6, 16.12**

  - [ ] 5.4 API 클라이언트, 한국어 문구, 아이콘, 스타일 (`frontend/src/api/client.ts`, `frontend/src/lib/strings.ts`, `frontend/src/components/Icon.tsx`, `frontend/src/styles/automation.css`, `frontend/src/styles/app.css`)
    - `Icon` 이름 목록에 `bell`만 추가한다. Phase 2 스타일은 전부 새 `automation.css`에 두고 `app.css`에는 import 한 줄만 넣는다(5.5~5.8은 `app.css`를 고치지 않는다)
    - `schema.d.ts` 타입만 쓰는 Phase 2 함수: `getAutomationStatus`, `getGalleryImports`, `reanalyzeImport`, `confirmImport`, `postLocation`, `getLocation`, `getNotifications`, `runDailyBrief`(30초), `getVapidPublicKey`, `postPushSubscription`, `deletePushSubscription`, `postPushTest`
    - `strings.ts`에 Phase 2 문구 묶음 `notifications`, `push`, `location`, `gallery`, `brief`, `nav.notifications`를 **한 번에** 추가한다(5.5~5.8은 이 키만 쓴다)
    - _Requirements: 16.9, 16.10, 16.8_

  - [ ] 5.5 Daily_Brief_Card와 홈 화면 (`frontend/src/components/DailyBriefCard.tsx`, `frontend/src/pages/HomePage.tsx`)
    - 오늘(`app_timezone`) daily_brief 알림 1건만 표시, fixture 배지, 탭하면 `url`로 이동. `HomePage`의 `home-dashboard` 첫 영역 `<div className="home-brief">`로 `home-weather`(WeatherHero) 앞에 둔다. `WeatherHero`, `RecommendationCard`, `NotificationCard` 등 PR #10 컴포넌트 코드는 고치지 않는다
    - _Requirements: 14.6, 14.7_

  - [ ] 5.6 Notifications_Page, 탭, 라우트, 자동 위치 보고 (`frontend/src/pages/NotificationsPage.tsx`, `frontend/src/components/{PushToggle,LocationToggle,GalleryImportList}.tsx`, `frontend/src/components/layout/BottomTabBar.tsx`, `frontend/src/components/layout/AppShell.tsx`, `frontend/src/components/layout/AppShell.test.tsx`, `frontend/src/App.tsx`)
    - 푸시 상태 4종과 켜기/끄기·차단 안내, 위치 상태와 "현재 위치 사용", 브리핑 사용 여부·시각(읽기 전용), 최근 알림, "확인이 필요한 사진"(uncertain)과 "착장 맞음"(confirm-outfit, 처리 중 비활성, 성공 시 "기록에 추가했어요"와 목록 갱신, 409는 상태 다시 조회)·"다시 분석", 처리 기록 칩(`estimated`에 "촬영 정보 추정")
    - `BottomTabBar`의 tabs 배열에 `{ to: "/notifications", label: STRINGS.nav.notifications, icon: "bell", end: false }`를 넣어 홈 / 기록 / 알림(+개발 모드 데모), `App.tsx`에 `/notifications` 라우트, `AppShell`에서 `shouldReportLocation`이 true일 때만 조용히 위치 보고(권한 요청 없음)
    - Phase 1 `AppShell.test.tsx`의 탭 기대값을 [홈, 기록, 알림]으로 고친다
    - _Requirements: 16.2, 16.6, 16.8, 6.9, 5.5, 20.4_

  - [ ] 5.7 기록 화면 재조회 (`frontend/src/pages/HistoryPage.tsx`)
    - `visibilitychange`로 `visible`이 되면 GET /api/outfits를 다시 부르고, 이전 목록은 유지한다(Phase 1 Req 12.7). 기존 Phase 1 기록 화면 테스트는 고치지 않고 통과해야 한다
    - _Requirements: 16.11_

  - [ ] 5.8 데모 관리 화면 추가 (`frontend/src/pages/DemoPage.tsx`)
    - "오늘 브리핑 지금 보내기"(처리 중 비활성, 성공 시 제목·본문, 실패·30초·409 표시), 자동화 상태 카드(감시 동작·마지막 확인·최근 처리 5건·다음 브리핑·NOTIFIER). 기존 스캔·시나리오 컨트롤은 그대로 둔다
    - _Requirements: 13.4, 17.4_

  - [ ] 5.9 번들 비밀값 검사 확장 (`frontend/scripts/check-bundle.mjs`)
    - `PRIVATE KEY` 문자열, VAPID_KEY_DIR(환경 변수 → 루트 `.env` → `data/secrets`)의 `vapid_private.pem` 본문 base64 연속 16자 창 검사. 파일 이름만 출력하고 키·일치 내용은 출력하지 않는다
    - _Requirements: 18.8_

  - [ ]* 5.10 프론트엔드 화면 테스트 작성 (`frontend/src/pages/NotificationsPage.test.tsx`, `frontend/src/components/DailyBriefCard.test.tsx`, `frontend/src/pages/HomePage.smoke.test.tsx`, `frontend/src/pages/HistoryPage.smoke.test.tsx`, `frontend/src/pages/DemoPage.smoke.test.tsx`)
    - Phase 1 smoke 파일 3개(`HomePage`·`HistoryPage`·`DemoPage`)에는 케이스만 추가하고 기존 기대값은 고치지 않는다
    - 마운트 시 권한 요청 0회, 상태 4종 문구, 켜기·끄기 흐름, 위치 거부 안내, "착장 맞음" 성공·409·처리 중 비활성, 오늘 브리핑만 표시와 `home-dashboard` 첫 영역 위치, `visibilitychange` 재조회, "지금 보내기"와 자동화 상태
    - _Requirements: 16.2, 16.8, 16.11, 14.6, 13.4, 17.4, 6.9_

- [ ] 6. Checkpoint - 흐름별 확인
  - 흐름 A·B·C의 테스트와 Phase 1 전체 테스트가 외부 연결 없이 통과하는지, `npm run typecheck`와 `npm run check:bundle`이 통과하는지 확인한다. 질문이 생기면 사용자에게 묻는다.
  - _Requirements: 20.5_

- [ ] 7. 통합, 의존성, README (리드)
  - [ ] 7.1 컨테이너 연결과 lifespan (`backend/app/deps.py`, `backend/app/main.py`)
    - `build_container()`가 `build_gallery_runtime()`, `build_brief_runtime()`, LocationService(`place_label` 연결)를 부른다. lifespan: NOTIFIER=webpush이면 `vapid_keys.ensure()`(실패해도 기동 계속), 감시·스케줄러 데몬 스레드 시작, 종료 시 `stop.set()` + join(35초) + engine dispose
    - `install_logging()`의 RedactingFilter에 VAPID 키 문자열 등록(키를 처음 만들 때도 등록)
    - 기본 AutomationConfig(꺼짐)에서는 스레드를 만들지 않고 Gallery_Dir를 읽지 않는다
    - 결정 A8: `weather_provider == "open_meteo"`일 때만 `TodayWeatherService(..., live_current=lambda: current_weather.get_current(location.brief_location()[0]))`로 만든다(4.16). fixture 모드는 `None`
    - 확정 경로: `OutfitConfirmService`를 컨테이너에 넣어 3.20의 confirm-outfit 경로가 쓰게 한다
    - _Requirements: 1.1, 1.2, 1.3, 12.1, 12.2, 15.1, 18.7, 4.6, 10.7_

  - [ ] 7.2 자동화 상태 API (`backend/app/api/automation.py`, `backend/tests/test_api_automation.py`)
    - `GET /api/automation/status`: 감시·브리핑·Notifier·위치 상태를 모은다(경로·키·endpoint·위경도 원값 없음)
    - 예제: `test_automation_status_fields_no_secrets`, `test_status_reflects_disabled_defaults`, `test_status_dir_unavailable_without_path`
    - _Requirements: 17.1, 17.2, 1.5_

  - [ ] 7.3 lifespan·종단 테스트 (`backend/tests/test_lifespan_automation.py`, `backend/tests/test_automation_e2e.py`)
    - `test_lifespan_starts_and_stops_threads`(감시·스케줄러 on, `poll_seconds=0.5`, 종료 후 스레드 없음)
    - `test_fixture_end_to_end_simulate_to_brief`: tmp Gallery_Dir에서 `simulate_capture.main()` → `poll_once()` 두 번 → `imported` → GET /api/outfits에 포함 → POST /api/daily-brief/run → GET /api/notifications 1건, 소켓 가드로 외부 연결 0건
    - _Requirements: 20.1, 20.2, 20.3, 1.1, 1.2_

  - [ ] 7.4 의존성 고정 (`backend/requirements.txt`)
    - `pywebpush`, `cryptography`를 설치하고 전체 테스트가 통과한 버전으로 `==` 고정. Phase 1 `test_dependencies_pinned`가 통과하는지 확인한다
    - _Requirements: 20.6_

  - [ ] 7.5 README Phase 2 절 (`README.md`)
    - 감시·브리핑 켜기(`.env.example`과 `compose.yaml`은 `true`, 코드 기본값은 `false`), Phase 2 변수 9개와 기본값, fixture 모드에서 overlay 없는 새 사진은 `uncertain`이고 실제 분석은 `VISION_PROVIDER=gemini`일 때만 된다는 점, uncertain 사진을 알림 탭 "착장 맞음"으로 기록에 넣는 방법, EXIF 없는 사진은 파일 수정 시각을 쓴다는 점, Live 모드에서만 홈 날씨가 마지막 위치 기준이라는 점, `simulate_capture.py`·`watch_gallery.py`·`daily_brief.py` 사용법, secure context와 iOS 홈 화면 조건, 감시 사진은 사용자 본인 사진으로 `data/user/`에만 두고 manifest(데모 사진 189장)에 넣지 않는다는 점
    - Docker Compose로 띄울 때: `GALLERY_HOST_DIR`, `./data/user` 공유, `vapid-keys` volume, 호스트에서 `simulate_capture.py`를 돌릴 때 `--lat/--lon`을 주거나 기본 위치를 쓴다는 점
    - 번호 매긴 Phase 2 데모 절차(API 키 없이 fixture 모드): 기동 → 알림 탭에서 위치·알림 허용 → `simulate_capture.py` → 기록 화면에 새 착장 → 데모 화면 "지금 보내기" → 홈 카드와 알림 목록
    - 저장소 링크는 `https://github.com/jgoneit/kirothon-020`으로 맞춘다. Phase 2 개선 목록에서 Web Push와 갤러리 대체 감시를 완료로 옮기고, 실제 갤러리 접근과 FCM은 범위 밖으로 남긴다
    - _Requirements: 20.7, 20.8, 4.3, 4.4_

  - [ ] 7.6 Docker Compose 자동화 변수와 볼륨 (`compose.yaml`, `backend/tests/test_compose_automation.py`)
    - backend 서비스 `environment`에 `GALLERY_WATCH_ENABLED: ${GALLERY_WATCH_ENABLED:-true}`, `DAILY_BRIEF_ENABLED: ${DAILY_BRIEF_ENABLED:-true}`, `DAILY_BRIEF_TIME`, `NOTIFIER: ${NOTIFIER:-log}`, `VAPID_SUBJECT`와 고정 경로 `GALLERY_DIR=/data/gallery`, `USER_PHOTO_DIR=/data/user/photos`, `VAPID_KEY_DIR=/data/secrets`
    - `volumes`에 `${GALLERY_HOST_DIR:-./ex_img}:/data/gallery:ro`, `./data/user:/data/user`, `vapid-keys:/data/secrets`(최상위 `volumes:`에도 선언). 기존 `read_only: true`, DB named volume, `/data/photos:ro`는 그대로 둔다
    - 예제: `test_compose_has_automation_env_and_volumes`(파일 텍스트를 읽어 위 키·마운트가 있고 `read_only: true`가 남아 있는지 확인, docker는 부르지 않는다)
    - _Requirements: 18.10_

- [ ] 8. Final checkpoint - Phase 2 전체 확인
  - 백엔드 `cd backend && python -m pytest -q`, 프론트엔드 `npx vitest --run`, `npm run typecheck`, `npm run check:bundle`이 API 키와 외부 연결 없이 모두 통과하는지 확인하고, README 데모 절차를 fixture 모드로 한 번 따라 한다. 질문이 생기면 사용자에게 묻는다.
  - _Requirements: 20.5_

## Notes

- `*`가 붙은 sub-task는 선택 항목이며 빠른 구현을 위해 건너뛸 수 있다. 모든 property test(Hypothesis, fast-check)와 프론트엔드 화면 테스트 묶음이 여기에 속한다. Requirement 20.3의 예제 테스트 영역 (a)~(n)은 필수 태스크 안에 있다.
- **기준선은 main `cf87e92`(PR #6·#7·#8·#10 병합)다.** 데모 사진은 189장(생성 사진 141~155는 EXIF 없이 사이드카)이고, `ex_img/` 기준선 동작은 바뀌지 않는다. 프론트엔드 파일과 컴포넌트 이름은 PR #10 구조(`HomePage`, `WeatherHero`, `RecommendationCard`, `BottomTabBar`, `AppShell`, `Icon`, `styles/app.css`)를 따른다. 저장소는 `jgoneit/kirothon-020`이다.
- **흐름과 소유 파일.**
  - 리드: 1.x, 7.x. `config.py`, `db.py`, `db_automation.py`, `schemas_automation.py`, `location.py`, `deps.py`, `main.py`, Phase 1 파일 3개(`ingestion/service.py`, `recommendation/service.py`, `repository.py`), `.env.example`, `.gitignore`, `compose.yaml`, README, `requirements.txt`를 맡는다.
  - A: 3.x. `app/gallery/`(확정 `confirm.py` 포함), `providers/vision/fixture.py`·`factory.py`, `api/outfits.py`, `api/gallery.py`, `scripts/watch_gallery.py`, `scripts/simulate_capture.py`를 맡는다.
  - B: 4.x. `app/notifications/`, `providers/weather/forecast.py`, Phase 1 `app/today_weather.py`(4.16), `api/notifications.py`·`push.py`·`daily_brief.py`, `scripts/daily_brief.py`를 맡는다.
  - C: 5.x. `frontend/`를 맡는다. 단 `schema.d.ts`는 1.7에서 리드가 재생성한다. `app.css`는 5.4가 import 한 줄만 넣고, Phase 2 스타일은 `styles/automation.css`에 둔다.
  - 같은 wave 안의 태스크는 파일이 겹치지 않는다.
- **흐름 사이 의존은 네 곳이다.** 3.9(A)는 4.1(B)의 `ForecastClient`를 쓴다. 3.20(A)은 1.6(리드)의 `ingest_file(analysis_override, before_commit)`을 쓴다. 7.1(리드)은 3.13·4.11의 runtime builder, 3.20의 `OutfitConfirmService`, 4.16의 `live_current` 인자를 연결한다. 흐름 A·B는 `deps.py`를 고치지 않고 builder 함수와 생성자 인자만 내보낸다.
- property test 생성기는 각 `*_props.py` 파일 안에 둔다. Phase 1 `tests/strategies.py`는 읽기만 한다.
- Phase 1 테스트 중 고치는 것은 Requirement 20.4의 5곳뿐이다(1.2, 1.4, 1.7, 5.6). 1.6, 3.7, 3.14, 4.16, 5.4, 5.5, 5.7, 5.8, 5.10, 7.6은 Phase 1 파일을 고치거나 테스트 케이스를 더하지만 기존 테스트는 그대로 통과해야 한다.
- 4.1은 구현 전에 Open-Meteo Forecast 문서를 확인하는 태스크다(design "근거와 가정" 표의 가정). 7.4는 `pywebpush`·`cryptography` 버전을 확인하는 태스크다. 둘 다 구현자 환경의 네트워크가 필요하고, 테스트는 계속 오프라인이다.
- 실제 브라우저 푸시 수신 확인(Chrome, `NOTIFIER=webpush`)과 Finder 복사 중 debounce 확인, 360/390/430px 가로 스크롤 확인은 design "수동 확인" 절에 두고 태스크에 넣지 않았다.
- 각 태스크는 requirements.md의 세부 기준 번호를 참조한다. Property 번호는 design.md의 Correctness Properties 절을 따른다.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.4", "1.6"] },
    { "id": 2, "tasks": ["1.3", "1.5", "1.7"] },
    { "id": 3, "tasks": ["1.8", "3.1", "3.3", "3.5", "3.7", "4.1", "4.4", "4.6", "4.7", "5.1", "5.4", "5.9"] },
    { "id": 4, "tasks": ["1.9", "3.2", "3.4", "3.6", "3.8", "3.9", "4.2", "4.3", "4.5", "4.8", "4.9", "5.2", "5.5", "5.7", "5.8"] },
    { "id": 5, "tasks": ["3.10", "3.11", "3.14", "3.16", "3.17", "4.10", "4.11", "4.16", "5.3", "5.6"] },
    { "id": 6, "tasks": ["3.12", "3.13", "3.15", "3.18", "3.20", "4.12", "4.13", "4.17", "5.10"] },
    { "id": 7, "tasks": ["3.19", "3.21", "4.14", "4.15"] },
    { "id": 8, "tasks": ["7.1", "7.4", "7.6"] },
    { "id": 9, "tasks": ["7.2"] },
    { "id": 10, "tasks": ["7.3"] },
    { "id": 11, "tasks": ["7.5"] }
  ]
}
```
