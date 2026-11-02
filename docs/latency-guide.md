# Latency metric guide

All values are real wall-clock milliseconds, not simulated seconds. Start/end
boundaries differ and often overlap. Do not add stage percentiles or full-cycle
values together. World is the authoritative simulation/global controller; local
is a TAZ inference worker.

## Two primary data paths

- **Snapshot → Grafana query** starts when the simulated observation snapshot is
  exported and ends when an instrumented Grafana datasource query returns its
  matching historical event and payload. This is query availability, not browser
  refresh, network delivery to a remote browser, or a rendered frame. The probe
  runs in parallel, polls at 250 ms and stops 15 seconds after snapshot creation.
  Its resource overhead is included and must match between architectures.
- **Persistence confirmed → SUMO action** starts at the first successful historical
  readback of that observation and ends at the authoritative TLS batch readback.
  It includes coordinator/local evaluation, command transport and waiting for the
  other TAZs. It starts at observed confirmation, not the exact DB commit instant.

The third optional interval, Snapshot → SUMO action, includes both ingestion and
control. A no-op policy command is still a processed decision; readback does not
imply that a phase actually changed. No-agent runs have no policy-action interval.

There are seven snapshots per TAZ (including the terminal snapshot), but only six
control decisions. Initial samples can include runtime/provisioning delays before
first publication. Samples within one run/TAZ cohort are correlated; repeat matched
runs before interpreting tail percentiles as architecture evidence.

New timestamps cannot be reconstructed from old results. Missing path metrics
are unavailable, never zero. Protocol version 2 verifies observations in Timescale
before policy evaluation and adds the Grafana observer. Compare equal versions.

## Detailed intervals

| Identifier | Display label | Meaning |
|---|---|---|
| `actuator_readback_ms` | Individual TLS update/readback | One TLS program modification and its immediate TraCI readback. It does not measure the next physical green transition. |
| `command_to_readback_ms` | Command → TLS readback | Command creation in the local worker to authoritative batch TLS program/state readback. Includes waiting for other TAZ commands. |
| `control_closed_loop_ms` | Control loop without physics | Before observation publication to verified feedback/history, before advancing SUMO. Includes persistence and compute; overlaps component metrics. |
| `control_history_barrier_ms` | Cycle persistence verification | Verification of all current-sequence events in Timescale, including queries, polling and repairs. |
| `coordinator_ms` | Coordinator computation | Computation of global context/prices. Excludes context publication. Without a coordinator this is bookkeeping time. |
| `decision_barrier_ms` | Decision barrier | Cycle start until all local workers return decisions. Includes observation transport/persistence, coordination and worker processing; excludes the actuator’s final command reads. |
| `local_inference_ms` | Local policy inference | Local policy computation. Excludes broker input reads and command publication. |
| `observation_history_barrier_ms` | Observation persistence barrier | Verification of this cycle’s observations in Timescale before policy evaluation, including polling and repairs. Not an exact DB commit latency. |
| `telemetry_cycle_ms` | Passive telemetry cycle | No-agent observation/TLS-state publication and historical verification before physics advance. Contains no policy action. |
| `observation_to_actuation_ms` | Snapshot → SUMO action | Snapshot creation to TLS batch readback after applying decisions, including ingestion, persistence confirmation and control processing. |
| `observation_to_grafana_query_ms` | Snapshot → Grafana query | Snapshot creation to an instrumented Grafana datasource response containing the matching persisted observation. Includes query polling; not browser rendering. |
| `persisted_observation_to_actuation_ms` | Persistence confirmed → SUMO action | First successful Timescale readback of this observation to batch TLS program/state readback after policy decisions. Includes coordinator, local inference, commands and batch waiting; no-op actions are included. |
| `control_to_next_observation_ms` | Control → next observation | Cycle start through control transport, physics, next-state collection/reward processing and local feedback bookkeeping. |
| `cycle_ms` | Full control cycle | Full wall time of one RL cycle, including physics, control and optional replica catch-up. Do not add it to its component intervals. |
| `physics_advance_ms` | SUMO physics advance | Wall time for the physics advance, excluding FIWARE barriers. |
| `simulation_ms` | Simulation step | In active FIWARE runs this is the same physics interval as physics_advance_ms. Legacy offline runs also include environment processing. |
| `command_iot_accept_ms` | Command HTTP acceptance | Before command publication until IoT Agent HTTP response; excludes confirmed persistence. |
| `coordination_iot_accept_ms` | Coordination HTTP acceptance | Before coordination publication until IoT Agent HTTP response; excludes confirmed persistence. |
| `coordination_publish_to_read_ms` | Coordination publish → broker read | Before coordination POST until the world consumer reads the matching sequence/payload from Orion. Includes HTTP, processing and polling. |
| `feedback_iot_accept_ms` | Feedback HTTP acceptance | Before feedback publication until IoT Agent HTTP response; excludes confirmed persistence. |
| `feedback_publish_to_read_ms` | Feedback publish → broker read | Before feedback POST until the world consumer reads the matching sequence/payload from Orion. Includes HTTP, processing and polling. |
| `hour_persistence_verify_ms` | Hourly persistence verification | After the last source-hour send returns until all expected hourly records are verified in Timescale, including polling and repairs. |
| `iot_accept_ms` | Sensor HTTP acceptance | Before sensor POST until IoT Agent HTTP response. Does not prove historical persistence. |
| `local_coordination_broker_read_ms` | Local reads coordination | Local consumer GET/poll duration for the matching coordination event; starts at the read, not at publication. |
| `local_observation_broker_read_ms` | Local reads observation | Local consumer GET/poll duration for the matching observation event; starts at the read, not at publication. |
| `observation_iot_accept_ms` | Observation HTTP acceptance | Before observation publication until IoT Agent HTTP response; excludes confirmed persistence. |
| `observation_publish_to_read_ms` | Observation publish → broker read | Before observation POST until the world consumer reads the matching sequence/payload from Orion. Includes HTTP, processing and polling. |
| `schedule_lateness_ms` | Scheduled-send delay | Actual start of sensor transmission minus scheduled start, clamped at zero. Includes producer backlog and configured delay, not HTTP response time. |
| `world_command_broker_read_ms` | World reads command | World consumer GET/poll duration for the matching command event; starts at the read, not at publication. |
| `world_coordination_broker_read_ms` | World reads coordination | World consumer GET/poll duration for the matching coordination event; starts at the read, not at publication. |
| `world_feedback_broker_read_ms` | World reads feedback | World consumer GET/poll duration for the matching feedback event; starts at the read, not at publication. |
| `world_observation_broker_read_ms` | World reads observation | World consumer GET/poll duration for the matching observation event; starts at the read, not at publication. |

## Plots and exports

Footprint & latency defaults to completed dashboard simulations with active
FIWARE. Select Data emulation for replay metrics, or expand Other recorded
operations to include CLI/validation, failed or offline runs. Each operation is
listed once; metrics/summary.json is its export, not another run.

The main data-path graphs show p50 and p95 in milliseconds for each selected run.
Latency detail groups the remaining measurements by purpose. The expandable
metric guide explains every selected field. ZIPs contain latency-guide.csv,
data-path-latency.png/.svg, the complete latency CSV and raw timing traces.
Grafana observer failures are retained and warned about; missing responses must
not be interpreted as successful fast visualizations.

The source here is the live SUMO observation snapshot. Initial hourly CSV replay
has separate IoT acceptance, schedule lateness and hourly persistence metrics;
its historical results do not gain these new simulation-path timings.
