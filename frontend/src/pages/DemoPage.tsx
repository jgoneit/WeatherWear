import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import {
  api,
  ApiError,
  type DemoStatus,
  type RecommendationResponse,
  type ScanReport,
  type Schemas,
} from "../api/client";
import { useReportTodayTemperature } from "../components/layout/TodayTemperatureContext";
import { IS_DEV } from "../lib/env";
import { formatTemp } from "../lib/format";
import { STRINGS } from "../lib/strings";
import { useAsync } from "../lib/useAsync";

type Scenario = Schemas["ScenarioOut"];

interface DemoPageProps {
  /** 테스트에서 개발 모드 여부를 바꿀 때 사용한다. 기본값은 import.meta.env.DEV. */
  isDev?: boolean;
}

/**
 * 데모 관리 화면 (Req 14.1~14.7, 16.5).
 * 개발 모드가 아니면 컨트롤 없이 안내 문구만 보여주고 API도 부르지 않는다 (Req 14.2).
 */
export function DemoPage({ isDev = IS_DEV }: DemoPageProps) {
  if (!isDev) {
    return (
      <section className="glass rounded-(--radius-card) p-5">
        <h1 className="text-2xl font-semibold tracking-tight">{STRINGS.demo.title}</h1>
        <p className="mt-2 text-slate-700">{STRINGS.demo.devOnly}</p>
      </section>
    );
  }
  return <DemoPanel />;
}

// ---------------------------------------------------------------------------
// 동작 상태 훅
// ---------------------------------------------------------------------------

interface ActionState<T> {
  pending: boolean;
  /** 마지막으로 성공한 결과. 이후 실패해도 지우지 않는다 (Req 14.6). */
  data: T | undefined;
  /** 마지막 시도의 오류. 새 시도를 시작하거나 성공하면 지운다. */
  error: unknown;
}

type ActionOutcome<T> = { ok: true; value: T } | { ok: false };

/**
 * 버튼 하나에 대응하는 비동기 동작. 진행 중에는 같은 동작을 다시 시작하지 않는다 (Req 14.5).
 * 요청 제한 시간(30초)은 api 클라이언트가 건다 (Req 14.6).
 */
function useAction<T>() {
  const [state, setState] = useState<ActionState<T>>({ pending: false, data: undefined, error: undefined });
  const busyRef = useRef(false);
  const mountedRef = useRef(true);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
    };
  }, []);

  const run = useCallback(async (fn: (signal: AbortSignal) => Promise<T>): Promise<ActionOutcome<T>> => {
    if (busyRef.current) return { ok: false };
    busyRef.current = true;
    const controller = new AbortController();
    controllerRef.current = controller;
    setState((prev) => ({ ...prev, pending: true, error: undefined }));
    try {
      const value = await fn(controller.signal);
      if (mountedRef.current) setState({ pending: false, data: value, error: undefined });
      return { ok: true, value };
    } catch (error) {
      if (mountedRef.current) setState((prev) => ({ ...prev, pending: false, error }));
      return { ok: false };
    } finally {
      busyRef.current = false;
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  }, []);

  return { ...state, run };
}

/** 오류 사유 문구. 서버 ErrorResponse 메시지나 클라이언트 타임아웃/네트워크 문구를 쓴다. */
function errorReason(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return STRINGS.error.server;
}

// ---------------------------------------------------------------------------
// 화면
// ---------------------------------------------------------------------------

type ScenarioOutcome = { kind: "selected"; scenario: Scenario } | { kind: "cleared" };

