# Gunicorn loads this file automatically. The port is read here, in Python,
# so the start command never contains "$PORT" -- Railway runs the start
# command without a shell, which would pass "$PORT" to gunicorn literally.
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
workers = 2
threads = 4
