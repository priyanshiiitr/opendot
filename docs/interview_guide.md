# Interview guide

Use these answers only for features that exist in this repository. Do not claim cloud hosting, always-on execution while the PC is off, or multi-agent delegation.

1. **How does an LLM tool loop work?**
   The provider emits a structured call; the backend validates, authorizes, executes, persists the result, and gives that actual result back to the provider for final text.

2. **Why is structured calling different from ordinary text?**
   Text has no authority. Only typed `tool_calls` fields can reach the backend registry. Parsing natural language into code would be unsafe and brittle.

3. **What prevents arbitrary Python execution?**
   A fixed registry maps only known names to handlers. There is no `eval`, `exec`, shell, dynamic import, or model-controlled registration.

4. **How are arguments validated?**
   JSON is size-limited, parsed to an object, and validated by Pydantic before an executor receives it. Invalid arguments never reach the handler.

5. **How do you prevent infinite tool loops?**
   The runtime caps iterations, tool calls per turn, argument/result size, and execution time. Exceeding a cap becomes a structured error event.

6. **Why return a tool result to the model?**
   The final answer must be grounded in real execution. Otherwise the model can invent a number that never came from `calculate`.

7. **How does streaming handle partial tool JSON?**
   The xAI adapter accumulates fragments by call index and parses only after the stream completes. Incomplete or malformed arguments raise `ProviderError` and never execute.

8. **Why is a permission broker stronger than a prompt?**
   It is backend enforcement that can pause or deny execution regardless of model text. The model cannot claim “the user approved this.”

9. **What happens on timeout or cancellation?**
   A timeout becomes a structured tool failure. Cancellation marks the `AgentRun`; later tool starts and stale approvals are blocked.

10. **How is execution tested without paid API calls?**
    The deterministic mock provider requests real tools and consumes their stored results. xAI SSE parsing uses fake HTTP streams.

11. **How would you evaluate an agent system?**
    Assert tool selection, argument validity, permission outcomes, result grounding in the final answer, limit behavior, and persistence after restart. Separate offline mock tests from rare live provider smoke tests.

12. **How would production reliability improve from here?**
    Adopt full Alembic for multi-host deploys, isolate long browser jobs, add structured tracing/metrics, and optionally move the worker to a separate process while keeping the same run schema.

13. **How would computer-use tools be made safe later?**
    Separate least-privilege adapters, observation-first mode, strict allowlists, explicit approval, screenshot/audit trails, and never let the model escalate its own permissions.

14. **Why SSE instead of WebSockets for this chat UI?**
    One-way server push fits token/activity streaming, works through ordinary HTTP, and is simple to parse. Bidirectional sockets can wait until we need client-pushed mid-stream control beyond REST cancel/approve endpoints.

15. **What is the difference between a tool call and a tool result message?**
    A tool call is the model’s structured request (`id`, name, arguments). A tool result is the backend’s authoritative outcome associated with that same `tool_call_id`, which subsequent provider turns must include.

16. **Why Playwright browser automation before full computer use?**
    Browsers expose structured pages, fixtures, and network policy. You can block private IPs and test with local HTML. OS mouse/keyboard control is broader, harder to sandbox, and harder to evaluate safely.

17. **What is the difference between a browser process and a browser context?**
    The process is the Chromium executable. A context is an isolated profile-like sandbox (cookies, storage) inside that process. We create a fresh context so the agent never inherits the user’s everyday logins.

18. **How do you stop an agent from reaching localhost or cloud metadata?**
    Allow only http/https, resolve DNS, classify every resolved IP with `ipaddress`, block loopback/private/link-local/metadata, and re-validate the final URL after redirects. Fail closed when unsure.

19. **How do you handle webpage prompt injection?**
    Treat page text as untrusted evidence. Permissions and approvals stay in backend code the page cannot call. The mock final answer explicitly refuses instructions to self-approve or open internal URLs; tests cover that fixture.

20. **Why is a test-only fixture exception required?**
    Automated tests need local HTML without hitting the public internet, but normal app mode must not browse arbitrary local/private services. The exception is host+port scoped and disabled by default.

21. **How do connectors relate to the agent loop?**
    Connectors verify credentials and register tools into the existing ToolRegistry. The model never sees tokens. Authorization still goes through PermissionBroker and approvals.

22. **Why Bot API instead of a personal Telegram session?**
    Official bots are supported, auditable, and do not require scraping or stealing user sessions. They cannot read an unrestricted personal inbox—document that limit honestly.

23. **Where should API tokens live?**
    OS secure storage (Windows Credential Manager via keyring). Not SQLite chat history, not frontend state after save, not logs, not git.

24. **How do you stop Telegram prompt injection?**
    Treat message text as untrusted evidence, filter by chat allowlist, and keep permission decisions in backend code the message cannot call.

25. **How do durable background agent jobs work here?**
    An `AutomationRun` row is queued in SQLite. A FastAPI lifespan worker claims it with a conditional update, opens a dedicated conversation, and drives the same `AgentRuntime` used by chat. Progress and checkpoints are committed; the UI can disconnect.

26. **How do you prevent duplicate scheduled runs?**
    Each due occurrence has a unique `occurrence_key` (`automation_id` + due timestamp). Scheduler ticks and restarts that try to enqueue the same key hit a uniqueness constraint and no-op.

27. **What happens if the process crashes mid-Telegram-send?**
    Checkpoints mark consequential in-flight tools as uncertain. On boot the run becomes `needs_reconciliation` so we do not blindly resend. Safe interrupted work is marked `interrupted` for explicit retry.

