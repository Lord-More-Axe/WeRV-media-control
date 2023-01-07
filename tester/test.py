import win32api
import ctypes
import asyncio
from PIL import Image
import io
import array

VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
hwcode = win32api.MapVirtualKey(VK_MEDIA_PLAY_PAUSE, 0)


from winsdk.windows.media.control import \
    GlobalSystemMediaTransportControlsSessionManager as MediaManager

from winsdk.windows.storage.streams import DataReader, Buffer, InputStreamOptions

async def get_media_info():
    sessions = await MediaManager.request_async()


    current_session = sessions.get_current_session()
    info = await current_session.try_get_media_properties_async()


    info_dict = {song_attr: info.__getattribute__(song_attr) for song_attr in dir(info) if song_attr[0] != '_'}


    info_dict['genres'] = list(info_dict['genres'])


    # print(info_dict)
    return info_dict



info = asyncio.run(get_media_info())


print(info)


thumbnail = info['thumbnail']
print(type(thumbnail))


async def read_stream_into_buffer(stream_ref, buffer):
    readable_stream = await stream_ref.open_read_async()
    await readable_stream.read_async(buffer, buffer.capacity, InputStreamOptions.READ_AHEAD)

thumb_read_buffer = Buffer(5000000)
asyncio.run(read_stream_into_buffer(thumbnail, thumb_read_buffer))

buffer_reader = DataReader.from_buffer(thumb_read_buffer)
print(thumb_read_buffer.length)
print(buffer_reader)
print(type(buffer_reader))

# byte_buffer = buffer_reader.read_string(thumb_read_buffer.length)


mv = memoryview(thumb_read_buffer)
byte_array = bytearray(mv)
bytes_read = bytes(byte_array)
image_bytes = io.BytesIO(bytes(byte_array))
image = Image.open(image_bytes)
image.show()



while True:
    cmd = input('Command:\n')
    if cmd=='p':
        win32api.keybd_event(VK_MEDIA_PLAY_PAUSE, win32api.MapVirtualKey(VK_MEDIA_PLAY_PAUSE, 0))
    elif cmd == 'n':
        win32api.keybd_event(VK_MEDIA_NEXT_TRACK, win32api.MapVirtualKey(VK_MEDIA_NEXT_TRACK, 0))
    elif cmd == 'pre':
        win32api.keybd_event(VK_MEDIA_PREV_TRACK, win32api.MapVirtualKey(VK_MEDIA_PREV_TRACK, 0))
