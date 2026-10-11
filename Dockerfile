FROM node:24-alpine AS console
WORKDIR /console
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock
COPY atlas/ ./atlas/
COPY apps/ ./apps/
COPY benchmarks/ ./benchmarks/
RUN pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home atlas \
    && mkdir -p /app/data /app/repositories && chown -R atlas:atlas /app
COPY --from=console /console/dist/ ./frontend/dist/
USER atlas
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3)"
CMD ["python", "-m", "atlas.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
