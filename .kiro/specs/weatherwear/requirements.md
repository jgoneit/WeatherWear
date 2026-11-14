# Requirements Document

## Introduction

WeatherWear는 사용자가 저장해 둔 착장 사진을 분석하고 촬영 시점의 날씨와 연결한 뒤, 오늘과 기온이 비슷했던 과거 착장과 그때의 체감 피드백(추웠어 / 적당했어 / 더웠어)을 근거로 개인화된 착장을 추천하는 모바일 브라우저용 반응형 웹 애플리케이션이다.

이 문서는 Kirothon 6시간 데모를 위한 Phase 1(MVP) 범위만 다룬다. 검증하려는 가설은 "머신러닝 없이도, 과거 실제 경험과 유사 기온 검색을 결합하면 일반적인 기온 기반 추천보다 유용하다"는 것이다.

MVP 구성: 지정 폴더 스캔 + 사진에 내장된 EXIF 메타데이터(촬영 시각·위치) 읽기(사이드카 JSON은 선택적 대체 수단), Google Cloud Agent Platform API(express mode)의 Gemini Vision(또는 Fixture) 기반 의류 태그 추출, Open-Meteo(또는 Open-Meteo에서 미리 기록한 Fixture) 기반 날씨 연결, SQLite 저장, 규칙 기반 추천(rule_based_v1), 웹 내부 알림 카드와 원클릭 체감 피드백.

### Phase 1 구현 후 변경 사항

Phase 1 구현을 마친 뒤 사용자가 다음을 결정했고, 이 문서에 반영했다.

- Gemini는 Google AI Studio가 아니라 Google Cloud Agent Platform API(구 Vertex AI)를 express mode(API 키 인증)로 호출한다. `.env`의 GEMINI_API_KEY 값은 Agent Platform express mode API 키이며, 사용자가 이 키를 `.env`에 넣는다. 변수 이름은 바꾸지 않는다.
- 촬영 시각과 위치는 각 사진에 내장된 EXIF 메타데이터에서 읽는다. 원래 Phase 2였던 EXIF 읽기를 Phase 1로 옮겼다. 사이드카 파일은 선택적 대체 수단이며 사전 체감 피드백만 담을 수 있다.
- Fixture 날씨는 사진마다 내장된 시각·위치로 Open-Meteo Archive API를 한 번 호출해 기록한 실제 날씨 값이다.
- 데모 사진은 사용자가 사용 권한을 확인한 `ex_img/` 전체 174장이고, 의류 태그 fixture는 `ex_img/clothing_tags.json`을 사용한다.

### Phase 1 범위 제외 항목 (Phase 2 후보)

- 실제 휴대폰 갤러리 접근, 동의 기반 업로드, 실제(합성이 아닌) 촬영 위치의 개인정보 처리
- 네이티브 앱, Web Push / FCM 실제 푸시 알림, 브라우저 Notification API
- 로그인 및 다중 사용자 계정
- 사용자 수동 옷장 등록, 의류 아이템 재식별
- 사용자별 모델 학습 및 학습 기반 추천 전략 구현 (교체 가능한 구조만 준비)
- PostgreSQL 이전, 시계열 DB, 벡터 DB, 그래프 DB, Redis, Kafka
- 추천 자체에 대한 좋아요/싫어요 피드백 저장

## Glossary

- **WeatherWear_System**: 본 문서가 정의하는 백엔드와 프론트엔드를 포함한 전체 애플리케이션
- **API_Server**: FastAPI 기반 백엔드 REST API 서버. OpenAPI/Pydantic 스키마가 API 계약의 기준(source of truth)이다
- **Mobile_Web_Client**: React + Vite + TypeScript + Tailwind 기반 모바일 우선 반응형 웹 프론트엔드
- **Ingestion_Service**: 지정 사진 폴더를 스캔하고 이미지 검증, SHA-256 해시 계산, 중복 제거, 메타데이터 읽기(Embedded_Metadata 우선, Sidecar_Metadata 대체)를 수행하는 ImageIngestionService
- **Metadata_Reader**: 이미지 바이트에서 Embedded_Metadata를 읽어 촬영 시각, 위도, 경도, 합성 여부를 반환하는 순수 함수 구성 요소
- **Vision_Provider**: 사진 1장을 분석하여 의류 태그 JSON을 반환하는 인터페이스. 구현체는 Gemini_Vision_Provider와 Fixture_Vision_Provider이다
- **Agent_Platform_Express_Mode**: Google Cloud Gemini Enterprise Agent Platform API(구 Vertex AI)를 프로젝트 ID와 위치 없이 API 키로 인증해 전역 엔드포인트(`https://aiplatform.googleapis.com`)로 호출하는 방식. google-genai SDK에서는 `genai.Client(enterprise=True, api_key=...)`로 사용하며, `vertexai=True`는 같은 동작의 이전 이름이다
- **Gemini_Vision_Provider**: Gemini 공식 Python SDK(google-genai)로 Agent_Platform_Express_Mode에 이미지 1장당 1회 요청하는 live 모드 Vision_Provider 구현체. Google AI Studio(Gemini Developer API) 엔드포인트는 사용하지 않는다
- **Fixture_Vision_Provider**: API 키 없이 사전 정의된 결과(`ex_img/clothing_tags.json`에서 가져온 `data/demo/fixtures/vision.json`)를 반환하는 mock Vision_Provider 구현체
- **Weather_Provider**: 특정 시각과 위치의 날씨를 반환하는 인터페이스. 구현체는 OpenMeteo_Weather_Provider와 Fixture_Weather_Provider이다
- **OpenMeteo_Weather_Provider**: Open-Meteo API로 과거 및 오늘 날씨를 조회하는 Weather_Provider 구현체
- **Open_Meteo_Archive_API**: OpenMeteo_Weather_Provider가 호출하는 Open-Meteo 과거 날씨 API. 엔드포인트는 `https://archive-api.open-meteo.com/v1/archive`이고, 쿼리 파라미터는 latitude, longitude, start_date(YYYY-MM-DD), end_date(YYYY-MM-DD), hourly(조회할 날씨 변수 목록), timezone이다. 시간 단위 응답 변수 temperature_2m(°C), precipitation(mm), relative_humidity_2m(%)를 사용한다
- **Fixture_Weather_Provider**: `data/demo/fixtures/weather.json`의 기록 데이터로 날씨를 반환하는 Weather_Provider 구현체. 결과의 provider는 항상 `fixture`, is_fixture는 항상 true이다
- **Recorded_Weather_Fixture**: Weather_Recording_Script가 사진마다 Embedded_Metadata의 촬영 시각·위치로 Open_Meteo_Archive_API를 1회 조회해 `data/demo/fixtures/weather.json`에 기록한 실제 날씨 값과 기록 정보(출처, 엔드포인트, 기록 날짜)
- **Weather_Recording_Script**: 데모 데이터 준비 때 개발자가 한 번 실행하는 `scripts/record_fixture_weather.py`. 자동화 테스트에서는 실행하지 않는다
- **Outfit_Repository**: outfits, weather_snapshots, feedback 테이블에 대한 원자적이고 멱등적인 저장 및 조회를 담당하는 SQLite 저장소 계층
- **Recommendation_Strategy**: `recommend(today: WeatherContext, candidates: list[OutfitWithWeatherAndFeedback]) -> list[RecommendationResult]` 인터페이스. MVP 구현체는 Rule_Based_V1이다
- **Rule_Based_V1**: 기온 허용 오차 검색과 피드백 가중 랭킹을 수행하는 규칙 기반 추천 전략. 알고리즘 버전 문자열은 `rule_based_v1`이다
- **Feedback_Service**: 착장별 체감 피드백(thermal_vote)을 저장하고 유효 피드백을 계산하는 서비스
- **Notification_Card**: Mobile_Web_Client 내부에 표시되는 알림 형태의 카드 UI. 실제 푸시 알림이 아니다
- **Demo_Admin_Panel**: 개발 모드에서만 노출되는 데모 관리 화면(폴더 스캔, 오늘 날씨 시나리오 선택, 추천 재계산, 처리 상태 확인)
- **Config_Loader**: 환경 변수와 `.env` 파일에서 설정을 읽는 백엔드 구성 요소
- **Outfit**: 사진 1장과 그 분석 결과를 나타내는 레코드 (outfits 테이블)
- **Weather_Snapshot**: Outfit 1건의 촬영 시점·위치에 연결된 날씨 레코드 (weather_snapshots 테이블, Outfit당 최대 1건)
- **Thermal_Vote**: 착장에 대한 체감 피드백 값. `cold`(추웠어), `comfortable`(적당했어), `hot`(더웠어) 중 하나
- **Effective_Vote**: Outfit에 대해 가장 최근에 기록된 Thermal_Vote
- **Sidecar_Metadata**: 사진과 같은 이름의 선택적 `*.metadata.json` 파일. 사전 체감 피드백(thermal_vote)을 담을 수 있고, Embedded_Metadata가 없거나 유효하지 않은 사진에 한해 촬영 시각, 위도, 경도의 대체 출처로 쓰인다
- **Embedded_Metadata**: 이미지 파일 안에 저장된 EXIF 메타데이터. 우선 출처는 Exif IFD(0x8769)의 UserComment 태그(0x9286)로, 8바이트 문자 코드 헤더(`ASCII\0\0\0`) 뒤에 date(YYYY-MM-DD), time(HH:MM:SS), timezone(IANA 이름), utc_offset(±HH:MM), latitude, longitude, synthetic 등을 담은 JSON이 있다. 대체 출처는 표준 EXIF의 DateTimeOriginal(0x9003)과 OffsetTimeOriginal(0x9011), GPS IFD(0x8825)의 위도·경도(도·분·초)와 GPSProcessingMethod이다
- **Metadata_Source**: Outfit의 촬영 시각·위치를 어디서 읽었는지 나타내는 source 값. Embedded_Metadata이면 `exif`, Sidecar_Metadata이면 `demo_sidecar`이다
- **Synthetic_Metadata**: 실제 촬영 기록이 아닌 가상(합성) 메타데이터. Embedded_Metadata의 UserComment JSON에서 synthetic이 true이거나 GPSProcessingMethod 값이 `SYNTHETIC`인 경우, 그리고 Sidecar_Metadata에서 읽은 경우가 해당한다
- **Non_Outfit_Photo**: 착장이 아닌 사진(음식, 풍경 등). 의류 태그의 upper_type, outer_type, lower_type이 모두 `unknown`이고 confidence가 0.0인 Outfit을 말한다
- **Weather_Scenario**: `data/demo/scenarios.json`에 정의된 고정 "오늘 날씨" 시나리오
- **Scenario_Rules**: 4개 Weather_Scenario가 Recorded_Weather_Fixture에 대해 만족해야 하는 규칙 S1(일반 추천), S2(경계값 3.0°C), S3(범위 확장), S4(데이터 부족). Scenario_Checker가 검사한다
- **Scenario_Checker**: Recorded_Weather_Fixture와 Fixture_Vision_Provider 데이터로 Scenario_Rules를 검사하는 `scripts/select_scenarios.py`의 검사 기능과 같은 규칙을 쓰는 자동화 테스트
- **WeatherContext**: 오늘 날씨를 표현하는 값 객체. actual_temperature_c는 필수이고 apparent_temperature_c, relative_humidity_pct, wind_speed_kmh, precipitation_mm은 선택이다
- **Temperature_Tolerance**: 유사 기온 판정 허용 오차. 기본값 3.0°C이며 환경 변수 TEMPERATURE_TOLERANCE_C로 설정한다
- **Fallback_Tolerance**: 기본 허용 오차 내 후보가 없을 때 1회 확장하는 허용 오차 5.0°C
- **Fixture_Mode**: VISION_PROVIDER=fixture 및 WEATHER_PROVIDER=fixture로 외부 API와 API 키 없이 동작하는 모드
- **Live_Mode**: VISION_PROVIDER=gemini(Agent_Platform_Express_Mode 호출) 또는 WEATHER_PROVIDER=open_meteo로 실제 외부 API를 호출하는 모드
- **Photo_Dir**: 스캔 대상 사진 폴더. 환경 변수 PHOTO_DIR로 설정하며 기본값은 `./data/demo/photos`이다

