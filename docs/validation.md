# Validation record (2026-10-29)

## Current live evidence

Docker/network access is now available. The complete federated data path has been
exercised on the running eleven FIWARE stacks:

- Source date 2025-01-01, source hours 8 and 17: **156/156 measurements verified**
  after IoT Agent → Orion-LD → QuantumLeap → Timescale ingestion.
- Replay: `sessions/federated/gw_20261029_165333_08ae1b7e/manifest.json`, status
  `verified`, with completed source hours `[8, 17]` and per-attempt traces.
- Exact previously failing device `loop_97cb3720a353e2f7152d`, TAZ B: the persisted
  count/time/sample identity match the source and its decoded name is
  `VIA DE' CARRACCI`.
- A real Grafana PostgreSQL datasource query returns the original Carracci and
  Matteotti names. The running provisioned dashboard contains the same decoded
  query as the generator, checked using Grafana's dashboard API.
- The explicit live integration test passes with `BOMODT_LIVE_TEST=1` and
  `BOMODT_LIVE_MODE=federated`, creating another verified one-hour replay.

Live probes reproduce the pinned Orion-LD 1.5.1 NGSI-v2 restriction with both Text
and TextUnrestricted. The earlier TextUnrestricted-only change was not sufficient.
UTF-8 hex is now used only for descriptive road metadata and decoded at read time.
Live testing also observed missing notifications after successful measurement
HTTP responses. Per-hour persistence barriers and bounded missing-sample repair
now cover that failure; repairs retain the same sample ID, timestamp and count.
The two-hour verified replay required one repair batch per hour, recorded in its
manifest. Failed earlier sessions remain available as diagnostic evidence.

## Automated checks

The full suite under the user's Conda Python 3.11 environment reports **28 tests,
27 passed and one live test skipped by default**. The skipped live test then
passes separately with the live flag enabled. Checks include:

- Both English Streamlit entry points, five tabs, source preview and prepared
  scenario actions, using AppTest.
- Compose validation, 7/77 service isolation, broker entrypoint argument
  regression, health gating and regeneration of stale configuration.
- Bodyless HTTP requests, JSON writes, provisioning and exact-device street-name
  codec round trips, delivery retries, source-hour barriers and complete history.
- Real routeSampler execution and a complete no-agent SUMO run with an injected
  history boundary; this is distinct from the live ingestion evidence above.
- Worker failure/cleanup, local ownership and sequences, synthetic placement
  action parity, model checksums, strict checkpoint loading and row-inference
  parity for all four profiles (local and coordinated policies).

Validated application packages: Python 3.11.18, torch 2.15.0, torchrl 0.14.1,
tensordict 0.14.3, Streamlit 1.66.1 and psycopg 3.3.6. This is an environment
record, not a new universal compatibility claim or a complete frozen lock file.

## Remaining limits

The October 29 checks did not revalidate a full learned-policy SUMO episode or
independent replica-state equivalence. The October 30 local-policy GUI check is
recorded below; coordinated episodes and replica equivalence still need separate
validation. AppTest does not prove browser iframe rendering;
the live Grafana datasource and provisioned query checks validate the data path.
The original network still emits inherited unsafe-green-link warnings. No network
program rewrite has been performed, and a simulated state readback does not
confirm physical traffic-light behavior. Results from one live date are not a
resource/performance benchmark across scenarios.

## Dashboard, timed emulation, GUI and report update (2026-10-30)

The dashboards now have six tabs, with diagnostic controls/logs isolated from the
normal workflow. AppTest covers both modes, a source hour outside the training
schedule, the single simulation action and GUI flag propagation. Actual workspace
rendering with recorded reports also completes without exceptions.

The full suite reports 34 tests, 33 passed and the opt-in live test skipped. New
checks cover exact conservation of 150 hourly passages, seed reproducibility,
variable minute rates and zero counts, midnight rollover, exclusion of incomplete
stream totals from scenario history, GUI propagation to both simulation paths,
CPU delta/byte-unit conversion and CSV/JSON/PNG/SVG ZIP artifacts.

Real tests on the local services:

- Monolithic timed replay from 2025-01-01 23:00 for two hours, crossing midnight:
  all 193 source sensor/hour totals verified. Replay
  `sessions/monolithic/gw_20261030_081121_8c9ddc1e`.
- Federated timed replay at 2025-01-01 14:00, independent of model training hours,
  with one-minute reports: all 78 source totals verified, 4684 transport attempts
  including four final-report repairs. Replay
  `sessions/federated/gw_20261030_081429_ba569ec2`.
- A subsequent federated replay validates the append-only JSONL collector and
  current exports: `sessions/federated/gw_20261030_082147_0fd50467`.
- Actual SUMO GUI with automatic start/close completes a 3600-second no-agent
  scenario in `experiments/gui-smoke-20261030`.
- Actual SUMO GUI also completes the local-policy simulation, six control cycles,
  in `experiments/gui-local-smoke-20261030`.
- Measured runs have CSV/JSON reports and standalone PNG/SVG plots, collected
  into ZIP exports. A generated footprint plot was visually inspected.

