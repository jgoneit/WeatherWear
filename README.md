# WeatherWear

WeatherWear는 저장해 둔 착장 사진에 촬영 당시 날씨를 붙여 두고, 오늘과 기온이 비슷했던 날의 과거 착장을 그때 남긴 체감 피드백(추웠어 / 적당했어 / 더웠어)으로 가중해 추천하는 모바일 브라우저용 웹 앱이다. Kirothon 6시간 데모용 Phase 1(MVP)이며, 머신러닝 없이 규칙 기반 추천(`rule_based_v1`)만 쓴다.

- 백엔드: FastAPI + SQLModel + SQLite (`backend/`)
- 프론트엔드: React + Vite + TypeScript + Tailwind v4 (`frontend/`)
- 데모 데이터: `data/demo/` (사이드카 메타데이터, 시나리오, fixture)
- 보조 스크립트: `scripts/`

기본 설정은 Fixture 모드라서 API 키와 외부 네트워크 없이 기동, 스캔, 추천, 피드백, 테스트가 모두 동작한다.

라이선스: [LICENSE](LICENSE) (MIT)

## 요구 환경

- Python 3.10 이상 (개발·검증 환경: Python 3.14.8)
- Node.js와 npm (개발·검증 환경: Node 26.12.1, npm 11.20.0)

## 백엔드 실행

처음 한 번 가상 환경을 만들고 의존성을 설치한다.

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

서버 실행(`backend/`에서):

```bash
.venv/bin/uvicorn app.main:app --port 8000
```

- 설정은 저장소 루트의 `.env`에서 읽는다. 백엔드를 `backend/`에서 실행해도 루트 `.env`를 읽는다.
- `PHOTO_DIR`, `DATABASE_URL`의 상대 경로는 저장소 루트 기준으로 해석한다. 기본 DB 파일은 `data/weatherwear.db`이고 기동 시 자동으로 만든다.
- 동작 확인: `http://localhost:8000/api/meta`가 `{"app_timezone":"Asia/Seoul","algorithm_version":"rule_based_v1",...}`를 반환한다. API 문서는 `http://localhost:8000/docs`에 있다.

## 프론트엔드 실행

```bash
cd frontend
npm ci
VITE_API_BASE_URL=http://localhost:8000 npm run dev
```

- 브라우저에서 `http://localhost:5173`을 연다. 휴대폰 크기로 보려면 개발자 도구의 기기 모드(360~430px)를 쓴다.
- Vite는 `frontend/` 폴더의 env 파일만 읽고 저장소 루트 `.env`는 읽지 않는다. 매번 변수를 붙이기 싫으면 `frontend/.env.local`에 `VITE_API_BASE_URL=http://localhost:8000` 한 줄을 넣는다(`*.local`은 git에서 제외된다). 값이 없으면 프론트엔드는 같은 출처(`localhost:5173`)로 요청하므로 API 호출이 실패한다.
- 데모 관리 화면(`/demo`)과 하단 "데모" 탭은 `npm run dev`(개발 모드)에서만 보인다.

## Docker Compose 실행

백엔드, 프론트엔드, 데이터 볼륨을 한 번에 실행한다. 기본은 Fixture 모드라 API 키가 필요 없다.

```bash
docker compose up --build        # 빌드 후 실행. -d를 붙이면 백그라운드, --wait를 붙이면 health check 통과까지 대기
docker compose ps                # 두 서비스가 (healthy)여야 한다
curl localhost:8000/api/meta     # 백엔드 확인. 프론트엔드는 http://localhost:5173
docker compose down              # 종료. SQLite 볼륨(db-data)은 유지한다
docker compose down -v           # 종료와 함께 DB 볼륨 삭제
```

