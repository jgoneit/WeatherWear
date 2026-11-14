# 👕 WeatherWear

> **"14°C에 뭐 입었더라?"**
> 예전에 비슷한 기온에서 내가 실제로 입은 옷과 그날의 체감(🥶 추웠어 / 😊 적당했어 / 🥵 더웠어)을 보여 주는 착장 추천 웹 앱

**Demo:** TODO(팀): 데모 영상 링크

---

## 1. 문제 정의

### 대상 사용자
매일 아침 날씨 앱의 기온만 보고 옷을 고르는 사람. 갤러리에 착장 사진은 쌓여 있지만 옷 고를 때 다시 보지는 않는 사람.

### 문제 상황
- 날씨 앱은 **"14°C"라는 숫자만** 알려 준다.
- 내가 14°C에 무엇을 입고 **추웠는지, 더웠는지는 모른다.**
- 그래서 같은 실수를 반복하고 하루 종일 춥거나 덥게 지낸다.

### 기존 방식과 한계

| 기존 방식 | 한계 |
|---|---|
| 기온별 옷차림 표 | 모두에게 같은 답. 추위·더위를 타는 정도가 반영되지 않음 |
| 갤러리 직접 넘겨 보기 | 사진에 날씨가 없어 "비슷한 기온의 날"을 찾을 수 없음 |
| 옷장 관리 앱 | 옷을 하나씩 등록해야 해서 금방 안 쓰게 됨 |

### 근거
- 가설(`.kiro/specs/weatherwear/requirements.md`): **"머신러닝 없이도, 과거 실제 경험 + 유사 기온 검색이 일반적인 기온 기반 추천보다 유용하다."**
- TODO(팀): 직접 조사한 근거(설문 응답 수, 인터뷰 등)가 있으면 숫자와 출처를 적는다.

### 목표
- 이미 찍어 둔 사진만으로 **비슷한 기온(±3.0°C)의 내 착장 + 그날 체감**을 한 화면에 보여 준다.
- 사용자가 하는 일은 **체감 버튼 한 번**뿐. 옷 등록, 날씨 입력은 없다.

---

## 2. 핵심 기능

✅ Phase 1 구현 완료 · 🗓️ Phase 2 계획(spec 완료, 구현 예정)

| 기능 | 해결하는 불편 | 코드 위치 |
|---|---|---|
| ✅ 사진 폴더 스캔 | 사진을 하나씩 등록하는 수고 | `backend/app/ingestion/service.py` |
| ✅ 착장 사진만 고르기 (착장 / 비착장 / 불확실) | 음식·풍경 사진이 추천에 섞임 | `backend/app/providers/vision/` |
| ✅ 옷 종류 자동 태그 (Gemini Vision) | 옷 종류를 직접 입력하는 수고 | `backend/app/providers/vision/gemini.py` |
| ✅ 사진 EXIF로 촬영 시각·위치 읽기 | 언제·어디서 찍었는지 적는 수고 | `backend/app/ingestion/metadata.py` |
| ✅ 촬영 당일 날씨 붙이기 (Open-Meteo) | 과거 사진에 날씨가 없음 | `backend/app/providers/weather/` |
| ✅ 비슷한 기온 착장 추천 (`rule_based_v1`) | 숫자만 보고 옷을 고름 | `backend/app/recommendation/` |
| ✅ 체감 피드백 원클릭 저장 → 즉시 재추천 | 그 옷이 적당했는지 기록이 없음 | `backend/app/feedback.py`, `frontend/src/components/FeedbackButtons.tsx` |
| ✅ 모바일 화면 (홈 · 기록 · 상세 · 데모) | 과거 착장을 날씨와 함께 다시 보기 | `frontend/src/pages/` |
| ✅ Vision 정확도 평가 · Docker Compose | 품질 확인 · 한 번에 실행 | `backend/app/evaluation/`, `compose.yaml` |
| 🗓️ 새 사진 자동 감지 | 찍을 때마다 스캔 버튼을 누르는 수고 | `.kiro/specs/weatherwear-automation/` |
| 🗓️ 매일 아침 위치 기반 Web Push 브리핑 | 앱을 열어야만 추천이 보임 | `.kiro/specs/weatherwear-automation/` |

## 3. 사용 시나리오

1. 📁 **폴더 스캔**: 착장 사진만 골라 옷 태그를 뽑고, 촬영 당일 날씨를 붙인다.
   (데모 189장 → 착장 169 / 비착장 20)
2. 🌤️ **아침에 홈 화면**: 오늘 14.5°C와 함께, 기온 차이 3.0°C 이내였던 날의 내 착장 5개와 근거가 보인다.
   (1순위: 과거 14.4°C에 "적당했어"를 남긴 착장, 114.0점)
3. 👆 **체감 버튼 한 번**: 🥶 / 😊 / 🥵 중 하나를 누르면 확인 없이 저장된다.
4. 🔄 **추천이 바로 바뀐다**: 새로고침 없이 점수가 다시 계산된다.
5. 🗂️ **기록 탭**: 과거 착장을 날짜순으로 보고, 그날 날씨와 체감을 확인한다.
6. 🗓️ *(Phase 2)* 매일 아침 이런 알림을 받는다:
   > "오늘 서울 14.5°C, 습도 55%. 비슷했던 14.4°C 날 크림 니트를 입고 '적당했어'라고 했어요"

## 4. 기대 효과

> 아래는 측정 결과가 아니라 **목표와 가설**이다. 사용자 대상 측정 데이터는 아직 없다.

| 항목 | 내용 |
|---|---|
| 🎯 사용자 입력 (목표) | 옷 등록 **0건** · 날씨 입력 **0건** · 체감 버튼 **1회** |
| 💡 가설 | 내 과거 경험을 쓰면 기온별 옷차림 표보다 춥거나 덥게 입는 날이 줄어든다 |
| 🗓️ Phase 2 목표 | 처음 권한만 허용하면 그 뒤로는 버튼 없이 사진 등록과 아침 추천 |

레포로 확인할 수 있는 수치:

