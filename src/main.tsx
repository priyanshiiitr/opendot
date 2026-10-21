import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  api,
  AppNotification,
  Automation,
  AutomationRun,
  AutomationRunReceipt,
  ConnectorStatus,
  Conversation,
  ExecutionEvent,
  isSuccessfulTerminal,
  Message,
  StreamEvent,
  Teammate,
  toActivity,
} from './api'
import './styles.css'

type View = 'chat' | 'automations'

function Activity({
  events,
  decide,
}: {
  events: ExecutionEvent[]
  decide: (id: string, decision: 'approved' | 'rejected') => void
}) {
  return (
    <>
      {events.map((event) => {
        const approveLabel = event.tool_name?.startsWith('telegram_') ? 'Approve send' : 'Approve'
        return (
          <div className={`activity ${event.status}`} key={event.id}>
            <strong>{event.tool_name || event.event_type}</strong>
            <span>{event.status.replaceAll('_', ' ')}</span>
            {Object.keys(event.payload).length > 0 && <pre>{JSON.stringify(event.payload, null, 2)}</pre>}
            {event.event_type === 'approval' && event.status === 'waiting_approval' && (
              <div className="approval-actions">
                <button onClick={() => decide(event.id, 'approved')}>{approveLabel}</button>
                <button className="reject" onClick={() => decide(event.id, 'rejected')}>
                  Reject
                </button>
              </div>
            )}
          </div>
        )
      })}
    </>
  )
}

function ConnectionsPanel({
  status,
  onChanged,
}: {
  status: ConnectorStatus | null
  onChanged: (status: ConnectorStatus) => void
}) {
  const [token, setToken] = useState('')
  const [allowlist, setAllowlist] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  useEffect(() => {
    if (status?.chat_allowlist?.length) setAllowlist(status.chat_allowlist.join(', '))
  }, [status?.chat_allowlist])

  async function save() {
    setBusy(true)
    setNote('')
    try {
      const chats = allowlist
        .split(/[,\s]+/)
        .map((part) => part.trim())
        .filter(Boolean)
        .map((part) => Number(part))
        .filter((n) => Number.isFinite(n))
      const next = await api.configureTelegram(token, chats)
      setToken('')
      onChanged(next)
      setNote('Saved. Token is stored in the OS credential store and is not shown again.')
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'Could not save Telegram connection')
    } finally {
      setBusy(false)
    }
  }

  async function verify() {
    setBusy(true)
    setNote('')
    try {
      onChanged(await api.verifyTelegram())
      setNote('Verification finished (getMe only; no message sent).')
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'Verification failed')
    } finally {
      setBusy(false)
    }
  }

  async function disconnect() {
    setBusy(true)
    setNote('')
    try {
      onChanged(await api.disconnectTelegram())
      setNote('Disconnected. Stored bot token deleted.')
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'Disconnect failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="connections">
      <div className="side-label">CONNECTIONS</div>
      <div className="connection-card">
        <strong>Telegram Bot</strong>
        <small>
          {status?.connected
            ? `Connected as @${status.bot_username || 'bot'}`
            : status?.message || 'Not connected'}
        </small>
        <p className="connection-help">
          Create a bot with BotFather, copy the bot token here (never into chat), then message your bot and add that chat
          ID to the allowlist. Bot API cannot read your personal Telegram inbox.
        </p>
        {!status?.credentials_writable && (
          <p className="connection-warn">Secure credential storage is unavailable. Token saving is disabled.</p>
        )}
        <label>
          Bot token
          <input
            type="password"
            autoComplete="off"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder={status?.token_configured ? '•••• stored securely' : '123456:ABC…'}
            disabled={busy || !status?.credentials_writable}
          />
        </label>
        <label>
          Allowed chat IDs
          <input value={allowlist} onChange={(e) => setAllowlist(e.target.value)} placeholder="111222333" disabled={busy} />
        </label>
        <div className="connection-actions">
          <button disabled={busy || !token.trim() || !status?.credentials_writable} onClick={save}>
            Save & verify
          </button>
          <button disabled={busy || !status?.token_configured} onClick={verify}>
            Test getMe
          </button>
          <button className="reject" disabled={busy || !status?.token_configured} onClick={disconnect}>
            Disconnect
          </button>
        </div>
        {note && <p className="connection-note">{note}</p>}
      </div>
    </div>
  )
}

