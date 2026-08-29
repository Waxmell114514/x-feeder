from .base import Source, SourceResult
from .fixture import FixtureSource
from .x_api import XApiSource


def build_source(cfg) -> Source:
    provider = cfg.source.provider
    if provider == "x_api":
        return XApiSource(cfg)
    if provider == "fixture":
        return FixtureSource(cfg)
    raise ValueError(f"unknown source provider: {provider!r}")


__all__ = ["Source", "SourceResult", "FixtureSource", "XApiSource", "build_source"]