function DemoPanel() {
  const status = useAsync((signal) => api.getStatus({ signal }), []);
  const scenarios = useAsync((signal) => api.getScenarios({ signal }), []);
  const scan = useAction<ScanReport>();
  const scenarioAction = useAction<ScenarioOutcome>();
  const recompute = useAction<RecommendationResponse>();

  // 선택 성공 직후에는 응답 값을, 목록을 다시 받으면 서버 값을 현재 선택으로 쓴다.
  // 선택이 실패하면 이 값을 바꾸지 않으므로 이전 선택이 유지된다 (Req 14.6).
  const [selectedOverride, setSelectedOverride] = useState<{ id: string | null } | null>(null);
  useEffect(() => {
    setSelectedOverride(null);
  }, [scenarios.data]);
  const selectedId = selectedOverride ? selectedOverride.id : (scenarios.data?.selected_id ?? null);

  // 어느 시나리오 동작(선택/해제)이 실패했는지 표시하기 위한 라벨.
  const [scenarioFailLabel, setScenarioFailLabel] = useState<string>(STRINGS.demo.scenarioSelectFailed);

  useReportTodayTemperature(recompute.data?.today.actual_temperature_c);

  const reloadStatus = status.reload;

  const handleScan = useCallback(async () => {
    const outcome = await scan.run((signal) => api.scan({ signal }));
    if (outcome.ok) void reloadStatus();
  }, [scan.run, reloadStatus]);

  const handleSelect = useCallback(
    async (scenarioId: string) => {
      setScenarioFailLabel(STRINGS.demo.scenarioSelectFailed);
      const outcome = await scenarioAction.run(async (signal) => {
        const res = await api.selectScenario(scenarioId, { signal });
        return { kind: "selected", scenario: res.scenario };
      });
      if (outcome.ok && outcome.value.kind === "selected") {
        setSelectedOverride({ id: outcome.value.scenario.id });
        void reloadStatus();
      }
    },
    [scenarioAction.run, reloadStatus],
  );

  const handleClear = useCallback(async () => {
    setScenarioFailLabel(STRINGS.demo.scenarioClearFailed);
    const outcome = await scenarioAction.run(async (signal) => {
      await api.clearScenario({ signal });
      return { kind: "cleared" };
    });
    if (outcome.ok) {
      setSelectedOverride({ id: null });
      void reloadStatus();
    }
  }, [scenarioAction.run, reloadStatus]);

  const handleRecompute = useCallback(async () => {
    const outcome = await recompute.run((signal) => api.getRecommendations({ signal }));
    if (outcome.ok) void reloadStatus();
  }, [recompute.run, reloadStatus]);

  return (
    <div className="flex w-full max-w-full min-w-0 flex-col gap-4">
      <div className="px-1">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{STRINGS.demo.title}</h1>
        <p className="mt-1 text-sm text-slate-700">{STRINGS.demo.intro}</p>
      </div>

      <StatusCard
        data={status.data}
        error={status.error}
        pending={status.pending}
        onRefresh={() => void status.reload()}
      />

      <ScanCard
        report={scan.data}
        error={scan.error}
        pending={scan.pending}
        onScan={() => void handleScan()}
      />

      <ScenarioCard
        scenarios={scenarios.data?.scenarios}
        listError={scenarios.error}
        listPending={scenarios.pending}
        selectedId={selectedId}
        actionPending={scenarioAction.pending}
        actionError={scenarioAction.error}
        actionFailLabel={scenarioFailLabel}
        lastOutcome={scenarioAction.data}
        onRefresh={() => void scenarios.reload()}
        onSelect={(id) => void handleSelect(id)}
        onClear={() => void handleClear()}
      />

      <RecomputeCard
        result={recompute.data}
        error={recompute.error}
        pending={recompute.pending}
        onRecompute={() => void handleRecompute()}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 공통 조각
// ---------------------------------------------------------------------------

function Card({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="glass flex min-w-0 flex-col gap-3 rounded-3xl p-4">
      <div className="flex min-w-0 items-center justify-between gap-3">
        <h2 id={headingId} className="min-w-0 truncate text-base font-semibold text-slate-900">
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function PrimaryButton({
  label,
  pending,
  disabled,
  onClick,
}: {
  label: string;
  pending: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={pending || disabled}
      aria-busy={pending}
      className="min-h-11 w-full rounded-full bg-slate-900 px-4 text-sm font-semibold text-white transition active:scale-95 disabled:opacity-50 motion-reduce:transition-none"
    >
      {pending ? STRINGS.demo.inProgress : label}
    </button>
  );
}

function SecondaryButton({
  label,
  pending,
  onClick,
}: {
  label: string;
  pending: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={pending}
      aria-busy={pending}
      className="min-h-11 shrink-0 rounded-full border border-white/70 bg-white/60 px-3 text-xs font-semibold text-slate-900 transition active:scale-95 disabled:opacity-50 motion-reduce:transition-none"
    >
      {pending ? STRINGS.demo.inProgress : label}
    </button>
  );
}

/** 어느 동작이 실패했는지와 사유를 보여준다 (Req 14.6). */
function ActionError({ action, error }: { action: string; error: unknown }) {
  if (error === undefined) return null;
  return (
    <p role="alert" className="rounded-2xl bg-red-50/90 px-3 py-2 text-sm font-medium text-red-800">
      {STRINGS.demo.failedWithReason(action, errorReason(error))}
    </p>
  );
}

/** 동작 완료 표시 (Req 14.7). */
function DoneNotice({ title, detail }: { title: string; detail?: string }) {
  return (
    <div role="status" className="flex min-w-0 flex-col gap-0.5 rounded-2xl bg-emerald-50/90 px-3 py-2 text-sm text-emerald-900">
      <p className="font-semibold">
        <span aria-hidden="true">✓ </span>
        {title} · {STRINGS.demo.done}
      </p>
      {detail ? <p className="min-w-0 break-words tabular-nums">{detail}</p> : null}
    </div>
  );
}

function BentoTile({ label, value, testId }: { label: string; value: ReactNode; testId?: string }) {
  return (
    <div data-testid={testId} className="flex min-w-0 flex-col gap-1 rounded-2xl border border-white/60 bg-white/50 p-3">
      <span className="text-xs font-medium text-slate-600">{label}</span>
      <span className="min-w-0 truncate text-2xl font-semibold tracking-tight text-slate-900 tabular-nums">
        {value}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 상태 카드 (Req 14, 16.5): API 키는 설정 여부만 보인다.
// ---------------------------------------------------------------------------

function StatusCard({
  data,
  error,
  pending,
  onRefresh,
}: {
  data: DemoStatus | undefined;
  error: unknown;
  pending: boolean;
  onRefresh: () => void;
}) {
  return (
    <Card
      title={STRINGS.demo.statusTitle}
      action={<SecondaryButton label={STRINGS.demo.statusRefresh} pending={pending} onClick={onRefresh} />}
    >
      <ActionError action={STRINGS.demo.statusFailed} error={error} />
      {data ? (
        <>
          <dl className="grid min-w-0 grid-cols-1 gap-2 text-sm">
            <StatusRow label={STRINGS.demo.visionProvider} value={data.vision_provider} testId="status-vision" />
            <StatusRow label={STRINGS.demo.weatherProvider} value={data.weather_provider} testId="status-weather" />
            <StatusRow
              label={STRINGS.demo.apiKey}
              value={data.gemini_api_key_configured ? STRINGS.demo.keyConfigured : STRINGS.demo.keyMissing}
              testId="status-api-key"
            />
          </dl>
          <div className="grid min-w-0 grid-cols-3 gap-2">
            <BentoTile label={STRINGS.demo.outfitCount} value={data.outfit_count} />
            <BentoTile label={STRINGS.demo.snapshotCount} value={data.weather_snapshot_count} />
            <BentoTile label={STRINGS.demo.feedbackCount} value={data.feedback_count} />
          </div>
          <p className="text-sm text-slate-700 tabular-nums">
            {data.selected_scenario
              ? STRINGS.demo.scenarioApplied(
                  data.selected_scenario.name,
                  formatTemp(data.selected_scenario.actual_temperature_c),
                )
              : STRINGS.demo.scenarioNoneApplied}
          </p>
        </>
      ) : null}
    </Card>
  );
}

function StatusRow({ label, value, testId }: { label: string; value: string; testId: string }) {
  return (
    <div className="flex min-w-0 items-center justify-between gap-3 rounded-2xl bg-white/40 px-3 py-2">
      <dt className="min-w-0 truncate text-slate-700">{label}</dt>
      <dd data-testid={testId} className="shrink-0 font-semibold text-slate-900">
        {value}
      </dd>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 폴더 스캔 카드 (Req 14.3, 14.4)
// ---------------------------------------------------------------------------

function ScanCard({
  report,
  error,
  pending,
  onScan,
}: {
  report: ScanReport | undefined;
  error: unknown;
  pending: boolean;
  onScan: () => void;
}) {
  return (
    <Card title={STRINGS.demo.scan}>
      <p className="text-sm text-slate-700">{STRINGS.demo.scanDescription}</p>
      <PrimaryButton label={STRINGS.demo.scan} pending={pending} onClick={onScan} />
      <ActionError action={STRINGS.demo.scanFailed} error={error} />

      {report ? (
        <div className="flex min-w-0 flex-col gap-3" aria-label={STRINGS.demo.scanResultLabel}>
          {error === undefined && !pending ? <DoneNotice title={STRINGS.demo.scanDone} /> : null}
          <div className="grid min-w-0 grid-cols-2 gap-2">
            <BentoTile label={STRINGS.demo.scanned} value={report.scanned} testId="scan-scanned" />
            <BentoTile label={STRINGS.demo.succeeded} value={report.succeeded} testId="scan-succeeded" />
            <BentoTile label={STRINGS.demo.duplicate} value={report.duplicate} testId="scan-duplicate" />
            <BentoTile label={STRINGS.demo.failed} value={report.failed} testId="scan-failed" />
          </div>
          {report.failed === 0 || report.errors.length === 0 ? (
            <p className="text-sm text-slate-700">{STRINGS.demo.noFailures}</p>
          ) : (
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-slate-900">{STRINGS.demo.failuresTitle}</h3>
              <ul className="mt-2 flex min-w-0 flex-col gap-2">
                {report.errors.map((item, index) => (
                  <li
                    key={`${item.file_name}-${index}`}
                    data-testid="scan-error"
                    className="min-w-0 rounded-2xl bg-white/50 px-3 py-2 text-sm"
                  >
                    <p className="min-w-0 font-medium break-all text-slate-900">{item.file_name}</p>
                    <p className="min-w-0 break-words text-slate-700">
                      [{STRINGS.demo.stage[item.stage]}] {item.message}{" "}
                      <span className="text-xs text-slate-500">({item.code})</span>
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      ) : (
        <p className="text-sm text-slate-600">{STRINGS.demo.scanNotRun}</p>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 날씨 시나리오 카드 (Req 14.3, 14.5~14.7)
// ---------------------------------------------------------------------------

function ScenarioCard({
  scenarios,
  listError,
  listPending,
  selectedId,
  actionPending,
  actionError,
  actionFailLabel,
  lastOutcome,
  onRefresh,
  onSelect,
  onClear,
}: {
  scenarios: Scenario[] | undefined;
  listError: unknown;
  listPending: boolean;
  selectedId: string | null;
  actionPending: boolean;
  actionError: unknown;
  actionFailLabel: string;
  lastOutcome: ScenarioOutcome | undefined;
  onRefresh: () => void;
  onSelect: (id: string) => void;
  onClear: () => void;
}) {
  const optionsDisabled = actionPending || scenarios === undefined;
  return (
    <Card
      title={STRINGS.demo.scenario}
      action={<SecondaryButton label={STRINGS.demo.scenarioRefresh} pending={listPending} onClick={onRefresh} />}
    >
      <ActionError action={STRINGS.demo.scenarioListFailed} error={listError} />
      <ActionError action={actionFailLabel} error={actionError} />

      {actionPending ? (
        <p role="status" className="text-sm text-slate-700">
          {STRINGS.demo.inProgress}
        </p>
      ) : null}

      {lastOutcome && actionError === undefined && !actionPending ? (
        lastOutcome.kind === "selected" ? (
          <DoneNotice
            title={STRINGS.demo.scenario}
            detail={STRINGS.demo.scenarioApplied(
              lastOutcome.scenario.name,
              formatTemp(lastOutcome.scenario.actual_temperature_c),
            )}
          />
        ) : (
          <DoneNotice title={STRINGS.demo.scenarioCleared} detail={STRINGS.demo.scenarioNoneApplied} />
        )
      ) : null}

      {scenarios === undefined ? (
        listPending ? <p className="text-sm text-slate-700">{STRINGS.demo.scenarioLoading}</p> : null
      ) : (
        <ul aria-label={STRINGS.demo.scenarioListLabel} className="flex min-w-0 flex-col gap-2">
          {scenarios.length === 0 ? (
            <li className="text-sm text-slate-700">{STRINGS.demo.scenarioEmpty}</li>
          ) : (
            scenarios.map((scenario) => {
              const selected = scenario.id === selectedId;
              return (
                <li key={scenario.id} className="min-w-0">
                  <ScenarioOption
                    label={scenario.name}
                    detail={formatTemp(scenario.actual_temperature_c)}
                    selected={selected}
                    disabled={optionsDisabled}
                    onClick={() => onSelect(scenario.id)}
                    testId={`scenario-${scenario.id}`}
                  />
                </li>
              );
            })
          )}
          <li className="min-w-0">
            <ScenarioOption
              label={STRINGS.demo.scenarioClear}
              selected={selectedId === null}
              disabled={optionsDisabled}
              onClick={onClear}
              testId="scenario-clear"
            />
          </li>
        </ul>
      )}
    </Card>
  );
}

function ScenarioOption({
  label,
  detail,
  selected,
  disabled,
  onClick,
  testId,
}: {
  label: string;
  detail?: string;
  selected: boolean;
  disabled: boolean;
  onClick: () => void;
  testId: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      aria-pressed={selected}
      disabled={disabled}
      onClick={onClick}
      className={`flex min-h-11 w-full min-w-0 items-center justify-between gap-3 rounded-2xl border px-3 py-2 text-left text-sm transition active:scale-[0.98] disabled:opacity-60 motion-reduce:transition-none ${
        selected
          ? "border-slate-900 bg-slate-900 text-white"
          : "border-white/70 bg-white/50 text-slate-900"
      }`}
    >
      <span className="min-w-0 break-words font-medium">{label}</span>
      <span className="flex shrink-0 items-center gap-2 tabular-nums">
        {detail ? <span>{detail}</span> : null}
        {selected ? (
          <span className="rounded-full bg-white/20 px-2 py-0.5 text-[11px] font-semibold">
            {STRINGS.demo.scenarioSelected}
          </span>
        ) : null}
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// 추천 재계산 카드 (Req 14.3, 14.5~14.7): GET /api/recommendations/today 재호출
// ---------------------------------------------------------------------------

function RecomputeCard({
  result,
  error,
  pending,
  onRecompute,
}: {
  result: RecommendationResponse | undefined;
  error: unknown;
  pending: boolean;
  onRecompute: () => void;
}) {
  const today = result?.today;
  const applied = today
    ? today.scenario_name
      ? STRINGS.demo.scenarioApplied(today.scenario_name, formatTemp(today.actual_temperature_c))
      : STRINGS.demo.recomputeTodayProvider(formatTemp(today.actual_temperature_c))
    : undefined;
  const summary = result
    ? result.status === "insufficient_data"
      ? STRINGS.demo.recomputeInsufficient
      : `${STRINGS.demo.recomputeItems(result.items.length)}${result.fallback ? ` · ${STRINGS.demo.recomputeFallback}` : ""}`
    : undefined;

  return (
    <Card title={STRINGS.demo.recompute}>
      <p className="text-sm text-slate-700">{STRINGS.demo.recomputeDescription}</p>
      <PrimaryButton label={STRINGS.demo.recompute} pending={pending} onClick={onRecompute} />
      <ActionError action={STRINGS.demo.recomputeFailed} error={error} />
      {result && error === undefined && !pending ? (
        <DoneNotice title={STRINGS.demo.recomputeDone} detail={applied} />
      ) : null}
      {summary ? (
        <p data-testid="recompute-summary" className="text-sm text-slate-700">
          {summary}
        </p>
      ) : null}
    </Card>
  );
}
