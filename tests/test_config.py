from pathlib import Path
import yaml


def test_dashboard_configuration_is_coherent():
    config = yaml.safe_load(Path("config.yaml").read_text())
    assert config["airport"]["icao"]
    assert config["airport"]["radius_nm"] > 0
    assert config["collector"]["poll_interval_s"] > 0
    assert config["web"]["altitude_bands_m"]["low_max"] < config["web"]["altitude_bands_m"]["medium_max"]
