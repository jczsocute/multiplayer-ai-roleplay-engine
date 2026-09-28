"""Small helpers shared by the authenticated-user tests."""

from server.platform.models import AuthenticatedUser


def user(user_id: int, username: str) -> AuthenticatedUser:
    """Stand-in for a cookie-authenticated account inside a test."""
    return AuthenticatedUser(id=user_id, username=username)
