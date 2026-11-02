# FIWARE-mediated simulation and measurement

Dashboard simulations and `python -m federation.fiware simulate` now use an active
FIWARE data path by default. The existing saved scenario is still reusable across
architectures; no new route generation is needed. Start the selected deployment
before running. `--offline` (or the advanced offline checkbox) retains the legacy
SUMO/RL experiment without FIWARE traffic. Old results remain labelled offline.

## Timing and ownership

The duration is 3600 simulated seconds with six decisions at t=0,600,...,3000 and a
terminal observation at t=3600. The 600-second decision interval matches the local
and coordinated training scripts. SUMO's internal physics step is unchanged.

For each barrier:

1. Publish each TAZ's observation/mask and flow row through IoT Agent to Orion.
2. Read all same-sequence observations from Orion, confirm their Timescale
   persistence, then make them available to the single coordinator.
3. Publish coordinator price/context to each TAZ's broker via IoT Agent.
4. Local controllers fetch observation and context from Orion, infer an action,
   and publish a command event through IoT Agent. Local worker RPC carries the
   sequence/reference; it does not substitute an in-memory sensor observation.
5. The authoritative SUMO actuator reads every command from Orion before applying
   the batch. After application, program durations/state are read back via TraCI.
6. Publish that feedback through IoT Agent, read it from Orion and verify all
   logical events for the barrier in QuantumLeap's Timescale history.
7. Advance physics by 600 simulated seconds and collect the next observation.

A fresh tenant per simulation isolates sequence identities and histories. Each
TAZ and event kind owns one latest-state entity of type `TwinControlEvent`.
`payloadHex` losslessly encodes JSON to accommodate the pinned broker's restricted
Text parser. This preserves the checkpoint's observation ordering and numeric
values. Historical rows retain event identity, TAZ, kind, sequence, source UTC
time and simulated elapsed seconds. The terminal snapshot is persisted without
issuing another action.

Monolithic mode routes all events through the one city stack. Federated mode uses
the eleven independent TAZ stacks and inference workers. I/O fan-out concurrency
is identical in both modes; only stack/controller placement changes. One global
coordinator and authoritative SUMO remain. Optional replicas are additional
full-city physics replays, not territorial partitions.

Commands are broker-mediated `TwinControlEvent` messages read by the TraCI
actuator. They do not use IoT Agent's separate device-command protocol. There is
no direct broker-write shortcut and no CSV/shadow fallback on a control timeout.
Wrong/future sequences or payload conflicts fail the run. If history notifications
are missing, up to three repair batches resend the same logical event through
IoT Agent with a fresh delivery nonce. Repairs do not create new decisions.

No-agent runs retain normal SUMO TLS programs and do not load Torch/policies.
They publish approach-edge vehicle/halting/emission-rate snapshots and TLS state
at the same boundaries. These are explicitly labelled raw snapshots, not the
304-feature RL observation schema; compare no-agent with no-agent. They have no
command, inference or policy actuation latency. Emission snapshots are rates,
not integrated city emission totals.

## Latency interpretation

All latency values are real wall-clock milliseconds on the same host, not
simulated seconds or isolated network propagation measurements.

| Metric | Interval |
|---|---|
| `<stage>_iot_accept_ms` | HTTP sensor/controller publication until IoT Agent response |
| `<stage>_publish_to_read_ms` | Before publication until matching state is read from Orion; includes HTTP, processing and any polling |
| `world/local_<stage>_broker_read_ms` | Consumer GET/poll duration; world and local readers are kept separate |
| `command_to_readback_ms` | Command creation in local worker until authoritative TLS state readback; includes queueing for the complete batch |
| `control_history_barrier_ms` | Verification of all published payloads for this sequence in Timescale, including polling/repairs |
| `control_closed_loop_ms` | Before observation publication until feedback and history are verified, excluding physics advance |
| `physics_advance_ms` | SUMO physics advance, excluding FIWARE barriers |
| `cycle_ms` | Full RL cycle including observation/control transport, physics, feedback bookkeeping and optional replica catch-up |

Passive runs expose `telemetry_cycle_ms` instead of a policy-control-loop metric.
Local inference and coordinator compute are recorded separately. Persistence is
a synchronous barrier in both architectures; this intentionally measures a
consistent durable-control workload. It is not a real-time asynchronous deployment.
Slow services lengthen real execution, not simulated decision timestamps. Consumer
HTTP failures stop the run; they are not silently replaced with cached state.
A TraCI readback confirms simulated programs, not physical hardware.