- 백엔드(`backend/Dockerfile`)와 프론트엔드(`frontend/Dockerfile`)는 멀티스테이지 빌드이고 비루트 사용자로 실행한다. 백엔드는 `/api/meta`, 프론트엔드는 `/healthz`로 health check 한다.
- SQLite는 이름 있는 볼륨 `db-data`(`/data/db`)에 저장한다. 사진 폴더는 호스트의 `./data/demo/photos`를 `/data/photos`에 읽기 전용으로 마운트한다. 사진이 없으면 스캔 결과가 0건이다(`scripts/prepare_demo_photos.py`로 먼저 복사). 다른 폴더를 쓰려면 `PHOTO_HOST_DIR=/절대/경로`를 지정한다.
- Gemini live 모드: 호스트 환경 변수 또는 `.env`에 `GEMINI_API_KEY`를 두고 `VISION_PROVIDER=gemini docker compose up --build`로 실행한다. 키는 컨테이너 실행 시 환경 변수로만 전달하며 이미지 레이어에 들어가지 않는다. 키를 `--build-arg`로 넘기지 않는다.
- 호스트 포트가 사용 중이면 `BACKEND_PORT`, `FRONTEND_PORT`로 바꾼다. 백엔드 포트를 바꾸면 브라우저가 호출할 주소도 빌드 시점에 맞춰야 하므로 `BACKEND_PORT=8010 VITE_API_BASE_URL=http://localhost:8010 docker compose up --build`처럼 함께 지정한다.
- 이미지에 키가 없는지 확인: `docker history --no-trunc weatherwear-backend | grep -c "$GEMINI_API_KEY"`가 0이어야 한다.

## 환경 변수

루트의 `.env.example`을 `.env`로 복사해 쓴다. 우선순위는 프로세스 환경 변수 > 루트 `.env` > 아래 기본값이다. 설정은 서버 기동 시 한 번 읽으므로 바꾼 뒤에는 서버를 다시 시작한다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `GEMINI_API_KEY` | (빈 값) | Gemini API 키. `VISION_PROVIDER=gemini`일 때만 필요하다 |
| `VISION_PROVIDER` | `fixture` | `fixture`(사전 정의 태그) 또는 `gemini`(Gemini API 호출) |
| `GEMINI_MODEL` | `gemini-4-hydrogen` | `VISION_PROVIDER=gemini`일 때 호출할 모델 ID |
| `WEATHER_PROVIDER` | `fixture` | `fixture`(고정 날씨) 또는 `open_meteo`(Open-Meteo Archive API, 키 불필요) |
| `DATABASE_URL` | `sqlite:///./data/weatherwear.db` | SQLite DB URL |
| `PHOTO_DIR` | `./data/demo/photos` | 스캔할 사진 폴더. 최상위의 `.jpg/.jpeg/.png/.webp`와 같은 이름의 `*.metadata.json`을 읽는다 |
| `APP_TIMEZONE` | `Asia/Seoul` | 날짜·시각 표시와 Open-Meteo 조회에 쓰는 IANA 타임존 |
| `TEMPERATURE_TOLERANCE_C` | `3` | 유사 기온 허용 오차(°C). 0 초과 5.0 미만, 비우면 3.0 |
| `DEFAULT_LATITUDE` | `37.5665` | 오늘 날씨 조회 위도(서울) |
| `DEFAULT_LONGITUDE` | `126.9780` | 오늘 날씨 조회 경도(서울) |
| `CORS_ORIGINS` | `http://localhost:5173` | CORS 허용 출처. 쉼표로 구분 |
| `VITE_API_BASE_URL` | `http://localhost:8000` | 프론트엔드가 호출할 백엔드 URL. 프론트엔드(Vite)만 읽는다 |

잘못된 값(허용 목록 밖 provider 이름, 범위 밖 숫자 등)은 기동을 막지 않는다. 로그에 변수 이름과 허용값만 남기고 기본값을 쓴다.

## API 키 설정

1. 루트 `.env`에 `GEMINI_API_KEY=<발급받은 키>`와 `VISION_PROVIDER=gemini`를 적는다.
2. 키는 루트 `.env`에만 둔다. `.env`는 `.gitignore`에 들어 있어 커밋되지 않는다. `.env.example`의 `GEMINI_API_KEY`는 빈 값으로 둔다.
3. 키를 `VITE_`로 시작하는 변수에 넣지 않는다. `VITE_` 변수는 브라우저 번들에 그대로 들어간다. 프론트엔드는 `VITE_API_BASE_URL`만 읽고, `npm run check:bundle`이 빌드 결과물에 `GEMINI` 문자열이나 실제 키 값이 없는지 검사한다.
4. 모델 ID 확인(선택): `backend/.venv/bin/python scripts/check_gemini_model.py`. 키가 있을 때만 `models.get`을 1회 호출하고 모델 ID만 출력한다.

백엔드는 키를 로그, 오류 메시지, API 응답에 출력하지 않는다. 데모 관리 화면에는 "설정됨" 또는 "미설정"만 보인다. `VISION_PROVIDER=gemini`인데 키나 모델이 비어 있으면 누락된 변수 이름만 로그에 남기고 Fixture Vision으로 기동한다.

