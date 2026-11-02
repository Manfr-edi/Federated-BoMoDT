"""Human-readable measurement boundaries; overlapping intervals are not additive."""
PATHS=('observation_to_grafana_query_ms','persisted_observation_to_actuation_ms','observation_to_actuation_ms')
GROUPS=('Data paths','Transmission','Control and feedback','Physics and full cycles','All metrics')
DEFINITIONS={
 'observation_to_grafana_query_ms':('Snapshot → Grafana query','Data paths','Snapshot creation to an instrumented Grafana datasource response containing the matching persisted observation. Includes query polling; not browser rendering.'),
 'persisted_observation_to_actuation_ms':('Persistence confirmed → SUMO action','Data paths','First successful Timescale readback of this observation to batch TLS program/state readback after policy decisions. Includes coordinator, local inference, commands and batch waiting; no-op actions are included.'),
 'observation_to_actuation_ms':('Snapshot → SUMO action','Data paths','Snapshot creation to TLS batch readback after applying decisions, including ingestion, persistence confirmation and control processing.'),
 'iot_accept_ms':('Sensor HTTP acceptance','Transmission','Before sensor POST until IoT Agent HTTP response. Does not prove historical persistence.'),
 'schedule_lateness_ms':('Scheduled-send delay','Transmission','Actual start of sensor transmission minus scheduled start, clamped at zero. Includes producer backlog and configured delay, not HTTP response time.'),
 'hour_persistence_verify_ms':('Hourly persistence verification','Transmission','After the last source-hour send returns until all expected hourly records are verified in Timescale, including polling and repairs.'),
 'observation_history_barrier_ms':('Observation persistence barrier','Control and feedback','Verification of this cycle’s observations in Timescale before policy evaluation, including polling and repairs. Not an exact DB commit latency.'),
 'control_history_barrier_ms':('Cycle persistence verification','Control and feedback','Verification of all current-sequence events in Timescale, including queries, polling and repairs.'),
 'coordinator_ms':('Coordinator computation','Control and feedback','Computation of global context/prices. Excludes context publication. Without a coordinator this is bookkeeping time.'),
 'local_inference_ms':('Local policy inference','Control and feedback','Local policy computation. Excludes broker input reads and command publication.'),
 'decision_barrier_ms':('Decision barrier','Control and feedback','Cycle start until all local workers return decisions. Includes observation transport/persistence, coordination and worker processing; excludes the actuator’s final command reads.'),
 'command_to_readback_ms':('Command → TLS readback','Control and feedback','Command creation in the local worker to authoritative batch TLS program/state readback. Includes waiting for other TAZ commands.'),
 'actuator_readback_ms':('Individual TLS update/readback','Control and feedback','One TLS program modification and its immediate TraCI readback. It does not measure the next physical green transition.'),
 'control_closed_loop_ms':('Control loop without physics','Control and feedback','Before observation publication to verified feedback/history, before advancing SUMO. Includes persistence and compute; overlaps component metrics.'),
 'control_to_next_observation_ms':('Control → next observation','Physics and full cycles','Cycle start through control transport, physics, next-state collection/reward processing and local feedback bookkeeping.'),
 'physics_advance_ms':('SUMO physics advance','Physics and full cycles','Wall time for the physics advance, excluding FIWARE barriers.'),
 'simulation_ms':('Simulation step','Physics and full cycles','In active FIWARE runs this is the same physics interval as physics_advance_ms. Legacy offline runs also include environment processing.'),
 'cycle_ms':('Full control cycle','Physics and full cycles','Full wall time of one RL cycle, including physics, control and optional replica catch-up. Do not add it to its component intervals.'),
 'telemetry_cycle_ms':('Passive telemetry cycle','Control and feedback','No-agent observation/TLS-state publication and historical verification before physics advance. Contains no policy action.'),
}


def metric_info(name):
    if name in DEFINITIONS:
        title,group,meaning=DEFINITIONS[name]
    elif name.endswith('_iot_accept_ms'):
        stage=name.removesuffix('_iot_accept_ms')
        title,group,meaning=f'{stage.title()} HTTP acceptance','Transmission',f'Before {stage} publication until IoT Agent HTTP response; excludes confirmed persistence.'
    elif name.endswith('_publish_to_read_ms'):
        stage=name.removesuffix('_publish_to_read_ms')
        title,group,meaning=f'{stage.title()} publish → broker read','Transmission',f'Before {stage} POST until the world consumer reads the matching sequence/payload from Orion. Includes HTTP, processing and polling.'
    elif name.endswith('_broker_read_ms'):
        prefix=name.removesuffix('_broker_read_ms');parts=prefix.split('_',1)
        reader,stage=parts if parts[0] in ('world','local') else ('consumer',prefix)
        title,group,meaning=f'{reader.title()} reads {stage}','Transmission',f'{reader.title()} consumer GET/poll duration for the matching {stage} event; starts at the read, not at publication.'
    else:
        title,group,meaning=name,'Other','No boundary definition is registered for this legacy/custom metric.'
    return dict(metric=name,label=title,group=group,meaning=meaning,unit='wall-clock ms')
