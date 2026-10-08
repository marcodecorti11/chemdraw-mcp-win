"""The Windows candidate must not carry the build machine's paths (editable-install records)."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / 'scripts' / 'build_windows.py'


@pytest.fixture(scope='module')
def build_windows():
    spec = importlib.util.spec_from_file_location('build_windows_under_test', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_folder(tmp_path, root):
    folder = tmp_path / 'ChemDraw MCP'
    info = folder / '_internal' / 'chemdraw_mcp_macos-0.10.0rc22.dist-info'
    info.mkdir(parents=True)
    (info / 'direct_url.json').write_text(json.dumps({'url': 'file:///' + root.as_posix(), 'dir_info': {'editable': True}}), encoding='utf-8')
    (info / 'METADATA').write_text('Name: chemdraw-mcp-macos\n', encoding='utf-8')
    return folder, info


def test_editable_install_record_is_removed_and_metadata_kept(build_windows, tmp_path):
    folder, info = fake_folder(tmp_path, tmp_path / 'private checkout')
    removed = build_windows.drop_build_machine_records(folder)
    assert removed == [info / 'direct_url.json']
    assert not (info / 'direct_url.json').exists()
    assert (info / 'METADATA').is_file()


def test_build_refuses_a_candidate_that_still_names_the_build_tree(build_windows, tmp_path):
    root = tmp_path / 'private checkout'
    folder, info = fake_folder(tmp_path, root)
    with pytest.raises(RuntimeError, match='build machine'):
        build_windows.require_no_build_paths(folder, [root])
    build_windows.drop_build_machine_records(folder)
    build_windows.require_no_build_paths(folder, [root])  # clean now
