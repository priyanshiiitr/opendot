"""Optional auth providers (Clerk). Local pairing remains the default."""

from .clerk import ClerkAuth, ClerkAuthError

__all__ = ["ClerkAuth", "ClerkAuthError"]
