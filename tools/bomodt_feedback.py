"""Opt-in command/readback measurement for an existing BoMoDT libtraci process.

Call install(libtraci, 'feedback.jsonl') before starting its feedback controller.
This measures existing commands; it does not introduce a controller.
"""
import json
import time


def install(traci_module, output):
    tls, originals = traci_module.trafficlight, {}
    for name in ['setProgramLogic','setPhaseDuration']:
        original = getattr(tls,name)
        originals[name] = original
        def measured(tls_id, value, _original=original, _name=name):
            start = time.perf_counter_ns()
            result = _original(tls_id,value)
            program = tls.getProgram(tls_id)
            logic = next(p for p in tls.getAllProgramLogics(tls_id) if p.programID==program)
            record = dict(tls=tls_id,command=_name,command_readback_ms=(time.perf_counter_ns()-start)/1e6,
                simulation_seconds=traci_module.simulation.getTime(),phase=tls.getPhase(tls_id),
                next_switch=tls.getNextSwitch(tls_id),phase_durations=[p.duration for p in logic.phases])
            with open(output,'a') as handle:
                handle.write(json.dumps(record)+'\n')
            return result
        setattr(tls,name,measured)
    def restore():
        for name,original in originals.items():
            setattr(tls,name,original)
    return restore
