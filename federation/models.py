"""Load the exported GreenWave policies without changing their input ABI."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor/greenwave"
# Retain upstream absolute imports inside the isolated application process.
sys.path.insert(0, str(VENDOR))


def checkpoint_path(profile, role="coordinated_controller"):
    entry = json.loads((ROOT / "configs/models.json").read_text())[profile][role]
    path = ROOT / entry["path"]
    if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
        raise ValueError(f"Checkpoint checksum mismatch: {path}")
    return path


def load_checkpoint(profile, role="coordinated_controller"):
    import torch
    # These are trusted, locally exported artifacts, containing dataclass metadata.
    return torch.load(checkpoint_path(profile, role), map_location="cpu", weights_only=False)


def local_policy(c):
    from rl_module.policy import MultiDiscreteActorCritic, PriceAdapterActorCritic
    bins = tuple(c["local_controller_settings"]["action_bins"])
    groups = max(map(len, c["control_groups_by_taz"].values()))
    if c.get("local_policy_class") == "PriceAdapterActorCritic":
        s = c["coordinator_settings"]
        model = PriceAdapterActorCritic(
            base_obs_dim=c["base_obs_dim"], price_context_dim=s.get("local_price_context_dim", 3),
            act_dim=groups, action_bins=bins,
            adapter_hidden_dim=s.get("price_adapter_hidden_dim", 64),
            adapter_scale=s.get("price_adapter_scale", 1.0),
            adapter_mode=s.get("price_adapter_mode", "legacy"))
        state = c["local_controller_state_dict"]
    else:
        model = MultiDiscreteActorCritic(c["obs_dim"], groups, bins)
        state = c.get("local_controller_state_dict", c.get("model_state_dict"))
    model.load_state_dict(state, strict=True)
    return model.eval()


def coordinator(c):
    from rl_module.policy import MultiDiscreteActorCritic
    bins = tuple(c["coordinator_settings"]["price_bins"])
    model = MultiDiscreteActorCritic(c["coordinator_obs_dim"], len(c["taz_ids"]), bins)
    model.load_state_dict(c["coordinator_state_dict"], strict=True)
    return model.eval()
