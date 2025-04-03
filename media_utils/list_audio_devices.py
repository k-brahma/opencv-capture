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
import logging # logging を追加

import sounddevice as sd

# --- 追加: システム音声を示す可能性のあるキーワード ---
SYSTEM_AUDIO_KEYWORDS = ["mix", "loopback", "hear", "what u hear", "ミックス", "ループバック", "ステレオミキサー", "内部オーディオ"] # 必要に応じて追加
# --- ここまで追加 ---

logger = logging.getLogger(__name__) # logger を定義

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

    入力デバイスを「マイク候補」と「システム音声候補」に分類して表示します。
    """
    try:
        logger.info("利用可能なオーディオデバイスを検索中...")
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()

        if not devices:
            logger.warning("オーディオデバイスが見つかりませんでした。")
            return

        # Host API情報を辞書に変換（インデックスをキーに）してアクセスしやすくする
        hostapi_map = {api['index']: api['name'] for api in hostapis if isinstance(api, dict)}

        print("\n--- すべてのオーディオデバイス ---")
        # sd.query_devices() のデフォルト出力をそのまま表示
        print(sd.query_devices())
        print("\n--- 利用可能なホスト API ---")
        print(hostapis)

        print("\n--- 入力デバイス候補 (マイクまたはシステム音声) ---")
        found_input = False
        potential_mic_devices = []
        potential_sys_audio_devices = []

        # デバイス情報が単一辞書の場合リスト化 (sd.query_devices() の仕様変更に対応)
        device_list = devices
        if isinstance(devices, dict):
            device_list = [devices]
        elif not isinstance(devices, list):
            logger.error(f"予期しないデバイス形式: {type(devices)}. スキップします。")
            device_list = [] # 空リストにしてループをスキップ

        for i, device_info in enumerate(device_list):
            # device_info が辞書型で、入力チャンネルがあるか確認
            if isinstance(device_info, dict) and device_info.get('max_input_channels', 0) > 0:
                found_input = True
                # query_devices() のリストインデックスではなく、デバイス自身のインデックスを使用
                # デバイス辞書に 'index' がない場合 (理論上は考えにくいが)、リストのインデックスを代替として使う
                device_index = device_info.get('index', i)
                device_name = device_info.get('name', 'N/A')
                hostapi_index = device_info.get('hostapi', -1)
                hostapi_name = hostapi_map.get(hostapi_index, 'N/A')
                input_channels = device_info.get('max_input_channels')
                # is_default_input が存在するかチェックし、なければ False 扱い
                is_default = ">" if device_info.get('is_default_input', False) else " " # デフォルト入力かチェック

                # システム音声キーワードを含むかチェック (case-insensitive)
                is_potential_system_audio = any(keyword.lower() in device_name.lower() for keyword in SYSTEM_AUDIO_KEYWORDS)

                # is_default が ">" の場合、先頭に付与
                prefix = f"{is_default} " if is_default == ">" else "  " # デフォルトでない場合はスペース2つでインデントを合わせる
                device_details = (
                    f"Index {device_index}: {device_name} "
                    f"(API: {hostapi_name}, In: {input_channels})"
                )

                if is_potential_system_audio:
                    potential_sys_audio_devices.append(prefix + device_details + " [*システム音声候補*]")
                else:
                    potential_mic_devices.append(prefix + device_details + " [マイク候補]")

        if not found_input:
            print("\n利用可能な入力デバイスが見つかりませんでした。")
        else:
            print("\n【マイク候補】:")
            if potential_mic_devices:
                for mic in potential_mic_devices:
                    print(f"  {mic}")
            else:
                print("  明確なマイク候補は見つかりませんでした。")

            print("\n【システム音声（ループバック）候補】:")
            if potential_sys_audio_devices:
                for sys_audio in potential_sys_audio_devices:
                    print(f"  {sys_audio}")
            else:
                print("  明確なシステム音声候補は見つかりませんでした（'Stereo Mix' など）。")
            print("\n[*] 注意: 上記はデバイス名からの推測です。実際の機能は異なる場合があります。")
            print("       Windows の場合、WASAPI Host API のデバイスがループバック録音に対応していることが多いです。")
            print("       デフォルト入力デバイスには '>' が付いています。")

        logger.info("デバイス情報の表示が完了しました。")

    except Exception as e:
        logger.exception(f"デバイス情報の取得・表示中にエラーが発生しました: {e}")
        print(f"デバイス情報の取得中にエラーが発生しました: {e}", file=sys.stderr) # 標準エラーにも出力
