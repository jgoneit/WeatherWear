# BUILD REPORT

WeatherWear는 Kiro Spec(requirements-first)으로 요구사항 → 설계 → 태스크를 만들고, Kiro가 태스크 목록을 실행해 Phase 1(MVP)을 구현했다. 아래 수치는 `.kiro/specs/weatherwear/tasks.meta.json`의 `executionHistory`를 직접 읽어 확인한 값이다. Kiro가 직접 만든 것과 다른 도구(Claude Code, Orca)로 만든 것을 구분해 적는다. Phase 2(자동화)에는 Kiro를 쓰지 않았다. Kiro spec 형식만 따라 Claude Code가 spec을 쓰고, Claude Code와 Orca worker가 구현했다.

## Kiro 활용 내역

| 단계 | 사용한 Kiro 기능 | 무엇을 했는지 | 결과물 위치 |
|---|---|---|---|
| 기획 | Spec (requirements, `workflowType: requirements-first`) | 문제와 가설("머신러닝 없이도 과거 경험 + 유사 기온 검색이 일반 기온 기반 추천보다 유용하다"), 용어집, 요구사항 17개와 인수 조건(EARS 형식), Phase 2 후보(범위 제외 항목) 작성 | `.kiro/specs/weatherwear/requirements.md`, `.kiro/specs/weatherwear/.config.kiro` |
| 설계 | Spec (design) | FastAPI + SQLModel + SQLite 백엔드, React + Vite 프론트엔드 구조, Vision·Weather provider 인터페이스(Fixture / Live 교체), `rule_based_v1` 점수 규칙, 데이터 모델, API, 오류 처리, 테스트 전략 설계 | `.kiro/specs/weatherwear/design.md` |
| 구현 | Spec (tasks) + 태스크 실행 | 태스크 목록 작성 후 Kiro가 실행. 실행 1(`executionId f8d9e993…`): 13:03~14:55, 태스크 1~18(65개 항목, 저장소 골격·설정 로더·도메인·DB·스키마·수집·Vision·날씨·추천·피드백·API·프론트엔드). 실행 2(`89392b19…`): 15:26~15:45, Phase 1 변경 태스크 19.x와 20(20개 항목에 실행 기록). 전체 `executionHistory` 항목 85개, 같은 Kiro 채팅 세션 1개. **Kiro `tasks.md` 19.20~20은 PR #9 병합(merge commit `9a6da32`)으로 완료되어 모든 태스크가 체크되어 있다** | `.kiro/specs/weatherwear/tasks.md`, `.kiro/specs/weatherwear/tasks.meta.json`, `backend/`, `frontend/` |
| 검증 | Spec 태스크에 포함된 테스트 작성·실행 | 태스크마다 pytest / vitest 테스트를 함께 작성하고 실행(예: 유사 기온 경계 3.0°C, 5.0°C 1회 확장, 피드백 가중 점수, 5분 데모 통합 테스트 `test_five_minute_demo_walkthrough`). 최종 결과(PR #9 병합 커밋 기록): 백엔드 602 passed(skip 0), vitest 92 passed, typecheck, check:bundle, `select_scenarios.py --check` 통과. `tasks.meta.json`의 `pbtResults`는 비어 있다(선택 항목인 property-based test는 구현하지 않음) | `backend/tests/`, `frontend/src/**/*.test.ts(x)` |
| 기타 | Steering, Hooks, MCP: 사용 안 함 | `.kiro/`에는 `specs/`만 있다. steering, hooks, MCP 설정 파일은 없다 | - |
| Phase 2 기획·설계 (Kiro 외 도구) | 사용 안 함. Kiro spec 형식만 따름 | Phase 2 자동화 spec(새 사진 자동 감지, 매일 위치 기반 Web Push 브리핑). **Kiro가 생성한 문서가 아니다.** Kiro spec과 같은 파일 구조(`.config.kiro`의 `workflowType: requirements-first`, requirements → design → tasks)로 Claude Code가 썼다. 요구사항 20개와 인수 조건 167개(EARS 형식), 설계의 정확성 속성 29개, 태스크(sub-task 68개 중 선택 21개, checkpoint 4개)와 흐름별 소유 파일·wave 순서. 사용자 결정(갤러리 대신 `ex_img/` 감시, FCM 대신 Web Push, 기본 notifier `log` 등)은 requirements.md "사용자 결정" 절에 있다 | `.kiro/specs/weatherwear-automation/`, 커밋 `b23341c` |
| Phase 2 구현·검증 (Kiro 외 도구) | 사용 안 함 | Claude Code와 Orca worker(리드 + 흐름 A/B/C)가 spec의 `tasks.md`를 wave 순서대로 구현했다. 리드가 공통 계약(설정, DB v3, `ingest_file`, API 골격, 위치: 1.x)과 통합(lifespan, 상태 API, README, compose: 7.x)을 맡았다. 흐름 A는 갤러리 감시·수집(3.x), B는 날씨·브리핑·알림(4.x), C는 프론트엔드(5.x)를 맡았다. 그 뒤 독립 검토를 1회 받았다(차단 0건, 비차단 4건). 4건을 모두 고치고 건마다 회귀 테스트를 더했다(tasks 9.x). 검토 반영 뒤 main 병합 상태에서 백엔드 1138 passed(skip 0), vitest 106 passed(13개 파일)이고, typecheck와 check:bundle도 통과했다. 선택 property test는 작성하지 않았다. Kiro 실행 기록(`tasks.meta.json`)은 없다 | 커밋 `73e2b6a`(1.x), `1029a67`(3.x·4.x·5.x), `5354ace`(7.x, Final checkpoint), `525dccd`(검토 수정), `backend/app/gallery/`, `backend/app/notifications/`, `frontend/` |

## 개발 과정에서 바뀐 점

- **Phase 1 구현 후 사용자 결정으로 3가지를 바꿨다**(`.kiro/specs/weatherwear/requirements.md` "Phase 1 구현 후 변경 사항"). 바뀐 요구사항을 spec에 먼저 반영하고, Kiro 태스크 19.x로 구현 변경을 추가했다.
  1. Gemini 호출: Google AI Studio 대신 Google Cloud Agent Platform API(구 Vertex AI) express mode(API 키 인증). 환경 변수 이름 `GEMINI_API_KEY`는 그대로 둔다.
  2. 촬영 시각·위치: 원래 Phase 2였던 내장 EXIF 읽기를 Phase 1로 당겼다. 사이드카 JSON은 선택적 대체 수단으로 낮췄다(`backend/app/ingestion/metadata.py`).
  3. Fixture 날씨: 사진의 시각·위치로 Open-Meteo Archive API를 한 번 호출해 기록한 실제 기록 날씨 값을 쓴다.
- **사진 분류 게이트 추가**: 갤러리에는 착장이 아닌 사진도 섞여 있어서, 팀원 PR #6(이슈 #2)로 Vision 결과를 `outfit` / `non_outfit` / `uncertain`으로 나누고 착장 사진만 저장하게 했다. 의류 분류도 닫힌 snake_case 목록으로 표준화했다(이슈 #1).
- **극한 날씨 데모 추가**: 한파·무더위·폭염용 생성 착장 사진 15장(141~155번)을 넣고 홈 화면 UI를 단순하게 다듬었다(PR #10). 이후 PR #9에서 데모 사진 189장(기존 159 + 126~140번 15 + 생성 15)의 기록 기온에 규칙 S1~S4를 적용해 시나리오를 다시 골랐다: 14.5°C(정상) / −16.0°C(정확히 3.0°C 차이 1건) / 44.0°C(5.0°C fallback) / 48.0°C(데이터 부족), 시드 투표 66개, vision fixture 189개(착장 169 / 비착장 20).
- **자동화는 Phase 2로**: 처음 계획의 "버튼으로 폴더 스캔 + 앱 내부 알림 카드"는 그대로 두고, 새 사진 자동 감지와 매일 Web Push 브리핑은 Phase 2 spec으로 분리했다. 그 spec대로 Phase 2를 구현했다(`phase2-automation` 브랜치).
- **Phase 2 범위**: 휴대폰 갤러리 대신 `ex_img/` 폴더를 감시하고, FCM 대신 표준 Web Push(VAPID)를 쓴다. 코드 기본값은 자동화 꺼짐이고 `.env.example`과 `compose.yaml`은 켜 둔다. 실제 휴대폰 갤러리 접근, FCM, 네이티브 앱, 로그인은 범위 밖이다.
- **Phase 2 독립 검토 반영**: 검토에서 나온 비차단 4건을 spec 기준부터 고친 뒤 구현했다(requirements.md "독립 검토 반영" 절).
  1. 같은 바이트 사진의 앞 파일이 실패하면 `duplicate`가 된 사진이 영영 저장되지 않았다.
  2. 푸시 구독 endpoint 호스트에 제한이 없었다(SSRF). 알려진 푸시 서비스 호스트만 받게 하고, README에 인증 없는 로컬 전용 API라는 점을 적었다.
  3. 번들 검사가 VAPID 개인키의 PEM 형태만 찾았다. JWK `d`, base64, hex 형태도 찾게 했다.
  4. 프로세스가 죽어 `running`으로 남은 예약 브리핑을 다시 시도하지 않았다.

## 어려웠던 점과 해결 방법

- **Kiro 크레딧이 태스크 19.20 도중에 소진됐다.** `tasks.meta.json`에는 19.x~20 실행 기록이 15:45까지 남아 있지만, 19.20은 끝나지 않았다. 남은 19.20 마감, 19.23, 19.26, 20은 Claude Code와 Orca 병렬 worker가 Kiro spec과 `tasks.md`를 그대로 따라 끝냈다. PR #9가 main에 병합되어(merge commit `9a6da32`) Kiro `tasks.md` 19.20~20이 완료·체크되었다. 이 부분의 코드는 Kiro가 직접 생성한 것이 아니다.
- **팀원 PR과 병합할 때 데이터 충돌**: 이슈 기반 팀원 PR(#6 의류 분류 표준화·사진 분류 게이트, #7 Docker Compose, #8 Vision 평가 도구, #10 UI 개편)을 합치면서 데모 데이터 충돌을 해결했다(main 병합 커밋 `87011a4`, `a8a1436`, `fbaae6e` 등). 생성 사진 15장의 데이터는 기존 fixture와 사용자 피드백을 유지하는 병합 스크립트(`scripts/add_generated_outfits.py`, 생성 이미지 해시가 다르면 중단)로 합쳤다.
- **Live Vision 검증 불가**: 실제 사진으로 보낸 Gemini 분석 요청이 HTTP 402(선불 크레딧 소진)로 실패했다. API 키 없이도 전 기능이 재현되도록 Fixture 모드를 기본값으로 두고, Gemini 경로는 가짜 클라이언트 테스트로 검증했다. Live 결과는 미검증으로 README에 적었다.
- **Phase 2 spec 작성**: Kiro 크레딧이 없어 Phase 2 자동화 spec은 Claude Code가 Kiro 형식으로 작성했다(`.kiro/specs/weatherwear-automation/`). Phase 1 불변식(수집 파이프라인, 분류 게이트, `rule_based_v1`, fixture 기본값, 오프라인 테스트)을 깨지 않도록 Phase 1 요구사항 번호를 참조해 확장했다.
- **Phase 2를 병렬 worker로 나눌 때 파일 충돌**: 흐름 A/B/C가 동시에 일할 수 있도록 리드가 공통 계약(설정, DB v3, `ingest_file`, API 골격, 위치)을 먼저 만들었다. `tasks.md`에는 흐름별 소유 파일과 wave를 정해 같은 wave 안에서 파일이 겹치지 않게 했다. Checkpoint 6에서는 흐름 사이 계약(OpenAPI에서 다시 만든 `schema.d.ts`, 알림 링크와 프론트엔드 라우트)을 대조했다.
- **Phase 1 테스트를 깨지 않기**: Phase 1의 경로 해석(`resolve_paths`)을 바꾸자 Phase 1 테스트가 깨졌다. 그래서 Phase 2 경로 해석은 별도 함수(`resolve_automation_paths`)로 분리했다. Phase 1 테스트는 Phase 2 Requirement 20.4가 정한 목록 고정 테스트만 고쳤다.
- **테스트가 저장소에 비밀 파일을 만든 일**: 통합 직후 한 테스트가 기본 경로로 VAPID 키를 저장소 `data/secrets/`에 만들었다. 그 키를 지우고 테스트를 임시 폴더로 옮겼다. 또 테스트 세션 중 저장소 `data/user/`나 `data/secrets/`가 새로 생기면 실패하는 가드를 `backend/tests/conftest.py`에 넣었다.
