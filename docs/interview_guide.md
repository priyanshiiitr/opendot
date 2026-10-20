# Interview guide

1. **How does an LLM tool loop work?** The provider emits a structured call; the backend validates, authorizes, executes, persists the result, and gives that actual result to the provider for final text.
2. **Why is structured calling different from ordinary text?** Text has no authority. Only typed `tool_calls` fields can reach the backend registry.
3. **What prevents arbitrary Python execution?** A fixed registry maps only three known names to code. There is no `eval`, `exec`, shell, dynamic import, or model-controlled registration.
4. **How are arguments validated?** JSON is size-limited, parsed to an object, and validated by Pydantic before an executor receives it.
5. **How do you prevent infinite tool loops?** The runtime caps iterations, tool calls, argument/result size, and execution time.
6. **Why return a result to the model?** It lets the final response be grounded in real execution rather than a claimed result.
7. **How does streaming handle partial tool JSON?** The xAI adapter accumulates fragments by call index and parses only after completion.
8. **Why is a permission broker stronger than a prompt?** It is backend enforcement that can pause or deny execution regardless of model text.
9. **What happens on timeout or cancellation?** A timeout becomes a structured tool failure; cancellation marks the run and blocks future calls.
10. **How is execution tested without paid API calls?** The deterministic mock provider requests real tools and consumes their stored results; xAI SSE parsing uses fake HTTP streams.
11. **How would production reliability improve?** Add database migrations, worker isolation, durable cancellation, idempotency, tracing, and metrics.
12. **How would computer-use tools be made safe?** Separate least-privilege adapters, observation-first mode, strict allowlists, explicit approval, and audit trails.
