export type Teammate = { id: number; name: string; description: string }
export type Message = { id: number; role: 'user' | 'assistant'; content: string; created_at: string }
export type ExecutionEvent = {
  id: string
  run_id: string | null
  tool_call_id: string | null
  event_type: string
  status: string
  tool_name: string | null
  payload: Record<string, unknown>
  created_at?: string
}
export type Conversation = {
  id: number
  teammate_id: number
  title: string
  messages: Message[]
  execution_events: ExecutionEvent[]
}
export type StreamEvent = {
  type: string
  content?: string
  message?: string
  run_id?: string
  approval_id?: string
  tool_name?: string
  arguments?: Record<string, unknown>
  reason?: string
  event_type?: string
  status?: string
  id?: string
  payload?: Record<string, unknown>
  tool_call_id?: string
  summary?: Record<string, unknown>
}

export type ConnectorStatus = {
  id: string
  display_name: string
  connected: boolean
  healthy: boolean
  credentials_writable: boolean
  bot_username: string | null
  bot_id: number | null
  chat_allowlist: number[]
  capabilities: { name: string; description: string; permission: string }[]
  message: string
  token_configured: boolean
}

const API = 'http://127.0.0.1:8000'
const SESSION_KEY = 'ai_teammates_session'

export function getSessionToken(): string | null {
  try {
    return sessionStorage.getItem(SESSION_KEY)
  } catch {
    return null
  }
}

export function setSessionToken(token: string | null) {
  try {
    if (token) sessionStorage.setItem(SESSION_KEY, token)
    else sessionStorage.removeItem(SESSION_KEY)
  } catch {
    /* ignore */
  }
}

function authHeaders(extra?: HeadersInit): HeadersInit {
  const token = getSessionToken()
  const headers: Record<string, string> = { ...(extra as Record<string, string>) }
  if (token) headers['X-AI-Teammates-Session'] = token
  return headers
}

/** Map SSE payloads into durable activity cards shown in the UI. */
export function toActivity(event: StreamEvent): ExecutionEvent | null {
  if (event.type === 'execution' && event.id) {
    return {
      id: event.id,
      run_id: event.run_id || null,
      tool_call_id: event.tool_call_id || null,
      event_type: event.event_type || 'tool_result',
      status: event.status || 'completed',
      tool_name: event.tool_name || null,
      payload: event.payload || {},
    }
  }
  if (event.type === 'approval_required' && event.approval_id) {
    return {
      id: event.approval_id,
      run_id: event.run_id || null,
      tool_call_id: event.tool_call_id || null,
      event_type: 'approval',
      status: 'waiting_approval',
      tool_name: event.tool_name || null,
      payload: {
        arguments: event.arguments || {},
        reason: event.reason || 'Explicit user approval is required',
      },
    }
  }
  return null
}

/** Successful terminals are explicit. A closed socket alone is not success. */
export function isSuccessfulTerminal(event: StreamEvent): boolean {
  return event.type === 'done' || event.type === 'approval_required'
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = authHeaders(init?.headers)
  const response = await fetch(API + path, { ...init, headers })
  if (!response.ok) throw new Error((await response.text()) || `Request failed (${response.status})`)
  return response.json()
}

async function* stream(path: string, init: RequestInit): AsyncGenerator<StreamEvent> {
  const headers = authHeaders(init.headers)
  const response = await fetch(API + path, { ...init, headers })
  if (!response.ok || !response.body) throw new Error(await response.text())
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const events = buffer.split('\n\n')
    buffer = events.pop() || ''
    for (const event of events) {
      const line = event.split('\n').find((l) => l.startsWith('data: '))
      if (line) yield JSON.parse(line.slice(6)) as StreamEvent
    }
  }
}

export type ComputerStatus = {
  id: string
  teammate_id: number
  provider: string
  state: string
  human_control: boolean
  last_url: string | null
  last_error: string | null
  last_action: string | null
  current_task: string | null
  workspace_path: string
  artifacts_path: string
  browser_open: boolean
  isolation_note?: string
  message?: string | null
}

