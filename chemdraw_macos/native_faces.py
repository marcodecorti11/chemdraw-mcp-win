"""Render native SVG text with the face ChemDraw measured, inferred from ChemDraw's own layout.

ChemDraw's SVG names only a family and a CSS weight, but positions every word (and every
label fragment) explicitly. On Windows the face ChemDraw measured can differ from the one a
CSS font matcher picks: observed on ChemDraw 26.1 with a per-user Helvetica Neue whose five
faces share one family name, ChemDraw laid out regular text with the Thin face while resvg
drew Roman, so words collided. A family that is not installed is substituted by Windows
(GDI), whereas resvg falls back to a serif face.

Evidence, strongest first: (1) the faces recorded at SVG export from ChemDraw's own metafile of
the same document, whose per-glyph advances identify the measured face for every text run
(faces_from_emf / annotate_faces, stored as an SVG comment); (2) the spacing between consecutive
text runs on one baseline compared with each candidate face's outline advances; (3) for a family
that is not installed, the GDI substitute. Only font-family/font-weight/font-style of text
elements change; geometry is untouched. Without evidence an installed family is left as declared.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import struct
import sys
import xml.etree.ElementTree as ET

from defusedxml import ElementTree as SafeET

_NS = 'http://www.w3.org/2000/svg'
_TEXT = f'{{{_NS}}}text'
_FONT_ATTRS = ('font-family', 'font-weight', 'font-style', 'font-size')
_MATRIX = re.compile(r'\s*matrix\(\s*([^)]*)\)\s*\Z')
TOLERANCE = 0.01  # relative; neighbouring weights of one family differ by several percent


class Face:
    """One installed font face: CSS-style family/weight/italic plus outline advances."""

    def __init__(self, family, weight, italic, path, index=0, units=None, offsets=None):
        self.family, self.weight, self.italic = family, weight, italic
        self.path, self.index = path, index
        self._units, self._tables, self._metrics = units, offsets, None

    def __repr__(self):
        return f'Face({self.family!r}, {self.weight}, italic={self.italic})'

    def advance(self, string):
        """Sum of glyph advances in em (no kerning), as ChemDraw positions its runs."""
        cmap, widths = self._load()
        last = widths[-1] if widths else 0
        total = 0
        for ch in string:
            glyph = cmap.get(ord(ch), 0)
            total += widths[glyph] if glyph < len(widths) else last
        return total / self._units

    def _load(self):
        if self._metrics is None:
            data = Path(self.path).read_bytes()
            tables = self._tables
            metrics = struct.unpack('>H', data[tables['hhea'] + 34:tables['hhea'] + 36])[0]
            start = tables['hmtx']
            widths = [struct.unpack('>H', data[start + 4 * i:start + 4 * i + 2])[0] for i in range(metrics)]
            self._metrics = (_cmap(data, tables['cmap']), widths)
        return self._metrics


def _cmap(data, offset):
    count = struct.unpack('>H', data[offset + 2:offset + 4])[0]
    subtables = {}
    for i in range(count):
        platform, encoding, sub = struct.unpack('>HHI', data[offset + 4 + 8 * i:offset + 12 + 8 * i])
        subtables[(platform, encoding)] = offset + sub
    for key in ((3, 10), (0, 6), (0, 4), (3, 1), (0, 3), (0, 1), (0, 0), (3, 0)):
        if key not in subtables:
            continue
        at = subtables[key]
        fmt = struct.unpack('>H', data[at:at + 2])[0]
        if fmt == 12:
            groups = struct.unpack('>I', data[at + 12:at + 16])[0]
            table = {}
            for g in range(groups):
                first, last, glyph = struct.unpack('>III', data[at + 16 + 12 * g:at + 28 + 12 * g])
                if last - first > 0x10000:
                    continue
                for code in range(first, last + 1):
                    table[code] = glyph + code - first
            return table
        if fmt == 4:
            segs = struct.unpack('>H', data[at + 6:at + 8])[0] // 2
            ends = struct.unpack(f'>{segs}H', data[at + 14:at + 14 + 2 * segs])
            base = at + 16 + 2 * segs
            starts = struct.unpack(f'>{segs}H', data[base:base + 2 * segs])
            deltas = struct.unpack(f'>{segs}h', data[base + 2 * segs:base + 4 * segs])
            range_at = base + 4 * segs
            ranges = struct.unpack(f'>{segs}H', data[range_at:range_at + 2 * segs])
            table = {}
            for s in range(segs):
                for code in range(starts[s], ends[s] + 1):
                    if code == 0xFFFF:
                        continue
                    if ranges[s] == 0:
                        glyph = (code + deltas[s]) & 0xFFFF
                    else:
                        at_glyph = range_at + 2 * s + ranges[s] + 2 * (code - starts[s])
                        glyph = struct.unpack('>H', data[at_glyph:at_glyph + 2])[0]
                        glyph = (glyph + deltas[s]) & 0xFFFF if glyph else 0
                    table[code] = glyph
            return table
    return {}


def _names(data, offset):
    _, count, strings = struct.unpack('>HHH', data[offset:offset + 6])
    found = {}
    for i in range(count):
        platform, _, language, name_id, length, at = struct.unpack('>HHHHHH', data[offset + 6 + 12 * i:offset + 18 + 12 * i])
        if platform == 3 and name_id in (1, 16):
            rank = 0 if language == 0x409 else 1
            if (name_id, rank) not in found:
                start = offset + strings + at
                found[(name_id, rank)] = data[start:start + length].decode('utf-16-be', 'replace')
    for key in ((16, 0), (16, 1), (1, 0), (1, 1)):
        if key in found:
            return found[key]
    return None


def _faces_in(path):
    data = path.read_bytes()
    if data[:4] == b'ttcf':
        count = struct.unpack('>I', data[8:12])[0]
        starts = struct.unpack(f'>{count}I', data[12:12 + 4 * count])
    else:
        starts = (0,)
    faces = []
    for index, start in enumerate(starts):
        tables = {}
        for i in range(struct.unpack('>H', data[start + 4:start + 6])[0]):
            tag, _, at, _ = struct.unpack('>4sIII', data[start + 12 + 16 * i:start + 28 + 16 * i])
            tables[tag.decode('latin-1')] = at
        if not {'name', 'OS/2', 'head', 'hhea', 'hmtx', 'cmap'} <= tables.keys():
            continue
        family = _names(data, tables['name'])
        if not family:
            continue
        os2 = tables['OS/2']
        weight = struct.unpack('>H', data[os2 + 4:os2 + 6])[0]
        selection = struct.unpack('>H', data[os2 + 62:os2 + 64])[0]
        units = struct.unpack('>H', data[tables['head'] + 18:tables['head'] + 20])[0]
        faces.append(Face(family, weight, bool(selection & 0x201), path, index, units, tables))
    return faces


_SYSTEM = None
_FONT_SUFFIXES = ('.ttf', '.otf', '.ttc', '.otc')


def _windows_fonts_folder():
    return Path(os.environ.get('WINDIR') or os.environ.get('SystemRoot') or r'C:\Windows') / 'Fonts'


def _registry_fonts():
    """Font files registered for this user (absolute paths) and machine, as Windows resolves them.

    Unlike %LOCALAPPDATA%, the registration does not depend on the environment an MCP client
    passes to the server, so per-user fonts stay visible when that variable is missing or differs.
    """
    import winreg
    files = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r'Software\Microsoft\Windows NT\CurrentVersion\Fonts') as key:
                for index in range(winreg.QueryInfoKey(key)[1]):
                    value = winreg.EnumValue(key, index)[1]
                    if isinstance(value, str) and value:
                        files.append(str(Path(value) if os.path.isabs(value) else _windows_fonts_folder() / value))
        except OSError:
            continue
    return files


def _extra_font_files():
    """Registered font files outside the system Fonts folder (which resvg scans by itself)."""
    system = _windows_fonts_folder()
    try:
        system = system.resolve()
    except OSError:
        pass
    result = []
    for name in _registry_fonts():
        path = Path(name)
        if path.suffix.lower() in _FONT_SUFFIXES and path.is_file() and path.resolve().parent != system:
            result.append(str(path))
    return list(dict.fromkeys(result))


def resvg_font_options():
    """resvg keyword arguments that make registered per-user fonts available on Windows."""
    if sys.platform != 'win32':
        return {}
    files = _extra_font_files()
    return {'font_files': files} if files else {}


def _system_index():
    """Faces of the system Fonts folder and of every registered font file."""
    global _SYSTEM
    if _SYSTEM is None:
        candidates = []
        try:
            candidates += sorted(_windows_fonts_folder().iterdir())
        except OSError:
            pass
        candidates += [Path(p) for p in _extra_font_files()]
        index, seen = {}, set()
        for path in candidates:
            if path.suffix.lower() not in _FONT_SUFFIXES:
                continue
            try:
                key = str(path.resolve()).lower()
                if key in seen:
                    continue
                seen.add(key)
                for face in _faces_in(path):
                    index.setdefault(face.family.lower(), []).append(face)
            except (OSError, struct.error, ValueError):
                continue  # unreadable or unusual font files are not candidates
        _SYSTEM = index
    return _SYSTEM


def system_faces(family):
    return list(_system_index().get(family.lower(), []))


def gdi_substitute(family):
    """Family Windows GDI realizes for a regular request of this name (ChemDraw's LOGFONT)."""
    import ctypes
    from ctypes import wintypes
    gdi32 = ctypes.WinDLL('gdi32')
    gdi32.CreateFontW.restype = wintypes.HFONT
    gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.GetFontData.restype = wintypes.DWORD
    gdi32.GetFontData.argtypes = [wintypes.HDC, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    dc = gdi32.CreateCompatibleDC(None)
    font = gdi32.CreateFontW(-100, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 0, 0, family[:31])
    try:
        previous = gdi32.SelectObject(dc, font)
        tag = struct.unpack('<I', b'name')[0]
        size = gdi32.GetFontData(dc, tag, 0, None, 0)
        if size in (0, 0xFFFFFFFF):
            return None
        buffer = ctypes.create_string_buffer(size)
        gdi32.GetFontData(dc, tag, 0, buffer, size)
        gdi32.SelectObject(dc, previous)
        return _names(buffer.raw, 0)
    finally:
        gdi32.DeleteObject(font)
        gdi32.DeleteDC(dc)


def emf_text_runs(data):
    """(face, weight, italic, em height) and per-character advances of each ExtTextOutW record."""
    fonts, current, runs, at = {}, None, [], 0
    while at + 8 <= len(data):
        kind, size = struct.unpack_from('<II', data, at)
        if size < 8 or at + size > len(data):
            break
        if kind == 82 and size >= 104:  # EMR_EXTCREATEFONTINDIRECTW: ihFont, LOGFONTW
            handle = struct.unpack_from('<I', data, at + 8)[0]
            height, _, _, _, weight = struct.unpack_from('<iiiii', data, at + 12)
            face = data[at + 40:at + 104].decode('utf-16-le', 'replace').split('\0')[0]
            fonts[handle] = (face, weight or 400, bool(data[at + 32]), abs(height))
        elif kind == 37:  # EMR_SELECTOBJECT; stock and non-font objects are not fonts
            handle = struct.unpack_from('<I', data, at + 8)[0]
            if handle in fonts:
                current = fonts[handle]
        elif kind == 84 and current is not None and size >= 76:  # EMR_EXTTEXTOUTW
            count, offset, options = struct.unpack_from('<III', data, at + 44)
            dx_offset = struct.unpack_from('<I', data, at + 72)[0]
            step = 2 if options & 0x2000 else 1  # ETO_PDY stores (dx, dy) pairs
            if (count and dx_offset and not options & 0x10  # glyph-index runs carry no characters
                    and dx_offset + 4 * count * step <= size):
                string = data[at + offset:at + offset + 2 * count].decode('utf-16-le', 'replace')
                advances = struct.unpack_from(f'<{count * step}i', data, at + dx_offset)[::step]
                runs.append((current, string, advances))
        at += size
    return runs


def faces_from_emf(data, faces=system_faces, substitute=None, tolerance=.03):
    """Map (family, weight, italic) to the installed face whose advances ChemDraw used."""
    samples = {}
    for (face, weight, italic, height), string, advances in emf_text_runs(data):
        if height and string.strip():
            samples.setdefault((face, weight, italic), []).append((string, sum(advances) / height))
    mapping = {}
    for (face, weight, italic), runs in samples.items():
        candidates = faces(face)
        if not candidates and substitute is not None:
            replacement = substitute(face)
            candidates = faces(replacement) if replacement else []
        best = None
        for candidate in candidates:
            try:
                predicted = [candidate.advance(string) for string, _ in runs]
            except (OSError, struct.error, KeyError, IndexError, ZeroDivisionError):
                continue
            total = sum(predicted)
            if total <= 0:
                continue
            error = sum(abs(observed - value) for (_, observed), value in zip(runs, predicted)) / total
            rank = (error, abs(candidate.weight - weight), candidate.italic != italic)
            if best is None or rank < best[0]:
                best = (rank, candidate)
        if best is not None and best[0][0] <= tolerance:
            mapping[(face.lower(), weight, italic)] = best[1]
    return mapping


def recorded_faces(svg_text, emf, faces=system_faces, substitute=None):
    """Faces for the SVG's own font keys, from ChemDraw's metafile of the same objects.

    Usually the SVG family is the metafile face name. Windows ChemDraw can draw a family with
    another face, e.g. SVG font-family="@MS Gothic" drawn as Microsoft Sans Serif; such SVG keys
    are linked through identical text runs and use the fitted face of that metafile family, or
    its installed face nearest in weight when no face fits (e.g. a synthetic GDI bold).
    """
    runs = emf_text_runs(emf)
    fitted = faces_from_emf(emf, faces, substitute)
    result = {}
    strings = {}
    for run in _runs(SafeET.fromstring(svg_text, forbid_entities=True, forbid_external=True)):
        family_list, weight, style = run['key']
        family = family_list.split(',')[0].strip().strip('\'"')
        declared = _declared_weight(weight)
        if not family or declared is None:
            continue
        key = (family.lower(), declared, style in ('italic', 'oblique'))
        if key in fitted:
            result[key] = fitted[key]
        elif run.get('text'):
            strings.setdefault(key, set()).add(run['text'])
    for key, texts in strings.items():
        drawn = {(face.lower(), weight, italic) for (face, weight, italic, _), string, _ in runs
                 if string in texts and weight == key[1] and italic == key[2]}
        if len(drawn) != 1:
            continue
        source = drawn.pop()
        target = fitted.get(source)
        if target is None:
            candidates = [f for f in faces(source[0]) if f.italic == source[2]] or faces(source[0])
            target = min(candidates, key=lambda f: (abs(f.weight - source[1]), f.weight), default=None)
        if target is not None:
            result[key] = target
    return result


_COMMENT = re.compile(r'<!--\s*chemdraw-mcp-faces\s+(\[.*?\])\s*-->', re.S)


def annotate_faces(svg_text, mapping):
    """Record measured faces as an SVG comment; rendering and geometry are unchanged."""
    if not mapping:
        return svg_text
    entries = sorted([key[0], key[1], key[2], face.family, face.weight, face.italic] for key, face in mapping.items())
    payload = json.dumps(entries, ensure_ascii=True)
    root = re.search(r'<svg\b[^>]*>', svg_text)
    if root is None or '--' in payload:
        return svg_text
    return svg_text[:root.end()] + f'<!-- chemdraw-mcp-faces {payload} -->' + svg_text[root.end():]


def _recorded(svg_text):
    match = _COMMENT.search(svg_text)
    if not match:
        return {}
    try:
        return {(str(f).lower(), int(w), bool(i)): (str(face), int(weight), bool(italic))
                for f, w, i, face, weight, italic in json.loads(match[1])}
    except (ValueError, TypeError):
        return {}


def _declared_weight(value):
    value = (value or 'normal').strip()
    if value == 'normal':
        return 400
    if value == 'bold':
        return 700
    return int(value) if value.isdigit() else None


def _runs(root):
    """Text elements in document order with inherited font attributes and their origin."""
    runs = []

    def walk(element, inherited, parent):
        attrs = dict(inherited)
        for name in _FONT_ATTRS:
            if element.get(name) is not None:
                attrs[name] = element.get(name)
        if element.tag == _TEXT:
            match = _MATRIX.match(element.get('transform') or 'matrix(1 0 0 1 0 0)')
            values = [float(v) for v in re.split(r'[\s,]+', match[1].strip())] if match else None
            size = re.fullmatch(r'\s*([0-9.]+)(?:px)?\s*', attrs.get('font-size') or '')
            if values and len(values) == 6 and size:
                a, b, c, d, e, f = values
                x, y = float(element.get('x') or 0), float(element.get('y') or 0)
                runs.append({'element': element, 'parent': parent, 'scale': (a, b, c, d),
                             'origin': (a * x + c * y + e, b * x + d * y + f), 'size': float(size[1]),
                             'key': (attrs.get('font-family') or '', attrs.get('font-weight') or 'normal',
                                     attrs.get('font-style') or 'normal'),
                             'text': ''.join(element.itertext()).lstrip('\r\n')})
            else:
                runs.append({'element': element, 'parent': parent, 'scale': None,
                             'key': (attrs.get('font-family') or '', attrs.get('font-weight') or 'normal',
                                     attrs.get('font-style') or 'normal')})
            return
        for child in element:
            walk(child, attrs, element)

    walk(root, {}, None)
    return runs


def _evidence(runs):
    pairs = {}
    for first, second in zip(runs, runs[1:]):
        if (first['scale'] is None or second['scale'] is None or first['parent'] is not second['parent']
                or first['key'] != second['key'] or first['size'] != second['size'] or first['scale'] != second['scale']
                or not first['text']):
            continue
        a, b, c, d = first['scale']
        if b or c or a <= 0 or d <= 0:
            continue
        em = a * first['size']
        dx = second['origin'][0] - first['origin'][0]
        if abs(second['origin'][1] - first['origin'][1]) > 1e-3 * em or dx <= 0:
            continue
        pairs.setdefault(first['key'], []).append((first['text'], dx / em))
    return pairs


def match_native_faces(svg_text, faces=system_faces, substitute=gdi_substitute):
    """Return svg_text with text runs pointed at the faces that reproduce ChemDraw's layout."""
    root = SafeET.fromstring(svg_text, forbid_entities=True, forbid_external=True)
    runs = _runs(root)
    if not runs:
        return svg_text
    evidence = _evidence(runs)
    recorded = _recorded(svg_text)
    chosen = {}
    for key in {run['key'] for run in runs}:
        family_list, weight, style = key
        family = family_list.split(',')[0].strip().strip('\'"')
        declared = _declared_weight(weight)
        if not family or declared is None:
            continue
        exact = recorded.get((family.lower(), declared, style in ('italic', 'oblique')))
        if exact is not None:
            chosen[key] = exact
            continue
        candidates = faces(family)
        installed = bool(candidates)
        if not installed and substitute is not None:
            replacement = substitute(family)
            candidates = faces(replacement) if replacement else []
        if not candidates:
            continue
        italic = style in ('italic', 'oblique')
        closeness = lambda face: (abs(face.weight - declared), face.italic != italic, face.weight)
        best = None
        pairs = evidence.get(key, [])
        if pairs:
            scores = {}
            for face in candidates:
                try:
                    scores[face] = sum(abs(observed - face.advance(string)) <= TOLERANCE * face.advance(string)
                                       for string, observed in pairs)
                except (OSError, struct.error, KeyError, IndexError, ZeroDivisionError):
                    scores[face] = 0
            top = max(scores.values())
            if top:
                best = min((face for face in candidates if scores[face] == top), key=closeness)
        if best is None and not installed:
            best = min(candidates, key=closeness)
        if best is not None:
            chosen[key] = (best.family, best.weight, best.italic)
    if not chosen:
        return svg_text
    for run in runs:
        target = chosen.get(run['key'])
        if target is not None:
            family, weight, italic = target
            run['element'].set('font-family', family)
            run['element'].set('font-weight', str(weight))
            run['element'].set('font-style', 'italic' if italic else 'normal')
    ET.register_namespace('', _NS)
    return ET.tostring(root, encoding='unicode')
