# Interview guide

Use these answers only for features that exist in this repository. Do not claim browser control, connectors, or multi-agent orchestration yet.

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
    Add formal DB migrations, worker isolation for long tools, durable cancellation across processes, idempotency keys for approvals, structured tracing, and metrics on tool success/latency.

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
