"""Per-user private files: POSIX mode 600, or on Windows a protected DACL.

The Windows DACL grants full access only to the current user, SYSTEM and Administrators
(the counterpart of mode 600, which root can still read) and blocks inherited entries.
"""
import os


def _windows_sids():
    import win32api
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    return [user, win32security.CreateWellKnownSid(win32security.WinLocalSystemSid),
            win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid)]


def make_private(path, mode=0o600):
    if os.name != 'nt':
        os.chmod(path, mode)
        return
    import ntsecuritycon as con
    import win32security
    dacl = win32security.ACL()
    for sid in _windows_sids():
        dacl.AddAccessAllowedAce(win32security.ACL_REVISION, con.FILE_ALL_ACCESS, sid)
    win32security.SetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, dacl, None)


def is_private(path):
    if os.name != 'nt':
        return os.stat(path).st_mode & 0o077 == 0
    import win32security
    descriptor = win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
                                                    win32security.DACL_SECURITY_INFORMATION)
    dacl = descriptor.GetSecurityDescriptorDacl()
    if dacl is None:  # a NULL DACL grants everyone full access
        return False
    allowed = {win32security.ConvertSidToStringSid(s) for s in _windows_sids()}
    for index in range(dacl.GetAceCount()):
        (ace_type, _flags), _mask, sid = dacl.GetAce(index)
        if ace_type == win32security.ACCESS_ALLOWED_ACE_TYPE and win32security.ConvertSidToStringSid(sid) not in allowed:
            return False
    return True
