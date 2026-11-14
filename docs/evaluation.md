# 비전 정확도 평가

Gemini 단위 테스트는 가짜 응답만 검증하므로 실제 사진에서의 정확도를 알 수 없다. 이 도구는 정답 라벨이 있는 사진으로 착장 판정(`photo_type`)과 의류 카테고리(`upper_type`, `outer_type`, `lower_type`)의 정확도를 측정한다.

## 실행

```bash
# 1) 오프라인 파이프라인 점검: Fixture provider, 네트워크와 키 불필요
backend/.venv/bin/python scripts/evaluate_vision.py

# 2) 실제 측정(opt-in): GEMINI_API_KEY가 환경 변수 또는 .env에 있을 때만 실행
backend/.venv/bin/python scripts/evaluate_vision.py --provider gemini --check-thresholds

# 비용을 줄이는 스모크 실행(앞 N장만), 이전 결과와 비교
backend/.venv/bin/python scripts/evaluate_vision.py --provider gemini --limit 20
backend/.venv/bin/python scripts/evaluate_vision.py --provider gemini --baseline eval-results/<이전>/results.json --check-thresholds
```

- 사진이 필요하다. `data/demo/photos`는 git에서 제외되므로 먼저 `scripts/prepare_demo_photos.py`로 복사하거나 `--photos DIR`로 지정한다. 라벨된 사진이 하나도 없으면 exit 2로 종료한다.
- `--provider gemini`에서 키가 없으면 API를 호출하지 않고 exit 2로 종료한다. 모델은 `GEMINI_MODEL` 또는 `--model`로 정한다. 호출은 사진당 1회, 재시도 없음이다.
- **Fixture 모드의 수치는 모델 정확도가 아니다.** Fixture는 정답 라벨과 같은 출처라 100%에 가깝게 나오는 것이 정상이다. 사진 바이트가 fixture 해시와 다르면 대부분 `uncertain`으로 나오고 요약에 경고가 붙는다.
- 일반 `pytest`/`vitest`는 이 도구의 live 경로를 실행하지 않는다. 테스트는 가짜 provider만 쓴다.

## 출력 (`eval-results/<provider>-<시각>/`, git 제외)

| 파일 | 내용 |
|---|---|
| `results.json` | 전체 결과: provider, 모델 ID, 프롬프트 버전, 라벨 버전/해시, 건수, 지표, 사진별 예측, 판정 |
| `summary.md` | 사람이 읽는 요약(핵심 지표, 클래스별 P/R/F1, 임계값 판정) |
| `misclassified.json` | 하나라도 틀린 사진과 필드별 expected/predicted |
| `unknown.json` | 정답은 `unknown`이 아닌데 모델이 `unknown`이라고 답한 사진(놓친 의류)과 `uncertain` 판정 사진 |

기록되는 메타데이터: `provider`, `model`(Gemini일 때 모델 ID), `prompt_version`(Gemini 프롬프트의 SHA-256 앞 12자리. 프롬프트가 바뀌면 자동으로 달라진다), 라벨 `version`과 파일 SHA-256.

## 정답 라벨 (`data/eval/labels.json`)

- 159장: 착장 139, 비착장 20. 사람이 사진마다 확인한 라벨이며 `scripts/build_fixtures.py`의 Fixture 라벨과 같은 출처다.
- 권리가 확인된 사진만 포함한다(`data/demo/photos.manifest.json`의 `rights_confirmed: true`와 일치. 테스트가 검증).
- 라벨은 어휘 검증을 거친다(`domain.py`의 허용값). 비착장 사진의 세 카테고리는 모두 `unknown`이다.
- 라벨을 고치면 `version`을 올린다. 결과에 라벨 해시가 남아 어떤 라벨로 측정했는지 추적된다.
- 색상(`colors`)과 겹 수(`layers_visible`)는 평가하지 않는다.

## 지표 정의

- `photo_type`: 정상 분석된 모든 사진에 대해 클래스(`outfit`, `non_outfit`, `uncertain`)별 precision/recall/F1과 accuracy. `uncertain`은 정답에 없으므로 recall은 정의되지 않고(n/a), macro-F1은 정답에 존재하는 클래스만 평균한다.
- `upper_type`/`outer_type`/`lower_type`: 정답이 `outfit`인 사진에 대해서만 계산한다. 모델이 이런 사진을 `non_outfit`으로 답하면 태그가 `unknown`이 되어 오류로 센다.
- `exact_match`: 정답이 `outfit`인 사진 중 `photo_type`과 세 카테고리를 모두 맞힌 비율.
- 분모가 0이면 `n/a`(None)이며 0%로 표시하지 않는다. 임계값 검사에서 계산 불가 지표는 실패로 취급한다.
- provider 오류(`VisionError`)와 파일 없음은 지표에서 제외하고 별도 건수(`provider_errors`, `missing_files`, `error_rate`)로 보고한다.

## 목표 임계값과 회귀 실패 기준 (`data/eval/thresholds.json`)

> 아래 값은 **live 측정 전에 정한 초기값**이다. 첫 Gemini 평가 결과를 보고 조정하고, 조정할 때 이 표와 JSON을 함께 고친다.

| 지표 | 목표(경고) | 회귀 하한(실패) |
|---|---|---|
| `photo_type.accuracy` | 95% | 90% |
| `photo_type.outfit.precision` (비착장이 추천 데이터로 저장되는 오류) | 95% | 90% |
| `photo_type.outfit.recall` | 90% | - |
| `photo_type.non_outfit.recall` | 90% | 80% |
| `exact_match.accuracy` | 60% | 45% |
| `upper_type.accuracy` / `lower_type.accuracy` | 80% | 70% |
| `outer_type.accuracy` | 85% | 75% |
| 카테고리별 `macro_f1` | 70% | - |
| provider 오류율 | - | 5% 초과 시 실패 |
| 이전 결과 대비 하락(`--baseline`) | - | 지표당 3%p 초과 하락 시 실패 |

- 목표 미달은 `summary.md`와 `results.json`의 `below_target`에 경고로 남지만 종료 코드에는 영향이 없다.
- `--check-thresholds`를 주면 하한 미달, 오류율 초과, 베이스라인 대비 하락, 평가된 사진 0장 중 하나라도 해당할 때 exit 1이다.
- 모델 ID나 프롬프트를 바꾸면 변경 전 `results.json`을 `--baseline`으로 넘겨 회귀 여부를 확인한다.