## Fixture 모드와 Live 모드 전환

| 모드 | 설정 | 동작 |
|---|---|---|
| Fixture (기본) | `VISION_PROVIDER=fixture`, `WEATHER_PROVIDER=fixture` | 외부 호출 0건. 같은 데이터면 매번 같은 태그·날씨·추천 순위 |
| Live Vision | `VISION_PROVIDER=gemini` (+ `GEMINI_API_KEY`) | 새 사진 1장당 Gemini 호출 1회(30초 제한, 재시도 없음) |
| Live 날씨 | `WEATHER_PROVIDER=open_meteo` | Open-Meteo Archive API 호출(10초 제한). 실패하면 fixture 날씨로 대체 |

두 변수는 따로 바꿀 수 있다. 예: `VISION_PROVIDER=fixture WEATHER_PROVIDER=open_meteo .venv/bin/uvicorn app.main:app --port 8000`.

이미 스캔한 사진은 같은 SHA-256 해시라 다시 분석하지 않는다(duplicate). Fixture로 스캔한 DB에서 Live Vision으로 바꿔도 기존 태그는 그대로다. Live 결과를 보려면 `DATABASE_URL=sqlite:///./data/weatherwear-live.db`처럼 새 DB 파일을 지정한다.

## 데모 사진 준비

기존 개인 사진(`ex_img/`, `data/demo/photos/`의 174장)은 git에 올리지 않는다. 이번에 생성한 가상 인물 착장 15장(141~155번, 약 43MB)은 저장소에 포함되어 별도 다운로드 없이 사용할 수 있다. 저장소에는 사진별 사이드카(`data/demo/photos/*.metadata.json`), 목록(`data/demo/photos.manifest.json`), fixture(`data/demo/fixtures/`), 시나리오(`data/demo/scenarios.json`)만 들어 있다.

1. 전체 사진 세트를 쓰려면 프로젝트 소유자에게 받은 `ex_img/` 폴더를 저장소 루트에 둔다. 현재 목록은 기존 174장과 생성 착장 15장, 총 189장이다. 저장소에 포함된 15장만으로도 한파·무더위·폭염 추천을 실행할 수 있다.
2. `python3 scripts/prepare_demo_photos.py`로 manifest의 승인된 사진을 `data/demo/photos/`로 복사한다. 같은 파일과 기존 사이드카는 유지한다.
3. 생성 착장 15장의 데이터는 `python3 scripts/add_generated_outfits.py`로 병합한다. 기존 개인 사진의 fixture와 사용자 피드백은 유지하며, 생성 이미지 해시가 다르면 중단한다. 생성 프롬프트와 가상 기온은 `data/demo/generated_outfits.json`에 기록되어 있다.
4. 백엔드를 재시작하고 설정 화면에서 폴더를 스캔한다. `cd backend && ../.venv/bin/python -m pytest tests/test_integration.py tests/test_recommendation_service.py tests/test_api_routes.py tests/test_image_check.py -q`로 사진·추천·재스캔을 확인한다. (`.venv`가 backend 안에 있다면 경로를 조정한다.)

기존 `build_fixtures.py`는 처음 159장의 온도표와 경계/데이터 부족 시나리오를 위한 스크립트다. 확장된 사진 세트를 재생성하는 용도로 실행하지 않는다. 아직 라벨이 없는 기존 126~140번 15장은 계속 uncertain으로 제외된다.

생성 사진을 원본 폴더에도 보관하려면 저장소 루트에서 `python3 scripts/add_generated_outfits.py --export-to ~/Downloads/ex_img`를 실행한다. 생성 PNG 15장만 복사하며 다른 파일을 덮어쓰지 않는다.

권한이 확인된 사진이 0장일 때만 `backend/.venv/bin/python scripts/make_placeholders.py`로 플레이스홀더 8장과 사이드카, vision fixture를 만든다. 확인된 사진이 있으면 이 스크립트는 실행을 거부한다. 현재 데모는 플레이스홀더를 쓰지 않는다.

### 사진 출처와 사용 권한

