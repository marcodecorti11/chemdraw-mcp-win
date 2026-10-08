"""Windows per-user installation: versioned runtime, stable junction, launchers, PATH, clients."""
import json
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows installation layout')


def runtime(tmp_path, version, marker='a'):
    source = tmp_path / f'staging-{version}' / 'ChemDraw MCP'
    source.mkdir(parents=True)
    (source / 'chemdraw-runtime.exe').write_bytes(b'MZ' + marker.encode())
    (source / '_internal').mkdir()
    (source / '_internal' / 'lib.dll').write_bytes(b'x')
    (source / 'version.json').write_text(json.dumps({'product': 'chemdraw-mcp-windows', 'version': version}),
                                         encoding='utf-8')
    return source


@pytest.fixture
def env(tmp_path, monkeypatch):
    from chemdraw_macos import windows_install as wi
    home = tmp_path / 'home'
    paths = wi.Paths(home=home)
    store = {}
    monkeypatch.setattr(wi, '_read_user_path', lambda: store.get('Path'))
    monkeypatch.setattr(wi, '_write_user_path', lambda value: store.__setitem__('Path', value))
    monkeypatch.setattr(wi, '_broadcast_environment_change', lambda: None)
    return wi, paths, home, store


def test_paths_follow_windows_profile_folders(env):
    wi, paths, home, _ = env
    assert paths.support == home / 'AppData/Local/ChemDraw MCP'
    assert paths.claude_config == home / 'AppData/Roaming/Claude/claude_desktop_config.json'
    assert paths.codex_config == home / '.codex/config.toml'
    assert paths.runtime == paths.current / 'chemdraw-runtime.exe'


def test_fresh_install_creates_versioned_copy_junction_launchers_path_and_clients(env, tmp_path):
    wi, paths, home, store = env
    store['Path'] = r'C:\Tools;%USERPROFILE%\bin'
    result = wi.install_and_connect(runtime(tmp_path, '0.10.0rc22-win.1'), ['claude', 'codex'], paths=paths)
    installed = paths.versions / '0.10.0rc22-win.1' / 'ChemDraw MCP'
    assert (installed / 'chemdraw-runtime.exe').read_bytes() == b'MZa'
    assert paths.current.is_junction() and paths.current.resolve() == installed.resolve()
    assert (paths.bin / 'chemdraw-mac.cmd').read_text(encoding='utf-8').strip().endswith('--cli %*')
    assert str(paths.runtime) in (paths.bin / 'chemdraw-mcp-macos.cmd').read_text(encoding='utf-8')
    assert store['Path'] == r'C:\Tools;%USERPROFILE%\bin;' + str(paths.bin)
    claude = json.loads(paths.claude_config.read_text(encoding='utf-8'))
    assert claude['mcpServers']['glecko_chemdraw'] == {'command': str(paths.runtime), 'args': ['--desktop-serve']}
    assert 'glecko_chemdraw' in paths.codex_config.read_text(encoding='utf-8')
    assert result['installed_runtime'] == str(installed)


def test_update_switches_junction_and_preserves_client_settings_and_path(env, tmp_path):
    wi, paths, home, store = env
    paths.claude_config.parent.mkdir(parents=True)
    paths.claude_config.write_text(json.dumps({'mcpServers': {'other': {'command': 'x'}}, 'theme': 'dark'}),
                                   encoding='utf-8')
    wi.install_and_connect(runtime(tmp_path, '0.10.0rc22-win.1', 'a'), ['claude'], paths=paths)
    config_after_first = paths.claude_config.read_bytes()
    path_after_first = store['Path']
    wi.install_and_connect(runtime(tmp_path, '0.10.0rc22-win.2', 'b'), ['claude'], paths=paths)
    assert paths.current.resolve() == (paths.versions / '0.10.0rc22-win.2' / 'ChemDraw MCP').resolve()
    assert (paths.runtime).read_bytes() == b'MZb'
    assert (paths.versions / '0.10.0rc22-win.1' / 'ChemDraw MCP' / 'chemdraw-runtime.exe').is_file()  # never removed
    assert paths.claude_config.read_bytes() == config_after_first      # stable command: nothing rewritten
    assert json.loads(config_after_first)['mcpServers']['other'] == {'command': 'x'}
    assert store['Path'] == path_after_first                           # not duplicated


def test_reinstalling_same_version_with_different_bytes_is_refused(env, tmp_path):
    wi, paths, home, _ = env
    wi.install_and_connect(runtime(tmp_path, '0.10.0rc22-win.1', 'a'), [], paths=paths)
    other = runtime(tmp_path / 'x', '0.10.0rc22-win.1', 'changed')
    with pytest.raises(ValueError, match='differs'):
        wi.install_and_connect(other, [], paths=paths)


def test_client_failure_rolls_back_launchers_path_and_junction(env, tmp_path, monkeypatch):
    wi, paths, home, store = env
    store['Path'] = r'C:\Tools'
    def fail(*a, **k):
        raise ValueError('client settings unreadable')
    monkeypatch.setattr(wi, 'connect_clients', fail)
    with pytest.raises(ValueError, match='unreadable'):
        wi.install_and_connect(runtime(tmp_path, '0.10.0rc22-win.1'), ['claude'], paths=paths)
    assert store['Path'] == r'C:\Tools'
    assert not (paths.bin / 'chemdraw-mac.cmd').exists()
    assert not os.path.lexists(paths.current)


def test_foreign_current_directory_is_never_replaced(env, tmp_path):
    wi, paths, home, _ = env
    paths.current.mkdir(parents=True)
    (paths.current / 'keep.txt').write_text('user data', encoding='utf-8')
    with pytest.raises(ValueError, match='not a ChemDraw MCP junction'):
        wi.install_and_connect(runtime(tmp_path, '0.10.0rc22-win.1'), [], paths=paths)
    assert (paths.current / 'keep.txt').read_text(encoding='utf-8') == 'user data'


def test_unrecognized_runtime_folder_is_refused(env, tmp_path):
    wi, paths, home, _ = env
    bogus = tmp_path / 'bogus'
    bogus.mkdir()
    with pytest.raises(ValueError, match='ChemDraw MCP runtime'):
        wi.install_and_connect(bogus, [], paths=paths)
