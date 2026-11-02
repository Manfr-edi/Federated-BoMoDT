"""Launch: streamlit run dashboards/federated.py --server.port 8502"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
if __name__ == '__main__':
    from federation.fiware.ui import app
    app('federated')
