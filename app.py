import datetime
import os
import queue
import threading

import pyautogui
import sounddevice as sd
from flask import Flask, jsonify, render_template, request, send_from_directory

# Import the Recorder class
from media_utils.recorder import Recorder

app = Flask(__name__)

# Configuration
RECORDINGS_FOLDER = "recordings"
TEMP_FOLDER = "temp_recordings"
os.makedirs(RECORDINGS_FOLDER, exist_ok=True)
os.makedirs(TEMP_FOLDER, exist_ok=True)
app.config["RECORDINGS_FOLDER"] = RECORDINGS_FOLDER
app.config["TEMP_FOLDER"] = TEMP_FOLDER
app.config["FFMPEG_PATH"] = "ffmpeg"  # Path to ffmpeg executable, change if not in PATH

# --- Application State ---
recording = False
recorder_instance = None  # Holds the current Recorder instance
main_recording_thread = None
stop_event = threading.Event()

# Variables to track current operation for status endpoint
current_status_info = {"final_output": None}

# --- Flask Routes ---


@app.route("/")
def index():
    # Clean up leftover temp files
    for folder in [app.config["TEMP_FOLDER"], app.config["RECORDINGS_FOLDER"]]:
        if not os.path.isdir(folder):
            continue
        for f in os.listdir(folder):
            # Clean temp AVI/WAV or potentially failed MP3 tests
            if f.startswith("screen_recording_") and (
                f.endswith(".avi") or f.endswith(".wav") or f.endswith(".mp3")
            ):
                try:
                    os.remove(os.path.join(folder, f))
                    print(f"Removed leftover temp/test file: {f}")
                except OSError as e:
                    print(f"Error removing leftover file {f}: {e}")
    return render_template("index.html")


@app.route("/start_recording", methods=["POST"])
def start_recording_route():
    global main_recording_thread, recording, stop_event, recorder_instance, current_status_info

    if recording:
        return jsonify({"status": "error", "message": "すでに録画中です"})

    data = request.json
    if not data:
        return (
            jsonify(
                {"status": "error", "message": "リクエストボディが空か、JSON形式ではありません。"}
            ),
            400,
        )

    try:
        duration = int(data.get("duration", 30))
        fps = int(data.get("fps", 30))
        shorts_format = data.get("shorts_format", True)
        region_enabled = data.get("region_enabled", False)
        region = None

        # --- Generate Filenames ---
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base_filename = f"screen_recording_{timestamp}"
        temp_video_file = os.path.join(app.config["TEMP_FOLDER"], f"{base_filename}_temp.avi")
        temp_audio_file = os.path.join(app.config["TEMP_FOLDER"], f"{base_filename}_temp.wav")
        # Output filename for Recorder (will be MP3 in test mode)
        output_filename = os.path.join(
            app.config["RECORDINGS_FOLDER"], f"{base_filename}.mp4"
        )  # Intended final name

        # --- Setup Recording Region ---
        if region_enabled:
            left = int(data.get("left", 0))
            top = int(data.get("top", 0))
            width = int(data.get("width", 800))
            height = int(data.get("height", 600))
            if width <= 0 or height <= 0:
                raise ValueError("幅と高さは正の値")
            screen_width, screen_height = pyautogui.size()
            if left < 0 or top < 0 or left + width > screen_width or top + height > screen_height:
                print(
                    f"警告: 領域({left},{top},{width},{height})が画面({screen_width},{screen_height})外. 調整します."
                )
                left = max(0, left)
                top = max(0, top)
                width = min(width, screen_width - left)
                height = min(height, screen_height - top)
                if width <= 0 or height <= 0:
                    raise ValueError("画面内に有効な録画領域がありません")
            region = (left, top, width, height)

        # --- Determine Audio Settings ---
        target_device_index = 10  # Try Stereo Mix first
        samplerate = Recorder.DEFAULT_SAMPLERATE
        channels = Recorder.DEFAULT_CHANNELS
        selected_device_index = None  # Store the actually used device index
        try:
            device_info = sd.query_devices(target_device_index, "input")
            samplerate = int(device_info.get("default_samplerate", Recorder.DEFAULT_SAMPLERATE))  # type: ignore
            channels = (
                1 if device_info.get("max_input_channels", 0) >= 1 else 0  # type: ignore
            )  # .get() に合わせて 0 をデフォルトに
            selected_device_index = target_device_index
            device_name = device_info.get("name", f"Device {target_device_index}")  # type: ignore
            print(f"Using {device_name} with {samplerate} Hz, {channels} channels.")
        except (ValueError, sd.PortAudioError) as e:
            print(
                f"Could not use target device ({target_device_index}): {e}. Falling back to default."
            )
            try:
                default_device_info = sd.query_devices(kind="input")
                samplerate = int(default_device_info.get("default_samplerate", Recorder.DEFAULT_SAMPLERATE))  # type: ignore
                channels = (
                    1
                    if default_device_info.get("max_input_channels", 0) >= 1  # type: ignore
                    else 0
                )
                selected_device_index = None  # Indicate default device
                default_device_name = default_device_info.get("name", "Unknown Default Device")  # type: ignore
                print(
                    f"Using default input ({default_device_name}) with {samplerate} Hz, {channels} channels."
                )
            except Exception as e_fallback:
                print(f"Could not query default input: {e_fallback}. Using hardcoded defaults.")
                samplerate = Recorder.DEFAULT_SAMPLERATE
                channels = Recorder.DEFAULT_CHANNELS
                selected_device_index = None
                print(
                    f"Using default input (Unknown Default Device) with {samplerate} Hz, {channels} channels."
                )

        # --- Create Recorder instance and Start Thread ---
        recording = True  # Set recording state
        stop_event.clear()
        current_status_info["final_output"] = output_filename  # Store intended final name

        recorder_instance = Recorder(
            video_filename_temp=temp_video_file,
            audio_filename_temp=temp_audio_file,
            output_filename_final=output_filename,  # Pass intended final name
            stop_event_ref=stop_event,  # Pass the shared event
            duration=duration,
            fps=fps,
            region=region,
            shorts_format=shorts_format,
            samplerate=samplerate,
            channels=channels,
            audio_device_index=selected_device_index,
            ffmpeg_path=app.config["FFMPEG_PATH"],
        )

        main_recording_thread = threading.Thread(
            target=run_recording_process,  # Wrapper function
            args=(recorder_instance,),
            daemon=True,
        )
        main_recording_thread.start()

        return jsonify(
            {
                "status": "success",
                "message": f"録画を開始しました ({os.path.basename(output_filename)})",
            }
        )

    except ValueError as e:
        recording = False  # Reset state on error
        return jsonify({"status": "error", "message": f"設定エラー: {str(e)}"}), 400
    except Exception as e:
        recording = False  # Reset state on error
        print(f"録画開始処理中の予期せぬエラー: {e}")
        import traceback

        traceback.print_exc()
        return jsonify({"status": "error", "message": f"録画開始エラー: {str(e)}"}), 500


