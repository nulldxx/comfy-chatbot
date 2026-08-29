"""ComfyUI WebSocket progress listener.

ComfyUI publishes step-level progress *only* on its WebSocket feed
(``ws://<host>/ws?clientId=…``) — ``GET /history/<prompt_id>``, which
``ComfyServer.poll_status`` polls, stays empty until the prompt finishes. This
module opens that feed for one prompt and reduces the message stream to a single
small snapshot dict that the generation thread reads every couple of seconds.

The correlation key already existed: ``ComfyServer`` generates a ``client_id``
and sends it with every ``POST /prompt``, and ComfyUI routes that prompt's
progress messages to whichever socket connected with the same ``clientId``.

This is telemetry, never a dependency. Every failure path — no server, a proxy
that refuses the upgrade, a ComfyUI too old to send a message we understand —
ends with ``latest()`` returning ``None``, which the UI renders as today's
indeterminate marquee.
"""

import json
import threading

try:                                                  # pragma: no cover - trivial
    import websocket                                  # websocket-client
except ImportError:                                   # pragma: no cover
    websocket = None


# How long a socket read blocks before we re-check the stop flag. Also bounds how
# long stop() waits for the reader thread to notice it.
RECV_TIMEOUT = 1.0

# Handshake timeout. Short: a ComfyUI that won't upgrade should cost the job
# nothing, and the poll loop is already running by the time we'd give up.
CONNECT_TIMEOUT = 5.0


