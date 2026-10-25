"""Safe, local-only 503 injection for the Issue #3 incident exercise.

Never installed on Render: all three explicit local-demo environment guards are required.
The injected response is produced *inside* OpenTelemetry's HTTP middleware, so it
creates real request metrics, a structured log, and an error-status server span.
"""
import os
from starlette.responses import JSONResponse


def install_local_drill(app):
    if not (
        os.getenv("TASKLANE_INCIDENT_DRILL_ENABLED") == "local-only"
        and os.getenv("TASKLANE_OTEL_ENABLED", "").lower() == "true"
        and os.getenv("TASKLANE_OTEL_ENDPOINT") == "http://otel-collector:4318"
        and os.getenv("DATABASE_URL") == "postgresql://postgres:postgres@postgres:5432/tasklane"
    ):
        return False

    @app.middleware("http")
    async def inject_local_503(request, call_next):
        if (
            request.method == "POST"
            and request.url.path == "/api/tasks"
            and request.headers.get("x-tasklane-drill") == "inject-503"
        ):
            # Do not parse the request body or write anything to PostgreSQL.
            return JSONResponse(
                status_code=503,
                content={"detail": "Controlled local service-unavailable drill"},
            )
        return await call_next(request)

    return True
