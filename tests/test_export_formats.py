"""Platform export formats: Windows ChemDraw has no PDF export; refusals precede native work."""
import sys

import pytest

from chemdraw_macos import core
from chemdraw_macos.batch import batch_export
from test_batch import BatchBridge, entries
from test_live_document import XML, Fake

NO_PDF = ('cdxml', 'svg', 'png', 'cdx')


def test_bridge_declares_platform_export_formats():
    # The class default is core.EXPORT_FORMATS (simulated-native tests pin the macOS list; conftest).
    expected = NO_PDF if sys.platform == 'win32' else ('cdxml', 'svg', 'png', 'pdf', 'cdx')
    assert core.EXPORT_FORMATS == expected
    import inspect
    assert 'export_formats=EXPORT_FORMATS' in inspect.getsource(core.Bridge)


def test_unavailable_format_is_refused_before_any_native_call(tmp_path, monkeypatch):
    bridge = core.Bridge.__new__(core.Bridge)
    bridge.export_formats = NO_PDF
    calls = []
    monkeypatch.setattr(core.Bridge, '_run', lambda self, *a: calls.append(a), raising=False)
    with pytest.raises(ValueError, match='PDF export is not available'):
        bridge.export(1, str(tmp_path / 'figure.pdf'), 'pdf')
    assert calls == [] and not (tmp_path / 'figure.pdf').exists()


def test_batch_refuses_unavailable_format_before_output_or_native_calls(tmp_path):
    bridge = BatchBridge(tmp_path / 'work')
    bridge.export_formats = NO_PDF
    with pytest.raises(ValueError, match='PDF export is not available'):
        batch_export(bridge, entries(tmp_path), str(tmp_path / 'out'))
    assert not bridge.events and not (tmp_path / 'out').exists()


def test_batch_without_unavailable_formats_still_exports(tmp_path):
    bridge = BatchBridge(tmp_path / 'work')
    bridge.export_formats = NO_PDF
    items = entries(tmp_path)
    for item in items:
        item['formats'] = ['cdx']
    assert batch_export(bridge, items, str(tmp_path / 'out'), pixels=1200)['status'] == 'completed'


class Render(Fake):
    def create(self, text, visible=True):
        self.calls.append(('create', visible))
        return {'document': {'document_id': 12}}

    def export(self, did, path, fmt, pixels=3200):
        self.calls.append(('export', fmt))
        open(path, 'w', encoding='utf-8').write('x')

    def close(self, did):
        self.calls.append(('close', did))
        return {}


def test_render_exports_only_available_formats_and_records_the_rest(tmp_path):
    from chemdraw_macos.live import render_cdxml
    bridge = Render(tmp_path)
    bridge.export_formats = NO_PDF
    result = render_cdxml(bridge, XML, str(tmp_path / 'output'), background=True)
    assert [c[1] for c in bridge.calls if c[0] == 'export'] == ['cdxml', 'svg', 'png']
    assert result['unavailable_formats'] == ['pdf'] and 'pdf' not in result['artifacts']
    assert bridge.calls[-1] == ('close', 12) and result['document_closed'] is True


def test_render_keeps_pdf_where_the_platform_exports_it(tmp_path):
    from chemdraw_macos.live import render_cdxml
    bridge = Render(tmp_path)  # portable fakes declare no restriction: macOS behaviour
    result = render_cdxml(bridge, XML, str(tmp_path / 'output'), background=True)
    assert [c[1] for c in bridge.calls if c[0] == 'export'] == ['cdxml', 'svg', 'png', 'pdf']
    assert 'unavailable_formats' not in result


def test_figure_pdf_request_is_refused_before_reading_or_writing(tmp_path):
    from chemdraw_macos.physical_export import export_figure

    import threading

    class Bridge:
        export_formats = NO_PDF
        lock = threading.RLock()  # the transaction lock is taken first; it is not a native call

        def __getattr__(self, name):
            raise AssertionError(f'native access {name} before refusal')

    with pytest.raises(ValueError, match='PDF export is not available'):
        export_figure(Bridge(), 123, str(tmp_path / 'out'), dpi=300, include_pdf=True)
    assert not (tmp_path / 'out').exists()
