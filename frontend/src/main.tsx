import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  api,
  post,
  savePatch,
  setKey,
  type Approval,
  type Config,
  type Dashboard,
  type Demo,
  type Event,
  type Run,
} from './api';
import './style.css';

const terminal = new Set(['completed', 'failed', 'cancelled']);
const labels: Record<string, string> = {
  queued: 'Queued',
  running: 'Running',
  waiting_tool: 'Running tool',
  waiting_approval: 'Awaiting approval',
  completed: 'Verified',
  failed: 'Failed',
  cancelled: 'Cancelled',
};
const compact = (n: number) =>
  new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(n);
const shortTime = (s: string) =>
  new Date(s).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

function Mark({ small = false }: { small?: boolean }) {
  return (
    <svg
      className={small ? 'mark small' : 'mark'}
      viewBox="0 0 40 40"
      fill="none"
      aria-hidden="true"
    >
      <path d="M20 4 36 33H4L20 4Z" stroke="currentColor" strokeWidth="2.5" />
      <path d="m20 15 10 18H10l10-18Z" fill="currentColor" />
      <path d="M3 39h34" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

function App() {
  const [view, setView] = useState('overview');
  const [config, setConfig] = useState<Config | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [demos, setDemos] = useState<Demo[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [newRun, setNewRun] = useState(false);
  const [repository, setRepository] = useState('');
  const [instruction, setInstruction] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [keyOpen, setKeyOpen] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const [c, d, r, fixtures] = await Promise.all([
        api<Config>('/api/config'),
        api<Dashboard>('/api/dashboard'),
        api<Run[]>('/api/runs'),
        api<Demo[]>('/api/demos'),
      ]);
      setConfig(c);
      setDashboard(d);
      setRuns(r);
      setDemos(fixtures);
      setError('');
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 2000);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!selected) return;
    let disposed = false;
    async function load() {
      try {
        const [r, e, a] = await Promise.all([
          api<Run>(`/api/runs/${selected}`),
          api<Event[]>(`/api/runs/${selected}/events`),
          api<Approval[]>(`/api/runs/${selected}/approvals`),
        ]);
        if (!disposed) {
          setRun(r);
          setEvents(e);
          setApprovals(a);
        }
      } catch (e) {
        if (!disposed) setError((e as Error).message);
      }
    }
    void load();
    const timer = setInterval(() => void load(), 1200);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [selected]);

  async function perform(action: () => Promise<unknown>) {
    setBusy(true);
    setError('');
    try {
      await action();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function startDemo(id: string) {
    await perform(async () => {
      const r = await post<Run>(`/api/demos/${id}/run`);
      setRun(r);
      setSelected(r.id);
      setView('runs');
    });
  }

  async function upload(file: File) {
    await perform(async () => {
      const result = await api<{ repository_path: string }>('/api/repositories', {
        method: 'POST',
        headers: { 'Content-Type': 'application/zip' },
        body: file,
      });
      setRepository(result.repository_path);
    });
  }

  const timeline = events.filter((e) =>
    [
      'run.created',
      'plan.created',
      'tool.started',
      'tool.completed',
      'approval.requested',
      'run.recovered',
      'verification.completed',
      'run.completed',
      'run.failed',
    ].includes(e.type),
  );
  const active = runs.filter((r) => !terminal.has(r.status));
  const [evidenceTab, setEvidenceTab] = useState('timeline');

  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="Atlas home">
          <Mark small />
          <span>
            atlas<span className="brand-dot">.</span>
          </span>
        </a>
        <div className="sidebar-label">RUNTIME CONSOLE</div>
        <nav>
          {[
            ['overview', '◈', 'Overview'],
            ['runs', '▤', 'Agent runs'],
            ['evidence', '⌘', 'Evidence'],
          ].map(([id, icon, name]) => (
            <button
              className={view === id ? 'nav-item current' : 'nav-item'}
              key={id}
              onClick={() => setView(id)}
            >
              <span className="nav-icon">{icon}</span>
              {name}
              {id === 'runs' && active.length > 0 && <b>{active.length}</b>}
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <div className="small-dot" />
          LOCAL CONTROL PLANE
          <p>
            Plan. Execute. Verify.
            <br />
            Resume from the last event.
          </p>
        </div>
        <div className="sidebar-bottom">
          <span className="version">v1.0.0</span>
          <button onClick={() => setKeyOpen(true)}>API access ↗</button>
        </div>
      </aside>
      <main>
        <header>
          <div className="breadcrumb">
            Workspace <span>/</span>{' '}
            <strong>
              {view === 'overview' ? 'Overview' : view === 'runs' ? 'Agent runs' : 'Evidence'}
            </strong>
          </div>
          <div className="header-right">
            <span className="connection">
              <i />
              {config ? 'Runtime connected' : 'Connecting'}
            </span>
            <button
              className="avatar"
              onClick={() => setKeyOpen(true)}
              aria-label="Configure API access"
            >
              A
            </button>
          </div>
        </header>
        <div className="content">
          {error && (
            <div className="error" role="alert">
              {error}
              <button onClick={() => setKeyOpen(true)}>Configure access</button>
            </div>
          )}
          <div className="page-heading">
            <div>
              <div className="eyebrow">
                AGENT OPERATIONS / {config?.backend === 'demo' ? 'FIXTURE MODE' : 'MODEL MODE'}
              </div>
              <h1>
                {view === 'overview'
                  ? 'Every step, accounted for.'
                  : view === 'runs'
                    ? 'Work in progress.'
                    : 'Show your work.'}
              </h1>
              <p>
                {view === 'overview'
                  ? 'A durable workspace for agents that inspect, repair and verify code.'
                  : view === 'runs'
                    ? 'Follow the plan, review a patch, and inspect the final verification.'
                    : 'Trace decisions back to files, tool results and immutable events.'}
              </p>
            </div>
            <button className="primary" onClick={() => setNewRun(true)}>
              ＋ New agent run
            </button>
          </div>
          <section className="stats" aria-label="Runtime statistics">
            {[
              ['Active runs', dashboard?.active ?? 0, 'Live execution'],
              ['Verified patches', dashboard?.completed ?? 0, 'Tests + checks passed'],
              ['Tool executions', dashboard?.tool_calls ?? 0, 'Recorded in the event store'],
              [
                'Context reduction',
                dashboard ? `${Math.round(dashboard.context_reduction * 100)}%` : '—',
                'Estimated tokens · latest 100 runs',
              ],
            ].map(([name, value, note]) => (
              <div className="stat" key={String(name)}>
                <div className="stat-label">
                  {name}
                  <span>↗</span>
                </div>
                <div className="stat-value">
                  {typeof value === 'number' ? compact(value) : value}
                </div>
                <div className="stat-note">{note}</div>
              </div>
            ))}
          </section>

          {view === 'overview' && (
            <>
              <section className="hero-panel">
                <div>
                  <div className="eyebrow">BUILT TO PICK UP WHERE IT LEFT OFF</div>
                  <h2>
                    Long tasks.
                    <br />
                    Durable execution.
                  </h2>
                  <p>
                    From the first hypothesis to the final diff,
                    <br />
                    keep the evidence and the execution together.
                  </p>
                  <button
                    className="light-button"
                    disabled={busy}
                    onClick={() => startDemo('auth')}
                  >
                    Run authentication demo <span>↗</span>
                  </button>
                </div>
                <div className="execution-map">
                  <div className="map-top">
                    <span className="live-dot" />
                    EXECUTION PATH<span>01 — 04</span>
                  </div>
                  {[
                    ['01', 'Inspect', 'Source files + existing tests'],
                    ['02', 'Reproduce', 'Capture the failing behavior'],
                    ['03', 'Repair', 'Isolated patch + regression test'],
                    ['04', 'Verify', 'Independent final checks'],
                  ].map(([n, title, subtitle]) => (
                    <div className="map-row" key={n}>
                      <span className="map-number">{n}</span>
                      <div>
                        <strong>{title}</strong>
                        <small>{subtitle}</small>
                      </div>
                      <span className="map-check">↳</span>
                    </div>
                  ))}
                </div>
              </section>
              <div className="section-heading">
                <div>
                  <h2>Start with a known failure</h2>
                  <p>Four small repositories. Real tools, actual tests, downloadable diffs.</p>
                </div>
                <span className="muted-tag">
                  {config?.backend === 'demo' ? 'DETERMINISTIC DEMOS' : 'LIVE MODEL'}
                </span>
              </div>
              <section className="demo-grid">
                {demos.map((demo, i) => (
                  <button
                    className="demo-card"
                    disabled={busy}
                    key={demo.id}
                    onClick={() => startDemo(demo.id)}
                  >
                    <div>
                      <span className="demo-number">0{i + 1}</span>
                      <span>↗</span>
                    </div>
                    <h3>{demo.title}</h3>
                    <p>{demo.instruction.split('.')[0]}.</p>
                    <span className="demo-footer">
                      Launch run <span>→</span>
                    </span>
                  </button>
                ))}
              </section>
              <div className="runtime-note">
                <span>◉</span>
                <p>
                  {config?.backend === 'demo'
                    ? 'Fixture mode uses a deterministic driver. Its tests and patch results are real; model tokens and GPU measurements are unavailable.'
                    : `Model: ${config?.model || 'connecting'} · Sandbox: ${config?.sandbox || 'connecting'}. Patch approval and final verification are enforced by the runtime.`}
                </p>
              </div>
            </>
          )}

          {view !== 'overview' && (
            <section className="workbench">
              <div className="run-list">
                <div className="panel-heading">
                  <h2>Recent runs</h2>
                  <span>{runs.length}</span>
                </div>
                {runs.length === 0 ? (
                  <div className="empty small-empty">
                    <p>No runs yet.</p>
                    <button className="secondary" onClick={() => startDemo('auth')}>
                      Try a demo →
                    </button>
                  </div>
                ) : (
                  runs.map((r) => (
                    <button
                      key={r.id}
                      className={selected === r.id ? 'run-row selected' : 'run-row'}
                      onClick={() => {
                        setSelected(r.id);
                        setRun(null);
                        setEvents([]);
                      }}
                    >
                      <div className="run-row-top">
                        <code>{r.id.slice(0, 8)}</code>
                        <span className={`badge ${r.status}`}>{labels[r.status]}</span>
                      </div>
                      <h3>{r.task.instruction}</h3>
                      <div className="run-row-bottom">
                        <span>{shortTime(r.created_at)}</span>
                        <span>{r.step_count} model turns</span>
                      </div>
                    </button>
                  ))
                )}
              </div>
              <div className="run-detail">
                {!run ? (
                  <div className="empty">
                    <Mark />
                    <h2>Select a run</h2>
                    <p>Its plan, timeline and verification report will appear here.</p>
                  </div>
                ) : (
                  <>
                    <div className="detail-heading">
                      <div>
                        <div className="eyebrow">RUN / {run.id.slice(0, 8).toUpperCase()}</div>
                        <h2>
                          {run.task.demo_id
                            ? demos.find((d) => d.id === run.task.demo_id)?.title
                            : 'Repository task'}
                        </h2>
                      </div>
                      <span className={`badge ${run.status}`}>{labels[run.status]}</span>
                    </div>
                    <p className="task-text">{run.task.instruction}</p>
                    {run.plan && (
                      <div className="plan-strip">
                        {run.plan.map((node, i) => (
                          <div className={`plan-step ${node.status}`} key={node.id}>
                            <span>
                              {node.status === 'succeeded' ? '✓' : String(i + 1).padStart(2, '0')}
                            </span>
                            <strong>{node.title}</strong>
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="detail-tabs">
                      {['timeline', 'verification', 'patch'].map((tab) => (
                        <button
                          className={evidenceTab === tab ? 'active' : ''}
                          onClick={() => setEvidenceTab(tab)}
                          key={tab}
                        >
                          {tab[0].toUpperCase() + tab.slice(1)}
                        </button>
                      ))}
                      <div className="tab-spacer" />
                      {!terminal.has(run.status) && (
                        <button
                          className="danger-text"
                          disabled={busy}
                          onClick={() => perform(() => post(`/api/runs/${run.id}/cancel`))}
                        >
                          Cancel run
                        </button>
                      )}
                    </div>
                    {approvals
                      .filter((a) => a.status === 'pending')
                      .map((a) => (
                        <div className="approval" key={a.id}>
                          <h3>Patch ready for review</h3>
                          <p>Approve this exact patch to apply it to the isolated workspace.</p>
                          <pre>{a.action.arguments.patch}</pre>
                          <div>
                            <button
                              className="primary"
                              disabled={busy}
                              onClick={() =>
                                perform(() =>
                                  post(`/api/runs/${run.id}/approvals/${a.id}`, { approved: true }),
                                )
                              }
                            >
                              Approve patch
                            </button>
                            <button
                              className="secondary"
                              disabled={busy}
                              onClick={() =>
                                perform(() =>
                                  post(`/api/runs/${run.id}/approvals/${a.id}`, {
                                    approved: false,
                                  }),
                                )
                              }
                            >
                              Reject
                            </button>
                          </div>
                        </div>
                      ))}
                    {evidenceTab === 'timeline' && (
                      <div className="timeline">
                        {timeline.map((event) => (
                          <details className="timeline-item" key={event.id}>
                            <summary>
                              <span className="timeline-dot" />
                              <span className="event-time">{shortTime(event.at)}</span>
                              <span className="event-title">
                                {event.type.replaceAll('.', ' / ')}
                              </span>
                              <code>#{event.sequence}</code>
                            </summary>
                            <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                            <small>Evidence ID: {event.id}</small>
                          </details>
                        ))}
                        {!terminal.has(run.status) && (
                          <div className="working">
                            <span className="live-dot" />
                            Waiting for the next event…
                          </div>
                        )}
                      </div>
                    )}
                    {evidenceTab === 'verification' &&
                      (run.report ? (
                        <div className="verification">
                          <div className="verification-summary">
                            <span className={run.report.passed ? 'good' : 'bad'}>
                              {run.report.passed ? '✓ Final checks passed' : 'Final checks failed'}
                            </span>
                            <span>
                              {run.report.tests_passed} tests · {run.report.sandbox}
                            </span>
                          </div>
                          {run.report.checks.map((check) => (
                            <details key={check.name}>
                              <summary>
                                <span className={check.passed ? 'good' : 'bad'}>
                                  {check.passed ? '✓' : '×'}
                                </span>
                                {check.name.replaceAll('_', ' ')}
                              </summary>
                              {check.output && <pre>{check.output}</pre>}
                              {check.error && <p className="bad">{check.error}</p>}
                            </details>
                          ))}
                          {run.answer && (
                            <div className="answer">
                              <h3>Agent report</h3>
                              <p>{run.answer}</p>
                            </div>
                          )}
                        </div>
                      ) : (
                        <div className="empty compact-empty">
                          <p>The report is available after final verification.</p>
                        </div>
                      ))}
                    {evidenceTab === 'patch' &&
                      (run.report ? (
                        <div className="patch-panel">
                          <div className="patch-heading">
                            <span>{run.report.changed_files.length} changed files</span>
                            <button
                              className="secondary"
                              onClick={() => perform(() => savePatch(run.id))}
                            >
                              Download .patch ↓
                            </button>
                          </div>
                          <pre className="diff">
                            {run.report.diff.split('\n').map((line, index) => (
                              <span
                                className={
                                  line.startsWith('+')
                                    ? 'added'
                                    : line.startsWith('-')
                                      ? 'removed'
                                      : ''
                                }
                                key={index}
                              >
                                {line}
                                {'\n'}
                              </span>
                            ))}
                          </pre>
                        </div>
                      ) : (
                        <div className="empty compact-empty">
                          <p>The final diff appears after verification.</p>
                        </div>
                      ))}
                    {run.error && <div className="error">{run.error}</div>}
                  </>
                )}
              </div>
            </section>
          )}
          <footer>
            <span>ATLAS AGENT RUNTIME</span>
            <span>Evidence over assumptions.</span>
          </footer>
        </div>
      </main>
      {newRun && (
        <div className="modal-backdrop" onClick={() => setNewRun(false)}>
          <section className="modal" onClick={(e) => e.stopPropagation()}>
            <button className="close" onClick={() => setNewRun(false)} aria-label="Close">
              ×
            </button>
            <div className="eyebrow">NEW EXECUTION</div>
            <h2>Give the agent a task.</h2>
            {config?.backend === 'demo' ? (
              <>
                <p>Choose a bundled failure to explore the complete runtime.</p>
                <div className="modal-demos">
                  {demos.map((d) => (
                    <button
                      className="secondary"
                      key={d.id}
                      disabled={busy}
                      onClick={() => {
                        setNewRun(false);
                        void startDemo(d.id);
                      }}
                    >
                      {d.title} →
                    </button>
                  ))}
                </div>
                <p className="help">
                  Configure a local or cloud model to work with your own repository.
                </p>
              </>
            ) : (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void perform(async () => {
                    const r = await post<Run>('/api/runs', {
                      repository_path: repository,
                      instruction,
                      approval_policy: 'manual',
                    });
                    setRun(r);
                    setSelected(r.id);
                    setNewRun(false);
                    setView('runs');
                  });
                }}
              >
                <label>
                  Repository
                  <input
                    required
                    value={repository}
                    onChange={(e) => setRepository(e.target.value)}
                    placeholder="Path inside the configured repository folder"
                  />
                </label>
                <input
                  className="hidden"
                  ref={fileInput}
                  type="file"
                  accept=".zip"
                  onChange={(e) => {
                    if (e.target.files?.[0]) void upload(e.target.files[0]);
                  }}
                />
                <button
                  className="upload-button"
                  type="button"
                  disabled={busy}
                  onClick={() => fileInput.current?.click()}
                >
                  Upload a repository ZIP ↗
                </button>
                <label>
                  Task
                  <textarea
                    required
                    minLength={3}
                    value={instruction}
                    onChange={(e) => setInstruction(e.target.value)}
                    placeholder="Investigate the failing auth tests, repair the bug and add regression coverage."
                  />
                </label>
                <p className="help">The source is copied. Patches wait for your review.</p>
                <button className="primary" disabled={busy || !repository || !instruction}>
                  Create run →
                </button>
              </form>
            )}
          </section>
        </div>
      )}
      {keyOpen && (
        <div className="modal-backdrop" onClick={() => setKeyOpen(false)}>
          <section className="modal" onClick={(e) => e.stopPropagation()}>
            <button className="close" onClick={() => setKeyOpen(false)} aria-label="Close">
              ×
            </button>
            <div className="eyebrow">API ACCESS</div>
            <h2>Connect your console.</h2>
            <p>
              Enter the API key configured on your runtime. It stays in this browser tab's session.
            </p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                setKey(apiKey);
                setKeyOpen(false);
                void refresh();
              }}
            >
              <label>
                API key
                <input
                  type="password"
                  autoComplete="off"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="Bearer key"
                />
              </label>
              <button className="primary">Connect →</button>
            </form>
          </section>
        </div>
      )}
    </div>
  );
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
