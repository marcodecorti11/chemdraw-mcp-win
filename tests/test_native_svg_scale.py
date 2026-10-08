"""Windows ChemDraw SVG units are display pixels, not points; physical size must not depend on the display.

Observed on ChemDraw 26.1 (Windows, 200 % display scaling): a 722 x 110 pt drawing exported as a
1926 x 293 px SVG whose elements use matrix(0.133333 ...), i.e. 20 internal units per point times
192/72. ChemDraw on macOS writes matrix(0.05 ...) and one SVG unit per point.
"""
import struct
import xml.etree.ElementTree as ET

import pytest

from chemdraw_macos import physical_export
from chemdraw_macos.physical_export import page_svgs, physical_png, physical_svg

WINDOWS_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="1926px" height="293px" viewBox="0 0 1926 293">'
               '<path stroke="#000000" stroke-width="32" fill="none" transform="matrix(0.133333 0 0 0.133333 -96 -96)" '
               'd="M 1031.91,1451.07 L 1249.43,1451.07"/>'
               '<text x="0" y="0" transform="matrix(0.133333 0 0 0.133333 1034.38 176.92)" font-size="165px">O</text></svg>')


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(physical_export, 'WINDOWS', True)


def test_windows_svg_is_sized_in_points_from_the_native_transform(windows):
    result, size = physical_svg(WINDOWS_SVG)
    assert size == pytest.approx((1926 * 72 / 192, 293 * 72 / 192))
    root = ET.fromstring(result)
    assert root.get('width') == '722.25pt' and root.get('height') == '109.875pt'
    assert root.get('viewBox') == '0 0 1926 293'  # geometry untouched; only the unit mapping changes


def test_macos_behaviour_is_unchanged(monkeypatch):
    monkeypatch.setattr(physical_export, 'WINDOWS', False)
    result, size = physical_svg(WINDOWS_SVG)
    assert size == (1926, 293) and ET.fromstring(result).get('width') == '1926pt'


def test_windows_png_has_the_physical_pixel_size(windows):
    png = physical_png(WINDOWS_SVG, 300)
    width, height = struct.unpack('>II', png[16:24])
    assert abs(width - 722.25 * 300 / 72) <= 1 and abs(height - 109.875 * 300 / 72) <= 1


def test_windows_scale_requires_one_consistent_native_transform(windows):
    mixed = WINDOWS_SVG.replace('matrix(0.133333 0 0 0.133333 1034.38', 'matrix(0.05 0 0 0.05 1034.38')
    with pytest.raises(ValueError, match='scale'):
        physical_svg(mixed)
    bare = '<svg xmlns="http://www.w3.org/2000/svg" width="72px" height="36px" viewBox="0 0 72 36"><path d="M9 18H27"/></svg>'
    with pytest.raises(ValueError, match='scale'):
        physical_svg(bare)


def test_windows_pages_are_clipped_in_native_units(windows):
    cdxml = ('<CDXML><page id="1" HeightPages="2" WidthPages="1" BoundingBox="0 0 540 720">'
             '<fragment id="2"><n id="3" p="10 10"/></fragment></page></CDXML>')
    svg = WINDOWS_SVG.replace('width="1926px" height="293px" viewBox="0 0 1926 293"',
                              'width="1440px" height="1920px" viewBox="0 0 1440 1920"')
    sheets = page_svgs(svg, cdxml)
    assert len(sheets) == 2
    second = ET.fromstring(sheets[1])
    s = 192 / 72
    from chemdraw_macos.polish import bounds
    from chemdraw_macos.core import validate_cdxml
    extent = bounds(validate_cdxml(cdxml).find('page'))
    half = extent.height / 2
    expected = [extent.left * s - 96, (extent.top + half) * s - 96, extent.width * s, half * s]
    assert [float(v) for v in second.get('viewBox').split()] == pytest.approx(expected)
    assert physical_svg(sheets[1])[1] == pytest.approx((extent.width, half))


def test_windows_physical_png_uses_the_measured_faces(windows, monkeypatch):
    from chemdraw_macos import native_faces
    seen = []
    monkeypatch.setattr(native_faces, 'match_native_faces', lambda svg, *a, **k: seen.append(svg) or svg)
    physical_png(WINDOWS_SVG, 150)
    assert seen == [WINDOWS_SVG]


def test_macos_physical_png_does_not_rewrite_faces(monkeypatch):
    from chemdraw_macos import native_faces
    monkeypatch.setattr(physical_export, 'WINDOWS', False)
    monkeypatch.setattr(native_faces, 'match_native_faces', lambda *a, **k: pytest.fail('rewritten on macOS'))
    physical_png(WINDOWS_SVG, 150)
