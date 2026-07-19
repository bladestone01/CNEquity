from pathlib import Path

from ashare_lake.config import load_config


def test_load_init_phases_reads_job_init_phases_names(tmp_path):
    cfg_path = tmp_path / "test.toml"
    cfg_path.write_text(
        f"""
[data]
root = "{tmp_path / "data"}"

[[job.daily.waves]]
name = "core"
parallel = true
steps = ["instruments"]

[job.init.phases]
names = ["phase1_reference", "phase4_finalize"]
"""
    )
    cfg = load_config(cfg_path)
    assert cfg.init_phases == ["phase1_reference", "phase4_finalize"]


def test_example_config_loads_init_phases():
    root = Path(__file__).resolve().parents[2]
    cfg = load_config(root / "configs" / "ashare-lake.example.toml")
    assert "phase1_reference" in cfg.init_phases
    assert "phase4_finalize" in cfg.init_phases
