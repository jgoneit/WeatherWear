# Requirements Document

## Introduction

WeatherWear Phase 2(자동화)는 Phase 1 MVP(`.kiro/specs/weatherwear/`)에서 사람이 직접 하던 두 가지 일을 없앤다. Phase 1에서는 데모 관리 화면의 버튼을 눌러 사진 폴더를 스캔했고, 추천은 사용자가 앱을 열어야 보이는 웹 내부 알림 카드였다. Phase 2는 다음 두 가지를 자동으로 한다.

1. 휴대폰 갤러리 역할을 하는 폴더에 새 사진 파일이 생기면 버튼 없이 감지해 분석하고, 착장 사진이면 착장 기록에 넣는다.
2. 하루 한 번 정해진 현지 시각에 사용자 위치의 현재 기온·습도와, 그 기온에서 입었던 과거 착장과 체감 피드백을 알림으로 보낸다.

제품 목표는 **사람이 귀찮은 일을 최대한 하지 않게 하는 것**이다. 처음 한 번 알림과 위치 권한을 허용하고 설정 두 개를 켜면, 그다음부터 사용자가 하는 일은 원할 때 체감 피드백 버튼을 누르는 것뿐이다.

Phase 2는 Phase 1의 규칙을 다시 쓴다. 수집 파이프라인, 메타데이터 출처 우선순위, 사진 분류 게이트, rule_based_v1 추천, fixture 기본값, 비밀값 비노출, 오프라인 테스트가 여기에 해당한다. 이 문서는 Phase 1 요구사항을 고치지 않는다. Phase 1 요구사항을 확장하는 기준에는 해당 Phase 1 번호(예: Phase 1 Req 10.8)를 적는다.

### 전제: Phase 1과 main 병합 결과

