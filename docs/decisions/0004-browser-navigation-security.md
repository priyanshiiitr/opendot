# ADR 0004: Browser navigation and session security (Milestone 3)

## Status

Accepted and implemented for Milestone 3 (`app/browser/policy.py`, `app/browser/session.py`, fixture-mode settings, automated tests).

## Context

Milestone 3 adds Playwright browser tools to the existing agent loop. A model that can navigate the web can also be steered toward local services, private networks, credential endpoints, or everyday browser profiles unless the backend enforces destination and isolation policy.

## Decision

### 1. Destination policy (fail closed)

- Allow only `http://` and `https://` URLs.
- Reject `file://`, `javascript:`, `data:`, and any other unsupported scheme.
- Block localhost, loopback, private-network, link-local, and cloud metadata addresses by default.
- Validate DNS resolution and the resolved IP addresses before navigation; do not rely only on string checks of the URL host.
- Recheck destinations after redirects; redirects must not bypass the policy.
- Reduce DNS rebinding and TOCTOU gaps as far as practical (resolve → classify → navigate with pinned/checked destination; re-validate on redirect).
- Apply the same policy to navigation from links, clicks, and any other navigation-producing action.
- Do not allow arbitrary access to local development servers, internal services, or credential endpoints.
- Provide an explicit, **test-only** exception for controlled local fixture pages. Never enable that exception automatically in normal application mode.
- If a safe navigation decision cannot be made, fail closed with a structured tool error.

### 2. Session isolation

- Use a dedicated Playwright-managed browser **context**, not the user’s everyday Chrome profile.
- Do not import or expose existing cookies, passwords, session tokens, or saved credentials.
- Keep each session’s storage isolated; provide a clear way to close the session and clear temporary data.
- Never log cookies, authorization headers, passwords, or sensitive form contents.

**Terminology for docs and interviews:**
- A **browser process** is the Playwright-launched Chromium (or other) executable.
- A **browser context** is an isolated profile-like sandbox inside that process (separate cookies, localStorage, cache). Multiple contexts can share one process without sharing login state.

### 3. Untrusted website content

- Treat page text, HTML, screenshots, downloads, and extracted metadata as untrusted evidence, not as instructions that override the user or app policy.
- A webpage must never grant tool permissions, approve pending operations, alter the tool registry, or change security configuration.
- Enforce permissions in backend code regardless of on-page instructions.
- Require approval before credentials, form submits, messages, purchases, or other consequential account actions.
- Do not automatically follow website instructions to reveal secrets, access local files, or navigate to internal services.

### 4. Tests

Add automated tests for:
- Disallowed URL schemes
- Private-network / loopback / metadata destinations
- Redirect policy bypasses
- Website prompt-injection attempts (fixture pages that try to override policy)

Use controlled local fixtures and mocked network responses. Do not probe real internal networks during tests.

## Consequences

- Browser tools must go through a shared navigation gate before every navigation.
- Mock/fixture browsing uses an explicit test configuration flag, never the default app settings.
- Documentation and interview answers must distinguish process vs context isolation.
