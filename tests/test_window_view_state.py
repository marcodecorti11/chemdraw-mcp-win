"""Window-only view state must not count as a content change.

Measured on Windows ChemDraw 26.1: activating or closing another document rewrites exactly
WindowPosition, WindowSize and WindowIsZoomed in an untouched document's CDXML.
"""
import xml.etree.ElementTree as ET

BASE = ('<CDXML BondLength="18" WindowPosition="0 0" WindowSize="-2147483648 536870912" WindowIsZoomed="yes">'
        '<page id="1" BoundingBox="0 0 540 720"><fragment id="2" BoundingBox="70 40 110 60">'
        '<n id="3" p="80 50"/><n id="4" p="98 50" Element="8"/><b id="5" B="3" E="4"/></fragment></page></CDXML>')
MOVED = (BASE.replace('WindowPosition="0 0"', 'WindowPosition="-1073741824 -1610612736"')
             .replace('WindowSize="-2147483648 536870912"', 'WindowSize="536870912 -2147483648"')
             .replace(' WindowIsZoomed="yes"', ''))
EDITED = BASE.replace('p="98 50"', 'p="99 50"')


def test_workflow_content_fingerprint_ignores_view_state_only():
    from chemdraw_macos.workflow import content_fingerprint
    assert content_fingerprint(BASE) == content_fingerprint(MOVED)
    assert content_fingerprint(BASE) != content_fingerprint(EDITED)


def test_shared_fingerprint_ignores_view_state_only():
    from chemdraw_macos.shared import fingerprint
    assert fingerprint(BASE) == fingerprint(MOVED)
    assert fingerprint(BASE) != fingerprint(EDITED)


def test_live_content_ignores_view_state_only():
    from chemdraw_macos.live import _content
    assert _content(BASE)[1] == _content(MOVED)[1]
    assert _content(BASE)[1] != _content(EDITED)[1]


def test_editing_source_token_ignores_view_state_only():
    from chemdraw_macos.editing import source_token
    assert source_token(BASE) == source_token(MOVED)
    assert source_token(BASE) != source_token(EDITED)
