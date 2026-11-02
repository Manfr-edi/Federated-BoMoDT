# Attribution and provenance

This research checkout derives from the local BoMoDT and GreenWave_HRL projects.
The BoMoDT AGPL-3.0 license text is preserved in LICENSE. Upstream source headers
remain in the vendored files. The project does not assert ownership of upstream
code, Bologna geographic data, SUMO assets, or pretrained model weights.

The vendored GreenWave modules preserve the original package import layout to
avoid changing the trained observation/action contract. New code lives under
federation/. The copied runtime does not call the original project repositories.
See docs/source-analysis.md for source revisions and configs/models.json for
model source paths and artifact checksums. No private datasets, FIWARE environment
secrets, databases, full training histories or episode checkpoints were copied.

The integration update includes the normalized `processed_traffic_flow.csv` and
`processed_traffic_flow_2026.csv` files copied from GreenWave_HRL, derived from
Bologna detector data. They are emulator inputs; no local FIWARE credentials or
source-project database files are copied. Public documentation references for
FIWARE/QuantumLeap/Streamlit are linked in docs/fiware.md.