## Requirements

### Requirement 1: 사진 폴더 스캔 및 수집

**User Story:** 데모 사용자로서, 지정된 폴더의 착장 사진을 한 번에 수집하고 싶다. 그래서 수동 입력 없이 착장 기록을 만들 수 있다.

#### Acceptance Criteria

1. WHEN POST /api/demo/scan 요청이 수신되면, THE Ingestion_Service SHALL Photo_Dir 최상위 경로에 있는 파일 중 확장자가 `.jpg`, `.jpeg`, `.png`, `.webp`(대소문자 무시)인 파일만 이미지 파일로 스캔하고, 하위 폴더, Sidecar_Metadata 파일, 그 밖의 확장자를 가진 파일은 scanned 건수에 포함하지 않는다
2. WHEN 스캔이 완료되면, THE API_Server SHALL scanned, succeeded, duplicate, skipped_non_outfit, uncertain, failed 건수와 실패 파일별 오류 상세 및 저장하지 않은 사진별 판정 결과를 반환한다. scanned는 succeeded, duplicate, skipped_non_outfit, uncertain, failed의 합과 같고, Photo_Dir에 이미지 파일이 0건이면 모든 건수를 0으로 반환한다
3. WHEN 이미지 파일이 발견되면, THE Ingestion_Service SHALL 해당 파일 내용 전체의 SHA-256 해시를 계산한다
4. IF 이미 저장된 Outfit 또는 같은 스캔에서 앞서 처리된 파일과 동일한 SHA-256 해시를 가진 파일이 발견되면, THEN THE Ingestion_Service SHALL 해당 파일에 대해 Vision_Provider와 Weather_Provider를 호출하지 않고, 새 Outfit을 생성하지 않으며, 해당 파일을 duplicate로 집계한다
5. WHEN 파일 변경 없이 동일한 Photo_Dir에 대해 스캔이 반복 실행되면, THE Outfit_Repository SHALL 첫 스캔 이후와 동일한 Outfit 건수와 Weather_Snapshot 건수를 유지한다 (멱등성)
6. IF 이미지 파일 1건의 검증, 분석 또는 저장이 실패하면, THEN THE Ingestion_Service SHALL 해당 파일에 대한 Outfit과 Weather_Snapshot 레코드를 남기지 않고, 해당 파일을 failed로 집계하며, 나머지 파일의 처리를 계속한다
7. WHEN 부분 실패 이후 스캔이 재실행되면, THE Ingestion_Service SHALL 이전에 실패한 파일을 다시 처리하여 처리 결과에 따라 succeeded 또는 failed로 집계하고, 이전에 성공한 파일은 duplicate로 집계한다
8. IF 지원 확장자를 가진 파일의 내용이 해당 이미지 형식으로 디코딩되지 않으면, THEN THE Ingestion_Service SHALL 해당 파일에 대해 Vision_Provider를 호출하지 않고 failed로 집계하며 오류 상세에 형식 오류를 기록한다
9. THE API_Server SHALL 스캔 대상 경로를 Photo_Dir 설정값으로 한정하고, 요청 본문에 경로 값이 포함되어 있어도 이를 무시하고 Photo_Dir만 스캔한다
10. WHEN 하나의 Outfit과 Outfit에 연결된 Weather_Snapshot을 저장하면, THE Outfit_Repository SHALL 두 레코드를 단일 트랜잭션으로 저장하고, 둘 중 하나라도 저장에 실패하면 두 레코드를 모두 롤백한다
11. IF Photo_Dir이 존재하지 않거나, 디렉터리가 아니거나, 읽기 권한이 없으면, THEN THE API_Server SHALL 파일을 처리하지 않고 Photo_Dir에 접근할 수 없음을 나타내는 오류 응답을 반환하며, Outfit_Repository에 저장된 데이터는 변경하지 않는다
12. IF 이미지 파일의 크기가 20 MB를 초과하면, THEN THE Ingestion_Service SHALL 해당 파일에 대해 Vision_Provider를 호출하지 않고 failed로 집계하며 오류 상세에 파일 크기 초과를 기록한다
13. WHILE 스캔이 진행 중일 때, WHEN 추가 POST /api/demo/scan 요청이 수신되면, THE API_Server SHALL 새 스캔을 시작하지 않고 스캔이 이미 진행 중임을 나타내는 오류 응답을 반환하며, 진행 중인 스캔은 중단 없이 계속한다

### Requirement 2: 사진 내장 메타데이터 읽기

**User Story:** 데모 사용자로서, 사진 파일에 들어 있는 촬영 시각과 위치를 자동으로 읽게 하고 싶다. 그래서 별도 메타데이터 파일 없이도 과거 날씨를 연결할 수 있다.

#### Acceptance Criteria

1. WHEN Ingestion_Service가 이미지 형식 검증과 Vision_Provider의 착장 판정을 통과한 `outfit` 사진 1건을 처리하면, THE Metadata_Reader SHALL Weather_Provider를 호출하기 전에 해당 이미지 바이트의 Embedded_Metadata에서 촬영 시각(captured_at), latitude, longitude, 합성 여부를 읽는다. `non_outfit` 또는 `uncertain` 사진에는 메타데이터를 요구하지 않는다
2. WHEN captured_at을 저장하면, THE Outfit_Repository SHALL captured_at을 메타데이터에 기재된 것과 같은 지역 시각과 같은 타임존 오프셋을 가진 ISO 8601 형식(예: UserComment JSON의 date `2025-01-08`, time `08:15:00`, utc_offset `+09:00` → `2025-01-08T08:15:00+09:00`)으로 저장한다
3. IF 이미지에 유효한 Embedded_Metadata가 없고 Sidecar_Metadata도 없거나, 사용할 출처에서 촬영 시각·latitude·longitude 중 하나 이상이 누락되었거나 null이거나, UserComment JSON 또는 Sidecar_Metadata의 JSON 파싱에 실패하면, THEN THE Ingestion_Service SHALL 해당 사진을 failed로 집계하고 오류 상세에 파일명과 원인 유형(메타데이터 없음, 파싱 실패, 누락된 필드 이름 중 하나)을 기록한다
4. IF 촬영 시각이 날짜·시각으로 파싱되지 않거나(UserComment JSON의 date가 YYYY-MM-DD, time이 HH:MM:SS 형식이 아님, 표준 EXIF의 DateTimeOriginal이 `YYYY:MM:DD HH:MM:SS` 형식이 아님, Sidecar_Metadata의 captured_at이 ISO 8601이 아님) 타임존 오프셋을 정할 수 없으면, THEN THE Ingestion_Service SHALL 해당 사진을 failed로 집계하고 오류 상세에 촬영 시각 형식 오류를 기록한다
5. IF latitude가 숫자가 아니거나 -90 이상 90 이하 범위를 벗어나거나, longitude가 숫자가 아니거나 -180 이상 180 이하 범위를 벗어나면, THEN THE Ingestion_Service SHALL 해당 사진을 failed로 집계하고 오류 상세에 범위를 벗어난 필드 이름을 기록한다
6. WHEN 사전 체감 피드백 값이 포함된 Sidecar_Metadata가 있는 사진으로부터 Outfit이 생성되면, THE Feedback_Service SHALL 해당 값을 그 Outfit에 연결된 source=`demo_seed` feedback 레코드 1건으로 Outfit과 같은 트랜잭션에서 저장한다. 사전 체감 피드백은 Sidecar_Metadata에서만 읽는다
7. WHEN Outfit을 생성하면, THE Outfit_Repository SHALL 촬영 시각·위치를 Embedded_Metadata에서 읽었으면 source 필드를 `exif`로, Sidecar_Metadata에서 읽었으면 `demo_sidecar`로 저장하고, 합성 여부를 함께 저장한다
8. FOR ALL 유효한 Embedded_Metadata 값(임의의 타임존 오프셋을 가진 촬영 시각, 범위 안의 위경도, 합성 여부)에 대해, 그 값을 UserComment JSON으로 이미지에 기록한 뒤 Metadata_Reader로 다시 읽은 결과 SHALL 촬영 시각의 지역 시각과 타임존 오프셋, latitude, longitude, 합성 여부가 모두 원래 값과 같고, 표준 EXIF(DateTimeOriginal, OffsetTimeOriginal, GPS 도·분·초)로 기록한 뒤 다시 읽은 결과 SHALL 촬영 시각과 오프셋이 같고 위경도 차이가 각각 0.00001도 이하이다 (round-trip 속성)
9. IF Sidecar_Metadata의 사전 체감 피드백 값이 있지만 `cold`, `comfortable`, `hot` 중 하나가 아니면, THEN THE Ingestion_Service SHALL 해당 사진을 failed로 집계하고 오류 상세에 Thermal_Vote 값 오류를 기록한다
10. IF 기준 3, 4, 5, 9 중 하나의 이유로 사진이 failed로 집계되면, THEN THE Ingestion_Service SHALL 해당 사진에 대해 Outfit, Weather_Snapshot, feedback 레코드를 생성하지 않고 Vision_Provider와 Weather_Provider를 호출하지 않는다
11. WHEN UserComment JSON 또는 Sidecar_Metadata에 이 요구사항이 정의한 필드 외의 필드(예: location, outfit, lighting, prompt, source, file, index)가 있으면, THE Ingestion_Service SHALL 추가 필드를 무시하고 저장하지 않으며 해당 사진을 failed로 집계하지 않는다
12. THE Metadata_Reader SHALL 촬영 시각·위치 출처를 다음 순서로 고른다: (1) UserComment JSON, (2) 표준 EXIF의 DateTimeOriginal·OffsetTimeOriginal과 GPS IFD 위경도, (3) Sidecar_Metadata의 captured_at·latitude·longitude. 앞 순위 출처가 없거나 유효하지 않으면 다음 순위 출처를 쓰고, 모든 출처가 실패하면 존재했던 출처 중 가장 앞 순위 출처의 오류를 기준 3, 4, 5의 형식으로 기록한다
13. WHEN Metadata_Reader가 UserComment JSON을 읽으면, THE Metadata_Reader SHALL 8바이트 문자 코드 헤더를 제거한 나머지를 UTF-8 JSON 객체로 해석하고, date와 time을 utc_offset으로 결합해 촬영 시각을 만들며, utc_offset이 없으면 timezone(IANA 이름)으로 해당 지역 시각의 오프셋을 계산한다
14. WHEN Metadata_Reader가 표준 EXIF를 읽으면, THE Metadata_Reader SHALL DateTimeOriginal과 OffsetTimeOriginal이 모두 있을 때만 촬영 시각으로 쓰고, GPS 위도·경도의 도·분·초 값을 십진 도로 변환하며 GPSLatitudeRef가 `S`이거나 GPSLongitudeRef가 `W`이면 부호를 음수로 한다
15. WHEN Embedded_Metadata의 UserComment JSON에서 synthetic이 true이거나 GPSProcessingMethod 값이 `SYNTHETIC`이면, 또는 촬영 시각·위치를 Sidecar_Metadata에서 읽으면, THE Outfit_Repository SHALL 해당 Outfit의 합성 여부를 true로 저장한다
16. WHEN 사진에 유효한 Embedded_Metadata와 Sidecar_Metadata가 모두 있으면, THE Ingestion_Service SHALL 촬영 시각·위치는 Embedded_Metadata 값을 쓰고 Sidecar_Metadata의 captured_at·latitude·longitude는 무시하며, Sidecar_Metadata에서는 사전 체감 피드백만 읽는다. 이때 Sidecar_Metadata에 captured_at·latitude·longitude가 없어도 failed로 집계하지 않지만, Sidecar_Metadata 파일이 있는데 JSON 파싱에 실패하면 사전 체감 피드백을 확인할 수 없으므로 기준 3에 따라 failed로 집계한다
17. THE Ingestion_Service SHALL Embedded_Metadata 중 촬영 시각, latitude, longitude, 합성 여부만 저장하고, 그 밖의 EXIF 값과 UserComment JSON의 텍스트 필드를 Outfit_Repository, API 응답, Vision_Provider 요청에 넣지 않는다

