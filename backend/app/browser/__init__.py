"""Isolated Playwright browser automation with fail-closed navigation policy."""

from .policy import NavigationDenied, NavigationPolicy
from .session import BrowserService

__all__ = ["BrowserService", "NavigationDenied", "NavigationPolicy"]
