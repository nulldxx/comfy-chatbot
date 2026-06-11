# Gunicorn configuration for ComfyUI chatbot
# SSE (Server-Sent Events) requires long-lived connections; gthread worker
# class handles concurrent streams without needing gevent/eventlet.

bind = "0.0.0.0:5000"
backlog = 2048

workers = 1
worker_class = "gthread"
threads = 8
timeout = 120          # Allow up to 2 min for slow image generation handoff
keepalive = 5

max_requests = 1000
max_requests_jitter = 100

accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

proc_name = "comfy-chatbot"

limit_request_line = 0
limit_request_fields = 100
limit_request_field_size = 8190

preload_app = True