### Requirement 3: 의류 태그 비전 분석

**User Story:** 데모 사용자로서, 사진 속 착장이 자동으로 태그되기를 원한다. 그래서 옷 종류를 직접 입력하지 않아도 된다.

#### Acceptance Criteria

1. WHEN Ingestion_Service가 신규 이미지(기존 Outfit과 SHA-256 해시가 일치하지 않는 이미지)를 수집하면, THE Vision_Provider SHALL 이미지 1장당 정확히 1회 분석하고 `photo_type`(`outfit`, `non_outfit`, `uncertain`), `photo_type_confidence`(0.0~1.0), 판정 근거 `reason`(1~200자), `tags`를 가진 VisionAnalysis JSON을 반환한다. `tags`는 upper_type(UpperType enum), outer_type(OuterType enum, 겉옷이 없으면 `none`), lower_type(LowerType enum), colors(문자열 최대 5개), layers_visible(0~5), confidence(0.0~1.0)를 포함한다. 세 의류 타입은 snake_case canonical 식별자만 허용하며 최소한 `long_puffer`, `short_puffer`, `t_shirt`, `shorts`, `none`, `unknown`을 포함한다
2. WHERE VISION_PROVIDER가 `gemini`로 설정되면, THE WeatherWear_System SHALL Gemini_Vision_Provider를 사용하고, Gemini 공식 Python SDK(google-genai)로 Agent_Platform_Express_Mode 클라이언트(`genai.Client(enterprise=True, api_key=GEMINI_API_KEY 값)`, 프로젝트 ID와 위치 없음)를 만들어 Config_Loader가 읽은 GEMINI_MODEL 환경 변수의 모델 ID로 호출하며, Google AI Studio(Gemini Developer API) 엔드포인트로는 요청하지 않는다
3. WHERE VISION_PROVIDER가 `fixture`로 설정되면, THE WeatherWear_System SHALL Fixture_Vision_Provider를 사용하고, 외부 네트워크 호출을 0회 수행하며, 같은 이미지(같은 SHA-256 해시)에 대해 매번 같은 VisionAnalysis JSON을 반환한다. fixture에 없는 해시는 `uncertain`과 빈 의류 태그를 반환한다
4. WHEN Gemini_Vision_Provider가 응답을 받으면, THE Gemini_Vision_Provider SHALL 응답을 VisionAnalysis Pydantic 스키마로 검증하고 기준 1의 모든 필드·타입·범위를 만족하는 구조화 JSON만 반환한다
5. IF Gemini_Vision_Provider의 응답이 스키마 검증에 실패하거나, API 호출이 오류를 반환하거나, API 호출이 30초 안에 응답하지 않으면, THEN THE Ingestion_Service SHALL 오류를 로그에 기록하고, 해당 사진을 failed로 집계하고, 실패 원인(스키마 검증 실패, API 오류, 시간 초과 중 하나)을 오류 상세에 기록하고, 해당 사진의 Outfit과 Weather_Snapshot을 저장하지 않고, 같은 스캔 안에서 해당 사진의 분석을 재시도하지 않은 채 나머지 파일의 처리를 계속한다
6. IF upper_type, outer_type, lower_type 중 어떤 의류 속성을 사진에서 확인할 수 없으면, THEN THE Vision_Provider SHALL 해당 필드에 문자열 `unknown`을 반환한다. 사진으로 판단할 수 없는 속성(속옷, 원단 두께, 보온성 등)은 추측값이 아닌 `unknown`으로 표현한다
7. WHEN Gemini_Vision_Provider가 외부 API를 호출하면, THE Gemini_Vision_Provider SHALL EXIF(GPS 포함)와 그 밖의 메타데이터 블록을 제거한 이미지 바이트와 분석 프롬프트만 전송하고, Embedded_Metadata와 Sidecar_Metadata의 촬영 시각, latitude, longitude, UserComment JSON 내용, 파일 이름은 요청에 포함하지 않는다
8. WHEN Outfit_Repository가 Vision 분석 결과를 저장하면, THE Outfit_Repository SHALL 결과를 생성한 Vision_Provider 구현체에 따라 vision_provider 필드에 `gemini` 또는 `fixture`를 저장한다
9. FOR ALL 기준 1을 만족하는 유효한 VisionAnalysis 객체에 대해, 객체를 JSON으로 직렬화한 뒤 다시 파싱한 결과 SHALL 모든 필드에서 원래 객체와 값이 같고 colors 목록의 순서도 같다 (round-trip 속성)
10. WHEN VisionAnalysis의 photo_type이 `non_outfit`이면, THE Ingestion_Service SHALL 해당 사진을 skipped_non_outfit으로 집계하고 Sidecar_Metadata 또는 Weather_Provider를 사용하지 않으며 Outfit을 저장하지 않는다
11. WHEN VisionAnalysis의 photo_type이 `uncertain`이거나 `outfit`/`non_outfit` 판정의 photo_type_confidence가 0.70 미만이면, THE Ingestion_Service SHALL 해당 사진을 uncertain으로 집계하고 Sidecar_Metadata 또는 Weather_Provider를 사용하지 않으며 Outfit을 저장하지 않는다
12. WHEN VisionAnalysis의 photo_type이 `non_outfit`이면, THE Vision_Provider SHALL tags를 UNKNOWN_TAGS와 같은 값으로 반환한다
13. IF Agent_Platform_Express_Mode가 GEMINI_MODEL의 모델 ID를 지원하지 않아 오류를 반환하면, THEN THE Ingestion_Service SHALL 기준 5와 같이 해당 사진을 API 오류로 failed 집계하고, 오류 상세와 로그에 모델 ID와 HTTP 상태 코드만 기록하며 GEMINI_API_KEY 값은 기록하지 않는다

### Requirement 4: 촬영 시점 날씨 연결

**User Story:** 데모 사용자로서, 각 착장 사진이 촬영 당시의 날씨와 자동으로 연결되기를 원한다. 그래서 비슷한 날씨의 과거 착장을 찾을 수 있다.

#### Acceptance Criteria

