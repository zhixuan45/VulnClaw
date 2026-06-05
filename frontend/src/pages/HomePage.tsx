import { useEffect, useMemo, useState } from "react";
import type { TaskCommand, TaskEvent, TaskOptions, TaskRecord, TaskSummary } from "../types/api";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { SectionCard } from "../components/SectionCard";
import { loadUiPreferences, subscribeUiPreferences } from "../utils/preferences";
import {
  countConstraintViolations,
  formatActionLabel,
  formatActionList,
  formatEventLabel,
  formatPhaseLabel,
  formatTaskCommand,
  formatTaskStatus,
} from "../utils/taskLabels";
import { parseOptionalPort } from "../utils/validation";

type CheckMode = "quick" | "standard" | "deep" | "continuous";

interface HomePageProps {
  selectedTarget: string | null;
  activeTask: TaskRecord | null;
  latestEvent: TaskEvent | null;
  taskEvents: TaskEvent[];
  onCreateTask: (command: TaskCommand, target: string, resume: boolean, options: TaskOptions) => Promise<TaskRecord>;
  onOpenRisk: () => void;
  onOpenReports: () => void;
  onOpenBoundary: () => void;
}

const MODES: Array<{
  key: CheckMode;
  title: string;
  copy: string;
  command: TaskCommand;
  allowActions?: string[];
  blockActions?: string[];
}> = [
  {
    key: "quick",
    title: "Quick",
    copy: "Light discovery.",
    command: "recon",
    allowActions: ["recon"],
    blockActions: ["exploit", "persistent"],
  },
  {
    key: "standard",
    title: "Standard",
    copy: "Recommended scan.",
    command: "run",
    allowActions: ["recon", "scan"],
    blockActions: ["post_exploitation"],
  },
  {
    key: "deep",
    title: "Deep",
    copy: "More checks.",
    command: "scan",
    allowActions: ["recon", "scan", "exploit"],
  },
  {
    key: "continuous",
    title: "Loop",
    copy: "Repeat scan.",
    command: "persistent",
    allowActions: ["recon", "scan", "persistent"],
    blockActions: ["post_exploitation"],
  },
];

const ACTION_OPTIONS = [
  { value: "recon", copy: "Asset discovery and public signal collection." },
  { value: "scan", copy: "Service and entry-point identification." },
  { value: "exploit", copy: "Verification actions that need explicit approval." },
  { value: "persistent", copy: "Multi-round continuous checking." },
  { value: "post_exploitation", copy: "Post-exploitation steps, usually blocked." },
];

function latestEventText(event: TaskEvent | null): string {
  if (!event) return "Waiting for task events.";
  const message = event.payload.message ?? event.payload.text;
  if (typeof message === "string" && message.trim()) return message;
  if (typeof event.payload.phase === "string" && event.payload.phase.trim()) {
    return formatPhaseLabel(event.payload.phase);
  }
  return formatEventLabel(event.event);
}

function currentPhaseKey(task: TaskRecord | null, event: TaskEvent | null): string {
  if (!task) return "scope";
  if (task.status === "completed" || task.status === "failed" || task.status === "stopped") return "report";
  const text = `${event?.payload.phase ?? ""} ${event?.event ?? ""} ${task.latest_phase ?? ""}`.toLowerCase();
  if (text.includes("report")) return "report";
  if (text.includes("exploit") || text.includes("verify")) return "verify";
  if (text.includes("scan")) return "scan";
  if (text.includes("recon")) return "recon";
  return task.status === "running" ? "recon" : "scope";
}

function taskResultTitle(task: TaskRecord): string {
  if (task.status === "completed") return "Scan complete";
  if (task.status === "failed") return "Scan stopped by an error";
  if (task.status === "stopped") return "Scan stopped";
  return `Scanning ${task.target}`;
}

function eventSummary(event: TaskEvent | null): TaskSummary | null {
  const summary = event?.payload.summary;
  return summary && typeof summary === "object" ? (summary as TaskSummary) : null;
}

function taskSummary(task: TaskRecord, event: TaskEvent | null): TaskSummary | null {
  return task.summary ?? eventSummary(event);
}

