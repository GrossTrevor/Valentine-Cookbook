# Only needed if your host wants a container (Fly.io, or Railway's Docker mode).
# Render and Railway can both build this project without it.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/var/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /var/data
VOLUME ["/var/data"]

EXPOSE 8080
CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --workers 2 --threads 4 --timeout 60"]
