"""Platform-aware setup for opt-in native tests. Only ever creates test-owned documents."""
import subprocess
import sys

WINDOWS = sys.platform == 'win32'

# examples/sn2-annotation-input.cdxml (ChemDraw 23) places both circled charges where ChemDraw itself
# flags "Chemical symbol is not associated with an atom." ChemDraw 23 keeps the charge; Windows
# ChemDraw 26.1 drops the ion charge and the symbol association on import, so the product correctly
# refuses the document. These are the associated positions ChemDraw 26.1 chose for the same ions
# through chemdraw_add_symbols; chemistry, IDs and every other object are unchanged.
SN2_ASSOCIATED_SYMBOL_BOXES = {'42.70 86 32.20 86': '14.95 97.46 4.45 97.46',
                               '344.98 86 334.48 86': '320.42 95.67 309.92 95.67'}


def importable_sn2(cdxml):
    """The SN2 example as ChemDraw 26.1 on Windows can import it; unchanged on macOS."""
    if not WINDOWS:
        return cdxml
    for old, new in SN2_ASSOCIATED_SYMBOL_BOXES.items():
        assert cdxml.count(f'BoundingBox="{old}"') == 1
        cdxml = cdxml.replace(f'BoundingBox="{old}"', f'BoundingBox="{new}"')
    return cdxml


def new_untitled_document():
    """Create a new untitled ChemDraw document owned by the test and return its native ID."""
    if not WINDOWS:
        return int(subprocess.check_output(
            ['osascript', '-e', 'tell application "ChemDraw 23.0.1" to get id of (make new document)'], text=True))
    import pythoncom
    import win32com.client
    from chemdraw_macos import windows_native as wn
    pythoncom.CoInitialize()
    app = win32com.client.GetActiveObject(wn.PROGID)
    doc = app.Documents.Add()
    # The user's default template may be multi-page; the shared-append contract needs one page,
    # which is what macOS 'make new document' gives. Only this owned test document is resized.
    doc.NumPagesWide = 1
    doc.NumPagesHigh = 1
    doc.Activate()  # A normal, persistent window (an unactivated document vanishes on release).
    return wn.document_id(wn._pid(app), wn._key(doc))


def focus_elsewhere():
    """macOS: bring Finder forward to prove background operation. Windows COM needs no focus."""
    if not WINDOWS:
        subprocess.run(['osascript', '-e', 'tell application "Finder" to activate'], check=True)


def foreground_name():
    if WINDOWS:
        return None
    return subprocess.check_output(['osascript', '-e', 'tell application "System Events" to get name of '
                                    'first application process whose frontmost is true'], text=True).strip()


def assert_background(foreground):
    """macOS asserts Finder kept focus; Windows has no equivalent focus requirement to check."""
    if not WINDOWS:
        assert foreground == 'Finder'
