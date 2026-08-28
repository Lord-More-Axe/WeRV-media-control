"""Minimal comtypes stand-in. CoInitialize is recorded so a test can assert it ran."""
CLSCTX_ALL = 23
initialised = []
def CoInitialize(): initialised.append(True)
def CoUninitialize(): pass
