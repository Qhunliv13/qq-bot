"""Current-user Windows DPAPI storage for the native lightweight client's API key."""
import os


def protect(plaintext):
    """Encrypt a UTF-8 secret for this Windows user without machine-wide scope or UI."""
    if os.name != 'nt':
        raise ValueError('Windows current-user protection is required')
    import base64
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        """Describe the native length-delimited DPAPI byte buffer."""
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    raw = plaintext.encode('utf-8')
    buffer = ctypes.create_string_buffer(raw)
    incoming = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    api = ctypes.WinDLL('crypt32', use_last_error=True)
    api.CryptProtectData.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                                    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    api.CryptProtectData.restype = wintypes.BOOL
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not api.CryptProtectData(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise ValueError('Windows could not protect the credential')
    try:
        return base64.b64encode(ctypes.string_at(outgoing.data, outgoing.size)).decode('ascii')
    finally:
        kernel.LocalFree(outgoing.data)


def unprotect(encoded):
    """Decrypt a current-user DPAPI value; another user/machine must reconfigure it."""
    if os.name != 'nt':
        raise ValueError('Windows current-user protection is required')
    import base64
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        """Describe the native length-delimited DPAPI byte buffer."""
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise ValueError('invalid protected credential') from None
    buffer = ctypes.create_string_buffer(raw)
    incoming = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    api = ctypes.WinDLL('crypt32', use_last_error=True)
    api.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    api.CryptUnprotectData.restype = wintypes.BOOL
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not api.CryptUnprotectData(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise ValueError('protected credential belongs to another Windows user or machine')
    try:
        return ctypes.string_at(outgoing.data, outgoing.size).decode('utf-8')
    finally:
        kernel.LocalFree(outgoing.data)
