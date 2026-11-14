/**
 * 화면에 표시하는 한국어 문구 모음 (Req 11.5).
 * 컴포넌트는 문자열 리터럴 대신 이 상수를 사용한다.
 */
export const STRINGS = {
  appName: "WeatherWear",

  nav: {
    home: "홈",
    history: "기록",
    demo: "데모",
    label: "주요 메뉴",
  },

  home: {
    title: "오늘의 추천",
    placeholder: "오늘 날씨와 추천 착장이 여기에 표시됩니다.",
    todayTemperature: "오늘 기온",
    apparentTemperature: "체감",
    humidity: "습도",
    precipitation: "강수",
    source: "출처",
    demoLink: "데모 관리 화면으로",
  },

  history: {
    title: "착장 기록",
    placeholder: "저장된 착장 기록이 여기에 표시됩니다.",
    empty: "아직 착장 기록이 없어요. 데모 화면에서 폴더 스캔을 실행해 주세요.",
    noWeather: "날씨 정보 없음",
  },

  detail: {
    title: "착장 상세",
    placeholder: "착장 사진, 태그, 당시 날씨, 평가가 여기에 표시됩니다.",
    notFound: "착장을 찾을 수 없습니다.",
  },

  demo: {
    title: "데모 관리",
    placeholder: "폴더 스캔, 날씨 시나리오 선택, 추천 재계산을 여기서 실행합니다.",
    devOnly: "데모 관리 화면은 개발 모드에서만 사용할 수 있습니다",
    scan: "폴더 스캔",
    scanFailed: "폴더 스캔에 실패했어요",
    scenario: "날씨 시나리오",
    recompute: "추천 재계산",
  },

  feedback: {
    question: (tempLabel: string) => `어제 이 착장, 오늘 ${tempLabel}엔 어땠어요?`,
    cold: "🥶 추웠어",
    comfortable: "😊 적당했어",
    hot: "🥵 더웠어",
    saveFailed: "저장하지 못했어요. 다시 시도해 주세요.",
    notificationHeader: "WeatherWear · 지금",
  },

  vote: {
    cold: "추웠어",
    comfortable: "적당했어",
    hot: "더웠어",
    none: "미평가",
  },

  notice: {
    estimate: "실측값이 아닌 제공자 추정치/재분석값입니다",
    fixture: "fixture",
    virtualData: "가상 데이터",
    fallback: "유사 기록 부족 / 범위 확장",
    insufficientData: "개인 착장 데이터 부족",
  },

  error: {
    loadFailed: "데이터를 불러오지 못했어요",
    retry: "다시 시도",
    timeout: "응답 시간이 초과되었어요.",
    network: "서버에 연결하지 못했어요.",
    server: "서버 오류가 발생했어요.",
    aborted: "요청이 취소되었어요.",
  },

  emptyValue: "–",
} as const;