class ProgressListener:
    """Reads one ComfyUI /ws feed and maintains a normalised progress snapshot.

    Usage is start-before-submit: ComfyUI buffers nothing, so a socket opened
    after the ``POST /prompt`` misses the opening messages. ``bind(prompt_id)``
    supplies the filter once the submit returns; until then every message on our
    own ``clientId`` socket is ours anyway.
    """

    def __init__(self, server, client_id, node_titles=None, total_nodes=0):
        self.server = server
        self.client_id = client_id
        self.node_titles = node_titles or {}
        self.total_nodes = max(int(total_nodes or 0), 0)

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._ws = None
        self._dead = False
        self._prompt_id = None

        self._reset()

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        """Connect and begin reading, on a daemon thread. Never raises."""
        if websocket is None or not self.total_nodes:
            self._dead = True
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="comfy-progress")
        self._thread.start()

    def bind(self, prompt_id):
        """Filter to one prompt, once submit_workflow() has returned its id."""
        with self._lock:
            self._prompt_id = prompt_id

    def stop(self):
        """Ask the reader to finish. Best-effort; never raises."""
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=RECV_TIMEOUT * 2)

    def latest(self):
        """Return a copy of the current snapshot, or None if there's nothing to show.

        None means "no data" — the caller leaves the bar indeterminate rather
        than drawing a bar at 0%.
        """
        with self._lock:
            if self._dead or self._percent is None:
                # A queue depth alone is still worth showing while we wait.
                if not self._dead and self._queue:
                    return {"queue": self._queue}
                return None
            snap = {"percent": round(self._percent, 1),
                    # Clamped: at 100% every node is in _done, and "node 3/2"
                    # would be nonsense.
                    "node_index": min(len(self._done) + 1, self.total_nodes),
                    "node_total": self.total_nodes}
            if self._phase:
                snap["phase"] = self._phase
            if self._steps:
                snap["step"] = self._step
                snap["steps"] = self._steps
            if self._queue:
                snap["queue"] = self._queue
            return snap

    # -- socket loop -------------------------------------------------------

    def _run(self):
        url = f"ws://{self.server}/ws?clientId={self.client_id}"
        try:
            self._ws = websocket.create_connection(url, timeout=CONNECT_TIMEOUT)
            self._ws.settimeout(RECV_TIMEOUT)
        except Exception as e:
            print(f"[comfy-progress] connect to {url} failed: {e}")
            with self._lock:
                self._dead = True
            return

        try:
            while not self._stop.is_set():
                try:
                    raw = self._ws.recv()
                except Exception as e:
                    # A read timeout is the normal idle case and carries no
                    # payload; anything else ends the feed.
                    if isinstance(e, getattr(websocket, "WebSocketTimeoutException", ())):
                        continue
                    if not self._stop.is_set():
                        print(f"[comfy-progress] read ended: {e}")
                    break
                # ComfyUI sends binary frames for latent previews; ignore them.
                if not isinstance(raw, str) or not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    self._handle(msg)
        finally:
            with self._lock:
                self._dead = True
            try:
                self._ws.close()
            except Exception:
                pass

    # -- message handling --------------------------------------------------

    def _reset(self):
        """Clear per-execution counters. Caller holds the lock (or is __init__)."""
        # Distinguishes "0% and running" from "nothing heard yet". Without it a
        # message we understood but that carried no state — or a malformed one —
        # would publish a phantom 0%, flipping the client's bar to determinate
        # and empty when we in fact know nothing.
        self._started = False
        self._done = set()
        self._current = None
        self._current_frac = 0.0
        self._percent = None
        self._phase = None
        self._step = 0
        self._steps = 0
        self._queue = 0

    def _title(self, node_id):
        return self.node_titles.get(str(node_id)) or f"Node {node_id}"

    def _handle(self, msg):
        """Fold one decoded message into the snapshot.

        Kept free of socket concerns so tests can drive a message sequence
        directly.
        """
        mtype = msg.get("type")
        data = msg.get("data") or {}
        if not isinstance(data, dict):
            return

        with self._lock:
            # Queue depth rides on `status`, which carries no prompt_id — it is
            # about the server, not about us, so it is read before the filter.
            if mtype == "status":
                info = ((data.get("status") or {}).get("exec_info") or {})
                try:
                    self._queue = max(int(info.get("queue_remaining", 0)) - 1, 0)
                except (TypeError, ValueError):
                    self._queue = 0
                return

            # Once bound, ignore other clients' prompts. Messages with no
            # prompt_id at all are accepted: on our own clientId socket they're
            # ours, and dropping them would lose progress on older ComfyUI builds.
            pid = data.get("prompt_id")
            if pid is not None and self._prompt_id is not None and pid != self._prompt_id:
                return

            if mtype == "execution_start":
                self._reset()
                self._started = True
            elif mtype == "execution_cached":
                # Cached nodes are skipped outright and never "execute".
                for node in data.get("nodes") or []:
                    self._done.add(str(node))
            elif mtype == "executing":
                node = data.get("node")
                if self._current is not None:
                    self._done.add(self._current)
                if node is None:
                    # End of prompt: everything ran.
                    self._current = None
                    self._current_frac = 0.0
                    self._phase = None
                    self._step = self._steps = 0
                    self._percent = 100.0
                    return
                self._current = str(node)
                self._current_frac = 0.0
                self._phase = self._title(node)
                self._step = self._steps = 0
            elif mtype == "progress":
                node = data.get("node")
                if node is not None and str(node) != self._current:
                    if self._current is not None:
                        self._done.add(self._current)
                    self._current = str(node)
                    self._phase = self._title(node)
                self._step, self._steps = _step_pair(data)
                self._current_frac = (self._step / self._steps) if self._steps else 0.0
            elif mtype == "progress_state":
                self._handle_progress_state(data)
            else:
                return

            if mtype in ("execution_cached", "executing", "progress"):
                self._started = True
            self._recompute()

    def _handle_progress_state(self, data):
        """Newer ComfyUI: per-node execution state in one message.

        Carries the running node's step counts, which is what the phase caption
        needs. It is *not* a full picture of the graph: observed against ComfyUI
        0.34.2, it lists only the nodes it holds progress records for — a run
        whose first node came from `execution_cached` never appears in it. So its
        finished nodes are unioned into what we already know rather than
        replacing it, otherwise progress would drop back on every one of these.
        """
        nodes = data.get("nodes")
        if not isinstance(nodes, dict):
            return
        done, running = set(), None
        for node_id, entry in nodes.items():
            if not isinstance(entry, dict):
                continue
            state = entry.get("state")
            if state == "finished":
                done.add(str(node_id))
            elif state == "running" and running is None:
                running = (str(node_id), entry)
        self._started = True
        self._done |= done
        if running is None:
            self._current = None
            self._current_frac = 0.0
            self._phase = None
            self._step = self._steps = 0
        else:
            node_id, entry = running
            self._current = node_id
            self._phase = self._title(entry.get("display_node_id") or node_id)
            self._step, self._steps = _step_pair(entry)
            self._current_frac = (self._step / self._steps) if self._steps else 0.0

    def _recompute(self):
        """Update the percentage. Caller holds the lock.

        Every node counts the same, with the running node's step fraction
        interpolated into its slice. Clamped non-decreasing: ComfyUI revisits
        nodes and multi-pass graphs re-run samplers, and a bar that goes
        backwards is worse than no bar at all.
        """
        if not self.total_nodes or not self._started:
            return
        done = len(self._done)
        if self._current is not None and self._current not in self._done:
            done += min(max(self._current_frac, 0.0), 1.0)
        pct = min(100.0 * done / self.total_nodes, 100.0)
        if self._percent is None or pct > self._percent:
            self._percent = pct


def _step_pair(entry):
    """Read a (value, max) step pair off a progress payload, defensively."""
    try:
        step = int(entry.get("value") or 0)
        steps = int(entry.get("max") or 0)
    except (TypeError, ValueError):
        return 0, 0
    if steps <= 0:
        return 0, 0
    return max(min(step, steps), 0), steps


def node_titles_for(workflow):
    """Map node id -> display title for an API-format workflow.

    ``_meta.title`` is what ComfyUI's API export carries and what the [opt:…]
    markers already rely on, but ``convert_ui_to_api_format`` does not emit it —
    so the class_type fallback is the real path for UI-format templates, not a
    formality.
    """
    titles = {}
    for node_id, node in (workflow or {}).items():
        if not isinstance(node, dict):
            continue
        title = (node.get("_meta") or {}).get("title") or node.get("class_type")
        if title:
            titles[str(node_id)] = str(title)
    return titles
