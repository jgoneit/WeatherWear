/** Human-readable labels at the presentation boundary; API values stay unchanged. */
const LABELS: Record<string, string> = {
  fleece: "플리스", jogger_pants: "조거 팬츠", coveralls: "커버올",
  none: "없음", unknown: "알 수 없음", coat: "코트", trench_coat: "트렌치코트", jacket: "재킷", varsity_jacket: "바시티 재킷", leather_jacket: "가죽 재킷", denim_jacket: "데님 재킷", shirt_jacket: "셔츠 재킷", bomber_jacket: "봄버 재킷", utility_jacket: "유틸리티 재킷", track_jacket: "트랙 재킷", rain_jacket: "레인 재킷", fleece_jacket: "플리스", windbreaker: "바람막이", puffer_jacket: "패딩", short_puffer: "숏패딩", long_puffer: "롱패딩", parka: "파카", blazer: "블레이저", cardigan: "카디건", vest: "베스트", knit: "니트", sweater: "스웨터", t_shirt: "티셔츠", long_sleeve_t_shirt: "긴소매 티셔츠", shirt: "셔츠", blouse: "블라우스", hoodie: "후디", sweatshirt: "스웨트셔츠", turtleneck: "터틀넥", sleeveless_top: "슬리브리스", jersey: "저지", trousers: "팬츠", slacks: "슬랙스", jeans: "데님", skirt: "스커트", shorts: "쇼츠", chinos: "치노 팬츠", cargo_pants: "카고 팬츠", corduroy_pants: "코듀로이 팬츠", track_pants: "트랙 팬츠", parachute_pants: "파라슈트 팬츠", leggings: "레깅스", dress: "원피스", jumpsuit: "점프수트",
};
export function clothingLabel(value: string): string { return LABELS[value] ?? value.replaceAll("_", " "); }
export function localizeExplanation(value: string): string {
  return value.replace(/\b[a-z][a-z_]*\b/g, token => LABELS[token] ?? token);
}
