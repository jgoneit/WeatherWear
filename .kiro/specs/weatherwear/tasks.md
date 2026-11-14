# Implementation Plan: WeatherWear (Phase 1 MVP)

## Overview

6시간 데모 안에 끝낼 수 있도록 얇은 수직 슬라이스 순서로 구현한다. 백엔드는 Python(FastAPI, SQLModel, Hypothesis), 프론트엔드는 TypeScript(React, Vite, Tailwind v4, vitest, fast-check)를 쓴다.

순서는 다음과 같다. ① 공유 계약(`config.py`, `domain.py`, `db.py`, `schemas.py`)과 골격, fixture 데이터 ② 사이드카·이미지 검증, Vision Provider, Open-Meteo Archive 날씨 ③ 저장소, 수집 서비스 ④ 피드백, 오늘 날씨, rule_based_v1 추천 ⑤ REST API ⑥ Soft Glass + Bento 스타일 모바일 UI ⑦ 통합 테스트, README, 5분 데모 확인.

공유 계약 파일을 먼저 만들어 두면 이후 작업(수집, 추천, 프론트엔드)을 서로 기다리지 않고 진행할 수 있다. 모든 테스트는 외부 API를 호출하지 않는다. `conftest.py`의 소켓 가드가 localhost 외 연결을 막고, Open-Meteo는 `respx`로, Gemini는 가짜 클라이언트로 대체한다.

## Tasks

