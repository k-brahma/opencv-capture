import datetime
import json
import logging
import os
import queue
import threading
import traceback

import pyautogui
import sounddevice as sd
from flask import Flask, jsonify, render_template, request, send_from_directory

# --- 修正: 使用する Host API を MME に明示的に指定 ---
try:
    mme_index = -1
    host_apis = sd.query_hostapis()
    for i, api_info in enumerate(host_apis):
        # api_info が辞書であることを確認してからアクセス
        if isinstance(api_info, dict) and api_info.get("name") == "MME":
            mme_index = i
            break
    if mme_index != -1:
        logging.info(f"Setting default Host API to MME (index: {mme_index})")
        # sd.default.hostapi への代入が正しい方法
        sd.default.hostapi = mme_index
    else:
        logging.warning("MME Host API not found. Using default Host API.")
except Exception as e:
    logging.warning(f"Error setting Host API to MME: {e}. Using default Host API.")
# --- ここまで修正 ---

# Import the Recorder class
from media_utils.recorder import Recorder

# --- ロギング設定 ---
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

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


# --- ヘルパークラス定義 ---
class RecordingRequestHandler:
    """/start_recording のリクエスト処理ロジックをカプセル化するクラス"""

    def __init__(self, request_data, app_config):
        """初期化

        :param request_data: Flaskリクエストから取得したJSONデータ (辞書)
        :param app_config: Flaskアプリケーションの設定オブジェクト (app.config)
        """
        self.data = request_data if request_data is not None else {}
        self.config = app_config
        # 処理結果を格納するインスタンス変数
        self.duration = None
        self.fps = None
        self.shorts_format = None
        self.region_enabled = None
        self.region = None
        self.temp_video_file = None
        self.temp_audio_file = None
        self.output_filename = None
        self.samplerate = None
        self.channels = None
        self.selected_device_index = None

    def validate_parameters(self):
        """リクエストパラメータの存在、型、妥当性を検証する。"""
        logging.debug("Validating request parameters...")
        required_keys = ["duration", "fps", "shorts_format", "region_enabled"]
        missing_keys = [key for key in required_keys if key not in self.data]
        if missing_keys:
            raise ValueError(f"必須パラメータが不足しています: {', '.join(missing_keys)}")

        try:
            self.duration = int(self.data["duration"])
            self.fps = int(self.data["fps"])
        except (ValueError, TypeError):
            raise ValueError("duration と fps は整数である必要があります")
        try:
            sf_val = self.data["shorts_format"]
            re_val = self.data["region_enabled"]
            if not isinstance(sf_val, bool):
                logging.warning(
                    f"shorts_format should be boolean, got {type(sf_val)}. Attempting conversion."
                )
            if not isinstance(re_val, bool):
                logging.warning(
                    f"region_enabled should be boolean, got {type(re_val)}. Attempting conversion."
                )
            self.shorts_format = bool(sf_val)
            self.region_enabled = bool(re_val)
        except Exception as e:
            raise ValueError(f"shorts_format/region_enabled の解釈中にエラー: {e}")

        logging.debug(
            f"Validated base parameters: duration={self.duration}, fps={self.fps}, shorts={self.shorts_format}, region_enabled={self.region_enabled}"
        )

        if self.region_enabled:
            region_keys = ["left", "top", "width", "height"]
            missing_region_keys = [key for key in region_keys if key not in self.data]
            if missing_region_keys:
                raise ValueError(
                    f"region_enabled が true の場合、必須パラメータが不足しています: {', '.join(missing_region_keys)}"
                )
            try:
                left = int(self.data["left"])
                top = int(self.data["top"])
                width = int(self.data["width"])
                height = int(self.data["height"])
            except (ValueError, TypeError):
                raise ValueError("left, top, width, height は整数である必要があります")

            logging.debug(f"Requested region: L={left}, T={top}, W={width}, H={height}")
            if width <= 0 or height <= 0:
                raise ValueError("領域の幅と高さは正の値である必要があります")

            screen_width, screen_height = pyautogui.size()
            if left < 0 or top < 0 or left + width > screen_width or top + height > screen_height:
                logging.warning(
                    f"Region adjusted: was ({left},{top},{width},{height}), screen is ({screen_width},{screen_height})"
                )
                left = max(0, left)
                top = max(0, top)
                width = min(width, screen_width - left)
                height = min(height, screen_height - top)
                if width <= 0 or height <= 0:
                    raise ValueError("調整後、画面内に有効な録画領域がありません")
            self.region = (left, top, width, height)
            logging.debug(f"Final region: {self.region}")
        else:
            self.region = None
            logging.debug("Region recording disabled.")

    def generate_filenames(self):
        """タイムスタンプに基づき、一時ファイル名と最終出力ファイル名を生成する。"""
        logging.debug("Generating filenames...")
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base_filename = f"screen_recording_{timestamp}"
        self.temp_video_file = os.path.join(self.config["TEMP_FOLDER"], f"{base_filename}_temp.avi")
        self.temp_audio_file = os.path.join(self.config["TEMP_FOLDER"], f"{base_filename}_temp.wav")
        self.output_filename = os.path.join(
            self.config["RECORDINGS_FOLDER"], f"{base_filename}.mp4"
        )
        logging.debug(
            f"Generated filenames: TempVideo={self.temp_video_file}, TempAudio={self.temp_audio_file}, Output={self.output_filename}"
        )

    def determine_audio_settings(self):
        """オーディオデバイスをクエリし、録音に使用する設定を決定する。"""
        logging.debug("Determining audio settings...")
        target_device_index = 2  # <<<--- ステレオミキサーの MME でのインデックスに変更 (以前は 11)
        self.samplerate = Recorder.DEFAULT_SAMPLERATE
        self.channels = Recorder.DEFAULT_CHANNELS
        self.selected_device_index = target_device_index

        try:
            logging.debug(f"Attempting to query target device: {target_device_index}")
            device_info_raw = sd.query_devices(target_device_index, "input")
            device_info = None
            if isinstance(device_info_raw, dict):
                device_info = device_info_raw

            if device_info:
                logging.debug(f"Target device info raw: {device_info}")
                self.samplerate = int(
                    device_info.get("default_samplerate", Recorder.DEFAULT_SAMPLERATE)
                )
                self.channels = 2 if device_info.get("max_input_channels", 0) >= 2 else 1
                self.selected_device_index = target_device_index
                device_name = device_info.get("name", f"Device {target_device_index}")
                logging.info(
                    f"Using target device ({device_name}) with {self.samplerate} Hz, {self.channels} channels."
                )
            else:
                raise ValueError(
                    f"Device with index {target_device_index} not found or returned unexpected format."
                )

        except (ValueError, sd.PortAudioError) as e:
            logging.warning(
                f"Could not use target device ({target_device_index}): {e}. Falling back to default."
            )
            try:
                logging.debug("Attempting to query default input device...")
                default_device_info_raw = sd.query_devices(kind="input")
                default_device_info = None
                if isinstance(default_device_info_raw, dict):  # デフォルトデバイスも型チェック
                    default_device_info = default_device_info_raw
                # elif isinstance(default_device_info_raw, list) ... # 必要ならリスト対応

                if default_device_info:
                    logging.debug(f"Default device info raw: {default_device_info}")
                    self.samplerate = int(
                        default_device_info.get("default_samplerate", Recorder.DEFAULT_SAMPLERATE)
                    )
                    # フォールバック時は max_channels を確認して 1 or 2 を設定 (マイクは通常1ch)
                    self.channels = (
                        2 if default_device_info.get("max_input_channels", 0) >= 2 else 1
                    )
                    self.selected_device_index = None  # デフォルトデバイスを使用
                    default_device_name = default_device_info.get("name", "Unknown Default Device")
                    logging.info(
                        f"Using default input ({default_device_name}) with {self.samplerate} Hz, {self.channels} channels."
                    )
                else:
                    raise ValueError(
                        "Default input device not found or returned unexpected format."
                    )
            except Exception as e_fallback:
                logging.error(
                    f"Could not query default input: {e_fallback}. Using hardcoded defaults."
                )
                self.samplerate = Recorder.DEFAULT_SAMPLERATE
                self.channels = Recorder.DEFAULT_CHANNELS
                self.selected_device_index = None
                logging.warning(
                    f"Using hardcoded default audio settings: {self.samplerate} Hz, {self.channels} channels."
                )

    def prepare_recorder_args(self):
        """Recorderクラスの初期化に必要な引数を辞書として返す。"""
        logging.debug("Preparing Recorder arguments...")
        if not all(
            [
                self.duration is not None,
                self.fps is not None,
                self.shorts_format is not None,
                self.region_enabled is not None,
            ]
        ):
            raise ValueError(
                "パラメータが検証されていません。validate_parameters() を先に呼び出してください。"
            )
        if not all([self.temp_video_file, self.temp_audio_file, self.output_filename]):
            raise ValueError(
                "ファイル名が生成されていません。generate_filenames() を先に呼び出してください。"
            )
        if self.samplerate is None or self.channels is None:
            raise ValueError(
                "オーディオ設定が決定されていません。determine_audio_settings() を先に呼び出してください。"
            )

        recorder_args = {
            "video_filename_temp": self.temp_video_file,
            "audio_filename_temp": self.temp_audio_file,
            "output_filename_final": self.output_filename,
            "stop_event_ref": stop_event,
            "duration": self.duration,
            "fps": self.fps,
            "region": self.region,
            "shorts_format": self.shorts_format,
            "samplerate": self.samplerate,
            "channels": self.channels,
            "audio_device_index": self.selected_device_index,
            "ffmpeg_path": self.config["FFMPEG_PATH"],
        }
        logging.debug(f"Recorder args prepared: {recorder_args}")
        return recorder_args


