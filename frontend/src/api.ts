export type Task = {
  instruction: string;
  repository_path: string;
  demo_id: string | null;
  context_mode: string;
};
export type Run = {
  id: string;
  task: Task;
  status: string;
  step_count: number;
  token_usage: number;
  created_at: string;
  updated_at: string;
  error: string | null;
  answer?: string;
  plan?: { id: string; title: string; status: string; dependencies: string[] }[];
  report?: {
    passed: boolean;
    checks: { name: string; passed: boolean; output?: string; error?: string }[];
    diff: string;
    changed_files: string[];
    tests_passed: number;
    sandbox: string;
  };
};
export type Event = {
  id: string;
  sequence: number;
  type: string;
  at: string;
  payload: Record<string, unknown>;
};
export type Approval = {
  id: string;
  status: string;
  action: { tool: string; arguments: { patch?: string } };
};
export type Dashboard = {
  runs: number;
  completed: number;
  active: number;
  failed: number;
  model_calls: number;
  tool_calls: number;
  context_reduction: number;
  compiled_tokens_estimate: number;
  backend: string;
  scope: string;
};
export type Demo = { id: string; title: string; instruction: string };
export type Config = { backend: string; model: string; sandbox: string; authentication: boolean };

export function setKey(key: string) {
  sessionStorage.setItem('atlas.key', key);
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const key = sessionStorage.getItem('atlas.key');
  const response = await fetch(path, {
    ...options,
    headers: { ...(key ? { Authorization: `Bearer ${key}` } : {}), ...options.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function post<T>(path: string, body?: unknown) {
  return api<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}

export async function savePatch(runId: string) {
  const key = sessionStorage.getItem('atlas.key');
  const response = await fetch(`/api/runs/${runId}/patch`, {
    headers: key ? { Authorization: `Bearer ${key}` } : {},
  });
  if (!response.ok) throw new Error('Patch is not available');
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = `atlas-${runId.slice(0, 8)}.patch`;
  link.click();
  URL.revokeObjectURL(url);
}
