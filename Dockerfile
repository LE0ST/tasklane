# ==============================================================================
# Stage 1: Build the React frontend SPA
# ==============================================================================
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend

# Install dependencies using clean install for reproducible builds
COPY frontend/package*.json ./
RUN npm ci

# Copy frontend source and compile production assets
COPY frontend/ ./
RUN npm run build

# ==============================================================================
# Stage 2: Production Python runtime with FastAPI
# ==============================================================================
FROM python:3.11-slim AS runtime

# Prevent Python from writing bytecode and enable unbuffered streaming logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install uv from official image for fast, reproducible dependency installation
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /bin/uv

# Copy dependency specifications and install production dependencies only
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy backend application source code, Alembic configuration, and migrations
COPY backend/database.py backend/main.py backend/models.py backend/schemas.py ./
COPY backend/alembic.ini ./
COPY backend/alembic/ ./alembic/

# Copy compiled frontend distribution into static directory for FastAPI
COPY --from=frontend-builder /app/frontend/dist ./static

# Expose default application port
EXPOSE 8000

# Default environment configuration
ENV HOST=0.0.0.0 \
    PORT=8000

# Start Uvicorn ASGI server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
