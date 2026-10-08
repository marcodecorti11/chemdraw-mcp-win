"""Raster text must use the face ChemDraw measured, inferred from ChemDraw's own word positions.

Windows ChemDraw positions every word of a caption explicitly in its SVG. When the rasterizer
picks a different face of the same family (or a different substitute for a missing family),
words collide or the caption changes typeface. These tests use synthetic faces; the Windows
tests use real installed fonts.
"""
import re
import sys

import pytest

from chemdraw_macos.native_faces import Face, match_native_faces

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="100" viewBox="0 0 400 100">'
       '<g>{}</g></svg>')


def text(x, y, body, family='Helvetica Neue', weight='normal', size=165, scale=0.133333):
    return (f'<text x="0" y="0" stroke="none" fill="#000000" transform="matrix({scale} 0 0 {scale} {x} {y})" '
            f'font-style="normal" font-weight="{weight}" font-size="{size}px" font-family="{family}" >\n{body}</text>')


class Fake(Face):
    def __init__(self, family, weight, width, italic=False):
        super().__init__(family=family, weight=weight, italic=italic, path=None, index=0)
        self.width = width

    def advance(self, string):
        return self.width * len(string)


THIN, ROMAN, BOLD = Fake('Helvetica Neue', 100, .40), Fake('Helvetica Neue', 400, .50), Fake('Helvetica Neue', 700, .60)
ARIAL = Fake('Arial', 400, .55)
ARIAL_BOLD = Fake('Arial', 700, .65)
FACES = {'helvetica neue': [THIN, ROMAN, BOLD], 'arial': [ARIAL, ARIAL_BOLD]}


def faces(family):
    return FACES.get(family.lower(), [])


def caption(width, family='Helvetica Neue', weight='normal', words=('Glycine ', 'zwitterion')):
    em = 165 * 0.133333
    first = 10.0
    return SVG.format(text(first, 50, words[0], family, weight) +
                      text(first + em * width * len(words[0]), 50, words[1], family, weight))


def weights(svg):
    return re.findall(r'font-weight="([^"]*)"', svg)


def test_words_laid_out_with_a_lighter_face_are_rendered_with_that_face():
    out = match_native_faces(caption(.40), faces, lambda family: None)
    assert weights(out) == ['100', '100']
    assert re.findall(r'font-family="([^"]*)"', out) == ['Helvetica Neue', 'Helvetica Neue']


def test_matching_declared_face_keeps_its_weight():
    out = match_native_faces(caption(.50), faces, lambda family: None)
    assert weights(out) == ['400', '400']


def test_missing_family_uses_the_windows_substitute_instead_of_a_serif_fallback():
    out = match_native_faces(caption(.55, family='No Such Family'), faces, lambda family: 'Arial')
    assert re.findall(r'font-family="([^"]*)"', out) == ['Arial', 'Arial']
    assert weights(out) == ['400', '400']


def test_missing_family_without_layout_evidence_still_uses_the_substitute_at_the_declared_weight():
    svg = SVG.format(text(10, 50, 'OH', family='No Such Family', weight='bold'))
    out = match_native_faces(svg, faces, lambda family: 'Arial')
    assert re.findall(r'font-family="([^"]*)"', out) == ['Arial']
    assert weights(out) == ['700']


def test_single_installed_word_without_evidence_is_unchanged():
    svg = SVG.format(text(10, 50, 'Benzoate'))
    assert match_native_faces(svg, faces, lambda family: None) == svg


def test_unrelated_texts_on_one_baseline_are_not_evidence():
    # Two separate captions side by side: their spacing says nothing about glyph advances.
    em = 165 * 0.133333
    svg = SVG.format(text(10, 50, 'Parent') + text(10 + em * 9.7, 50, '2-Me'))
    assert match_native_faces(svg, faces, lambda family: None) == svg


def test_texts_on_different_baselines_or_sizes_are_not_paired():
    em = 165 * 0.133333
    svg = SVG.format(text(10, 50, 'NH') + text(10 + em * .4 * 2, 55, '3', size=120))
    assert match_native_faces(svg, faces, lambda family: None) == svg


def test_rewritten_svg_keeps_geometry_and_text():
    out = match_native_faces(caption(.40), faces, lambda family: None)
    assert out.count('<text') == 2 and 'Glycine' in out and 'zwitterion' in out
    assert re.findall(r'transform="([^"]*)"', out) == re.findall(r'transform="([^"]*)"', caption(.40))


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows installed fonts and GDI substitution')
def test_windows_missing_family_is_rendered_like_the_gdi_substitute(monkeypatch):
    from chemdraw_macos import native_faces, raster
    from chemdraw_macos.raster import rasterize_svg
    monkeypatch.setattr(raster, 'WINDOWS', True)
    arial = [f for f in native_faces.system_faces('Arial') if f.weight == 400 and not f.italic][0]
    em = 30.0
    width = arial.advance('Glycine ')
    def svg(family):
        return ('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="60" viewBox="0 0 300 60">'
                f'<text x="0" y="0" transform="matrix(1 0 0 1 5 40)" font-size="{em}px" font-family="{family}" '
                f'font-weight="normal" font-style="normal">\nGlycine </text>'
                f'<text x="0" y="0" transform="matrix(1 0 0 1 {5 + em * width} 40)" font-size="{em}px" '
                f'font-family="{family}" font-weight="normal" font-style="normal">\nzwitterion</text></svg>')
    assert native_faces.gdi_substitute('No Such Family XYZ') == 'Arial'
    assert rasterize_svg(svg('No Such Family XYZ'), 600) == rasterize_svg(svg('Arial'), 600)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows installed fonts')
