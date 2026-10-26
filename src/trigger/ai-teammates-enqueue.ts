import { task } from "@trigger.dev/sdk";

/**
 * Receives a JobEnvelope from the FastAPI control plane (trigger_http backend).
 * Optionally forwards to AI_TEAMMATES_CONTROL_PLANE_CALLBACK_URL so Python runs the work.
 * Secrets stay on the control plane — this task only carries the job envelope.
 */
export const aiTeammatesEnqueue = task({
  id: "ai-teammates-enqueue",
  run: async (payload: {
    job_type: string;
    run_id: string;
    teammate_id?: number | null;
    payload?: Record<string, unknown>;
    idempotency_key?: string | null;
  }) => {
    console.log("ai-teammates-enqueue", {
      job_type: payload.job_type,
      run_id: payload.run_id,
      teammate_id: payload.teammate_id ?? null,
      idempotency_key: payload.idempotency_key ?? null,
    });

    const callback = (process.env.AI_TEAMMATES_CONTROL_PLANE_CALLBACK_URL || "").trim();
    const secret = (process.env.AI_TEAMMATES_TRIGGER_WEBHOOK_SECRET || "").trim();
    let callbackStatus: string | null = null;

    if (callback) {
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
        "User-Agent": "ai-teammates-trigger-task/1.0",
      };
      if (secret) {
        headers["X-AI-Teammates-Signature"] = secret;
      }
      const res = await fetch(callback, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });
      callbackStatus = `${res.status}`;
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`Control plane callback failed (${res.status}): ${text.slice(0, 500)}`);
      }
    }

    return {
      ok: true,
      run_id: payload.run_id,
      job_type: payload.job_type,
      callback: callback ? callbackStatus : "skipped_no_callback_url",
      timestamp: new Date().toISOString(),
    };
  },
});