Playback was accelerated for these checks. They establish operation and exported
measurement availability, not comparable architecture benchmark conclusions:
source periods, pacing intervals, GUI and service availability differ. CLI tests
do not include a Streamlit server; UI jobs include their originating server PID.
Reports distinguish application RSS/USS from Docker memory usage and record the
architecture and settings. Hardware GPU/energy measurement is not implemented.

## Subscription-pool starvation recovery (2026-10-30)

The TAZ B broker could answer `/version` but stalled on data/subscription queries.
A runtime debugger trace showed a wait on the legacy Mongo connection-pool
semaphore. Broker and Mongo restarts alone did not recover it. An explicit
`-dbPoolSize 64` restored requests; both architectures now use that capacity.
This mitigates starvation in the pinned broker, rather than fixing its driver.
See troubleshooting for the remaining tenant-capacity limitation.

Client readiness now exercises tenant subscriptions; socket errors identify the
safe endpoint and HTTP method, and provisioning errors identify the owner/TAZ.
The regression suite completed 36 tests: 35 passed, one opt-in live test skipped.
A separate real timed federated replay,
`sessions/federated/gw_20261030_083914_2928a45a`, verified all 78 source measurements
at 08:00 on January 1. All eleven stacks then passed database-backed readiness.
No stored data were removed. Both deployments remain running as found at the
start of this repair. Dashboard rendering also exposed and fixed late-bound
period labels when monitored and verified replays cover different hours.

## Active FIWARE simulation loop (2026-10-30)

Default dashboard/CLI simulations now use FIWARE every 600 simulated seconds,
including broker-consumed observations/context, broker-mediated commands, TraCI
readback feedback and verified history before advancing physics. A final t=3600
snapshot is persisted without another action. Offline runs remain explicit.

The full suite completed 47 tests: 46 passed and one opt-in live test skipped.
Seven new control/measurement tests cover payload integrity and sequence gates,
matching data with different broker placement, worker reads without an observation
RPC shortcut, provisioning, missing-history repair without changing logical event
identity, whole-operation CPU/I/O/transport accounting, raw export inclusion and
both dashboard simulation-state views. Targeted checks also pass after timeout
bounds and plot-scale refinements.

Separate live runs used the immutable scenario
`scenarios/20261030-103426-monolithic-147482`, February 1 at 09:00, profile
`200_weekends_9_20`:

- Coordinated monolithic and federated runs completed six cycles, with identical
  actions and coordinator prices. Each persisted 275 logical events: six rounds
  of four event kinds for eleven TAZs plus eleven terminal observations.
  Artifacts: `experiments/fiware-loop-rl-mono-20261030`,
  `experiments/fiware-loop-rl-fed-20261030`, and the later
  `experiments/fiware-loop-coordinated-fed-final-20261030`.
- Local-only monolithic run completed six cycles in
  `experiments/fiware-loop-local-mono-confirm-20261030`.
- No-agent runs in both architectures completed one hour and persisted 143 logical
  events: seven observations and six TLS-state feedbacks per TAZ.
  Artifacts: `experiments/fiware-loop-passive-mono-final-20261030` and
  `experiments/fiware-loop-passive-fed-repair-20261030`.
- Active control Grafana provisioning was fetched from both deployments; both
  panel SQL queries executed against the final federated TAZ F history, returning
  25 stage/state rows. Export ZIP integrity was checked and a latency plot viewed.

A preliminary passive run exposed a missing history notification; repair handling
was added and validated. One preliminary local-only run failed in the inherited
terminal emission parser; a clean repeat completed without a parser change. Its
failed artifact/log remains, and that isolated parser failure has not been
explained. Application failures now preserve their detailed runtime cause when
the launcher also reports a subprocess failure.

These are functional checks on one scenario, not a full model/scenario benchmark.
They do not validate multi-host clock synchronization, territorial co-simulation,
GUI/replica equivalence under the new data path, or all eight checkpoint modes in
live FIWARE. Smoke runs include development activity and are not a performance
ranking. The final service state is restored to the initial state: monolithic
stopped, federated running. See closed-loop.md for capture boundaries and limits.

## Terminal TraCI clock fix (2026-11-02)

The final live clock query added to horizon validation failed after the upstream
environment's terminal step had already closed TraCI. The adapter now records
actual time immediately after physics advances, and the runtime uses that last
measured time for its final summary/check. A regression test reproduces terminal
closure, verifies that a direct clock query fails, and checks preservation of the
measured time and its reset between episodes.

The reported scenario `scenarios/20261102-140047-monolithic-068fd1` was rerun with
federated coordinated control and active FIWARE in
`experiments/20261102-terminal-clock-federated-validation`. It completed six
cycles, recorded an actual final time of 3600 seconds, persisted 275 logical
control events and generated the report/ZIP. It was a CLI functional check, not a
paired dashboard resource benchmark. The original failed artifact was preserved.

## Measurement catalogue and data-path plots (2026-11-02)

