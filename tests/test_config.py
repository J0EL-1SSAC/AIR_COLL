from pathlib import Path
import yaml


def test_dashboard_configuration_is_coherent():
    config = yaml.safe_load(Path("config.yaml").read_text())
    assert config["airport"]["icao"]
    assert config["airport"]["radius_nm"] > 0
    assert config["collector"]["poll_interval_s"] > 0
    assert config["web"]["altitude_bands_m"]["low_max"] < config["web"]["altitude_bands_m"]["medium_max"]
    assert config["cpa"]["lookahead_s"] > 0
    assert config["cpa"]["horizontal_cutoff_nm"] > 0
    assert config["risk"]["active_profile"] in config["risk"]["profiles"]
    assert config["risk"]["profiles"]["sensitive_test"]["levels"]["LOW"]["max_horizontal_separation_m"] > config["risk"]["profiles"]["research_default"]["levels"]["LOW"]["max_horizontal_separation_m"]
