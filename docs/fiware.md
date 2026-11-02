# FIWARE integration contract

## Wire protocol

The emulator provisions `/iot/services` and `/iot/devices` on the JSON IoT Agent's
northbound port. It sends `trafficFlow`, `observedAt`, `sampleId` and `timeSlot`
through southbound `/iot/json?i=<device>&k=<key>`. Static attributes bind the sensor
to its source ID, SUMO edge, TAZ, road and coordinates. It does not POST entity
measurements directly to Orion or insert measurement rows into PostgreSQL.
The pinned Orion-LD 1.5.1 NGSI-v2 path rejects apostrophes even in TextUnrestricted.
Descriptive street names are therefore transported as `roadNameHex` (UTF-8 hex,
ordinary Text). Python readers decode it as road_name/roadName and Grafana uses
PostgreSQL decode/convert_from. The source spelling is preserved exactly; counts,
identifiers and timestamps are never encoded. The actual Carracci device and
real Grafana datasource query have been validated against the running images.

Orion-LD is configured as the broker; the ingestion path deliberately uses its
NGSI-v2 compatibility API, with multiservice mode enabled for replay isolation. A `/v2/subscriptions` registration forwards full entity
representations to QuantumLeap `/v2/notify`. `httpCustom` passes the replay tenant,
service path and `Fiware-TimeIndex-Attribute: observedAt`. Historical source time
is therefore distinct from HTTP arrival time. Automatic IoT timestamps are
disabled so they cannot replace the original measurement timestamp.

References: [FIWARE JSON IoT Agent tutorial](https://fiware-tutorials.readthedocs.io/en/latest/iot-agent-json.html),
[QuantumLeap subscriptions and time indexing](https://quantumleap.readthedocs.io/en/latest/user/using/).

## Persistence and verification

QuantumLeap uses Timescale/PostGIS with `QL_DEFAULT_DB=timescale`. The generated
PostgreSQL configuration creates `quantumleap` and enables its extensions on a
new volume. The tenant is `gw_<time>_<random>`, and the expected QuantumLeap table
is `mt<tenant>.ettrafficflowobserved`. The SQL reader uses quoted identifiers,
bound time parameters and the original `observedat` column. Required sample
identity/count fields are checked; absent tables or missing rows cannot produce
a verified replay. Duplicate identical samples are deduplicated by sample ID;
conflicting duplicates fail validation.

The source manifest is an integrity expectation, not the source of scenario
counts. `build_scenario` calls the historical reader again and builds edge counts
from the returned SQL rows. It rejects non-verified sessions and changed maps.
The default code path has no CSV fallback if FIWARE is down.

Direct Timescale queries mirror the original BoMoDT DataManager design. This is
also necessary to avoid assuming backend-independent historical-query support:
see [QuantumLeap Timescale setup](https://quantumleap.readthedocs.io/en/stable/admin/timescale/)
and [backend configuration](https://quantumleap.readthedocs.io/en/stable/admin/configuration/).
Live interoperability and the physical SQL schema have now been checked in the
running federated deployment, including the opt-in integration test. Re-run it
after changing images before collecting results.

## Federation and dashboard ownership

The federation has eleven networks, Mongo stores, IoT registries, brokers,
QuantumLeap instances, Redis instances, PostgreSQL volumes and Grafana instances.
Service names are prefixed by TAZ. The monolith uses one of each for all zones.
The same replay tenant name may exist in different independent federated stores;
this does not create a shared database. The supplied data covers the eleven
trained TAZs, not every polygon in the geographic map.

A selected TAZ chooses its own Grafana endpoint in the federated control room.
Grafana receives the replay's historical time range and tenant as URL variables;
its provisioned PostgreSQL datasource reads QuantumLeap's persisted measurements.
The monolithic Grafana has all measurements and can filter by TAZ. Streamlit is
the control plane; long-running emulation, fitting and simulation jobs execute
outside its rerun lifecycle. The two entry points fix their mode, project names,
job namespaces and ports. [Streamlit fragments](https://docs.streamlit.io/develop/concepts/architecture/fragments)
refresh the job panel without rerunning operations.

## Version and runtime validation

Image references are explicit configuration, not proof of interoperability. The
Timescale, Mongo and Redis defaults include moving tags inherited from the source
project. Resolve and pin image digests for experiments. Existing PostgreSQL volumes
will not rerun initialization SQL. No destructive reset is performed automatically.
All exposed service ports bind to localhost; the included credentials are for
local research. No external service deployment or public hosting is configured.

## Delivery acknowledgement and repair

The replay verifies one source hour before moving to the next. HTTP acceptance
is distinct from historical persistence: live testing observed missing broker
notifications even though measurement requests returned success. A missing-sample
exception exposes only the absent sample identities; the emulator can resend only
those observations through IoT Agent, up to three repair batches per hour.
`deliveryId` is fresh on each attempt and included in subscription conditions;
`sampleId`, observedAt and trafficFlow remain stable. Canonical history reads
deduplicate identical repeated samples and reject conflicting values. Grafana
queries use DISTINCT. Per-attempt and per-hour artifacts retain repair costs,
which must be included in ingestion resource/latency comparisons. A replay still
fails if the complete, correct persisted history cannot be verified by its deadline.