export const api = {
  teammates: () => request<Teammate[]>('/api/teammates'),
  createTeammate: (name: string, description = '') =>
    request<Teammate>('/api/teammates', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description }),
    }),
  computer: (teammateId: number) => request<ComputerStatus>(`/api/teammates/${teammateId}/computer`),
  startComputer: (teammateId: number, headed = false) =>
    request<ComputerStatus>(`/api/teammates/${teammateId}/computer/start?headed=${headed}`, { method: 'POST' }),
  stopComputer: (teammateId: number) =>
    request<ComputerStatus>(`/api/teammates/${teammateId}/computer/stop`, { method: 'POST' }),
  pauseComputer: (teammateId: number) =>
    request<ComputerStatus>(`/api/teammates/${teammateId}/computer/pause`, { method: 'POST' }),
  resumeComputer: (teammateId: number) =>
    request<ComputerStatus>(`/api/teammates/${teammateId}/computer/resume`, { method: 'POST' }),
  takeoverComputer: (teammateId: number) =>
    request<ComputerStatus>(`/api/teammates/${teammateId}/computer/takeover`, { method: 'POST' }),
  releaseComputer: (teammateId: number) =>
    request<ComputerStatus>(`/api/teammates/${teammateId}/computer/release`, { method: 'POST' }),
  clearComputerBrowser: (teammateId: number) =>
    request<ComputerStatus>(`/api/teammates/${teammateId}/computer/clear-browser?confirm=true`, { method: 'POST' }),
  computerPreviewUrl: (teammateId: number) =>
    `http://127.0.0.1:8000/api/teammates/${teammateId}/computer/preview?ts=${Date.now()}`,
  workspaceFiles: (teammateId: number, area: 'workspace' | 'artifacts' = 'workspace') =>
    request<{ area: string; files: { path: string; bytes: number; suffix: string }[] }>(
      `/api/teammates/${teammateId}/workspace?area=${area}`,
    ),
  workspaceFile: (teammateId: number, path: string, area: 'workspace' | 'artifacts' = 'workspace') =>
    request<{ path: string; content: string; bytes: number }>(
      `/api/teammates/${teammateId}/workspace/file?path=${encodeURIComponent(path)}&area=${area}`,
    ),
  conversations: (teammateId: number) => request<Conversation[]>(`/api/teammates/${teammateId}/conversations`),
  createConversation: (teammateId: number) =>
    request<Conversation>(`/api/teammates/${teammateId}/conversations`, { method: 'POST' }),
  conversation: (id: number) => request<Conversation>(`/api/conversations/${id}`),
  streamMessage: (id: number, content: string) =>
    stream(`/api/conversations/${id}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    }),
  decideApproval: (conversationId: number, approvalId: string, decision: 'approved' | 'rejected') =>
    stream(`/api/conversations/${conversationId}/approvals/${approvalId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision }),
    }),
  cancelRun: (runId: string) =>
    request<{ cancelled: boolean }>(`/api/runs/${runId}/cancel`, { method: 'POST' }),
  connectors: () => request<ConnectorStatus[]>('/api/connectors'),
  telegramStatus: () => request<ConnectorStatus>('/api/connectors/telegram'),
  configureTelegram: (token: string, chat_allowlist: number[]) =>
    request<ConnectorStatus>('/api/connectors/telegram/configure', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, chat_allowlist }),
    }),
  verifyTelegram: () => request<ConnectorStatus>('/api/connectors/telegram/verify', { method: 'POST' }),
  disconnectTelegram: () => request<ConnectorStatus>('/api/connectors/telegram/disconnect', { method: 'POST' }),
  automations: () => request<Automation[]>('/api/automations'),
  createAutomation: (body: AutomationCreate) =>
    request<Automation>('/api/automations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  updateAutomation: (id: string, body: Partial<AutomationCreate> & { paused?: boolean; enabled?: boolean }) =>
    request<Automation>(`/api/automations/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  runAutomation: (id: string) => request<AutomationRun>(`/api/automations/${id}/run`, { method: 'POST' }),
  automationRuns: (id: string) => request<AutomationRun[]>(`/api/automations/${id}/runs`),
  allAutomationRuns: () => request<AutomationRun[]>('/api/automation-runs'),
  automationRun: (runId: string) => request<AutomationRunReceipt>(`/api/automation-runs/${runId}`),
  cancelAutomationRun: (runId: string) =>
    request<AutomationRun>(`/api/automation-runs/${runId}/cancel`, { method: 'POST' }),
  decideAutomationApproval: (runId: string, approvalId: string, decision: 'approved' | 'rejected') =>
    request<{ status: string; run_id: string }>(`/api/automation-runs/${runId}/approvals/${approvalId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision }),
    }),
  createWebhook: (automationId: string) =>
    request<WebhookCreated>(`/api/automations/${automationId}/webhooks`, { method: 'POST' }),
  notifications: (unreadOnly = false) =>
    request<AppNotification[]>(`/api/notifications?unread_only=${unreadOnly ? 'true' : 'false'}`),
  readNotification: (id: string) => request<AppNotification>(`/api/notifications/${id}/read`, { method: 'POST' }),
  memories: (params?: { scope?: string; scope_id?: string; memory_type?: string }) => {
    const q = new URLSearchParams()
    if (params?.scope) q.set('scope', params.scope)
    if (params?.scope_id) q.set('scope_id', params.scope_id)
    if (params?.memory_type) q.set('memory_type', params.memory_type)
    const qs = q.toString()
    return request<MemoryRecord[]>(`/api/memories${qs ? `?${qs}` : ''}`)
  },
  createMemory: (body: MemoryCreate) =>
    request<MemoryRecord>('/api/memories', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  updateMemory: (id: string, content: string) =>
    request<MemoryRecord>(`/api/memories/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    }),
  deleteMemory: (id: string) => request<MemoryRecord>(`/api/memories/${id}`, { method: 'DELETE' }),
  clearTeammateMemories: (teammateId: number) =>
    request<{ cleared: number }>(`/api/teammates/${teammateId}/memories/clear`, { method: 'POST' }),
  retrieveMemories: (teammateId: number, query = '', projectId?: string) => {
    const q = new URLSearchParams({ teammate_id: String(teammateId), query })
    if (projectId) q.set('project_id', projectId)
    return request<MemoryRecord[]>(`/api/memories/retrieve/for-task?${q}`)
  },
  skillsCatalog: () => request<SkillPackage[]>('/api/skills/catalog'),
  skillInstalls: (teammateId?: number) =>
    request<SkillInstall[]>(
      `/api/skills/installs${teammateId != null ? `?teammate_id=${teammateId}` : ''}`,
    ),
  installSkill: (body: { skill_id: string; teammate_id?: number | null; reviewed?: boolean; enabled?: boolean }) =>
    request<SkillInstall>('/api/skills/installs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  reviewSkill: (installId: string) =>
    request<SkillInstall>(`/api/skills/installs/${installId}/review`, { method: 'POST' }),
  enableSkill: (installId: string, enabled: boolean) =>
    request<SkillInstall>(`/api/skills/installs/${installId}/enable`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    }),
  skillPackage: (skillId: string) => request<SkillPackage>(`/api/skills/${skillId}`),
  connectorGrants: (teammateId?: number) =>
    request<ConnectorGrant[]>(
      `/api/connector-grants${teammateId != null ? `?teammate_id=${teammateId}` : ''}`,
    ),
  upsertConnectorGrant: (body: {
    teammate_id: number
    connector_id: string
    operations: string[]
    resource_scope?: Record<string, unknown>
    enabled?: boolean
  }) =>
    request<ConnectorGrant>('/api/connector-grants', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  revokeConnectorGrant: (grantId: string) =>
    request<{ revoked: boolean }>(`/api/connector-grants/${grantId}`, { method: 'DELETE' }),
  workflows: (teammateId?: number) =>
    request<WorkflowRun[]>(`/api/workflows${teammateId != null ? `?teammate_id=${teammateId}` : ''}`),
  createWorkflow: (body: {
    teammate_id: number
    title?: string
    instruction: string
    skill_id?: string
    conversation_id?: number
  }) =>
    request<WorkflowRun>('/api/workflows', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  workflow: (id: string) => request<WorkflowRun>(`/api/workflows/${id}`),
  advanceWorkflow: (id: string, completeExecute = false) =>
    request<WorkflowRun>(`/api/workflows/${id}/advance?complete_execute=${completeExecute}`, { method: 'POST' }),
  teams: () => request<Team[]>('/api/teams'),
  createTeam: (body: TeamCreate) =>
    request<Team>('/api/teams', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  updateTeam: (id: string, body: Partial<TeamCreate> & { enabled?: boolean }) =>
    request<Team>(`/api/teams/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  teamRuns: (teamId?: string) =>
    request<TeamRunOut[]>(`/api/team-runs${teamId ? `?team_id=${teamId}` : ''}`),
  createTeamRun: (teamId: string, body: { objective: string; subtasks: TeamSubtaskIn[] }) =>
    request<TeamRunOut>(`/api/teams/${teamId}/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  teamRun: (id: string) => request<TeamRunOut>(`/api/team-runs/${id}`),
  tickTeamRun: (id: string, maxSteps?: number) =>
    request<TeamRunOut>(
      `/api/team-runs/${id}/tick${maxSteps != null ? `?max_steps=${maxSteps}` : ''}`,
      { method: 'POST' },
    ),
  cancelTeamRun: (id: string) => request<TeamRunOut>(`/api/team-runs/${id}/cancel`, { method: 'POST' }),
  remoteStatus: () => request<RemoteStatus>('/api/remote/status'),
  startPairing: () => request<PairingStart>('/api/remote/pairing/start', { method: 'POST' }),
  completePairing: (code: string, device_name: string) =>
    request<PairingComplete>('/api/remote/pairing/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, device_name }),
    }),
  devices: () => request<PairedDevice[]>('/api/remote/devices'),
  revokeDevice: (id: string) => request<{ revoked: boolean }>(`/api/remote/devices/${id}`, { method: 'DELETE' }),
  logoutRemote: () => request<{ logged_out: boolean }>('/api/remote/logout', { method: 'POST' }),
  diagnostics: () => request<Diagnostics>('/api/diagnostics'),
  ready: () => request<ReadyStatus>('/ready'),
}

export type RemoteStatus = {
  remote_access_enabled: boolean
  bind_host: string
  bind_port: number
  local_trust_loopback: boolean
  pairing_available: boolean
  message: string
}

export type PairingStart = {
  challenge_id: string
  code: string
  expires_at: string
  ttl_seconds: number
  message: string
}

export type PairingComplete = {
  session_id: string
  device_name: string
  token: string
  expires_at: string
  message: string
}

export type PairedDevice = {
  id: string
  device_name: string
  revoked: boolean
  expires_at: string
  last_seen_at: string | null
  created_at: string
}

export type Diagnostics = {
  status: string
  provider: string
  remote_access_enabled: boolean
  worker_enabled: boolean
  worker_running: boolean
  bind_host: string
  active_automation_runs: number
  waiting_approvals: number
  paired_devices: number
  browser_computers_ready: number
  recent_security_events: { id: string; event_type: string; detail: string; created_at: string }[]
  notes: string[]
}

export type ReadyStatus = {
  ready: boolean
  database: boolean
  worker_enabled: boolean
  worker_running: boolean
  remote_access_enabled: boolean
  bind_host: string
}

export type Team = {
  id: string
  name: string
  description: string
  lead_teammate_id: number
  member_ids: number[]
  enabled: boolean
  max_subtasks: number
  max_concurrent: number
  max_depth: number
  max_retries: number
  max_total_runs: number
  created_at: string
  updated_at: string
}

export type TeamCreate = {
  name: string
  description?: string
  lead_teammate_id: number
  member_ids: number[]
  enabled?: boolean
  max_concurrent?: number
  max_depth?: number
}

export type TeamSubtaskIn = {
  subtask_key: string
  assignee_teammate_id: number
  objective: string
  depends_on?: string[]
  critical?: boolean
  allowed_tool_categories?: string[]
}

export type TeamRunOut = {
  id: string
  team_id: string
  lead_teammate_id: number
  objective: string
  status: string
  cancelled: boolean
  plan: Record<string, unknown>
  report: Record<string, unknown>
  shared_artifact_dir: string
  error_message: string | null
  subtasks: {
    id: string
    subtask_key: string
    assignee_teammate_id: number
    objective: string
    depends_on: string[]
    status: string
    attempts: number
    result: Record<string, unknown>
    artifact_refs: string[]
    error_message: string | null
    conversation_id: number | null
  }[]
  events: { id: string; event_type: string; payload: Record<string, unknown>; created_at: string }[]
  created_at: string
  updated_at: string
  finished_at: string | null
}

export type MemoryRecord = {
  id: string
  memory_type: string
  scope: string
  scope_id: string
  content: string
  source: string
  status: string
  tags: string[]
  source_conversation_id: number | null
  source_run_id: string | null
  supersedes_id: string | null
  created_at: string
  updated_at: string
}

export type MemoryCreate = {
  content: string
  memory_type?: string
  scope?: string
  scope_id?: string
  source?: string
  tags?: string[]
}

export type SkillPackage = {
  skill_id: string
  version: string
  name: string
  description: string
  when_to_use: string
  required_inputs: string[]
  expected_outputs: string[]
  allowed_tool_categories: string[]
  required_permissions: string[]
  constraints: string[]
  evaluation_criteria: string[]
  instructions_md: string
  package_path: string
}

export type SkillInstall = {
  id: string
  skill_id: string
  version: string
  name: string
  enabled: boolean
  teammate_id: number | null
  package_path: string
  allowed_tool_categories: string[]
  reviewed: boolean
  created_at: string
  updated_at: string
}

export type ConnectorGrant = {
  id: string
  teammate_id: number
  connector_id: string
  operations: string[]
  resource_scope: Record<string, unknown>
  enabled: boolean
  created_at: string
  updated_at: string
}

export type WorkflowRun = {
  id: string
  teammate_id: number
  conversation_id: number | null
  title: string
  instruction: string
  skill_id: string | null
  skill_version: string | null
  status: string
  memory_ids: string[]
  report: Record<string, unknown>
  steps: {
    id: string
    step_key: string
    description: string
    depends_on: string[]
    status: string
    tool_name: string | null
    attempts: number
    output: Record<string, unknown>
    artifact_path: string | null
    error_message: string | null
    checkpoint: Record<string, unknown>
    started_at: string | null
    finished_at: string | null
  }[]
  created_at: string
  updated_at: string
  finished_at: string | null
  awaiting?: string
}

export type Automation = {
  id: string
  name: string
  instruction: string
  teammate_id: number
  enabled: boolean
  paused: boolean
  timezone: string
  schedule_kind: string
  schedule: Record<string, unknown>
  next_run_at: string | null
  missed_policy: string
  include_previous_summary: boolean
  autonomy_policy: string
  max_iterations: number
  max_tool_calls: number
  max_run_seconds: number
  run_on_startup: boolean
  created_at: string
  updated_at: string
}

export type AutomationCreate = {
  name: string
  instruction: string
  teammate_id: number
  schedule_kind?: string
  schedule?: Record<string, unknown>
  timezone?: string
  paused?: boolean
  enabled?: boolean
  include_previous_summary?: boolean
  run_on_startup?: boolean
}

export type AutomationRun = {
  id: string
  automation_id: string
  conversation_id: number | null
  agent_run_id: string | null
  status: string
  trigger_type: string
  occurrence_key: string | null
  instruction_snapshot: string
  progress: Record<string, unknown>
  checkpoint: Record<string, unknown>
  error_message: string | null
  stop_reason: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
}

export type AutomationRunReceipt = {
  run: AutomationRun
  events: ExecutionEvent[]
  assistant_text: string
  checkpoint: Record<string, unknown>
  progress: Record<string, unknown>
}

export type AppNotification = {
  id: string
  kind: string
  title: string
  body: string
  automation_run_id: string | null
  read: boolean
  created_at: string
}

export type WebhookCreated = {
  id: string
  automation_id: string
  name: string
  secret: string
  secret_prefix: string
  path: string
  message: string
}
