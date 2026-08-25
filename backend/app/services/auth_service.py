"""Auth service for testing distinct Sentry issue."""

class AuthService:
    def authenticate(self, token: str):
        if token != "valid-token":
            raise PermissionError(f"Invalid token for auth_service: {token!r}")
        return {"user": "ok"}
