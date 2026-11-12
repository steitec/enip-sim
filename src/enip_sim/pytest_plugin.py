"""pytest plugin: a fresh simulated EtherNet/IP bench for every test.

Fixtures:

- ``enip_bench`` -- the bench from ``--enip-bench FILE`` or the ini key ``enip_bench``
  (relative to the rootdir); without either, one ``drive``. Serves EtherNet/IP on a free
  port; ``enip_bench.client()`` returns a client, ``enip_bench.port`` the port.
- ``enip_bench_factory`` -- ``factory(source, **start_options)`` for more benches: a bench
  file, a dict, or a bundled profile name. Start options are those of :meth:`Bench.start`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from .bench import Bench, BenchError
from .profile import bundled_profiles

DEFAULT_PROFILE = "drive"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("enip-sim")
    group.addoption("--enip-bench", metavar="FILE",
                    help="bench/profile file (YAML) for the enip_bench fixture")
    parser.addini("enip_bench", "bench/profile file (YAML) for the enip_bench fixture, "
                  "relative to the rootdir")


class BenchPool:
    def __init__(self, root: Path | None):
        self.root = root
        self.benches: list[Bench] = []

    def start(self, source: str | Path | dict, **options: Any) -> Bench:
        message = None
        try:
            if isinstance(source, dict):
                bench = Bench.from_dict(source)
            elif isinstance(source, str) and source in bundled_profiles():
                bench = Bench.single(source)
            else:
                path = Path(source)
                if self.root is not None and not path.is_absolute():
                    path = self.root / path
                bench = Bench.from_yaml(path)
            options.setdefault("port", 0)
            bench.start(**options)
        except (BenchError, OSError) as error:
            message = f"enip-sim bench: {error}"
        if message is not None:
            pytest.fail(message, pytrace=False)
        self.benches.append(bench)
        return bench

    def stop_all(self) -> None:
        failure = None
        for bench in self.benches:
            try:
                bench.stop()
            except Exception as error:  # keep stopping the others
                failure = failure or error
        self.benches.clear()
        if failure is not None:
            raise failure


def _configured(config: pytest.Config) -> Path | str:
    option = config.getoption("--enip-bench")
    if option:
        return config.invocation_params.dir / option
    ini = config.getini("enip_bench")
    if ini:
        return config.rootpath / ini
    return DEFAULT_PROFILE


@pytest.fixture
def enip_bench_factory(request: pytest.FixtureRequest):
    pool = BenchPool(request.config.rootpath)
    yield pool.start
    pool.stop_all()


@pytest.fixture
def enip_bench(request: pytest.FixtureRequest):
    pool = BenchPool(None)
    yield pool.start(_configured(request.config))
    pool.stop_all()