28. **How do approvals survive restart?**
    `waiting_approval` is persisted with the exact argument payload. Reconcile leaves those rows waiting; only an explicit user decision resumes via `resolve_approval`.

29. **Why not Celery/Redis for a desktop agent?**
    Single-user local SQLite already gives durability and a claim primitive. Extra brokers add ops cost without matching the threat/scale model.

30. **How are webhooks kept safe?**
    Dedicated `/hooks/{id}` route, bearer secret stored as a hash, body size and rate limits, event-id dedupe, loopback bind, enqueue-only — never expose the general agent API to the public internet.

31. **How do you isolate one teammate’s browser from another?**
    Each teammate has a unique computer id and dedicated Playwright user-data directory. Tools resolve the session from a server-side ContextVar bound to the authorized teammate, not from model text.

32. **Why a ComputerProvider interface if you only have Playwright?**
    So a future Linux VM or cloud desktop can plug in without rewriting AgentRuntime. Today only `local_browser` is implemented and tested — we do not claim VM semantics.

33. **How does persistent browser state work?**
    `launch_persistent_context` writes cookies/local storage into that computer’s profile folder. An exclusive lock file prevents concurrent launches. Everyday Chrome is never imported.

34. **How do you secure workspace file tools?**
    Canonicalize paths, require the result to stay under the computer’s workspace/artifacts root, reject `..` and symlinks, limit size/types, and require approval for overwrites.

35. **How does human takeover prevent races?**
    Backend sets `human_control` / `waiting_human`, sets `agent_blocked_reason` on the browser session, and blocks mutation tools. Release refreshes URL and clears the block. UI buttons are not the security boundary.

36. **What happens to takeover state after a crash?**
    Reconcile clears `human_control` and marks the computer `stopped`. The user must re-establish control — we never silently restore a stale human lock.

37. **How is the live preview implemented?**
    Playwright PNG capture served on a local authenticated app route with refresh interval limits. Preview stops when the panel is hidden. Screenshots are not pushed to a cloud CDN.

38. **Can the model pick another teammate’s computer id?**
    No. Computer resolution ignores tool arguments for ownership; it uses the runtime-bound teammate id from the conversation/task.

39. **How do approvals interact with computers?**
    PermissionBroker still gates consequential tools. After human control, page assumptions are invalid; consequential actions need fresh validation/approval if parameters or targets changed.

40. **What are the remaining isolation limits?**
    Same host OS, shared machine resources, and Playwright’s process model — not a hypervisor. Stronger isolation needs a future VM/cloud provider behind the same interface.

41. **Conversation history vs persistent memory?**
    History is the turn-by-turn transcript for one conversation. Persistent memory is a separate SQLite store of scoped preferences/facts with status and provenance. We do not dump all past chats into the system prompt.

42. **How does structured memory retrieval work?**
    At task start, `MemoryService.retrieve_for_task` loads active user + teammate (+ optional project) rows, scores by explicit preference boost and keyword overlap with the query, then caps the count (`memory_max_retrieve`).

43. **How do provenance and corrections work?**
    Each memory has scope, source (`explicit`/`inferred`), optional conversation/run ids, and status. An edit supersedes the old row; the new row points at `supersedes_id` so audits remain honest.

44. **How do you manage context size?**
    Hard retrieve limits, short prompt formatting (id + scope + content), and separate skill instructions loaded only when a skill is selected — not every memory and every skill every turn.

45. **How are reusable skills designed?**
    Local folders with `SKILL.md` frontmatter: id, version, inputs/outputs, allowed tool categories, evaluation criteria, and Markdown steps. They are workflows/instructions, not specialist personas.

46. **How do skill versioning and evaluation work?**
    Installs record `skill_id` + `version`. Workflow runs pin that version. Success checks expected workspace artifacts (e.g. report file + Sources section), not fluent model text alone.

47. **Tool permissions vs prompt instructions?**
    Skill/memory text can only advise. `PermissionBroker` assesses each tool with `AuthContext` (skill categories + connector grants). The model cannot expand its own allow-list.

48. **What is account-scoped authorization here?**
    `ConnectorGrant` binds teammate_id → connector_id → allowed operations (+ optional resource_scope). When a grant exists, missing ops are denied. The model cannot switch accounts via tool arguments.

49. **How do multi-step workflows plan and execute?**
    Backend validates a step graph (`step_key`, deps, status). `plan_from_skill` builds a skeleton; `advance_deterministic` runs safe steps; execute completes only when artifacts are ready; verify checks deliverables.

50. **Idempotency and retry safety?**
    Safe read-ish tools may retry with attempt caps. Consequential/uncertain failures mark `needs_reconciliation` instead of blind replay that might double-send.

51. **Checkpoints and crash recovery?**
    Completed steps store checkpoint JSON (`safe_to_resume`). Resume picks the next pending eligible step and skips completed work. Status lives in SQLite, not only in RAM.

52. **Why deterministic verification?**
    Models can invent completion. We check files under the authorized workspace against skill expected outputs before marking the workflow completed.

53. **How does the schema evolve without wiping data?**
    Additive `create_all` plus versioned SQL in `migrations.py` (`2026_10_m7_…`). Existing conversations/tasks/computers remain; new tables get indexes for scope and grants.

54. **How do you handle prompt injection via memory or skills?**
    Memory/skill text is labeled untrusted context in the prompt. Permissions and grants are code paths. Secret-like memory content is rejected at write time. Skills cannot approve tools or change credentials.

55. **How do you test autonomous workflows offline?**
    Pytest creates workflows, advances steps, writes fixture artifacts via `WorkspaceStore`, and asserts verification/failure without calling paid providers or real Telegram.
