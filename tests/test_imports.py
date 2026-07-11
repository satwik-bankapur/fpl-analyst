"""Every module in the codebase must import cleanly.

The lesson from the writer/generic.py latent bug (a wrong import that shipped green
in f96ad2d because no test ever imported the module): walk every package and
importlib.import_module each module, so an un-imported module can never ship again.
Parametrized so a single broken module is a single red test, named for the module.
"""
from __future__ import annotations

import importlib
import pkgutil

import pytest

import api
import engine
import fpl
import graph
import writer

_PACKAGES = [fpl, engine, graph, writer, api]


def _all_modules() -> list[str]:
    names: list[str] = []
    for pkg in _PACKAGES:
        names.append(pkg.__name__)
        # walk_packages imports subpackages to recurse; a broken __init__ surfaces
        # here at collection time (still a suite failure), leaf modules below.
        for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
            names.append(info.name)
    return sorted(set(names))


@pytest.mark.parametrize("module_name", _all_modules())
def test_module_imports(module_name):
    importlib.import_module(module_name)
