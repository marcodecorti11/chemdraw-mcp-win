"""Private files: POSIX mode 600, or on Windows a protected DACL for the user, SYSTEM and Administrators."""
import os
import sys

import pytest

from chemdraw_macos.private_files import is_private, make_private


def test_new_file_becomes_private(tmp_path):
    path = tmp_path / 'secret.json'
    path.write_text('{}', encoding='utf-8')
    make_private(path)
    assert is_private(path)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows DACL inspection')
def test_inherited_world_readable_acl_is_not_private(tmp_path):
    import ntsecuritycon as con
    import win32security
    path = tmp_path / 'shared.txt'
    path.write_text('x', encoding='utf-8')
    everyone = win32security.CreateWellKnownSid(win32security.WinWorldSid)
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION, con.FILE_GENERIC_READ, everyone)
    win32security.SetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
                                       win32security.DACL_SECURITY_INFORMATION, None, None, dacl, None)
    assert not is_private(path)
    make_private(path)
    assert is_private(path)
    assert path.read_text(encoding='utf-8') == 'x'  # still readable by its owner


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX permission bits')
def test_posix_mode(tmp_path):
    path = tmp_path / 'x'
    path.write_text('x', encoding='utf-8')
    os.chmod(path, 0o644)
    assert not is_private(path)
    make_private(path)
    assert path.stat().st_mode & 0o777 == 0o600
