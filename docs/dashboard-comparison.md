# Comparing the two dashboards

The dashboards share saved scenarios and experiment folders, but run independent
FIWARE deployments. Ports 8501 and 8502 can stay open together. For a measurement,
stop the unused deployment and run operations sequentially. Opening a dashboard
does not start its containers; closing a browser does not stop them.

## Test A: FIWARE distribution

1. Open the monolithic dashboard, press **Start services**, and wait for the green
   service indicator. Ensure the federated deployment is stopped.
2. In **City emulation**, select a dataset and period. For a first repeatable test,
   use January 1, 2025 at 08:00, one hour, timed reports, speed 60, interval 60
   seconds, delay 0 and seed 42. Record the settings and replay identifier.
3. Press **Start emulation** and wait for a verified replay. Stop monolithic
   services, start federated services, and repeat with exactly the same settings.
4. In **Footprint & latency**, choose **Measurements to evaluate → Data emulation**, select both architectures and these two emulation
   reports. Choose FIWARE resource scope for aggregate container CPU/memory, or
   application scope for the emulator/dashboard process tree. Inspect IoT HTTP
   acceptance, schedule lateness and hour persistence verification separately.
5. Download both ZIP files. Repeat paired runs, alternating the architecture
   order. Five pairs are a practical starting point, not a statistical guarantee.

## Test B: full FIWARE control pipeline and RL placement

1. In **SUMO scenarios**, prepare one scenario from a verified replay. For learned
   control select a matching profile; the example above matches
   `200_weekdays_8_17`. Note the saved scenario name.
2. In **Simulation** on the monolithic dashboard, select that scenario and
   **Local agents + coordinator**, keep GUI and offline mode off, and run it.
3. Stop monolithic services and start federated services. Select the exact same
   saved scenario and controller in the federated dashboard. Keep additional
   full-city replicas off and run it. Do not regenerate routes for this test.
4. Compare the two simulation reports in **Footprint & latency**. Application
   scope includes SUMO and the inference workers. Inspect local inference,
   decision barrier, observation/command/feedback transport, closed-loop and cycle
   latency distributions. FIWARE scope records the active services during the run.
5. Repeat with fixed settings and retain exports. An optional separate test can
   compare both architectures with local-only control. No-agent versus no-agent
   provides a SUMO plus FIWARE telemetry baseline, without distributed-policy commands.

Initial source ingestion and route preparation remain separate operations. During
simulation, observations and commands now cross the active FIWARE stack every
600 simulated seconds, with verified feedback before physics advances. See
[the loop and metric definitions](closed-loop.md). The advanced offline baseline
bypasses this path and must be measured separately.
Full-city replicas change the amount of simulated physics and must be treated as
a separate test. These dashboards use the same GreenWave map and the new monolith;
the original BoMoDT checkout is not launched by either dashboard.

The tab flags differences in available report settings, but this is not a proof
that two runs are experimentally equivalent. Match GUI/delay, replicas, source,
routes, controllers, launch method and logging, and distinguish cold starts from
steady state. Polling can miss short peaks. See [the full protocol](benchmark.md)
for experimental controls and measurement limits.

## Service indicator and scenario cleanup

The top indicator checks Docker state every 15 seconds: green means all expected
containers are running and none of their health checks report starting/unhealthy;
yellow means partial/startup state; red means stopped; white means unavailable
status. It is a container-status indicator, not proof of successful data ingestion.
Emulation separately probes database-backed subscriptions and verifies history.

Under **SUMO scenarios → Manage saved scenarios**, select the deletion checkbox
and press **Delete all saved scenarios**. This permanently deletes generated
scenario directories shared by both dashboards, including failed preparations.
It keeps FIWARE replay data, experiment exports and model weights. It refuses to
run while either dashboard has an active operation. Do not run standalone CLI
preparation or simulations concurrently with cleanup. Existing reports remain
readable, but their deleted scenarios must be prepared again before rerunning.

## Choosing measurements and reading latency

The default comparison contains only completed dashboard simulations with active
FIWARE, newest first. Use Other recorded operations for CLI/validation, failed
or offline runs. Data emulation is a separate measurement category. Generated
metrics/summary.json files never become additional selectable operations.

The two primary latency graphs and their guide are described in
[the latency metric guide](latency-guide.md). New path timings require new runs
and matching protocol versions; Grafana query availability is distinct from
browser rendering.
