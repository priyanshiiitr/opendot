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
  const response = await fetch(API + path, init)
  if (!response.ok) throw new Error((await response.text()) || `Request failed (${response.status})`)
  return response.json()
}

async function* stream(path: string, init: RequestInit): AsyncGenerator<StreamEvent> {
  const response = await fetch(API + path, init)
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

export const api = {
  teammates: () => request<Teammate[]>('/api/teammates'),
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
