# ADR 0001: Local-first Electron and FastAPI foundation

We chose Electron + React for the desktop shell and FastAPI + SQLite for the local service. This preserves the requested Python-first business logic while allowing a conventional desktop UI. A browser-only app would make local permissions and packaging harder; a heavier desktop framework would increase the learning and maintenance surface.

The model is behind a small async streaming interface. Mock mode is the default so tests and demonstrations do not spend money. xAI is opt-in through environment configuration.
