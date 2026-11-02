# Source analysis and design decisions

Inspected local working trees on 2026-10-29:

- BoMoDT HEAD: `0fe4b6ad22ba0fb253210d55305a6ea19bdee557`
- GreenWave_HRL HEAD: `8d77b6e39a6df5ca88d17440a11770c32b3032bb`

Working-tree files were used; revision identifiers alone do not imply clean
upstream checkouts. Exported checkpoint hashes are in `configs/models.json`.
Source code, databases and networks in the original projects remain unchanged.

## BoMoDT component roles

`mobilityvenv/MobilityVirtualEnvironment.py` sets up detector devices and replays
hourly traffic counts from Bologna data. `PhysicalSystemConnector.py` represents
roads and attached sensors. The original emulator sleeps between source hours;
source time and wall-clock pacing are separate concepts.

`main.py` builds the broker/IoT Agent connection, QuantumLeap subscriptions,
Timescale manager and planner/digital-twin manager. `libraries/classes` separates
registration, subscription, data management, shadows, planning, traffic modeling
and SUMO execution. `fiwareenv` deploys Orion-LD, MongoDB, IoT Agent JSON,
QuantumLeap, Timescale/PostGIS, Redis and Grafana. `udtBackEnd` supplies Django
entity/monitoring/modeling/simulation views.

Duplicating the original directory per TAZ would collide on container names,
ports, database paths and file cleanup. For example, DigitalShadowManager clears
its shared shadow directory during initialization. The new deployment generates
per-stack service names, networks, database volumes and Grafana configurations;
new per-run/per-TAZ outputs do not invoke that cleanup routine.

## Data ingestion adaptation

The normalized 2025/2026 flow CSVs are copied from GreenWave_HRL as emulator inputs.
They retain original sensor IDs, hours, TAZ labels and SUMO edge assignments. The
new emulator provisions JSON IoT devices and source-timestamped measurements;
broker notifications feed QuantumLeap, whose Timescale history is read back before
route fitting. This preserves the original emulator → ingestion → historical-data
→ scenario chain rather than treating FIWARE as a display-only side service.

The monolith uses one full stack. Federation uses the same seven component roles
for each of eleven trained TAZs, with physically independent storage. The two
English Streamlit applications replace Django as the requested control plane.
Orion-LD remains the broker, with an explicit NGSI-v2 compatibility wire contract
for IoT Agent/QuantumLeap. Live interoperability requires validation with the
configured image versions. See docs/fiware.md.

## GreenWave controller contract

The vendored stack retains `SumoSimulator.py`, constants and the RL environment,
policy, coordination and settings modules. The environment layers are:

1. LocalTazTrafficEnv: TLS/TAZ features, rewards and bounded phase-duration changes.
2. GroupedTazTrafficEnv: topological TLS groups, local observation rows, padded
   action heads, runtime masks and axis-aware group-member selection.
3. CoordinatedTazTrafficEnv: vehicle transitions and inter-TAZ flow tracking.

The local policy is shared across TAZ observation rows, rather than eleven
independently trained weight sets. Federated workers own copies of that policy.
The monolithic runtime shares one in-memory instance across zones. Both retain
304 local input features and six padded action slots; selecting only one TAZ at
environment construction would change padding and invalidate naive checkpoint
reuse. Runtime TAZ/TLS order, groups and dimensions are checked explicitly.

The coordinator predicts one price per trained zone. PriceAdapterActorCritic
consumes own/neighbor/difference price features. Three requested profiles use
legacy adapters; weekday 8/17 uses zero-anchored v2. Adapter scale and environment
clipping differ across checkpoints and come from saved metadata. Current upstream
settings alone cannot reproduce all saved trainings. The saved reward combines
CO2 and NOx, not CO2 alone. Training remains in GreenWave_HRL.

## Common map and simulation boundary

Original BoMoDT network SHA-256:
`9c62392d5282b1dae48220827b16f45efdffe7cc0c8c1d188625202b59125371`

GreenWave network SHA-256, used by both new deployment modes:
`7227c51be1423d9f858256a020290f8887b4d32fafb1e7e34f3b79321d137964`

The new monolithic dashboard is a unified adaptation using the GreenWave map,
not a bit-for-bit execution of the old BoMoDT CLI. This makes route/policy matching
possible. The geographic file includes eighteen TAZ polygons; available detector
data and learned controllers cover eleven.

Shared-physics execution isolates service/inference placement. Optional local
simulations replicate the full-city network and replay the global action barrier.
They provide process independence at full-city computation/storage cost per TAZ.
Territorial network partitioning would require edge ownership, boundary routes,
vehicle handoff, synchronized clocks and an equivalent observation contract; it
is not implemented. Runtime RL transport is currently single-host multiprocessing,
not a network protocol for multi-host deployment.