1. WHEN Outfit이 생성되고 해당 Outfit에 captured_at, latitude(-90.0 이상 90.0 이하), longitude(-180.0 이상 180.0 이하)가 모두 존재하면, THE Weather_Provider SHALL 해당 captured_at, latitude, longitude에 대한 날씨를 1회 조회한다
2. WHEN 시간 단위 날씨 데이터를 사용하면, THE Weather_Provider SHALL 동일 시간대 기준으로 비교하여 captured_at과의 시간 차이가 가장 작은 시각의 시간 단위 값을 선택하고, 시간 차이가 같은 두 시각이 있으면 더 이른 시각의 값을 선택한다
3. WHEN Weather_Snapshot을 저장하면, THE Outfit_Repository SHALL observed_at(선택된 시간 단위 값의 시각), actual_temperature_c(필수), apparent_temperature_c, relative_humidity_pct, wind_speed_kmh, precipitation_mm, weather_code, provider(`open_meteo` 또는 `fixture`), is_fixture 필드를 저장하고, 제공자가 값을 반환하지 않은 선택 필드는 빈 값(null)으로 저장한다
4. THE Outfit_Repository SHALL Outfit 1건당 Weather_Snapshot을 최대 1건만 저장하며, 이미 Weather_Snapshot이 있는 Outfit이 다시 스캔되면 새 Weather_Snapshot을 추가하지 않고 기존 Weather_Snapshot을 유지한다
5. WHERE WEATHER_PROVIDER가 `open_meteo`로 설정되면, THE WeatherWear_System SHALL OpenMeteo_Weather_Provider를 사용하고 API 키 없이 Open-Meteo를 호출한다
6. WHERE WEATHER_PROVIDER가 `fixture`로 설정되면, THE WeatherWear_System SHALL Fixture_Weather_Provider를 사용하고 외부 API를 호출하지 않으며, Recorded_Weather_Fixture에서 사진의 촬영 시각·위치에 해당하는 기록 값을 반환하고, 저장되는 모든 Weather_Snapshot의 provider를 `fixture`, is_fixture를 true로 기록한다
7. IF OpenMeteo_Weather_Provider의 호출이 10초 안에 응답하지 않거나, 네트워크 오류 또는 실패 응답을 반환하거나, 응답에 선택된 시각의 actual_temperature_c 값이 없으면, THEN THE Weather_Provider SHALL 고정 fixture 날씨를 반환하고, 해당 Weather_Snapshot의 provider를 `fixture`, is_fixture를 true로 기록하며, Outfit 생성을 취소하지 않는다
8. WHEN OpenMeteo_Weather_Provider가 응답을 정규화하면, THE OpenMeteo_Weather_Provider SHALL 기온과 체감 기온을 °C, 상대 습도를 %(0 이상 100 이하), 풍속을 km/h, 강수량을 mm 단위로 변환하여 Weather_Snapshot 필드에 저장하고, 응답 단위가 이미 해당 단위이면 값을 변경하지 않는다
9. WHEN Mobile_Web_Client가 Weather_Snapshot 기반 날씨 값을 표시하면, THE Mobile_Web_Client SHALL 같은 날씨 표시 영역에 해당 값이 실측값이 아니라 제공자의 추정치/재분석값이라는 안내 문구를 표시하고, is_fixture가 true이면 fixture 데이터임을 나타내는 표시를 함께 보여준다
10. WHEN Fixture_Weather_Provider가 동일한 captured_at, latitude, longitude로 여러 번 호출되면, THE Fixture_Weather_Provider SHALL 매 호출마다 동일한 날씨 값을 반환한다
11. WHEN OpenMeteo_Weather_Provider가 날씨를 조회하면, THE OpenMeteo_Weather_Provider SHALL Open_Meteo_Archive_API(`https://archive-api.open-meteo.com/v1/archive`)에 latitude, longitude, 조회 대상 날짜(APP_TIMEZONE 기준 YYYY-MM-DD)를 start_date와 end_date로, hourly=`temperature_2m,precipitation,relative_humidity_2m`, timezone=APP_TIMEZONE(기본값 `Asia/Seoul`)을 담아 요청 1건을 보낸다
12. WHEN OpenMeteo_Weather_Provider가 Open_Meteo_Archive_API 응답을 Weather_Snapshot으로 변환하면, THE OpenMeteo_Weather_Provider SHALL 선택된 시각의 temperature_2m을 actual_temperature_c로, precipitation을 precipitation_mm으로, relative_humidity_2m을 relative_humidity_pct로 저장하고, 응답에 없는 apparent_temperature_c, wind_speed_kmh, weather_code는 null로 저장한다
13. WHEN 개발자가 Weather_Recording_Script를 실행하면, THE Weather_Recording_Script SHALL Photo_Dir의 각 사진에서 Metadata_Reader로 촬영 시각·위치를 읽고, 기준 11과 같은 요청 파라미터로 Open_Meteo_Archive_API를 조회하며(같은 날짜와 같은 좌표의 사진은 요청 1건을 공유), 기준 2와 12와 같은 규칙으로 변환한 값을 사진별 항목(파일 이름, 촬영 시각, latitude, longitude, observed_at, actual_temperature_c, apparent_temperature_c, relative_humidity_pct, wind_speed_kmh, precipitation_mm, weather_code)으로 `data/demo/fixtures/weather.json`에 기록하고, 파일에 기록 정보(출처 Open-Meteo Archive API, 엔드포인트, 요청한 hourly 변수, timezone, 기록 날짜 YYYY-MM-DD)를 함께 남긴다
14. IF Weather_Recording_Script가 사진 1건의 메타데이터를 읽지 못하거나 Open_Meteo_Archive_API 응답에서 선택 시각의 temperature_2m을 얻지 못하면, THEN THE Weather_Recording_Script SHALL 해당 사진의 항목을 지어내지 않고 파일 이름과 원인을 출력하며, 0이 아닌 종료 코드로 끝나고 기존 `weather.json`을 덮어쓰지 않는다
15. THE WeatherWear_System SHALL Weather_Recording_Script를 자동화 테스트와 API_Server 기동 과정에서 실행하지 않고, Fixture_Weather_Provider가 Recorded_Weather_Fixture를 반환할 때 Weather_Snapshot의 provider를 `open_meteo`가 아닌 `fixture`로 기록한다

### Requirement 5: 오늘 날씨 컨텍스트

**User Story:** 데모 사용자로서, 오늘 날씨를 확인하고 시나리오를 바꿔 볼 수 있기를 원한다. 그래서 다양한 날씨에서 추천을 시연할 수 있다.

#### Acceptance Criteria

1. WHEN GET /api/weather/today 요청이 수신되면, THE API_Server SHALL 선택된 Weather_Scenario가 있으면 그 Weather_Scenario의 값으로, 선택된 Weather_Scenario가 없으면 설정된 Weather_Provider(WEATHER_PROVIDER 값에 따른 구현체)가 조회한 오늘 날씨로 WeatherContext를 만들어 반환하며, 반환되는 WeatherContext는 항상 actual_temperature_c 값을 포함한다
2. WHEN API_Server가 시작되면, THE WeatherWear_System SHALL `data/demo/scenarios.json`에서 고정 Weather_Scenario 목록을 1회 읽고, 각 Weather_Scenario에 고유 식별자와 actual_temperature_c 값이 있는지 검증한다
3. WHEN Demo_Admin_Panel에서 Weather_Scenario가 선택되면, THE API_Server SHALL 선택 이후 처리하는 모든 GET /api/weather/today 응답과 GET /api/recommendations/today 응답에 선택된 Weather_Scenario를 오늘 날씨로 사용하고, 다른 Weather_Scenario가 선택되거나 API_Server 프로세스가 재시작될 때까지 이 선택을 유지한다
4. WHERE DEFAULT_LATITUDE와 DEFAULT_LONGITUDE가 설정되면, THE Weather_Provider SHALL Config_Loader가 읽은 두 좌표 값(위도 −90~90, 경도 −180~180)을 오늘 날씨 조회 위치로 사용한다
5. THE API_Server SHALL 모든 WeatherContext 응답에 날씨 출처(provider)와 fixture 여부를 포함하고, Weather_Scenario 기반 응답이면 출처를 Weather_Scenario로 표시하고 해당 Weather_Scenario 식별자를 함께 담으며 fixture 여부를 true로 설정한다
6. IF `data/demo/scenarios.json` 파일이 없거나, JSON으로 파싱할 수 없거나, actual_temperature_c가 없는 Weather_Scenario 항목이 있으면, THEN THE WeatherWear_System SHALL 오류를 로그에 기록하고 유효한 Weather_Scenario 항목만 선택 가능 목록에 남기며, 유효한 항목이 0건이어도 Weather_Provider 기반 오늘 날씨 응답을 계속 제공한다
7. IF Demo_Admin_Panel 또는 API 요청이 Weather_Scenario 목록에 없는 식별자를 선택하면, THEN THE API_Server SHALL 선택을 거부하고 알 수 없는 시나리오임을 나타내는 오류 상태 코드와 오류 메시지를 반환하며, 기존에 선택된 Weather_Scenario(또는 미선택 상태)를 그대로 유지한다
8. IF 선택된 Weather_Scenario가 없는 상태에서 OpenMeteo_Weather_Provider의 오늘 날씨 조회가 실패하면, THEN THE API_Server SHALL 고정 fixture 날씨로 WeatherContext를 반환하고 응답의 fixture 여부를 true로 설정한다

### Requirement 6: 유사 기온 후보 검색

**User Story:** 데모 사용자로서, 오늘과 기온이 비슷했던 날의 과거 착장을 보고 싶다. 그래서 실제 경험에 기반해 옷을 고를 수 있다.

#### Acceptance Criteria

1. WHEN GET /api/recommendations/today 요청이 수신되면, THE Rule_Based_V1 SHALL Weather_Snapshot이 연결되어 있고 그 Weather_Snapshot의 actual_temperature_c 값이 존재하는 Outfit 중에서 |과거 actual_temperature_c − 오늘 actual_temperature_c| ≤ Temperature_Tolerance를 만족하는 Outfit만 후보로 선택하고, 응답의 fallback 플래그를 false로 설정한다
2. THE Config_Loader SHALL TEMPERATURE_TOLERANCE_C 환경 변수 값을 Temperature_Tolerance(단위 °C)로 사용하고, 환경 변수가 설정되지 않았거나 빈 문자열이면 3.0을 사용한다
3. IF Temperature_Tolerance 내 후보가 0건이면, THEN THE Rule_Based_V1 SHALL 허용 오차를 Fallback_Tolerance(5.0°C)로 정확히 1회 확장하여 1번 기준과 같은 조건으로 후보를 다시 선택하고, 응답의 fallback 플래그를 true로 설정하며, 응답에 실제 적용된 허용 오차 값(5.0)을 포함한다
4. WHILE 응답의 fallback 플래그가 true이고 추천 후보가 1건 이상인 동안, THE Mobile_Web_Client SHALL "유사 기록 부족 / 범위 확장" 문구를 추천 영역에 표시한다
5. IF Fallback_Tolerance 내 후보도 0건이면, THEN THE Rule_Based_V1 SHALL 허용 오차를 추가로 확장하지 않고, fallback 플래그를 true로 유지한 채 빈 추천 목록(0건)과 "개인 착장 데이터 부족" 상태를 반환한다
6. WHILE 추천 응답이 "개인 착장 데이터 부족" 상태인 동안, THE Mobile_Web_Client SHALL 추천 착장 카드를 0개 표시하고, 오늘 날씨 요약(최소 actual_temperature_c 포함)과 "개인 착장 데이터 부족" 안내만 표시한다
7. THE Rule_Based_V1 SHALL 과거 기온과 오늘 기온의 차이를 소수점 첫째 자리로 반올림한 뒤 허용 오차와 비교하고, 반올림한 차이가 적용 중인 허용 오차(Temperature_Tolerance 또는 Fallback_Tolerance)와 정확히 같은 Outfit을 후보에 포함한다 (경계값 포함)
8. IF 오늘 WeatherContext에 actual_temperature_c가 없으면, THEN THE API_Server SHALL 추천을 계산하지 않고, 오류 상태 코드와 오늘 기온 정보 누락을 나타내는 오류 메시지를 반환하며, 저장된 Outfit, Weather_Snapshot, feedback 데이터를 변경하지 않는다
9. IF Weather_Snapshot이 없거나 Weather_Snapshot의 actual_temperature_c 값이 없는 Outfit이 있으면, THEN THE Rule_Based_V1 SHALL 해당 Outfit을 Temperature_Tolerance 검색과 Fallback_Tolerance 검색 모두에서 후보에서 제외하고, 나머지 Outfit으로 추천을 계속 계산한다
10. IF 후보 Outfit이 Non_Outfit_Photo(upper_type, outer_type, lower_type이 모두 `unknown`이고 confidence가 0.0)이면, THEN THE Rule_Based_V1 SHALL 해당 Outfit을 Temperature_Tolerance 검색과 Fallback_Tolerance 검색 모두에서 후보에서 제외하고(후보 0건 판정과 fallback 판정에도 포함하지 않음), 착장 기록 화면과 GET /api/outfits 응답에서는 해당 Outfit을 제외하지 않는다

