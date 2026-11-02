# Experimental protocol

## Separate deployment and policy questions

| Question | Baseline | Candidate | What changes |
|---|---|---|---|
| FIWARE distribution | Monolithic Streamlit pipeline | Federated Streamlit pipeline | One stack versus eleven |
| RL service placement | Monolithic RL | Federated RL, shared physics | Local inference process placement |
| Simulation replication | Federated shared physics | Federated full-city replicas | Eleven additional simulators |
| Control quality | No agents / local-only | Locals plus coordinator | Control law |

Hold the policy and simulation configuration constant when comparing deployment.
Compare `none` with `none`, `local` with `local`, and `coordinated` with
`coordinated`. No-agent versus coordinated is a control experiment, not a pure
architecture-overhead measurement. Both new dashboards use the GreenWave network;
the original BoMoDT network is different and is not silently substituted into the
paired workload. The new monolith is a common-stack adaptation, not its old CLI.

## Paired workload design

Prepare one verified FIWARE-backed scenario, then reuse its immutable route file
in both dashboards. Freeze map, routes, seed, source time, detectors, TLS programs,
policy weights, control interval, threads and output logging. Check scenario map
and route hashes before running. Use held-out source dates and both training hours
for all four profiles. Source timestamps and replay tenants are preserved.

For each scenario run architectures in randomized order without overlapping
runs. Stop the unused FIWARE project if measuring whole-host footprint. Separate
cold start, idle baseline and steady state; distinguish warm file caches and pin
CPU budgets. Start with repeated paired runs and select sample size from measured
variance. Report paired differences and uncertainty across scenarios. Adjacent
control cycles are not independent replications.

First verify deterministic action traces and traffic outcomes agree under the two
RL placements for each profile. Validate replica-state equivalence before using
replication measurements. A topology mismatch or divergent control trace
invalidates a pure placement-overhead interpretation.

## Resources

The runtime records application/SUMO process-tree samples in `resources.json` and
Docker snapshots for the selected FIWARE project in `fiware-resources.json`.
Separate these measurements when reporting costs. Include all containers for
city-level totals, and report per-TAZ distributions as well. The shared Streamlit
control plane also consumes resources; include it or state its exclusion in both
architectures. Raw Docker snapshots retain error messages; an inaccessible daemon
is not reported as zero resource use.

Report CPU seconds, memory peaks and steady-state levels, disk bytes, process and
container counts, and wall time per simulated second. RSS sums can double-count
shared pages; USS/PSS and cgroup v2 `memory.peak`, `memory.current`, `cpu.stat` and
`io.stat` provide stronger accounting. Short processes/peaks may be missed by
polling. Record resolved container image IDs/digests, package versions and host
hardware before collecting results. Moving image tags must be pinned for a
reproducible comparison.

Network bytes/messages require transport or network-namespace counters; Python
object sizes are not wire-byte measurements. Host energy needs an independent
energy counter or external meter and cannot be derived from CPU utilization alone.

## Latency and clocks

Keep wall-clock milliseconds and simulated seconds distinct:

- Emulator → IoT Agent acceptance is recorded per sample. This does not prove
  broker processing or persistence.
- The complete ingestion → verified Timescale barrier includes transmission,
  persistence and polling. It is not a precise per-component service-time measure.
- Decision barrier includes coordinator computation, local transmission emulation,
  serialization, local inference and persistence.
- Actuator-with-readback wall time records program state before/after a nonzero
  request, requested/applied delta and clipping/no-op details.
- Control → next observation includes SUMO advancement and feedback persistence;
  it cannot be labelled network RTT or actuator-only latency.
- Full cycle additionally includes replica catch-up when enabled.

Use p50/p95/p99/max and retain raw traces. Three-cycle demo percentiles do not have
scientific significance. Report startup separately, derive simulation real-time
factor, and count missed control deadlines. Single-host monotonic timestamps are
not a multi-host clock-synchronization protocol.

A successful TraCI readback confirms simulated program state, not the physical
instant a real lamp changes. The true no-agent path has no policy actuation latency;
report that field as unavailable rather than zero.

## Traffic quality

Report CO2, NOx, waiting time, throughput, arrivals and teleports alongside resource
cost. Use identical treatment of unfinished trips and identical output settings.
Instantaneous emission rates need integration over simulation time; state units.
Detailed emission XML can dominate disk footprint, so never enable it in only one
architecture. Assess worst-zone and dispersion metrics as well as city totals.
Route-fitting `mismatch.xml` documents whether observed edge counts were actually
reproduced; aggregate demand is not proof of a good fit.

## Original BoMoDT application

The old source checkout remains intact. `tools/bomodt_feedback.py` can instrument
its libtraci command/readback calls if a controller is integrated there. It adds
measurement, not a controller. The new Streamlit monolith supplies the common
FIWARE/scenario/RL pipeline on the GreenWave map instead. Comparisons against the
old CLI still require aligning its map, services, feedback and workload explicitly.

## Timed workload and GUI controls

Record source period, seed, interval length, playback speed, transmission delay,
GUI flag/delay, controller and dashboard inclusion. Reuse them across placements.
Pacing lateness measures inability to meet the chosen source clock, not network
RTT. Hour-finalization barriers include readback/retries but exclude planned
playback waiting. App CPU is computed from process CPU-time deltas; container CPU
comes from Docker. Container memory usage is distinct from process RSS/USS.

Footprint & latency exports raw CSV, configuration/summary JSON and PNG/SVG plots
in a ZIP. Capture includes Streamlit for UI-launched jobs and excludes it for
standalone CLI runs. Compare like launch paths. Report rendering happens after
capture. JSONL ingestion traces avoid quadratic history rewrite overhead; sampling
and trace overhead are still included. Short smoke-test plots are functional
checks, not statistically sufficient evidence of architectural efficiency.

## Active FIWARE control workload

The default simulation now transports observations, coordination context, commands
and feedback through FIWARE every 600 simulated seconds. Timescale verification
completes before each physics advance. The new `--offline` option retains the
previous uncoupled baseline. Match this setting in both architectures; old
simulation reports are not active-control benchmark evidence.

Use `operation-resources.json` for whole-operation application capture and the
parallel Docker samples for stack consumption. Report control-loop time excluding
physics, full cycle time, per-stage HTTP/publish-to-read latency and persistence
verification separately. I/O fan-out concurrency is the same in both modes. Body
byte counts, aggregate container-interface bytes and unique wire bytes are
separate concepts. See [the implementation and metric contract](closed-loop.md).
