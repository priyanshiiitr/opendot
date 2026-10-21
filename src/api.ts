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
}
