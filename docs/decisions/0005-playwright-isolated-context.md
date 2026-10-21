# ADR 0005: Playwright process vs isolated browser context

## Status

Accepted for Milestone 3.

## Decision

Browser automation launches a **Playwright-managed Chromium process** and creates a fresh **browser context** with no `storage_state` and no user-data-dir tied to the developer’s everyday Chrome profile.

- **Browser process:** the Chromium executable Playwright starts.
- **Browser context:** an isolated cookie/localStorage/cache sandbox inside that process.

We do not import saved passwords, cookies, or session tokens from the user’s normal browser.

## Alternatives rejected

1. **Attach to installed Chrome with the default profile** — would expose real logins.
2. **Selenium with a shared user profile** — harder isolation story for interviews and higher credential-leak risk.

## Consequences

`browser_close` disposes page, context, and browser process cleanup. Artifacts (screenshots) are written under a local directory and are not secrets logs.
