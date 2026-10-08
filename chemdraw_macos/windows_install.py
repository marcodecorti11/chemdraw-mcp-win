"""Per-user Windows installation of the bundled runtime; the counterpart of install_and_connect.

Layout (no administrator rights):
  %LOCALAPPDATA%\\ChemDraw MCP\\versions\\<version>\\ChemDraw MCP\\   immutable runtime copies
  %LOCALAPPDATA%\\ChemDraw MCP\\current                            junction -> the selected version
  %LOCALAPPDATA%\\ChemDraw MCP\\bin\\chemdraw-mac.cmd ...            terminal launchers (user PATH)
Assistant settings reference current\\chemdraw-runtime.exe, so an update switches the junction and
leaves client settings untouched. Earlier versions are never removed. Any failure rolls back.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from .client_install import _atomic, _regular, connect_clients

PRODUCT = 'chemdraw-mcp-windows'
RUNTIME = 'chemdraw-runtime.exe'


class Paths:
    def __init__(self, home=None):
        if home is None:
            local = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local')
            roaming = Path(os.environ.get('APPDATA') or Path.home() / 'AppData/Roaming')
            profile = Path.home()
        else:
            profile = Path(home)
            local, roaming = profile / 'AppData/Local', profile / 'AppData/Roaming'
        self.support = local / 'ChemDraw MCP'
        self.versions = self.support / 'versions'
        self.current = self.support / 'current'
        self.bin = self.support / 'bin'
        self.logs = self.support / 'Logs'
        self.settings = self.support / 'desktop-setup.json'
        self.runtime = self.current / RUNTIME
        self.claude_config = roaming / 'Claude/claude_desktop_config.json'
        self.claude_manifest = roaming / 'Claude/Claude Extensions/local.mcpb.glenn-bojanov.chemdraw-macos/manifest.json'
        self.codex_config = profile / '.codex/config.toml'


def runtime_version(source):
    source = Path(source)
    try:
        info = json.loads((source / 'version.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('Not a recognized ChemDraw MCP runtime folder') from exc
    version = str(info.get('version', ''))
    if (info.get('product') != PRODUCT or not re.fullmatch(r'[0-9][0-9A-Za-z.\-]{0,63}', version)
            or not (source / RUNTIME).is_file()):
        raise ValueError('Not a recognized ChemDraw MCP runtime folder')
    return version


def _tree_digest(folder):
    digest = hashlib.sha256()
    for path in sorted(p for p in Path(folder).rglob('*') if p.is_file()):
        digest.update(str(path.relative_to(folder)).replace('\\', '/').encode('utf-8') + b'\0')
        with path.open('rb') as handle:
            for block in iter(lambda: handle.read(1 << 20), b''):
                digest.update(block)
    return digest.hexdigest()


def install_runtime(source, paths):
    """Copy the runtime into versions/<version>; reuse an identical copy, refuse a differing one."""
    source = Path(source).resolve()
    version = runtime_version(source)
    target = paths.versions / version / 'ChemDraw MCP'
    if any(p.is_symlink() or p.is_junction() for p in (target, *target.parents) if os.path.lexists(p)):
        raise ValueError('Refusing links in the installation path')
    if target.exists():
        if _tree_digest(target) != _tree_digest(source):
            raise ValueError(f'An installation of {version} already exists but differs; nothing was changed')
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.install-', dir=target.parent))
    try:
        shutil.copytree(source, staging / target.name, symlinks=False)
        os.rename(staging / target.name, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target


def _junction_target(link, paths):
    if not os.path.lexists(link):
        return None
    if not Path(link).is_junction():
        raise ValueError(f'{link} exists but is not a ChemDraw MCP junction; nothing was changed')
    target = Path(os.readlink(link))
    if str(target).startswith('\\\\?\\'):
        target = Path(str(target)[4:])
    if not target.resolve().is_relative_to(paths.versions.resolve()):
        raise ValueError(f'{link} points outside the ChemDraw MCP versions folder; nothing was changed')
    return target


def _set_junction(link, target):
    import _winapi
    if os.path.lexists(link):
        os.rmdir(link)  # removes the junction itself, never the version it points to
    link.parent.mkdir(parents=True, exist_ok=True)
    _winapi.CreateJunction(str(target), str(link))


def _read_user_path():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
            return winreg.QueryValueEx(key, 'Path')[0]
    except FileNotFoundError:
        return None


def _write_user_path(value):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment', 0, winreg.KEY_SET_VALUE) as key:
        if value is None:
            winreg.DeleteValue(key, 'Path')
        else:
            winreg.SetValueEx(key, 'Path', 0, winreg.REG_EXPAND_SZ, value)


def _broadcast_environment_change():
    import ctypes
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, 'Environment', 0x0002, 5000, ctypes.byref(result))


def _launchers(paths):
    runtime = str(paths.runtime)
    body = lambda suffix: f'@echo off\r\n"{runtime}"{suffix} %*\r\n'.encode('utf-8')
    return [(paths.bin / 'chemdraw-mcp.cmd', body('')),
            (paths.bin / 'chemdraw-mac.cmd', body(' --cli')),
            (paths.bin / 'chemdraw-mcp-macos.cmd', body(' --cli serve'))]


def install_and_connect(source, clients, *, paths=None):
    paths = paths or Paths()
    installed = install_runtime(source, paths)
    previous_target = _junction_target(paths.current, paths)
    previous_path = _read_user_path()
    entries = [e for e in (previous_path or '').split(';') if e]
    wanted_path = previous_path if str(paths.bin) in entries else ';'.join(entries + [str(paths.bin)])
    plans = []
    for path, data in _launchers(paths):
        _regular(path)
        before = path.read_bytes() if path.exists() else None
        if before != data:
            plans.append((path, before, data))
    written, junction_changed, path_changed = [], False, False
    try:
        if previous_target is None or previous_target.resolve() != installed.resolve():
            _set_junction(paths.current, installed)
            junction_changed = True
        for path, before, data in plans:
            _atomic(path, data)
            written.append((path, before, data))
        if wanted_path != previous_path:
            _write_user_path(wanted_path)
            path_changed = True
            _broadcast_environment_change()
        result = connect_clients(clients, paths.runtime, args=['--desktop-serve'],
                                 claude_config=paths.claude_config, codex_config=paths.codex_config,
                                 claude_manifest=paths.claude_manifest) if clients else {
            'clients': [], 'bundle_clients': [], 'backups': [], 'command': str(paths.runtime), 'args': ['--desktop-serve']}
    except Exception:
        for path, before, data in reversed(written):
            if path.read_bytes() == data:
                if before is None:
                    path.unlink()
                else:
                    _atomic(path, before)
        if path_changed:
            _write_user_path(previous_path)
            _broadcast_environment_change()
        if junction_changed:
            if previous_target is None:
                os.rmdir(paths.current)
            else:
                _set_junction(paths.current, previous_target)
        raise
    return {**result, 'installed_runtime': str(installed), 'current': str(paths.current),
            'terminal_command': str(paths.bin / 'chemdraw-mac.cmd'), 'user_path_updated': path_changed}
