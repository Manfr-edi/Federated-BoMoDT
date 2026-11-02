# Timed traffic emulation

The processed datasets contain hourly detector counts, not vehicle arrival times.
The emulator reconstructs a deterministic synthetic sub-hour profile constrained
by each observed total. For an hourly count N it generates positive smooth/random
weights, draws N interval assignments, and counts them. Therefore interval counts
are nonnegative integers and sum exactly to N; zero-count hours remain zero.
Seed, detector identity and source hour determine the profile, independently of
monolithic/federated placement. This permits matched traffic in comparisons.

A one-minute stream sends 60 reports per detector per hour, including zero-count
intervals. Each report contains flowIncrement, cumulative trafficFlow, observedAt,
hourStart, hourSampleId and hourComplete. The last event is placed just inside the
hour boundary. Canonical history reads exclude unfinished hours and map the final
report back to the source hour/sample identity. Retry delivery IDs change while
source identities and counts remain stable. A missed final report is retried as
the same final interval, not as an additional set of passages.

The global pacing clock begins after provisioning. Due time equals source elapsed
seconds / speed; processing late events does not shift that original clock. This
makes overload visible as schedule_lateness_ms instead of silently slowing the
source clock. IoT HTTP latency excludes the planned waiting time; the persistence
verification metric starts after the last interval of each hour. Configured
transmission delay is part of send lateness. All timestamps use one host's
monotonic clock for wall latency and explicit UTC source times for historical data.

Start date/hour and duration are independent of every RL training profile. The
reader supports contiguous whole hours across midnight, requires all requested
source dates to exist and rejects ambiguous/nonexistent DST source hours. The
source's hourly resolution is why arbitrary minute-aligned starts are not
silently treated as fully observed hours. Training-hour checks apply only when
actually deploying a learned policy, not when replaying sensors or running a
no-agent scenario.

Trace collection appends JSONL rather than rewriting a growing JSON array.
Resource capture covers the operation and its subprocesses plus the originating
Streamlit server when launched from a dashboard. FIWARE container stats are a
separate scope. Export rendering is outside the capture interval. Reports retain
source/configuration metadata, so users can distinguish pacing, inference,
simulation advance, command readback and persistence verification latencies.
