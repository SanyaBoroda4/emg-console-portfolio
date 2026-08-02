FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first so this layer is cached across code changes.
# pyproject.toml declares packages = [], so this installs only the
# dependencies — app code comes from the bind mount (dev) / COPY below.
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