- [x] 1. 공유 계약과 프로젝트 골격
  - [x] 1.1 저장소 골격, 의존성 고정 초안, 테스트 하네스 만들기
    - `backend/app/` 패키지 디렉터리(`api/`, `ingestion/`, `providers/vision/`, `providers/weather/`, `recommendation/`)와 `__init__.py`, `backend/tests/` 생성
    - `backend/requirements.txt`에 design의 "기술 스택과 고정 버전 후보" 백엔드·테스트 패키지를 `==`로 기재
    - 저장소 루트 `.env.example`에 12개 변수(GEMINI_API_KEY 빈 값 포함)의 이름, 기본값, 한 줄 설명 작성
    - 루트 `.gitignore`에 `.env`, `!.env.example`, `data/weatherwear.db*`, `node_modules/`, `dist/` 추가
    - `backend/tests/conftest.py`: autouse 픽스처로 `socket.socket.connect`를 감싸 `127.0.0.1`/`localhost` 외 연결 시 `RuntimeError`, `GEMINI_API_KEY` 환경 변수 제거, Hypothesis `ci` 프로필(`max_examples=100, deadline=None`) 등록·로드
    - _Requirements: 15.1, 16.1, 16.3, 16.4, 17.5, 17.7_

  - [x] 1.2 Config_Loader 구현 (`backend/app/config.py`)
    - `RawSettings`(pydantic-settings, 프로세스 환경 변수 > `.env` > 기본값), `AppConfig`, `ConfigIssue`, `resolve_config()`, `parse_cors_origins()` 구현
    - provider 허용값 검사 후 `fixture` 대체, `VISION_PROVIDER=gemini`인데 키·모델이 비면 누락 변수 이름만 기록하고 `fixture` 사용
    - `TEMPERATURE_TOLERANCE_C`(빈 값 3.0, `0 < x < 5.0`, NaN/inf 거부), 위경도 범위, `APP_TIMEZONE` IANA 검증
    - `gemini_api_key`는 `SecretStr`로 보관, 로그 레코드 메시지와 예외 텍스트의 키 문자열을 `***`로 바꾸는 `RedactingFilter` 구현
    - _Requirements: 5.4, 6.2, 15.6, 16.2, 16.5, 16.7, 16.8, 16.9_

  - [ ]* 1.3 설정 해석 property test 작성 (`backend/tests/test_config_props.py`)
    - **Property 29: 설정 해석은 항상 유효한 설정을 만든다**
    - **Validates: Requirements 6.2, 15.6, 16.8, 16.9, 5.4**

  - [x] 1.4 도메인 값 객체 구현 (`backend/app/domain.py`)
    - `ThermalVote`, `FeedbackSource`, `ClothingTags`(`extra="forbid"`, 길이·개수·범위 제약), `UNKNOWN_TAGS`, `SidecarMetadata`, `WeatherReading`, `WeatherContext`, `OutfitWithWeatherAndFeedback`, `RecommendationResult` 정의
    - _Requirements: 3.1, 3.6, 4.3, 7.9, 15.8_

  - [ ]* 1.5 의류 태그 검증 property test 작성 (`backend/tests/test_vision_props.py`)
    - **Property 5: 의류 태그 검증은 제약을 만족할 때만 통과한다**
    - **Validates: Requirements 3.1, 3.4**

  - [ ]* 1.6 의류 태그 round-trip property test 작성 (`backend/tests/test_vision_props.py`)
    - **Property 6: 의류 태그 round-trip**
    - **Validates: Requirements 3.9**

  - [x] 1.7 SQLite 테이블과 엔진 구현 (`backend/app/db.py`)
    - SQLModel 테이블 `outfits`, `weather_snapshots`, `feedback`을 ER 다이어그램대로 정의(`sha256 UNIQUE`, `weather_snapshots.outfit_id UNIQUE` + `ON DELETE CASCADE`, CHECK 제약, 인덱스)
    - 시각은 ISO 8601 TEXT(`captured_at` 원본 오프셋, `captured_at_utc` 정렬용)로 저장
    - 연결 시 `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, `DATABASE_URL` 부모 디렉터리 생성, `create_all()`
    - _Requirements: 1.4, 1.5, 2.2, 4.4, 9.5_

  - [x] 1.8 API 스키마 구현 (`backend/app/schemas.py`)
    - `ScanError`, `ScanReport`, `DemoStatus`, `ScenarioListResponse`, `ScenarioSelection`, `AppMeta`, `WeatherContextOut`, `WeatherSnapshotOut`, `OutfitOut`, `RecommendationItemOut`, `RecommendationResponse`, `FeedbackIn`(`strict=True`, `extra="ignore"`), `FeedbackOut`, 공통 `ErrorResponse`
    - 모든 datetime은 `AwareDatetime`, 선택 필드는 생략 없이 `null`
    - _Requirements: 1.2, 8.1, 9.1, 9.2, 10.4, 10.6, 10.10_

  - [x] 1.9 Hypothesis 생성기 작성 (`backend/tests/strategies.py`)
    - `temps()`(−30.0~45.0, 0.05 단위, 경계값 혼합), `aware_datetimes()`(−12:00~+14:00), `clothing_tags()`, `sidecars()`, `candidate_sets()`(weather/기온/투표 None, 동일 diff·score 포함), `archive_payloads()`, `raw_env()`
    - _Requirements: 17.4_

- [x] 2. 데모 데이터 준비
  - [x] 2.1 `ex_img/` 사진 사용 권한 확인 후 manifest 작성 (`data/demo/photos.manifest.json`)
    - 사용자(프로젝트 소유자)가 `ex_img/`의 159장(PNG 119장 `007_…`~`125_…`, JPG 40장 `2025-01-08_081500_KST.jpg`~`2025-12-17_175400_KST.jpg`) 전체의 사용 권한을 확인했고, 159장 모두를 데모 세트에 넣기로 했다. 사용자 확인에 따라 manifest는 159장을 모두 `rights_confirmed: true`로 기록해야 한다. `source`는 "프로젝트 소유자 제공", `license`는 "프로젝트 소유자가 ex_img/ 159장의 데모 사용 권한을 확인함"으로 적는다
    - `ex_img/`에 이후 새 파일이 생기면 그 파일은 `rights_confirmed: false`로 두고 사용자에게 확인한다. 에이전트가 임의로 권한을 확정하지 않는다. 현재 `126_…` 이후 PNG(`126_blank_baseball_jersey` 등)가 이 경우에 해당한다
    - placeholder fallback(확인된 사진 0장)은 현재 쓰지 않지만 manifest 형식은 그대로 지원한다
    - _Requirements: 15.2, 15.3, 15.7_

  - [x] 2.2 사진 복사 스크립트와 placeholder fallback 구현
    - `scripts/prepare_demo_photos.py`: manifest의 `rights_confirmed: true` 항목 전체(현재 159장: PNG 119, JPG 40)를 `ex_img/`에서 `data/demo/photos/`로 파일 이름·확장자 그대로 복사, 사이드카 템플릿이 없으면 생성(JPG는 파일 이름의 날짜·시각으로 `captured_at` 채움)
    - `scripts/make_placeholders.py`: 확인된 사진이 0장일 때만 Pillow로 8장의 placeholder PNG(기온색 배경 + 착장 텍스트)와 사이드카 생성. 생성물은 커밋해 해시를 고정
    - 스크립트를 실행해 `data/demo/photos/`를 159장으로 채운다(복사본 약 290MB)
    - _Requirements: 15.2, 15.3, 15.7_

  - [x] 2.3 사이드카, 시나리오, fixture 파일 작성
    - 159장 사이드카 작성: design "사이드카 작성 규칙"대로 PNG 119장은 사진마다 서로 다른 `captured_at`(`+09:00`, 배정 기온에 맞는 계절, JPG 시각과 겹치지 않게), JPG 40장은 파일 이름의 날짜·시각을 KST `+09:00`으로 그대로 사용, 서울 위경도, `thermal_vote`는 규칙 T6(081·021·076=comfortable, 033=cold, 나머지는 약 1/3에만, 비착장 JPG 20장은 투표 없음)
    - `data/demo/fixtures/weather.json`: design "사진별 fixture 기온" 표대로 159건의 기온(0.5°C 단위, −9.0~26.5)과 체감·습도·풍속·강수(rain/rainy 사진 040·080·107·113과 `2025-07-14_182600_KST`는 강수 > 0) 작성, `today_default`는 9.0°C. 규칙 T1~T6을 지킨다
    - `data/demo/scenarios.json`: 시나리오 4개(쌀쌀한 아침 9.0, 기록적 한파 −12.0, 때 이른 무더위 30.0, 기록적 폭염 38.0)
    - `scripts/build_fixtures.py`: 159장의 SHA-256을 계산해 `data/demo/fixtures/vision.json` 키를 채운다. 태그 값은 각 사진을 보고 작성하고, 파일 이름에서 나온 값은 사진 내용과 맞을 때만 쓴다. 보이지 않는 속성은 `unknown`, 비착장 JPG 20장은 `UNKNOWN_TAGS`와 같은 값. 사진이 파일 이름과 맞지 않아 기온을 옮기면 T1~T6을 지키고 design 표도 고친다
    - _Requirements: 2.6, 3.3, 4.6, 15.2, 15.8, 15.9_

  - [ ]* 2.4 데모 데이터 구성 테스트 작성 (`backend/tests/test_demo_data.py`)
    - `test_demo_data_layout`: `data/demo/photos`의 사진 수가 manifest의 `rights_confirmed: true` 항목 수와 같고(현재 159) 모든 사진이 manifest에 있음, 사이드카 1:1, vision·weather fixture 키가 모든 사진을 포함, fixture 기온이 규칙 T1~T6을 만족. 확인된 사진이 0장이면 placeholder 6~10장
    - _Requirements: 15.2, 15.3, 15.7, 15.9_

- [x] 3. 사이드카 파싱과 이미지 검증
  - [x] 3.1 사이드카 파서 구현 (`backend/app/ingestion/sidecar.py`)
    - 순수 함수 `parse_sidecar_text(text) -> SidecarMetadata | SidecarError`, `sidecar_path_for(image)` 구현
    - 오류 코드: `SIDECAR_NOT_FOUND`, `SIDECAR_PARSE_ERROR`, `SIDECAR_MISSING_FIELD`(+필드), `SIDECAR_CAPTURED_AT_INVALID`(오프셋 없음 포함), `SIDECAR_OUT_OF_RANGE`(문자열·불리언 거부, +필드), `SIDECAR_THERMAL_VOTE_INVALID`. 추가 필드는 무시
    - _Requirements: 2.1, 2.3, 2.4, 2.5, 2.8, 2.9, 2.11_

  - [ ]* 3.2 사이드카 round-trip property test 작성 (`backend/tests/test_sidecar_props.py`)
    - **Property 3: 사이드카 round-trip**
    - **Validates: Requirements 2.8, 2.2**

  - [ ]* 3.3 사이드카 결함 보고 property test 작성 (`backend/tests/test_sidecar_props.py`)
    - **Property 4: 사이드카 검증은 결함 종류를 정확히 보고하고 추가 필드는 무시한다**
    - **Validates: Requirements 2.3, 2.4, 2.5, 2.9, 2.11**

  - [x] 3.4 이미지 파일 검사 구현 (`backend/app/ingestion/image_check.py`)
    - `SUPPORTED_EXTENSIONS`, `MAX_IMAGE_BYTES=20MB`, `list_image_files()`(최상위 일반 파일, 확장자 대소문자 무시, `*.metadata.json` 제외, 이름순), `check_image()`(Pillow `verify()` + 감지 형식과 확장자 일치)
    - _Requirements: 1.1, 1.8, 1.12_

- [x] 4. Vision Provider
  - [x] 4.1 Vision 인터페이스, Fixture 구현, 팩토리 작성 (`backend/app/providers/vision/base.py`, `fixture.py`, `factory.py`)
    - `VisionError(kind)`, `VisionProvider` Protocol, `FixtureVisionProvider`(기동 시 `vision.json` 로드, 미등록 해시는 `UNKNOWN_TAGS`), `build_vision_provider(cfg)`
    - _Requirements: 3.2, 3.3, 15.5, 15.8_

  - [ ]* 4.2 Fixture Vision property test 작성 (`backend/tests/test_vision_props.py`)
    - **Property 7: Fixture Vision은 해시별로 결정적이고 미등록 해시에는 unknown 기본값을 준다**
    - **Validates: Requirements 3.3, 15.8**

  - [x] 4.3 Gemini 모델 ID 확인 (첫 live Vision 호출 전)
    - Google AI 공식 모델 문서와 `google-genai` 문서에서 현재 Stable Flash 모델 ID를 다시 확인한다. design 기본값 `gemini-4-hydrogen`와 다르면 `config.py`의 `GEMINI_MODEL` 기본값과 `.env.example`을 고친다
    - `scripts/check_gemini_model.py` 작성: `GEMINI_API_KEY`가 있을 때만 `client.models.get(model=GEMINI_MODEL)`로 모델 존재를 확인하고 결과(모델 ID만, 키 제외)를 출력. 키가 없으면 실행하지 않고 안내만 출력. pytest에서는 실행하지 않는다
    - 확인 결과(날짜, 모델 ID, 출처 URL)를 README 작성(17.4)에 넘길 수 있도록 메모해 둔다
    - _Requirements: 3.2, 16.2, 16.5_

  - [x] 4.4 GeminiVisionProvider 구현 (`backend/app/providers/vision/gemini.py`)
    - `genai.Client` 생성(SDK 자동 재시도 끔, 30초 timeout, `concurrent.futures` 30초 이중 안전장치), 클라이언트는 생성자로 주입 가능하게 한다(테스트용 가짜 클라이언트)
    - Pillow로 EXIF를 제거한 이미지 바이트 1개 + 고정 프롬프트 1개만 전송, `response_schema=ClothingTags`, `temperature=0`
    - 응답을 `ClothingTags.model_validate_json`으로 재검증, 실패 시 `VisionError("schema_invalid")`, API 오류 `api_error`, 시간 초과 `timeout`
    - _Requirements: 3.2, 3.4, 3.5, 3.6, 3.7_

  - [ ]* 4.5 Gemini 요청 내용 property test 작성 (`backend/tests/test_vision_props.py`)
    - **Property 8: Gemini 요청에는 이미지와 프롬프트만 들어간다**
    - **Validates: Requirements 3.7**

  - [ ]* 4.6 Gemini 단위 테스트 작성 (`backend/tests/test_vision.py`)
    - `test_gemini_factory_uses_model_env`, `test_gemini_schema_invalid_api_error_timeout_fail_once`, `test_prompt_instructs_unknown` (모두 가짜 클라이언트, 네트워크 없음)
    - _Requirements: 3.2, 3.5, 3.6_

- [x] 5. Weather Provider (Open-Meteo Archive API)
  - [x] 5.1 Weather 인터페이스와 정규화 순수 함수 구현 (`backend/app/providers/weather/base.py`, `normalize.py`)
    - `WeatherProvider` Protocol, `select_nearest_hour()`(UTC instant 비교, 동률이면 이른 시각), `normalize_value()`(°F→°C, inch→mm, m/s·mph→km/h, 습도 범위 밖 None), `map_archive_response()`(`temperature_2m`→actual, `precipitation`→mm, `relative_humidity_2m`→%, apparent/wind/weather_code는 None)
    - _Requirements: 4.2, 4.8, 4.12_

  - [ ]* 5.2 가장 가까운 시각 선택 property test 작성 (`backend/tests/test_weather_props.py`)
    - **Property 9: 가장 가까운 시각 선택**
    - **Validates: Requirements 4.2**

  - [ ]* 5.3 단위 정규화 property test 작성 (`backend/tests/test_weather_props.py`)
    - **Property 10: 단위 정규화**
    - **Validates: Requirements 4.8**

  - [x] 5.4 FixtureWeatherProvider 구현 (`backend/app/providers/weather/fixture.py`)
    - `weather.json`에서 `(captured_at UTC instant, round(lat,4), round(lon,4))` 키 조회, 없으면 난수 없는 `seasonal_temp(month, hour)`, `observed_at`은 정시 내림, 항상 `provider='fixture'`, `is_fixture=True`
    - _Requirements: 4.6, 4.10_

  - [ ]* 5.5 Fixture 날씨 결정성 property test 작성 (`backend/tests/test_weather_props.py`)
    - **Property 13: Fixture 날씨는 결정적이다**
    - **Validates: Requirements 4.6, 4.10**

  - [x] 5.6 OpenMeteoWeatherProvider와 팩토리 구현 (`backend/app/providers/weather/open_meteo.py`, `factory.py`)
    - `GET https://archive-api.open-meteo.com/v1/archive`에 `latitude`, `longitude`, `start_date=end_date=`(APP_TIMEZONE 기준 YYYY-MM-DD), `hourly=temperature_2m,precipitation,relative_humidity_2m`, `timezone=APP_TIMEZONE` 요청 1건. API 키 없음
    - `httpx.Client(timeout=10.0)`. 타임아웃, 네트워크 오류, 2xx 아님, JSON 오류, 선택 시각 기온 없음이면 경고 로그 후 fixture 결과 반환
    - `build_weather_provider(cfg, fixture)`
    - _Requirements: 4.5, 4.7, 4.11, 4.12, 5.8_

  - [ ]* 5.7 Archive 요청·응답 매핑 property test 작성 (`backend/tests/test_weather_props.py`)
    - **Property 11: Archive API 요청과 응답 매핑** (respx로 요청 파라미터 검사)
    - **Validates: Requirements 4.11, 4.12, 4.5**

  - [ ]* 5.8 Open-Meteo 실패 대체 property test 작성 (`backend/tests/test_weather_props.py`)
    - **Property 12: Open-Meteo 실패 시 fixture로 대체한다**
    - **Validates: Requirements 4.7, 5.8**

  - [x] 5.9 날씨 정규화 단위 테스트 작성 (`backend/tests/test_weather.py`)
    - 영역 (b): `test_archive_mapping_example`(서울 응답 형태 고정 JSON + respx), `test_open_meteo_no_api_key_param`, `test_fixture_weather_marks_fixture`
    - _Requirements: 4.5, 4.6, 4.12, 17.4_

