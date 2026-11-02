# Readable monolithic/federated comparisons

In **Footprint & latency**, select **Simulations**, both architectures and exactly
one completed run per architecture. The primary **Presentation comparison** checks
scenario route/map hashes, controller/profile, source time, seed, integration and
protocol, GUI/replicas, dashboard inclusion and recorded horizon/cycle count.
A mismatch blocks the matched export: comparing a no-agent monolith with a
coordinated federation changes both architecture and control law.

Press **Build presentation report (PDF, plots and CSV)**, then download the report.
The ZIP contains:

- `comparison.pdf`: readable pages with both architectures on the same axes;
- `comparison-footprint.png/.svg`: CPU work, memory and elapsed time;
- `comparison-latency.png/.svg`, when measured: the two data paths and complete
  control-loop time, with median/p95 and real-millisecond labels;
- `comparison-policy-work.png/.svg`, when measured in both runs: CPU per logical
  TAZ decision, using the same work unit;
- `comparison.csv`: values, differences and federated/monolithic ratios;
- `coverage.csv`: measurements omitted because they were not recorded/applicable;
- `runs.json` and `READ-ME.txt`: source run identifiers, settings and definitions.

Individual-run diagnostics are collapsed by default. They remain available for
investigation, alongside raw traces/CSV. Export plots no longer contain empty
panels saying "CPU attribution requires a new agent run". Historical wall times
can be recovered, but historical CPU-per-decision values cannot be inferred from
wall time or divided from process totals. No-agent experiments have no policy CPU.

## CPU units and the corrected calculation

The old chart used the Linux/Docker convention **100% = one fully occupied logical
CPU**. Thus 3200% means approximately 32 occupied CPUs, not 3200% of the machine.
Charts now use **CPU cores used** for parallel activity and **CPU seconds** for
work over the run. No normalization using today's host CPU count is retroactively
applied to historical runs. Short sampled estimates are not exact scheduler traces.

An accounting edge case was also fixed: if a process missed a sample and then
reappeared, its CPU increment was previously divided by the most recent global
sampling interval. Each process now uses its own previous observation timestamp.
New captures timestamp individual process CPU reads. CPU seconds sum observed
counter increments directly, keeping the measured work across missed samples;
no counter pair means unavailable, not an invented zero.

## Memory and process labels

Main comparisons distinguish application USS (when available in both runs; RSS
otherwise) from Docker memory usage. They are not added into an alleged physical
memory total. CPU/application and FIWARE costs are likewise shown separately.

Process plots use semantic roles: **Central control runtime**, **Coordinator and
world runtime**, **TAZ B controller**, **SUMO physics**, **Streamlit dashboard**.
PID details stay in raw `components.csv`; plots use `roles.csv`. Peaks for a role
are computed from simultaneous process sums, not the sum of unrelated PID peaks.
Unlabelled historical processes remain an explicit runtime-support category;
there is no reliable way to reconstruct worker ownership that was never recorded.

The monolith performs all logical TAZ decisions in a shared process/policy. The
federation has independent worker processes. Compare logical CPU per decision to
hold work constant; use physical role and whole-system costs to see placement
and infrastructure overhead. Shared monolithic RAM is not artificially split
among TAZs. Central runtime cost also includes orchestration/coordinator duties.

## What the latency pages mean

**Snapshot → Grafana query response** ends at an instrumented datasource response
containing the matching historical observation, not a browser repaint.
**Persistence confirmation → SUMO action** starts at the first matching historical
readback and ends at TLS program/state readback after coordinator, local inference
and command delivery. It is not the exact DB commit time or a physical green-light
transition. Initial observations may include startup delays. The complete control
loop includes feedback/persistence but excludes physics advance. These intervals
overlap and must not be added together. See [the metric guide](latency-guide.md).

A single matched pair is descriptive. Repeat matched runs with the same logging,
observer, load and launch method before making performance claims. Missing legacy
fields cannot certify all execution conditions; metadata validation is not proof
of equal external load or statistically established superiority.
