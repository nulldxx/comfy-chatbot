# Portainer API reference (moria)

Only needed when the redeploy script fails, the API token is rejected, or the
deployed image needs verifying. Normal deploys just run the bundled script.

## Connection & auth

- Base URL: `https://moria:9443` (self-signed cert — use `curl -k` / the script's
  `PORTAINER_INSECURE=1` default).
- Preferred auth: header `X-API-Key: <PORTAINER_API_KEY>` (from `~/dot-files/.bash_shared`).
- Fallback auth: `POST /api/auth` `{"username","password"}` → `{"jwt"}`, then
  `Authorization: Bearer <jwt>` (JWTs expire ~8h; tokens don't).

## The comfy-chatbot stack

- Name `comfy-chatbot`, **stack id 19**, **endpoint id 3**, type **compose**
  (not git-backed; env is inline in the compose file).
- Image: `nerwander/comfy-chatbot:latest`, container `comfy-chatbot`.
- The redeploy script finds the stack **by name**, so the id can drift without breaking it.

## Key endpoints

```bash
# List stacks (find id/endpoint by name)
curl -sk https://moria:9443/api/stacks -H "X-API-Key: $PORTAINER_API_KEY"

# Get a stack's compose content
curl -sk https://moria:9443/api/stacks/19/file -H "X-API-Key: $PORTAINER_API_KEY"

# Redeploy pulling latest images (what the script does)
curl -sk -X PUT "https://moria:9443/api/stacks/19?endpointId=3" \
  -H "X-API-Key: $PORTAINER_API_KEY" -H 'Content-Type: application/json' \
  -d '{"stackFileContent":"<compose>","env":[],"prune":false,"pullImage":true}'
```

## Verify the deployed image

Use the Docker proxy on endpoint 3 to check the running container's image digest:

```bash
curl -sk "https://moria:9443/api/endpoints/3/docker/containers/comfy-chatbot/json" \
  -H "X-API-Key: $PORTAINER_API_KEY" \
 | python3 -c 'import sys,json;d=json.load(sys.stdin);print("Image:",d["Config"]["Image"]);print("ImageID:",d["Image"])'
```

Compare `ImageID` before/after the redeploy, or against the freshly built image's digest
on Docker Hub, to confirm the pull took effect.

## Mint / rotate an API token (if the key is missing or rejected)

Admin user id is `1`. Create a non-expiring access token (needs the admin password):

```bash
JWT=$(curl -sk https://moria:9443/api/auth -X POST -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<ADMIN_PASSWORD>"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["jwt"])')

curl -sk https://moria:9443/api/users/1/tokens -X POST -H "Authorization: Bearer $JWT" \
  -H 'Content-Type: application/json' \
  -d '{"password":"<ADMIN_PASSWORD>","description":"push-to-portainer skill"}'
# -> {"rawAPIKey":"<token, shown once>","apiKey":{...}}
```

Store the `rawAPIKey` as `PORTAINER_API_KEY` in `~/dot-files/.bash_shared` (that repo is
private; commit the change there). Existing tokens: `GET /api/users/1/tokens`;
revoke: `DELETE /api/users/1/tokens/<id>`.
