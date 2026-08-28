"""Stand-in for the WinRT bindings, so the Windows backend is testable off-Windows.

Every signature here was taken from the generated type stubs shipped in the real
package: ``request_async`` is a static method returning an operation with ``.get()``,
``get_current_session`` may return None, event handlers are plain callables taking
``(sender, args)``, and ``read_async`` hands back a *different* buffer than the one
passed in. Getting those details wrong in the mock would make these tests worthless.
"""
