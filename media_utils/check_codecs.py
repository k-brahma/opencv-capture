import os

import cv2

# チェックしたいFourCCコードのリスト
fourcc_codes = ["DIVX", "XVID", "MJPG", "mp4v", "FMP4"]  # 他にも試したいものがあれば追加

# ダミーのファイル名と設定
dummy_filename = "test_codec.avi"  # コーデックによっては拡張子を .avi や .mp4 にする必要があるかも
fps = 30
width = 640
height = 480

print("Checking available VideoWriter codecs for OpenCV...")

available_codecs = []
unavailable_codecs = []

for code in fourcc_codes:
    fourcc = cv2.VideoWriter_fourcc(*code)
    writer = cv2.VideoWriter(dummy_filename, fourcc, fps, (width, height))

    if writer.isOpened():
        print(f"'{code}' seems AVAILABLE.")
        available_codecs.append(code)
        writer.release()  # すぐに解放
    else:
        print(f"'{code}' seems UNAVAILABLE.")
        unavailable_codecs.append(code)
        # writer.release() は isOpened() が False なら不要

# ダミーファイルを削除
if os.path.exists(dummy_filename):
    try:
        os.remove(dummy_filename)
    except Exception as e:
        print(f"Note: Could not remove dummy file {dummy_filename}: {e}")

print("\n--- Summary ---")
print(f"Available: {available_codecs}")
print(f"Unavailable (or failed to initialize): {unavailable_codecs}")

if "DIVX" not in available_codecs and "XVID" not in available_codecs:
    print("\nNote: DIVX/XVID codecs might not be installed or accessible by OpenCV.")
    print(
        "Consider installing a codec pack like K-Lite Codec Pack (Basic version is usually sufficient),"
    )
    print("or trying other codecs like 'MJPG'.")