### Requirement 7: 피드백 가중 랭킹

**User Story:** 데모 사용자로서, 과거에 적당했던 착장이 우선 추천되기를 원한다. 그래서 추웠거나 더웠던 실수를 반복하지 않는다.

#### Acceptance Criteria

1. THE Rule_Based_V1 SHALL 각 후보의 기온 차이(|후보 actual_temperature_c − 오늘 actual_temperature_c|)로 기온 점수를 계산하고, 기온 차이가 작은 후보에 더 높은 기온 점수를 부여한다 (기온 차이가 같은 두 후보는 기온 점수가 같다)
2. THE Rule_Based_V1 SHALL 각 후보의 최종 점수를 기온 점수와 Effective_Vote 보정값의 합으로 계산하고, 추천 결과를 최종 점수 내림차순으로 정렬한다
3. WHEN 후보의 Effective_Vote가 `comfortable`이면, THE Rule_Based_V1 SHALL 해당 후보에 0보다 큰 가산점을 부여하고 "과거에 적당했음"을 나타내는 사유를 설명에 포함한다
4. WHEN 후보의 Effective_Vote가 `cold` 또는 `hot`이면, THE Rule_Based_V1 SHALL 해당 후보에 0보다 큰 감점을 부여하고, `cold`이면 "과거에 추웠음", `hot`이면 "과거에 더웠음"을 나타내는 감점 사유를 설명에 포함한다
5. WHEN 후보에 Effective_Vote가 없으면, THE Rule_Based_V1 SHALL 해당 후보의 Effective_Vote 보정값을 0으로 두고, 같은 기온 차이를 가진 Effective_Vote 보유 후보보다 낮은 신뢰도 값을 부여한다
6. WHEN 두 후보의 기온 차이가 같으면, THE Rule_Based_V1 SHALL Effective_Vote가 `comfortable`인 후보, Effective_Vote가 없는 후보, Effective_Vote가 `cold` 또는 `hot`인 후보 순으로 높은 순위를 부여한다
7. WHEN 두 후보의 최종 점수가 같으면, THE Rule_Based_V1 SHALL 오늘 WeatherContext와의 체감온도 차이, 습도 차이, 풍속 차이, 강수량 차이를 이 순서로 비교하여 차이가 작은 후보에 높은 순위를 부여한다
8. IF 동점 처리 비교 항목의 값이 오늘 WeatherContext 또는 후보의 Weather_Snapshot에 없으면, THEN THE Rule_Based_V1 SHALL 해당 항목을 값이 있는 후보보다 낮은 순위로 처리하고, 모든 동점 처리 항목이 같으면 Outfit 식별자 오름차순으로 순위를 정한다
9. THE Rule_Based_V1 SHALL 기온 점수 가중치, `comfortable` 가산점, `cold`/`hot` 감점을 명시적인 설정 상수로 정의하고, 추천 결과에 알고리즘 버전 문자열 `rule_based_v1`을 포함한다
10. FOR ALL 동일한 WeatherContext와 후보 집합에 대해, THE Rule_Based_V1 SHALL 후보 입력 순서와 관계없이 동일한 순서와 동일한 점수의 추천 결과를 반환한다 (결정성)
11. FOR ALL 후보 집합에 대해, 한 후보의 Effective_Vote를 `cold` 또는 `hot`에서 `comfortable`로 변경하면 THE Rule_Based_V1 SHALL 해당 후보의 최종 점수를 변경 전 점수보다 높게 계산한다 (메타모픽 속성)
12. FOR ALL 추천 결과에 대해, THE Rule_Based_V1 SHALL 입력 후보 집합에 포함된 Outfit만 반환하고, 같은 Outfit을 2회 이상 반환하지 않는다 (불변 속성)
13. THE Recommendation_Strategy SHALL Weather_Provider, Vision_Provider, Outfit_Repository 구현에 의존하지 않고 입력 인자(today, candidates)만으로 결과를 계산한다
14. THE API_Server SHALL 팩토리 또는 의존성 주입으로 Recommendation_Strategy 구현체를 선택한다

### Requirement 8: 추천 응답과 설명

**User Story:** 데모 사용자로서, 왜 이 착장이 추천되었는지 이해하고 싶다. 그래서 추천을 신뢰하고 판단할 수 있다.

#### Acceptance Criteria

1. WHEN GET /api/recommendations/today 요청이 성공적으로 처리되면, THE API_Server SHALL 응답에 다음을 포함한다: 점수 내림차순으로 정렬된 최대 5건의 후보 목록, 후보별 점수(숫자), 후보별 한국어 설명(1자 이상 300자 이하), 알고리즘 버전 문자열 `rule_based_v1`, boolean 타입 fallback 플래그
2. THE Rule_Based_V1 SHALL 각 후보 설명에 다음 5개 항목을 모두 포함한다: 오늘 actual_temperature_c, 과거 Weather_Snapshot의 actual_temperature_c, 두 값의 차이 절댓값(세 값 모두 소수점 첫째 자리까지 반올림하고 °C 단위 표기), Vision_Provider가 추출한 과거 착장 의류 태그 최대 3개, Effective_Vote의 한국어 표기(`cold`→"추웠음", `comfortable`→"적당함", `hot`→"더웠음") (예: "오늘 9.0°C, 지난 8.0°C에 니트+코트로 '적당함'을 선택했습니다. 기온 차이 1.0°C인 과거 착장을 추천합니다.")
3. WHEN 후보가 Temperature_Tolerance 밖이면서 Fallback_Tolerance(5.0°C) 이내여서 선택되었으면, THE Rule_Based_V1 SHALL 해당 후보 설명에 "기온 허용 범위를 5.0°C로 확장하여 선택되었음"을 나타내는 문장을 포함한다
4. WHEN 후보에 Effective_Vote가 없으면, THE Rule_Based_V1 SHALL 해당 후보 설명에서 Effective_Vote 항목 대신 "체감 피드백 기록 없음"을 나타내는 문장을 포함한다
5. THE API_Server SHALL 각 추천 결과에 `high`, `medium`, `low` 중 하나의 데이터 신뢰도 수준을 포함한다. 결정 규칙은 다음과 같다: Effective_Vote가 있고 Temperature_Tolerance 이내로 선택된 후보는 `high`, Effective_Vote가 없거나 Fallback_Tolerance로 선택된 경우 중 하나만 해당하는 후보는 `medium`, 두 경우 모두 해당하는 후보는 `low`
6. IF 후보의 의류 태그가 0개이면, THEN THE Rule_Based_V1 SHALL 설명의 착장 태그 항목에 "착장 태그 정보 없음"을 나타내는 문구를 넣고, 나머지 설명 항목과 점수는 그대로 반환한다
7. IF 추천 응답이 "개인 착장 데이터 부족" 상태이면, THEN THE API_Server SHALL 빈 후보 목록, 알고리즘 버전 `rule_based_v1`, fallback 플래그 true, 데이터 부족 상태 표시를 포함한 응답을 반환하고 오류 상태로 처리하지 않는다

### Requirement 9: 체감 피드백 저장

**User Story:** 데모 사용자로서, 착장에 대해 추웠는지 더웠는지를 한 번의 탭으로 기록하고 싶다. 그래서 다음 추천이 내 경험을 반영한다.

#### Acceptance Criteria

1. WHEN POST /api/outfits/{id}/feedback 요청이 존재하는 Outfit id와 {"thermal_vote": "cold|comfortable|hot"} 본문으로 수신되면, THE Feedback_Service SHALL 해당 Outfit id, 요청된 Thermal_Vote 값, 저장 시각, source=`user_action`을 가진 feedback 레코드 1건을 저장하고, THE API_Server SHALL 성공 상태 코드와 함께 저장된 Outfit id, Thermal_Vote 값, 저장 시각을 응답 본문으로 반환한다
2. IF 요청 본문에 thermal_vote 필드가 없거나, thermal_vote 값이 null이거나 문자열이 아니거나, 소문자 문자열 `cold`, `comfortable`, `hot` 중 하나와 정확히 일치하지 않으면(예: `COLD`, ` cold`, 빈 문자열), THEN THE API_Server SHALL 422 상태 코드와 thermal_vote 값이 유효하지 않음을 나타내는 오류 응답을 반환하고, feedback 레코드를 저장하지 않으며, 해당 Outfit의 기존 Effective_Vote를 변경하지 않는다
3. IF 요청 경로의 Outfit id에 해당하는 Outfit이 outfits 테이블에 존재하지 않으면, THEN THE API_Server SHALL 404 상태 코드와 Outfit을 찾을 수 없음을 나타내는 오류 응답을 반환하고 feedback 레코드를 저장하지 않는다
4. THE Feedback_Service SHALL source 값(`user_action` 또는 `demo_seed`)과 관계없이 Outfit별로 저장 시각이 가장 늦은 feedback 레코드의 Thermal_Vote를 Effective_Vote로 사용하고, 저장 시각이 같은 레코드가 2건 이상이면 마지막에 저장된 레코드(가장 큰 feedback id)를 사용하며, feedback 레코드가 0건인 Outfit은 Effective_Vote가 없는 것으로 취급한다
5. WHEN 같은 Outfit에 새 feedback이 저장되면, THE Feedback_Service SHALL 이전 feedback 레코드를 삭제하거나 수정하지 않고 모두 이력으로 보존한다. 같은 Outfit에 같은 Thermal_Vote 값이 반복 제출되어도 각각 별도의 레코드로 저장한다
6. WHEN 새 feedback 저장에 대한 성공 응답이 반환된 후 GET /api/recommendations/today 요청이 수신되면, THE Rule_Based_V1 SHALL 서버 재시작이나 수동 추천 재계산 없이 해당 Outfit의 갱신된 Effective_Vote로 랭킹을 계산하여 반환한다
7. THE Feedback_Service SHALL 착장 체감 피드백(Thermal_Vote)만 저장하고, 요청 본문에 thermal_vote 외의 필드(예: 추천 좋아요/싫어요)가 포함되어도 그 값을 저장하지 않는다
8. IF feedback 레코드 저장 중 Outfit_Repository에서 저장 오류가 발생하면, THEN THE API_Server SHALL 저장 실패를 나타내는 오류 상태 코드를 반환하고, 부분 저장된 feedback 레코드를 남기지 않으며, 해당 Outfit의 기존 Effective_Vote를 유지한다

