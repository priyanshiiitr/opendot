# Roadmap

- [x] Milestone 0: workspace inspection and architecture plan
- [x] Milestone 1: runnable desktop chat (backend tests, mock streaming, SQLite persistence, Electron launch, restart persistence, and unavailable-backend UI handling verified)
- [x] Milestone 2: bounded agent loop, validated local tools, approvals, execution events, cancellation endpoint, persisted traces, frontend terminal-event handling, and automated coverage without paid API calls
- [ ] Milestone 3: controlled Playwright browser automation
- [ ] Milestone 4: one connector with OAuth and audit records
- [ ] Milestone 5: configurable teammates and bounded delegation
- [ ] Milestone 6: controlled desktop operations and resumable work

## Milestone 2 acceptance (verified in automation)

1. Ordinary mock chat still streams and persists.
2. Mock provider emits structured `calculate` / time / approval tool calls.
3. Registry validates and executes registered tools only.
4. Actual tool results are returned to the provider for the final answer.
5. Iteration and tool-call limits stop runaway loops.
6. Approval is required for `demo_request_approval`; reject / stale / duplicate decisions cannot authorize unintended execution.
7. Frontend parses execution and approval SSE events; Approve / Reject controls are wired.
8. xAI streaming fragment assembly is unit-tested with mocked HTTP/SSE.
9. No paid model calls are made by the test suite.
