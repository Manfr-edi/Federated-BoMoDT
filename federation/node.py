"""TAZ service: transmission emulation, local shadow, inference, optional SUMO replica."""
import time
import traceback
from pathlib import Path
from .storage import ShadowStore
from .dashboard import serve


class Twin:
    def __init__(self, spec, shared_policy=None):
        self.spec = spec
        self.store = ShadowStore(Path(spec['output']) / spec['taz'] / 'shadow.sqlite', spec['taz'])
        self.server = serve(self.store.path, spec['port']) if spec['port'] else None
        self.last_seq = -1
        self.policy = None
        self.replica = None
        self.fiware = None
        if spec.get('fiware_tenant'):
            from .fiware.control import Endpoint
            self.fiware=Endpoint(spec,spec['taz'],role='local')
        if not spec['demo']:
            import torch
            from .models import load_checkpoint, local_policy
            torch.set_num_threads(1)
            self.policy = shared_policy if shared_policy is not None else local_policy(load_checkpoint(spec['profile'], spec['role']))
            if spec.get('replica'):
                from .simulation import City
                self.replica = City(dict(spec,gui=False), Path(spec['output']) / spec['taz'] / 'simulation')
                self.replica.reset()

    def handle(self, request):
        if request['op'] == 'feedback':
            if request['seq'] != self.last_seq:
                raise ValueError('Feedback sequence does not match the latest decision')
            self.store.acknowledge(request['seq'], request['feedback'])
            return {'acknowledged': request['seq']}
        if request['op'] == 'advance':
            if self.replica:
                return self.replica.advance(request['actions'])
            return None
        event = dict(request['event'])
        if event['taz'] != self.spec['taz'] or event['seq'] <= self.last_seq:
            raise ValueError('Wrong owner or stale/duplicate control sequence')
        self.last_seq = event['seq']
        # Explicit deterministic one-way sensor transmission delay, separate from compute.
        time.sleep(self.spec['delay_ms'] / 1000)
        event['ingest_ns'] = time.perf_counter_ns()
        if self.fiware:
            observation=self.fiware.read('observation',event['seq'])
            coordination=self.fiware.read('coordination',event['seq'])
            event.update(observation=observation['observation'],mask=observation['mask'],
                         price=coordination['price'],price_context=coordination['price_context'])
        start = time.perf_counter_ns()
        cpu_start=time.thread_time_ns()
        if self.replica and not self.fiware:
            local = self.replica.snapshot()
            i = self.replica.ids.index(self.spec['taz'])
            event['observation'], event['mask'] = local['obs'][i], local['mask'][i]
        if self.policy:
            import torch
            with torch.inference_mode():
                obs = torch.tensor([event['observation'] + event['price_context']], dtype=torch.float32)
                mask = torch.tensor([event['mask']], dtype=torch.bool)
                actions, _, _ = self.policy.act(obs, mask, deterministic=True)
                action = actions[0].tolist()
        else:
            # Infrastructure demo only: this controller is deliberately not described as RL.
            action = [2 if event['observation'][0] > 0.6 and valid else 1 for valid in event['mask']]
        event.update(action=action, inference_ms=(time.perf_counter_ns()-start)/1e6,
                     inference_cpu_ms=(time.thread_time_ns()-cpu_start)/1e6)
        if self.fiware:
            event['fiware_command']=self.fiware.publish('command',event['seq'],
                dict(action=action,created_ns=time.perf_counter_ns()))
        self.store.append(event)
        event['stored_ns'] = time.perf_counter_ns()
        return event

    def close(self):
        if self.replica:
            self.replica.close()
        if self.server:
            self.server.shutdown(); self.server.server_close()
        self.store.close()


def worker(connection, spec):
    twin = None
    try:
        twin = Twin(spec)
        connection.send({'ready': True})
        while True:
            request = connection.recv()
            if request['op'] == 'stop':
                break
            connection.send({'result': twin.handle(request)})
    except BaseException:
        try:
            connection.send({'error': traceback.format_exc()})
        except (BrokenPipeError, EOFError):
            pass
    finally:
        if twin:
            twin.close()
        connection.close()


class Fleet:
    """Spawned processes avoid sharing Torch/TraCI state; bounded RPCs fail closed."""
    def __init__(self, specs, mode, timeout=120):
        self.mode, self.timeout = mode, timeout
        self.nodes, self.connections, self.processes = [], [], []
        try:
            if mode == 'monolithic':
                for spec in specs:
                    shared = self.nodes[0].policy if self.nodes else None
                    self.nodes.append(Twin(spec, shared_policy=shared))
            else:
                import multiprocessing as mp
                ctx = mp.get_context('spawn')
                for spec in specs:
                    parent, child = ctx.Pipe()
                    process = ctx.Process(target=worker, args=(child, spec), name='taz-'+spec['taz'])
                    process.start(); child.close()
                    self.connections.append(parent); self.processes.append(process)
                for conn in self.connections:
                    self._receive(conn)
        except BaseException:
            self.close(); raise

    def _receive(self, conn):
        if not conn.poll(self.timeout):
            raise TimeoutError('TAZ response deadline exceeded; no partial action batch applied')
        result = conn.recv()
        if 'error' in result:
            raise RuntimeError(result['error'])
        return result.get('result')

    def batch(self, requests):
        if len(requests) != len(self.nodes or self.connections):
            raise ValueError('A control barrier must contain every TAZ')
        if self.mode == 'monolithic':
            return [node.handle(req) for node, req in zip(self.nodes, requests)]
        for conn, req in zip(self.connections, requests):
            conn.send(req)
        return [self._receive(conn) for conn in self.connections]

    def close(self):
        for conn in self.connections:
            try:
                conn.send({'op': 'stop'})
            except (BrokenPipeError, EOFError, OSError):
                pass
        for process in self.processes:
            process.join(5)
            if process.is_alive():
                process.terminate(); process.join(5)
        for conn in self.connections:
            conn.close()
        for node in self.nodes:
            node.close()