### Requirement 10: 착장 조회 API

**User Story:** 프론트엔드 개발자로서, 착장 목록과 상세 정보를 일관된 계약으로 조회하고 싶다. 그래서 화면에 필드를 임의로 만들어 쓰지 않는다.

#### Acceptance Criteria

1. WHEN GET /api/outfits 요청이 수신되면, THE API_Server SHALL Outfit_Repository에 저장된 모든 Outfit을 captured_at 내림차순으로 정렬하고, captured_at이 같은 Outfit끼리는 id 내림차순으로 정렬하여, 각 Outfit의 Weather_Snapshot 및 Effective_Vote와 함께 200 상태 코드로 반환한다
2. WHEN GET /api/outfits 요청이 수신되고 저장된 Outfit이 0건이면, THE API_Server SHALL 200 상태 코드와 빈 목록을 반환한다
3. WHEN GET /api/outfits/{id} 요청이 수신되면, THE API_Server SHALL 해당 Outfit의 id, captured_at, 촬영 정보 출처(source: `exif` 또는 `demo_sidecar`), 메타데이터 합성 여부, 사진 URL, 의류 태그, Weather_Snapshot, Effective_Vote를 200 상태 코드로 반환한다
4. IF GET /api/outfits 또는 GET /api/outfits/{id} 응답 대상 Outfit에 연결된 Weather_Snapshot이 없거나 기록된 Thermal_Vote가 0건이면, THEN THE API_Server SHALL 해당 필드를 생략하지 않고 null 값으로 반환한다
5. IF GET /api/outfits/{id}의 id에 해당하는 Outfit이 Outfit_Repository에 존재하지 않으면, THEN THE API_Server SHALL 404 상태 코드와 Outfit을 찾을 수 없음을 나타내는 오류 응답을 반환하고 저장된 데이터를 변경하지 않는다
6. THE API_Server SHALL 모든 REST 엔드포인트의 요청 및 응답 스키마를 Pydantic 모델로 정의하고, 모든 엔드포인트와 각 엔드포인트의 요청·응답 필드를 OpenAPI 문서로 노출한다
7. THE Mobile_Web_Client SHALL API_Server의 OpenAPI 스키마에 정의된 필드 이름만 사용하고, OpenAPI 스키마에 없는 필드를 만들어 화면에 표시하지 않는다
8. THE API_Server SHALL Outfit_Repository에 등록된 Outfit의 사진만 Outfit id 기반 엔드포인트로 제공하고, Photo_Dir 내 파일을 파일 이름이나 경로로 직접 요청하는 정적 경로를 노출하지 않는다
9. IF Outfit id 기반 사진 엔드포인트 요청의 id가 등록된 Outfit이 아니거나 해당 Outfit의 사진 파일이 Photo_Dir에 더 이상 존재하지 않으면, THEN THE API_Server SHALL 404 상태 코드를 반환하고 응답에 서버 파일 시스템 경로를 포함하지 않는다
10. THE API_Server SHALL 모든 응답의 datetime 값을 타임존 오프셋(`Z` 또는 `±HH:MM`)이 포함된 ISO 8601 형식으로 반환한다

### Requirement 11: 모바일 홈 화면

**User Story:** 데모 사용자로서, 휴대폰 브라우저에서 오늘 날씨와 추천 착장을 한눈에 보고 싶다. 그래서 외출 전에 빠르게 옷을 고를 수 있다.

#### Acceptance Criteria

1. WHEN 홈 화면이 열리고 API_Server가 추천 결과를 1건 이상 반환하면, THE Mobile_Web_Client SHALL 오늘 날짜, WeatherContext의 actual_temperature_c, apparent_temperature_c(체감온도), relative_humidity_pct(습도), 1순위 추천 착장 사진, 해당 Outfit의 Weather_Snapshot 기온, 추천 근거, API_Server가 반환한 데이터 신뢰도 수준을 표시한다
2. THE Mobile_Web_Client SHALL 기온과 체감온도를 소수점 이하 1자리의 °C 단위로 표시하고, 습도를 정수 % 단위로 표시한다
3. IF WeatherContext의 apparent_temperature_c 또는 relative_humidity_pct 값이 없으면, THEN THE Mobile_Web_Client SHALL 해당 항목 자리에 값이 없음을 나타내는 표시를 하고, 나머지 항목은 기준 1과 같이 표시한다
4. THE Mobile_Web_Client SHALL 날짜와 시각을 APP_TIMEZONE(기본값 Asia/Seoul) 기준으로 표시한다
5. THE Mobile_Web_Client SHALL 사용자 데이터 값(태그 값, 환경 변수 값 등)을 제외한 모든 UI 문구(레이블, 버튼, 안내, 오류 메시지)를 한국어로 표시한다
6. WHILE 뷰포트 너비가 360px 이상 430px 이하인 동안, THE Mobile_Web_Client SHALL 가로 스크롤 없이 모든 화면 콘텐츠를 표시한다
7. THE Mobile_Web_Client SHALL 사용자에게 날씨 또는 의류를 수동으로 입력하는 입력 필드를 제공하지 않는다
8. IF API_Server가 "개인 착장 데이터 부족" 상태(추천 결과 0건)를 반환하면, THEN THE Mobile_Web_Client SHALL 오늘 날씨 항목은 계속 표시하고, 추천 착장 영역에는 "개인 착장 데이터 부족" 안내를 표시한다
9. IF 홈 화면 데이터 요청이 실패하거나 10초 안에 응답이 오지 않으면, THEN THE Mobile_Web_Client SHALL 데이터를 불러오지 못했다는 한국어 오류 메시지와 재시도 버튼을 표시하고, 이전에 표시한 내용을 지우지 않는다

### Requirement 12: 착장 기록 화면

**User Story:** 데모 사용자로서, 과거 착장 기록을 날씨와 함께 둘러보고 싶다. 그래서 내가 어떤 날씨에 무엇을 입었는지 떠올릴 수 있다.

#### Acceptance Criteria

1. WHEN 사용자가 기록 화면에 진입하면, THE Mobile_Web_Client SHALL GET /api/outfits가 반환한 Outfit을 모두 반환 순서(captured_at 역순)대로 썸네일 그리드 또는 카드 형태로 표시한다
2. THE Mobile_Web_Client SHALL 각 기록 항목에 다음 4가지를 표시한다: captured_at의 날짜(YYYY-MM-DD, APP_TIMEZONE 기준), Weather_Snapshot의 실제 기온(°C, 소수점 첫째 자리), 의류 태그 최대 3개, 평가 상태. 평가 상태는 Effective_Vote에 대응하는 "추웠어", "적당했어", "더웠어" 중 하나이며, Effective_Vote가 없으면 "미평가"이다
3. IF Outfit에 연결된 Weather_Snapshot이 없으면, THEN THE Mobile_Web_Client SHALL 해당 항목을 목록에서 제외하지 않고, 기온 자리에 날씨 정보가 없음을 나타내는 표시를 한다
4. WHEN 사용자가 기록 항목을 탭하면, THE Mobile_Web_Client SHALL GET /api/outfits/{id}의 응답으로 해당 Outfit의 상세 화면을 표시한다
5. WHEN 상세 화면이 표시되면, THE Mobile_Web_Client SHALL 사진, 해당 Outfit의 의류 태그 전체, Weather_Snapshot(실제 기온과, 값이 있는 경우 체감 기온·상대 습도·풍속·강수량), 평가 상태(Effective_Vote 또는 "미평가"), 촬영 정보 출처(`exif`이면 "사진 내장 메타데이터", `demo_sidecar`이면 "사이드카 파일")를 표시한다
6. IF GET /api/outfits가 0건을 반환하면, THEN THE Mobile_Web_Client SHALL 빈 그리드 대신 기록이 없음을 알리고 Demo_Admin_Panel에서 폴더 스캔을 실행하도록 안내하는 빈 상태 메시지를 표시한다
7. IF GET /api/outfits 또는 GET /api/outfits/{id} 요청이 실패하거나 404 상태 코드를 반환하면, THEN THE Mobile_Web_Client SHALL 불러오기 실패를 나타내는 오류 메시지와 재시도 버튼을 표시하고, 이미 표시 중이던 기록 목록은 유지한다

### Requirement 13: 웹 내부 알림 카드와 원클릭 피드백

**User Story:** 데모 사용자로서, 알림처럼 보이는 카드에서 바로 체감을 기록하고 싶다. 그래서 별도 화면 이동 없이 피드백을 남길 수 있다.

#### Acceptance Criteria

