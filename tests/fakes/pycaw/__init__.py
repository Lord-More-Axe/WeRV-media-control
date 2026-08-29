"""Stand-in for pycaw's modern layout: GetSpeakers returns an AudioDevice wrapper.

The point of this fake is the 2025 breaking change: ``GetSpeakers()`` used to return a
raw ``IMMDevice`` with an ``.Activate`` method and now returns an ``AudioDevice`` that
has none. Code written against the older contract raises AttributeError here, which is
exactly what we want the test to catch.
"""

#: Mutable so a test can observe what the backend actually wrote.
endpoint_state = {"level": 0.5, "muted": False}
