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

# Worker recycling is disabled: a server-side sequence run (/api/sequence-run)
# drives a long generation loop in a daemon thread on the single worker, and
# recycling the worker mid-run would kill it (only images already written to the
# session file would survive). This is a single-user appliance, so the leak
# mitigation max_requests normally provides isn't worth the risk of aborting runs.
max_requests = 0
max_requests_jitter = 0

accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

proc_name = "comfy-chatbot"

limit_request_line = 0
limit_request_fields = 100
limit_request_field_size = 8190

preload_app = True