def test_windows_font_tables_give_outline_advances():
    from chemdraw_macos import native_faces
    ImageFont = pytest.importorskip('PIL.ImageFont')
    arial = [f for f in native_faces.system_faces('Arial') if f.weight == 400 and not f.italic][0]
    reference = ImageFont.truetype(str(arial.path), 2048, index=arial.index)  # independent FreeType reading
    for string in ('Glycine ', 'zwitterion', 'NH3+ (S)-1'):
        assert arial.advance(string) == pytest.approx(reference.getlength(string) / 2048, abs=1e-3)


# ---- exact evidence from ChemDraw's own metafile (per-glyph advances), Windows export side

import struct as _struct


class Widths(Face):
    def __init__(self, family, weight, widths, italic=False):
        super().__init__(family=family, weight=weight, italic=italic, path=None, index=0)
        self.widths = widths

    def advance(self, string):
        return sum(self.widths[ch] for ch in string)


THIN_W = Widths('Helvetica Neue', 200, {'a': .40, 'b': .45, 'c': .38})
ROMAN_W = Widths('Helvetica Neue', 400, {'a': .52, 'b': .56, 'c': .50})
BOLD_W = Widths('Helvetica Neue', 700, {'a': .58, 'b': .62, 'c': .55})


def emf(runs):
    """Minimal EMF: one font object and one ExtTextOutW record per run (face, weight, height, text)."""
    records = [_struct.pack('<II', 1, 8)]
    for handle, (face, weight, height, text, *given) in enumerate(runs, 1):
        logfont = _struct.pack('<iiiii', -height, 0, 0, 0, weight) + bytes([0, 0, 0, 1, 0, 0, 0, 0])
        logfont += face.encode('utf-16-le').ljust(64, b'\0')
        records.append(_struct.pack('<III', 82, 12 + len(logfont), handle) + logfont)
        records.append(_struct.pack('<III', 37, 12, handle))
        face_widths = given[0] if given else THIN_W.widths if weight == 400 else BOLD_W.widths
        dx = [round(face_widths[ch] * height) for ch in text]
        n = len(text)
        body = _struct.pack('<16sIff', b'\0' * 16, 1, 1.0, 1.0)
        off_string = 8 + len(body) + 8 + 4 + 4 + 4 + 16 + 4
        off_dx = off_string + ((2 * n + 3) // 4) * 4
        emrtext = _struct.pack('<iiIII16sI', 0, 0, n, off_string, 0, b'\0' * 16, off_dx)
        payload = body + emrtext
        string = text.encode('utf-16-le').ljust(((2 * n + 3) // 4) * 4, b'\0')
        record = payload + string + _struct.pack(f'<{n}i', *dx)
        records.append(_struct.pack('<II', 84, 8 + len(record)) + record)
    records.append(_struct.pack('<II', 14, 8))
    return b''.join(records)


def widths_faces(family):
    return [THIN_W, ROMAN_W, BOLD_W] if family.lower() == 'helvetica neue' else []


def test_metafile_advances_identify_the_face_chemdraw_measured():
    from chemdraw_macos.native_faces import faces_from_emf
    data = emf([('Helvetica Neue', 400, 100, 'abcab'), ('Helvetica Neue', 700, 100, 'cab')])
    mapping = faces_from_emf(data, widths_faces)
    assert mapping[('helvetica neue', 400, False)] is THIN_W
    assert mapping[('helvetica neue', 700, False)] is BOLD_W


def test_single_word_runs_use_the_recorded_metafile_faces():
    from chemdraw_macos.native_faces import annotate_faces
    svg = SVG.format(text(10, 50, 'Benzoate') + text(10, 80, '1', weight='bold'))
    annotated = annotate_faces(svg, {('helvetica neue', 400, False): THIN_W, ('helvetica neue', 700, False): BOLD_W})
    out = match_native_faces(annotated, faces, lambda family: None)
    assert weights(out) == ['200', '700']


def test_recorded_faces_survive_only_as_a_comment():
    from chemdraw_macos.native_faces import annotate_faces
    import xml.etree.ElementTree as ET
    svg = SVG.format(text(10, 50, 'Benzoate'))
    annotated = annotate_faces(svg, {('helvetica neue', 400, False): THIN_W})
    assert '<!--' in annotated
    assert ET.tostring(ET.fromstring(annotated)) == ET.tostring(ET.fromstring(svg))  # geometry/text untouched


FALLBACK = Widths('Fallback Sans', 400, {'G': .7, 'r': .35, 'o': .55, 'u': .55, 'p': .55, ' ': .28, '1': .55})


def alias_faces(family):
    return {'helvetica neue': [THIN_W, ROMAN_W, BOLD_W], 'fallback sans': [FALLBACK]}.get(family.lower(), [])


def test_svg_family_drawn_with_another_face_is_linked_through_identical_text():
    # Windows ChemDraw wrote font-family="@MS Gothic" but drew those runs with another face.
    from chemdraw_macos.native_faces import recorded_faces
    svg = SVG.format(text(10, 50, 'Group ', family='@Vertical Font', weight='bold') + text(10, 90, 'abc'))
    # The fallback family has only a regular face; ChemDraw requested bold.
    data = emf([('Helvetica Neue', 400, 100, 'abc'), ('Fallback Sans', 700, 100, 'Group ', FALLBACK.widths)])
    mapping = recorded_faces(svg, data, alias_faces)
    assert mapping[('@vertical font', 700, False)] is FALLBACK
    assert mapping[('helvetica neue', 400, False)] is THIN_W


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows vertical-font names')
def test_windows_at_font_names_are_accepted_but_css_syntax_is_not(monkeypatch):
    from chemdraw_macos import raster
    from chemdraw_macos.raster import _validate
    monkeypatch.setattr(raster, 'WINDOWS', True)
    ok = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><text font-family="@MS Gothic">G</text></svg>'
    _validate(ok, 256)
    for bad in ('@import url(x)', '@MS Gothic; x', 'a@b'):
        with pytest.raises(ValueError):
            _validate(ok.replace('@MS Gothic', bad), 256)
    with pytest.raises(ValueError):
        _validate(ok.replace('font-family="@MS Gothic"', 'fill="@x"'), 256)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows font registration')
def test_registered_font_files_are_found_without_the_environment_folders(tmp_path, monkeypatch):
    import shutil
    from chemdraw_macos import native_faces
    custom = tmp_path / 'elsewhere' / 'CustomArial.ttf'
    custom.parent.mkdir()
    shutil.copyfile(native_faces._windows_fonts_folder() / 'arial.ttf', custom)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'no-local'))
    monkeypatch.setattr(native_faces, '_windows_fonts_folder', lambda: tmp_path / 'no-windows-fonts')
    monkeypatch.setattr(native_faces, '_registry_fonts', lambda: [str(custom)])
    monkeypatch.setattr(native_faces, '_SYSTEM', None)
    assert [f.path for f in native_faces.system_faces('Arial')] == [custom]
    assert native_faces.resvg_font_options() == {'font_files': [str(custom)]}


def test_rasterizer_passes_registered_font_files_on_windows(monkeypatch):
    import resvg_py
    from chemdraw_macos import native_faces, raster
    seen = {}
    monkeypatch.setattr(native_faces, 'resvg_font_options', lambda: {'font_files': ['C:/x/font.otf']})
    monkeypatch.setattr(native_faces, 'match_native_faces', lambda svg, *a, **k: svg)
    monkeypatch.setattr(raster, 'WINDOWS', True)
    def fake(**kwargs):
        seen.update(kwargs)
        raise RuntimeError('stop')
    monkeypatch.setattr(resvg_py, 'svg_to_bytes', fake)
    with pytest.raises(RuntimeError, match='stop'):
        raster.rasterize_svg('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>', 256)
    assert seen['font_files'] == ['C:/x/font.otf'] and seen['skip_system_fonts'] is False


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows per-user fonts')
def test_per_user_font_renders_the_same_when_the_client_environment_lacks_localappdata(tmp_path):
    import os
    import subprocess
    from chemdraw_macos import native_faces
    if not native_faces.system_faces('Helvetica Neue'):
        pytest.skip('Helvetica Neue is not installed on this machine')
    svg = tmp_path / 'x.svg'
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="60" viewBox="0 0 300 60">'
                   '<text x="5" y="40" font-family="Helvetica Neue" font-size="30">Glycine zwitterion</text></svg>', encoding='utf-8')
    def render(name, env):
        out = tmp_path / name
        subprocess.run([sys.executable, '-m', 'chemdraw_macos.raster', str(svg), str(out), '600'], check=True,
                       env=env, stdin=subprocess.DEVNULL, capture_output=True)
        return out.read_bytes()
    normal = render('normal.png', dict(os.environ))
    trimmed = dict(os.environ, LOCALAPPDATA=str(tmp_path / 'redirected'), USERPROFILE=str(tmp_path))
    assert render('trimmed.png', trimmed) == normal