def run_recording_process(recorder):
    """Wrapper function to run recorder.start() and manage state."""
    global recording, stop_event, recorder_instance, current_status_info
    try:
        recorder.start()
    except Exception as e:
        print(f"Error during recording process thread: {e}")
        if recorder and recorder.stop_event:
            recorder.stop_event.set()
    finally:
        print("Recording thread finished, resetting state.")
        recording = False
        recorder_instance = None  # Clear instance
        current_status_info["final_output"] = None
        # stop_event.clear() # No need to clear here, cleared on next start


@app.route("/stop_recording", methods=["POST"])
def stop_recording_route():
    global recording, stop_event, recorder_instance

    if not recording or recorder_instance is None:
        return jsonify({"status": "error", "message": "録画していません"})

    print("Stop recording request received. Signaling recorder...")
    # Use the recorder's stop method (which sets the event)
    recorder_instance.stop()

    # The thread's finally block will reset the main `recording` flag
    return jsonify({"status": "success", "message": "録画停止リクエストを送信しました"})


@app.route("/status", methods=["GET"])
def get_status():
    global recording, current_status_info
    # Return recording status and the *final* output filename being processed
    return jsonify(
        {
            "recording": recording,
            "current_file": (
                os.path.basename(current_status_info["final_output"])
                if current_status_info["final_output"]
                else None
            ),
        }
    )


@app.route("/recordings", methods=["GET"])
def list_recordings():
    try:
        # List final MP4/MP3 files from the RECORDINGS_FOLDER
        recordings_dir = app.config["RECORDINGS_FOLDER"]
        files = [
            f
            for f in os.listdir(recordings_dir)
            if (f.endswith(".mp4") or f.endswith(".mp3"))
            and os.path.isfile(os.path.join(recordings_dir, f))
        ]
        files.sort(
            key=lambda x: os.path.getmtime(os.path.join(recordings_dir, x)),
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


@app.route("/download/<path:filename>", methods=["GET"])
def download_file_route(filename):
    try:
        recordings_dir = app.config["RECORDINGS_FOLDER"]
        return send_from_directory(recordings_dir, filename, as_attachment=True)
    except FileNotFoundError:
        return jsonify({"status": "error", "message": "ファイルが見つかりません。"}), 404
    except Exception as e:
        print(f"ダウンロードエラー ({filename}): {e}")
        return (
            jsonify({"status": "error", "message": "ダウンロード中にエラーが発生しました。"}),
            500,
        )


@app.route("/delete/<path:filename>", methods=["DELETE"])
def delete_file_route(filename):
    try:
        recordings_dir = app.config["RECORDINGS_FOLDER"]
        file_path = os.path.join(recordings_dir, filename)
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
    app.run(debug=True, host="127.0.0.1", port=5000)
