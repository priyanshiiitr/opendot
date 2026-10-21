"""Isolate automated tests from the developer's local SQLite database and API keys."""

import os

# Must run before any app.* import so Settings and the SQLAlchemy engine bind to the test DB.
os.environ["AI_TEAMMATES_DATABASE_URL"] = "sqlite:///./test_ai_teammates.db"
os.environ["AI_TEAMMATES_MODEL_PROVIDER"] = "mock"
os.environ["AI_TEAMMATES_AUTOMATION_WORKER_ENABLED"] = "false"
os.environ.pop("AI_TEAMMATES_XAI_API_KEY", None)
os.environ.pop("AI_TEAMMATES_GROQ_API_KEY", None)
