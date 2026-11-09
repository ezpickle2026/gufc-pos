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

# Port, workers and threads are in gunicorn.conf.py (it reads Railway's PORT).
# The database location comes from DB_PATH (e.g. /data/gufc.db on a volume).
EXPOSE 8080
CMD ["gunicorn", "app:app"]
