import enum
class InputStreamOptions(enum.IntFlag):
    NONE = 0; PARTIAL = 1; READ_AHEAD = 2

class Buffer(bytearray):
    """Backed by a real bytearray so memoryview() spans it, as the WinRT Buffer does."""
    def __init__(self, capacity=0, data=b""):
        super().__init__(data if data else int(capacity))
        self.capacity = int(capacity)
        self.length = len(data) if data else 0
