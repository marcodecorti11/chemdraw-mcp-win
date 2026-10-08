"""Simulated-native unit tests model ChemDraw for macOS: one SVG unit per drawing point, PDF
export, and the alignment/distribution submenu commands that Windows COM does not expose.

Windows ChemDraw writes SVG in display pixels and physical_export reads that scale from the
native transforms (test_native_svg_scale.py). Portable tests whose fake bridges emit macOS-style
SVG keep the macOS convention on every host. Opt-in live tests use the real platform rule, and
tests that set physical_export.WINDOWS themselves override this default. No effect on macOS.
"""
import functools
from pathlib import Path

import pytest


@functools.lru_cache(maxsize=None)
def _live_module(path):
    text = Path(path).read_text(encoding='utf-8')
    return 'CHEMDRAW_LIVE_TEST' in text or 'CHEMDRAW_ADDIN_LIVE_TEST' in text


@pytest.fixture(autouse=True)
def _simulated_native_svg_units(request, monkeypatch):
    if _live_module(str(request.node.path)) and request.node.get_closest_marker('skipif') is not None:
        return
    from chemdraw_macos import core, desktop_setup, native_actions, physical_export, raster
    monkeypatch.setattr(physical_export, 'WINDOWS', False)
    monkeypatch.setattr(desktop_setup, 'WINDOWS', False)  # macOS setup fixtures (app bundles, add-in)
    monkeypatch.setattr(raster, 'WINDOWS', False)  # macOS resvg arguments and validation
    monkeypatch.setattr(core.Bridge, 'export_formats', core.ALL_EXPORT_FORMATS)
    monkeypatch.setattr(native_actions, 'WINDOWS', False)
