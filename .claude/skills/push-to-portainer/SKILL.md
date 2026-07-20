---
name: push-to-portainer
description: End-to-end release-and-deploy pipeline for the comfy-chatbot project — commit and push to main, cut a new release with scripts/make-release, watch the GitHub Actions release build (diagnosing and fixing any failures, then re-releasing), and once the build is green redeploy the comfy-chatbot stack on the local Portainer server (moria) so it pulls the new image. Use when the user says "push to portainer", "release and deploy", "ship it / ship this", "deploy to portainer", "push and redeploy", "cut a release and deploy", or otherwise wants the full commit → release → build → deploy flow for comfy-chatbot. Run from the comfy-chatbot repo root.
---

# Push to Portainer

Full release-and-deploy pipeline for **comfy-chatbot**. Runs the four stages below
in order; **do not deploy until the release build is green.**

## Preconditions

- Current working directory is the **comfy-chatbot repo root** (has `scripts/make-release`).
- `gh` CLI is authenticated (build watching) and git can push to `origin`.
- Portainer secrets live in `~/dot-files/.bash_shared`:
  `PORTAINER_URL` and `PORTAINER_API_KEY` (already configured). The deploy step
  sources this file. If the key is missing or rejected, see
  [references/portainer-api.md](references/portainer-api.md) to mint a new token.

## Stage 1 — Commit & push to main

comfy-chatbot **deploys from `main`**, and `make-release` tags the current `HEAD`,
so everything must be on `main` and pushed **before** releasing.

1. `git status` — if there are relevant uncommitted changes, stage and commit them
   following the repo's commit conventions (see the repo `CLAUDE.md` for the required
   `Co-Authored-By` / `Claude-Session` trailers). If the work is already committed, skip.
2. If uncommitted changes look unrelated or unexpected, **stop and ask the user** before
   committing — a deploy restarts the live service.
3. `git push origin main` and confirm it succeeded.

## Stage 2 — Cut a release

Run `./scripts/make-release`. It fetches tags, increments the patch version, tags
`HEAD`, and pushes the tag. **Capture the new tag** (`vX.Y.Z`) from its output — the
tag push is what triggers the release build.

## Stage 3 — Watch the build, fix failures

The tag push triggers the **"Build, Test and Push Docker Image on Release"** workflow
(builds the image, runs container tests, pushes `nerwander/comfy-chatbot:latest` to
Docker Hub). A separate **"Build Check"** runs on the `main` push.

1. Find the release run: `gh run list --limit 5` and match the new tag / commit title.
2. Watch it: `gh run watch <run-id> --exit-status` (exit status reflects pass/fail).
3. **If it fails:** inspect with `gh run view <run-id> --log-failed`, diagnose the root
   cause, fix it in the repo, then **fix forward** — commit, `git push origin main`, and
   run `./scripts/make-release` again to cut a fresh patch tag. Re-watch. Repeat until green.
   (Release tags are immutable; a new patch is cleaner than mutating a tag. Only re-run a
   job in place for clearly transient infra failures.)
4. Proceed **only** when the release run's conclusion is `success` — this guarantees the
   new image was pushed to Docker Hub and is ready to pull.

## Stage 4 — Redeploy on Portainer

The bundled `scripts/redeploy-portainer.py` calls Portainer's stack-update endpoint
with `pullImage: true`, which re-pulls `nerwander/comfy-chatbot:latest` and recreates
the container (defaults target `https://moria:9443`, stack `comfy-chatbot`).

Run it with the secrets sourced (use this skill's bundled copy so it works from any repo):

```bash
source ~/dot-files/.bash_shared && python3 <skill-dir>/scripts/redeploy-portainer.py
```

`<skill-dir>` is this skill's directory. Success prints `✓ Stack 'comfy-chatbot'
redeployed with the latest image.`

**Verify** the running container picked up the new image (optional but recommended) —
see the "Verify the deployed image" section of
[references/portainer-api.md](references/portainer-api.md). If the container still shows
the old digest, wait a few seconds (registry propagation) and re-run the redeploy.

## Report back

Summarise: the new release tag, that the build passed, and that Portainer redeployed —
noting the running image/digest if verified.