- [x] 6. 저장소와 수집 서비스
  - [x] 6.1 OutfitRepository와 유효 피드백 함수 구현 (`backend/app/repository.py`, `backend/app/feedback.py`)
    - `feedback.py`에 순수 함수 `effective_vote(records)`(`(created_at, id)` 최대, 0건이면 None, source 무관) 먼저 작성
    - `exists_sha`, `create_outfit_bundle`(Outfit + Weather_Snapshot + 선택적 `demo_seed` feedback 단일 트랜잭션, 예외 시 롤백), `get_outfit`, `list_outfits`(`captured_at_utc DESC, id DESC`), `list_candidates`, `add_feedback`(INSERT만), `counts`
    - `source='demo_sidecar'`, `vision_provider` 저장, `captured_at` 원본 오프셋 유지
    - _Requirements: 1.10, 2.2, 2.6, 2.7, 3.8, 4.3, 4.4, 9.4, 9.5, 10.1_

  - [ ]* 6.2 저장소 round-trip property test 작성 (`backend/tests/test_repository_props.py`)
    - **Property 14: 저장소 round-trip은 시각 오프셋과 null을 보존한다**
    - **Validates: Requirements 2.2, 4.3**

  - [ ]* 6.3 착장 목록 정렬 property test 작성 (`backend/tests/test_repository_props.py`)
    - **Property 27: 착장 목록 정렬**
    - **Validates: Requirements 10.1**

  - [x] 6.4 ImageIngestionService 구현 (`backend/app/ingestion/service.py`)
    - PHOTO_DIR 검사(`exists`, `is_dir`, `os.access`) 실패 시 `PhotoDirUnavailable`(경로 비노출)
    - 파일별 처리 순서: 크기 검사(내용 안 읽음) → SHA-256 → DB/이번 스캔 성공 집합 중복 검사 → `check_image` → 사이드카 → Vision 1회 → Weather → `create_outfit_bundle`. 실패 단계에서 중단하고 `ScanError(file_name, stage, code, message)` 기록
    - `ScanReport` 집계(`scanned = succeeded + duplicate + skipped_non_outfit + uncertain + failed`), 비착장/불확실 사진은 저장하지 않고 decisions에 보고
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.6, 1.7, 1.8, 1.10, 1.11, 1.12, 2.1, 2.10, 3.1, 3.5, 4.1_

  - [ ]* 6.5 스캔 집계 참조 모델 property test 작성 (`backend/tests/test_ingestion_props.py`)
    - **Property 1: 스캔 집계가 참조 모델과 일치한다** (8×8 PNG, Vision 실패 무작위 주입)
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.6, 1.8, 2.1, 2.10, 3.1, 3.5, 4.1**

  - [ ]* 6.6 재스캔 멱등성 property test 작성 (`backend/tests/test_ingestion_props.py`)
    - **Property 2: 재스캔은 멱등이고 실패 파일만 다시 처리한다**
    - **Validates: Requirements 1.5, 1.7, 4.4**

  - [x] 6.7 수집·중복 제거 단위 테스트 작성 (`backend/tests/test_ingestion.py`)
    - 영역 (a): `test_rescan_same_hash_creates_no_outfit`, `test_same_content_two_names_one_outfit`, `test_snapshot_insert_failure_rolls_back_outfit`, `test_photo_dir_missing_notdir_unreadable_raises`(3개 파라미터), `test_file_over_20mb_fails_without_vision`(sparse 파일), `test_seed_vote_saved_as_demo_seed`, `test_source_is_demo_sidecar`, `test_vision_provider_stored`
    - _Requirements: 1.4, 1.5, 1.10, 1.11, 1.12, 2.6, 2.7, 3.8, 17.4_

- [x] 7. Checkpoint - 수집 슬라이스 확인
  - `cd backend && python -m pytest -q`가 외부 연결 없이 통과하는지 확인한다. 질문이 생기면 사용자에게 묻는다.

- [x] 8. 피드백과 오늘 날씨
  - [x] 8.1 FeedbackService 구현 (`backend/app/feedback.py`)
    - `submit(outfit_id, vote)`: Outfit 없으면 `OutfitNotFound`, `created_at=now(UTC, 마이크로초)`, `source='user_action'`, INSERT만, 저장 예외 시 롤백 후 `FeedbackSaveFailed`
    - _Requirements: 9.1, 9.3, 9.5, 9.7, 9.8_

  - [ ]* 8.2 Effective_Vote property test 작성 (`backend/tests/test_feedback_props.py`)
    - **Property 24: Effective_Vote는 최신 기록이다**
    - **Validates: Requirements 9.4**

  - [x] 8.3 TodayWeatherService와 ScenarioStore 구현 (`backend/app/today_weather.py`)
    - `WeatherScenario`, `load_scenarios()`(파일 없음/파싱 실패 시 빈 목록 + 오류, 항목별 검증, 중복 id는 뒤 항목 버림), `ScenarioStore`(메모리, `select`는 알 수 없는 id면 `UnknownScenario`로 상태 유지, `clear`)
    - `get_today()`: 선택 시나리오 → `provider='scenario'`, `is_fixture=True`, `scenario_id/name`. 없으면 `DEFAULT_LATITUDE/LONGITUDE`로 Weather_Provider 조회
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8_

  - [ ]* 8.4 시나리오 로드 property test 작성 (`backend/tests/test_scenarios_props.py`)
    - **Property 15: 시나리오 로드는 유효한 항목만 남긴다**
    - **Validates: Requirements 5.2, 5.6**

  - [ ]* 8.5 시나리오 선택 상태 property test 작성 (`backend/tests/test_scenarios_props.py`)
    - **Property 16: 시나리오 선택 상태는 마지막 유효 선택을 따른다**
    - **Validates: Requirements 5.3, 5.5, 5.7**

  - [x] 8.6 피드백 갱신 단위 테스트 작성 (`backend/tests/test_feedback.py`)
    - 영역 (e): `test_latest_vote_becomes_effective`, `test_same_timestamp_uses_larger_id`, `test_seed_then_user_vote_user_wins`, `test_repeated_same_vote_creates_separate_records`, `test_storage_error_rolls_back_and_keeps_vote`
    - _Requirements: 9.4, 9.5, 9.8, 17.4_

  - [ ]* 8.7 오늘 날씨 단위 테스트 작성 (`backend/tests/test_today_weather.py`)
    - `test_today_uses_scenario_else_provider`, `test_today_uses_default_coordinates`, `test_scenario_response_labeled`
    - _Requirements: 5.1, 5.4, 5.5_