The comparison selector now discovers operation-root files only. Recursive
summary discovery previously misclassified metrics/summary.json exports as extra
runs. The default view contains completed dashboard simulations with active
FIWARE, ordered by start time. Source emulation has a separate view; CLI validation,
offline and failed/incomplete operations require explicit inclusion. Existing
artifacts were preserved.

The full suite completed 52 tests: 51 passed and one opt-in live test skipped.
New checks cover root-only discovery, provenance/status/offline filters, category
switching in Streamlit, and requiring the matching event/payload from a Grafana
query before recording its latency. The new data-path charts also rendered in
both real-workspace AppTest dashboards. Every one of the 28 metrics in the new
runs has a registered human-readable boundary definition.

Live coordinated runs of the same January 2, 08:00 scenario completed six cycles:
`experiments/20261102-data-path-latency-validation` (federated) and
`experiments/20261102-data-path-monolithic-validation` (monolithic). Each produced
77 snapshot-to-Grafana-query measurements, 66 persistence-confirmation-to-actuation
measurements and zero Grafana observer failures. The plot was visually inspected.
Queries use the actual Grafana datasource API and match event identity and payload;
these are not browser paint measurements. Observer polling/resource costs are
included. Initial source snapshots can include startup delays.

Protocol version 2 verifies observation history before policy evaluation. Per-query
readback timestamps preserve each TAZ's first observed persistence boundary;
they are not exact database commit timestamps. Compare equal protocol/observer
settings and never synthesize these intervals from historical exports. The
functional checks do not rank the architectures, and the validation CLI runs
remain hidden from the default dashboard-run selection. Services were restored
to monolithic stopped and federated running.

## Per-controller footprint (2026-11-02)

Inference records now include current-thread CPU time per logical TAZ decision.
A PID-plus-creation-time registry identifies the central runtime and independent
local workers in sampled resource data. Exports add policy-work.csv, components.csv
and policy/process/IoT-Agent footprint plots. The dashboard displays logical
per-TAZ work, its execution placement, and physical CPU/memory separately. Shared
monolithic RAM is not divided among logical controllers, and historical unknown
PIDs are not guessed to be workers.

Real SUMO coordinated attribution checks used the current saved scenario
`scenarios/20261102-151158-monolithic-bc6f83` in explicit offline mode, without
starting or modifying the user's stopped services. Both runs completed six cycles
and 66 logical decisions with identical actions, and recorded CPU for all eleven
TAZs. The monolith registry identifies one shared central runtime; the federated
capture identifies eleven local worker processes. Artifacts:
`experiments/20261102-agent-cpu-mono-validation` and
`experiments/20261102-agent-cpu-fed-validation`. These check attribution, not active
FIWARE performance or an architecture ranking. An initial coupled check failed
readiness because the services were stopped; that failed artifact is preserved.

New tests verify physical identity and CPU deltas, no invented per-TAZ shared
memory, unavailable historical CPU, and individual IoT Agent container accounting.
The real-workspace Streamlit view rendered per-controller CPU and placement charts;
ZIP contents include the new CSV/plot artifacts. Existing dashboard reports were
re-exported from their original raw samples without fabricating missing CPU tags.

## Readable matched reports and CPU accounting audit (2026-11-02)

The old CPU plot used 100% per logical core, not percent of the whole host.
Observed application peaks near 3200% on this 32-logical-CPU host represented
approximately 32 occupied CPUs. Plots now use logical CPU cores and total sampled
CPU seconds. A separate missing-sample bug was corrected: each process rate uses
its own previous timestamp, including new per-process sampling timestamps, while
CPU work sums measured counter increments directly. Regression checks cover
missing samples and unequal per-process/global sampling boundaries.

Process presentation aggregates semantic roles without PID axes. Role memory
peaks use simultaneous sums rather than adding individual PID peaks. Raw PID
identity remains in components.csv. Missing measurements are listed in coverage
CSV instead of producing empty/unavailable panels. Legacy unknown ownership is
kept explicit and is not inferred to be agent data.

A matched-pair exporter validates known scenario/controller/integration settings
and produces PDF, PNG/SVG, comparison CSV, ratios, run metadata and coverage in one
ZIP. UI/exports use the same memory accounting (USS when both runs have it, RSS
otherwise), and separate application and FIWARE scopes. The two main path latency
figures use real milliseconds and median/p95 on common axes. Raw per-run views
are collapsed. Existing dashboard exports were regenerated without modifying raw
samples; missing historical CPU-per-decision data were not invented.

The presentation PDF/figures were generated and visually inspected from prior
matched development records in comparisons/20261102-readable-fiware-validation
and comparisons/20261102-readable-offline-validation. These demonstrate the format,
not statistically established performance. Streamlit AppTest selected a real pair,
rendered the policy chart and built/downloaded the presentation report.

All 58 tests were exercised in isolated per-module Python processes (57 passed,
one opt-in live skip). Targeted presentation regressions pass. A combined single-
process suite attempt reached the final Streamlit tests then crashed in native
code without a Python assertion failure; this mixed native-runtime test-process
instability remains unresolved. Separate-process checks, matching the production
separation of Streamlit and RL runtimes, all completed. No services were started
or stopped for this reporting-only work.
