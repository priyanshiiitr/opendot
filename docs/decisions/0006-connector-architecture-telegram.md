# ADR 0006: Connector architecture and Telegram Bot API

## Status

Accepted for Milestone 4.

## Decision

External apps integrate through a **Connector** abstraction:

1. `ConnectorManager` — lifecycle and tool registration refresh  
2. `CredentialStore` — OS keyring (Windows WinVault); in-memory for tests; plaintext disabled  
3. Adapter — fixed official API base URL (`https://api.telegram.org`)  
4. Tools — registered into the existing `ToolRegistry` only while connected  
5. `PermissionBroker` — unchanged; sends require approval  

Telegram uses the **official Bot API** with a BotFather token. This is not a personal Telegram user login and cannot scrape an unrestricted personal inbox.

## Capabilities (Bot API)

Supported in this milestone: `getMe`, bounded `getUpdates`, allowlisted `sendMessage` with approval.

Not supported: user-account sessions, reading arbitrary personal chats, inventing API base URLs, bulk messaging, OAuth for other products.

## Consequences

Tokens never appear in SQLite, SSE payloads, tool results, or connection status JSON. Chat allowlists gate which updates become agent evidence. Incoming Telegram text is untrusted.
