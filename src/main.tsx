import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  api,
  Conversation,
  ExecutionEvent,
  isSuccessfulTerminal,
  Message,
  StreamEvent,
  Teammate,
  toActivity,
} from './api'
import './styles.css'

function Activity({
  events,
  decide,
}: {
  events: ExecutionEvent[]
  decide: (id: string, decision: 'approved' | 'rejected') => void
}) {
  return (
    <>
      {events.map((event) => (
        <div className={`activity ${event.status}`} key={event.id}>
          <strong>{event.tool_name || event.event_type}</strong>
          <span>{event.status.replaceAll('_', ' ')}</span>
          {Object.keys(event.payload).length > 0 && <pre>{JSON.stringify(event.payload, null, 2)}</pre>}
          {event.event_type === 'approval' && event.status === 'waiting_approval' && (
            <div className="approval-actions">
              <button onClick={() => decide(event.id, 'approved')}>Approve demo</button>
              <button className="reject" onClick={() => decide(event.id, 'rejected')}>
                Reject
              </button>
            </div>
          )}
        </div>
      ))}
    </>
  )
}

function App() {
  const [teammates, setTeammates] = useState<Teammate[]>([])
  const [selected, setSelected] = useState<Teammate>()
  const [conversation, setConversation] = useState<Conversation>()
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [runId, setRunId] = useState<string>()

  useEffect(() => {
    api
      .teammates()
      .then((t) => {
        setTeammates(t)
        setSelected(t[0])
      })
      .catch((e) => setError(e.message))
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
      </aside>
      <main>
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
      </main>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