Phase 2는 Phase 1 브랜치(`phase2-automation`의 기준 커밋 `4a94897`)를 `origin/main`(`a63e37c`, 팀원 PR #6)과 병합한 결과 위에서 구현한다. 병합 작업은 이 문서의 범위가 아니다. 다만 Phase 2는 PR #6이 넣은 다음 동작을 전제로 한다.

- Vision 결과는 `VisionAnalysis`(`photo_type`: `outfit` | `non_outfit` | `uncertain`, `photo_type_confidence`, `reason`, `tags`)다. `tags`의 세 의류 타입은 닫힌 snake_case 목록(`UpperType`, `OuterType`, `LowerType`)이다.
- 수집은 이미지 검증 → Vision 1회 → (`outfit`일 때만) 메타데이터 → 날씨 → 저장 순서다. `non_outfit`은 `skipped_non_outfit`, `uncertain`이거나 판정 신뢰도가 0.70 미만이면 `uncertain`으로 집계한다. 둘 다 Outfit을 저장하지 않고, 메타데이터와 날씨도 요구하거나 조회하지 않는다. 판정은 `photo_analyses` 테이블에 해시별로 남는다.
- `ScanReport`에는 `skipped_non_outfit`, `uncertain`, `decisions`가 있다.
- Fixture_Vision_Provider는 fixture에 없는 해시에 `uncertain`(신뢰도 0.0)을 준다. 그래서 fixture 모드에서는 별도 조치가 없으면 감시로 들어온 새 사진이 저장되지 않는다.

### 사용자 결정 (2026-11-14)

1. **갤러리 대체**: `ex_img/` 폴더를 사용자의 휴대폰 갤러리로 본다. 새 사진 파일이 생기면(방금 찍었다고 가정) 버튼 없이 자동으로 감지해 분석한다. 이미 있던 사진은 다시 분석하지 않는다(Phase 1처럼 SHA-256으로 중복 제거).
2. **매일 배치**: 하루 한 번 설정한 현지 시각에 다음을 한다.
   - 사용자 현재 위치의 현재 기온과 습도를 가져온다.
   - 비슷한 기온에서 입었던 착장을 찾는다(rule_based_v1 / RecommendationService 재사용).
   - 이런 알림을 보낸다: "오늘 서울 14.5°C, 습도 55%. 비슷했던 14.4°C 날 크림 니트를 입고 '적당했어'라고 했어요"
3. **푸시 채널**: FCM이 아니라 표준 Web Push(VAPID)다.
   - VAPID 키 쌍은 첫 실행 때 자동으로 만들어 로컬에 저장한다(git 무시). Firebase 계정은 필요 없다.
   - 기본 notifier는 오프라인 `log`다. 알림 기록에도 남겨 웹 내부 알림 카드가 보여 줄 수 있게 한다. 그래서 테스트와 데모에 네트워크가 필요 없다.
   - `webpush`는 설정으로 켠다.
4. **현재 위치**: 웹 클라이언트가 브라우저 위치(권한 동의 후)를 API에 보낸다. 배치는 가장 최근에 받은 위치를 쓰고, 없으면 `.env`의 `DEFAULT_LATITUDE` / `DEFAULT_LONGITUDE`(서울, 이미 있는 변수)를 쓴다.
5. **범위 밖**: FCM, 네이티브 앱, 실제 휴대폰 갤러리 접근, 로그인과 다중 사용자.

#### 열린 질문에 대한 사용자 답 (2026-11-14, 아래 가정보다 우선)

- **기본 동작(A1)**: 코드 기본값은 꺼짐(테스트 안전)이다. `.env.example`에서는 `GALLERY_WATCH_ENABLED=true`, `DAILY_BRIEF_ENABLED=true`로 켠다.
- **EXIF 없는 사진의 촬영 시각(A3)**: 파일 수정 시각(mtime)을 APP_TIMEZONE 기준으로 쓴다. 처음 감지한 시각은 쓰지 않는다.
- **uncertain 사진(A5)**: 알림 화면 "확인이 필요한 사진"에 "착장 맞음" 확정 버튼을 둔다. 한 번 누르면 outfit으로 저장한다(날씨·메타데이터 연결, 감사 기록). "다시 분석" 버튼도 유지한다.
- **홈 화면 오늘 날씨(A8)**: Live 모드(`WEATHER_PROVIDER=open_meteo`)에서만 마지막 위치 + Forecast API 현재 날씨로 바꾼다. fixture 모드는 Phase 1 동작 그대로다.
- 나머지 가정(A6 흉내 스크립트 원본 순서, A7 07:30과 따라잡기 3시간)은 그대로 확정한다. 휴대폰 HTTPS는 범위 밖이며 데스크톱 브라우저로 데모한다.
- 이 답은 아직 design.md와 tasks.md에 반영하지 않았다. 구현 전에 두 문서를 이 답에 맞춰 갱신한다.

### 결정을 구체화하며 둔 가정

다음은 사용자 결정만으로 정해지지 않아 이 문서가 정한 값이다. 사용자 확인 전까지 가정으로 둔다.

- (가정 A1) 감시와 매일 브리핑은 기본값이 꺼짐(`false`)이다. 테스트와 기존 Phase 1 실행이 사용자 폴더를 읽지 않게 하기 위해서다. 데모와 실사용에서는 `.env`에서 두 값을 한 번 켠다.
- (가정 A2) 감시를 처음 시작할 때 이미 `ex_img/`에 있던 파일(현재 데모 사진 174장과 `clothing_tags.json`)은 기준선으로 기록하고 분석하지 않는다. SHA-256 중복 제거는 그 뒤에 생긴 파일에도 계속 적용한다.
- (가정 A3) 내장 EXIF가 없는 새 사진은 실패시키지 않는다. 촬영 시각은 감시가 그 파일을 처음 본 시각("방금 찍었다"), 위치는 마지막으로 받은 위치(없으면 기본 위치)로 추정하고, 추정 값이라는 표시를 남긴다.
- (가정 A4) fixture 모드 데모에서 새 사진이 저장되도록, "방금 찍은 사진" 흉내 스크립트가 새 사진의 SHA-256으로 fixture 판정(`outfit`)을 사용자 fixture overlay 파일에 먼저 기록한다. 데모 fixture(`data/demo/fixtures/vision.json`)는 바꾸지 않는다.
- (가정 A5) 착장이 아닌 사진(`non_outfit`)은 알리지 않고 처리 기록에만 남긴다. 판정이 불확실한 사진(`uncertain`)도 푸시하지 않고, 앱 안의 알림 화면에 "확인이 필요한 사진"으로 보여 주며 "다시 분석" 버튼을 둔다.
- (가정 A6) 데모에 쓰지 않은 원본 이미지가 `ex_img/`에 없다(2026-11-14 확인: 이미지 174장과 `clothing_tags.json`뿐). 흉내 스크립트는 `--source`, 사용자가 채우는 `data/user/capture_pool/`, Pillow로 그린 플레이스홀더 순서로 원본을 고른다.
- (가정 A7) 매일 브리핑 시각은 `07:30`(APP_TIMEZONE)이다. 서버가 그 뒤 3시간 안에 켜지면 그때 보내고, 3시간이 지나면 그날은 건너뛴다.
- (가정 A8) 홈 화면의 "오늘 날씨"(Phase 1 GET /api/weather/today)는 바꾸지 않는다. 마지막 위치와 Forecast API 현재 날씨는 매일 브리핑에만 쓴다.

### Phase 1 불변식 (Phase 2가 깨지 않는 것)

- `data/demo/photos`의 사진은 `data/demo/photos.manifest.json`의 `rights_confirmed: true` 174장과 같다(`test_demo_data_layout`, `test_image_check`). Phase 2는 이 폴더, manifest, `data/demo/fixtures/*`를 쓰거나 지우지 않는다.
- `VISION_PROVIDER=fixture`, `WEATHER_PROVIDER=fixture`가 기본값이고, API 키와 네트워크 없이 기동·테스트가 된다.
- Phase 1의 기존 API 경로, 응답 스키마, 홈 화면 Notification_Card 동작은 바뀌지 않는다. 새 경로와 새 스키마만 더한다.
- Phase 1 테스트 중 고쳐야 하는 것은 목록이 정확히 정해진 다음 테스트뿐이다: 환경 변수 목록(`test_config.py`), 스키마 버전(`test_db.py`), 라우터 목록(`test_api_routes.py`), OpenAPI 경로 목록(`test_api.py`), 하단 탭 목록(`frontend/src/components/layout/AppShell.test.tsx`). 나머지 Phase 1 테스트는 그대로 통과해야 한다.

### 범위 제외

- FCM, 네이티브 앱, 실제 휴대폰 갤러리 접근, 로그인과 다중 사용자(사용자 결정 5)
- 휴대폰에서 LAN 주소로 접속할 때 필요한 HTTPS(터널, 인증서). Web Push와 위치는 `http://localhost` 같은 secure context에서만 확인한다
- iOS 홈 화면 설치용 아이콘 세트와 앱 스토어 배포
- 새 사진 수집 결과를 푸시로 알리는 기능, 사용자가 `uncertain` 사진을 직접 착장으로 확정하는 기능
- 추천 알고리즘 변경(rule_based_v1 그대로), 홈 화면 오늘 날씨 출처 변경(가정 A8)
- 알림 시각을 앱 화면에서 바꾸는 기능(환경 변수로만 바꾼다)

## Glossary

- **WeatherWear_System**, **API_Server**, **Mobile_Web_Client**, **Ingestion_Service**, **Metadata_Reader**, **Vision_Provider**, **Gemini_Vision_Provider**, **Fixture_Vision_Provider**, **Weather_Provider**, **OpenMeteo_Weather_Provider**, **Open_Meteo_Archive_API**, **Fixture_Weather_Provider**, **Outfit_Repository**, **Rule_Based_V1**, **Feedback_Service**, **Notification_Card**, **Demo_Admin_Panel**, **Config_Loader**, **Outfit**, **Weather_Snapshot**, **Thermal_Vote**, **Effective_Vote**, **Sidecar_Metadata**, **Embedded_Metadata**, **Metadata_Source**, **Synthetic_Metadata**, **Weather_Scenario**, **WeatherContext**, **Temperature_Tolerance**, **Fallback_Tolerance**, **Fixture_Mode**, **Live_Mode**, **Photo_Dir**, **Agent_Platform_Express_Mode**: Phase 1 requirements의 Glossary와 같은 뜻이다
- **VisionAnalysis**: main 병합 후 Vision_Provider가 사진 1장에 돌려주는 결과. `photo_type`(`outfit`, `non_outfit`, `uncertain`), `photo_type_confidence`(0.0~1.0), `reason`(1~200자), `tags`(ClothingTags, 세 의류 타입은 닫힌 snake_case 목록)를 가진다
- **Photo_Gate**: main 병합 후 Ingestion_Service의 판정 규칙. `outfit`이고 판정 신뢰도가 0.70 이상이며 태그가 비어 있지 않은 사진만 Outfit으로 저장하고, `non_outfit`은 skipped_non_outfit, 나머지는 uncertain으로 집계한다. 판정은 `photo_analyses`에 해시별로 남는다
- **RecommendationService**: Phase 1의 추천 서비스. 오늘 WeatherContext와 저장된 Outfit으로 Rule_Based_V1 결과를 만든다
- **Gallery_Dir**: 사용자 휴대폰 갤러리를 대신하는 감시 폴더. 환경 변수 GALLERY_DIR, 기본값 `./ex_img`
- **Gallery_Watcher**: Gallery_Dir를 일정 간격으로 확인해 새 사진 파일을 찾는 구성 요소. API_Server 안의 백그라운드 스레드 또는 CLI `scripts/watch_gallery.py`로 실행한다
- **Gallery_File**: Gallery_Dir 최상위의 후보 이미지 파일 1개. 이름과 File_Signature가 같으면 같은 버전이다
- **File_Signature**: Gallery_File의 (크기 바이트, 수정 시각 나노초) 쌍
- **Stable_File**: File_Signature가 0바이트보다 크고, 연속 두 번 이상의 확인에서 같으며, 처음 관찰한 지 GALLERY_POLL_SECONDS 이상 지난 Gallery_File. 다른 프로그램이 쓰는 중이 아니라고 보는 파일이다
- **Baseline_File**: Gallery_Watcher가 어떤 Gallery_Dir를 처음 시작할 때 이미 있던 Gallery_File. 분석하지 않는다
- **Gallery_Import_Service**: Stable_File 1건을 중복 확인, 복사, Phase 1 수집 절차(Photo_Gate 포함), 처리 기록 저장 순서로 처리하는 구성 요소
- **User_Photo_Dir**: 감시로 수집한 사진의 복사본을 두는 폴더. 환경 변수 USER_PHOTO_DIR, 기본값 `./data/user/photos`, git 무시
- **Gallery_Status**: Gallery_File 버전의 처리 상태. `baseline`, `pending`, `processing`, `imported`, `duplicate`, `skipped_non_outfit`, `uncertain`, `retry_wait`, `failed` 중 하나
- **Estimated_Capture**: 내장 메타데이터가 없거나 유효하지 않은 새 사진에 대해 Gallery_Import_Service가 정한 추정 촬영 시각과 위치
- **Vision_Fixture_Overlay**: fixture 모드에서 Fixture_Vision_Provider가 데모 fixture보다 먼저 찾는 사용자 fixture 파일 `data/user/fixtures/vision.overlay.json`(`{sha256: VisionAnalysis}`, git 무시)
- **Capture_Simulator**: "방금 사진을 찍었다"를 흉내 내는 `scripts/simulate_capture.py`. 오프라인으로 돈다
- **Capture_Pool_Dir**: Capture_Simulator가 원본 이미지를 고르는 폴더 `data/user/capture_pool/`(git 무시)
- **Open_Meteo_Forecast_API**: `https://api.open-meteo.com/v1/forecast`. 현재 날씨(`current`)와 최근 며칠의 시간 단위 값(`past_days` + `hourly`)을 준다
- **Recent_Weather_Window**: 촬영 날짜(APP_TIMEZONE)가 오늘 기준 7일 전부터 1일 뒤까지인 범위. 이 범위의 사진은 Open_Meteo_Forecast_API로 날씨를 찾는다
- **Recent_Weather_Provider**: 감시로 수집한 사진의 촬영 시점 날씨를 Recent_Weather_Window에 따라 Open_Meteo_Forecast_API 또는 OpenMeteo_Weather_Provider로 조회하는 Weather_Provider 구현체
- **Last_Location**: 웹 클라이언트가 마지막으로 보고한 위치 1건(소수 둘째 자리로 반올림한 위경도와 받은 시각)
- **Location_Service**: Last_Location을 저장하고, 매일 브리핑이 쓸 위치(Last_Location, 없으면 기본 위치)를 정하는 구성 요소
- **Place_Label**: 위치에서 30km 안의 가장 가까운 국내 주요 도시 이름(코드에 둔 목록). 없으면 "현재 위치"
- **Current_Weather_Service**: 매일 브리핑이 쓰는 오늘 현재 날씨를 만드는 구성 요소. 선택된 Weather_Scenario, Open_Meteo_Forecast_API 현재 날씨, fixture `today_default` 순서로 고른다
- **Daily_Brief**: 하루 한 번 보내는 착장 알림 1건
- **Daily_Brief_Service**: 위치, 현재 날씨, RecommendationService 결과로 Daily_Brief를 만들고 기록하고 전달하는 구성 요소
- **Brief_Composer**: Daily_Brief의 제목과 본문을 만드는 순수 함수 묶음(착장 문구, 한국어 태그 표기, 조사 선택, Place_Label 포함)
- **Daily_Brief_Scheduler**: API_Server 안에서 매일 DAILY_BRIEF_TIME에 Daily_Brief_Service의 예약 실행을 시작하는 백그라운드 스레드
- **Catch_Up_Window**: DAILY_BRIEF_TIME부터 3시간. 이 안에 서버가 켜져 있으면 그날 예약 실행을 한다
- **Notifier**: 알림을 채널로 전달하는 인터페이스. 구현체는 Log_Notifier와 WebPush_Notifier이다
- **Log_Notifier**: 네트워크 없이 전달 결과를 로그와 기록에만 남기는 기본 Notifier(`NOTIFIER=log`)
- **WebPush_Notifier**: 저장된 Push_Subscription마다 표준 Web Push(VAPID) 요청을 보내는 Notifier(`NOTIFIER=webpush`)
- **Push_Sender**: Web Push 요청 1건을 보내는 교체 가능한 구성 요소. 실제 구현은 `pywebpush`, 테스트는 가짜 sender다
- **Notification_Log**: 만든 알림과 채널별 전달 결과를 저장하는 기록(`notifications`, `notification_deliveries` 테이블)
- **VAPID_Key_Store**: VAPID 키 쌍(P-256)을 VAPID_KEY_DIR에 만들고 읽는 구성 요소
- **Push_Subscription**: 브라우저가 `pushManager.subscribe()`로 만든 구독(endpoint, p256dh, auth)
- **Push_Subscription_Store**: Push_Subscription을 저장·삭제하는 구성 요소
- **Service_Worker**: `frontend/public/sw.js`. push 이벤트를 받아 시스템 알림을 띄운다
- **Daily_Brief_Card**: 홈 화면에서 오늘 만든 Daily_Brief를 보여 주는 카드
- **Notifications_Page**: 하단 탭 "알림"의 화면(`/notifications`). 푸시·위치 설정, 최근 알림, 확인이 필요한 사진을 보여 준다
- **Automation_Status**: 감시, 매일 브리핑, Notifier, 위치의 현재 상태를 담은 API 응답
- **AutomationConfig**: Phase 2 환경 변수를 해석한 설정 묶음. Phase 1 AppConfig에 기본값이 있는 필드로 붙는다

## Requirements

### Requirement 1: 갤러리 폴더 자동 감시

**User Story:** 사용자로서, 사진을 찍기만 하면 앱이 알아서 가져가기를 원한다. 그래서 스캔 버튼을 누르지 않아도 된다.

#### Acceptance Criteria

1. WHERE GALLERY_WATCH_ENABLED가 true이면, WHEN API_Server가 기동되면, THE Gallery_Watcher SHALL FastAPI lifespan 시작 단계에서 백그라운드 스레드 1개로 시작해 GALLERY_POLL_SECONDS 간격으로 Gallery_Dir를 확인한다
2. WHEN API_Server가 종료되면, THE Gallery_Watcher SHALL 새 파일 처리를 시작하지 않고, 진행 중인 파일 1건의 처리가 끝나면 멈추며, lifespan 종료는 최대 35초까지 기다린다
3. WHERE GALLERY_WATCH_ENABLED가 false(기본값)이면, THE API_Server SHALL Gallery_Watcher를 시작하지 않고 Gallery_Dir를 읽지 않는다
4. WHEN 개발자가 `scripts/watch_gallery.py`를 실행하면, THE Gallery_Watcher SHALL API_Server와 같은 설정과 DB로 포그라운드에서 동작하고, `--once`가 주어지면 Stable_File 판정에 필요한 두 번의 확인과 처리할 파일의 처리를 마친 뒤 종료 코드 0으로 끝난다
5. IF Gallery_Dir가 없거나, 디렉터리가 아니거나, 읽을 수 없으면, THEN THE Gallery_Watcher SHALL 사용 가능 여부가 바뀔 때만 WARNING 로그 1건을 남기고(매 확인마다 반복하지 않음), Automation_Status에 서버 경로 없이 사용 불가 상태를 표시하며, 멈추지 않고 다음 확인에서 다시 시도한다
6. THE Gallery_Watcher SHALL Gallery_Dir의 최상위 항목만 확인하고, 하위 폴더와 심볼릭 링크를 따라가지 않으며, Gallery_Dir 안의 파일을 수정·이동·삭제하지 않는다
7. WHILE Gallery_Watcher가 API_Server와 `scripts/watch_gallery.py` 두 곳에서 함께 동작하는 동안, THE Gallery_Import_Service SHALL 같은 Gallery_File 버전을 한 번만 처리한다 (DB 조건부 갱신으로 처리권을 얻은 쪽만 처리)
8. IF 파일 1건을 처리하다 예상하지 못한 예외가 나면, THEN THE Gallery_Watcher SHALL 그 파일을 재시도 대상 실패로 기록하고, 스레드를 끝내지 않고 다음 확인을 계속한다

### Requirement 2: 새 사진 판별과 쓰기 중 파일 처리

**User Story:** 사용자로서, 사진을 옮기는 중이거나 임시 파일일 때 앱이 잘못 분석하지 않기를 원한다. 그래서 깨진 기록이 생기지 않는다.

#### Acceptance Criteria

1. THE Gallery_Watcher SHALL 확장자가 `.jpg`, `.jpeg`, `.png`, `.webp`(대소문자 무시, Phase 1 Req 1.1과 같은 목록)인 일반 파일만 Gallery_File 후보로 보고, 이름이 `.`으로 시작하는 파일(`.DS_Store`, `._*` 포함), `~`로 끝나는 파일, `.tmp`·`.part`·`.crdownload`·`.download`·`.partial` 확장자 파일, Sidecar_Metadata(`*.metadata.json`), 그 밖의 확장자 파일(`clothing_tags.json` 포함)은 무시한다
2. THE Gallery_Watcher SHALL Stable_File로 판정하기 전에는 파일 내용을 읽지 않는다
3. WHEN Gallery_File의 File_Signature가 바뀌면, THE Gallery_Watcher SHALL 그 파일을 새 버전으로 보고 안정 판정을 처음부터 다시 하며, 새 버전은 이전 버전의 시도 횟수와 상태를 이어받지 않는다
4. WHEN Gallery_Watcher가 어떤 Gallery_Dir에 대해 처음 시작되면, THE Gallery_Watcher SHALL 그때 있던 후보 파일을 모두 Baseline_File로 DB에 기록하고 분석하지 않는다(현재 `ex_img/`의 데모 사진 174장 포함). 이 기록은 재시작 후에도 남는다
5. WHEN Gallery_Watcher가 재시작되면, THE Gallery_Watcher SHALL 서버가 멈춰 있던 동안 생긴 후보 파일(Baseline_File도 처리 기록도 없는 파일)을 새 사진으로 처리한다
6. WHEN Stable_File을 처리하면, THE Gallery_Import_Service SHALL 파일 바이트 전체의 SHA-256을 계산하고, 같은 해시의 Outfit이 이미 있거나, 같은 해시의 다른 Gallery_File이 수집됐거나(`imported`), 수집 중(`pending`, `processing`, `retry_wait`)이거나, `uncertain`으로 판정됐으면(자동 재분석 방지, Requirement 6.7) Vision_Provider와 Weather_Provider를 호출하지 않고 `duplicate`로 기록한다 (Phase 1 Req 1.4와 같은 중복 제거)
7. WHEN 같은 해시에 `non_outfit` 판정이 `photo_analyses`에 이미 있으면, THE Gallery_Import_Service SHALL Vision_Provider를 다시 호출하지 않고 `skipped_non_outfit`으로 기록한다
8. THE Gallery_Watcher SHALL 한 번의 확인에서 Stable_File을 처음 관찰한 시각 순(같으면 파일 이름 순)으로 한 번에 1건씩 처리한다
9. FOR ALL 관찰 순서(파일 생성, 크기 증가, 서명 변경, 삭제, 이름 변경이 섞인 임의 순서)에 대해, Stable_File 판정 결과 SHALL 같은 관찰 순서에 Stable_File 정의와 기준 3을 그대로 적용한 참조 모델의 결과와 같다

### Requirement 3: 새 사진 수집 흐름과 사진 분류 게이트

**User Story:** 사용자로서, 갤러리에 있는 음식이나 풍경 사진은 빼고 착장 사진만 기록되기를 원한다. 그래서 기록과 추천이 깨끗하게 유지된다.

#### Acceptance Criteria

1. WHEN 중복이 아닌 Stable_File을 처리하면, THE Gallery_Import_Service SHALL 파일을 바이트 그대로 User_Photo_Dir에 복사하고(숨김 임시 파일에 쓴 뒤 이름 바꾸기), 복사본의 SHA-256이 원본과 같은지 확인한 뒤, Phase 1 Ingestion_Service의 파일 1건 처리 절차(디코딩 검증 → Vision 1회 → Photo_Gate → 메타데이터 → 날씨 → 단일 트랜잭션 저장)를 복사본에 적용한다
2. IF User_Photo_Dir에 같은 이름의 다른 내용 파일이 있으면, THEN THE Gallery_Import_Service SHALL 기존 파일을 덮어쓰지 않고 `<원래 이름>_<SHA-256 앞 8자><확장자>` 이름으로 복사한다
3. WHEN Photo_Gate가 `outfit`으로 통과시키면, THE Gallery_Import_Service SHALL Phase 1과 같은 outfits·weather_snapshots·photo_analyses 레코드를 만들고, `user_photos`에 SHA-256과 User_Photo_Dir 안의 파일 이름을 저장하며, Gallery_Status를 `imported`로, Outfit id를 처리 기록에 남긴다
4. WHEN Photo_Gate가 `non_outfit` 또는 `uncertain`으로 판정하면, THE Gallery_Import_Service SHALL Outfit을 저장하지 않고, 메타데이터와 날씨를 조회하지 않으며, User_Photo_Dir의 복사본과 그 사이드카와 `user_photos` 행을 지우고, Gallery_Status를 `skipped_non_outfit` 또는 `uncertain`으로 하며, 판정(photo_type, photo_type_confidence, reason)을 처리 기록에 남긴다
5. WHEN GET /api/outfits/{id}/photo 요청의 Outfit 해시가 `user_photos`에 있으면, THE API_Server SHALL User_Photo_Dir에서 그 파일을 제공하고, 없으면 Phase 1과 같이 Photo_Dir에서 제공한다. 어느 경우든 해당 폴더 밖의 경로를 열지 않고, 응답에 서버 경로를 넣지 않는다 (Phase 1 Req 10.8, 10.9 확장)
6. WHEN 감시로 수집한 Outfit이 저장되면, THE API_Server SHALL 그 Outfit을 GET /api/outfits, 착장 기록 화면, Rule_Based_V1 후보에 Phase 1 Outfit과 같은 규칙으로 포함한다
7. FOR ALL Stable_File과 Photo_Gate 판정(`outfit`, `non_outfit`, `uncertain`, Vision 실패)의 조합에 대해, 처리 후 User_Photo_Dir에 남는 파일 SHALL `imported` 상태 파일과 다음 시도를 기다리는 `retry_wait` 상태 파일의 복사본(과 그 사이드카)뿐이고, 남은 복사본의 바이트는 원본과 같다

### Requirement 4: Phase 1 데모 데이터와 권한 규칙 보존

**User Story:** 개발자로서, 자동 수집이 데모 데이터와 그 테스트를 깨지 않기를 원한다. 그래서 Phase 1 데모를 그대로 재현할 수 있다.

#### Acceptance Criteria

1. THE Gallery_Import_Service SHALL Photo_Dir(`data/demo/photos`), `data/demo/photos.manifest.json`, `data/demo/fixtures/*`, `data/demo/scenarios.json`에 파일을 쓰거나 지우지 않는다. 그래서 Phase 1 데모 데이터 불변식(사진 수 = manifest의 `rights_confirmed: true` 174장)과 그 테스트(`test_demo_data_layout`, `test_image_check`)가 그대로 통과한다
2. THE Config_Loader SHALL User_Photo_Dir가 Photo_Dir 또는 Gallery_Dir와 같거나, 그 안에 있거나, 그것을 포함하면 변수 이름만 담은 설정 오류를 기록하고 USER_PHOTO_DIR 기본값을 쓴다
3. THE WeatherWear_System SHALL 감시로 수집한 사진을 데모 데이터가 아닌 사용자 본인 사진으로 다루어, photos.manifest.json에 추가하지 않고 `rights_confirmed` 확인을 요구하지 않는다
4. THE WeatherWear_System SHALL Phase 1 tasks 19.16의 "이후 새로 생기는 `ex_img/` 파일은 `rights_confirmed: false`로 두고 사용자에게 확인한다" 규칙을 사진을 데모 데이터(manifest와 Photo_Dir)로 옮길 때에만 적용하고, 감시 수집에는 적용하지 않는다. Phase 2는 사진을 데모 데이터로 옮기는 동작을 하지 않는다
5. THE WeatherWear_System SHALL `.gitignore`에 `data/user/`를 넣어 User_Photo_Dir, 추정 사이드카, Vision_Fixture_Overlay, Capture_Pool_Dir를 저장소에서 제외한다
6. WHILE Phase 1 데모 스캔(POST /api/demo/scan)과 Gallery_Watcher가 함께 동작하는 동안, THE WeatherWear_System SHALL 데모 스캔의 집계와 결과를 Phase 1과 같게 유지하고, 같은 해시가 두 경로에서 동시에 처리돼도 Outfit을 1건만 만든다 (`outfits.sha256 UNIQUE`)

### Requirement 5: 새 사진의 촬영 시각·위치

**User Story:** 사용자로서, 위치 기록이 꺼진 사진도 기록되기를 원한다. 그래서 사진마다 설정을 확인하지 않아도 된다.

#### Acceptance Criteria

1. WHEN 감시로 들어온 사진에 유효한 Embedded_Metadata가 있으면, THE Gallery_Import_Service SHALL Phase 1 Metadata_Reader 결과(UserComment JSON → 표준 EXIF)를 그대로 쓰고, outfits.source를 `exif`로 저장한다
2. IF 감시로 들어온 사진에 유효한 Embedded_Metadata가 없으면, THEN THE Gallery_Import_Service SHALL 실패로 처리하지 않고 Estimated_Capture를 만든다. 촬영 시각은 EXIF DateTimeOriginal이 파싱되면 그 값(OffsetTimeOriginal이 없으면 그 지역 시각의 APP_TIMEZONE 오프셋), 아니면 Gallery_Watcher가 그 파일 버전을 처음 관찰한 시각(APP_TIMEZONE)이다. 위치는 EXIF GPS 위경도가 유효하면 그 값, 아니면 Last_Location, 그것도 없으면 DEFAULT_LATITUDE·DEFAULT_LONGITUDE이다
3. WHEN Estimated_Capture를 만들면, THE Gallery_Import_Service SHALL 그 값을 User_Photo_Dir의 복사본 옆 Sidecar_Metadata(`captured_at`, `latitude`, `longitude`와, Phase 1 파서가 무시하는 추가 필드 `estimated: true`, `time_source`, `location_source`)로 쓰고, Phase 1 사이드카 대체 경로(Phase 1 Req 2.12의 3순위)로 읽게 하여 outfits.source=`demo_sidecar`, metadata_synthetic=true로 저장되게 한다
4. THE Gallery_Import_Service SHALL Gallery_Dir에 사이드카를 쓰지 않고, Gallery_Dir에 있는 Sidecar_Metadata를 읽거나 복사하지 않는다
5. THE API_Server SHALL 사진 처리 기록(GET /api/gallery/imports)에 촬영 정보 출처를 `exif` 또는 `estimated`로 표시하고, THE Mobile_Web_Client SHALL `estimated` 항목에 "촬영 정보 추정" 라벨을 표시한다
6. FOR ALL (내장 메타데이터 유효/없음/무효, DateTimeOriginal 있음/없음, OffsetTimeOriginal 있음/없음, GPS 유효/없음, Last_Location 있음/없음) 조합에 대해, Estimated_Capture 결과 SHALL 기준 1·2의 우선순위를 그대로 옮긴 참조 함수와 같고, captured_at은 항상 타임존 오프셋을 가지며, 위경도는 항상 범위 안이다
7. FOR ALL Estimated_Capture 값에 대해, 기준 3의 사이드카를 Phase 1 `parse_sidecar_text`로 다시 읽은 결과 SHALL 같은 지역 시각·오프셋·위경도이고, 추가 필드 때문에 실패하지 않는다

### Requirement 6: 새 사진의 Vision 분석, 재시도, 건너뛴 사진 알림

**User Story:** 사용자로서, 분석이 일시적으로 실패해도 다시 시도되고, 착장이 아닌 사진 때문에 알림이 쏟아지지 않기를 원한다. 그래서 신경 쓸 일이 줄어든다.

#### Acceptance Criteria

1. WHEN 새 사진을 분석하면, THE Gallery_Import_Service SHALL API_Server에 설정된 Vision_Provider를 시도 1회마다 정확히 1회 호출한다
2. WHERE VISION_PROVIDER가 `gemini`이면, THE Gallery_Import_Service SHALL Phase 1 Gemini_Vision_Provider(Agent_Platform_Express_Mode, 메타데이터 제거 후 전송, 30초 제한, SDK 자동 재시도 없음)와 Photo_Gate로 판정한다
3. WHERE VISION_PROVIDER가 `fixture`이면, THE Fixture_Vision_Provider SHALL 사진 해시를 Vision_Fixture_Overlay에서 먼저 찾고, 없으면 데모 fixture(`data/demo/fixtures/vision.json`)에서 찾으며, 둘 다 없으면 Phase 1과 같은 `uncertain`(신뢰도 0.0)을 돌려준다. 외부 네트워크는 호출하지 않는다
4. WHEN Vision_Fixture_Overlay 파일의 크기나 수정 시각이 바뀌면, THE Fixture_Vision_Provider SHALL 서버를 재시작하지 않아도 다음 분석에서 바뀐 내용을 쓰고, 파일이 없거나 깨졌으면 overlay 없이 데모 fixture만 쓰며 경고 로그 1건을 남긴다
5. IF 시도가 VISION_API_ERROR, VISION_TIMEOUT, VISION_SCHEMA_INVALID, STORAGE_ERROR, WEATHER_ERROR, FILE_READ_ERROR, GALLERY_COPY_ERROR 중 하나로 실패하면, THEN THE Gallery_Import_Service SHALL 실패 단계와 코드를 기록하고, 같은 파일 버전을 1분 뒤, 그다음 5분 뒤에 다시 시도하며, 세 번째 시도까지 실패하면 Gallery_Status를 `failed`로 두고 더 시도하지 않는다
6. IF 시도가 FILE_TOO_LARGE 또는 INVALID_IMAGE_FORMAT으로 실패하면, THEN THE Gallery_Import_Service SHALL 재시도하지 않고 `failed`로 기록하며, 파일 서명이 바뀌면 Requirement 2.3에 따라 새 버전으로 처리한다
7. THE Gallery_Import_Service SHALL `uncertain`과 `skipped_non_outfit` 판정을 그 파일 버전의 최종 결과로 두고 자동으로 다시 분석하지 않는다
8. WHEN POST /api/gallery/imports/{id}/reanalyze 요청을 받으면, THE API_Server SHALL `uncertain` 또는 `failed` 상태의 기록만 `pending`(시도 횟수 0)으로 되돌려 다음 확인에서 다시 처리하게 하고 202를 반환하며, 그 밖의 상태는 409 `GALLERY_IMPORT_NOT_REANALYZABLE`, 없는 id는 404 `GALLERY_IMPORT_NOT_FOUND`를 반환한다
9. THE WeatherWear_System SHALL `skipped_non_outfit`과 `uncertain` 사진에 대해 푸시 알림을 보내지 않고, 매일 브리핑 본문에도 넣지 않는다. `uncertain` 사진은 Notifications_Page의 "확인이 필요한 사진" 목록에, `skipped_non_outfit` 사진은 사진 처리 기록에만 보여 준다
10. WHEN 시도가 실패하면, THE Gallery_Import_Service SHALL 그 시도의 Outfit, Weather_Snapshot, feedback 레코드를 남기지 않고(Phase 1 Req 1.6), 로그와 기록에는 파일 이름, 단계, 코드만 남기며 서버 경로와 GEMINI_API_KEY를 남기지 않는다
11. FOR ALL 시도 결과 순서에 대해, 한 파일 버전의 Vision_Provider 호출 횟수 SHALL 3 이하이고, `imported`, `duplicate`, `skipped_non_outfit`, `uncertain`, `failed`가 된 뒤에는 (reanalyze 요청 전까지) 0회다

### Requirement 7: 새 사진의 촬영 시점 날씨

**User Story:** 사용자로서, 방금 찍은 사진에도 실제 날씨가 붙기를 원한다. 그래서 다음 추천이 정확해진다.

#### Acceptance Criteria

1. WHERE WEATHER_PROVIDER가 `open_meteo`이고 촬영 날짜가 Recent_Weather_Window 안이면, THE Recent_Weather_Provider SHALL Open_Meteo_Forecast_API에 latitude, longitude, hourly=`temperature_2m,precipitation,relative_humidity_2m`, timezone=APP_TIMEZONE, past_days=max(0, 오늘 − 촬영 날짜의 일수), forecast_days=2를 담아 요청 1건을 보내고, Phase 1 Req 4.2와 같은 가장 가까운 시각 규칙과 Phase 1 Req 4.12와 같은 변수 매핑으로 Weather_Snapshot(provider `open_meteo`)을 만든다
2. WHERE WEATHER_PROVIDER가 `open_meteo`이고 촬영 날짜가 Recent_Weather_Window 밖이면, THE Recent_Weather_Provider SHALL Phase 1 OpenMeteo_Weather_Provider(Open_Meteo_Archive_API)를 그대로 쓴다
3. IF Open_Meteo_Forecast_API 요청이 10초 안에 응답하지 않거나, 실패 응답·네트워크 오류·JSON 오류를 반환하거나, 선택 시각의 temperature_2m이 없으면, THEN THE Recent_Weather_Provider SHALL Phase 1 OpenMeteo_Weather_Provider로 다시 조회하고(Archive, 실패하면 fixture), 값을 실제로 준 출처를 provider로 기록한다
4. WHERE WEATHER_PROVIDER가 `fixture`이면, THE WeatherWear_System SHALL Phase 1 Fixture_Weather_Provider를 써서, 촬영 날짜가 오늘이면 `weather.json`의 `today_default`를, 아니면 결정적 계절 공식 값을 provider=`fixture`, is_fixture=true로 저장하고 외부 호출을 하지 않는다
5. THE Recent_Weather_Provider SHALL 감시 수집에만 쓰고, Phase 1 데모 스캔(POST /api/demo/scan)의 날씨 조회 동작을 바꾸지 않는다
6. FOR ALL 촬영 시각과 오늘 날짜에 대해, 엔드포인트 선택 SHALL 촬영 날짜가 [오늘 − 7일, 오늘 + 1일] 안이면 Forecast, 아니면 Archive이고, Forecast 요청의 past_days는 0 이상 7 이하이며 forecast_days는 2이다

### Requirement 8: "방금 찍은 사진" 흉내 스크립트

**User Story:** 데모 발표자로서, 휴대폰 없이도 "방금 사진을 찍었다"를 손대지 않고 재현하고 싶다. 그래서 자동 수집을 오프라인으로 시연할 수 있다.

#### Acceptance Criteria

1. WHEN 개발자가 `scripts/simulate_capture.py`를 실행하면, THE Capture_Simulator SHALL 원본 이미지 1장을 골라 촬영 시각과 위치를 Embedded_Metadata로 기록한 새 이미지 파일을 Gallery_Dir에 만들고, 만든 파일 이름만 출력한 뒤 종료 코드 0으로 끝난다
2. THE Capture_Simulator SHALL 원본 이미지를 `--source` 경로, 없으면 Capture_Pool_Dir의 이미지 중 이름순으로 마지막으로 쓴 것의 다음 이미지(끝이면 처음), 그것도 없으면 Pillow로 그린 플레이스홀더 이미지(크림색 니트와 청바지 그림)에서 고른다
3. IF 원본 이미지의 파일 이름이 `data/demo/photos.manifest.json`에 있거나, 원본의 SHA-256이 `data/demo/fixtures/vision.json`의 키나 `vision.sources.json`의 `source_sha256`과 같으면, THEN THE Capture_Simulator SHALL 파일을 만들지 않고 데모 사진은 쓸 수 없다는 메시지와 종료 코드 2로 끝난다
4. THE Capture_Simulator SHALL 촬영 시각을 `--at`(ISO 8601, 오프셋 필수), 없으면 현재 시각(APP_TIMEZONE, 초 단위)으로, 위치를 `--lat`/`--lon`, 없으면 DB의 Last_Location, 없으면 DEFAULT_LATITUDE·DEFAULT_LONGITUDE로 정한다
5. THE Capture_Simulator SHALL UserComment JSON(date, time, timezone, utc_offset, latitude, longitude, `synthetic: true`, `source: "simulate_capture"`)을 기록하고, JPEG이면 표준 EXIF(DateTimeOriginal, OffsetTimeOriginal, GPS 도·분·초, GPSProcessingMethod `SYNTHETIC`)도 기록한다
6. WHERE VISION_PROVIDER가 `fixture`이고 `--no-fixture`가 없으면, THE Capture_Simulator SHALL 새 파일을 Gallery_Dir에 넣기 전에 그 파일의 SHA-256을 키로 VisionAnalysis(`photo_type: outfit`, `photo_type_confidence: 0.95`, `reason: "simulate_capture fixture"`, 원본의 태그)를 Vision_Fixture_Overlay에 원자적으로 추가한다. 원본의 태그는 `--tags`(JSON), Capture_Pool_Dir의 `clothing_tags.json`(`ex_img/clothing_tags.json`과 같은 형식, 파일 이름 키), 플레이스홀더의 고정 태그 순서로 정하고, 태그가 없으면 overlay를 쓰지 않으며 "fixture 모드에서는 이 사진이 uncertain으로 남는다"는 안내를 출력한다
7. THE Capture_Simulator SHALL 결과를 Gallery_Dir 안의 숨김 임시 파일(`.` 접두사)에 다 쓴 뒤 최종 이름(`capture_<YYYYMMDD_HHMMSS>.<ext>`)으로 바꿔, Gallery_Watcher가 쓰는 중인 파일을 보지 않게 하고, 같은 이름의 파일이 있으면 덮어쓰지 않고 `_2`, `_3` 접미사를 붙인다
8. THE Capture_Simulator SHALL 네트워크를 쓰지 않고, Gemini와 Open-Meteo를 호출하지 않으며, API_Server가 실행 중이 아니어도 동작한다
9. FOR ALL 유효한 촬영 시각(임의 오프셋, 초 단위)과 범위 안 위경도에 대해, Capture_Simulator가 기록한 이미지를 Phase 1 `read_embedded_metadata`로 읽은 결과 SHALL 같은 지역 시각, 같은 오프셋, 같은 위경도(표준 EXIF만 기록한 경우 0.00001도 이내), synthetic=true이다

### Requirement 9: 현재 위치 보고

**User Story:** 사용자로서, 내가 있는 곳의 날씨로 알림을 받고 싶다. 그래서 다른 도시에 있어도 맞는 착장을 고를 수 있다.

#### Acceptance Criteria

1. WHEN POST /api/location 요청을 {"latitude", "longitude", "accuracy_m"(선택)} 본문으로 받으면, THE Location_Service SHALL 위도가 −90~90, 경도가 −180~180의 유한한 숫자인지 확인하고, 소수 둘째 자리로 반올림한 값과 받은 시각을 Last_Location 1건으로 저장(이전 값 대체)한 뒤 200과 저장된 값, Place_Label을 반환한다
2. IF 위도나 경도가 없거나, 숫자가 아니거나(불리언 포함), 범위를 벗어나거나, accuracy_m이 음수이면, THEN THE API_Server SHALL 422 `INVALID_LOCATION`을 반환하고 Last_Location을 바꾸지 않는다
3. WHEN GET /api/location 요청을 받으면, THE API_Server SHALL Last_Location(없으면 null), 기본 위치(DEFAULT_LATITUDE·DEFAULT_LONGITUDE), 매일 브리핑이 쓸 위치와 그 출처(`browser` 또는 `default`), Place_Label을 반환한다
4. THE Location_Service SHALL 위치 이력을 쌓지 않고 마지막 1건만 보관하며, 로그에 위경도를 남기지 않는다
5. WHEN Daily_Brief_Service가 위치를 정하면, THE Location_Service SHALL Last_Location이 있으면 받은 시각과 관계없이 그 값을, 없으면 DEFAULT_LATITUDE·DEFAULT_LONGITUDE를 돌려준다
6. FOR ALL 유효·무효 위치 보고가 섞인 순서에 대해, Last_Location SHALL 마지막 유효 보고 값을 소수 둘째 자리로 반올림한 값이고, 무효 보고는 상태를 바꾸지 않는다

### Requirement 10: 오늘 현재 날씨

**User Story:** 사용자로서, 아침 알림에 지금 바깥 날씨가 담기기를 원한다. 그래서 앱을 열지 않고도 옷을 고를 수 있다.

#### Acceptance Criteria

1. WHERE WEATHER_PROVIDER가 `open_meteo`이면, THE Current_Weather_Service SHALL Open_Meteo_Forecast_API에 latitude, longitude, current=`temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,wind_speed_10m,weather_code`, timezone=APP_TIMEZONE을 담아 요청 1건을 보내고, current.temperature_2m을 actual_temperature_c로, relative_humidity_2m을 relative_humidity_pct로, apparent_temperature를 apparent_temperature_c로, precipitation을 precipitation_mm으로, wind_speed_10m을 wind_speed_kmh로, current.time(APP_TIMEZONE 지역 시각)을 observed_at으로 옮긴 WeatherContext(provider `open_meteo`, is_fixture false)를 만든다
2. IF Open_Meteo_Forecast_API 요청이 10초 안에 응답하지 않거나, 실패하거나, current.temperature_2m이 없으면, THEN THE Current_Weather_Service SHALL `weather.json`의 `today_default`로 WeatherContext(provider `fixture`, is_fixture true)를 만들고 WARNING 로그를 남긴다
3. WHERE WEATHER_PROVIDER가 `fixture`이면, THE Current_Weather_Service SHALL 외부 호출 없이 `today_default`(현재 14.5°C, 습도 55%)로 WeatherContext를 만든다
4. WHILE Demo_Admin_Panel에서 Weather_Scenario가 선택되어 있는 동안, THE Current_Weather_Service SHALL Phase 1 Req 5.3과 같이 선택된 Weather_Scenario 값을 오늘 날씨(provider `scenario`)로 쓴다
5. THE Current_Weather_Service SHALL Phase 1의 GET /api/weather/today와 GET /api/recommendations/today 동작을 바꾸지 않는다 (가정 A8)
6. FOR ALL Forecast current 응답(변수 일부가 null이거나 없을 수 있음)에 대해, 변환 결과 SHALL temperature_2m이 있으면 각 변수가 기준 1의 필드로 Phase 1 단위 정규화 규칙에 따라 옮겨지고 없는 변수는 null이며, temperature_2m이 없으면 기준 2의 fixture 결과와 같다

### Requirement 11: 매일 착장 브리핑 내용

**User Story:** 사용자로서, 아침마다 "오늘은 그때 그 옷"이라는 한 줄을 받고 싶다. 그래서 옷장 앞에서 고민하는 시간이 줄어든다.

#### Acceptance Criteria

1. WHEN Daily_Brief를 만들면, THE Daily_Brief_Service SHALL Location_Service의 위치로 Current_Weather_Service의 오늘 날씨를 얻고, RecommendationService와 Rule_Based_V1(Temperature_Tolerance, Fallback_Tolerance, Photo_Gate로 저장된 Outfit만, 피드백 가중 포함)을 그 날씨에 그대로 적용해 1순위 추천을 고른다
2. WHEN 1순위 추천이 있고 Effective_Vote가 있으면, THE Brief_Composer SHALL 본문을 "오늘 {Place_Label} {오늘 기온}°C, 습도 {습도}%. 비슷했던 {과거 기온}°C 날 {착장 문구}{을/를} 입고 '{체감}'라고 했어요." 형식으로 만든다. 기온은 소수 첫째 자리, 습도는 정수, 체감은 Effective_Vote에 대응하는 "추웠어", "적당했어", "더웠어"이다 (예: "오늘 서울 14.5°C, 습도 55%. 비슷했던 14.4°C 날 크림 니트를 입고 '적당했어'라고 했어요.")
3. WHEN Effective_Vote가 `cold`이면, THE Brief_Composer SHALL 본문 끝에 " 오늘은 한 겹 더 챙겨 보세요."를, `hot`이면 " 오늘은 조금 가볍게 입어 보세요."를 붙인다
4. WHEN 1순위 추천에 Effective_Vote가 없으면, THE Brief_Composer SHALL 두 번째 문장을 "비슷했던 {과거 기온}°C 날 {착장 문구}{을/를} 입었어요. 그날 어땠는지 알려 주세요."로 만든다
5. WHEN 1순위 추천이 Fallback_Tolerance로 선택됐으면, THE Brief_Composer SHALL "비슷했던" 대신 "기온이 가장 가까웠던"을 쓴다
6. THE Brief_Composer SHALL 착장 문구를 1순위 Outfit의 태그로 만든다. upper_type이 `unknown`이 아니면 "{첫 색상 표기} {upper_type 표기}"(색상이 없으면 upper_type 표기만), upper_type이 `unknown`이면 outer_type 또는 lower_type 중 처음 나오는 알려진 값을 쓰고, upper_type과 outer_type이 모두 알려진 값(`none`, `unknown` 제외)이면 "{앞 문구}에 {outer_type 표기}"로 만든다. 알려진 값이 하나도 없으면 "그날 착장"을 쓴다
7. THE Brief_Composer SHALL UpperType, OuterType, LowerType의 모든 canonical 값(`none`, `unknown` 제외)에 한국어 표기를 두고(예: `knit`→"니트", `long_puffer`→"롱패딩", `jeans`→"청바지"), 색상은 데모 fixture에 나오는 색상 이름에 한국어 표기를 두며(예: `cream`→"크림"), 표기가 없는 색상은 원문을 쓴다
8. THE Brief_Composer SHALL 조사 을/를을 착장 문구 마지막 글자의 받침으로 고르고(받침 있음 "을", 없음 "를"), 마지막 글자가 한글 음절이 아니면 "을(를)"을 쓴다
9. IF 추천 결과가 "개인 착장 데이터 부족" 상태(0건)이면, THEN THE Brief_Composer SHALL 본문을 "오늘 {Place_Label} {기온}°C, 습도 {습도}%. 비슷한 기온에서 입었던 착장 기록이 아직 없어요. 오늘 입은 옷을 찍어 두면 다음에 알려 드릴게요."로 만들고, THE Daily_Brief_Service SHALL 이 알림을 오류로 처리하지 않고 기록·전달한다
10. IF 오늘 날씨에 습도가 없으면, THEN THE Brief_Composer SHALL ", 습도 {습도}%" 부분만 빼고 나머지 문장을 만든다
11. THE Brief_Composer SHALL Place_Label을 위치에서 30km 안에 있는 가장 가까운 알려진 도시 이름으로 정하고(기본 위치 37.5665, 126.9780은 "서울"), 30km 안에 없으면 "현재 위치"를 쓰며, 외부 지오코딩 서비스를 호출하지 않는다
12. THE Brief_Composer SHALL 제목을 "오늘의 착장 브리핑"으로 하고, 오늘 날씨가 fixture이면 " (fixture 날씨)", Weather_Scenario이면 " (시나리오 날씨)"를 제목 끝에 붙인다. 본문에는 이 표시를 넣지 않는다
13. THE Daily_Brief_Service SHALL 알림 링크를 1순위 Outfit이 있으면 `/outfits/{id}`, 없으면 `/`로 정한다
14. FOR ALL 1순위 추천(투표 유무와 종류, fallback 여부, 태그 조합)과 기온·습도 값(습도 없음 포함)에 대해, 본문 SHALL 기준 2~10의 규칙대로 오늘 기온과 과거 기온을 소수 첫째 자리 °C 문자열로 담고, 체감 표기가 Effective_Vote와 대응하며, 200자 이하이다
15. FOR ALL 한글 음절로 끝나는 문자열에 대해, 조사 선택 SHALL 마지막 음절의 받침 유무(`(코드 − 0xAC00) mod 28 ≠ 0`)와 일치한다

### Requirement 12: 매일 배치 스케줄과 멱등성

**User Story:** 사용자로서, 매일 아침 알림을 한 번만 받고 싶다. 그래서 중복 알림에 시달리지 않는다.

#### Acceptance Criteria

1. WHERE DAILY_BRIEF_ENABLED가 true이면, THE Daily_Brief_Scheduler SHALL FastAPI lifespan에서 백그라운드 스레드 1개로 시작해 30초마다 실행 시점인지 확인하고, 현지 시각(APP_TIMEZONE)이 그날 Catch_Up_Window([DAILY_BRIEF_TIME, DAILY_BRIEF_TIME + 3시간)) 안이며 그 날짜의 예약 실행 기록이 없으면 Daily_Brief_Service의 예약 실행을 1회 시작한다
2. WHERE DAILY_BRIEF_ENABLED가 false(기본값)이면, THE API_Server SHALL Daily_Brief_Scheduler를 시작하지 않는다
3. THE Daily_Brief_Service SHALL 예약 실행을 현지 날짜마다 최대 1회로 제한하고, 이 제한을 프로세스 메모리가 아닌 DB 고유 제약(daily_brief_runs의 날짜별 예약 실행 1건)으로 보장한다. 그래서 서버 재시작, 여러 프로세스, CLI 실행이 겹쳐도 같은 날 예약 알림은 1번만 나간다
4. IF API_Server가 그날 Catch_Up_Window가 끝난 뒤 처음 기동되면, THEN THE Daily_Brief_Scheduler SHALL 그날 예약 실행을 하지 않고 다음 날 DAILY_BRIEF_TIME을 기다린다
5. IF 예약 실행이 예외(DB 오류 등)로 실패하면, THEN THE Daily_Brief_Scheduler SHALL 실패를 기록하고 10분 뒤 같은 날짜로 다시 시도하며, 같은 날짜의 시도는 최대 3회이고 Catch_Up_Window를 넘기지 않는다. 날씨 조회 실패와 데이터 부족은 실패가 아니다 (Requirement 10.2, 11.9)
6. WHEN 개발자가 `scripts/daily_brief.py`를 실행하면, THE Daily_Brief_Service SHALL API_Server와 같은 설정과 DB로 그날 예약 실행 1회를 시도하고(이미 있으면 보내지 않고 "이미 보냄"을 출력한 뒤 종료 코드 0), `--force`이면 수동 실행으로 알림 1건을 만든다. 그래서 OS cron이나 launchd에서도 같은 멱등 규칙으로 쓸 수 있다
7. THE Daily_Brief_Scheduler SHALL 주입할 수 있는 시계로 시각을 판정하고, 현지 시각은 `zoneinfo`로 계산하며, 서머타임 때문에 같은 현지 시각이 두 번 오거나 오지 않는 날에도 그 날짜에 최대 1회만 실행한다
8. FOR ALL 시계 진행 순서(재시작, 뒤로 가는 시각, 날짜 경계 포함)에 대해, 예약 실행 횟수 SHALL 현지 날짜마다 1 이하이고, 각 실행 시작 시각은 그 날짜의 Catch_Up_Window 안이다

### Requirement 13: 지금 실행 API와 데모 버튼

**User Story:** 데모 발표자로서, 아침까지 기다리지 않고 알림을 바로 보내 보고 싶다. 그래서 5분 안에 흐름을 보여 줄 수 있다.

#### Acceptance Criteria

1. WHEN POST /api/daily-brief/run 요청을 받으면, THE Daily_Brief_Service SHALL 예약 실행 기록과 관계없이 수동 실행으로 Daily_Brief를 1건 만들고, 200과 만든 알림(id, 제목, 본문, 링크, 날씨 출처, 전달 결과 요약)을 반환한다. 수동 실행은 그날 예약 실행을 막지 않는다
2. WHILE 다른 Daily_Brief를 만드는 중인 동안, WHEN POST /api/daily-brief/run 요청을 받으면, THE API_Server SHALL 새로 만들지 않고 409 `DAILY_BRIEF_IN_PROGRESS`를 반환한다
3. THE API_Server SHALL POST /api/daily-brief/run을 DAILY_BRIEF_ENABLED 값과 관계없이 처리한다
4. THE Demo_Admin_Panel SHALL "오늘 브리핑 지금 보내기" 버튼을 두고, 처리 중에는 버튼을 비활성화하며, 성공하면 만든 알림의 제목과 본문을 표시하고, 실패하거나 30초 안에 응답이 없으면 Phase 1 Req 14.6과 같이 어느 동작이 실패했는지 표시하고 버튼을 다시 활성화한다

### Requirement 14: Notifier와 알림 기록

**User Story:** 개발자로서, 네트워크 없이도 알림 흐름 전체를 확인하고 싶다. 그래서 테스트와 데모가 외부 서비스에 의존하지 않는다.

#### Acceptance Criteria

1. THE Daily_Brief_Service SHALL 만든 알림을 먼저 Notification_Log에 1건 저장하고(종류, 제목, 본문, 링크, Outfit id, 날씨 출처와 fixture 여부, 생성 시각), 그다음 NOTIFIER로 정한 Notifier에 전달한다. 전달이 실패해도 알림 기록은 남는다
2. WHERE NOTIFIER가 `log`(기본값)이면, THE Log_Notifier SHALL 알림 id와 종류만 INFO 로그로 남기고, 채널 `log`의 전달 결과 1건을 `sent`로 기록하며, 네트워크를 쓰지 않는다
3. WHERE NOTIFIER가 `webpush`이면, THE WebPush_Notifier SHALL 저장된 Push_Subscription마다 Web Push 요청을 1건씩 보내고, 구독마다 전달 결과(`sent`, `failed`, `gone`과 HTTP 상태 코드)를 기록한다. 구독이 0건이면 webpush 전달 결과 없이 알림 기록만 남긴다
4. THE Notifier SHALL 하나의 인터페이스(`send(notification) -> list[DeliveryResult]`) 뒤에 두고, 팩토리 1곳에서 NOTIFIER 값으로 구현체를 고른다
5. WHEN GET /api/notifications 요청을 받으면, THE API_Server SHALL Notification_Log를 생성 시각 내림차순(같으면 id 내림차순)으로 최대 `limit`(기본 20, 최대 100)건 반환하고, `kind` 필터를 받으며, 각 항목에 전달 결과 요약(webpush 성공 수, 실패 수, gone 수, log 기록 여부)을 담는다
6. WHEN 홈 화면이 열리고 오늘(APP_TIMEZONE) 만든 `daily_brief` 알림이 있으면, THE Mobile_Web_Client SHALL 가장 최근 1건을 Daily_Brief_Card로 Phase 1 Notification_Card 위에 표시하고, 카드를 누르면 알림 링크로 이동하며, 날씨가 fixture이면 FixtureBadge를 붙인다. 오늘 알림이 없으면 카드를 표시하지 않는다
7. THE Mobile_Web_Client SHALL Phase 1 Notification_Card의 동작(Phase 1 Req 13)을 바꾸지 않는다
8. FOR ALL 알림 생성과 전달 결과(성공, 실패, 예외)의 순서에 대해, Notification_Log의 알림 수 SHALL 생성 요청 수와 같고, GET /api/notifications의 순서는 (생성 시각 내림차순, id 내림차순) 참조 정렬과 같다

### Requirement 15: Web Push 구독과 VAPID 키

**User Story:** 사용자로서, 앱을 닫아 두어도 휴대폰 알림처럼 아침 브리핑을 받고 싶다. 그래서 앱을 열어 확인하는 수고가 없다.

#### Acceptance Criteria

1. WHEN VAPID 키가 처음 필요해지면(NOTIFIER=`webpush`로 API_Server가 기동하거나, GET /api/push/vapid-public-key 요청을 처음 받으면), THE VAPID_Key_Store SHALL VAPID_KEY_DIR에 P-256 EC 키 쌍을 만들어 개인키는 PKCS8 PEM(`vapid_private.pem`, 파일 권한 0600)으로, 공개키는 base64url 비압축 점 형식(`vapid_public.txt`)으로 저장하고, 네트워크와 Firebase 계정 없이 동작한다
2. WHILE 유효한 개인키 파일이 있는 동안, THE VAPID_Key_Store SHALL 새 키를 만들지 않고 개인키에서 공개키를 계산해 쓴다. 두 프로세스가 동시에 처음 실행돼도 키 쌍은 1개만 만들어진다
3. IF 개인키 파일을 읽을 수 없거나 형식이 맞지 않으면, THEN THE VAPID_Key_Store SHALL 그 파일을 지우지 않고 `vapid_private.pem.invalid-<UTC yyyymmddHHMMSS>`로 이름을 바꾼 뒤 새 키 쌍을 만들고, 이전 공개키로 만든 Push_Subscription을 모두 지우며, WARNING 로그를 남긴다
4. WHEN GET /api/push/vapid-public-key 요청을 받으면, THE API_Server SHALL 공개키(base64url)만 반환하고 개인키는 어떤 응답에도 넣지 않는다
5. WHEN POST /api/push/subscriptions 요청을 브라우저 PushSubscription JSON(`endpoint`(https URL, 2,048자 이하), `keys.p256dh`(base64url, 65바이트), `keys.auth`(base64url, 16바이트))으로 받으면, THE Push_Subscription_Store SHALL endpoint 기준으로 저장하거나 갱신하고(같은 endpoint 2건 없음) 201과 구독 id를 반환하며, 형식이 맞지 않으면 422 `PUSH_SUBSCRIPTION_INVALID`를 반환하고 저장하지 않는다
6. WHEN DELETE /api/push/subscriptions/{id} 요청을 받으면, THE Push_Subscription_Store SHALL 그 구독을 지우고 204를 반환하며, 없는 id이면 404 `PUSH_SUBSCRIPTION_NOT_FOUND`를 반환한다
7. WHEN Web Push 전송 응답이 404 또는 410(Gone)이면, THE WebPush_Notifier SHALL 그 Push_Subscription을 지우고 전달 결과를 `gone`으로 기록한다. 그 밖의 실패(나머지 4xx, 5xx, 10초 시간 초과, 연결 오류)는 `failed`로 기록하고 구독을 지우지 않으며, 같은 알림을 다시 보내지 않는다
8. THE WebPush_Notifier SHALL 푸시 본문을 JSON {"title", "body", "url", "notification_id", "kind"}로 만들어 3,000바이트 이하로 보내고, TTL은 12시간, VAPID 클레임 `sub`는 VAPID_SUBJECT로 둔다
9. WHEN POST /api/push/test 요청을 받으면, THE API_Server SHALL NOTIFIER가 `webpush`이면 "알림이 켜졌어요" 테스트 알림(`kind: push_test`)을 Notification_Log에 저장하고 모든 구독에 보내 전달 결과를 반환하며, NOTIFIER가 `log`이면 409 `WEB_PUSH_DISABLED`를 반환한다
10. IF VAPID_KEY_DIR에 쓸 수 없어 키를 만들지 못하면, THEN THE API_Server SHALL GET /api/push/vapid-public-key와 POST /api/push/test에 503 `VAPID_KEY_UNAVAILABLE`을 반환하고, WebPush_Notifier는 webpush 전달 없이 알림 기록만 남긴다
11. FOR ALL 생성한 키 쌍에 대해, 저장된 개인키 PEM을 다시 읽어 계산한 공개키 SHALL 저장된 공개키와 같고, 공개키를 base64url로 디코딩하면 0x04로 시작하는 65바이트다

### Requirement 16: 브라우저 푸시·위치 권한과 화면

**User Story:** 사용자로서, 권한은 한 번만 허용하고 그 뒤로는 신경 쓰지 않기를 원한다. 그래서 앱이 알아서 위치와 알림을 처리한다.

#### Acceptance Criteria

1. THE Mobile_Web_Client SHALL `frontend/public/sw.js` Service_Worker를 제공하고, Service_Worker는 push 이벤트를 받으면 payload의 title·body로 시스템 알림(tag `weatherwear-daily-brief`)을 띄우며, 알림을 누르면 이미 열린 WeatherWear 창을 앞으로 가져와 payload의 url로 이동하거나, 열린 창이 없으면 그 url을 새 창으로 연다
2. THE Mobile_Web_Client SHALL 페이지를 열 때 알림 권한이나 위치 권한을 자동으로 요청하지 않고, 사용자가 Notifications_Page의 "알림 켜기" 또는 "현재 위치 사용" 버튼을 누를 때에만 브라우저 권한 요청을 띄운다
3. WHEN 사용자가 "알림 켜기"를 누르고 권한을 허용하면, THE Mobile_Web_Client SHALL Service_Worker를 등록하고, GET /api/push/vapid-public-key의 공개키로 `pushManager.subscribe({userVisibleOnly: true, applicationServerKey})`를 호출한 뒤, 구독을 POST /api/push/subscriptions로 보내고, 받은 구독 id를 브라우저 저장소에 보관한다
4. WHEN 사용자가 "알림 끄기"를 누르면, THE Mobile_Web_Client SHALL 브라우저 구독을 해지하고 DELETE /api/push/subscriptions/{id}를 호출한다
5. THE Mobile_Web_Client SHALL 푸시 상태를 "지원 안 함"(Service Worker, PushManager, Notification 중 하나가 없거나 secure context가 아님), "꺼짐", "켜짐", "차단됨"(권한 denied) 중 하나로 표시하고, "차단됨"이면 브라우저 설정에서 권한을 바꾸는 방법을 안내한다
6. WHEN 사용자가 "현재 위치 사용"을 눌러 위치 권한을 허용하면, THE Mobile_Web_Client SHALL `navigator.geolocation.getCurrentPosition`(enableHighAccuracy false, 10초 제한, maximumAge 30분)으로 얻은 위경도를 POST /api/location으로 보내고, 그 뒤 앱을 열 때마다 권한이 이미 `granted`이면 묻지 않고 위치를 다시 보내되 30분에 1회 이하로 보낸다
7. IF 위치 권한이 거부되거나 위치를 10초 안에 얻지 못하면, THEN THE Mobile_Web_Client SHALL "기본 위치(서울) 날씨로 알려 드릴게요" 안내를 표시하고 화면을 막지 않는다
8. THE Mobile_Web_Client SHALL 하단 탭바에 "알림" 탭(`/notifications`)을 추가하고, Notifications_Page에 푸시 상태와 켜기/끄기, 위치 상태(마지막 보고 시각과 Place_Label), 매일 브리핑 사용 여부와 시각, 최근 알림 목록, "확인이 필요한 사진"(`uncertain`) 목록과 각 항목의 "다시 분석" 버튼을 표시한다
9. THE Mobile_Web_Client SHALL Phase 2의 모든 UI 문구를 `src/lib/strings.ts`에 한국어로 두고(Phase 1 Req 11.5), 360~430px 너비에서 가로 스크롤이 없게 한다(Phase 1 Req 11.6)
10. THE Mobile_Web_Client SHALL 새 `VITE_` 환경 변수를 추가하지 않고(Phase 1 Req 16.6), VAPID 공개키는 실행 중에 API에서 받는다
11. WHILE 기록 화면이 열려 있는 동안, WHEN 브라우저 탭이 다시 보이게 되면(`visibilitychange`), THE Mobile_Web_Client SHALL GET /api/outfits를 다시 불러 그사이 감시로 수집된 Outfit을 표시한다
12. FOR ALL (Service Worker·PushManager·Notification 지원 여부, secure context 여부, 권한 상태, 구독 여부) 조합에 대해, 푸시 상태 판정 SHALL 기준 5의 규칙을 그대로 옮긴 참조 함수와 같고, 위치 재보고 판정은 권한이 `granted`이고 마지막 보고 후 30분 이상 지났을 때에만 true이다

### Requirement 17: 자동화 상태와 사진 처리 기록

**User Story:** 데모 발표자로서, 자동화가 지금 돌고 있는지 한눈에 보고 싶다. 그래서 문제가 생기면 바로 알 수 있다.

#### Acceptance Criteria

1. WHEN GET /api/automation/status 요청을 받으면, THE API_Server SHALL 감시 사용 여부·동작 여부·Gallery_Dir 사용 가능 여부·마지막 확인 시각·대기 중 파일 수·`uncertain` 수, 매일 브리핑 사용 여부·DAILY_BRIEF_TIME·다음 예정 시각·마지막 예약 실행 결과, NOTIFIER 이름·VAPID 키 준비 여부·구독 수, Last_Location 유무·받은 시각·Place_Label을 반환한다
2. THE API_Server SHALL Automation_Status와 사진 처리 기록 응답에 서버 파일 경로, VAPID 개인키, 구독 endpoint와 키, 위경도 원값, GEMINI_API_KEY를 넣지 않는다
3. WHEN GET /api/gallery/imports 요청을 받으면, THE API_Server SHALL Baseline_File을 뺀 Gallery_File 기록을 갱신 시각 내림차순으로 최대 `limit`(기본 20, 최대 100)건 반환하고, `status` 필터를 받으며, 각 항목에 Gallery_Dir 기준 파일 이름, Gallery_Status, 시도 횟수, 마지막 실패 단계·코드, 다음 시도 시각, Outfit id, 판정(photo_type, 신뢰도, reason), 촬영 정보 출처(`exif`/`estimated`)를 담는다
4. THE Demo_Admin_Panel SHALL 자동화 상태(감시 동작 여부, 마지막 확인 시각, 최근 사진 처리 5건과 각 상태, 다음 브리핑 예정 시각, NOTIFIER)를 표시한다

### Requirement 18: 환경 변수와 비밀값

**User Story:** 개발자로서, 새 설정도 Phase 1처럼 환경 변수로 관리하고 비밀값이 새지 않기를 원한다. 그래서 저장소를 안전하게 공유할 수 있다.

#### Acceptance Criteria

1. THE Config_Loader SHALL Phase 2 변수 9개를 Phase 1 Req 16.2와 같은 우선순위(프로세스 환경 변수 > `.env` > 기본값)로 읽고, 값이 없으면 다음 기본값을 쓴다: GALLERY_WATCH_ENABLED=false, GALLERY_DIR=./ex_img, GALLERY_POLL_SECONDS=2, USER_PHOTO_DIR=./data/user/photos, DAILY_BRIEF_ENABLED=false, DAILY_BRIEF_TIME=07:30, NOTIFIER=log, VAPID_KEY_DIR=./data/secrets, VAPID_SUBJECT=mailto:weatherwear@example.com
2. IF 불리언 변수가 `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off`(대소문자 무시)가 아니거나, GALLERY_POLL_SECONDS가 0.5 이상 60 이하의 유한한 숫자가 아니거나, DAILY_BRIEF_TIME이 24시간 `HH:MM`이 아니거나, NOTIFIER가 `log`/`webpush`가 아니거나, VAPID_SUBJECT가 `mailto:` 또는 `https://`로 시작하지 않으면, THEN THE Config_Loader SHALL 변수 이름과 허용값만 담은 설정 오류를 ERROR 로그로 남기고(입력값은 남기지 않음) 그 변수에 기본값을 쓴다
3. THE Config_Loader SHALL 상대 경로인 GALLERY_DIR, USER_PHOTO_DIR, VAPID_KEY_DIR를 Phase 1 PHOTO_DIR과 같이 저장소 루트 기준 절대 경로로 바꾼다
4. THE Config_Loader SHALL Phase 2 설정을 기본값이 있는 AutomationConfig로 AppConfig에 붙여, Phase 1 테스트가 AppConfig를 만드는 기존 호출을 고치지 않아도 되게 하고, AutomationConfig 기본값은 감시와 매일 브리핑이 모두 꺼진 상태로 둔다
5. THE WeatherWear_System SHALL `.env.example`에 Phase 1 변수 12개와 Phase 2 변수 9개(합계 21개)를 각각 이름, 기본값, 한 줄 이상 설명과 함께 두고, GALLERY_DIR 설명에 "`ex_img/`를 휴대폰 갤러리로 본다"는 점을, VAPID_KEY_DIR 설명에 "개인키가 저장되므로 커밋하지 않는다"는 점을 적는다
6. THE WeatherWear_System SHALL `.gitignore`에 `data/secrets/`를 넣어 VAPID 키 파일이 커밋되지 않게 한다
7. THE WeatherWear_System SHALL VAPID 개인키 값과 Push_Subscription의 endpoint 전체·`p256dh`·`auth` 값을 로그, 예외 메시지, API 응답, README, 브라우저 콘솔에 출력하지 않고, 로그에는 구독 id와 endpoint의 호스트 이름만 남기며, Phase 1 RedactingFilter가 VAPID 개인키 문자열도 `***`로 바꾸게 한다
8. THE WeatherWear_System SHALL `npm run check:bundle`을 넓혀, `dist/`에 `PRIVATE KEY` 문자열이 있거나, VAPID_KEY_DIR(프로세스 환경 변수, 루트 `.env`, 기본값 순)의 `vapid_private.pem`이 있을 때 그 키 본문(base64 부분에서 연속 16자 이상)이 있으면 실패하게 하고, 일치한 내용과 키를 출력하지 않는다
9. FOR ALL Phase 2 변수 9개에 대한 임의 문자열(빈 문자열, 형식 밖 값, 범위 밖 숫자, NaN 포함)에 대해, 설정 해석 SHALL 예외 없이 AutomationConfig를 만들고, 각 변수는 유효하면 그 값, 무효하면 기준 1의 기본값이 되며, 무효한 변수마다 변수 이름을 담은 ConfigIssue가 1건 생긴다

### Requirement 19: DB 스키마 확장

**User Story:** 사용자로서, Phase 2로 업데이트해도 지금까지 남긴 체감 피드백이 사라지지 않기를 원한다. 그래서 처음부터 다시 기록하지 않아도 된다.

#### Acceptance Criteria

1. THE Outfit_Repository SHALL Phase 2 테이블(`gallery_files`, `app_state`, `user_photos`, `user_location`, `push_subscriptions`, `notifications`, `notification_deliveries`, `daily_brief_runs`)을 기존 테이블을 바꾸지 않는 추가 방식(`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`)으로 만들고, 스키마 버전을 `PRAGMA user_version = 3`으로 표시한다
2. WHEN 기동 시 DATABASE_URL 파일의 user_version이 2이면, THE WeatherWear_System SHALL 그 파일을 백업하거나 다시 만들지 않고 Phase 2 테이블만 더한 뒤 user_version을 3으로 바꾸며, 기존 outfits·weather_snapshots·feedback·photo_analyses 행을 그대로 둔다
3. WHEN user_version이 2 미만이면, THE WeatherWear_System SHALL Phase 1과 같이 파일을 백업 이름으로 옮기고 새로 만든다
4. IF user_version이 3보다 크면, THEN THE WeatherWear_System SHALL user_version을 낮추지 않고 WARNING 로그를 남기며, 없는 테이블만 만든다
5. THE Outfit_Repository SHALL outfits, weather_snapshots, feedback, photo_analyses 테이블의 열과 CHECK 제약을 바꾸지 않는다
6. FOR ALL user_version 2 DB(임의의 Outfit, Weather_Snapshot, feedback, photo_analyses 행 포함)에 대해, Phase 2 기동 후 네 테이블의 행 SHALL 기동 전과 같고 user_version은 3이며, 같은 파일로 다시 기동해도 바뀌는 것이 없다 (멱등)

### Requirement 20: 오프라인 테스트, Phase 1 테스트 갱신, 문서

**User Story:** 심사위원과 개발자로서, 네트워크 없이 Phase 2 전체를 테스트하고 README대로 데모를 재현하고 싶다. 그래서 자동화가 실제로 동작함을 확인할 수 있다.

#### Acceptance Criteria

1. THE WeatherWear_System SHALL Phase 2 자동화 테스트를 Phase 1 소켓 가드(`conftest.py`) 아래에서 외부 네트워크 없이 실행하고, Open_Meteo_Forecast_API는 respx로, Web Push 전송은 주입한 가짜 Push_Sender로, 시각은 주입한 시계로 대신한다
2. THE WeatherWear_System SHALL Gallery_Watcher 테스트는 백그라운드 스레드 없이 tmp 디렉터리에서 `poll_once()`를 직접 호출해 확인하고, 스레드 시작과 종료는 lifespan 테스트로만 확인한다
3. THE WeatherWear_System SHALL 다음 영역마다 1개 이상의 pytest 예제 테스트를 둔다: (a) 감시 기준선과 새 파일 수집, (b) 쓰기 중 파일 debounce, (c) 중복 제거와 Phase 1 데모 데이터 불변식, (d) Photo_Gate 판정별 처리(`outfit`/`non_outfit`/`uncertain`)와 fixture overlay, (e) 내장 메타데이터 없음 → 추정 사이드카, (f) Vision 실패 재시도와 최종 실패, (g) Recent_Weather_Window 엔드포인트 선택, (h) 매일 브리핑 문구(사용자 결정 2의 예시 문장과 fixture 데모 데이터로 정확히 일치), (i) 하루 1회 멱등성, (j) Web Push 410 구독 삭제, (k) DB user_version 2 → 3 추가 업그레이드, (l) 환경 변수 기본값과 오류 대체
4. THE WeatherWear_System SHALL Phase 1 테스트 중 목록을 정확히 고정한 다음 테스트만 Phase 2 기준으로 고친다: `test_env_example_lists_exactly_twelve_vars_with_empty_key`와 `test_raw_settings_declares_same_backend_vars_and_model_default`(변수 21개 / 백엔드 변수 20개), `test_new_db_has_schema_version`(3), `test_api_routes.py`의 ROUTER_MODULES 집합, `test_api.py`의 `EXPECTED_OPERATIONS`, 프론트엔드 `AppShell.test.tsx`의 하단 탭 목록(홈, 기록, 알림). 그 밖의 Phase 1 테스트는 고치지 않고 통과해야 한다
5. WHEN 개발자가 API 키 없이 README의 테스트 명령을 실행하면, THE WeatherWear_System SHALL Phase 1과 Phase 2 pytest 테스트, 프론트엔드 vitest·typecheck·check:bundle을 모두 통과하고 종료 코드 0으로 끝난다 (Phase 1 Req 17.5)
6. THE WeatherWear_System SHALL 새 직접 의존성(`pywebpush`, `cryptography`)을 `backend/requirements.txt`에 정확한 버전(`==`)으로 고정하고, 그 버전으로 전체 테스트가 통과함을 확인한다 (Phase 1 Req 17.7)
7. THE WeatherWear_System SHALL README에 Phase 2 절을 두고 다음을 적는다: 감시와 매일 브리핑을 켜는 방법, Phase 2 환경 변수 9개와 기본값, fixture 모드에서는 Vision_Fixture_Overlay가 없는 새 사진이 `uncertain`으로 남고 실제 분석은 VISION_PROVIDER=gemini일 때만 된다는 점, `scripts/simulate_capture.py`·`scripts/watch_gallery.py`·`scripts/daily_brief.py` 사용법, Web Push와 위치는 secure context(`http://localhost` 또는 HTTPS)에서만 동작하고 iOS Safari는 홈 화면에 추가한 웹 앱에서만 푸시를 받는다는 점, 번호 매긴 Phase 2 데모 절차(API 키 없이 fixture 모드로 가능)
8. THE WeatherWear_System SHALL README의 Phase 2 개선 목록에서 이번에 구현한 Web Push와 갤러리 대체 감시를 완료 목록으로 옮기고, 실제 휴대폰 갤러리 접근과 FCM은 범위 밖으로 남긴다
