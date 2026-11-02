"""Compare summaries only when workload and policy choices match."""
import json
from pathlib import Path
import sys
summaries=[json.loads((Path(p)/'summary.json').read_text()) for p in sys.argv[1:]]
if len(summaries)<2:
    raise SystemExit('Usage: python tools/compare_runs.py RUN_A RUN_B [RUN_C...]')
for key in ['demo','profile','control','routes','seed','steps','step_seconds','end','hour','vehicle_count','demand_noise','delay_ms','replica']:
    if any(s['config'][key]!=summaries[0]['config'][key] for s in summaries):
        raise SystemExit(f'Incomparable configuration field: {key}')
if any(s.get('routes_sha256') != summaries[0].get('routes_sha256') for s in summaries):
    raise SystemExit('Route contents differ')
print('| Mode | Startup ms | Decision p95 ms | Cycle p95 ms |')
print('|---|---:|---:|---:|')
for s in summaries:
    print(f"| {s['config']['mode']} | {s['startup_ms']:.2f} | {s['latency_ms']['decision_barrier_ms']['p95']:.2f} | {s['latency_ms']['cycle_ms']['p95']:.2f} |")