- 기존 174장은 프로젝트 소유자가 제공했고 manifest에 사용 권한이 확인되어 있다. 126~140번 15장은 착장·날씨 fixture가 아직 없다.
- 추가한 141~155번 15장은 사용자 요청으로 OpenAI image_gen에서 생성한 가상 성인 인물의 착장이다. 한파·무더위·폭염용으로 각각 5장씩이며, 생성 프롬프트·SHA-256·출처를 `data/demo/generated_outfits.json`에 보관한다.
- 생성 사진의 촬영 날짜·위치·기온은 데모를 위해 작성한 가상 데이터다. 실제 관측값이나 착용자의 체감 경험을 뜻하지 않으며, 체감 피드백은 미리 넣지 않았다. 기존 live 평가용 `data/eval/labels.json`에는 추가하지 않았다.
- JPG 40장 중 20장은 착장이 아닌 사진으로, 추천에서 제외된다.

## 5분 데모 절차 (Fixture 모드, API 키 없음)

이 절차는 `backend/tests/test_integration.py`의 `test_five_minute_demo_walkthrough`가 같은 순서로 API를 호출해 확인한다.

준비:

- 데모 사진 189장을 준비한다(위 절).
- 루트 `.env`가 없거나 `VISION_PROVIDER=fixture`, `WEATHER_PROVIDER=fixture`이어야 한다. 키를 넣어 둔 상태면 1단계의 API 키 상태만 "설정됨"으로 보이고 나머지는 같다.
- DB가 비어 있어야 한다. 이미 스캔한 적이 있으면 새 DB 파일로 백엔드를 실행한다: `DATABASE_URL=sqlite:///./data/demo-run.db .venv/bin/uvicorn app.main:app --port 8000`.
- 백엔드와 프론트엔드(`npm run dev`)를 실행하고 `http://localhost:5173`을 연다.

| 단계 | 동작 | 화면에서 확인할 결과 |
|---|---|---|
| 1. 상태 확인 | 홈의 "데모 관리 화면으로" 링크 또는 하단 "데모" 탭을 누른다 | "현재 상태"에 Vision 제공자 `fixture`, 날씨 제공자 `fixture`, Gemini API 키 "미설정", 착장 0 |
| 2. 폴더 스캔 | "폴더 스캔" 버튼을 누른다 | 스캔 189, 성공 154, 비착장 20, uncertain 15, 중복 0, 실패 0 |
| 3. 기록 화면 | 하단 "기록" 탭을 누르고 첫 항목을 누른다 | 착장 154건이 촬영 시각 최신순 그리드로 보이고, 첫 항목 상세 화면에 사진이 뜬다 |
| 4. 시나리오 선택 | "데모" 탭으로 돌아가 날씨 시나리오 목록에서 "쌀쌀한 아침"을 누른다 | 시나리오 4개(쌀쌀한 아침, 기록적 한파, 때 이른 무더위, 기록적 폭염)가 보이고, "적용된 시나리오: 쌀쌀한 아침 · 9.0°C" |
| 5. 오늘 추천 | "홈" 탭을 누른다 | 오늘 9.0°C(출처: 시나리오). 알림 카드와 1순위 추천이 `081_hiphop_varsity_baggy_denim`(바시티 재킷 + 배기 데님) 115.0점이고, 근거에 "과거에 적당했음"이 있다. 알림 카드에서 "😊 적당했어"가 선택 상태다 |
| 6. 체감 피드백 | 알림 카드의 "🥵 더웠어"를 누른다 | 확인 단계 없이 저장되고 "🥵 더웠어"가 선택 상태로 바뀐다 |
| 7. 추천 재조회 | 기다린다(새로고침 불필요) | 추천이 자동으로 다시 계산된다. 081은 85.0점으로 떨어져 상위 5건에서 빠지고, 1순위가 `052_burgundy_sweater_skirt_full`(버건디 스웨터 + 스커트) 110.0점으로 바뀐다. "기록" 탭에서 081의 평가 상태는 "더웠어" |
| 8. 재스캔 | "데모" 탭에서 "폴더 스캔"을 다시 누른다 | 스캔 189, 성공 0, 중복 154, 비착장 20, uncertain 15, 실패 0. 착장 수는 154 그대로 |

"기록적 한파"(−12.0°C), "때 이른 무더위"(30.0°C), "기록적 폭염"(38.0°C)에서도 각각 기온 차이 3.0°C 이내의 추천 5개가 나온다. 범위 확장·데이터 부족 동작은 독립적인 합성 데이터 단위 테스트로 검증한다.

## 데이터와 제공자에 관한 사실

