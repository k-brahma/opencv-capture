import os

import cv2


def check_available_codecs(fourcc_codes=None):
    """指定された FourCC コードリストについて、OpenCV の VideoWriter で利用可能かチェックします。

    一時ファイルを作成して VideoWriter を初期化し、isOpened() の結果を確認します。

    :param fourcc_codes: チェックする FourCC コードの文字列リスト (例: ["DIVX", "XVID"])。
                         None の場合はデフォルトのリストを使用します。
    :type fourcc_codes: list[str] | None
    :return: 利用可能なコーデックと利用不可能なコーデックのリストを含む辞書。
             {'available': [...], 'unavailable': [...]}
    :rtype: dict
    """
    if fourcc_codes is None:
        fourcc_codes = ["DIVX", "XVID", "MJPG", "mp4v", "FMP4"]

    # 一時ファイル名 (カレントディレクトリに作成)
    dummy_filename = "test_codec.avi"
    fps = 30
    width = 640
    height = 480

    available_codecs = []
    unavailable_codecs = []

    print("Checking codec availability...")
    for code in fourcc_codes:
        print(f"  Testing: {code}", end="... ")
        try:
            fourcc = cv2.VideoWriter_fourcc(*code)  # type: ignore
            writer = cv2.VideoWriter(dummy_filename, fourcc, fps, (width, height))

            if writer.isOpened():
                print("Available")
                available_codecs.append(code)
                writer.release()
            else:
                print("Unavailable")
                unavailable_codecs.append(code)
        except Exception as e:
            # VideoWriter_fourcc や VideoWriter で例外が発生した場合も Unavailable とする
            print(f"Error testing {code}: {e}")
            unavailable_codecs.append(code)

    # ダミーファイルを削除
    if os.path.exists(dummy_filename):
        try:
            os.remove(dummy_filename)
            print(f"Removed temporary file: {dummy_filename}")
        except Exception as e:
            print(f"Warning: Failed to remove temporary file {dummy_filename}: {e}")

    return {"available": available_codecs, "unavailable": unavailable_codecs}
