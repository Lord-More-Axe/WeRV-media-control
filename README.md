# WeRV (beta)

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

## *Remote Media Control*

This program allows you to use Windows medica controls from another device connected to the same WiFi network. With this program, you can play, pause, and skip media without alt-tabbing or physically interacting with the device.

> **NOTES:** 
>
> - Volume control will be added in future updates. 
> - This program is currently only available on Windows 10 and 11. (have not been tested for previous versions)

## Installation

1. Download the latest release of the program from the [releases page](https://github.com/VRWE/WeRV/releases/tag/beta).
2. Extract the downloaded zip file and run the `werv.exe` file. That's it!
3. As soon as the firewall alert appears, make sure to enable both the private and public networks.

## Usage

1. Make sure that your main device and the device you want to use to control it are connected to the same WiFi network.

2. After running `werv.exe`, a QR code will appear.

3. On the device you want to use to control the media, scan the QR code.
   - If you accidentally closed the QR code, you can find it in the directory you unzipped as `qr_code.png`. You can also regenerate the QR code and make it appear by clicking on the track image from the browser. Alternatively, you can access the same information from the main device by visiting this link: [http://127.0.0.1:5000](http://127.0.0.1:5000/)

4. Make sure that some music or other media is playing in the background, such as from YouTube, Telegram, or a browser.
   - If multiple media programs are open on your Windows device, it might interfere with the track information. However, it will still control the original track that was opened first.

5. You should now be able to use the remote control features of the program to control the media. If the track information is not showing up, try reloading the page.

## Configuration

Unfortunately, customization is not an option for now, but it is on my list to add it in the future.

You will not be able to hide the Python console, and if you value minimalist design, I apologise for the time being as I was unable to do so.

## Troubleshooting

If you are unable to connect to the server, make sure that both devices are connected to the same WiFi network. VPNs and Postmaster can also cause interference. To fix this, make sure that "block incoming connections" is disabled for `werv.exe` in Portmaster's settings.

## Credits

This [GitHub project](https://github.com/sayantanm19/js-music-player) was used for the interface of the music player.

## License

This project is licensed under the [Apache License](https://github.com/VRWE/WeRV/blob/main/LICENSE).

## Screenshots
![image](https://user-images.githubusercontent.com/61895507/211321689-d176fd6e-6a7d-4879-b7a3-3f9de375a36c.png)
![photo_2023-01-09_21-46-48](https://user-images.githubusercontent.com/61895507/211323755-63d85669-9b19-4a94-ab89-2dc134647ffc.jpg)
![photo_2023-01-09_21-46-42](https://user-images.githubusercontent.com/61895507/211323739-a8bfbdab-b792-452a-abf8-554a7f4227ff.jpg)

