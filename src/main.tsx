import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  api,
  AppNotification,
  Automation,
  AutomationRun,
  AutomationRunReceipt,
  ComputerStatus,
  ConnectorGrant,
  ConnectorStatus,
  Conversation,
  ExecutionEvent,
  isSuccessfulTerminal,
  MemoryRecord,
  Message,
  SkillInstall,
  SkillPackage,
  StreamEvent,
  Teammate,
  toActivity,
  WorkflowRun,
} from './api'
import './styles.css'

type View = 'chat' | 'automations' | 'computer' | 'memory' | 'skills' | 'workflows'

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

function ComputerPanel({
  teammate,
  onError,
}: {
  teammate?: Teammate
  onError: (message: string) => void
}) {
  const [status, setStatus] = useState<ComputerStatus | null>(null)
  const [preview, setPreview] = useState('')
  const [files, setFiles] = useState<{ path: string; bytes: number }[]>([])
  const [filePreview, setFilePreview] = useState('')
  const [busy, setBusy] = useState(false)
  const [panelVisible, setPanelVisible] = useState(true)

  async function refresh() {
    if (!teammate) return
    const next = await api.computer(teammate.id)
    setStatus(next)
    const listing = await api.workspaceFiles(teammate.id, 'workspace')
    setFiles(listing.files)
  }

  useEffect(() => {
    refresh().catch((e) => onError(e instanceof Error ? e.message : 'Computer status failed'))
  }, [teammate?.id])

  useEffect(() => {
    if (!teammate || !panelVisible || !status?.browser_open) return
    let cancelled = false
    const tick = async () => {
      try {
        const url = api.computerPreviewUrl(teammate.id)
        if (!cancelled) setPreview(url)
      } catch {
        /* preview optional */
      }
    }
    void tick()
    const id = setInterval(() => void tick(), 2000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [teammate?.id, panelVisible, status?.browser_open, status?.state])

  useEffect(() => {
    const onVis = () => setPanelVisible(document.visibilityState === 'visible')
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [])

  async function act(fn: () => Promise<ComputerStatus>) {
    if (!teammate) return
    setBusy(true)
    try {
      setStatus(await fn())
      await refresh()
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Computer action failed')
    } finally {
      setBusy(false)
    }
  }

  if (!teammate) return <div className="empty"><h2>Select a teammate</h2></div>

  const human = status?.human_control || status?.state === 'human_control'

  return (
    <section className="computer-panel">
      <div className="computer-grid">
        <div>
          <p className="connection-help">
            State: <strong>{(status?.state || '…').replaceAll('_', ' ')}</strong>
            {human ? ' · HUMAN IN CONTROL' : ''}
            {status?.last_url ? ` · ${status.last_url}` : ''}
          </p>
          <p className="connection-help">{status?.isolation_note}</p>
          <div className="connection-actions">
            <button disabled={busy} onClick={() => act(() => api.startComputer(teammate.id, false))}>
              Start
            </button>
            <button disabled={busy} onClick={() => act(() => api.startComputer(teammate.id, true))}>
              Start headed
            </button>
            <button disabled={busy || human} onClick={() => act(() => api.pauseComputer(teammate.id))}>
              Pause
            </button>
            <button disabled={busy || human} onClick={() => act(() => api.resumeComputer(teammate.id))}>
              Resume
            </button>
            <button disabled={busy || human} onClick={() => act(() => api.takeoverComputer(teammate.id))}>
              Take over
            </button>
            <button disabled={busy || !human} onClick={() => act(() => api.releaseComputer(teammate.id))}>
              Return control
            </button>
            <button disabled={busy} onClick={() => act(() => api.stopComputer(teammate.id))}>
              Close browser
            </button>
            <button
              className="reject"
              disabled={busy}
              onClick={() => {
                if (window.confirm('Delete this teammate’s browser profile/cookies? Workspace files are kept.')) {
                  void act(() => api.clearComputerBrowser(teammate.id))
                }
              }}
            >
              Clear browser data
            </button>
          </div>
          {status?.last_error && <p className="connection-warn">{status.last_error}</p>}
          <div className="preview-frame">
            {preview && status?.browser_open ? (
              <img src={preview} alt="Teammate browser preview" />
            ) : (
              <div className="preview-empty">Preview appears when the browser is open.</div>
            )}
          </div>
        </div>
        <div>
          <div className="side-label">WORKSPACE FILES</div>
          {files.length === 0 && <p className="connection-help">No files yet. Ask the teammate to save a report.</p>}
          {files.map((f) => (
            <button
              key={f.path}
              className="run-row"
              onClick={() => {
                void api
                  .workspaceFile(teammate.id, f.path)
                  .then((r) => setFilePreview(r.content))
                  .catch((e) => onError(e instanceof Error ? e.message : 'Read failed'))
              }}
            >
              <strong>{f.path}</strong>
              <small>{f.bytes} bytes</small>
            </button>
          ))}
          {filePreview && <pre className="auto-secret">{filePreview.slice(0, 4000)}</pre>}
        </div>
      </div>
    </section>
  )
}

function MemoryPanel({
  teammate,
  onError,
}: {
  teammate: Teammate | null
  onError: (msg: string) => void
}) {
  const [rows, setRows] = useState<MemoryRecord[]>([])
  const [content, setContent] = useState('')
  const [scope, setScope] = useState('user')
  const [filter, setFilter] = useState('')
  const [busy, setBusy] = useState(false)

  async function refresh() {
    const data = await api.memories(scope ? { scope } : undefined)
    setRows(data)
  }

  useEffect(() => {
    refresh().catch((e) => onError(e instanceof Error ? e.message : 'Failed to load memories'))
  }, [scope])

  async function save() {
    if (!content.trim()) return
    setBusy(true)
    try {
      await api.createMemory({
        content: content.trim(),
        memory_type: scope === 'teammate' ? 'teammate' : 'preference',
        scope,
        scope_id: scope === 'teammate' && teammate ? String(teammate.id) : 'default',
        source: 'explicit',
      })
      setContent('')
      await refresh()
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Could not save memory')
    } finally {
      setBusy(false)
    }
  }

  const visible = rows.filter(
    (r) => !filter || r.content.toLowerCase().includes(filter.toLowerCase()) || r.id.includes(filter),
  )

  return (
    <section className="m7-panel">
      <p className="auto-instruction">
        Persistent memories are scoped facts and preferences — not full chat history. Deleted memories stay deleted.
        Secrets are rejected. Inferred items are labeled separately from explicit user preferences.
      </p>
      <div className="m7-grid">
        <div className="auto-list">
          <label>
            Scope filter
            <select value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="user">user</option>
              <option value="teammate">teammate</option>
              <option value="project">project</option>
              <option value="task">task</option>
            </select>
          </label>
          <label>
            Search
            <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter content…" />
          </label>
          <label>
            New memory
            <textarea value={content} onChange={(e) => setContent(e.target.value)} rows={4} placeholder="Prefer concise reports…" />
          </label>
          <div className="connection-actions">
            <button disabled={busy || !content.trim()} onClick={() => void save()}>
              Save preference
            </button>
            {teammate && (
              <button
                className="reject"
                disabled={busy}
                onClick={() =>
                  void api
                    .clearTeammateMemories(teammate.id)
                    .then(refresh)
                    .catch((e) => onError(e instanceof Error ? e.message : 'Clear failed'))
                }
              >
                Clear teammate memory
              </button>
            )}
          </div>
        </div>
        <div>
          {visible.map((m) => (
            <div className={`run-row ${m.status}`} key={m.id}>
              <strong>
                {m.memory_type} · {m.scope}:{m.scope_id}
              </strong>
              <small>
                {m.source} · {m.id.slice(0, 8)}
                {m.supersedes_id ? ` · corrects ${m.supersedes_id.slice(0, 8)}` : ''}
              </small>
              <p className="auto-instruction" style={{ width: '100%', margin: '6px 0 0' }}>
                {m.content}
              </p>
              <button
                className="run-cancel"
                onClick={() =>
                  void api
                    .deleteMemory(m.id)
                    .then(refresh)
                    .catch((e) => onError(e instanceof Error ? e.message : 'Delete failed'))
                }
              >
                Delete
              </button>
            </div>
          ))}
          {!visible.length && <p className="auto-instruction">No active memories in this scope.</p>}
        </div>
      </div>
    </section>
  )
}

function SkillsPanel({
  teammate,
  onError,
}: {
  teammate: Teammate | null
  onError: (msg: string) => void
}) {
  const [catalog, setCatalog] = useState<SkillPackage[]>([])
  const [installs, setInstalls] = useState<SkillInstall[]>([])
  const [selected, setSelected] = useState<SkillPackage | null>(null)
  const [grants, setGrants] = useState<ConnectorGrant[]>([])

  async function refresh() {
    const [c, i, g] = await Promise.all([
      api.skillsCatalog(),
      api.skillInstalls(teammate?.id),
      api.connectorGrants(teammate?.id),
    ])
    setCatalog(c)
    setInstalls(i)
    setGrants(g)
  }

  useEffect(() => {
    refresh().catch((e) => onError(e instanceof Error ? e.message : 'Failed to load skills'))
  }, [teammate?.id])

  return (
    <section className="m7-panel">
      <p className="auto-instruction">
        Skills are versioned instruction packages. They never grant tool permissions — the PermissionBroker stays
        authoritative. Review before enable. Connector grants below control which teammate may use Telegram ops.
      </p>
      <div className="m7-grid">
        <div className="auto-list">
          <h3>Catalogue</h3>
          {catalog.map((s) => (
            <button key={s.skill_id} className="run-row" onClick={() => setSelected(s)}>
              <strong>{s.name}</strong>
              <small>
                {s.skill_id}@{s.version}
              </small>
            </button>
          ))}
          {selected && teammate && (
            <div className="connection-actions" style={{ marginTop: 12 }}>
              <button
                onClick={() =>
                  void api
                    .installSkill({
                      skill_id: selected.skill_id,
                      teammate_id: teammate.id,
                      reviewed: true,
                      enabled: true,
                    })
                    .then(refresh)
                    .catch((e) => onError(e instanceof Error ? e.message : 'Install failed'))
                }
              >
                Review & enable for {teammate.name}
              </button>
            </div>
          )}
          <h3 style={{ marginTop: 24 }}>Connector grants</h3>
          {teammate && (
            <button
              onClick={() =>
                void api
                  .upsertConnectorGrant({
                    teammate_id: teammate.id,
                    connector_id: 'telegram',
                    operations: ['telegram_get_bot_info', 'telegram_get_recent_updates'],
                    enabled: true,
                  })
                  .then(refresh)
                  .catch((e) => onError(e instanceof Error ? e.message : 'Grant failed'))
              }
            >
              Grant Telegram read ops
            </button>
          )}
          {grants.map((g) => (
            <div className={`run-row ${g.enabled ? '' : 'failed'}`} key={g.id}>
              <strong>
                {g.connector_id} → teammate {g.teammate_id}
              </strong>
              <small>{g.operations.join(', ') || '(none)'}</small>
              {g.enabled && (
                <button
                  className="run-cancel"
                  onClick={() =>
                    void api
                      .revokeConnectorGrant(g.id)
                      .then(refresh)
                      .catch((e) => onError(e instanceof Error ? e.message : 'Revoke failed'))
                  }
                >
                  Revoke
                </button>
              )}
            </div>
          ))}
        </div>
        <div>
          <h3>Installs</h3>
          {installs.map((i) => (
            <div className={`run-row ${i.enabled ? 'waiting_approval' : ''}`} key={i.id}>
              <strong>
                {i.name} {i.enabled ? '· enabled' : '· disabled'}
              </strong>
              <small>
                {i.skill_id}@{i.version} · reviewed={String(i.reviewed)}
              </small>
              <button
                onClick={() =>
                  void api
                    .enableSkill(i.id, !i.enabled)
                    .then(refresh)
                    .catch((e) => onError(e instanceof Error ? e.message : 'Toggle failed'))
                }
              >
                {i.enabled ? 'Disable' : 'Enable'}
              </button>
            </div>
          ))}
          {selected && (
            <div className="receipt">
              <h3>
                {selected.name} · v{selected.version}
              </h3>
              <p className="auto-instruction">{selected.description}</p>
              <pre className="auto-secret">{selected.instructions_md.slice(0, 4000)}</pre>
              <small>Categories: {selected.allowed_tool_categories.join(', ')}</small>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

function WorkflowsPanel({
  teammate,
  onError,
}: {
  teammate: Teammate | null
  onError: (msg: string) => void
}) {
  const [rows, setRows] = useState<WorkflowRun[]>([])
  const [selected, setSelected] = useState<WorkflowRun | null>(null)
  const [instruction, setInstruction] = useState('Research AI startups and save a shortlist report.')
  const [skillId, setSkillId] = useState('startup-research')
  const [busy, setBusy] = useState(false)

  async function refresh() {
    if (!teammate) return
    const data = await api.workflows(teammate.id)
    setRows(data)
  }

  useEffect(() => {
    refresh().catch((e) => onError(e instanceof Error ? e.message : 'Failed to load workflows'))
  }, [teammate?.id])

  async function create() {
    if (!teammate) return
    setBusy(true)
    try {
      const wf = await api.createWorkflow({
        teammate_id: teammate.id,
        title: 'Skill workflow',
        instruction,
        skill_id: skillId || undefined,
      })
      setSelected(wf)
      await refresh()
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Create workflow failed')
    } finally {
      setBusy(false)
    }
  }

  async function advance(completeExecute = false) {
    if (!selected) return
    setBusy(true)
    try {
      const next = await api.advanceWorkflow(selected.id, completeExecute)
      setSelected(next)
      await refresh()
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Advance failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="m7-panel">
      <p className="auto-instruction">
        Multi-step workflows persist step status and checkpoints. Safe steps resume without redoing completed work.
        Final success requires verified artifacts, not fluent model text.
      </p>
      <div className="m7-grid">
        <div className="auto-list">
          <label>
            Skill id
            <input value={skillId} onChange={(e) => setSkillId(e.target.value)} />
          </label>
          <label>
            Instruction
            <textarea value={instruction} onChange={(e) => setInstruction(e.target.value)} rows={4} />
          </label>
          <button disabled={busy || !teammate} onClick={() => void create()}>
            Create workflow
          </button>
          <h3 style={{ marginTop: 20 }}>Runs</h3>
          {rows.map((w) => (
            <button key={w.id} className={`run-row ${w.status}`} onClick={() => setSelected(w)}>
              <strong>{w.title}</strong>
              <small>
                {w.status} · {w.skill_id || 'no skill'}@{w.skill_version || '—'}
              </small>
            </button>
          ))}
        </div>
        <div>
          {selected ? (
            <div className="receipt">
              <h2>{selected.title}</h2>
              <p className="auto-instruction">{selected.instruction}</p>
              <small>
                Status: {selected.status}
                {selected.awaiting ? ` · awaiting ${selected.awaiting}` : ''}
              </small>
              <div className="connection-actions">
                <button disabled={busy} onClick={() => void advance(false)}>
                  Advance next step
                </button>
                <button disabled={busy} onClick={() => void advance(true)}>
                  Complete execute step
                </button>
              </div>
              {selected.steps.map((s) => (
                <div className={`run-row ${s.status}`} key={s.id}>
                  <strong>
                    {s.step_key} · {s.status}
                  </strong>
                  <small>
                    attempts {s.attempts}
                    {s.error_message ? ` · ${s.error_message}` : ''}
                  </small>
                  <p className="auto-instruction" style={{ width: '100%', margin: 0 }}>
                    {s.description}
                  </p>
                </div>
              ))}
              {Object.keys(selected.report || {}).length > 0 && (
                <pre className="auto-secret">{JSON.stringify(selected.report, null, 2)}</pre>
              )}
              {selected.memory_ids?.length > 0 && (
                <small>Memories used: {selected.memory_ids.map((id) => id.slice(0, 8)).join(', ')}</small>
              )}
            </div>
          ) : (
            <p className="auto-instruction">Select or create a workflow to inspect steps.</p>
          )}
        </div>
      </div>
    </section>
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
        <button className={`teammate ${view === 'computer' ? 'active' : ''}`} onClick={() => setView('computer')}>
          <span className="avatar">P</span>
          <span>
            <strong>Computer</strong>
            <small>preview · takeover</small>
          </span>
        </button>
        <button className={`teammate ${view === 'memory' ? 'active' : ''}`} onClick={() => setView('memory')}>
          <span className="avatar">M</span>
          <span>
            <strong>Memory</strong>
            <small>prefs · scopes</small>
          </span>
        </button>
        <button className={`teammate ${view === 'skills' ? 'active' : ''}`} onClick={() => setView('skills')}>
          <span className="avatar">S</span>
          <span>
            <strong>Skills</strong>
            <small>versioned packs</small>
          </span>
        </button>
        <button className={`teammate ${view === 'workflows' ? 'active' : ''}`} onClick={() => setView('workflows')}>
          <span className="avatar">W</span>
          <span>
            <strong>Workflows</strong>
            <small>steps · verify</small>
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
        ) : view === 'automations' ? (
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
        ) : view === 'memory' ? (
          <>
            <header>
              <div>
                <p className="eyebrow">PERSISTENT MEMORY</p>
                <h1>Memories</h1>
              </div>
              <span className="status">
                <i /> scoped · inspectable
              </span>
            </header>
            {error && <div className="error">{error}</div>}
            <MemoryPanel teammate={selected ?? null} onError={setError} />
          </>
        ) : view === 'skills' ? (
          <>
            <header>
              <div>
                <p className="eyebrow">REUSABLE SKILLS</p>
                <h1>Skills & grants</h1>
              </div>
              <span className="status">
                <i /> reviewed before enable
              </span>
            </header>
            {error && <div className="error">{error}</div>}
            <SkillsPanel teammate={selected ?? null} onError={setError} />
          </>
        ) : view === 'workflows' ? (
          <>
            <header>
              <div>
                <p className="eyebrow">MULTI-STEP WORKFLOWS</p>
                <h1>Workflows</h1>
              </div>
              <span className="status">
                <i /> checkpoints · verify
              </span>
            </header>
            {error && <div className="error">{error}</div>}
            <WorkflowsPanel teammate={selected ?? null} onError={setError} />
          </>
        ) : (
          <>
            <header>
              <div>
                <p className="eyebrow">ISOLATED COMPUTER</p>
                <h1>{selected?.name || 'Teammate'} Computer</h1>
              </div>
              <span className="status">
                <i /> local browser profile
              </span>
            </header>
            {error && <div className="error">{error}</div>}
            <ComputerPanel teammate={selected} onError={setError} />
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
