# Railway builds from this Dockerfile instead of Railpack, so a deploy no
# longer depends on downloading build tools (railpack / mise) from GitHub.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Railway sets PORT; 8080 is the fallback for running the image locally.
# The database location comes from DB_PATH (e.g. /data/gufc.db on a volume).
ENV PORT=8080
EXPOSE 8080
CMD exec gunicorn app:app --bind 0.0.0.0:${PORT} --workers 2 --threads 4
