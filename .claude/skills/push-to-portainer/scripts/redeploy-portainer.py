#!/usr/bin/env python3
"""Pull the latest comfy-chatbot image and redeploy its Portainer stack.

The stack (`comfy-chatbot`, id 19 on moria) is a plain compose stack running
`nerwander/comfy-chatbot:latest`. Portainer's stack-update endpoint with
`pullImage: true` re-pulls the image tag and recreates the container — i.e. a
"pull latest + restart" in one call.

Config comes from the environment (nothing secret is baked into the file):

    PORTAINER_URL        default https://moria:9443
    PORTAINER_STACK      default comfy-chatbot           (stack name to redeploy)
    PORTAINER_INSECURE   default 1  (moria uses a self-signed cert; set 0 to verify)

    # authenticate with EITHER an API token (preferred) ...
    PORTAINER_API_KEY    a Portainer access token (My account -> Access tokens)
    # ... OR username/password:
    PORTAINER_USERNAME   default admin
    PORTAINER_PASSWORD

Usage:
    PORTAINER_PASSWORD=... ./scripts/redeploy-portainer.py
    PORTAINER_API_KEY=ptr_... ./scripts/redeploy-portainer.py --stack comfy-chatbot
"""
import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request


def _request(url, ctx, method="GET", token=None, api_key=False, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-API-Key" if api_key else "Authorization",
                       token if api_key else f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, context=ctx) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        sys.exit(f"HTTP {e.code} on {method} {url}\n{detail}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach {url}: {e.reason}")


def main():
    ap = argparse.ArgumentParser(description="Redeploy a Portainer compose stack, pulling latest images.")
    ap.add_argument("--url", default=os.environ.get("PORTAINER_URL", "https://moria:9443"))
    ap.add_argument("--stack", default=os.environ.get("PORTAINER_STACK", "comfy-chatbot"),
                    help="stack name to redeploy")
    ap.add_argument("--prune", action="store_true", help="remove services no longer in the compose file")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    insecure = os.environ.get("PORTAINER_INSECURE", "1") != "0"
    ctx = ssl._create_unverified_context() if insecure else ssl.create_default_context()

    api_key = os.environ.get("PORTAINER_API_KEY")
    if api_key:
        token, use_api_key = api_key, True
    else:
        username = os.environ.get("PORTAINER_USERNAME", "admin")
        password = os.environ.get("PORTAINER_PASSWORD")
        if not password:
            sys.exit("Set PORTAINER_API_KEY, or PORTAINER_PASSWORD (and optionally PORTAINER_USERNAME).")
        print(f"Authenticating to {base} as {username} ...")
        auth = _request(f"{base}/api/auth", ctx, method="POST",
                        body={"username": username, "password": password})
        token, use_api_key = auth["jwt"], False

    def call(path, method="GET", body=None):
        return _request(f"{base}{path}", ctx, method=method,
                        token=token, api_key=use_api_key, body=body)

    # Find the stack by name.
    stacks = call("/api/stacks")
    stack = next((s for s in stacks if s.get("Name") == args.stack), None)
    if not stack:
        names = ", ".join(sorted(s.get("Name", "?") for s in stacks))
        sys.exit(f"Stack {args.stack!r} not found. Available: {names}")
    sid, eid = stack["Id"], stack["EndpointId"]
    print(f"Found stack {args.stack!r}: id={sid}, endpoint={eid}")

    # Fetch the current compose content; keep the existing env unchanged.
    content = call(f"/api/stacks/{sid}/file")["StackFileContent"]
    env = stack.get("Env") or []

    # Redeploy with pullImage=true -> re-pull the image tag(s) and recreate.
    print("Pulling latest image(s) and redeploying (this can take a minute) ...")
    call(f"/api/stacks/{sid}?endpointId={eid}", method="PUT", body={
        "stackFileContent": content,
        "env": env,
        "prune": args.prune,
        "pullImage": True,
    })
    print(f"✓ Stack {args.stack!r} redeployed with the latest image.")


if __name__ == "__main__":
    main()
