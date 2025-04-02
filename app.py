import datetime
import os
import threading
import time

import cv2
import numpy as np
import pyautogui
from flask import Flask, jsonify, render_template, request, send_from_directory

app = Flask(__name__)

# 録画保存フォルダ
UPLOAD_FOLDER = "recordings"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# アプリケーション状態変数
recording = False
recording_thread = None
current_filename = None


def screen_record(output_filename, duration=10, fps=30, region=None, shorts_format=True):
    global recording, current_filename

    current_filename = output_filename
    recording = True

    # 画面サイズの取得
    if region is None:
        screen_width, screen_height = pyautogui.size()
        region = (0, 0, screen_width, screen_height)
    # else: region is already a tuple (left, top, width, height)

    # VideoWriterの設定
    fourcc = cv2.VideoWriter_fourcc(*"DIVX")  # Changed codec to DIVX

    output_width = region[2]
    output_height = region[3]
    target_size = (output_width, output_height)

    if shorts_format:
        # YouTube Shorts用の設定（1080x1920）
        target_size = (1080, 1920)

    out = cv2.VideoWriter(output_filename, fourcc, fps, target_size)

    start_time = time.time()
    end_time = start_time + duration if duration > 0 else float("inf")
    last_frame_time = time.time()

    try:
        while recording and time.time() < end_time:
            # 目標フレーム時間からの待機時間を計算 (より正確なFPS制御のため)
            current_time = time.time()
            sleep_duration = (1 / fps) - (current_time - last_frame_time)
            if sleep_duration > 0:
                time.sleep(sleep_duration)
            last_frame_time = time.time()  # time.sleep後にもう一度取得

            # スクリーンショットを取得
            img = pyautogui.screenshot(region=region)

            # OpenCVで処理できるように変換（RGBからBGRへ）
            frame = np.array(img)
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

            # YouTube Shorts用にリサイズ
            if shorts_format:
                # 元のアスペクト比を保持しながら、縦型動画にフィットさせる
                h, w = frame.shape[:2]
                target_h, target_w = target_size[1], target_size[0]  # 1920, 1080

                # アスペクト比を計算
                source_aspect = w / h
                target_aspect = target_w / target_h  # 9 / 16

                if (
                    source_aspect > target_aspect
                ):  # 元画像がターゲットより横長 -> 幅を基準にリサイズ
                    new_w = target_w
                    new_h = int(new_w / source_aspect)
                    resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
                    # 上下に黒帯を追加
                    pad_top = (target_h - new_h) // 2
                    pad_bottom = target_h - new_h - pad_top
                    final_frame = cv2.copyMakeBorder(
                        resized, pad_top, pad_bottom, 0, 0, cv2.BORDER_CONSTANT, value=[0, 0, 0]
                    )
                elif (
                    source_aspect < target_aspect
                ):  # 元画像がターゲットより縦長 -> 高さを基準にリサイズ
                    new_h = target_h
                    new_w = int(new_h * source_aspect)
                    resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
                    # 左右に黒帯を追加
                    pad_left = (target_w - new_w) // 2
                    pad_right = target_w - new_w - pad_left
                    final_frame = cv2.copyMakeBorder(
                        resized, 0, 0, pad_left, pad_right, cv2.BORDER_CONSTANT, value=[0, 0, 0]
                    )
                else:  # アスペクト比が同じ
                    final_frame = cv2.resize(
                        frame, (target_w, target_h), interpolation=cv2.INTER_AREA
                    )

                # フレームを書き込む
                out.write(final_frame)
            else:
                # 指定されたサイズにリサイズして書き込む（領域指定がない場合は元のサイズ）
                if frame.shape[1] != output_width or frame.shape[0] != output_height:
                    frame = cv2.resize(
                        frame, (output_width, output_height), interpolation=cv2.INTER_AREA
                    )
                out.write(frame)

    except Exception as e:
        print(f"録画エラー: {str(e)}")
        # TODO: エラー状態をUIに通知する仕組み

    finally:
        # リソースの解放
        if out.isOpened():
            out.release()
        recording = False
        print(f"録画完了 or 停止: {output_filename}")
        current_filename = None  # グローバル変数をリセット