- [x] 9. 추천 rule_based_v1
  - [x] 9.1 추천 전략 구현 (`backend/app/recommendation/base.py`, `rule_based_v1.py`, `factory.py`)
    - `RecommendationStrategy` Protocol, 설정 상수(`FALLBACK_TOLERANCE_C=5.0`, `TEMP_SCORE_BASE=100`, `TEMP_WEIGHT_PER_C=10`, `COMFORTABLE_BONUS=15`, `MISMATCH_PENALTY=15`, `MAX_RESULTS=5`), `build_strategy()`
    - Decimal + ROUND_HALF_UP 소수 첫째 자리 diff, 경계 포함, 5.0 확장 1회, 날씨·기온 없는 후보 제외, `MissingTodayTemperature`
    - 정렬 키 `(−score, tb(apparent), tb(humidity), tb(wind), tb(precip), outfit_id)`, 상위 5건 rank 부여, `confidence_level` 규칙
    - Provider·Repository를 import하지 않는다
    - _Requirements: 6.1, 6.3, 6.5, 6.7, 6.8, 6.9, 7.1, 7.2, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 7.12, 7.13, 8.5_

  - [ ]* 9.2 허용 오차 후보 선택 property test 작성 (`backend/tests/test_recommendation_props.py`)
    - **Property 17: 허용 오차 후보 선택이 참조 모델과 일치한다**
    - **Validates: Requirements 6.1, 6.3, 6.5, 6.7, 6.9**

  - [ ]* 9.3 점수와 순위 property test 작성 (`backend/tests/test_recommendation_props.py`)
    - **Property 18: 점수와 순위가 참조 정렬과 일치한다**
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8**

  - [ ]* 9.4 입력 순서 무관성 property test 작성 (`backend/tests/test_recommendation_props.py`)
    - **Property 19: 추천은 입력 순서와 무관하다**
    - **Validates: Requirements 7.10**

  - [ ]* 9.5 투표 메타모픽 property test 작성 (`backend/tests/test_recommendation_props.py`)
    - **Property 20: 투표를 comfortable로 바꾸면 점수가 오른다**
    - **Validates: Requirements 7.11**

  - [ ]* 9.6 신뢰도 수준 property test 작성 (`backend/tests/test_recommendation_props.py`)
    - **Property 23: 신뢰도 수준 규칙**
    - **Validates: Requirements 8.5, 7.5**

  - [x] 9.7 설명 생성 구현 후 전략에 연결 (`backend/app/recommendation/explain.py`, `rule_based_v1.py`)
    - `VOTE_KO`, `display_tags()`(outer→upper→lower, `none`/`unknown` 제외, 최대 3개), `build_explanation()`(오늘·과거·차이 °C 소수 첫째 자리, "착장 태그 정보 없음", "체감 피드백 기록 없음", 사유와 점수, fallback 문장), 300자 초과 시 태그 20자 절단
    - `rule_based_v1.py`가 `reasons`, `explanation`을 채우도록 연결
    - _Requirements: 7.3, 7.4, 8.1, 8.2, 8.3, 8.4, 8.6_

  - [ ]* 9.8 설명 필수 항목 property test 작성 (`backend/tests/test_recommendation_props.py`)
    - **Property 22: 설명 문장은 필수 항목을 모두 담는다**
    - **Validates: Requirements 8.2, 8.3, 8.4, 8.6, 7.3, 7.4**

  - [ ]* 9.9 추천 결과 불변식 property test 작성 (`backend/tests/test_recommendation_props.py`)
    - **Property 21: 추천 결과 불변식**
    - **Validates: Requirements 7.12, 8.1, 7.9**

  - [x] 9.10 RecommendationService 구현 (`backend/app/recommendation/service.py`)
    - `TodayWeatherService` + `OutfitRepository.list_candidates()` + 전략을 묶어 `RecommendationResponse` 생성(`applied_tolerance_c`, `fallback`), 0건이면 `status="insufficient_data"`, `fallback=True`, `items=[]`, 캐시 없음
    - _Requirements: 6.3, 6.5, 6.8, 8.1, 8.7, 9.6_

  - [x] 9.11 허용 오차 경계·데이터 부족·구조 단위 테스트 작성 (`backend/tests/test_recommendation.py`)
    - 영역 (c): `test_diff_exactly_3_0_included`, `test_diff_3_1_excluded`, `test_rounding_3_04_included_3_05_excluded`, `test_fallback_boundary_5_0_included_5_1_excluded`, `test_fallback_applied_once_only`
    - 영역 (d): `test_insufficient_data_status`, `test_outfits_without_weather_excluded`, `test_missing_today_temperature_raises`
    - `test_strategy_module_imports_no_providers_or_repo`(AST import 검사), `test_explanation_max_length_with_50_char_tags`
    - _Requirements: 6.1, 6.3, 6.5, 6.7, 6.8, 6.9, 7.13, 8.1, 8.7, 17.4_

- [x] 10. REST API
  - [x] 10.1 앱 생성과 의존성 주입 구현 (`backend/app/deps.py`, `backend/app/main.py`)
    - `deps.py`: 설정·Provider·Repository·서비스·`get_strategy` 의존성 함수, 프로세스 전역 `scan_lock`
    - `main.py`: `create_app()`, 기동 시 `resolve_config` + 이슈 로그(ERROR), 로깅 형식과 `RedactingFilter` 설치, `create_all()`, `CORSMiddleware(allow_origins=cfg.cors_origins)`, 공통 오류 형식 예외 핸들러(스택 트레이스 비노출, feedback 경로 검증 오류는 `INVALID_THERMAL_VOTE`, 그 외 `VALIDATION_ERROR`)
    - _Requirements: 7.14, 9.2, 10.6, 15.6, 16.5, 16.7_

  - [x] 10.2 착장 라우터 구현 (`backend/app/api/outfits.py`)
    - `GET /api/outfits`, `GET /api/outfits/{id}`(404 `OUTFIT_NOT_FOUND`), `GET /api/outfits/{id}/photo`(`resolve()`가 PHOTO_DIR 안인지 확인, 404 `PHOTO_NOT_FOUND`, 경로 비노출, StaticFiles 없음), `POST /api/outfits/{id}/feedback`(201, 본문 검증 후 존재 확인, 500 `FEEDBACK_SAVE_FAILED`)
    - `response_model_exclude_none` 사용 안 함
    - _Requirements: 9.1, 9.2, 9.3, 9.7, 9.8, 10.1, 10.2, 10.3, 10.4, 10.5, 10.8, 10.9, 10.10_

  - [x] 10.3 데모·날씨·추천·메타 라우터 구현 (`backend/app/api/demo.py`, `weather.py`, `recommendations.py`, `meta.py`)
    - `POST /api/demo/scan`(본문 무시, `acquire(blocking=False)` 실패 시 409, `PhotoDirUnavailable` 503), `GET /api/demo/status`(`gemini_api_key_configured` 불리언만), `GET /api/demo/scenarios`, `PUT/DELETE /api/demo/scenarios/selected`(404 `UNKNOWN_SCENARIO`)
    - `GET /api/weather/today`, `GET /api/recommendations/today`(503 `TODAY_TEMPERATURE_MISSING`), `GET /api/meta`
    - _Requirements: 1.2, 1.9, 1.11, 1.13, 5.1, 5.3, 5.5, 5.7, 6.8, 8.1, 8.7, 14.3, 16.5_

  - [x] 10.4 라우터 등록과 OpenAPI 타입 재생성
    - `main.py`에 4개 라우터 등록, `uvicorn app.main:app` 진입점 확인
    - `/openapi.json`을 덤프하는 스크립트(`scripts/export_openapi.py`)를 만들고 `openapi-typescript`로 `frontend/src/api/schema.d.ts`를 재생성해 커밋
    - _Requirements: 7.14, 10.6, 10.7_

  - [ ]* 10.5 피드백 이력·즉시 반영 property test 작성 (`backend/tests/test_feedback_props.py`)
    - **Property 25: 피드백 이력 보존과 즉시 반영**
    - **Validates: Requirements 9.1, 9.5, 9.6**

  - [ ]* 10.6 유효하지 않은 thermal_vote property test 작성 (`backend/tests/test_feedback_props.py`)
    - **Property 26: 유효하지 않은 thermal_vote는 422로 거부된다**
    - **Validates: Requirements 9.2**

  - [ ]* 10.7 응답 datetime 오프셋 property test 작성 (`backend/tests/test_api_props.py`)
    - **Property 28: 응답 datetime에는 항상 오프셋이 있다**
    - **Validates: Requirements 10.10**

  - [ ]* 10.8 CORS 출처 property test 작성 (`backend/tests/test_api_props.py`)
    - **Property 30: CORS 출처 해석과 정확 일치**
    - **Validates: Requirements 16.7**

  - [ ]* 10.9 API 키 비노출 property test 작성 (`backend/tests/test_api_props.py`)
    - **Property 31: API 키는 어디에도 출력되지 않는다**
    - **Validates: Requirements 16.5**

  - [x] 10.10 API 상태 코드 단위 테스트 작성 (`backend/tests/test_api.py`)
    - 영역 (f): `test_get_outfits_200_empty_list`, `test_get_outfit_404`, `test_feedback_201`, `test_feedback_404`, `test_feedback_422_variants`, `test_feedback_extra_fields_not_stored`, `test_feedback_storage_error_returns_500`, `test_unknown_scenario_404_keeps_selection`, `test_photo_404_no_path_leak`, `test_null_fields_present_in_json`, `test_openapi_lists_all_paths`
    - `test_scan_body_path_ignored`, `test_photo_dir_unavailable_returns_503`, `test_concurrent_scan_returns_409`, `test_missing_today_temperature_returns_503`, `test_insufficient_data_returns_200_empty`, `test_strategy_injected_via_dependency_override`, `test_env_precedence_process_over_dotenv_over_default`, `test_gemini_missing_key_falls_back_to_fixture_and_logs_name_only`
    - _Requirements: 1.9, 1.11, 1.13, 5.7, 6.8, 7.14, 8.7, 9.1, 9.2, 9.3, 9.7, 9.8, 10.2, 10.4, 10.5, 10.6, 10.8, 10.9, 15.6, 16.2, 17.4_

- [x] 11. Checkpoint - 백엔드 전체 확인
  - `cd backend && python -m pytest -q`가 실패 0건, 종료 코드 0으로 끝나는지 확인한다. 질문이 생기면 사용자에게 묻는다.