function formatEventPayload(event: TaskEvent): string {
  return JSON.stringify(event.payload, null, 2);
}

function joinScopeItems(items: string[]): string {
  return items.length ? items.join(" - ") : "Auto scope";
}

function inferScopeFromTarget(value: string): { host: string; port: string; path: string } {
  const target = value.trim();
  if (!target) return { host: "", port: "", path: "" };
  try {
    const parsed = new URL(target.includes("://") ? target : `https://${target}`);
    const inferredPath = parsed.pathname && parsed.pathname !== "/" ? parsed.pathname : "";
    return { host: parsed.hostname, port: parsed.port, path: inferredPath };
  } catch {
    const withoutScheme = target.replace(/^[a-z][a-z0-9+.-]*:\/\//i, "");
    const match = withoutScheme.match(/^([^/:?#]+)(?::([^/?#]+))?(\/[^?#]*)?/);
    const inferredPath = match?.[3] && match[3] !== "/" ? match[3] : "";
    return { host: match?.[1] ?? "", port: match?.[2] ?? "", path: inferredPath };
  }
}

function uniqueActions(actions: Array<string | undefined>): string[] {
  return Array.from(new Set(actions.filter((action): action is string => Boolean(action))));
}

export function HomePage({ selectedTarget, activeTask, latestEvent, taskEvents, onCreateTask, onOpenRisk, onOpenReports, onOpenBoundary }: HomePageProps) {
  const preferences = loadUiPreferences();
  const [target, setTarget] = useState(selectedTarget ?? "");
  const [mode, setMode] = useState<CheckMode>(() => preferences.defaultCheckMode);
  const [onlyPort, setOnlyPort] = useState(preferences.defaultBoundary.onlyPort);
  const [onlyHost, setOnlyHost] = useState(preferences.defaultBoundary.onlyHost);
  const [onlyPath, setOnlyPath] = useState(preferences.defaultBoundary.onlyPath);
  const [blockedHost, setBlockedHost] = useState(preferences.defaultBoundary.blockedHost);
  const [blockedPath, setBlockedPath] = useState(preferences.defaultBoundary.blockedPath);
  const [allowActions, setAllowActions] = useState<string[]>(preferences.defaultBoundary.allowActions);
  const [blockActions, setBlockActions] = useState<string[]>(preferences.defaultBoundary.blockActions);
  const [resume, setResume] = useState(true);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [technicalLogsOpen, setTechnicalLogsOpen] = useState(() => preferences.showTechnicalLogs);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => subscribeUiPreferences((nextPreferences) => {
    setMode(nextPreferences.defaultCheckMode);
    setTechnicalLogsOpen(nextPreferences.showTechnicalLogs);
    setOnlyPort(nextPreferences.defaultBoundary.onlyPort);
    setOnlyHost(nextPreferences.defaultBoundary.onlyHost);
    setOnlyPath(nextPreferences.defaultBoundary.onlyPath);
    setBlockedHost(nextPreferences.defaultBoundary.blockedHost);
    setBlockedPath(nextPreferences.defaultBoundary.blockedPath);
    setAllowActions(nextPreferences.defaultBoundary.allowActions);
    setBlockActions(nextPreferences.defaultBoundary.blockActions);
  }), []);

  useEffect(() => {
    if (selectedTarget) setTarget(selectedTarget);
  }, [selectedTarget]);

  const selectedMode = useMemo(() => MODES.find((item) => item.key === mode) ?? MODES[1], [mode]);
  const inferredScope = inferScopeFromTarget(target);
  const effectiveOnlyHost = onlyHost.trim() || inferredScope.host;
  const effectiveOnlyPort = onlyPort.trim() || inferredScope.port;
  const effectiveOnlyPath = onlyPath.trim() || inferredScope.path;
  const scopeCount = [effectiveOnlyPort, effectiveOnlyHost, effectiveOnlyPath, blockedHost, blockedPath].filter((item) => item.trim()).length;
  const activeSummary = activeTask ? taskSummary(activeTask, latestEvent) : null;
  const boundaryBlockCount = countConstraintViolations(activeSummary?.constraint_violation_events, activeSummary?.constraint_violations);
  const scopePreview = joinScopeItems([
    effectiveOnlyHost ? `host ${effectiveOnlyHost}${onlyHost.trim() ? "" : " (inferred)"}` : "",
    effectiveOnlyPort ? `port ${effectiveOnlyPort}${onlyPort.trim() ? "" : " (inferred)"}` : "",
    effectiveOnlyPath ? `path ${effectiveOnlyPath}${onlyPath.trim() ? "" : " (inferred)"}` : "",
    blockedHost.trim() ? `block host ${blockedHost.trim()}` : "",
    blockedPath.trim() ? `block path ${blockedPath.trim()}` : "",
  ].filter(Boolean));
  const effectiveAllowActions = uniqueActions([...(allowActions.length ? allowActions : selectedMode.allowActions ?? []), selectedMode.command]);
  const effectiveBlockActions = uniqueActions(blockActions.length ? blockActions : selectedMode.blockActions ?? [])
    .filter((action) => action !== selectedMode.command);
  const allowPreview = formatActionList(effectiveAllowActions);
  const blockPreview = formatActionList(effectiveBlockActions);
  const requiresExtraCare = mode === "deep" || mode === "continuous";
  const confirmCopy = [
    `Target: ${target.trim() || "Not set"}`,
    `Mode: ${selectedMode.title}`,
    `Scope: ${scopePreview}`,
    requiresExtraCare ? "This scan may run longer." : "",
  ].join("\n");

  function buildOptions(): TaskOptions {
    return {
      only_port: parseOptionalPort(effectiveOnlyPort),
      only_host: effectiveOnlyHost || undefined,
      only_path: effectiveOnlyPath || undefined,
      blocked_host: blockedHost.trim() || undefined,
      blocked_path: blockedPath.trim() || undefined,
      allow_actions: effectiveAllowActions,
      block_actions: effectiveBlockActions,
    };
  }

  function toggleAction(
    value: string,
    selected: string[],
    setSelected: (next: string[]) => void,
    oppositeSelected?: string[],
    setOppositeSelected?: (next: string[]) => void,
  ) {
    const isSelected = selected.includes(value);
    setSelected(isSelected ? selected.filter((item) => item !== value) : [...selected, value]);
    if (!isSelected && oppositeSelected && setOppositeSelected) {
      setOppositeSelected(oppositeSelected.filter((item) => item !== value));
    }
  }

  async function submit() {
    try {
      setSubmitting(true);
      setError(null);
      await onCreateTask(selectedMode.command, target.trim(), resume, buildOptions());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start task");
    } finally {
      setSubmitting(false);
      setConfirmOpen(false);
    }
  }

  function handleStart() {
    try {
      parseOptionalPort(effectiveOnlyPort);
      if (mode === "continuous" && effectiveOnlyPath) {
        setError("Continuous mode does not support a path-only scope.");
        return;
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid port format");
      return;
    }
    if (requiresExtraCare) {
      setConfirmOpen(true);
      return;
    }
    void submit();
  }

  const phaseKey = currentPhaseKey(activeTask, latestEvent);
  const phaseSteps = [
    ["scope", "Scope"],
    ["recon", "Recon"],
    ["scan", "Scan"],
    ["verify", "Verify"],
    ["report", "Report"],
  ] as const;

  return (
    <section className="home-page">
      <div className="goby-home-board">
        <div className="goby-welcome-panel" aria-hidden="true">
          <div className="goby-map-illustration">
            <span className="map-node map-node-a">IP</span>
            <span className="map-node map-node-b">WEB</span>
            <span className="map-node map-node-c">APP</span>
            <span className="map-node map-node-d">CVE</span>
            <div className="map-ring">
              <div className="map-shield">VC</div>
            </div>
          </div>
          <div className="goby-welcome-copy">
            <h2>Welcome to VulnClaw</h2>
            <p>Attack surface mapping</p>
          </div>
          <button
            type="button"
            className={`goby-scan-orb ${submitting ? "hero-orb-busy" : ""}`}
            disabled={submitting || !target.trim()}
            onClick={handleStart}
          >
            {submitting ? "Starting" : "Scan"}
          </button>
        </div>

        <div className="scan-launch goby-task-panel">
          <div className="goby-task-title">
            <span className="goby-task-icon">▣</span>
            <strong>New Scan Task</strong>
            <button type="button" className="text-btn inline-text-btn" onClick={() => setTarget("")} aria-label="Clear target">
              ×
            </button>
          </div>
          <div className="goby-task-form">
            <label className="field scan-target-field field-wide">
              <span>IP/Domain</span>
              <textarea
                value={target}
                onChange={(event) => setTarget(event.target.value)}
                placeholder={"172.16.20.36\nexample.com\n192.0.2.0/24"}
              />
            </label>
            <label className="field field-wide">
              <span>Black IP</span>
              <textarea value={blockedHost} onChange={(event) => setBlockedHost(event.target.value)} placeholder="192.0.2.10" />
            </label>
            <label className="field">
              <span>Port</span>
              <select value={mode} onChange={(event) => setMode(event.target.value as CheckMode)}>
                {MODES.map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.title}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Custom ports</span>
              <input value={onlyPort} onChange={(event) => setOnlyPort(event.target.value)} inputMode="numeric" placeholder="21,22,80,443" />
            </label>
          </div>

          <div className="scan-mode-row" aria-label="Scan mode">
            {MODES.map((item) => (
              <button
                key={item.key}
                type="button"
                className={`scan-mode-pill ${mode === item.key ? "selected-item" : ""}`}
                onClick={() => setMode(item.key)}
              >
                <strong>{item.title}</strong>
                <span>{item.copy}</span>
              </button>
            ))}
          </div>

          <div className="scan-summary-row">
            <label className="check-row goby-printer-row">
              <input checked={resume} onChange={(event) => setResume(event.target.checked)} type="checkbox" />
              <span>Resume previous state</span>
            </label>
            <span>{scopeCount ? `${scopeCount} bounds` : "Auto scope"}</span>
            <button type="button" className="text-btn inline-text-btn" onClick={() => setAdvancedOpen((value) => !value)}>
              {advancedOpen ? "Hide advanced" : "Advanced"}
            </button>
          </div>

          <button
            type="button"
            className={`primary-btn scan-start-btn ${submitting ? "hero-orb-busy" : ""}`}
            disabled={submitting || !target.trim()}
            onClick={handleStart}
          >
            {submitting ? "Starting..." : "Start"}
          </button>
        </div>
      </div>

      {advancedOpen && (
        <SectionCard title="Advanced">
          <div className="form-grid compact-form">
            <label className="check-row">
              <input checked={resume} onChange={(event) => setResume(event.target.checked)} type="checkbox" />
              <span>Resume previous state</span>
            </label>
            <label className="field">
              <span>Port</span>
              <input value={onlyPort} onChange={(event) => setOnlyPort(event.target.value)} inputMode="numeric" placeholder="443" />
            </label>
            <label className="field">
              <span>Host</span>
              <input value={onlyHost} onChange={(event) => setOnlyHost(event.target.value)} placeholder="example.com" />
            </label>
            <label className="field">
              <span>Path</span>
              <input value={onlyPath} onChange={(event) => setOnlyPath(event.target.value)} placeholder="/admin" />
            </label>
            <label className="field">
              <span>Block host</span>
              <input value={blockedHost} onChange={(event) => setBlockedHost(event.target.value)} placeholder="staging.example.com" />
            </label>
            <label className="field">
              <span>Block path</span>
              <input value={blockedPath} onChange={(event) => setBlockedPath(event.target.value)} placeholder="/internal" />
            </label>
          </div>
          <div className="scope-summary">
            <strong>Scope</strong>
            <span>{scopePreview}</span>
            <strong>Allow</strong>
            <span>{allowPreview}</span>
            <strong>Block</strong>
            <span>{blockPreview}</span>
          </div>
          <details className="advanced-details">
            <summary>Action rules</summary>
            <div className="action-boundary-panel">
              <div className="action-choice-grid">
                {ACTION_OPTIONS.map((action) => (
                  <button
                    key={`allow-${action.value}`}
                    type="button"
                    className={`action-choice ${allowActions.includes(action.value) ? "selected-item" : ""}`}
                    onClick={() => toggleAction(action.value, allowActions, setAllowActions, blockActions, setBlockActions)}
                  >
                    <strong>{formatActionLabel(action.value)}</strong>
                  </button>
                ))}
              </div>
              <div className="action-choice-grid">
                {ACTION_OPTIONS.map((action) => (
                  <button
                    key={`block-${action.value}`}
                    type="button"
                    className={`action-choice action-choice-block ${blockActions.includes(action.value) ? "selected-item" : ""}`}
                    onClick={() => toggleAction(action.value, blockActions, setBlockActions, allowActions, setAllowActions)}
                  >
                    <strong>Block {formatActionLabel(action.value)}</strong>
                  </button>
                ))}
              </div>
            </div>
          </details>
          {error && <div className="error-box">{error}</div>}
        </SectionCard>
      )}

      {activeTask && (
        <SectionCard title="Running" aside={<span className="status-badge">{formatTaskStatus(activeTask.status)}</span>}>
          <div className="check-progress-card">
            <div className="check-progress-head">
              <div>
                <span className="pill">Current task</span>
                <h3>{taskResultTitle(activeTask)}</h3>
                <p>{latestEventText(latestEvent)}</p>
              </div>
              <div className="check-progress-target">
                <span>Target</span>
                <strong>{activeTask.target}</strong>
              </div>
            </div>
            <div className="check-stepper">
              {phaseSteps.map(([key, label]) => {
                const done = phaseSteps.findIndex(([stepKey]) => stepKey === key) <= phaseSteps.findIndex(([stepKey]) => stepKey === phaseKey);
                return (
                  <div key={key} className={`check-step ${done ? "check-step-done" : ""}`}>
                    <span />
                    <strong>{label}</strong>
                  </div>
                );
              })}
            </div>
            <div className="next-actions">
              <button type="button" className="primary-btn" onClick={onOpenRisk}>View results</button>
              <button type="button" className="secondary-btn" onClick={onOpenReports}>View reports</button>
              <button type="button" className="secondary-btn" onClick={onOpenBoundary}>View boundary</button>
            </div>
            {activeSummary && (
              <div className="stats-grid check-result-stats">
                <article className="stat">
                  <span className="stat-label">Verified</span>
                  <strong>{activeSummary.verified_count}</strong>
                </article>
                <article className="stat">
                  <span className="stat-label">Pending</span>
                  <strong>{activeSummary.pending_count}</strong>
                </article>
                <article className="stat">
                  <span className="stat-label">Boundary hits</span>
                  <strong>{boundaryBlockCount}</strong>
                </article>
                <article className="stat">
                  <span className="stat-label">Snapshot</span>
                  <strong>{activeSummary.snapshot_id || "Saved"}</strong>
                </article>
              </div>
            )}
            <div className="technical-log-panel">
              <button type="button" className="text-btn technical-log-toggle" onClick={() => setTechnicalLogsOpen((value) => !value)}>
                {technicalLogsOpen ? "Hide raw events" : "Show raw events"}
              </button>
              {technicalLogsOpen && (
                <div className="technical-log-stream" aria-live="polite">
                  {taskEvents.length ? (
                    taskEvents.slice(-24).map((event) => (
                      <article key={`${event.task_id}-${event.timestamp}-${event.event}`} className="technical-log-entry">
                        <header>
                          <strong>{formatEventLabel(event.event)}</strong>
                          <span>{new Date(event.timestamp).toLocaleTimeString()}</span>
                        </header>
                        <pre>{formatEventPayload(event)}</pre>
                      </article>
                    ))
                  ) : (
                    <div className="empty-state">No raw task events yet.</div>
                  )}
                </div>
              )}
            </div>
          </div>
        </SectionCard>
      )}

      <ConfirmDialog
        open={confirmOpen}
        title="Start deep scan?"
        copy={confirmCopy}
        confirmLabel="Start scan"
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => {
          setConfirmOpen(false);
          void submit();
        }}
      />
    </section>
  );
}
