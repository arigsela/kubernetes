# hk demo: saved runs in Valkey

Date: 2026-10-25. Owner's call in conversation: "run instantly is the goal" (not keeping runs past
their 7 days), Valkey in the cluster ("lets implement valkey please"). App: `arigsela/hk-demo`
v0.7.0. Cluster: `base-apps/hk-demo/valkey.yaml` (§T.143).

## Why

Picking a past run on Ask hk or On its own rebuilt it from scratch: about a dozen reads against
Loki, Tempo, Prometheus and GitHub. Measured in the pod on 2026-10-25, opening a run took 11-31 s
(13 s for an alert run), while the lists took under 1 s. The time went to:

- reading the trace (about 2 s);
- the adapter's log lines (about 2 s);
- waits meant for a run that just ended, applied to old runs too: up to 2 min looking for a
  trace, and 30 s for a late `a2a.request` span.

A run asked since the pod started was already instant from memory, but every release restarts
the pod (and every hk-demo PR is a release).

## What it does

- **Saved:** once a run has settled, the app keeps its evidence in Valkey. That is the trace, the
  log lines (run, Gateway, waypoint, adapter), the waypoint's principal, and whether the trace or
  the `a2a.request` span were missing. A later pick builds the boxes from that copy at once.
- **Read live on every pick:** what is meant to be current, or still growing:
  - the "today, all agents" totals, the gateway's spend today and refused calls today;
  - the agent's last eval score;
  - an alert run's memory series, which grows until the alert resolves, often after the run is
    saved;
  - an alert run's pull request, whose state changes when a person merges or closes it.

  The dispatcher's lines come from the 10-second cache the list already uses.
- **Only clean builds are saved:** a failed store read (a timeout, a 5xx) looks like "no data", so
  the stores count them. A build that saw one is not saved, so a Loki blip never becomes an 8-day
  copy with empty boxes. A 404 (a trace past retention) is an answer, not a failure.
- **Evidence, not boxes:** the copy keeps the inputs, so a later release that changes how a box
  checks or reads applies to saved runs too. Box JSON would have frozen the old checks until the
  copy expired.
- **When a run is saved:**
  - 5 min after it ended. For an alert run, that is 5 min after its finding line; a timed-out
    run, which goes on in its session after the dispatcher stops waiting, waits 5 min past its
    30-min pull request window.
  - A copy with its trace is kept 8 days, past the 7 days the lists show. A copy without its trace
    is kept 1 hour and then tried again. A missing `a2a.request` span does not count: 5 min after
    the run it is not coming (the collector holds a trace about 10 s).
- **The saver:** a background task, 30 s after the app starts and then every 5 min.
  - It copies each settled run in the lists that has no copy yet, Ask hk first, newest first, one
    at a time.
  - It never calls GitHub (60 requests an hour, unauthenticated) and never reads the totals.
  - It stops while a question is running or its boxes are filling, and when Valkey fails.
  - A run it could not save (a failed read, an error) is tried again after an hour, and does not
    hold up the runs after it.
  - A pick of an unsaved settled run also saves it.
- **Old runs are looked up once, over their own window:** a run that ended over 3 min ago gets one
  look for its trace, spans and log lines, as On its own already did; the waits stay for a run
  that just ended. Its log and trace searches end 2 min after the run, not now. Before, a 6-day-old
  run's log query scanned 6 days of three namespaces. The model box now says "trace not found in
  Tempo" (it said "after 2 min", which was only true of a live run).

## Valkey

- `valkey/valkey:9.1.2-alpine`, one pod in the `hk-demo` namespace, pinned by digest, updated by
  Renovate.
- A cache only: no disk writes (`--save ""`, `--appendonly no`), 32 MB `maxmemory` with
  `allkeys-lru` (a copy is 40-75 KB compressed; 19 Ask hk runs and 1 alert run in the 7 days
  measured).
- Restarted, it starts empty and the saver refills it over a few minutes.
- Its own pod, so an hk-demo release keeps the copies.
- **Who connects:**
  - an Istio AuthorizationPolicy (ztunnel, L4) allows port 6379 only from
    `cluster.local/ns/hk-demo/sa/hk-demo`;
  - a NetworkPolicy allows only the hk-demo pod (6379, and 15008 for HBONE).
- **No password**, so nothing in Vault.
- **The one user** may run GET, SET, EXISTS and PING, only on `hk-demo:*` keys. FLUSHALL, KEYS,
  CONFIG, INFO, EVAL and DEBUG are refused, so a caller that got in could neither reconfigure it
  nor empty it.
- The settings are container arguments, so a change rolls the pod.
- Non-root (uid 999), read-only root filesystem, no capabilities, exec probes (`valkey-cli ping`
  over loopback, which the mesh never sees).

## Failure is a miss

`VALKEY_URL` unset (local runs, tests), Valkey down, a copy that will not decode: the run is
built from the stores as before. The client connects in 0.5 s at most, never retries, and does not
send `CLIENT SETINFO`. After a failure the app leaves Valkey alone for 30 s and logs the error
class once.

## What is stored

The raw evidence, as the stores hold it: log lines and span attributes, including the
question as the agent saw it. The app masks at render time (§V.107), so the copy is not masked.
It is held in memory only, in hk-demo's namespace, readable only by hk-demo's principal, and
expires after 8 days. Loki and Tempo hold the same data unmasked for longer.

## Calls made in the build

- **Evidence, not the boxes:** see "What it does". The owner was told "save a run's final boxes";
  evidence gives the same result and survives box changes in later releases.
- **The background saver:** keeps runs nobody has opened yet (alert runs, runs from before a Valkey
  restart) instant on their first pick, which the goal ("run instantly") needs.
- **Old runs looked up once:** makes an unsaved pick and the saver's own reads faster.
- **The "today" totals stay live,** because the boxes label them "today".
- **No password; mesh identity and the ACL instead.** This is the platform's
  callers-as-principals pattern, and there is no secret to rotate.
- **Arguments instead of a ConfigMap,** so a settings change restarts Valkey.
- **The settle times:** 5 min, and 30 + 5 min for a timed-out alert run.

Live 2026-10-26 (v0.7.0): the saver's first round saved all 19 Ask hk runs of the week. A saved
pick shows its boxes 0.4-0.5 s after the click (it took 11-31 s before). The stream ends 1.3-5 s
in: the second boxes event waits for the live "today" totals, and the refused-calls count is a
24-hour Loki query that takes 0.2-4.6 s. Caching the totals for a minute would close that gap.

The whole-change review (2026-10-25, before release) found the failed-read, memory-series,
unbounded-window and stuck-saver problems above; each is fixed with a test that failed first. Left
as they are:
- the saver's two 7-day list reads every 5 min (under 1 s of Loki each);
- a pick still waits for the answer's GetTask before the copy;
- the "saved N" log line counts a save that Valkey then refused;
- shutdown does not wait for the saver or close the client.