# --- Flask Routes ---


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/start_recording", methods=["POST"])
def start_recording_route():  # Renamed to avoid conflict with function name
    global recording_thread, recording, current_filename

    if recording:
        return jsonify({"status": "error", "message": "すでに録画中です"})

    # リクエストから設定を取得
    data = request.json
    if not data:  # Check if data is None or empty
        return (
            jsonify(
                {"status": "error", "message": "リクエストボディが空か、JSON形式ではありません。"}
            ),
            400,
        )

    duration = int(data.get("duration", 30))  # Default duration 30s
    fps = int(data.get("fps", 30))
    shorts_format = data.get("shorts_format", True)

    region_enabled = data.get("region_enabled", False)
    region = None
    if region_enabled:
        try:
            left = int(data.get("left", 0))
            top = int(data.get("top", 0))
            width = int(data.get("width", 800))
            height = int(data.get("height", 600))
            # Ensure width and height are positive
            if width <= 0 or height <= 0:
                raise ValueError("幅と高さは正の値である必要があります")
            # Check if region is within screen bounds (optional but good practice)
            screen_width, screen_height = pyautogui.size()
            if left < 0 or top < 0 or left + width > screen_width or top + height > screen_height:
                print(
                    f"警告: 指定された領域({left},{top},{width},{height})が画面サイズ({screen_width},{screen_height})を超えています。"
                )
                # Adjust if necessary or just warn
                left = max(0, left)
                top = max(0, top)
                width = min(width, screen_width - left)
                height = min(height, screen_height - top)
                if width <= 0 or height <= 0:
                    raise ValueError("画面内に有効な録画領域がありません")

            region = (left, top, width, height)
        except ValueError as e:
            return jsonify({"status": "error", "message": f"領域指定エラー: {str(e)}"})
        except Exception as e:
            return jsonify(
                {"status": "error", "message": f"領域指定の処理中に予期せぬエラー: {str(e)}"}
            )

    # ファイル名を作成
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(app.config["UPLOAD_FOLDER"], f"screen_recording_{timestamp}.mp4")

    # 別スレッドで録画を開始
    recording_thread = threading.Thread(
        target=screen_record,
        args=(filename, duration, fps, region, shorts_format),
        daemon=True,  # Set daemon to True so thread doesn't block app exit
    )
    recording_thread.start()

    return jsonify({"status": "success", "message": f"録画を開始しました ({filename})"})


@app.route("/stop_recording", methods=["POST"])
def stop_recording_route():  # Renamed to avoid conflict
    global recording

    if not recording:
        return jsonify({"status": "error", "message": "録画していません"})

    recording = False  # Signal the recording thread to stop
    # Wait briefly for the thread to finish writing the file? Optional.
    # if recording_thread and recording_thread.is_alive():
    #    recording_thread.join(timeout=2.0) # Wait max 2 seconds

    return jsonify({"status": "success", "message": "録画停止リクエストを送信しました"})


@app.route("/status", methods=["GET"])
def get_status():
    # Potentially add more status info later (e.g., error state)
    return jsonify({"recording": recording, "current_file": current_filename})


@app.route("/recordings", methods=["GET"])
def list_recordings():
    try:
        files = [
            f
            for f in os.listdir(app.config["UPLOAD_FOLDER"])
            if f.endswith(".mp4") and os.path.isfile(os.path.join(app.config["UPLOAD_FOLDER"], f))
        ]
        # Sort by modification time, newest first
        files.sort(
            key=lambda x: os.path.getmtime(os.path.join(app.config["UPLOAD_FOLDER"], x)),
            reverse=True,
        )
        return jsonify({"recordings": files})
    except FileNotFoundError:
        return jsonify({"recordings": [], "message": "録画フォルダが見つかりません。"})
    except Exception as e:
        print(f"録画リスト取得エラー: {e}")
        return (
            jsonify({"recordings": [], "error": "録画リストの取得中にエラーが発生しました。"}),
            500,
        )


@app.route("/download/<path:filename>", methods=["GET"])  # Use path converter for flexibility
def download_file_route(filename):  # Renamed
    try:
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=True)
    except FileNotFoundError:
        return jsonify({"status": "error", "message": "ファイルが見つかりません。"}), 404
    except Exception as e:
        print(f"ダウンロードエラー ({filename}): {e}")
        return (
            jsonify({"status": "error", "message": "ダウンロード中にエラーが発生しました。"}),
            500,
        )


@app.route("/delete/<path:filename>", methods=["DELETE"])  # Use path converter
def delete_file_route(filename):  # Renamed
    try:
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            os.remove(file_path)
            return jsonify({"status": "success", "message": f"{filename} を削除しました"})
        else:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "ファイルが見つからないかすでに削除されています。",
                    }
                ),
                404,
            )
    except Exception as e:
        print(f"削除エラー ({filename}): {e}")
        return (
            jsonify(
                {"status": "error", "message": f"ファイルの削除中にエラーが発生しました: {str(e)}"}
            ),
            500,
        )


if __name__ == "__main__":
    # Use host='0.0.0.0' to be accessible from other devices on the network
    # Use debug=False for production or stable use, debug=True enables auto-reloading and more detailed errors
    app.run(debug=True, host="127.0.0.1", port=5000)  # Keep default localhost and port