| 항목 | 값 |
|---|---|
| 데모 사진 | 189장 (착장 169 / 비착장 20) |
| 시드 체감 투표 | 66개 |
| 유사 기온 허용 오차 | ±3.0°C (후보가 없으면 ±5.0°C로 1회 확장) |
| 데모 시나리오 | 14.5°C 정상 · −16.0°C 경계 · 44.0°C 범위 확장 · 48.0°C 데이터 부족 |
| 테스트 | 백엔드 602 passed · 프론트엔드 92 passed |

TODO(팀): 사용자 테스트를 했다면 "춥거나 덥게 입은 날 비율" 같은 측정값을 적는다.

## 5. 차별점

- 🙋 **내 사진, 내 체감 기준**: 모두에게 같은 옷차림 표가 아니라 *내가* 입었던 옷으로 추천한다.
- ✋ **입력이 거의 없다**: 옷을 등록하지 않는다. 사진 EXIF와 기록 날씨로 자동으로 날씨를 붙인다.
- 🔍 **이유가 보이는 추천**: 머신러닝 없이 점수와 근거 문장을 보여 준다. 추천 전략은 교체 가능하게 분리했다.
- 🧹 **착장 사진만**: 갤러리에 섞인 음식·풍경 사진은 자동으로 뺀다.
- 🔌 **API 키 없이 바로 실행**: 기본 Fixture 모드에서 외부 호출 없이 전 기능이 동작한다.

---

## 6. 설치와 실행

> 필요: Python 3.10+, Node.js + npm. 기본 Fixture 모드라 **API 키가 필요 없다.**

```bash
# 1. 의존성 설치
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd ../frontend && npm ci

# 2. 실행 (터미널 두 개)
cd backend && .venv/bin/uvicorn app.main:app --port 8000
cd frontend && VITE_API_BASE_URL=http://localhost:8000 npm run dev
```

브라우저에서 **http://localhost:5173** 을 연다. (휴대폰 화면은 개발자 도구 기기 모드 360~430px)

🐳 Docker로 한 번에:

```bash
docker compose up --build    # 백엔드 :8000, 프론트엔드 :5173
docker compose down
```

