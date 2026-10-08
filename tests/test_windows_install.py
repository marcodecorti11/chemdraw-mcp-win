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


def test_gemini_cli_entry_is_added_once_and_other_settings_are_kept(env, tmp_path):
    wi, paths, home, store = env
    assert paths.gemini_config == home / '.gemini/settings.json'
    paths.gemini_config.parent.mkdir(parents=True)
    paths.gemini_config.write_text(json.dumps({'ui': {'theme': 'dark'}, 'mcpServers': {'other': {'command': 'x'}}}),
                                   encoding='utf-8')
    first = wi.install_and_connect(runtime(tmp_path, '0.10.0rc22-win.1'), ['gemini'], paths=paths)
    data = json.loads(paths.gemini_config.read_text(encoding='utf-8'))
    assert data['ui'] == {'theme': 'dark'} and data['mcpServers']['other'] == {'command': 'x'}
    assert data['mcpServers']['glecko_chemdraw'] == {'command': str(paths.runtime), 'args': ['--desktop-serve'],
                                                     'timeout': 300000}
    assert 'gemini' in first['clients'] and len(first['backups']) == 1
    before = paths.gemini_config.read_bytes()
    second = wi.install_and_connect(runtime(tmp_path, '0.10.0rc22-win.2', 'b'), ['gemini'], paths=paths)
    assert paths.gemini_config.read_bytes() == before and second['backups'] == []


def fake_claude(paths, calls, *, accept=True):
    def run(argv):
        calls.append(argv)
        if accept:
            data = json.loads(paths.claude_code_config.read_text(encoding='utf-8')) if paths.claude_code_config.exists() else {}
            data.setdefault('mcpServers', {})[argv[5]] = {'type': 'stdio', 'command': argv[7], 'args': argv[8:], 'env': {}}
            paths.claude_code_config.parent.mkdir(parents=True, exist_ok=True)
            paths.claude_code_config.write_text(json.dumps(data), encoding='utf-8')
            return 0, 'Added'
        return 1, 'refused'
    return run


def test_claude_code_is_registered_through_its_own_cli(env, tmp_path, monkeypatch):
    wi, paths, home, store = env
    calls = []
    monkeypatch.setattr(wi, '_find_claude_cli', lambda: r'C:\npm\claude.cmd')
    monkeypatch.setattr(wi, '_run_claude', fake_claude(paths, calls))
    result = wi.install_and_connect(runtime(tmp_path, '0.10.0rc22-win.1'), ['claude-code'], paths=paths)
    assert calls == [[r'C:\npm\claude.cmd', 'mcp', 'add', '--scope', 'user', 'glecko_chemdraw', '--',
                      str(paths.runtime), '--desktop-serve']]
    assert result['claude_code']['status'] == 'added' and 'claude-code' in result['clients']
    # An identical entry is left alone on update: no second CLI call.
    again = wi.install_and_connect(runtime(tmp_path, '0.10.0rc22-win.2', 'b'), ['claude-code'], paths=paths)
    assert len(calls) == 1 and again['claude_code']['status'] == 'unchanged'


def test_claude_code_entry_configured_differently_is_not_overwritten(env, tmp_path, monkeypatch):
    wi, paths, home, store = env
    paths.claude_code_config.parent.mkdir(parents=True)
    paths.claude_code_config.write_text(json.dumps({'mcpServers': {'glecko_chemdraw': {'command': 'other.exe', 'args': []}}}),
                                        encoding='utf-8')
    calls = []
    monkeypatch.setattr(wi, '_find_claude_cli', lambda: r'C:\npm\claude.cmd')
    monkeypatch.setattr(wi, '_run_claude', fake_claude(paths, calls))
    result = wi.install_and_connect(runtime(tmp_path, '0.10.0rc22-win.1'), ['claude-code'], paths=paths)
    assert calls == [] and result['claude_code']['status'] == 'conflict'
    assert 'claude-code' not in result['clients']
    assert json.loads(paths.claude_code_config.read_text(encoding='utf-8'))['mcpServers']['glecko_chemdraw']['command'] == 'other.exe'


def test_missing_claude_cli_gives_the_exact_manual_command(env, tmp_path, monkeypatch):
    wi, paths, home, store = env
    monkeypatch.setattr(wi, '_find_claude_cli', lambda: None)
    monkeypatch.setattr(wi, '_run_claude', lambda argv: pytest.fail('no CLI must not be run'))
    result = wi.install_and_connect(runtime(tmp_path, '0.10.0rc22-win.1'), ['claude-code'], paths=paths)
    assert result['claude_code']['status'] == 'manual'
    assert result['claude_code']['command'] == f'claude mcp add --scope user glecko_chemdraw -- "{paths.runtime}" --desktop-serve'
    assert paths.current.is_junction()  # the installation itself still completed


def test_claude_cli_failure_is_reported_without_undoing_the_installation(env, tmp_path, monkeypatch):
    wi, paths, home, store = env
    calls = []
    monkeypatch.setattr(wi, '_find_claude_cli', lambda: r'C:\npm\claude.cmd')
    monkeypatch.setattr(wi, '_run_claude', fake_claude(paths, calls, accept=False))
    result = wi.install_and_connect(runtime(tmp_path, '0.10.0rc22-win.1'), ['claude', 'claude-code'], paths=paths)
    assert len(calls) == 1 and result['claude_code']['status'] == 'failed'  # never retried
    assert 'manual command' not in result['claude_code'] or result['claude_code']['command']
    assert result['clients'] == ['claude'] and paths.current.is_junction()


def test_unknown_assistant_is_refused_before_any_change(env, tmp_path):
    wi, paths, home, store = env
    with pytest.raises(ValueError):
        wi.install_and_connect(runtime(tmp_path, '0.10.0rc22-win.1'), ['claude', 'cursor'], paths=paths)
    assert not paths.support.exists()
