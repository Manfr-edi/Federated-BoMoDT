"""Create a small synthetic fixture, not calibrated Bologna demand."""
from pathlib import Path
import subprocess
import sys
root = Path(__file__).resolve().parents[1]
out = Path(sys.argv[1] if len(sys.argv)>1 else root/'runs/fixture').resolve()
out.mkdir(parents=True,exist_ok=True)
subprocess.run([sys.executable, '/usr/share/sumo/tools/randomTrips.py', '-n',
    str(root/'vendor/greenwave/sumoenv/static/full.net.xml'), '-r',str(out/'routes.rou.xml'),
    '-o',str(out/'trips.xml'),'--seed','42','--begin','0','--end','3600','--period','60','--validate'],check=True)
print(out/'routes.rou.xml')
