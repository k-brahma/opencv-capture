"""
media_utils.list_audio_devices - 利用可能なオーディオデバイス情報を取得・表示するユーティリティ
==================================================================================

このモジュールは、`sounddevice` ライブラリを使用してシステムで利用可能な
オーディオデバイスとホスト API の情報を取得・表示する関数を提供します。

* `query_audio_devices()`: デバイス情報とホスト API 情報を辞書として取得します。
* `display_audio_devices()`: 取得したデバイス情報を整形し、ログ (INFO/DEBUG) に出力します。

使用例:
-------

デバイス情報を辞書として取得:

>>> from media_utils import list_audio_devices
>>> result = list_audio_devices.query_audio_devices()
>>> if result['success']:
...     print(f"Found {len(result['devices'])} devices.")
...
Found ... devices.

デバイス情報をログに出力 (別途 logging 設定が必要):

jINFO:media_utils.list_audio_devices:Querying available audio devices...
...

"""

import sys  # display_audio_devices で使用

import sounddevice as sd


def query_audio_devices():
    """利用可能なオーディオデバイスとホストAPIの情報を取得します。

    :return: デバイス情報とホストAPI情報を含む辞書。成功時は {'success': True, 'devices': devices, 'hostapis': hostapis}、
             失敗時は {'success': False, 'error': error_message}。
    :rtype: dict
    """
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        return {"success": True, "devices": devices, "hostapis": hostapis}
    except Exception as e:
        print(f"Error querying audio devices: {e}", file=sys.stderr)  # 標準エラーへ出力
        return {"success": False, "error": str(e)}


def display_audio_devices():
    """利用可能なオーディオデバイス情報を整形して標準出力に表示します。

    ループバックデバイスの候補も表示します。
    """
    try:
        print("Querying available audio devices...\n")
        print(sd.query_devices())
        print("\nFinished querying devices.")
        print("\nLook for devices that might represent system audio output/loopback.")
        print(
            "Examples: 'Stereo Mix', 'Loopback', 'What U Hear', '内部オーディオ アナログステレオ' etc."
        )
        print("Note the 'index' number (e.g., > 1) or 'name' of the desired device.")
        print("The device with '>' is the default input, '<' is the default output.")
        print("\nAvailable Host APIs:")
        print(sd.query_hostapis())
        print("\nNote: WASAPI on Windows often supports loopback recording.")

    except Exception as e:
        print(f"An error occurred while querying devices: {e}", file=sys.stderr)  # 標準エラーへ出力