- **촬영 시각과 위치**: 실제 EXIF가 아니라 사진마다 같은 이름의 `*.metadata.json` 사이드카(가상 메타데이터)에서 읽는다. 수집 서비스는 EXIF를 읽지 않는다. 화면에는 "가상 데이터" 라벨이 붙는다.
- **Fixture 날씨**: Fixture 모드의 날씨는 `data/demo/fixtures/weather.json`의 고정 값이다. 사진별 기온은 착장 단서와 촬영 날짜로 정한 값이며 실제 관측값이 아니다. 화면에는 "fixture" 배지가 붙는다.
- **mock Vision**: Fixture 모드의 의류 태그는 `data/demo/fixtures/vision.json`에서 사진의 SHA-256으로 찾는다. 태그 값은 사람이 사진을 하나씩 보고 적었다. 등록되지 않은 사진은 `unknown` 태그로 저장된다. 화면에는 "가상 데이터" 라벨이 붙는다.
- **Open-Meteo Archive**: Live 날씨는 `https://archive-api.open-meteo.com/v1/archive`에서 `temperature_2m`, `precipitation`, `relative_humidity_2m`을 받는다. 이 값은 재분석(reanalysis) 기반 추정치라서 화면에 "실측값이 아닌 제공자 추정치/재분석값입니다"를 표시한다. Archive 데이터는 최근 며칠이 비어 있을 수 있어서, 오늘 날씨와 최근 사진의 날씨는 Live 모드에서도 fixture로 대체될 수 있다. 대체되면 `is_fixture=true`로 저장되고 fixture 배지가 붙는다.
- **자정 근처 시각**: Archive 요청은 촬영 시각을 `APP_TIMEZONE`으로 바꾼 날짜 하루(`start_date = end_date`)만 조회한다. 그래서 23:30 사진은 다음 날 00:00이 아니라 같은 날 23:00 값이 선택된다. 같은 날 안에서는 가장 가까운 시각을 고르고, 차이가 같으면 이른 시각을 고른다.
- **Gemini 모델**: 기본 모델 ID는 `gemini-4-hydrogen`이다. 2026-11-14에 [Google AI 공식 모델 목록](https://ai.google.dev/gemini-api/docs/models)에서 확인했고, 같은 날 `scripts/check_gemini_model.py`의 `models.get` 호출이 성공했다. 공식 SDK `google-genai`를 쓴다. Gemini에는 이미지 바이트(EXIF 제거)와 고정 프롬프트만 보내고, 파일 이름·촬영 시각·위치는 보내지 않는다.
- **Live Vision은 끝까지 검증하지 못했다**: 실제 사진으로 보낸 Gemini 분석 요청은 HTTP 402(선불 크레딧 소진)로 실패해, Live 모드의 태그 추출 결과는 확인하지 못했다. Gemini 경로는 가짜 클라이언트를 쓰는 pytest로만 검증했다.
- **Live 모드 첫 스캔**: 159장을 처음 스캔하면 Gemini를 159회 호출한다. 데모 관리 화면은 30초가 지나면 실패로 표시하지만 서버 스캔은 끝까지 진행된다. 발표 전에 미리 한 번 스캔해 두면 발표 중 재스캔은 모두 중복으로 처리되어 바로 끝난다.

## 테스트

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

- 테스트는 API 키 없이 돈다. `backend/tests/conftest.py`가 테스트마다 `GEMINI_API_KEY`를 환경에서 지우고 localhost 외 네트워크 연결을 막는다. Open-Meteo는 `respx`, Gemini는 가짜 클라이언트로 대체한다.
- 이 README 작성 시점 결과(데모 사진 159장 준비 상태): 백엔드 489 passed, 프론트엔드 11개 파일 90 tests passed, typecheck 오류 0, check:bundle OK.
- 데모 사진이 없으면 사진을 쓰는 테스트 일부(`test_integration.py`의 3개 등)는 skip된다. 단, 현재 `tests/test_api_routes.py::test_demo_data_end_to_end`와 `tests/test_image_check.py::test_all_demo_photos_pass_check_image`는 사이드카만 있고 사진이 없는 상태(새로 클론한 직후)에서 skip되지 않고 실패한다. 전체 통과를 확인하려면 먼저 데모 사진을 준비한다.

## 의존성 고정

`backend/requirements.txt`와 `frontend/package.json`의 직접 의존성은 모두 `==`(npm은 정확한 버전)으로 고정했고, 위 테스트가 통과한 버전이다. 설계 문서의 후보 버전과 다른 점은 다음과 같다.

- TypeScript는 7.0.2 대신 5.9.3을 쓴다. `openapi-typescript` 7.13.0의 peer 의존성 범위가 TypeScript `^5.x`이기 때문이다.
- `sqlalchemy`(2.1.8)와 `starlette`(1.10.0)는 `sqlmodel`·`fastapi`의 전이 의존성이지만 앱 코드가 직접 import하므로 직접 의존성으로 고정했다.

## 기능 상태

### 완료 (pytest/vitest 통과 또는 5분 데모에서 재현)

- 사진 폴더 스캔: 확장자 필터, 20MB 제한, 이미지 디코딩 검증, SHA-256 중복 제거, 재스캔 멱등성, 파일 단위 실패 집계, 스캔 중복 실행 거부(409)
- 사이드카 메타데이터 파싱·검증과 `demo_seed` 체감 피드백 저장
- Fixture Vision(해시별 고정 태그, 미등록 사진은 `unknown`)
- Gemini Vision Provider 코드 경로: 스키마 검증, API 오류·시간 초과 처리, 요청에 이미지와 프롬프트만 포함(가짜 클라이언트 테스트)
- 날씨 연결: Fixture 날씨, Open-Meteo Archive 요청·응답 매핑과 실패 시 fixture 대체(`respx` 테스트), 가장 가까운 시각 선택, 단위 정규화
- 오늘 날씨와 시나리오 선택(선택 유지, 알 수 없는 시나리오 거부)
- `rule_based_v1` 추천: 허용 오차 3.0°C 경계 포함, 5.0°C 1회 확장, 데이터 부족 상태, 피드백 가중 점수, 동점 처리, 설명 문장, 신뢰도 수준
- 체감 피드백 저장(이력 보존, 최신 기록이 Effective_Vote, 422/404/500 처리)과 다음 추천에 즉시 반영
- REST API와 OpenAPI 문서, 사진 엔드포인트(경로 비노출), CORS 정확 일치
- 설정 로더(기본값 대체, 잘못된 값 로그)와 API 키 마스킹, 번들 비밀값 검사
- 모바일 웹: 홈(오늘 날씨, 알림 카드, 추천), 기록 그리드, 상세, 데모 관리 화면(vitest smoke 테스트와 5분 데모 절차)

### 미완료 또는 미검증

- Live Gemini Vision 분석: HTTP 402(선불 크레딧 소진)로 실제 응답을 확인하지 못했다.
- Live Open-Meteo 호출: `respx`로 응답 형태만 테스트했고, 실제 네트워크 호출 결과는 이 README 작성 시점에 확인 기록이 없다.
- 360~430px 뷰포트에서 가로 스크롤이 없는지 수동 확인(Req 11.6)을 하지 않았다.
- 실제 휴대폰 기기에서 확인하지 않았다.
- 화면 문구 한국어 검수(`frontend/src/lib/strings.ts`)를 별도로 하지 않았다.
- 선택 항목(`tasks.md`에서 `*` 표시)인 property-based test(Hypothesis, fast-check)와 화면별 상세 UI 테스트, 저장소 위생 테스트는 구현하지 않았다. 대신 단위·smoke·통합 테스트가 있다.
- 사진이 없는 클론에서 테스트 2개가 실패한다(위 "테스트" 절).

## Phase 2 개선 목록

- **실제 갤러리 접근**: 사이드카 대신 사용자 동의를 받은 휴대폰 갤러리 업로드에서 사진과 EXIF 촬영 시각·위치를 읽는다. 위치는 개인정보이므로 저장 범위와 삭제 방법을 함께 설계해야 한다.
- **Web Push 알림**: 지금의 앱 내부 알림 카드를 Web Push(FCM 등) 실제 알림으로 바꿔, 앱을 열지 않아도 아침에 추천과 체감 기록 버튼을 받게 한다.
- **추천 엔진 교체**: `RecommendationStrategy` 인터페이스(`recommend(today, candidates)`)의 새 구현체를 만들고 팩토리(`build_strategy`)에서 고르면 API와 화면을 바꾸지 않고 학습 기반 추천 등으로 교체할 수 있다.
- **SQLite에서 다른 저장소로 이전하는 조건**: 다중 사용자 계정, 여러 서버 프로세스나 워커가 동시에 쓰는 배포, 사용자당 수천 건 이상으로 늘어난 기록 중 하나라도 생기면 PostgreSQL 같은 서버형 DB로 옮긴다. 이전은 `OutfitRepository` 구현과 `DATABASE_URL`만 바꾸는 범위로 끝나도록 저장소 경계를 유지한다.