환경 변수, Live 모드, 데모 사진 준비는 아래 [부록](#부록)을 본다.

## 7. 테스트

```bash
# 백엔드
cd backend && .venv/bin/python -m pytest -q

# 프론트엔드
cd frontend && npx vitest --run && npm run typecheck && npm run check:bundle
```

| 검사 | 결과 (PR #9 병합 기록) |
|---|---|
| 백엔드 pytest | ✅ 602 passed (skip 0) |
| 프론트엔드 vitest | ✅ 92 passed |
| typecheck · check:bundle | ✅ 통과 |
| 데모 데이터 규칙 `scripts/select_scenarios.py --check` | ✅ 통과 |

- API 키와 외부 네트워크 없이 돈다.
- 5분 데모 흐름은 `test_five_minute_demo_walkthrough`가 그대로 확인한다.

---

## 팀

- **팀 번호:** team-020 (레포 `jgoneit/kirothon-020`)
- **팀명:** 바퀸

| 이름 | 역할 |
|---|---|
| 서재곤 | 개발 |
| 조민재 | 개발 |

---

## 부록

<details>
<summary><b>자세한 기술 문서 펼치기</b> (환경 변수, Live 모드, 데모 사진 준비, 5분 데모 절차, 데이터 출처, 기능 상태)</summary>


아래는 기존 README의 자세한 기술 내용이다.

### 기술 구성

- 백엔드: FastAPI + SQLModel + SQLite (`backend/`)
- 프론트엔드: React + Vite + TypeScript + Tailwind v4 (`frontend/`)
- 데모 데이터: `data/demo/` (사진 목록, 시드 투표 사이드카, 시나리오, fixture)
- 보조 스크립트: `scripts/`
- Kiro spec: `.kiro/specs/weatherwear/`(Phase 1), `.kiro/specs/weatherwear-automation/`(Phase 2 계획)
- 라이선스: [LICENSE](LICENSE) (MIT)

### 실행 세부 사항

- 백엔드를 `backend/`에서 실행해도 루트 `.env`를 읽는다. `PHOTO_DIR`, `DATABASE_URL`의 상대 경로는 저장소 루트 기준이다. 기본 DB 파일은 `data/weatherwear.db`이고 기동 시 자동으로 만든다.
- Vite는 `frontend/` 폴더의 env 파일만 읽는다. 매번 변수를 붙이기 싫으면 `frontend/.env.local`에 `VITE_API_BASE_URL=http://localhost:8000`을 넣는다. 값이 없으면 API 호출이 실패한다.
- 데모 관리 화면(`/demo`)과 하단 "데모" 탭은 `npm run dev`(개발 모드)에서만 보인다.
- Docker: 백엔드·프론트엔드 이미지는 멀티스테이지 빌드이고 비루트 사용자로 실행한다. 사진 폴더는 호스트의 `./data/demo/photos`를 읽기 전용으로 마운트한다(다른 폴더는 `PHOTO_HOST_DIR`). 포트는 `BACKEND_PORT`, `FRONTEND_PORT`로 바꾼다. 백엔드 포트를 바꾸면 `VITE_API_BASE_URL`도 함께 지정한다. Gemini live 모드는 `VISION_PROVIDER=gemini docker compose up --build`로 실행하며, 키는 실행 시 환경 변수로만 전달하고 `--build-arg`로 넘기지 않는다.
- 참고: Phase 1 구현 뒤 사용자 결정으로 Gemini 호출은 Google Cloud Agent Platform API(express mode, API 키 인증)로, 촬영 시각·위치는 내장 EXIF 우선으로, Fixture 날씨는 Open-Meteo에서 기록한 값으로 바뀌었다(`.kiro/specs/weatherwear/requirements.md` "Phase 1 구현 후 변경 사항").

### 환경 변수

루트의 `.env.example`을 `.env`로 복사해 쓴다. 우선순위는 프로세스 환경 변수 > 루트 `.env` > 아래 기본값이다. 설정은 서버 기동 시 한 번 읽으므로 바꾼 뒤에는 서버를 다시 시작한다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `GEMINI_API_KEY` | (빈 값) | Google Cloud Agent Platform express mode API 키. Google AI Studio 키로는 동작하지 않는다. `VISION_PROVIDER=gemini`일 때만 필요하다 |
| `VISION_PROVIDER` | `fixture` | `fixture`(사전 정의 태그) 또는 `gemini`(Agent Platform express mode로 Gemini 호출) |
| `GEMINI_MODEL` | `gemini-4-hydrogen` | `VISION_PROVIDER=gemini`일 때 호출할 모델 ID. express mode 지원 여부는 아래 "API 키 설정" 절을 본다 |
| `WEATHER_PROVIDER` | `fixture` | `fixture`(기록해 둔 fixture 날씨) 또는 `open_meteo`(Open-Meteo Archive API, 키 불필요) |
| `DATABASE_URL` | `sqlite:///./data/weatherwear.db` | SQLite DB URL |
| `PHOTO_DIR` | `./data/demo/photos` | 스캔할 사진 폴더. 최상위의 `.jpg/.jpeg/.png/.webp`를 읽고, 촬영 시각·위치는 사진 내장 EXIF에서 읽는다. 같은 이름의 `*.metadata.json`(선택)은 시드 투표와 대체 출처로 쓴다 |
| `APP_TIMEZONE` | `Asia/Seoul` | 날짜·시각 표시와 Open-Meteo 조회에 쓰는 IANA 타임존 |
| `TEMPERATURE_TOLERANCE_C` | `3` | 유사 기온 허용 오차(°C). 0 초과 5.0 미만, 비우면 3.0 |
| `DEFAULT_LATITUDE` | `37.5665` | 오늘 날씨 조회 위도(서울) |
| `DEFAULT_LONGITUDE` | `126.9780` | 오늘 날씨 조회 경도(서울) |
| `CORS_ORIGINS` | `http://localhost:5173` | CORS 허용 출처. 쉼표로 구분 |
| `VITE_API_BASE_URL` | `http://localhost:8000` | 프론트엔드가 호출할 백엔드 URL. 프론트엔드(Vite)만 읽는다 |

잘못된 값(허용 목록 밖 provider 이름, 범위 밖 숫자 등)은 기동을 막지 않는다. 로그에 변수 이름과 허용값만 남기고 기본값을 쓴다.

### API 키 설정

`GEMINI_API_KEY`에는 Google Cloud Gemini Enterprise Agent Platform(구 Vertex AI)의 express mode API 키를 넣는다. Google AI Studio(Gemini Developer API) 키로는 동작하지 않는다. express mode는 API 키만으로 호출하므로 Google Cloud 프로젝트 ID, 위치, 서비스 계정 자격 증명은 설정하지 않는다.

1. express mode 콘솔의 APIs & Services > Credentials에서 API 키를 받는다.
2. 루트 `.env`에 `GEMINI_API_KEY=<발급받은 키>`와 `VISION_PROVIDER=gemini`를 적는다.
3. 키는 루트 `.env`에만 둔다. `.env`는 `.gitignore`에 들어 있어 커밋되지 않는다. `.env.example`의 `GEMINI_API_KEY`는 빈 값으로 둔다.
4. 키를 `VITE_`로 시작하는 변수에 넣지 않는다. `VITE_` 변수는 브라우저 번들에 그대로 들어간다. 프론트엔드는 `VITE_API_BASE_URL`만 읽고, `npm run check:bundle`이 빌드 결과물에 `GEMINI` 문자열이나 실제 키 값이 없는지 검사한다.
5. 첫 Live Vision 호출 전에 모델을 확인한다: `backend/.venv/bin/python scripts/check_gemini_model.py`. 이 스크립트는 옵션을 받지 않는다(`--help`도 없다). 키가 비어 있으면 호출하지 않고 `SKIP`을 출력한다(종료 코드 0). 키가 있으면 백엔드와 같은 express mode 클라이언트로 짧은 텍스트 `generate_content` 요청 1건을 보내고, 모델 ID와 `OK` 또는 `FAIL`(오류 종류와 HTTP 상태 코드)만 출력한다(성공 0, 실패 1). 키 값과 응답 본문은 출력하지 않는다.

#### `GEMINI_MODEL` 기본값과 express mode 지원 여부

- 기본값은 `gemini-4-hydrogen`이다. Agent Platform 모델 페이지는 Gemini 4 Hydrogen의 모델 ID를 `gemini-4-hydrogen`으로 안내한다([Gemini 4 Hydrogen](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/4-hydrogen)).
- 2026-11-14 기준 express mode 개요의 "사용 가능한 모델" 표에는 `gemini-4-hydrogen`이 없다. 이 문서는 표에 있는 모델만 express mode에서 지원한다고 적고 있다([express mode 개요](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/start/express-mode/overview)). 2026-11-12 출시 직후라 표가 늦게 갱신됐을 수 있다.
- 그래서 위 5번의 `scripts/check_gemini_model.py`로 실제 호출이 되는지 확인한다. 실패하면 `.env`에 `GEMINI_MODEL=gemini-3-flash-preview`(표에 있는 모델, 분당 요청 한도 Dynamic)를 적고 다시 확인한다. 표의 `gemini-2.5-*` 계열은 분당 10회 제한이라 189장 첫 스캔에 18분 이상 걸리므로 권하지 않는다.
- HTTP 404로 실패하면 스크립트는 `gemini-3-flash-preview`와 함께 global 위치 전체 경로 `projects/<PROJECT_NUMBER>/locations/global/publishers/google/models/gemini-4-hydrogen`도 안내한다. `GEMINI_MODEL`이 `projects/`로 시작하면 백엔드는 global 엔드포인트(`https://aiplatform.googleapis.com/`, API 버전 `v1`)로 호출한다.
- 호출 방법 출처: [express mode 개요](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/start/express-mode/overview), [API 빠른 시작](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/start/express-mode/vertex-ai-express-mode-api-quickstart) (2026-11-14 확인)

백엔드는 키를 로그, 오류 메시지, API 응답에 출력하지 않는다. 데모 관리 화면에는 "설정됨" 또는 "미설정"만 보인다. `VISION_PROVIDER=gemini`인데 키나 모델이 비어 있으면 누락된 변수 이름만 로그에 남기고 Fixture Vision으로 기동한다.

### Fixture 모드와 Live 모드 전환

| 모드 | 설정 | 동작 |
|---|---|---|
| Fixture (기본) | `VISION_PROVIDER=fixture`, `WEATHER_PROVIDER=fixture` | 외부 호출 0건. 같은 데이터면 매번 같은 태그·날씨·추천 순위 |
| Live Vision | `VISION_PROVIDER=gemini` (+ express mode 키 `GEMINI_API_KEY`) | 새 사진 1장당 Gemini 호출 1회(사진 유형 판정과 의류 태그를 한 번에, 30초 제한, 재시도 없음). 보내기 전에 이미지의 EXIF와 위치 정보를 지운다 |
| Live 날씨 | `WEATHER_PROVIDER=open_meteo` | Open-Meteo Archive API 호출(10초 제한). 실패하면 fixture 날씨로 대체 |

두 변수는 따로 바꿀 수 있다. 예: `VISION_PROVIDER=fixture WEATHER_PROVIDER=open_meteo .venv/bin/uvicorn app.main:app --port 8000`.

이미 스캔한 사진은 SHA-256 해시로 판정 결과를 기억해 다시 분석하지 않는다. 저장된 착장은 중복(duplicate), 착장 아닌 사진은 기억한 판정대로 다시 비착장 제외(`skipped_non_outfit`)로 집계한다. 판정이 불확실(`uncertain`)했던 사진만 다음 스캔에서 다시 분석한다. Fixture로 스캔한 DB에서 Live Vision으로 바꿔도 기존 태그는 그대로다. Live 결과를 보려면 `DATABASE_URL=sqlite:///./data/weatherwear-live.db`처럼 새 DB 파일을 지정한다.

### 데모 사진 준비

기존 개인 사진(`ex_img/`, `data/demo/photos/`의 174장)은 git에 올리지 않는다. 이번에 생성한 가상 인물 착장 15장(141~155번, 약 43MB)은 저장소에 포함되어 별도 다운로드 없이 사용할 수 있다. 저장소에는 사진별 사이드카(`data/demo/photos/*.metadata.json`), 목록(`data/demo/photos.manifest.json`), fixture(`data/demo/fixtures/`), 시나리오(`data/demo/scenarios.json`)만 들어 있다.

1. 전체 사진 세트를 쓰려면 프로젝트 소유자에게 받은 `ex_img/` 폴더를 저장소 루트에 둔다. 현재 목록은 기존 174장과 생성 착장 15장, 총 189장이다. 저장소에 포함된 15장만으로도 한파·무더위·폭염 추천을 실행할 수 있다.
2. `python3 scripts/prepare_demo_photos.py`로 manifest의 승인된 사진을 `data/demo/photos/`로 복사한다. 같은 파일과 기존 사이드카는 유지한다.
3. 생성 착장 15장의 데이터는 `python3 scripts/add_generated_outfits.py`로 병합한다. 기존 개인 사진의 fixture와 사용자 피드백은 유지하며, 생성 이미지 해시가 다르면 중단한다. 생성 프롬프트와 가상 기온은 `data/demo/generated_outfits.json`에 기록되어 있다.
4. 백엔드를 재시작하고 설정 화면에서 폴더를 스캔한다. `cd backend && ../.venv/bin/python -m pytest tests/test_integration.py tests/test_recommendation_service.py tests/test_api_routes.py tests/test_image_check.py -q`로 사진·추천·재스캔을 확인한다. (`.venv`가 backend 안에 있다면 경로를 조정한다.)

기존 `build_fixtures.py`는 처음 159장의 온도표와 경계/데이터 부족 시나리오를 위한 스크립트다. 확장된 사진 세트를 재생성하는 용도로 실행하지 않는다. 기존 126~140번 15장은 `import_clothing_tags.py`로 `vision.json`에 보충되어 착장으로 저장된다.

생성 사진을 원본 폴더에도 보관하려면 저장소 루트에서 `python3 scripts/add_generated_outfits.py --export-to ~/Downloads/ex_img`를 실행한다. 생성 PNG 15장만 복사하며 다른 파일을 덮어쓰지 않는다.

| 순서 | 명령 | 하는 일 |
|---|---|---|
| a. 사진 복사 | `backend/.venv/bin/python scripts/prepare_demo_photos.py` | manifest에서 `rights_confirmed: true`인 189장 중 `ex_img/`에 있는 174장을 `ex_img/`에서 `data/demo/photos/`로 파일 이름과 바이트 그대로 복사한다. 같은 파일은 건너뛰고 내용이 다른 파일은 덮어쓰며, 사이드카는 건드리지 않는다. 복사 후 모든 사진의 내장 메타데이터를 읽어 보고, 읽지 못한 사진이 있으면 파일 이름과 오류 코드를 출력하고 종료 코드 1로 끝난다 |
| b. 의류 태그 보충 | `backend/.venv/bin/python scripts/import_clothing_tags.py` | `data/demo/fixtures/vision.json`(현재 사진 바이트의 SHA-256 → `VisionAnalysis`)에 없는 사진만 `ex_img/clothing_tags.json`에서 채운다. 이미 있는 항목은 그대로 두고, 원본 값이 기존 항목과 다르면 바꾸지 않고 실패한다. 원본의 자유 표기 의류 이름(`t-shirt`, `utility vest` 등)은 표준 분류 값(`t_shirt`, `vest` 등)으로 바꾸고, 태그가 `UNKNOWN_TAGS`면 `non_outfit`, 아니면 `outfit`(판정 확신도 1.0)으로 적는다. `clothing_tags.json`의 `sha256`(메타데이터를 넣기 전 파일의 해시)은 `vision.sources.json`의 `source_sha256`으로만 남긴다. 누락, 검증 실패, 표준 값이 없는 이름, 기존 항목 변경 시도가 있으면 아무것도 쓰지 않고 종료 코드 1 |
| c. 날씨 기록 (네트워크 필요) | `backend/.venv/bin/python scripts/record_fixture_weather.py` | 사진 내장 메타데이터의 (날짜, 좌표) 묶음마다 Open-Meteo Archive API를 1회 호출해 `data/demo/fixtures/weather.json`을 기록값으로 바꾼다. 한 장이라도 실패하면 값을 지어내지 않고 기존 파일을 그대로 둔 채 종료 코드 1 |
| d. 시나리오 선택 | `backend/.venv/bin/python scripts/select_scenarios.py` | 기록 기온으로 시나리오 4개의 기온, `weather.json`의 `today_default`, 시드 투표 사이드카(기존 `*.metadata.json`을 지우고 다시 씀), `scenario_expectations.json`을 쓴다. 규칙 위반이면 아무것도 쓰지 않고 종료 코드 1 |

3. 확인: `backend/.venv/bin/python scripts/select_scenarios.py --check`가 `ok: 189 photos, current scenario files satisfy S1~S4 and V1~V4`와 시나리오별 결과를 출력하고 종료 코드 0으로 끝나야 한다. `--check`는 파일을 쓰지 않는다.

받은 `ex_img/`가 저장소의 데이터를 만들 때와 같은 사진이면 b~d의 결과는 이미 저장소에 있다. 이때는 a만 실행하고 3번 확인으로 넘어가면 되고, 네트워크가 필요한 c는 실행하지 않는다. 사진이 바뀌었으면 a~d를 순서대로 다시 실행한다. c를 다시 실행하면 기록 기온이 바뀌어 시나리오 기온과 아래 데모 절차의 숫자도 바뀔 수 있다.

`scripts/build_fixtures.py`는 Phase 1 변경 전의 고정 기온 표로 `weather.json`, 사이드카, `scenarios.json`, `vision.json`을 다시 쓰는 스크립트라서 지금 데모 데이터 준비에는 쓰지 않는다. `weather.json`에 `recorded` 블록(기록 날씨나 플레이스홀더)이 있으면 빌드와 `--check` 모두 아무것도 쓰지 않고 종료 코드 1로 끝난다. `vision.json`의 159개 항목은 이 스크립트의 손으로 확인한 태그 표에서 만들었고, 나머지 15장(`126_…`~`140_…`)은 b가 보충했다.

권한이 확인된 사진이 0장일 때만 `backend/.venv/bin/python scripts/make_placeholders.py`로 플레이스홀더 8장(내장 메타데이터 포함)과 vision fixture, `weather.json` 항목을 만든다. 확인된 사진이 있으면 이 스크립트는 실행을 거부한다. 현재 데모는 플레이스홀더를 쓰지 않는다.

#### 사진 출처와 사용 권한

- 기존 174장은 프로젝트 소유자가 제공했고 manifest에 사용 권한이 확인되어 있다. 126~140번 15장도 착장·날씨 fixture가 있다.
- 추가한 141~155번 15장은 사용자 요청으로 OpenAI image_gen에서 생성한 가상 성인 인물의 착장이다. 한파·무더위·폭염용으로 각각 5장씩이며, 생성 프롬프트·SHA-256·출처를 `data/demo/generated_outfits.json`에 보관한다.
- 생성 사진의 촬영 날짜·위치·기온은 데모를 위해 작성한 가상 데이터다. 실제 관측값이나 착용자의 체감 경험을 뜻하지 않으며, 체감 피드백은 미리 넣지 않았다. 기존 live 평가용 `data/eval/labels.json`에는 추가하지 않았다.
- JPG 40장 중 20장은 착장이 아닌 사진으로, 추천에서 제외된다.

### 5분 데모 절차 (Fixture 모드, API 키 없음)

이 절차는 `backend/tests/test_integration.py`의 `test_five_minute_demo_walkthrough`가 같은 순서로 API를 호출해 확인한다. 시나리오별 결과는 `data/demo/scenario_expectations.json`과 같다.

준비:

- 데모 사진 189장을 준비한다(위 절).
- 루트 `.env`가 없거나 `VISION_PROVIDER=fixture`, `WEATHER_PROVIDER=fixture`이어야 한다. 키를 넣어 둔 상태면 1단계의 API 키 상태만 "설정됨"으로 보이고 나머지는 같다.
- DB가 비어 있어야 한다. 이미 스캔한 적이 있으면 새 DB 파일로 백엔드를 실행한다: `DATABASE_URL=sqlite:///./data/demo-run.db .venv/bin/uvicorn app.main:app --port 8000`.
- 백엔드와 프론트엔드(`npm run dev`)를 실행하고 `http://localhost:5173`을 연다.

| 단계 | 동작 | 화면에서 확인할 결과 |
|---|---|---|
| 1. 상태 확인 | 홈의 "데모 관리 화면으로" 링크 또는 하단 "데모" 탭을 누른다 | "현재 상태"에 Vision 제공자 `fixture`, 날씨 제공자 `fixture`, Gemini API 키 "미설정", 착장 0 |
| 2. 폴더 스캔 | "폴더 스캔" 버튼을 누른다 | 스캔 189, 성공 169, 비착장 20, uncertain 0, 중복 0, 실패 0 |
| 3. 기록 화면 | 하단 "기록" 탭을 누르고 첫 항목을 누른다 | 착장 169건이 촬영 시각 최신순 그리드로 보이고, 첫 항목 상세 화면에 사진이 뜬다 |
| 4. 시나리오 선택 | "데모" 탭으로 돌아가 날씨 시나리오 목록에서 "쌀쌀한 아침"을 누른다 | 시나리오 4개(쌀쌀한 아침, 기록적 한파, 때 이른 무더위, 기록적 폭염)가 보이고, "적용된 시나리오: 쌀쌀한 아침 · 14.5°C" |
| 5. 오늘 추천 | "홈" 탭을 누른다 | 오늘 14.5°C(출처: 시나리오). 1순위 추천이 `015_loose_hair_front_cream.png`(과거 14.4°C) 114.0점이고 근거에 "과거에 적당했음"이 있다. 상위 5건은 `data/demo/scenario_expectations.json`과 같다 |
| 6. 체감 피드백 | 알림 카드의 체감 버튼(예: "🥵 더웠어")을 누른다 | 확인 단계 없이 저장되고 누른 버튼이 선택 상태로 바뀐다 |
| 7. 추천 재조회 | 기다린다(새로고침 불필요) | 추천이 자동으로 다시 계산되고, 바뀐 체감이 점수에 바로 반영된다. "기록" 탭에서 그 착장의 평가 상태도 바뀐다 |
| 8. 재스캔 | "데모" 탭에서 "폴더 스캔"을 다시 누른다 | 스캔 189, 성공 0, 중복 169, 비착장 20, uncertain 0, 실패 0. 착장 수는 169 그대로 |

"기록적 한파"(-16.0°C)는 기온 차이가 정확히 3.0°C인 후보 `142_cold_cream_down_denim` 1건(85.0점), "때 이른 무더위"(44.0°C)는 5.0°C 범위 확장으로 `153_heatwave_coral_tank_shorts`(50.0점)와 `155_heatwave_white_tee_linen_skirt`(45.0점) 2건, "기록적 폭염"(48.0°C)은 데이터 부족이다. 시나리오 기온은 `select_scenarios.py`가 189장 전체에 규칙 S1~S4를 적용해 고른다.

### 데이터와 제공자에 관한 사실

- **촬영 시각과 위치**: 사진마다 내장된 합성(synthetic) EXIF 메타데이터에서 읽는다. 1순위는 Exif IFD의 UserComment에 든 JSON(`date`, `time`, `utc_offset` 또는 `timezone`, `latitude`, `longitude`, `synthetic`)이고, 2순위는 표준 EXIF(`DateTimeOriginal` + `OffsetTimeOriginal`, GPS IFD)다. 데모 PNG 134장은 `eXIf` 청크에 UserComment JSON이 있고, JPG 40장은 UserComment JSON과 같은 값의 표준 EXIF도 담고 있다. UserComment JSON의 다른 키(`location`, `outfit`, `prompt` 등)는 저장하지 않는다. 데모 사진의 시각(2024-12-11~2025-12-17)과 위치(서울 시내 몇 곳)는 실제 촬영 기록이 아닌 가상 값이라 화면에 "가상 데이터" 라벨이 붙고, 상세 화면에는 "촬영 정보: 사진 내장 메타데이터"가 보인다.
- **사이드카**: 같은 이름의 `*.metadata.json`은 선택 파일이며 두 가지에만 쓴다. 내장 메타데이터가 없거나 잘못된 사진의 대체 출처(`captured_at`, `latitude`, `longitude`, 상세 화면 "촬영 정보: 사이드카 파일")와 사전 체감 피드백(`thermal_vote`, `demo_seed`로 저장)이다. 내장 메타데이터가 유효하면 사이드카의 시각·위치는 무시한다. 사이드카 JSON이 깨져 있으면 내장 메타데이터가 있어도 그 사진은 실패로 집계한다. 데모 세트의 시드 투표 66개는 `select_scenarios.py`가 시드 투표 규칙으로 만든 사이드카의 `thermal_vote`다(적당했어 23, 추웠어 25, 더웠어 18), Non_Outfit_Photo에는 사이드카가 없다.
- **Fixture 날씨**: `data/demo/fixtures/weather.json`의 사진별 값은 `scripts/record_fixture_weather.py`가 사진 내장 메타데이터의 시각·위치로 Open-Meteo Archive API(Historical Weather API)에서 한 번 기록한 재분석값이다. 기록 날짜와 출처는 같은 파일의 `recorded` 블록에 있다(`recorded_on: 2026-11-14`, 174건). 생성 착장 15장(141~155)의 항목은 기록값이 아니라 `generated_outfits.json`의 작성 값이며 항목마다 `synthetic: true`로 표시한다(`recorded.synthetic_entries`). 실제 관측값이 아닌 제공자 추정치이고, 앱은 `provider=fixture`로 저장해 화면에 "fixture" 배지를 붙인다. 기록한 변수가 `temperature_2m`, `precipitation`, `relative_humidity_2m`뿐이라 사진별 체감온도와 풍속은 비어 있다. 항목이 없는 시각·위치는 고정 계절 공식 값으로 대체한다.
- **시나리오**: `data/demo/scenarios.json`의 4개 기온(14.5°C, -16.0°C, 44.0°C, 48.0°C)은 `select_scenarios.py`가 기록 기온에서 규칙 S1~S4(정상 결과 5건 이상, 기온 차이 정확히 3.0°C인 후보 1건, 5.0°C 범위 확장, 데이터 부족)를 만족하도록 고른 값이다. 실제 날씨가 아니며 체감온도는 비워 두었다. `weather.json`의 `today_default`는 "쌀쌀한 아침"과 같은 값이다.
- **mock Vision**: Fixture 모드의 판정 결과(`VisionAnalysis`: 사진 유형 `outfit`/`non_outfit`/`uncertain`, 판정 확신도, 이유, 의류 태그)는 `data/demo/fixtures/vision.json`에서 사진의 SHA-256으로 찾는다. 189개 항목은 착장 169, 비착장 20이다. 생성 착장 15개(141~155)는 `data/demo/generated_outfits.json`의 값이다. 159개는 `scripts/build_fixtures.py`의 태그 표에서, 15개(`126_…`~`140_…`)는 `ex_img/clothing_tags.json`에서 `scripts/import_clothing_tags.py`로 보충했으며, 두 출처의 태그는 표준 분류로 바꾸면 174장 모두 같다. 의류 종류는 정해진 표준 분류 값(`knit`, `long_puffer`, `trousers` 등)만 쓴다. 등록되지 않은 사진은 `uncertain`(검토 필요)으로 집계되고 저장되지 않는다. 화면에는 "가상 데이터" 라벨이 붙는다.
- **착장 아닌 사진은 수집 단계에서 제외**: Vision 판정이 `non_outfit`이면 스캔은 그 사진을 착장으로 저장하지 않고 비착장 제외(`skipped_non_outfit`)로 집계한다. `uncertain`이거나 판정 확신도가 0.70 미만이면 검토 필요(`uncertain`)로 집계하고 역시 저장하지 않는다. 두 경우 모두 스캔 결과의 "저장하지 않은 사진"(`decisions`)에 파일 이름, 판정, 확신도, 이유가 남고, 촬영 메타데이터와 날씨는 읽지 않는다. 데모 세트의 JPG 20장이 비착장 제외에 해당하므로 기록 화면에도 추천 후보에도 없다. 추천 쪽 안전장치도 남아 있어서, 세 의류 타입이 모두 `unknown`이고 confidence가 0.0인 착장 기록은 후보에서 빠지고 후보 0건·5.0°C 확장 판단에도 세지 않는다. 알고리즘 버전 문자열은 요구사항대로 `rule_based_v1`이다.
- **Open-Meteo Archive**: Live 날씨는 `https://archive-api.open-meteo.com/v1/archive`에서 `temperature_2m`, `precipitation`, `relative_humidity_2m`을 받는다. 이 값은 재분석(reanalysis) 기반 추정치라서 화면에 "실측값이 아닌 제공자 추정치/재분석값입니다"를 표시한다. Archive 데이터는 최근 며칠이 비어 있을 수 있어서, 오늘 날씨와 최근 사진의 날씨는 Live 모드에서도 fixture로 대체될 수 있다. 대체되면 `is_fixture=true`로 저장되고 fixture 배지가 붙는다.
- **자정 근처 시각**: Archive 요청은 촬영 시각을 `APP_TIMEZONE`으로 바꾼 날짜 하루(`start_date = end_date`)만 조회한다. 그래서 23:30 사진은 다음 날 00:00이 아니라 같은 날 23:00 값이 선택된다. 같은 날 안에서는 가장 가까운 시각을 고르고, 차이가 같으면 이른 시각을 고른다.
- **Gemini 호출과 EXIF 제거**: 공식 SDK `google-genai`의 `genai.Client(enterprise=True, api_key=...)`로 Agent Platform express mode를 호출한다. 보내기 전에 이미지를 픽셀만으로 다시 인코딩해 EXIF(GPS와 UserComment 포함), XMP, ICC, PNG 텍스트 청크를 모두 지운다. 지우지 못하면 원본을 보내지 않고 분석 실패로 처리한다. 요청에는 이미지와 고정 프롬프트만 들어가고 파일 이름·촬영 시각·위치·사이드카 값은 들어가지 않는다. 모델 ID와 express mode 지원 여부는 "API 키 설정" 절에 있다.
- **Live Vision은 끝까지 검증하지 못했다**: Phase 1 변경 전 Gemini Developer API(AI Studio 키) 경로로 보낸 사진 분석 요청은 HTTP 402(선불 크레딧 소진)로 실패했다. Agent Platform express mode로 바꾼 뒤 실제 사진을 분석한 결과도 확인 기록이 없다. Gemini 경로는 가짜 클라이언트를 쓰는 pytest로만 검증했다.
- **Live 모드 첫 스캔**: 189장을 처음 스캔하면 Gemini를 189회 호출한다. 데모 관리 화면은 30초가 지나면 실패로 표시하지만 서버 스캔은 끝까지 진행된다. 발표 전에 미리 한 번 스캔해 두면 발표 중 재스캔은 착장은 중복, 비착장은 기억한 판정으로 처리되어 바로 끝난다. 검토 필요로 판정된 사진만 다시 분석한다.

### 테스트

비전 정확도 평가(실제 사진 기반, opt-in)는 [docs/evaluation.md](docs/evaluation.md)를 본다. `python scripts/evaluate_vision.py`가 Fixture로 파이프라인을 점검하고, `--provider gemini`는 키가 있을 때만 live로 측정한다.

백엔드:

```bash
cd backend && .venv/bin/python -m pytest -q
```

프론트엔드:

```bash
cd frontend
npx vitest --run
npm run typecheck
npm run check:bundle
```

통과 기준: 모든 명령이 실패 0건으로 끝나고 종료 코드가 0이다.

pytest 자동화 테스트 영역(Req 17.4)과 대표 테스트:

| 영역 | 파일 | 대표 테스트 |
|---|---|---|
| (a) 수집과 중복 제거 | `backend/tests/test_ingestion.py` | `test_rescan_same_hash_creates_no_outfit` |
| (b) 날씨 정규화 | `backend/tests/test_weather.py` | `test_archive_mapping_example` |
| (c) 허용 오차 경계값 | `backend/tests/test_recommendation.py` | `test_diff_exactly_3_0_included`, `test_fallback_boundary_5_0_included_5_1_excluded` |
| (d) 데이터 부족 처리 | `backend/tests/test_recommendation.py` | `test_insufficient_data_status` |
| (e) 피드백 갱신 | `backend/tests/test_feedback.py` | `test_latest_vote_becomes_effective` |
| (f) API 상태 코드 | `backend/tests/test_api.py` | `test_feedback_201`, `test_feedback_422_variants`, `test_get_outfit_404` |
| (g) 내장 메타데이터 읽기 | `backend/tests/test_metadata.py` | `test_user_comment_json_parsed`, `test_standard_exif_jpeg_parsed`, `test_user_comment_preferred_over_standard_exif`, `test_sidecar_fallback_when_no_exif`, `test_no_metadata_fails_metadata_not_found`, `test_broken_sidecar_fails_even_with_exif` |

- 테스트는 API 키 없이 돈다. `backend/tests/conftest.py`가 테스트마다 `GEMINI_API_KEY`를 환경에서 지우고 localhost 외 네트워크 연결을 막는다. Open-Meteo는 `respx`, Gemini는 가짜 클라이언트로 대체한다. pytest는 `scripts/record_fixture_weather.py`와 `scripts/check_gemini_model.py`를 실행하지 않는다.
- 이 README 작성 시점 결과(2026-11-14, 데모 사진 189장 준비 상태, PR #6·#7·#8·#10 병합 후): 백엔드 602 passed, 프론트엔드 11개 파일 92 tests passed, typecheck 오류 0, check:bundle OK, `select_scenarios.py --check` 통과.
- 데모 사진이 없으면(새로 클론한 직후) 사진 파일을 읽는 테스트(`test_integration.py`의 데모 사진 테스트, `test_api_routes.py::test_demo_data_end_to_end`, `test_image_check.py::test_all_demo_photos_pass_check_image`, `test_metadata.py::test_user_comment_json_parsed_from_real_ex_img_photo` 등)는 skip된다. 전체를 확인하려면 먼저 데모 사진을 준비한다.
- 데모 데이터 파일이 시나리오·시드 투표 규칙을 지키는지는 pytest와 별도로 `backend/.venv/bin/python scripts/select_scenarios.py --check`로 확인한다(위 "데모 사진 준비").

### 의존성 고정

`backend/requirements.txt`와 `frontend/package.json`의 직접 의존성은 모두 `==`(npm은 정확한 버전)으로 고정했고, 위 테스트가 통과한 버전이다. 설계 문서의 후보 버전과 다른 점은 다음과 같다.

- TypeScript는 7.0.2 대신 5.9.3을 쓴다. `openapi-typescript` 7.13.0의 peer 의존성 범위가 TypeScript `^5.x`이기 때문이다.
- `sqlalchemy`(2.1.8)와 `starlette`(1.10.0)는 `sqlmodel`·`fastapi`의 전이 의존성이지만 앱 코드가 직접 import하므로 직접 의존성으로 고정했다.

### 기능 상태

#### 완료 (pytest/vitest 통과 또는 5분 데모에서 재현)

- 사진 폴더 스캔: 확장자 필터, 20MB 제한, 이미지 디코딩 검증, SHA-256 중복 제거, 재스캔 멱등성, 파일 단위 실패 집계, 스캔 중복 실행 거부(409)
- 사진 내장 메타데이터 읽기: UserComment JSON → 표준 EXIF → 사이드카 대체 순서, 누락·형식 오류 처리, 메타데이터 출처(`exif`/`demo_sidecar`)와 합성 여부 저장, 상세 화면 "촬영 정보" 표시
- 사이드카 파싱·검증(시드 투표와 대체 출처)과 `demo_seed` 체감 피드백 저장
- 의류 분류 표준화: 상의·겉옷·하의 종류를 정해진 표준 값(`UpperType`/`OuterType`/`LowerType`)으로만 저장하고, 기존 DB의 옛 표기는 기동 시 표준 값으로 옮긴다
- 수집 단계 사진 분류 게이트: Vision 판정 `outfit`만 착장으로 저장, `non_outfit`은 비착장 제외, `uncertain`·확신도 0.70 미만은 검토 필요로 집계하고 "저장하지 않은 사진"으로 보고
- Fixture Vision(해시별 고정 `VisionAnalysis`, 미등록 사진은 `uncertain`)
- Gemini Vision Provider 코드 경로: Agent Platform express mode 클라이언트(`projects/...` 모델은 global 엔드포인트), 전송 전 EXIF·위치 제거, 스키마 검증, API 오류·시간 초과 처리, 요청에 이미지와 프롬프트만 포함(가짜 클라이언트 테스트)
- 날씨 연결: Fixture 날씨(Open-Meteo 기록값 형식, null 값 보존, 계절 공식 대체), Open-Meteo Archive 요청·응답 매핑과 실패 시 fixture 대체(`respx` 테스트), 가장 가까운 시각 선택, 단위 정규화
- 날씨 기록 스크립트의 묶기·항목 생성(`respx` 테스트)과 사진 복사·플레이스홀더 스크립트
- 데모 시나리오·시드 투표 규칙(`backend/app/demo_scenarios.py`): 실제 데이터에 대한 S1~S4·V1~V3 검사(`test_demo_data.py::test_demo_scenarios_satisfy_rules`)와 `scripts/select_scenarios.py --check`
- 오늘 날씨와 시나리오 선택(선택 유지, 알 수 없는 시나리오 거부)
- `rule_based_v1` 추천: 의류 근거가 없는 기록(세 타입 모두 `unknown`, confidence 0.0) 후보 제외 안전장치, 허용 오차 3.0°C 경계 포함, 5.0°C 1회 확장, 데이터 부족 상태, 피드백 가중 점수, 동점 처리, 설명 문장, 신뢰도 수준
- 체감 피드백 저장(이력 보존, 최신 기록이 Effective_Vote, 422/404/500 처리)과 다음 추천에 즉시 반영
- REST API와 OpenAPI 문서, 사진 엔드포인트(경로 비노출), CORS 정확 일치
- 설정 로더(기본값 대체, 잘못된 값 로그)와 API 키 마스킹, 번들 비밀값 검사
- 모바일 웹: 홈(오늘 날씨, 알림 카드, 추천), 기록 그리드, 상세, 데모 관리 화면(vitest smoke 테스트와 5분 데모 절차)

#### 미완료 또는 미검증

- Live Gemini Vision 분석: express mode로 실제 사진을 분석한 결과를 확인하지 못했다(위 "데이터와 제공자에 관한 사실").
- Live Open-Meteo 호출: 앱의 `WEATHER_PROVIDER=open_meteo` 경로는 `respx`로 응답 형태만 테스트했다. 같은 요청 파라미터를 쓰는 `scripts/record_fixture_weather.py`는 2026-11-14에 실제 Archive API로 174건을 기록했다(`weather.json`의 `recorded`).
- 360~430px 뷰포트에서 가로 스크롤이 없는지 수동 확인(Req 11.6)을 하지 않았다.
- 실제 휴대폰 기기에서 확인하지 않았다.
- 화면 문구 한국어 검수(`frontend/src/lib/strings.ts`)를 별도로 하지 않았다.
- 선택 항목(`tasks.md`에서 `*` 표시)인 property-based test(Hypothesis, fast-check)와 화면별 상세 UI 테스트, 저장소 위생 테스트는 구현하지 않았다. 대신 단위·smoke·통합 테스트가 있다.

### Phase 2 개선 목록

- **실제 갤러리 접근**: 지금은 `PHOTO_DIR` 폴더에 미리 넣어 둔 데모 사진(합성 메타데이터)을 스캔한다. Phase 2에서는 사용자 동의를 받은 휴대폰 갤러리 업로드로 실제 촬영 사진을 받는다. 실제 사진의 위치는 개인정보이므로 저장 범위와 삭제 방법을 함께 설계해야 한다.
- **Web Push 알림**: 지금의 앱 내부 알림 카드를 Web Push(FCM 등) 실제 알림으로 바꿔, 앱을 열지 않아도 아침에 추천과 체감 기록 버튼을 받게 한다.
- **추천 엔진 교체**: `RecommendationStrategy` 인터페이스(`recommend(today, candidates)`)의 새 구현체를 만들고 팩토리(`build_strategy`)에서 고르면 API와 화면을 바꾸지 않고 학습 기반 추천 등으로 교체할 수 있다.
- **SQLite에서 다른 저장소로 이전하는 조건**: 다중 사용자 계정, 여러 서버 프로세스나 워커가 동시에 쓰는 배포, 사용자당 수천 건 이상으로 늘어난 기록 중 하나라도 생기면 PostgreSQL 같은 서버형 DB로 옮긴다. 이전은 `OutfitRepository` 구현과 `DATABASE_URL`만 바꾸는 범위로 끝나도록 저장소 경계를 유지한다.

</details>