# --- Flask Routes ---


@app.route("/")
def index():
    logging.info("Checking for leftover temp files...")
    # Clean up leftover temp files
    for folder in [app.config["TEMP_FOLDER"], app.config["RECORDINGS_FOLDER"]]:
        if not os.path.exists(folder):
            logging.debug(f"Folder not found, skipping cleanup: {folder}")
            continue
        if not os.path.isdir(folder):
            logging.warning(f"Path is not a directory, skipping cleanup: {folder}")
            continue

        logging.debug(f"Checking folder: {folder}")
        try:
            for f in os.listdir(folder):
                file_path = os.path.join(folder, f)
                # ファイルのみを対象とするチェックを追加
                if not os.path.isfile(file_path):
                    continue

                # Clean temp AVI/WAV or potentially failed MP3 tests
                if f.startswith("screen_recording_") and (
                    f.lower().endswith(".avi")
                    or f.lower().endswith(".wav")
                    or f.lower().endswith(".mp3")
                ):
                    try:
                        os.remove(file_path)
                        logging.info(f"Removed leftover temp/test file: {f} in {folder}")
                    except OSError as e:
                        # 削除エラーは警告としてログに残す
                        logging.warning(f"Error removing leftover file {f} in {folder}: {e}")
        except Exception as e:
            # listdir 自体のエラーなど
            logging.error(f"Error during cleanup for folder {folder}: {e}")

    return render_template("index.html")