1. WHEN 오늘의 추천 화면이 로드되고 Recommendation_Strategy가 반환한 추천 결과가 1건 이상이면, THE Mobile_Web_Client SHALL 1순위 추천 Outfit 1건을 Notification_Card 형태로 앱 내부에 표시하며, Notification_Card에 해당 Outfit의 사진 썸네일과 오늘 WeatherContext의 actual_temperature_c를 포함한다
2. THE Notification_Card SHALL "🥶 추웠어", "😊 적당했어", "🥵 더웠어" 버튼 3개를 표시하고, 각 버튼을 순서대로 Thermal_Vote `cold`, `comfortable`, `hot`에 대응시킨다
3. IF Notification_Card에 표시된 Outfit에 Effective_Vote가 존재하면, THEN THE Notification_Card SHALL Effective_Vote에 대응하는 버튼 1개만 선택 상태로 구분하여 표시한다
4. WHEN 사용자가 Notification_Card의 피드백 버튼을 탭하면, THE Mobile_Web_Client SHALL 추가 입력이나 확인 단계 없이 Notification_Card에 표시된 Outfit의 id와 탭한 버튼에 대응하는 Thermal_Vote를 POST /api/outfits/{id}/feedback으로 1회 전송한다
5. WHILE 피드백 저장 요청이 응답 대기 중이면, THE Mobile_Web_Client SHALL Notification_Card의 피드백 버튼 3개를 모두 비활성화하고 추가 피드백 요청을 전송하지 않는다
6. WHEN 피드백 저장 응답이 성공이면, THE Mobile_Web_Client SHALL 브라우저 페이지 새로고침 없이 탭한 버튼을 선택 상태로 표시하고, 오늘의 추천 결과를 다시 조회하여 갱신된 결과를 표시한다
7. IF 피드백 저장 요청이 실패 응답을 받거나 10초 이내에 응답을 받지 못하면, THEN THE Mobile_Web_Client SHALL 저장 실패를 나타내는 메시지를 표시하고, 요청 이전의 선택 상태를 유지하며, 피드백 버튼 3개를 다시 활성화한다
8. WHEN 사용자가 Notification_Card에서 피드백 버튼 3개를 제외한 본문 영역을 탭하면, THE Mobile_Web_Client SHALL Notification_Card에 표시된 Outfit의 상세 화면을 표시한다
9. IF 오늘의 추천 결과가 0건("개인 착장 데이터 부족" 상태)이면, THEN THE Mobile_Web_Client SHALL Notification_Card를 표시하지 않고 기준 11.8과 같은 "개인 착장 데이터 부족" 안내를 표시한다

### Requirement 14: 데모 관리 화면

**User Story:** 데모 발표자로서, 스캔과 날씨 시나리오를 화면에서 제어하고 싶다. 그래서 5분 안에 전체 흐름을 시연할 수 있다.

#### Acceptance Criteria

1. WHILE 애플리케이션이 개발 모드로 실행되는 동안, THE Mobile_Web_Client SHALL 메인 화면과 분리된 별도 경로로 Demo_Admin_Panel을 제공하고, 메인 화면에 1회 탭으로 해당 경로로 이동하는 진입 링크를 표시한다
2. WHILE 애플리케이션이 개발 모드가 아닌 모드로 실행되는 동안, THE Mobile_Web_Client SHALL Demo_Admin_Panel 진입 링크를 표시하지 않고, Demo_Admin_Panel 경로에 직접 접근하면 폴더 스캔·Weather_Scenario 선택·추천 재계산 컨트롤 대신 사용할 수 없다는 안내를 표시한다
3. THE Demo_Admin_Panel SHALL 폴더 스캔 실행 버튼, `data/demo/scenarios.json`에 정의된 모든 Weather_Scenario를 담은 선택 목록(현재 선택된 Weather_Scenario 표시 포함), Weather_Scenario 목록 새로고침 버튼, 추천 재계산 버튼을 하나의 화면에 제공한다
4. WHEN 폴더 스캔이 완료되면, THE Demo_Admin_Panel SHALL API_Server 응답의 scanned, succeeded, duplicate, skipped_non_outfit, uncertain, failed 건수를 표시하고, 저장하지 않은 사진의 파일명·판정·신뢰도·근거와 실패한 파일의 파일명·오류 사유를 표시하며, failed가 0이면 실패 항목이 없다는 표시를 한다
5. WHILE 폴더 스캔, Weather_Scenario 선택 또는 추천 재계산 요청이 처리 중인 동안, THE Demo_Admin_Panel SHALL 해당 동작의 버튼을 비활성화하고 진행 중 표시를 보여 같은 동작의 중복 요청을 막는다
6. IF 폴더 스캔, Weather_Scenario 목록 조회, Weather_Scenario 선택 또는 추천 재계산 요청이 오류 응답을 받거나 30초 안에 응답을 받지 못하면, THEN THE Demo_Admin_Panel SHALL 어느 동작이 실패했는지 알리는 오류 메시지를 표시하고, 이전에 표시한 스캔 결과와 선택된 Weather_Scenario를 그대로 유지하며, 해당 버튼을 다시 활성화한다
7. WHEN Weather_Scenario 선택 또는 추천 재계산이 성공적으로 완료되면, THE Demo_Admin_Panel SHALL 현재 적용된 Weather_Scenario의 이름과 actual_temperature_c 값, 그리고 동작이 완료되었다는 표시를 보여준다

### Requirement 15: Fixture 모드와 데모 데이터

**User Story:** 데모 발표자로서, API 키 없이도 전체 데모를 실행하고 싶다. 그래서 외부 서비스 장애나 키 부재와 무관하게 시연할 수 있다.

#### Acceptance Criteria

1. WHILE WeatherWear_System이 Fixture_Mode로 실행되는 동안, THE WeatherWear_System SHALL GEMINI_API_KEY가 설정되지 않고 Gemini API와 Open-Meteo API로 외부 네트워크 요청을 0건 보낸 상태에서 기동, Photo_Dir 스캔, 추천 조회, Thermal_Vote 피드백 저장, 자동화 테스트 전체 통과를 완료하며, 동일한 데모 데이터와 동일한 Weather_Scenario로 반복 실행할 때마다 동일한 의류 태그, Weather_Snapshot 값, 추천 순위를 생성한다
2. THE WeatherWear_System SHALL `ex_img/`에 있는 사진 중 사용 권한이 확인된 사진 전체(현재 174장: PNG 134장, JPG 40장. 사용자가 `126_…`~`140_…`을 포함한 전체의 사용 권한을 확인함)를 파일 바이트를 바꾸지 않고(Embedded_Metadata 유지) `data/demo/photos`에 제공하며, 각 사진의 촬영 시각·위치는 사진의 Embedded_Metadata로 제공하고, Sidecar_Metadata는 사전 체감 피드백이 있는 사진에만 thermal_vote를 담아 제공한다
3. THE WeatherWear_System SHALL 출처와 사용 권한이 데모 데이터 목록에 기록된 사진만 데모 사진으로 사용한다
4. WHILE Fixture_Vision_Provider, Fixture_Weather_Provider 또는 Synthetic_Metadata가 사용되는 동안, THE Mobile_Web_Client SHALL 그 데이터에서 나온 각 항목(의류 태그, 날씨 값, 촬영 시각·위치) 옆에 fixture 또는 가상 데이터임을 나타내는 텍스트 라벨을 표시하고(촬영 시각·위치는 Outfit의 합성 여부가 true일 때 "가상 데이터"), 실제 Gemini_Vision_Provider 또는 OpenMeteo_Weather_Provider 결과와 합성 여부가 false인 메타데이터에서 나온 항목에는 이 라벨을 표시하지 않는다
5. THE WeatherWear_System SHALL Fixture_Vision_Provider 결과를 vision_provider=`fixture`로, Gemini_Vision_Provider 결과를 vision_provider=`gemini`로 저장하여 두 결과가 API 응답에서 구분되도록 한다
6. IF 기동 시 VISION_PROVIDER가 `gemini`이고 GEMINI_API_KEY 또는 GEMINI_MODEL이 설정되지 않았거나 빈 문자열이면, THEN THE Config_Loader SHALL 누락된 변수 이름(값은 제외)을 나타내는 설정 오류 메시지를 로그에 기록하고, 기동을 중단하지 않고 Fixture_Vision_Provider로 Vision_Provider를 설정한다
7. IF 사용 권한이 기록된 데모 사진이 0장이면, THEN THE WeatherWear_System SHALL 6장 이상 10장 이하의 플레이스홀더 이미지(각 이미지에 UserComment JSON Embedded_Metadata를 기록하고 synthetic을 true로 둠)와 각 이미지에 대응하는 시드 fixture(의류 태그와 Recorded_Weather_Fixture 형식의 날씨 항목)를 데모 데이터로 사용한다
8. IF Fixture_Vision_Provider에 스캔된 사진의 SHA-256 해시에 해당하는 사전 정의 결과가 없으면, THEN THE Fixture_Vision_Provider SHALL upper_type, outer_type, lower_type을 모두 `unknown`, colors를 빈 목록, layers_visible을 0, confidence를 0.0으로 하는 기본 결과를 반환하고, THE Outfit_Repository SHALL 그 결과를 vision_provider=`fixture`로 저장하여 스캔이 실패하지 않도록 한다
9. THE WeatherWear_System SHALL `data/demo/scenarios.json`에 Weather_Scenario 4개(`chilly-morning`, `cold-snap`, `early-heat`, `heatwave`)를 제공하고, 각 Weather_Scenario의 actual_temperature_c를 Recorded_Weather_Fixture에서 Non_Outfit_Photo를 뺀 착장 사진의 기온으로부터 다음 Scenario_Rules를 만족하도록 고른다(기온 차이는 Requirement 6.7과 같이 소수 첫째 자리 반올림, 기본 Temperature_Tolerance 3.0°C 기준): S1 `chilly-morning`은 기온 차이 3.0°C 이내 착장 사진이 5장 이상, S2 `cold-snap`은 기온 차이가 정확히 3.0°C인 착장 사진이 1장이고 3.0°C 이내의 다른 착장 사진이 0장, S3 `early-heat`은 3.0°C 이내 착장 사진이 0장이고 5.0°C 이내 착장 사진이 1장 이상, S4 `heatwave`는 5.0°C 이내 착장 사진이 0장
10. WHEN Recorded_Weather_Fixture나 Fixture_Vision_Provider 데이터가 바뀌면, THE Scenario_Checker SHALL 4개 Weather_Scenario가 기준 9의 Scenario_Rules를 모두 만족하는지 검사하고, 하나라도 만족하지 않으면 위반한 규칙 이름과 Weather_Scenario 식별자를 출력하며 실패(0이 아닌 종료 코드 또는 pytest 실패)로 끝난다
11. THE WeatherWear_System SHALL 데모 사진의 사전 체감 피드백을 설계 문서의 시드 투표 규칙에 따라 결정적으로 정하고, Non_Outfit_Photo에는 사전 체감 피드백을 두지 않으며, S1 `chilly-morning`의 3.0°C 이내 후보 중 1장 이상은 `comfortable`, 1장 이상은 `cold` 또는 `hot` 사전 체감 피드백을 갖게 한다
12. THE WeatherWear_System SHALL `ex_img/clothing_tags.json`(schema "WeatherWear ClothingTags", 174개 항목)의 태그를 파일 이름으로 `data/demo/photos`의 사진과 연결하고, 각 사진 파일 바이트 전체의 SHA-256 해시를 키로 `data/demo/fixtures/vision.json`에 기록하여 Fixture_Vision_Provider 데이터로 사용하며, 태그가 Requirement 3.1의 제약을 만족하지 않거나 사진과 연결되지 않는 항목이 있으면 가져오기를 실패로 끝낸다
13. THE WeatherWear_System SHALL Non_Outfit_Photo(현재 JPG 20장)를 데모 사진과 착장 기록에 포함하되, Requirement 6.10에 따라 추천 후보에서 제외한다

