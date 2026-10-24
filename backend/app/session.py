from __future__ import annotations

# In-memory login sessions shared by main.py and feature routers.
# The existing HRMS application uses opaque session tokens rather than JWTs.
active_sessions: dict[str, str] = {}
