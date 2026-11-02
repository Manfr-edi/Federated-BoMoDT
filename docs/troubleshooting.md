# Deployment troubleshooting

## ENTITY_GENERIC_ERROR when sending a detector measurement

This is an IoT Agent wrapper around a broker operation failure, not the broker's
detailed reason. Use the TAZ/device context in the exception to select the right
container logs. For example, `loop_97cb3720a353e2f7152d` in the supplied 2025 data
belongs to TAZ B and has road name `VIA DE' CARRACCI`.

Live probes against the configured Orion-LD 1.5.1 image reject the original
apostrophe with both Text and TextUnrestricted, including the unrestricted
metadata variant. The initial TextUnrestricted-only fix was therefore insufficient
for this image's NGSI-v2 compatibility path. The current wire field is roadNameHex,
encoded as UTF-8 hex with ordinary Text type. Python readers and Grafana decode
the original street name. Sensor identifiers, numeric measurements and source
timestamps remain unchanged.

Launch a **new city emulation** after updating the Python code. The fresh replay
tenant provisions the corrected wire attributes and subscription. Existing failed
replays remain unchanged. Generated Grafana provisioning files are updated by the
generator; an existing Grafana instance reloads the changed dashboard file.

The fix has been verified end-to-end on all eleven running stacks: 156 measures
for hours 8 and 17 are read back from Timescale, and a real Grafana datasource
query returns `VIA DE' CARRACCI`. The emulation additionally verifies each hour
and repairs missing notifications through the same IoT Agent path with stable
sample identities and fresh delivery identities.

If another ENTITY_GENERIC_ERROR occurs, inspect the affected TAZ's broker error:

```bash
docker compose -p bomodt-federated -f deployments/federated/compose.json \
  logs --tail 150 b-iot b-orion
```

## HTTP 400: Content-Type is forbidden on GET/DELETE

This was a client bug: the shared HTTP helper attached JSON Content-Type to
every request, including broker reads and readiness probes without a body.
The helper now sends Accept for JSON responses, adds Content-Type only when
serializing a request body, and rejects GET/DELETE bodies before transport.
Caller-provided Content-Type is also removed from bodyless requests regardless
of header capitalization. Tenant headers remain unchanged.

Restart the Streamlit processes after updating the Python client, then repeat
the health check or broker read. This fix does not require container recreation.
Regression tests inspect outgoing urllib Request objects, exercise health/entity
reads against a strict test transport, and check that JSON writes remain valid.

## Orion-LD containers exit; the dashboard reports connection refused

A connection refusal identifies a service that is not listening; it does not by
itself identify why the broker exited. Inspect stopped containers too, using the
Infrastructure tab's **Show container status and selected TAZ logs** button.

A concrete configuration bug was fixed in the deployment generator:
`quay.io/fiware/orion-ld:1.5.1` supplies `-fg`, `-multiservice` and
`-ngsiv1Autocast` in its image entrypoint. The old generated Compose command added
`-multiservice` again. That version's argument parser reports repeated boolean
options as `multiple use of ...`; startup can exit before binding the HTTP port.
The corrected command supplies only the Mongo host and log level.

Source evidence for the pinned release:

- [Orion-LD 1.5.1 Dockerfile](https://github.com/FIWARE/context.Orion-LD/blob/1.5.1/docker/Dockerfile-ubi)
- [Argument duplicate detection](https://github.com/FIWARE/context.Orion-LD/blob/1.5.1/src/lib/parseArgs/paOptionsParse.cpp)

The image entrypoint must remain intact. Do not add its existing flags to the
Compose command. An HTTP health check now gates IoT Agent startup, and the
container listing includes exited containers. The CLI regenerates Compose on
`stack up`, so an existing generated file cannot retain the old duplicate flag.

After updating the code, run from the repository root in the project environment:

```bash
# Use the deployment you actually started; repeat for the other mode if needed.
python -m federation.fiware stack up --mode federated
python -m federation.fiware stack ps --mode federated
```

For the monolith use `--mode monolithic`. Docker Compose `up` recreates services
whose configuration changed, retaining named database volumes. A plain container
restart does not update its old startup arguments. No volume deletion or database
reset is needed for this fix.

To inspect the first federated broker and its Mongo dependency:

```bash
docker compose -p bomodt-federated -f deployments/federated/compose.json \
  logs --tail 100 f-orion f-mongo
```

For the monolith:

```bash
docker compose -p bomodt-monolithic -f deployments/monolithic/compose.json \
  logs --tail 100 city-orion city-mongo
```

If a broker still exits after recreation, retain the exact log and exit reason.
Mongo connectivity, image/platform errors, PID-file errors and memory exhaustion
require different fixes; connection refusal alone does not establish any of them.
Runtime Docker access is now available. Docker's version check establishes
broker liveness; the client additionally checks database-backed subscriptions,
and the live replay verifies the entire ingestion path.

## Subscription timeout while the broker is healthy

A real October 30 failure affected only the federated TAZ B broker: `/version`
responded, but `/v2/subscriptions` and entity queries timed out. MongoDB responded
normally. A debugger trace located the blocked request in the legacy Mongo
connection-pool semaphore. Restarting the broker and MongoDB did not resolve it;
recreating the broker with `-dbPoolSize 64` restored database requests and a full
78-measurement replay passed. No database volumes were removed.

Compose now explicitly uses this pool capacity in both architectures. This is a
workaround for pool starvation in the pinned Orion-LD 1.5.1, not a patch to its
legacy driver or proof of unlimited tenant capacity. If it recurs after many
replays, preserve the logs and review pool usage and tenant accumulation rather
than repeatedly increasing HTTP timeouts. Pool size affects resource consumption
and must remain consistent between comparison runs.

Client readiness now also queries subscriptions using the replay tenant, because
Docker's `/version` check establishes liveness only. Transport timeouts name the
HTTP operation and endpoint, and provisioning failures identify the TAZ/owner.
An ambiguous timed-out subscription POST is not automatically repeated: Orion
may already have committed it, so blindly retrying could duplicate notifications.
Use **Start services**, or `python -m federation.fiware stack up --mode federated`,
to apply the generated configuration. Start a new emulation; failed manifests
remain available for diagnosis and are never presented as complete scenarios.

## `FatalTraCIError: Not connected` at the final `simulation.getTime()`

The upstream RL environment closes SUMO/TraCI inside its terminal step to flush
output files before terminal reward parsing. A subsequent live clock query from
the runtime summary therefore fails even when all six cycles and FIWARE barriers
have completed. This is independent of where the saved scenario was prepared.

The simulator adapter now records actual SUMO time immediately after every
physics advance, while TraCI is connected. The final summary and horizon check
use that measured value after the environment closes the connection. No requested
horizon or decision-count estimate substitutes for an observed timestamp. Reset
records a new initial time. Existing scenarios remain valid; start a new run to
obtain a successful summary rather than modifying the failed artifact.