- [x] 12. 프론트엔드 기반
  - [x] 12.1 Vite + React + TypeScript + Tailwind v4 골격 만들기 (`frontend/`)
    - `package.json`에 design 표의 버전을 `^`/`~` 없이 고정, `vite.config.ts`(`@tailwindcss/vite`, vitest jsdom), `tsconfig.json`, `typecheck` 스크립트
    - `src/styles/app.css`에 `@theme` 토큰(`--font-sans` Pretendard Variable, `--color-cold/mild/warm/hot`, `--radius-card`), `pretendard` 패키지 자체 호스팅
    - `src/main.tsx`, `src/App.tsx`(react-router: `/`, `/history`, `/outfits/:id`, `/demo`), placeholder 페이지 4개, placeholder `components/layout/AppShell.tsx`, 한국어 문구 상수 `src/lib/strings.ts`
    - `VITE_API_BASE_URL`만 참조
    - _Requirements: 11.5, 11.6, 14.1, 16.6, 17.7_

  - [x] 12.2 API 클라이언트 작성 (`frontend/src/api/schema.d.ts`, `client.ts`, `src/lib/useAsync.ts`)
    - `schemas.py` 기준으로 `schema.d.ts` 초안 작성(10.4에서 재생성)
    - `request<T>()`: `AbortController` 타임아웃(홈·피드백 10초, 데모 30초), `ApiError(status, code)`, `api.getRecommendations/getWeatherToday/getOutfits/getOutfit/postFeedback/scan/getScenarios/selectScenario/clearScenario/getStatus/getMeta`
    - 작은 `useAsync` 훅(이전 데이터 유지, pending 상태)
    - _Requirements: 10.7, 11.9, 13.5, 13.7, 14.5, 14.6_

  - [x] 12.3 표시 형식 순수 함수 구현 (`frontend/src/lib/format.ts`)
    - `formatTemp`, `formatHumidity`, `formatDate(iso, tz)`(`Intl.DateTimeFormat`), `voteLabel`, `displayTags`(백엔드 규칙과 동일), `provenance()`
    - _Requirements: 4.9, 11.2, 11.3, 11.4, 12.2, 15.4_

  - [ ]* 12.4 표시 형식 property test 작성 (`frontend/src/lib/format.test.ts`, fast-check `numRuns: 100`)
    - **Property 32: 프론트엔드 표시 형식**
    - **Validates: Requirements 11.2, 11.3, 11.4, 12.2**

  - [ ]* 12.5 fixture 라벨 property test 작성 (`frontend/src/lib/format.test.ts`)
    - **Property 33: fixture/가상 데이터 라벨은 fixture 출처에만 붙는다**
    - **Validates: Requirements 15.4, 4.9**

- [x] 13. Soft Glass + Bento 홈 화면과 알림 카드
  - [x] 13.1 레이아웃 셸 구현 (`frontend/src/components/layout/`)
    - `AppShell`: 오늘 기온 구간별 메시 그라디언트 배경(≤5 아이스 블루, 6~16 세이지, 17~24 피치, ≥25 코랄, 600ms 전환), sticky 글래스 헤더, 떠 있는 pill 글래스 `BottomTabBar`(홈/기록, `import.meta.env.DEV`일 때만 "데모" 탭), `env(safe-area-inset-bottom)`
    - 360px에서 가로 스크롤 없음(`min-w-0`, `overflow-x-clip`, `max-w-full`), `prefers-reduced-motion` 시 전환 제거, `backdrop-filter` 미지원 시 `bg-white/90`
    - _Requirements: 11.5, 11.6, 14.1, 14.2_

  - [x] 13.2 날씨·추천 공용 컴포넌트 구현 (`frontend/src/components/`)
    - `WeatherHero`(`text-7xl tabular-nums`), `WeatherBento` 2×2(체감·습도·강수·출처), `EstimateNotice`("실측값이 아닌 제공자 추정치/재분석값입니다"), `FixtureBadge`, "가상 데이터" 라벨, `ConfidenceBadge`, `FallbackNotice`("유사 기록 부족 / 범위 확장"), `InsufficientDataState`("개인 착장 데이터 부족"), `ErrorRetry`, `Toast`, `RecommendationCard`(4:5 사진 + 하단 글래스 오버레이, 태그 칩, 근거 펼치기)
    - _Requirements: 4.9, 6.4, 6.6, 11.1, 11.2, 11.3, 11.8, 11.9, 15.4_

  - [x] 13.3 NotificationCard와 FeedbackButtons 구현 (`frontend/src/components/NotificationCard.tsx`, `FeedbackButtons.tsx`)
    - iOS 알림 배너 형태(앱 아이콘, "WeatherWear · 지금", 56px 썸네일, 오늘 기온 문구), 버튼 "🥶 추웠어"/"😊 적당했어"/"🥵 더웠어" → `cold`/`comfortable`/`hot`, 높이 44px 이상
    - `effective_vote` 버튼에만 `aria-pressed=true`, 탭 시 확인 없이 POST 1회, 대기 중 3버튼 disabled, 성공 시 선택 표시 후 `onSaved()`로 추천 재조회, 실패/10초 초과 시 토스트 "저장하지 못했어요. 다시 시도해 주세요."와 이전 선택 복원
    - 본문 탭 → `/outfits/:id`, 버튼 영역 `stopPropagation`
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.8_

  - [ ]* 13.4 NotificationCard 테스트 작성 (`frontend/src/components/NotificationCard.test.tsx`)
    - 버튼 문구·순서·매핑, `aria-pressed` 4가지 상태, POST 1회, 대기 중 disabled, 성공 시 재조회, 실패 시 복원, 본문 탭 이동 (`fetch`는 `vi.fn`)
    - _Requirements: 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.8_

  - [x] 13.5 HomePage 구현 (`frontend/src/pages/HomePage.tsx`)
    - `getRecommendations` 1회 + `getMeta`(app_timezone), 오늘 날짜·기온·체감·습도, NotificationCard, 1순위 RecommendationCard, WeatherBento, 2~5순위 가로 스냅 캐러셀
    - `insufficient_data`면 추천 카드와 NotificationCard 숨김, fallback이면 FallbackNotice, 실패 시 이전 내용 유지 + ErrorRetry, 입력 필드 없음, DEV일 때만 데모 진입 링크
    - _Requirements: 6.4, 6.6, 11.1, 11.4, 11.7, 11.8, 11.9, 13.1, 13.9, 14.1, 14.2_

  - [ ]* 13.6 HomePage 테스트 작성 (`frontend/src/pages/HomePage.test.tsx`)
    - 정상 표시 항목, 데이터 부족 상태, fallback 문구, 실패 후 재시도와 기존 내용 유지, input/textarea 없음, 추정치 안내와 fixture 배지
    - _Requirements: 4.9, 6.4, 6.6, 11.1, 11.7, 11.8, 11.9, 13.9_

- [x] 14. 기록·상세 화면
  - [x] 14.1 HistoryPage 구현 (`frontend/src/pages/HistoryPage.tsx`, `components/OutfitGridItem.tsx`)
    - 2열 그리드, 4:5 `rounded-2xl` 사진(159장이므로 `loading="lazy"`, `decoding="async"`), 우상단 기온 칩(날씨 없으면 "날씨 정보 없음"), 태그 최대 3개, 평가 이모지 칩, sticky 글래스 날짜 섹션 헤더, 반환 순서 유지
    - 빈 상태: 데모 화면에서 폴더 스캔 안내, 실패 시 목록 유지 + 오류 + 다시 시도
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.6, 12.7_

  - [x] 14.2 OutfitDetailPage 구현 (`frontend/src/pages/OutfitDetailPage.tsx`)
    - 전체폭 사진 + bottom-sheet 글래스 패널(태그 전체, 날씨 Bento: 실제 기온과 값이 있는 체감·습도·풍속·강수, 평가 상태), 가상 데이터·fixture 라벨, 404/실패 시 오류 + 다시 시도
    - _Requirements: 4.9, 12.4, 12.5, 12.7, 15.4_

  - [ ]* 14.3 HistoryPage 테스트 작성 (`frontend/src/pages/HistoryPage.test.tsx`)
    - 순서, 날씨 없음 표시, 탭 이동, 빈 상태, 오류
    - _Requirements: 12.1, 12.3, 12.4, 12.6, 12.7_

  - [ ]* 14.4 OutfitDetailPage 테스트 작성 (`frontend/src/pages/OutfitDetailPage.test.tsx`)
    - 표시 항목, 404 오류 처리
    - _Requirements: 12.5, 12.7_

- [x] 15. 데모 관리 화면
  - [x] 15.1 DemoPage 구현 (`frontend/src/pages/DemoPage.tsx`)
    - DEV가 아니면 "데모 관리 화면은 개발 모드에서만 사용할 수 있습니다"만 표시
    - 폴더 스캔 버튼(결과 4개 숫자 Bento 타일 + 실패 파일명·사유, 0건이면 "실패 항목 없음"), 시나리오 선택 목록(현재 선택 표시) + 새로고침 버튼, 추천 재계산 버튼(`GET /api/recommendations/today` 재호출), provider·키 설정 여부("설정됨"/"미설정") 상태
    - 동작별 pending 비활성화, 실패/30초 초과 시 어느 동작이 실패했는지 표시하고 이전 결과·선택 유지, 성공 시 적용 시나리오 이름·기온과 완료 표시
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 16.5_

  - [ ]* 15.2 DemoPage 테스트 작성 (`frontend/src/pages/DemoPage.test.tsx`)
    - DEV 플래그별 노출, 컨트롤 구성, 결과 표시, 중복 방지, 실패 처리, 적용 시나리오 표시
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7_

  - [x] 15.3 번들 비밀값 검사 스크립트 작성 (`frontend/scripts/check-bundle.mjs`, `package.json`)
    - `npm run check:bundle`: `vite build` 후 `dist/`에 `GEMINI` 문자열이 없으면 종료 코드 0, 있으면 1
    - _Requirements: 16.6_

- [x] 16. Checkpoint - 프론트엔드 확인
  - `cd frontend && npx vitest --run`, `npm run typecheck`, `npm run check:bundle`이 통과하는지 확인한다. 질문이 생기면 사용자에게 묻는다.

