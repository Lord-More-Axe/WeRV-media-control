from flask import Flask, render_template, request, make_response, current_app, redirect, url_for, flash, session, logging, jsonify, abort
import win32api
import ctypes
import asyncio
from PIL import Image
import io
import array
from flask import Response
import base64
import os

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


    print(info_dict)
    return info_dict

async def get_media_stats():
    sessions = await MediaManager.request_async()


    current_session = sessions.get_current_session()
    info = current_session.get_playback_info()
    stats = info.playback_status

    if stats == 4:
        current_status = 'playing'
    elif stats == 5:
        current_status = 'pasued'


    return current_status


app = Flask(__name__)

@app.route("/")
def home():
    return render_template('index.html')


@app.route('/bg', methods=["POST","GET"])
def change_bg():
    info = asyncio.run(get_media_info())
    thumbnail = info['thumbnail']

    async def read_stream_into_buffer(stream_ref, buffer):
        readable_stream = await stream_ref.open_read_async()
        await readable_stream.read_async(buffer, buffer.capacity, InputStreamOptions.READ_AHEAD)

    thumb_read_buffer = Buffer(5000000)
    asyncio.run(read_stream_into_buffer(thumbnail, thumb_read_buffer))
    mv = memoryview(thumb_read_buffer)
    byte_array = bytearray(mv)
    image_bytes = io.BytesIO(bytes(byte_array))
    image_bytes = io.BytesIO(bytes(byte_array))
    image = Image.open(image_bytes)
    image.save(image_bytes, format='PNG')
    # my_encoded_img = base64.encodebytes(image_bytes.getvalue()).decode('ascii')
    
    indicator = info["title"].replace("'", '')
    indicator = indicator[:3]
    saved_image = image.save(f'static/images/tracks/{indicator}.png')
    
    infod = {
        'title': info["title"],
        'artist': info['artist'],
        'album_title': info['album_title'],
        'ind': indicator
    }
    
    return jsonify(infod)


@app.route('/buttons', methods=["POST","GET"])
def change_button():
    status = asyncio.run(get_media_info())

    return status


@app.route("/pp", methods=["POST","GET"])
def playpause():
    print('pped')
    win32api.keybd_event(VK_MEDIA_PLAY_PAUSE, win32api.MapVirtualKey(VK_MEDIA_PLAY_PAUSE, 0))

    return 'success'
    
    
    # print(image_bytes)
    # response = Response(image_bytes.getvalue(), mimetype='image/jpeg')
    # response.headers.set('Content-Disposition', 'attachment', filename='image.jpg')
    # return response
    # image = Image.open(image_bytes)
    # image.show()


@app.route("/next", methods=["POST","GET"])
def next():
    print('nexed')
    win32api.keybd_event(VK_MEDIA_NEXT_TRACK, win32api.MapVirtualKey(VK_MEDIA_NEXT_TRACK, 0))
        

    return 'success'


@app.route("/pre", methods=["POST","GET"])
def pre():
    print('pred')
    win32api.keybd_event(VK_MEDIA_PREV_TRACK, win32api.MapVirtualKey(VK_MEDIA_PREV_TRACK, 0))

    return 'success'


@app.route('/delete', methods=["POST","GET"])
def delete_images():
    

    dir_name = f"{os.getcwd()}/static/images/tracks"
    print('-------------------------------------------------------------------------\n\n\n')
    print(dir_name)
    test = os.listdir(dir_name)

    for item in test:
        os.remove(os.path.join(dir_name, item))
            

    return 'success'

if __name__ == '__main__':
    app.run(debug=True)