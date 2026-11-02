"""Process-tree resource sampling, including workers and SUMO descendants."""
import json
import os
import threading
import time


class ResourceMonitor:
    def __init__(self, path):
        self.path, self.stop_event, self.samples = path, threading.Event(), []
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            import psutil
        except ImportError:
            return  # Dependency-free infrastructure demo marks resources unavailable.
        root = psutil.Process(os.getpid())
        while not self.stop_event.is_set():
            sample = {'wall_ns': time.perf_counter_ns(), 'processes': []}
            processes=[root]+root.children(recursive=True)
            dashboard_pid=os.environ.get('BOMODT_DASHBOARD_PID')
            if dashboard_pid:
                try: processes.append(psutil.Process(int(dashboard_pid)))
                except (psutil.NoSuchProcess,ValueError): pass
            processes=list({p.pid:p for p in processes}.values())
            for proc in processes:
                try:
                    mem, cpu = proc.memory_full_info(), proc.cpu_times()
                    cpu_sampled_ns=time.perf_counter_ns()
                    io = proc.io_counters()
                    sample['processes'].append(dict(pid=proc.pid, create_time=proc.create_time(),
                        role='dashboard' if str(proc.pid)==dashboard_pid else 'runtime', name=proc.name(), rss=mem.rss,
                        sampled_ns=cpu_sampled_ns,
                        uss=getattr(mem,'uss',None), cpu_seconds=cpu.user+cpu.system, read_bytes=io.read_bytes, write_bytes=io.write_bytes))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            self.samples.append(sample)
            self.stop_event.wait(0.25)

    def close(self):
        self.stop_event.set(); self.thread.join()
        self.path.write_text(json.dumps({'available': bool(self.samples), 'samples':self.samples}))


def distribution(values):
    if not values:
        return None
    values = sorted(values)
    return {'count':len(values), 'mean':sum(values)/len(values),
            **{f'p{p}': values[round((len(values)-1)*p/100)] for p in [50,95,99]}, 'max':values[-1]}