- [x] 17. 통합, README, 5분 데모 확인
  - [x] 17.1 Fixture 모드 통합 테스트 작성 (`backend/tests/test_integration.py`)
    - `test_fixture_mode_end_to_end_deterministic`: 새 DB 2개에서 기동→스캔→시나리오 선택→추천→피드백을 반복하고 태그·날씨·순위가 같음을 확인, 소켓 가드로 외부 연결 0건
    - `test_demo_scenarios_cover_cases`: 실제 `data/demo` 데이터(159장)로 design 결과 표를 확인한다. 쌀쌀한 아침 9.0 → `fallback=false`, 5건, 모든 차이 ≤ 3.0, 1위 `081_hiphop_varsity_baggy_denim`. 기록적 한파 −12.0 → `fallback=false`, 1건(`076_winter_navy_long_parka`, 차이 정확히 3.0, `071`은 3.5로 제외). 때 이른 무더위 30.0 → `fallback=true`, `applied_tolerance_c=5.0`, 5건, 모든 차이 3.5~5.0. 기록적 폭염 38.0 → `insufficient_data`, 빈 목록
    - `test_five_minute_demo_walkthrough`: README 5분 데모 절차의 API 호출 순서를 `TestClient`로 그대로 따라가며 각 단계의 기대 결과 확인
    - _Requirements: 15.1, 15.9, 17.2, 17.5_

  - [ ]* 17.2 저장소 위생 테스트 작성 (`backend/tests/test_repo_hygiene.py`)
    - `test_env_example_and_gitignore`(12개 변수와 설명, `GEMINI_API_KEY=` 빈 값, `.env`/`!.env.example`), `test_dependencies_pinned`(`requirements.txt` 모든 줄 `==`, `package.json`에 `^`/`~` 없음), 커밋 대상 파일에 키 형태 문자열(`AIza`) 없음
    - _Requirements: 16.1, 16.3, 16.4, 17.7_

  - [x] 17.3 의존성 버전 최종 고정
    - 백엔드·프론트엔드 의존성을 설치하고 pytest와 vitest가 통과한 버전을 `requirements.txt`, `package.json`(+lock 파일)에 정확히 기록. 호환 문제로 버전을 내렸으면 사유를 README용으로 메모
    - _Requirements: 17.7_

  - [x] 17.4 README 작성 (`README.md`)
    - 별도 절: 백엔드 실행(`uvicorn`), 프론트엔드 실행(`npm run dev`), 12개 환경 변수 이름·기본값, API 키 설정 방법, Fixture/Live 모드 전환
    - 번호 매긴 5분 데모 절차(동작 + 화면 확인 결과, API 키 없이 Fixture 모드), 17.1 walkthrough 테스트와 단계 일치
    - 명시 사항: 촬영 시각·위치는 Sidecar 가상 메타데이터, Fixture 날씨, mock Vision, Archive API 재분석값과 최근 날짜 지연 시 fixture 대체, 자정 근처 시각 처리, 4.3에서 확인한 Gemini 모델 ID와 확인 날짜·출처, 데모 사진 출처/권한 또는 placeholder 사용 여부, Live 모드 첫 스캔(159장)은 Demo 패널 30초 제한을 넘길 수 있으니 발표 전에 미리 실행할 것
    - 테스트 명령(`cd backend && python -m pytest -q`)과 통과 기준(실패 0건, 종료 코드 0)
    - Phase 2 목록 4개(실제 갤러리 접근, Web Push, Recommendation_Strategy 교체, SQLite 이전 조건)와 항목별 설명
    - 완료 기능 목록은 테스트 또는 데모로 재현되는 것만, 나머지는 미완료/미검증으로 분리. API 키 값은 어디에도 적지 않는다
    - _Requirements: 16.5, 17.1, 17.2, 17.3, 17.6, 17.8, 17.9_

- [x] 18. Final checkpoint - 전체 테스트 통과 확인
  - 백엔드 pytest, 프론트엔드 vitest·typecheck·check:bundle이 외부 API 호출 없이 모두 통과하는지 확인한다. 질문이 생기면 사용자에게 묻는다.

