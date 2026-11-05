import pytest

from gridsim.config import load_config


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture(scope="session")
def plays_2025(cfg):
    from gridsim.model import load_prepared
    return load_prepared(2025, cfg)


@pytest.fixture(scope="session")
def model_2025(cfg, plays_2025):
    from gridsim.model import build_model
    return build_model(cfg, 2025, None, plays_2025)
