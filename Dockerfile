# Stage 1: build the Phase 10 dashboard's static assets. Kept entirely
# separate from the runtime image -- the final stage never gets Node.js
# installed, just the already-built frontend/dist output.
FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir .

# Guarded at app-construction time in main.py's _add_dashboard() -- if this
# directory is absent (e.g. a build that skipped the frontend stage),
# main.py simply doesn't mount /dashboard rather than failing to start.
COPY --from=frontend-build /frontend/dist ./frontend/dist

EXPOSE 8000
EXPOSE 8443
EXPOSE 9090

CMD ["python", "-m", "dynamokv.run"]
