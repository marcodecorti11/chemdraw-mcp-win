"""Windows branches of the graphical setup protocol (portable: the platform switch is patched)."""
import json
import sys
from pathlib import Path

import pytest

from chemdraw_macos import desktop_setup as setup


@pytest.fixture
def windows(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, 'WINDOWS', True)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'Local'))
    monkeypatch.setenv('APPDATA', str(tmp_path / 'Roaming'))
    return tmp_path


def fake_exe(tmp_path, monkeypatch, product='ChemDraw', company='Revvity', original='ChemDraw.exe'):
    from chemdraw_macos import windows_native
    exe = tmp_path / 'Program Files' / 'ChemDraw.exe'
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b'MZ')
    monkeypatch.setattr(windows_native, 'app_metadata', lambda path: {
        'ProductName': product, 'CompanyName': company, 'OriginalFilename': original, 'version': '26.1.0.6327'})
    return exe


def test_settings_live_in_the_per_user_windows_folder(windows):
    assert setup.settings_path() == windows / 'Local' / 'ChemDraw MCP' / 'desktop-setup.json'


def test_windows_app_selection_accepts_chemdraw_exe_or_its_folder(windows, monkeypatch):
    exe = fake_exe(windows, monkeypatch)
    assert setup.validate_app(str(exe)) == exe.resolve()
    assert setup.validate_app(str(exe.parent)) == exe.resolve()


@pytest.mark.parametrize('product,company,original', [('Notepad', 'Microsoft', 'notepad.exe'),
                                                      ('ChemDraw', 'Somebody', 'ChemDraw.exe')])
def test_windows_app_selection_refuses_other_programs(windows, monkeypatch, product, company, original):
    exe = fake_exe(windows, monkeypatch, product, company, original)
    with pytest.raises(ValueError, match='ChemDraw'):
        setup.validate_app(str(exe))


def test_windows_messages_have_no_addin_step(windows):
    local = setup.present_diagnostic({'status': 'local_ready'})
    assert 'add-in' not in local['message'].lower() and 'Test connection' in local['message']
    document = setup.present_diagnostic({'status': 'needs_document'})
    assert 'File > New' in document['message'] and 'Test connection' in document['message']
    stopped = setup.present_diagnostic({'status': 'unavailable', 'error': 'ChemDraw is not running. Start ChemDraw, then retry.'})
    assert stopped['title'] == 'Start ChemDraw' and stopped['details']['failure']['kind'] == 'chemdraw_not_running'


def test_windows_diagnostics_keep_the_windows_version_and_omit_paths(windows):
    details = setup.diagnostic_details({'windows': '10.0.26300', 'app': 'C:\\Users\\me\\x', 'status': 'ready'})
    assert details['windows'] == '10.0.26300' and 'app' not in details


def test_windows_prepare_needs_no_addin_and_installer_export_is_refused(windows, monkeypatch):
    from chemdraw_macos import addin
    monkeypatch.setattr(addin, 'get_backend', lambda *a, **k: pytest.fail('add-in prepared on Windows'))
    session = setup.SetupSession(settings_path=windows / 'settings.json')
    assert session.dispatch({'action': 'prepare'}) == {'status': 'prepared', 'addin_required': False}
    with pytest.raises(ValueError, match='No ChemDraw add-in is needed on Windows'):
        session.dispatch({'action': 'export_installer', 'path': str(windows / 'x.chemdrawaddin')})


def test_windows_client_installation_uses_the_frozen_runtime_folder(windows, monkeypatch):
    from chemdraw_macos import windows_install
    runtime = windows / 'download' / 'ChemDraw MCP' / 'chemdraw-runtime.exe'
    calls = []
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(runtime))
    monkeypatch.setattr(windows_install, 'install_and_connect',
                        lambda source, clients: calls.append((Path(source), clients)) or {'clients': clients, 'installed_runtime': 'x'})
    session = setup.SetupSession(settings_path=windows / 'settings.json')
    assert session.install_clients(['claude'])['clients'] == ['claude']
    assert calls == [(runtime.parent, ['claude'])]


def test_windows_client_installation_requires_the_packaged_runtime(windows, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    session = setup.SetupSession(settings_path=windows / 'settings.json')
    with pytest.raises(ValueError, match='packaged Windows setup'):
        session.install_clients(['claude'])


def test_windows_desktop_serve_runs_in_place_without_reexec_or_launching(windows, monkeypatch):
    import os
    from chemdraw_macos import server
    # runtime_main sets these for the server process; register them so the test restores them.
    monkeypatch.setenv('CHEMDRAW_DESKTOP_EXTENSION', 'unset-by-test')
    monkeypatch.setenv('CHEMDRAW_APP', 'unset-by-test')
    served = []
    monkeypatch.setattr(os, 'execv', lambda *a: pytest.fail('re-exec on Windows'))
    monkeypatch.setattr(setup.subprocess, 'run', lambda *a, **k: pytest.fail('launched a program'))
    monkeypatch.setattr(server, 'main', lambda argv: served.append((argv, os.environ.get('CHEMDRAW_DESKTOP_EXTENSION'))))
    path = setup.settings_path(); path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'setup_complete': True, 'installed_app': 'C:\\elsewhere'}), encoding='utf-8')
    assert setup.runtime_main(['--desktop-serve']) == 0
    assert served == [(['--profile', 'full'], '1')]


def test_windows_setup_gui_entry_point(windows, monkeypatch):
    from chemdraw_macos import windows_setup_gui
    monkeypatch.setattr(windows_setup_gui, 'main', lambda: 7)
    assert setup.runtime_main(['--setup-gui']) == 7


def test_packaged_runtime_reports_its_build_version(tmp_path, monkeypatch):
    from chemdraw_macos import diagnostics
    exe = tmp_path / 'ChemDraw MCP' / 'chemdraw-runtime.exe'
    exe.parent.mkdir()
    (exe.parent / 'version.json').write_text(json.dumps({'product': 'chemdraw-mcp-windows', 'version': '0.10.0rc22-win.2'}),
                                             encoding='utf-8')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(exe))
    assert diagnostics.runtime_info() == {'mode': 'packaged', 'restart_required': False, 'build': '0.10.0rc22-win.2'}