function AutomationsPanel({
  teammates,
  onError,
}: {
  teammates: Teammate[]
  onError: (message: string) => void
}) {
  const [items, setItems] = useState<Automation[]>([])
  const [selectedId, setSelectedId] = useState<string>()
  const [runs, setRuns] = useState<AutomationRun[]>([])
  const [receipt, setReceipt] = useState<AutomationRunReceipt | null>(null)
  const [name, setName] = useState('Morning research')
  const [instruction, setInstruction] = useState('add 2 and 3')
  const [scheduleKind, setScheduleKind] = useState('manual')
  const [time, setTime] = useState('09:00')
  const [busy, setBusy] = useState(false)
  const [webhookSecret, setWebhookSecret] = useState('')
  const [notes, setNotes] = useState('')

  const selected = items.find((a) => a.id === selectedId)

  async function refresh() {
    const list = await api.automations()
    setItems(list)
    if (selectedId) {
      setRuns(await api.automationRuns(selectedId))
    }
  }

  useEffect(() => {
    refresh().catch((e) => onError(e instanceof Error ? e.message : 'Failed to load automations'))
  }, [])

  useEffect(() => {
    if (!selectedId) return
    const timer = setInterval(() => {
      api.automationRuns(selectedId).then(setRuns).catch(() => undefined)
      if (receipt && ['queued', 'running', 'waiting_approval'].includes(receipt.run.status)) {
        api.automationRun(receipt.run.id).then(setReceipt).catch(() => undefined)
      }
    }, 2000)
    return () => clearInterval(timer)
  }, [selectedId, receipt?.run.id, receipt?.run.status])

  async function create() {
    if (!teammates[0]) return
    setBusy(true)
    setNotes('')
    try {
      const schedule =
        scheduleKind === 'daily' || scheduleKind === 'weekdays'
          ? { time, weekdays: scheduleKind === 'weekdays' ? [0, 1, 2, 3, 4] : undefined }
          : scheduleKind === 'interval'
            ? { interval_minutes: 60 }
            : {}
      const created = await api.createAutomation({
        name,
        instruction,
        teammate_id: teammates[0].id,
        schedule_kind: scheduleKind,
        schedule,
        timezone: 'Asia/Calcutta',
      })
      setSelectedId(created.id)
      await refresh()
      setNotes('Automation saved. Scheduling only runs while the backend is up.')
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Create failed')
    } finally {
      setBusy(false)
    }
  }

  async function select(id: string) {
    setSelectedId(id)
    setReceipt(null)
    setWebhookSecret('')
    setRuns(await api.automationRuns(id))
  }

  async function runNow() {
    if (!selectedId) return
    setBusy(true)
    try {
      const run = await api.runAutomation(selectedId)
      setNotes(`Queued run ${run.id}. You can leave this page — work continues in the backend.`)
      setRuns(await api.automationRuns(selectedId))
      setReceipt(await api.automationRun(run.id))
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Run failed')
    } finally {
      setBusy(false)
    }
  }

  async function togglePause() {
    if (!selected) return
    await api.updateAutomation(selected.id, { paused: !selected.paused })
    await refresh()
  }

  async function openRun(runId: string) {
    setReceipt(await api.automationRun(runId))
  }

  async function cancelRun(runId: string) {
    await api.cancelAutomationRun(runId)
    if (selectedId) setRuns(await api.automationRuns(selectedId))
    if (receipt?.run.id === runId) setReceipt(await api.automationRun(runId))
  }

  async function decide(approvalId: string, decision: 'approved' | 'rejected') {
    if (!receipt) return
    await api.decideAutomationApproval(receipt.run.id, approvalId, decision)
    setReceipt(await api.automationRun(receipt.run.id))
    if (selectedId) setRuns(await api.automationRuns(selectedId))
  }

  async function makeWebhook() {
    if (!selectedId) return
    const created = await api.createWebhook(selectedId)
    setWebhookSecret(`POST http://127.0.0.1:8000${created.path}\nBearer ${created.secret}\n(${created.message})`)
  }

  return (
    <section className="automations">
      <div className="auto-grid">
        <div className="auto-list">
          <div className="side-label">AUTOMATIONS</div>
          {items.map((a) => (
            <button
              key={a.id}
              className={`teammate ${selectedId === a.id ? 'active' : ''}`}
              onClick={() => select(a.id)}
            >
              <span className="avatar">{a.name[0]}</span>
              <span>
                <strong>{a.name}</strong>
                <small>
                  {a.paused ? 'paused' : a.schedule_kind}
                  {a.next_run_at ? ` · next ${new Date(a.next_run_at).toLocaleString()}` : ''}
                </small>
              </span>
            </button>
          ))}
          <div className="connection-card" style={{ marginTop: 16 }}>
            <strong>Create</strong>
            <label>
              Name
              <input value={name} onChange={(e) => setName(e.target.value)} disabled={busy} />
            </label>
            <label>
              Instruction
              <input value={instruction} onChange={(e) => setInstruction(e.target.value)} disabled={busy} />
            </label>
            <label>
              Schedule
              <select value={scheduleKind} onChange={(e) => setScheduleKind(e.target.value)} disabled={busy}>
                <option value="manual">Manual / run now</option>
                <option value="daily">Daily</option>
                <option value="weekdays">Weekdays</option>
                <option value="interval">Every 60 minutes</option>
                <option value="once">One-time (set via API schedule.run_at)</option>
              </select>
            </label>
            {(scheduleKind === 'daily' || scheduleKind === 'weekdays') && (
              <label>
                Local time (Asia/Calcutta)
                <input value={time} onChange={(e) => setTime(e.target.value)} disabled={busy} />
              </label>
            )}
            <div className="connection-actions">
              <button disabled={busy} onClick={create}>
                Save automation
              </button>
            </div>
          </div>
        </div>
        <div className="auto-detail">
          {selected ? (
            <>
              <h2>{selected.name}</h2>
              <p className="auto-instruction">{selected.instruction}</p>
              <p className="connection-help">
                Status: {selected.paused ? 'paused' : 'active'} · {selected.schedule_kind}
                {selected.next_run_at ? ` · next due ${new Date(selected.next_run_at).toLocaleString()}` : ''}
              </p>
              <div className="connection-actions">
                <button disabled={busy || selected.paused} onClick={runNow}>
                  Run now
                </button>
                <button onClick={togglePause}>{selected.paused ? 'Resume' : 'Pause'}</button>
                <button onClick={makeWebhook}>Create webhook</button>
              </div>
              {notes && <p className="connection-note">{notes}</p>}
              {webhookSecret && <pre className="auto-secret">{webhookSecret}</pre>}
              <div className="side-label" style={{ marginTop: 24 }}>
                RUN HISTORY
              </div>
              {runs.map((run) => (
                <button key={run.id} className={`run-row ${run.status}`} onClick={() => openRun(run.id)}>
                  <strong>{run.status.replaceAll('_', ' ')}</strong>
                  <small>
                    {run.trigger_type} · {new Date(run.created_at).toLocaleString()}
                  </small>
                  {['queued', 'running', 'waiting_approval'].includes(run.status) && (
                    <span
                      className="run-cancel"
                      onClick={(e) => {
                        e.stopPropagation()
                        cancelRun(run.id)
                      }}
                    >
                      Cancel
                    </span>
                  )}
                </button>
              ))}
              {receipt && (
                <div className="receipt">
                  <div className="side-label">RECEIPT</div>
                  <p>
                    <strong>{receipt.run.status.replaceAll('_', ' ')}</strong>
                    {receipt.run.stop_reason ? ` — ${receipt.run.stop_reason}` : ''}
                  </p>
                  {receipt.assistant_text && <div className="bubble">{receipt.assistant_text}</div>}
                  <Activity
                    events={receipt.events}
                    decide={(id, decision) => {
                      void decide(id, decision)
                    }}
                  />
                </div>
              )}
            </>
          ) : (
            <div className="empty">
              <h2>Automations</h2>
              <p>
                Create a durable task. Runs use the real agent runtime and continue even if you leave chat. The computer
                must stay on with the backend running.
              </p>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

function App() {
  const [view, setView] = useState<View>('chat')
  const [teammates, setTeammates] = useState<Teammate[]>([])
  const [selected, setSelected] = useState<Teammate>()
  const [conversation, setConversation] = useState<Conversation>()
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [runId, setRunId] = useState<string>()
  const [telegram, setTelegram] = useState<ConnectorStatus | null>(null)
  const [notifications, setNotifications] = useState<AppNotification[]>([])

  useEffect(() => {
    api
      .teammates()
      .then((t) => {
        setTeammates(t)
        setSelected(t[0])
      })
      .catch((e) => setError(e.message))
    api.telegramStatus().then(setTelegram).catch(() => setTelegram(null))
    let seenNotice = ''
    const poll = setInterval(() => {
      api
        .notifications(true)
        .then((rows) => {
          setNotifications(rows)
          const newest = rows[0]
          if (
            newest &&
            newest.id !== seenNotice &&
            typeof Notification !== 'undefined' &&
            Notification.permission === 'granted'
          ) {
            seenNotice = newest.id
            new Notification(newest.title, { body: newest.body || 'Automation update' })
          }
        })
        .catch(() => undefined)
    }, 4000)
    if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
      void Notification.requestPermission()
    }
    return () => clearInterval(poll)
  }, [])

  useEffect(() => {
    if (!selected) return
    api
      .conversations(selected.id)
      .then(async (cs) => setConversation(cs[0] ? await api.conversation(cs[0].id) : await api.createConversation(selected.id)))
      .catch((e) => setError(e.message))
  }, [selected])

  const refresh = async () => {
    if (conversation) setConversation(await api.conversation(conversation.id))
  }

  async function consume(stream: AsyncGenerator<StreamEvent>) {
    let answer = ''
    let terminal: StreamEvent | undefined
    for await (const event of stream) {
      if (event.type === 'run_started' && event.run_id) setRunId(event.run_id)
      const activity = toActivity(event)
      if (activity) {
        setConversation((current) =>
          current
            ? {
                ...current,
                execution_events: [...current.execution_events.filter((x) => x.id !== activity.id), activity],
              }
            : current,
        )
      }
      if (event.type === 'token') {
        answer += event.content || ''
        const content = answer
        setConversation((current) =>
          current
            ? {
                ...current,
                messages: [
                  ...current.messages.filter((m) => m.id !== -1),
                  { id: -1, role: 'assistant', content, created_at: new Date().toISOString() },
                ],
              }
            : current,
        )
      }
      if (event.type === 'error') throw new Error(event.message || 'Agent execution failed')
      if (isSuccessfulTerminal(event)) terminal = event
    }
    if (!terminal) throw new Error('The agent stream ended before a done or approval event.')
  }

  async function send() {
    if (!input.trim() || !conversation || sending) return
    const text = input.trim()
    setInput('')
    setSending(true)
    setError('')
    const optimistic: Message = {
      id: Date.now(),
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    }
    setConversation({ ...conversation, messages: [...conversation.messages, optimistic] })
    try {
      await consume(api.streamMessage(conversation.id, text))
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to send message')
    } finally {
      setSending(false)
      setRunId(undefined)
    }
  }

  async function decide(id: string, decision: 'approved' | 'rejected') {
    if (!conversation) return
    setSending(true)
    setError('')
    try {
      await consume(api.decideApproval(conversation.id, id, decision))
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Approval failed')
    } finally {
      setSending(false)
      setRunId(undefined)
    }
  }

  async function cancel() {
    if (runId) {
      await api.cancelRun(runId)
      setError('Task cancellation requested.')
    }
  }

  return (
    <div className="app">
      <aside>
        <div className="brand">
          <span className="mark">✦</span>
          <div>
            <strong>AI Teammates</strong>
            <small>local workspace</small>
          </div>
        </div>
        <div className="side-label">TEAMMATES</div>
        {teammates.map((t) => (
          <button className={`teammate ${selected?.id === t.id ? 'active' : ''}`} onClick={() => setSelected(t)} key={t.id}>
            <span className="avatar">{t.name[0]}</span>
            <span>
              <strong>{t.name}</strong>
              <small>{t.description}</small>
            </span>
          </button>
        ))}
        <div className="side-label" style={{ marginTop: 20 }}>
          WORKSPACE
        </div>
        <button className={`teammate ${view === 'chat' ? 'active' : ''}`} onClick={() => setView('chat')}>
          <span className="avatar">C</span>
          <span>
            <strong>Chat</strong>
            <small>interactive teammate</small>
          </span>
        </button>
        <button className={`teammate ${view === 'automations' ? 'active' : ''}`} onClick={() => setView('automations')}>
          <span className="avatar">A</span>
          <span>
            <strong>Automations</strong>
            <small>
              durable tasks
              {notifications.length ? ` · ${notifications.length} alert${notifications.length > 1 ? 's' : ''}` : ''}
            </small>
          </span>
        </button>
        <ConnectionsPanel status={telegram} onChanged={setTelegram} />
        {notifications.length > 0 && (
          <div className="connections">
            <div className="side-label">ALERTS</div>
            {notifications.slice(0, 5).map((n) => (
              <button
                key={n.id}
                className="teammate"
                onClick={() => {
                  void api.readNotification(n.id).then(() =>
                    api.notifications(true).then(setNotifications).catch(() => undefined),
                  )
                  setView('automations')
                }}
              >
                <span>
                  <strong>{n.title}</strong>
                  <small>{n.kind.replaceAll('_', ' ')}</small>
                </span>
              </button>
            ))}
          </div>
        )}
      </aside>
      <main>
        {view === 'chat' ? (
          <>
            <header>
              <div>
                <p className="eyebrow">PERSONAL AI WORKSPACE</p>
                <h1>{selected?.name || 'Loading teammate…'}</h1>
              </div>
              <span className="status">
                <i /> local agent
              </span>
            </header>
            <section className="messages">
              {conversation?.messages.length ? (
                conversation.messages.map((m) => (
                  <div className={`message-row ${m.role}`} key={m.id}>
                    <div className="bubble">{m.content}</div>
                  </div>
                ))
              ) : (
                <div className="empty">
                  <div className="empty-icon">✦</div>
                  <h2>What should we work on?</h2>
                  <p>Try “add 12 and 30”, “what time is it?”, or “request approval”.</p>
                </div>
              )}
              {conversation && <Activity events={conversation.execution_events} decide={decide} />}
              {sending && (
                <div className="typing">
                  Atlas is working<span> ···</span>
                  {runId && <button onClick={cancel}>Cancel</button>}
                </div>
              )}
            </section>
            {error && <div className="error">{error}</div>}
            <form
              className="composer"
              onSubmit={(e) => {
                e.preventDefault()
                send()
              }}
            >
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Message your teammate…"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    send()
                  }
                }}
              />
              <button disabled={sending || !input.trim()}>
                Send <span>↗</span>
              </button>
            </form>
          </>
        ) : (
          <>
            <header>
              <div>
                <p className="eyebrow">DURABLE TASK RUNNER</p>
                <h1>Automations</h1>
              </div>
              <span className="status">
                <i /> background worker
              </span>
            </header>
            {error && <div className="error">{error}</div>}
            <AutomationsPanel teammates={teammates} onError={setError} />
          </>
        )}
      </main>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