- [ ] 19. Phase 1 변경: Agent Platform, 내장 메타데이터, 실제 날씨 기록
  - [x] 19.1 설정과 `.env.example` 갱신 (`backend/app/config.py`, `.env.example`, `backend/tests/test_config.py`)
    - 사용자 결정으로 변수 이름 `GEMINI_API_KEY`는 그대로 두고 12개 변수 목록도 바꾸지 않는다. `.env.example`과 `RawSettings`의 설명을 "Google Cloud Agent Platform express mode API 키, Google AI Studio 키로는 동작하지 않음"으로 바꾼다
    - 프로젝트 ID·위치 관련 변수를 추가하지 않는다. `GEMINI_MODEL` 기본값 `gemini-4-hydrogen`은 유지한다
    - `.env.example` 설명 문구를 확인하는 테스트를 추가하거나 고친다(키 값은 빈 문자열)
    - _Requirements: 16.1, 16.2, 16.3, 16.10_

  - [x] 19.2 도메인·DB·API 스키마에 메타데이터 출처 추가 (`backend/app/domain.py`, `db.py`, `schemas.py`, `repository.py`)
    - `MetadataSource = Literal["exif", "demo_sidecar"]`, `CaptureMetadata`(captured_at, latitude, longitude, synthetic, origin) 추가, `SidecarMetadata`의 captured_at·latitude·longitude를 선택 필드로 변경
    - `outfits.source` CHECK를 `('demo_sidecar','exif')`로, `metadata_synthetic INTEGER NOT NULL DEFAULT 1` 컬럼 추가, `OutfitCreate`/`OutfitRecord`/`OutfitOut`에 `source`, `metadata_synthetic` 추가
    - `PRAGMA user_version = 2`. 기동 시 기존 DB의 `user_version < 2`이면 파일(`-wal`, `-shm` 포함)을 `weatherwear.db.v1-backup-<UTC 시각>`으로 이름만 바꾸고 WARNING 로그 후 새로 만든다(삭제하지 않음)
    - 단위 테스트 `test_old_schema_db_is_backed_up_and_recreated`, `test_outfit_source_check_allows_exif`, 기존 저장소·스키마 테스트의 `demo_sidecar` 고정값 수정
    - _Requirements: 2.7, 2.15, 10.3, 10.4_

  - [x] 19.3 내장 메타데이터 리더와 사이드카 파서 수정 (`backend/app/ingestion/metadata.py`, `sidecar.py`)
    - 순수 함수 `read_embedded_metadata(data)`: UserComment JSON(Exif IFD 0x8769, 태그 0x9286, 8바이트 헤더 제거, date+time+utc_offset 또는 timezone) 1순위, 표준 EXIF(DateTimeOriginal+OffsetTimeOriginal, GPS 도·분·초, Ref 부호, GPSProcessingMethod `SYNTHETIC`) 2순위. 없으면 None, 무효하면 `MetadataError`(`METADATA_PARSE_ERROR`, `METADATA_MISSING_FIELD`, `METADATA_CAPTURED_AT_INVALID`, `METADATA_OUT_OF_RANGE`)
    - `parse_sidecar_text(text, require_location=...)`: 내장 메타데이터가 유효하면 시각·위치 없이 `thermal_vote`만 허용. `SIDECAR_NOT_FOUND` 코드 제거
    - 순수 함수 `resolve_capture_metadata(embedded, sidecar)`: design "Metadata_Reader" 절의 표대로 출처, source, 합성 여부, 시드 투표, 오류 코드 결정
    - location·outfit·prompt 등 텍스트 필드는 읽지 않고 버린다
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.9, 2.11, 2.12, 2.13, 2.14, 2.15, 2.16, 2.17_

  - [ ]* 19.4 내장 메타데이터 round-trip property test 작성 (`backend/tests/test_metadata_props.py`, `strategies.py`에 `capture_metadata()`, `exif_images()` 추가)
    - **Property 3: 내장 메타데이터 round-trip**
    - **Validates: Requirements 2.8, 2.2, 2.13, 2.14, 2.15**

  - [ ]* 19.5 메타데이터 결함 보고 property test 작성 (`backend/tests/test_metadata_props.py`)
    - **Property 4: 메타데이터 검증은 결함 종류를 정확히 보고하고 추가 필드는 무시한다**
    - **Validates: Requirements 2.3, 2.4, 2.5, 2.9, 2.11**

  - [ ]* 19.6 메타데이터 출처 우선순위 property test 작성 (`backend/tests/test_metadata_props.py`)
    - **Property 34: 메타데이터 출처 우선순위**
    - **Validates: Requirements 2.3, 2.7, 2.12, 2.15, 2.16**

  - [x] 19.7 내장 메타데이터 단위 테스트 작성 (`backend/tests/test_metadata.py`)
    - 영역 (g): `test_user_comment_json_parsed`(ex_img 형식의 고정 바이트), `test_standard_exif_jpeg_parsed`, `test_user_comment_preferred_over_standard_exif`, `test_sidecar_fallback_when_no_exif`, `test_sidecar_vote_only_with_exif`, `test_broken_sidecar_fails_even_with_exif`, `test_no_metadata_fails_metadata_not_found`, `test_timezone_used_when_utc_offset_missing`, `test_text_fields_not_stored`
    - 테스트 이미지는 Pillow로 8×8 PNG/JPEG에 EXIF를 써서 만들고, `ex_img/` 파일에 의존하지 않는다
    - _Requirements: 2.1, 2.3, 2.4, 2.5, 2.12, 2.13, 2.14, 2.16, 2.17, 17.4_

  - [x] 19.8 수집 서비스가 내장 메타데이터를 쓰도록 연결 (`backend/app/ingestion/service.py`)
    - 처리 5단계를 `read_embedded_metadata` → (사이드카 파일이 있으면) `parse_sidecar_text` → `resolve_capture_metadata`로 바꾸고, `OutfitCreate`에 `source='exif'`(사이드카 대체 시 `demo_sidecar`)와 `metadata_synthetic` 전달
    - 사이드카가 없어도 내장 메타데이터가 유효하면 성공. 실패 코드는 `ScanError(stage="validation")`로 기존 형식 그대로 보고
    - 기존 `test_ingestion.py`, `test_ingestion_service.py`, `test_sidecar.py`의 "사이드카 필수" 가정을 고치고 `test_source_is_exif_for_embedded_metadata`, `test_source_is_demo_sidecar_for_sidecar_fallback` 추가
    - _Requirements: 1.6, 2.1, 2.6, 2.7, 2.10, 2.12, 2.16_

  - [x] 19.9 GeminiVisionProvider를 Agent Platform express mode로 전환 (`backend/app/providers/vision/gemini.py`)
    - `_build_client`: `genai.Client(enterprise=True, api_key=..., http_options=HttpOptions(timeout=30000, retry_options=HttpRetryOptions(attempts=1)))`, `project`/`location` 없음, `vertexai`와 함께 넘기지 않음
    - 모델 ID가 `gemini-3`/`gemini-4`로 시작하면 `ThinkingConfig(thinking_level="LOW")` 추가
    - 4xx(모델 미지원 포함)는 `VisionError("api_error")`, 로그에는 모델 ID와 HTTP 상태 코드만
    - 프롬프트에 "인물·착장이 없으면 세 타입 unknown, confidence 0" 문장 추가. `strip_metadata()`로 UserComment·GPS를 포함한 EXIF 제거는 그대로 유지
    - _Requirements: 3.2, 3.5, 3.6, 3.7, 3.10, 16.5, 16.10_

  - [x] 19.10 Gemini express mode 단위 테스트 작성 (`backend/tests/test_vision_gemini.py`)
    - `test_gemini_client_uses_agent_platform_express_mode`(`genai.Client` monkeypatch로 `enterprise=True`, `api_key`, `project`/`location` 없음 확인, 네트워크 없음), `test_gemini_thinking_level_low_for_gemini_3_and_4`, `test_gemini_model_unsupported_logs_status_only`(로그에 키 값 없음), `test_strip_metadata_removes_user_comment_and_gps`
    - _Requirements: 3.2, 3.7, 3.10, 16.5, 16.10_

  - [ ]* 19.11 Gemini 요청 내용 property test 갱신 (`backend/tests/test_vision_props.py`)
    - **Property 8: Gemini 요청에는 메타데이터가 제거된 이미지와 프롬프트만 들어간다**
    - **Validates: Requirements 3.7, 2.17**

  - [x] 19.12 `scripts/check_gemini_model.py` 갱신
    - 19.9의 `_build_client`로 express mode 클라이언트를 만들고, `models.get` 대신 짧은 텍스트 `generate_content` 1건으로 실제 호출 가능 여부 확인
    - 성공 시 모델 ID와 "Agent Platform express mode 호출 성공", 실패 시 오류 종류·HTTP 상태 코드와 대체 모델 `gemini-3-flash-preview` 안내(종료 코드 1). 키가 없으면 호출하지 않음. 키 값과 응답 본문은 출력하지 않음. 문서 링크를 Agent Platform express mode 문서로 교체
    - 기존 `test_demo_scripts.py`의 이 스크립트 관련 검사(키 없음 경로)를 맞춘다
    - _Requirements: 3.2, 16.5, 17.10_

  - [x] 19.13 착장이 아닌 사진을 추천 후보에서 제외 (`backend/app/recommendation/rule_based_v1.py`)
    - 순수 함수 `is_non_outfit(tags)`(세 타입 `unknown` + confidence 0.0)를 유효 후보 조건에 추가. 후보 0건·fallback 판정에도 적용. 알고리즘 버전 문자열은 `rule_based_v1` 유지, 모듈 독스트링에 동작 변경 기록
    - `UNKNOWN_TAGS` 후보가 결과에 나오길 기대하던 기존 테스트(`test_rule_based_v1.py`, `test_recommendation.py`, `test_recommendation_service.py`, `strategies.py`의 기본 태그)를 실제 태그로 바꾼다
    - 단위 테스트 `test_non_outfit_photo_excluded_from_candidates`, `test_non_outfit_photo_does_not_prevent_fallback`, `test_non_outfit_photo_still_listed_in_outfits`
    - _Requirements: 6.1, 6.3, 6.5, 6.10, 15.13_

  - [ ]* 19.14 허용 오차 후보 선택 property test 갱신 (`backend/tests/test_recommendation_props.py`)
    - **Property 17: 허용 오차 후보 선택이 참조 모델과 일치한다** (참조 모델에 Non_Outfit_Photo 제외 추가)
    - **Validates: Requirements 6.1, 6.3, 6.5, 6.7, 6.9, 6.10**

  - [x] 19.15 FixtureWeatherProvider가 기록 데이터 형식을 읽도록 수정 (`backend/app/providers/weather/fixture.py`)
    - `weather.json`의 `recorded` 블록을 허용하고, 항목의 `observed_at`·null 필드를 그대로 반환. 항목이 없을 때의 `seasonal_temp` 대체와 `provider='fixture'`, `is_fixture=True`는 유지
    - `test_weather_fixture.py`에 기록 형식 항목 조회와 null 보존 테스트 추가
    - _Requirements: 4.6, 4.10, 4.15_

  - [x] 19.16 사진 manifest를 174장으로 갱신 (`data/demo/photos.manifest.json`)
    - `126_…`~`140_…` 15장을 포함한 174장 전체를 `rights_confirmed: true`, `license`는 "프로젝트 소유자가 ex_img/ 174장의 데모 사용 권한을 확인함"
    - 이후 새로 생기는 `ex_img/` 파일은 `rights_confirmed: false`로 두고 사용자에게 확인한다
    - _Requirements: 15.2, 15.3_

  - [ ] 19.17 사진 복사 스크립트 수정 후 실행 (`scripts/prepare_demo_photos.py`)
    - manifest의 174장을 `shutil.copyfile`로 바이트 그대로 복사(이전 159장 복사본 덮어쓰기), 사이드카 템플릿 생성 제거, 복사 후 각 사진을 `read_embedded_metadata`로 확인하고 실패하면 종료 코드 1
    - 스크립트를 실행해 `data/demo/photos/`를 174장(약 325MB)으로 채운다
    - _Requirements: 2.1, 15.2, 15.3_

  - [ ] 19.18 `clothing_tags.json` 가져오기 스크립트 작성 후 실행 (`scripts/import_clothing_tags.py`)
    - design "의류 태그 fixture" 절대로 파일 이름으로 연결, `ClothingTags` 검증, 키는 `data/demo/photos` 파일 바이트 전체의 SHA-256, 원래 `sha256`은 `vision.sources.json`의 `source_sha256`으로 기록. 누락·검증 실패 시 파일을 쓰지 않고 종료 코드 1
    - 실행해 `data/demo/fixtures/vision.json`을 174개 키로 교체. `build_fixtures.py`의 태그 작성 경로는 이 스크립트를 쓰도록 바꾸거나 제거
    - _Requirements: 3.3, 15.8, 15.12, 15.13_

  - [ ] 19.19 날씨 기록 스크립트 작성 후 실행 (`scripts/record_fixture_weather.py`)
    - 각 사진의 내장 메타데이터로 `(날짜, 좌표)` 묶음마다 Archive API 요청 1건(OpenMeteoWeatherProvider와 같은 파라미터), `map_archive_response`로 변환, 실패 시 값을 지어내지 않고 종료 코드 1, 성공 시 `recorded` 블록과 함께 원자적 교체
    - 순수 부분(묶기, 항목 생성)에 대한 단위 테스트를 respx로 작성(네트워크 없음). pytest는 스크립트 본체를 실행하지 않는다
    - 개발자 환경에서 한 번 실행해 `data/demo/fixtures/weather.json`을 174건의 기록값으로 교체한다(네트워크 필요)
    - _Requirements: 4.11, 4.12, 4.13, 4.14, 4.15_

  - [ ] 19.20 시나리오 선택·검사와 시드 투표 구현 후 실행 (`backend/app/demo_scenarios.py`, `scripts/select_scenarios.py`)
    - 순수 함수 `choose_scenarios(T)`, `check_scenarios(scenarios, T)`, `assign_seed_votes(...)`를 design의 S1~S4, V1~V4 규칙대로 구현. Non_Outfit_Photo는 T와 투표에서 제외
    - 스크립트: `scenarios.json` 기온(소수 첫째 자리), `weather.json`의 `today_default`(S1과 같은 기온), 시드 투표 사이드카(기존 `*.metadata.json` 삭제 후 투표 있는 사진에만 작성), `data/demo/scenario_expectations.json` 작성 후 `check_scenarios`. 위반 시 종료 코드 1. `--check`는 쓰기 없이 검사만
    - 실행해 데이터 파일을 갱신한다
    - _Requirements: 2.6, 5.2, 15.9, 15.10, 15.11_

  - [ ]* 19.21 시나리오 선택 property test 작성 (`backend/tests/test_demo_scenarios_props.py`, `strategies.py`에 `recorded_temps()` 추가)
    - **Property 35: 시나리오 선택은 규칙 S1~S4를 만족한다**
    - **Validates: Requirements 15.9, 15.10, 15.11**

  - [ ] 19.22 플레이스홀더 스크립트에 내장 메타데이터 추가 (`scripts/make_placeholders.py`)
    - 생성 PNG마다 UserComment JSON(`synthetic: true`, 서로 다른 시각, 서울 좌표)을 EXIF로 기록하고, 사이드카 대신 `weather.json` 항목(계절 공식 값, `recorded.source="placeholder"`)과 vision fixture를 만든다. 확인된 사진이 0장일 때만 쓰는 경로다
    - _Requirements: 15.7_

  - [ ] 19.23 데모 데이터·통합 테스트 기대값 갱신 (`backend/tests/test_demo_data.py`, `test_integration.py`, `test_demo_scripts.py`)
    - `test_demo_data_layout`: 174장, 모든 사진의 내장 메타데이터 읽기, vision 키 = 현재 해시이고 태그가 `clothing_tags.json`과 같음, `weather.json`의 `recorded` 블록과 174건, 사이드카는 `thermal_vote`만 담고 Non_Outfit_Photo에는 없음
    - `test_demo_scenarios_satisfy_rules`(실제 데이터에 `check_scenarios`), `test_demo_scenarios_cover_cases`를 S1~S4 조건 + `scenario_expectations.json` 비교로 교체(고정값 `081`, `076`, 9.0/−12.0/30.0/38.0 제거), `test_five_minute_demo_walkthrough`와 `test_fixture_mode_end_to_end_deterministic`의 기대값도 맞춘다
    - 사진이 없는 환경에서는 기존처럼 skip
    - _Requirements: 15.1, 15.2, 15.9, 15.10, 15.11, 15.12, 17.2, 17.5_

  - [x] 19.24 프론트엔드 라벨과 타입 갱신 (`frontend/src/api/schema.d.ts`, `src/lib/format.ts`, `src/lib/strings.ts`, `src/pages/OutfitDetailPage.tsx`, `scripts/check-bundle.mjs`)
    - `scripts/export_openapi.py` + `openapi-typescript`로 `schema.d.ts` 재생성(`source: "exif" | "demo_sidecar"`, `metadata_synthetic`)
    - `provenance().meta = metadata_synthetic`, `metadataSourceLabel()` 추가, 상세 화면에 "촬영 정보: 사진 내장 메타데이터/사이드카 파일" 표시, 문구는 `strings.ts`에 추가
    - `check-bundle.mjs`: `GEMINI` 문자열 검사에 더해, 환경 변수나 루트 `.env`의 `GEMINI_API_KEY` 값(8자 이상)이 `dist/`에 있으면 실패(값은 출력하지 않음)
    - `format.unit.test.ts` 등 기존 테스트의 `source: "demo_sidecar"` 기반 라벨 기대값을 고친다
    - _Requirements: 10.7, 12.5, 15.4, 16.6_

  - [ ]* 19.25 fixture 라벨 property test 갱신 (`frontend/src/lib/format.test.ts`)
    - **Property 33: fixture/가상 데이터 라벨은 fixture 출처에만 붙는다**
    - **Validates: Requirements 15.4, 4.9, 12.5**

  - [ ] 19.26 README 갱신 (`README.md`)
    - API 키 절: `GEMINI_API_KEY`에 Agent Platform express mode 키(express mode 콘솔 APIs & Services > Credentials), AI Studio 키는 동작하지 않음, 프로젝트·위치 불필요
    - `GEMINI_MODEL` 기본값 `gemini-4-hydrogen`이 2026-11-14 기준 express mode 지원 모델 표에 없다는 점, `check_gemini_model.py`로 확인하고 실패하면 `gemini-3-flash-preview`를 쓰라는 안내와 출처 URL
    - 명시 사항 교체: 촬영 시각·위치는 사진 내장 합성 EXIF(UserComment JSON, JPG는 표준 EXIF), 사이드카는 대체 수단과 시드 투표, Gemini 전송 전 EXIF·위치 제거, fixture 날씨는 Open-Meteo 기록 재분석값(기록 날짜는 `weather.json`), Non_Outfit_Photo는 추천에서 제외, rule_based_v1 후보 선택 변경, 사진 174장
    - 데모 데이터 준비 명령 순서(prepare → import_clothing_tags → record_fixture_weather(네트워크) → select_scenarios), 5분 데모 절차의 시나리오 기온을 `scenarios.json` 값으로 갱신, Phase 2 목록에서 EXIF 항목 제거(실제 갤러리 접근은 유지), 테스트 영역 (g) 추가
    - _Requirements: 16.1, 17.1, 17.2, 17.3, 17.4, 17.8, 17.9, 17.10_