## Whole-operation resources

`operation-resources.json` captures the launcher plus runtime descendants, all
local workers, the coordinator, SUMO/replicas and the originating Streamlit server
for UI jobs. Capture starts before runtime import/setup/provisioning and finishes
after runtime/worker/SUMO teardown. `fiware-resources.json` samples all running
containers of the selected Compose project in parallel. Plot generation is outside
capture. Container startup before launching the simulation, earlier emulation,
route preparation and idle time outside this operation are separate phases, not
silently included in the simulation duration.

Reports contain CPU seconds, CPU utilization, application RSS/USS and Docker
memory peaks, application disk I/O, container block I/O and container-interface
network-counter deltas. `accounted_cpu_seconds` adds disjoint application and
container estimates when both exist. No combined RSS/container memory total is
claimed. Network totals count both ends of internal transfers and are not unique
wire bytes. Published JSON body sizes are separately recorded with retries and
exclude headers, TCP/IP and responses. Sampling can miss short peaks/processes;
container CPU seconds are approximate integrals, not cgroup lifetime accounting.
Docker daemon, OS/kernel, browser rendering, unrelated processes and energy are
outside this application/container accounting. Monitoring and logging overhead
remain included and should be identical between comparison runs.

Export ZIPs include CSV/JSON, PNG/SVG plots, control transport traces, source/run
configuration and process/container samples. The comparison UI flags mismatched
integration, control interval, GUI, replicas, route/network hashes and controller.
Never combine old offline runs with active FIWARE runs as architecture pairs.

## Inspecting the active twin

In **FIWARE & Grafana**, choose **Simulation control**, select the run and TAZ,
and inspect its event timeline and decoded state. The separate Traffic emulation
view retains source hourly sensor data. Refresh workspace to discover newly
started/completed runs. Grafana provisioning files are generated by Start services.

Use identical routes, control, launch method, logging and GUI/replica settings;
run one architecture at a time and repeat matched pairs. The current transport
measurements are on one machine's Docker/loopback networks, not a geographical
multi-host latency experiment. Six control cycles are functional evidence; they
are not enough to establish architecture superiority or stable tail estimates.

## Version 2 data-path timing

New runs verify observation persistence before inference and run an asynchronous
Grafana datasource probe for every snapshot. Snapshot, historical-confirmation
and actuation-readback timestamps identify the same sequence/TAZ. See
[the latency guide](latency-guide.md) for boundaries, observer cost and limits.
Compare equal control protocol versions and do not mix new path timings with
legacy runs. The Grafana probe measures query availability, not browser painting.

## Per-controller work and physical resources

Total application and FIWARE footprint remains the architecture cost. New agent
runs also record per-TAZ inference CPU (current-thread time), wall time and decision
counts. Both modes perform the same logical TAZ work. The monolithic runtime
executes those decisions sequentially in one process with a shared policy; the
federation uses a separate worker process/policy copy for each TAZ.

`policy-work.csv` and `policy-footprint.png/.svg` show measured policy work per
TAZ. CPU excludes broker reads, command publication and other service duties;
physical process CPU accounts for those separately. Current-thread CPU is not
all-thread process CPU. Historical inference wall times can be exported, but
missing historical CPU measurements remain unavailable.

`process-components.json` maps process IDs plus creation times to ownership, so
resource samples can be attributed even during worker startup. `components.csv`
contains sampled CPU seconds and memory peaks per physical process/container.
`process-footprint.png/.svg` displays process costs; `iot-agent-footprint.png/.svg`
displays individual FIWARE IoT Agent container costs. The expandable physical
breakdown can filter any FIWARE service. This distinguishes RL agents from IoT
Agents and keeps infrastructure overhead visible.

The central process also orchestrates SUMO and may run the coordinator; its entire
CPU/RAM is not labelled pure policy inference. Shared monolithic RAM is never
divided by eleven as if each logical agent had a private allocation. Unlabelled
historical processes are not guessed to be TAZ workers. Sampling may miss short
processes; one-sample CPU totals remain unavailable. No-agent runs have no policy
work. Per-agent differences must be measured, not assumed from placement; report
both work distribution and whole-system totals.
