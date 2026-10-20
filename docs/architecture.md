# Architecture

```text
User -> React/Electron -> FastAPI -> AgentRuntime -> ModelProvider
                                              |          |-> MockProvider
                                              |          |-> XAIProvider
                                              |-> ToolRegistry -> local deterministic tools
                                              |-> PermissionBroker -> approval state
                                              |-> SQLite (messages, runs, execution_events)
```

```text
User -> Frontend -> FastAPI -> Model provider -> Tool registry -> Permission broker
  ^                                                               |
  |<- SSE token/activity/approval events <- Tool result <- tool execution
```

Ordinary assistant text never executes. The backend validates structured tool calls, applies permission policy, runs the known tool within a timeout, persists its result, and provides the result to the model for final text. `AgentRun` and `ExecutionEvent` are durable execution records. The renderer cannot access credentials, register tools, or self-approve. The backend binds to loopback and exposes no shell, filesystem, browser, or computer-control tool.
