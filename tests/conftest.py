"""
conftest.py — stub hummingbot (not installed in test env) so funding rate
feed modules can be imported without the full Hummingbot dependency tree.
"""
import sys
import types


def _stub_module(full_name: str, **attrs):
    """Create and register a stub module with optional attributes."""
    mod = types.ModuleType(full_name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[full_name] = mod
    return mod


# Build parent stubs first so Python treats them as packages
for name in [
    "hummingbot",
    "hummingbot.client",
    "hummingbot.client.config",
    "hummingbot.client.settings",
    "hummingbot.data_feed",
    "hummingbot.data_feed.candles_feed",
    "hummingbot.data_feed.candles_feed.candles_factory",
    "hummingbot.data_feed.candles_feed.data_types",
]:
    if name not in sys.modules:
        _stub_module(name)


# Stub core.data_sources.clob to prevent deep dependency chain
# (plotly, hummingbot connectors, etc.) from being required in tests
import os as _os
_ds_path = _os.path.join(_os.path.dirname(__file__), "..", "core", "data_sources")
_clob_stub = _stub_module("core.data_sources.clob", CLOBDataSource=object)
# We also need core.data_sources to be real (a package) so submodule imports work.
# Inject clob stub before core.data_sources.__init__ tries to import it.
# We do this by registering the stub before core.data_sources is imported.

# Attribute stubs
sys.modules["hummingbot.client.config"].get_connector_class = lambda *a, **kw: None
sys.modules["hummingbot.client.config.config_helpers"] = _stub_module(
    "hummingbot.client.config.config_helpers",
    get_connector_class=lambda *a, **kw: None,
)
sys.modules["hummingbot.client.settings"] = _stub_module(
    "hummingbot.client.settings",
    AllConnectorSettings=object,
    ConnectorType=object,
)
sys.modules["hummingbot.data_feed.candles_feed.candles_factory"] = _stub_module(
    "hummingbot.data_feed.candles_feed.candles_factory",
    CandlesFactory=object,
)
sys.modules["hummingbot.data_feed.candles_feed.data_types"] = _stub_module(
    "hummingbot.data_feed.candles_feed.data_types",
    CandlesConfig=object,
    HistoricalCandlesConfig=object,
)