- [ ] 20. Final checkpoint - Phase 1 변경 확인
  - 백엔드 `cd backend && python -m pytest -q`, 프론트엔드 `npx vitest --run`, `npm run typecheck`, `npm run check:bundle`, `scripts/select_scenarios.py --check`가 외부 API 호출 없이 모두 통과하는지 확인한다. 질문이 생기면 사용자에게 묻는다.

## Notes

- `*`가 붙은 sub-task는 선택 항목이며 빠른 MVP를 위해 건너뛸 수 있다. 대부분의 property test(Hypothesis, fast-check)와 UI 테스트가 여기에 속한다.
- Requirement 17.4가 요구하는 pytest 영역 (a)~(f)의 단위 테스트(5.9, 6.7, 8.6, 9.11, 10.10)와 Fixture 모드 통합 테스트(17.1)는 데모 검증에 꼭 필요하므로 선택 항목으로 표시하지 않았다.
- 모든 테스트는 외부 네트워크 없이 돈다. `conftest.py`의 소켓 가드, `respx`(Open-Meteo), 가짜 Gemini 클라이언트를 쓰고, 테스트 중에는 `GEMINI_API_KEY`를 환경에서 지운다.
- 2.1의 사진 사용 권한은 사용자가 `ex_img/` 159장(PNG 119, JPG 40) 전체에 대해 확인했다. placeholder 생성(2.2)은 확인된 사진이 0장일 때만 쓰는 대비책이다.
- 4.3의 Gemini 모델 ID 확인은 첫 live Vision 호출 전에 끝내야 한다. 확인 스크립트는 API 키가 있을 때 사용자가 수동으로 실행하며 pytest에 포함하지 않는다.
- 다음 항목은 코딩 작업이 아니어서 태스크에 넣지 않았다: 360/390/430px 뷰포트 가로 스크롤 수동 확인(Req 11.6), `strings.ts` 한국어 문구 검수(Req 11.5), 발표 전 5분 데모 리허설.
- 각 태스크는 추적을 위해 requirements.md의 세부 기준 번호를 참조한다. Property 번호는 design.md의 Correctness Properties 절을 따른다.
- 19번은 Phase 1 구현 후 사용자 결정(Agent Platform express mode, 사진 내장 메타데이터, Open-Meteo 기록 날씨, `ex_img/` 174장과 `clothing_tags.json`)을 반영한다. 사용자 결정에 따라 키 변수 이름 `GEMINI_API_KEY`는 바꾸지 않는다.
- 19.19는 Open-Meteo 네트워크가 필요한 유일한 작업이다. 테스트는 계속 외부 연결 없이 돈다.
- 19.2의 DB 버전 처리로 기존 `data/weatherwear.db`는 백업 이름으로 옮겨지고 새로 만들어진다. 이전 DB의 사용자 피드백은 백업 파일에 남는다.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "12.1"] },
    { "id": 1, "tasks": ["1.2", "1.4", "2.2"] },
    { "id": 2, "tasks": ["1.7", "1.8", "1.9", "2.3", "3.1", "3.4", "4.1", "4.3", "5.1", "5.4"] },
    { "id": 3, "tasks": ["1.3", "1.5", "2.4", "3.2", "4.4", "5.2", "5.6", "6.1", "8.3", "9.1", "12.2"] },
    { "id": 4, "tasks": ["1.6", "3.3", "4.6", "5.3", "6.2", "6.4", "8.1", "8.4", "9.2", "9.7", "12.3", "13.1"] },
    { "id": 5, "tasks": ["4.2", "5.5", "5.9", "6.3", "6.5", "6.7", "8.2", "8.5", "8.6", "9.3", "9.10", "12.4", "13.2"] },
    { "id": 6, "tasks": ["4.5", "5.7", "6.6", "8.7", "9.4", "9.11", "10.1", "12.5", "13.3"] },
    { "id": 7, "tasks": ["5.8", "9.5", "10.2", "10.3", "13.4", "13.5", "14.1", "14.2", "15.1"] },
    { "id": 8, "tasks": ["9.6", "10.4", "13.6", "14.3", "14.4", "15.2", "15.3"] },
    { "id": 9, "tasks": ["9.8", "10.5", "10.7", "10.10"] },
    { "id": 10, "tasks": ["9.9", "10.6", "10.8"] },
    { "id": 11, "tasks": ["10.9", "17.1", "17.2"] },
    { "id": 12, "tasks": ["17.3"] },
    { "id": 13, "tasks": ["17.4"] },
    { "id": 14, "tasks": ["19.1", "19.2", "19.9", "19.13", "19.15", "19.16"] },
    { "id": 15, "tasks": ["19.3", "19.10", "19.12", "19.14", "19.24"] },
    { "id": 16, "tasks": ["19.4", "19.7", "19.8", "19.11", "19.22", "19.25"] },
    { "id": 17, "tasks": ["19.5", "19.17"] },
    { "id": 18, "tasks": ["19.6", "19.18", "19.19"] },
    { "id": 19, "tasks": ["19.20"] },
    { "id": 20, "tasks": ["19.21", "19.23"] },
    { "id": 21, "tasks": ["19.26"] }
  ]
}
```
