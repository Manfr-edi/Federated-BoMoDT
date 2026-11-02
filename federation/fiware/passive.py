"""FIWARE telemetry baseline using normal SUMO TLS programs, without Torch/RL."""
import json
from pathlib import Path
import time
from ..models import VENDOR
from .control import ControlBus
from .deployment import TAZS


def run_passive(command,spec):
    import libtraci as traci
    bus=None
    start=time.perf_counter_ns()
    traci.start(command)
    try:
        bus=ControlBus(spec,TAZS)
        mapping=json.loads((VENDOR/'data/realworlddata/taz_to_tls.json').read_text())
        known=set(traci.trafficlight.getIDList());edges=set(traci.edge.getIDList())
        tls={t:[v for v in mapping[t] if v in known] for t in TAZS}
        approaches={t:sorted({lane.rsplit('_',1)[0] for v in tls[t] for lane in traci.trafficlight.getControlledLanes(v)
            if lane.rsplit('_',1)[0] in edges and not lane.startswith(':')}) for t in TAZS}
        def observations(terminal=False):
            rows=[dict(terminal=terminal,scope='TLS approach-edge snapshots',
                edge_count=len(approaches[t]),vehicle_count=sum(traci.edge.getLastStepVehicleNumber(e) for e in approaches[t]),
                halting_count=sum(traci.edge.getLastStepHaltingNumber(e) for e in approaches[t]),
                co2_mg_per_s=sum(traci.edge.getCO2Emission(e) for e in approaches[t]),
                nox_mg_per_s=sum(traci.edge.getNOxEmission(e) for e in approaches[t])) for t in TAZS]
            generated=time.perf_counter_ns()
            return [dict(row,generated_ns=generated) for row in rows]
        for seq in range(6):
            cycle=time.perf_counter_ns()
            bus.exchange('observation',seq,observations())
            bus.exchange('feedback',seq,[dict(status='normal-sumo-program',tls_readback=[dict(tls=v,
                phase=traci.trafficlight.getPhase(v),state=traci.trafficlight.getRedYellowGreenState(v)) for v in tls[t]]) for t in TAZS])
            bus.verify_history(seq)
            bus.nodes[0].log(metric='telemetry_cycle_ms',value_ms=(time.perf_counter_ns()-cycle)/1e6,seq=seq)
            physics=time.perf_counter_ns()
            traci.simulationStep((seq+1)*600)
            bus.nodes[0].log(metric='physics_advance_ms',value_ms=(time.perf_counter_ns()-physics)/1e6,seq=seq)
        bus.exchange('observation',6,observations(terminal=True));bus.verify_history(6)
        return dict(completed_cycles=6,simulated_end_seconds=traci.simulation.getTime(),
                    wall_seconds=(time.perf_counter_ns()-start)/1e9)
    finally:
        if bus: bus.close()
        traci.close()