### Requirement 16: 환경 변수와 비밀값 관리

**User Story:** 개발자로서, 설정을 환경 변수로 관리하고 비밀값이 노출되지 않기를 원한다. 그래서 저장소를 안전하게 공유할 수 있다.

#### Acceptance Criteria

1. THE WeatherWear_System SHALL 저장소 루트에 `.env.example`을 제공하고, 이 파일은 GEMINI_API_KEY, VISION_PROVIDER, GEMINI_MODEL, WEATHER_PROVIDER, DATABASE_URL, PHOTO_DIR, APP_TIMEZONE, TEMPERATURE_TOLERANCE_C, DEFAULT_LATITUDE, DEFAULT_LONGITUDE, CORS_ORIGINS, VITE_API_BASE_URL 12개 변수 각각에 대해 변수 이름, 기본값(기본값이 없으면 빈 값), 한 줄 이상의 설명을 포함하며, GEMINI_API_KEY 설명에는 이 값이 Agent_Platform_Express_Mode API 키이고 Google AI Studio 키로는 동작하지 않는다는 점을 적는다
2. WHEN API_Server가 기동되면, THE Config_Loader SHALL 프로세스 환경 변수, `.env` 파일 값, 기본값 순서의 우선순위로 각 설정값을 결정하고, 프로세스 환경 변수와 `.env` 모두에 값이 없는 변수에는 다음 기본값을 사용한다: VISION_PROVIDER=fixture, WEATHER_PROVIDER=fixture, DATABASE_URL=sqlite:///./data/weatherwear.db, PHOTO_DIR=./data/demo/photos, APP_TIMEZONE=Asia/Seoul, TEMPERATURE_TOLERANCE_C=3, DEFAULT_LATITUDE=37.5665, DEFAULT_LONGITUDE=126.9780, CORS_ORIGINS=http://localhost:5173, VITE_API_BASE_URL=http://localhost:8000, GEMINI_MODEL=구현 시 Agent Platform 공식 문서로 확인한 Gemini 모델 ID(설계 문서의 리서치 요약에 확인 날짜와 express mode 지원 여부를 기록)
3. THE WeatherWear_System SHALL `.env.example`의 GEMINI_API_KEY 값을 빈 문자열로 두고, 저장소에 커밋되는 어떤 파일에도 실제 GEMINI_API_KEY 값을 포함하지 않는다
4. THE WeatherWear_System SHALL 저장소 루트 `.gitignore`에 `.env`를 포함하고, `.env.example`은 `.gitignore`에서 제외하여 저장소에 커밋되게 한다
5. THE WeatherWear_System SHALL GEMINI_API_KEY 값 전체와 그 일부 문자열을 백엔드 로그, 예외 메시지와 스택 트레이스, README, 브라우저 콘솔, API 응답 본문, Demo_Admin_Panel 화면에 출력하지 않고, GEMINI_API_KEY 상태는 "설정됨" 또는 "미설정" 여부로만 표시한다
6. THE Mobile_Web_Client SHALL `VITE_` 접두사 환경 변수로 VITE_API_BASE_URL만 사용하고, 프론트엔드 빌드 결과물에 GEMINI_API_KEY 값이나 이름이 포함되지 않게 한다
7. THE API_Server SHALL CORS_ORIGINS를 쉼표로 구분된 출처 목록(각 항목의 앞뒤 공백은 제거)으로 해석하고, 목록에 있는 출처와 정확히 일치하는 Origin의 요청에만 CORS 허용 응답 헤더를 반환하며, 목록에 없는 Origin의 요청에는 CORS 허용 응답 헤더를 반환하지 않는다
8. IF API_Server 기동 시 VISION_PROVIDER 값이 `gemini`, `fixture` 중 하나가 아니거나 WEATHER_PROVIDER 값이 `open_meteo`, `fixture` 중 하나가 아니면, THEN THE Config_Loader SHALL 해당 변수 이름과 허용값 목록을 담은 설정 오류 메시지를 로그에 기록하고 해당 변수에 기본값 `fixture`를 사용한다
9. IF API_Server 기동 시 TEMPERATURE_TOLERANCE_C가 0 초과 5.0 미만의 숫자가 아니거나, DEFAULT_LATITUDE가 -90 이상 90 이하의 숫자가 아니거나, DEFAULT_LONGITUDE가 -180 이상 180 이하의 숫자가 아니면, THEN THE Config_Loader SHALL 해당 변수 이름과 허용 범위를 담은 설정 오류 메시지를 로그에 기록하고 해당 변수에 기준 2의 기본값을 사용한다
10. WHERE VISION_PROVIDER가 `gemini`로 설정되면, THE WeatherWear_System SHALL 사용자가 `.env`(또는 프로세스 환경 변수)의 GEMINI_API_KEY에 넣은 Agent_Platform_Express_Mode API 키만으로 Gemini_Vision_Provider를 인증하고, Google Cloud 프로젝트 ID, 위치, 서비스 계정 자격 증명을 요구하지 않는다

### Requirement 17: 문서화와 자동화 테스트

**User Story:** 심사위원 및 개발자로서, README만 보고 로컬에서 실행하고 테스트 결과를 확인하고 싶다. 그래서 구현이 실제로 동작함을 검증할 수 있다.

#### Acceptance Criteria

1. THE WeatherWear_System SHALL 저장소 최상위에 README를 제공하고, README에 다음 항목을 각각 별도 절로 기재한다: API_Server 로컬 실행 명령, Mobile_Web_Client 로컬 실행 명령, Config_Loader가 읽는 모든 환경 변수의 이름과 기본값(PHOTO_DIR, TEMPERATURE_TOLERANCE_C, VISION_PROVIDER, WEATHER_PROVIDER 포함), API 키 설정 방법(GEMINI_API_KEY에 Agent_Platform_Express_Mode API 키를 넣고, Google AI Studio 키는 동작하지 않는다는 안내 포함), VISION_PROVIDER와 WEATHER_PROVIDER 값 변경을 통한 Fixture_Mode와 Live_Mode 전환 방법
2. THE WeatherWear_System SHALL README에 5분 데모 절차를 번호가 매겨진 단계 목록으로 기재하고, 각 단계에 수행할 동작과 화면에서 확인할 결과를 명시하며, 이 절차는 API 키 없이 Fixture_Mode에서 처음부터 끝까지 수행 가능해야 한다
3. THE WeatherWear_System SHALL README에 다음 사실을 명시한다: 촬영 시각과 위치는 각 사진에 내장된 합성(synthetic) EXIF 메타데이터(UserComment JSON, JPG는 표준 EXIF도)에서 읽고 Sidecar_Metadata는 대체 수단과 사전 체감 피드백에만 쓴다는 점, 데모 사진의 위치와 시각은 실제 촬영 기록이 아닌 가상 값이라는 점, Fixture_Mode의 날씨는 Weather_Recording_Script가 Open-Meteo Archive API에서 기록한 재분석값이며 기록 날짜가 `weather.json`에 있다는 점, Fixture_Mode에서는 `ex_img/clothing_tags.json`에서 가져온 Fixture_Vision_Provider의 mock Vision 결과를 사용한다는 점, Gemini 호출 전에 EXIF와 위치 정보를 이미지에서 제거한다는 점, Non_Outfit_Photo는 기록 화면에는 보이지만 추천에서는 제외된다는 점
4. THE WeatherWear_System SHALL 다음 영역마다 1개 이상의 pytest 자동화 테스트를 제공한다: (a) 수집 및 중복 제거(동일 SHA-256 해시 사진을 다시 스캔해도 Outfit이 추가로 생성되지 않음), (b) 날씨 정규화, (c) 허용 오차 경계값(기온 차이가 정확히 Temperature_Tolerance 3.0°C인 후보는 포함되고 3.0°C를 넘는 후보는 제외되며, 기본 허용 오차 내 후보가 0건이면 Fallback_Tolerance 5.0°C로 같은 경계 규칙을 적용함), (d) 데이터 부족 처리, (e) 피드백 갱신(가장 최근 Thermal_Vote가 Effective_Vote가 됨), (f) API 상태 코드(정상 요청 1건 이상과 유효하지 않은 입력 요청 1건 이상), (g) 내장 메타데이터 읽기(UserComment JSON, 표준 EXIF, Sidecar_Metadata 대체 순서와 메타데이터 누락·형식 오류 처리)
5. WHEN 개발자가 API 키 환경 변수를 설정하지 않은 상태에서 README에 기재된 테스트 명령을 실행하면, THE WeatherWear_System SHALL Gemini와 Open-Meteo 외부 API를 0회 호출하면서 모든 pytest 자동화 테스트를 실행하고, 전체 테스트 통과 시 종료 코드 0으로 종료한다
6. THE WeatherWear_System SHALL README에 pytest 테스트 실행 명령 1개와 전체 통과로 판정하는 기준(실패 0건, 종료 코드 0)을 기재한다
7. THE WeatherWear_System SHALL 백엔드와 프론트엔드 의존성 파일에서 모든 직접 의존성 라이브러리를 정확한 단일 버전으로 고정하며, 고정된 버전은 기준 4의 pytest 자동화 테스트가 전부 통과한 버전과 같아야 한다
8. THE WeatherWear_System SHALL README의 Phase 2 개선 목록에 다음 4개 항목과 항목별 1문장 이상의 설명을 포함한다: 실제 갤러리 접근, Web Push 알림, Recommendation_Strategy 구현체 교체를 통한 추천 엔진 교체, SQLite에서 다른 저장소로 옮기는 조건
9. THE WeatherWear_System SHALL README에서 완료된 기능 목록에는 통과하는 pytest 자동화 테스트가 있거나 5분 데모 절차에서 재현되는 기능만 기재하고, 두 조건을 모두 충족하지 못한 기능은 미완료 또는 미검증 항목으로 따로 기재한다
10. THE WeatherWear_System SHALL README에 데모 데이터 준비 명령(사진 복사, 의류 태그 가져오기, Weather_Recording_Script, Scenario_Checker)을 실행 순서대로 기재하고, Weather_Recording_Script만 네트워크를 쓴다는 점과 GEMINI_MODEL 기본값의 Agent_Platform_Express_Mode 지원 여부와 대체 모델 ID, 확인 날짜, 출처 URL을 기재한다