@app.route("/start_recording", methods=["POST"])
def start_recording_route():
    """録画開始リクエストを処理するエンドポイントハンドラ。"""
    global main_recording_thread, recording, stop_event, recorder_instance, current_status_info
    logging.debug("Received /start_recording request")

    if recording:
        logging.warning("Recording already in progress")
        return jsonify({"status": "error", "message": "すでに録画中です"})

    data = request.json
    if data is None:  # 空のボディも None になる場合があるため明示的にチェック
        logging.error("Request body is null or not JSON")
        return (
            jsonify(
                {"status": "error", "message": "リクエストボディが空か、JSON形式ではありません。"}
            ),
            400,
        )

    try:
        # --- リファクタリング: ヘルパークラスを利用 ---
        handler = RecordingRequestHandler(data, app.config)
        handler.validate_parameters()  # 1. パラメータ検証
        handler.generate_filenames()  # 2. ファイル名生成
        handler.determine_audio_settings()  # 3. オーディオ設定決定
        recorder_args = handler.prepare_recorder_args()  # 4. Recorder 引数準備

        # --- アプリケーションの状態を更新 ---
        logging.debug("Setting application state to recording...")
        recording = True
        stop_event.clear()
        current_status_info["final_output"] = handler.output_filename  # type: ignore
        logging.debug(f"Final output filename set in status: {handler.output_filename}")

        # --- Recorder インスタンス作成とスレッド開始 ---
        logging.debug("Creating Recorder instance...")
        recorder_instance = Recorder(**recorder_args)
        logging.debug("Recorder instance created.")

        logging.debug("Creating and starting recording thread...")
        main_recording_thread = threading.Thread(
            target=run_recording_process,
            args=(recorder_instance,),
            daemon=True,
        )
        main_recording_thread.start()
        # --- 修正: basename の引数が None でないことを確認 (より安全に) ---
        output_basename = (
            os.path.basename(handler.output_filename) if handler.output_filename else "unknown_file"
        )
        logging.info(f"Recording started successfully: {output_basename}")

        return jsonify(
            {
                "status": "success",
                "message": f"録画を開始しました ({output_basename})",
            }
        )

    except ValueError as e:
        logging.error(f"Parameter validation error: {e}")
        recording = False
        return jsonify({"status": "error", "message": f"パラメータエラー: {str(e)}"}), 400
    except Exception as e:
        logging.exception(f"Unexpected error during start_recording: {e}")
        recording = False
        return jsonify({"status": "error", "message": f"録画開始エラー: {str(e)}"}), 500


def run_recording_process(recorder):
    """Wrapper function to run recorder.start() and manage state."""
    global recording, stop_event, recorder_instance, current_status_info
    try:
        recorder.start()
    except Exception as e:
        logging.exception(f"Error during recording process thread: {e}")
        if recorder and recorder.stop_event:
            recorder.stop_event.set()
    finally:
        logging.info("Recording thread finished, resetting state.")
        recording = False
        recorder_instance = None  # Clear instance
        current_status_info["final_output"] = None


@app.route("/stop_recording", methods=["POST"])
def stop_recording_route():
    global recording, stop_event, recorder_instance

    if not recording or recorder_instance is None:
        return jsonify({"status": "error", "message": "録画していません"})

    logging.info("Stop recording request received. Signaling recorder...")
    recorder_instance.stop()

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
        recordings_dir = app.config["RECORDINGS_FOLDER"]
        files = [
            f
            for f in os.listdir(recordings_dir)
            if (f.lower().endswith(".mp4") or f.lower().endswith(".mp3"))
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
        logging.exception(f"録画リスト取得エラー: {e}")
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
        logging.exception(f"ダウンロードエラー ({filename}): {e}")
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
        logging.exception(f"削除エラー ({filename}): {e}")
        return (
            jsonify(
                {"status": "error", "message": f"ファイルの削除中にエラーが発生しました: {str(e)}"}
            ),
            500,
        )


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
