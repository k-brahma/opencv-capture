"""
recorder.py - 画面録画およびオーディオ録音機能を提供するモジュール

このモジュールは、画面キャプチャとオーディオ録音を同時に行い、それらをマージして
最終的な出力ファイルを生成する機能を提供します。画面録画にはOpenCVとpyautoguiを、
オーディオ録音にはsounddeviceとsoundfileを使用しています。

主な機能:
- スクリーンキャプチャ（任意の領域指定可能）
- オーディオストリーミング録音
- ショート動画形式（縦長動画）のサポート
- FFmpegを使用したオーディオファイルの変換とビデオマージング

使用方法:
- 関数ベースのインターフェース: screen_record_and_merge()関数を使用
- クラスベースのインターフェース: Recorderクラスをインスタンス化して使用

注意: このモジュールは共有のaudio_queueとstop_eventの管理のためにapp.pyに依存しています。
将来的にはクラスベースのアプローチでこれをより良くカプセル化できるかもしれません。

依存ライブラリ:
- OpenCV (cv2)
- NumPy
- PyAutoGUI
- SoundDevice
- SoundFile
- FFmpeg (外部コマンドとして)
"""

import os
import queue
import subprocess
import threading
import time

import cv2
import numpy as np
import pyautogui
import sounddevice as sd
import soundfile as sf

# 注意: このモジュールは共有のaudio_queueとstop_eventの管理のためにapp.pyに依存しています。
# 将来的にはクラスベースのアプローチでこれをより良くカプセル化できるかもしれません。


# --- FFmpeg 音声変換クラス ---
class AudioConverter:
    """FFmpegを使用して音声ファイルを変換するクラス。"""

    def __init__(self, ffmpeg_path="ffmpeg", cleanup_temp_files=True):
        """コンストラクタ

        Args:
            ffmpeg_path (str): FFmpeg実行可能ファイルのパス。
            cleanup_temp_files (bool): 変換後に一時ファイル（入力）を削除するかどうか。
        """
        self.ffmpeg_path = ffmpeg_path
        self.cleanup_temp_files = cleanup_temp_files

    def convert_wav_to_mp3(self, wav_path, mp3_path, bitrate="192k"):
        """指定されたWAVファイルをMP3に変換します。

        Args:
            wav_path (str): 入力WAVファイルのパス。
            mp3_path (str): 出力MP3ファイルのパス。
            bitrate (str): 出力MP3のビットレート (例: "192k")。

        Returns:
            bool: 変換が成功したかどうか。
        """
        if not os.path.exists(wav_path) or os.path.getsize(wav_path) <= 1024:
            print(f"オーディオ変換をスキップ: WAVファイルが存在しないか空です: {wav_path}")
            return False

        print(
            f"--- FFmpeg --- {wav_path} から {mp3_path} への変換を開始します (ビットレート: {bitrate})"
        )
        ffmpeg_command = [
            self.ffmpeg_path,
            "-y",
            "-i",
            wav_path,
            "-vn",
            "-acodec",
            "libmp3lame",
            "-ab",
            bitrate,
            mp3_path,
        ]
        success = False
        try:
            print(f"FFmpegコマンドを実行: {' '.join(ffmpeg_command)}")
            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
            process = subprocess.run(
                ffmpeg_command,
                check=True,
                capture_output=True,
                text=True,
                startupinfo=startupinfo,
            )
            print("FFmpegによるWAVからMP3への変換が成功しました！")
            success = True
            if self.cleanup_temp_files:
                try:
                    os.remove(wav_path)
                    print(f"一時的なオーディオファイルを削除しました: {wav_path}")
                except OSError as e:
                    print(f"一時的なオーディオファイル {wav_path} の削除中にエラー: {e}")
            else:
                print(f"一時ファイル削除スキップ: {wav_path}")

        except subprocess.CalledProcessError as e:
            print("!!!!!!!! FFmpegによるWAVからMP3への変換が失敗しました !!!!!!!!")
            print(f"コマンド: {' '.join(e.cmd)}")
            print(f"リターンコード: {e.returncode}")
            print(f"エラー出力 (stderr):\n{e.stderr}")
        except FileNotFoundError:
            print(f"エラー: '{self.ffmpeg_path}' コマンドが見つかりません。")
        except Exception as e:
            print(f"FFmpeg変換中に予期せぬエラーが発生しました: {e}")
        finally:
            if not success and not self.cleanup_temp_files:
                print("変換失敗のため、一時的なWAVファイルは削除されませんでした。")
            elif not success and self.cleanup_temp_files:
                # cleanup_temp_files=True でも失敗時は削除しない方がデバッグしやすいかも
                print("変換失敗のため、一時的なWAVファイルは削除されませんでした。")

        return success


class Recorder:
    # --- 修正: デフォルト設定をクラス変数として定義 ---
    DEFAULT_SAMPLERATE = 44100
    DEFAULT_CHANNELS = 1
    DEFAULT_FPS = 30
    DEFAULT_FFMPEG_PATH = "ffmpeg"

    def __init__(
        self,
        video_filename_temp,
        audio_filename_temp,
        output_filename_final,
        stop_event_ref,  # app.pyからのイベントを使用
        # --- 修正: デフォルト引数でクラス変数を使用 ---
        duration=10,
        fps=DEFAULT_FPS,
        region=None,
        shorts_format=True,
        samplerate=DEFAULT_SAMPLERATE,
        channels=DEFAULT_CHANNELS,
        audio_device_index=None,
        ffmpeg_path=DEFAULT_FFMPEG_PATH,
    ):
        """
        レコーダークラスの初期化。

        引数:
            video_filename_temp: 一時的な動画ファイルのパス
            audio_filename_temp: 一時的な音声ファイルのパス
            output_filename_final: 最終出力ファイルのパス
            stop_event_ref: 録画停止用の共有イベント
            duration: 録画時間（秒）
            fps: フレームレート (デフォルト: Recorder.DEFAULT_FPS)
            region: 録画する画面領域（None=全画面）
            shorts_format: ショート動画フォーマットを使用するかどうか
            samplerate: オーディオのサンプルレート (デフォルト: Recorder.DEFAULT_SAMPLERATE)
            channels: オーディオのチャンネル数 (デフォルト: Recorder.DEFAULT_CHANNELS)
            audio_device_index: 使用するオーディオデバイスのインデックス
            ffmpeg_path: FFmpegの実行ファイルパス (デフォルト: Recorder.DEFAULT_FFMPEG_PATH)
        """
        self.video_filename_temp = video_filename_temp
        self.audio_filename_temp = audio_filename_temp
        self.output_filename_final = output_filename_final
        self.stop_event = stop_event_ref
        self.duration = duration
        self.fps = fps  # 初期化時に渡された値を使用
        self.region = region
        self.shorts_format = shorts_format
        self.samplerate = samplerate  # 初期化時に渡された値を使用
        self.channels = channels  # 初期化時に渡された値を使用
        self.audio_device_index = audio_device_index
        self.ffmpeg_path = ffmpeg_path  # 初期化時に渡された値を使用

        # AudioConverter のインスタンスを Recorder 内に保持
        # ffmpeg_pathは初期化時に受け取ったものを使う
        self.converter = AudioConverter(ffmpeg_path=self.ffmpeg_path)

        self.audio_queue = queue.Queue()
        self.audio_recording_thread = None
        self.video_success = False

    def _audio_callback(self, indata, frames, time, status):
        """オーディオストリームのインスタンスメソッドコールバック。"""
        if status:
            print(status, flush=True)
        # キューにはselfを通してアクセス
        self.audio_queue.put(indata.copy())

    def _audio_record(self):
        """WAVファイルにオーディオを録音します（スレッドで実行することを想定）。"""
        print(f"オーディオ録音開始: {self.audio_filename_temp}")
        try:
            device_info = sd.query_devices(self.audio_device_index, "input")
            print(f"選択されたデバイス情報: {device_info['name']}")  # type: ignore
            actual_samplerate = int(device_info["default_samplerate"])  # type: ignore
            actual_channels = device_info["max_input_channels"]  # type: ignore
            print(
                f"デバイスがサポートするサンプルレート: {actual_samplerate}, 最大チャンネル数: {actual_channels}"
            )
            print(f"使用するサンプルレート: {self.samplerate}, チャンネル数: {self.channels}")

            with sf.SoundFile(
                self.audio_filename_temp,
                mode="xb",
                samplerate=self.samplerate,
                channels=self.channels,
                format="WAV",
            ) as file:
                with sd.InputStream(
                    samplerate=self.samplerate,
                    channels=self.channels,
                    callback=self._audio_callback,  # インスタンスメソッドを使用
                    device=self.audio_device_index,
                ):
                    while not self.stop_event.is_set():
                        try:
                            file.write(self.audio_queue.get(timeout=0.1))
                        except queue.Empty:
                            pass  # stop_eventのチェックを継続
        except sd.PortAudioError as e:
            print(f"デバイス {self.audio_device_index} の選択時にPortAudioエラーが発生: {e}")
        except ValueError as e:
            print(f"無効なデバイスインデックス {self.audio_device_index} またはパラメータ: {e}")
        except Exception as e:
            print(f"オーディオ録音エラー: {e}")
        finally:
            print(f"オーディオ録音停止: {self.audio_filename_temp}")

    # --- 修正: 新しいプライベートヘルパーメソッドを追加 ---
    def _resize_and_pad_frame(self, frame, target_size, shorts_format):
        """フレームを指定されたサイズにリサイズし、必要に応じてパディングします。"""
        output_height, output_width = (
            target_size[1],
            target_size[0],
        )  # target_size は (width, height)

        if shorts_format:
            # ショートフォーマット: 縦長 (1080x1920想定) にアスペクト比を維持してリサイズし、黒帯を追加
            h, w = frame.shape[:2]
            target_h, target_w = output_height, output_width  # ここでは target_size が (1080, 1920)
            source_aspect = w / h
            target_aspect = target_w / target_h

            if source_aspect > target_aspect:  # 元画像がターゲットより横長
                new_w = target_w
                new_h = int(new_w / source_aspect)
                resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
                pad_top = (target_h - new_h) // 2
                pad_bottom = target_h - new_h - pad_top
                final_frame = cv2.copyMakeBorder(
                    resized, pad_top, pad_bottom, 0, 0, cv2.BORDER_CONSTANT, value=[0, 0, 0]
                )
            elif source_aspect < target_aspect:  # 元画像がターゲットより縦長 (または同じ)
                new_h = target_h
                new_w = int(new_h * source_aspect)
                resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
                pad_left = (target_w - new_w) // 2
                pad_right = target_w - new_w - pad_left
                final_frame = cv2.copyMakeBorder(
                    resized, 0, 0, pad_left, pad_right, cv2.BORDER_CONSTANT, value=[0, 0, 0]
                )
            else:  # アスペクト比が同じ場合
                final_frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
            return final_frame
        else:
            # 通常フォーマット: 指定されたサイズに単純にリサイズ
            # 元のコードでは target_size は region のサイズだった
            current_h, current_w = frame.shape[:2]
            if current_w != output_width or current_h != output_height:
                frame = cv2.resize(
                    frame, (output_width, output_height), interpolation=cv2.INTER_AREA
                )
            return frame

    def _screen_record(self):
        """一時的なAVIファイルに画面を録画します。"""
        out = None
        self.video_success = False
        video_filename = self.video_filename_temp
        region = self.region
        fps = self.fps
        shorts_format = self.shorts_format

        try:
            if region is None:
                screen_width, screen_height = pyautogui.size()
                region = (0, 0, screen_width, screen_height)

            fourcc = cv2.VideoWriter_fourcc(*"DIVX")  # type: ignore
            output_width, output_height = region[2], region[3]
            # --- 修正: target_size の決定ロジックをここに集約 ---
            if shorts_format:
                target_size = (1080, 1920)  # ショート動画の固定サイズ (width, height)
            else:
                target_size = (output_width, output_height)  # 録画領域のサイズ (width, height)

            # --- 修正: VideoWriter に渡すサイズを target_size から取得 ---
            out = cv2.VideoWriter(video_filename, fourcc, fps, target_size)
            if not out.isOpened():
                raise IOError(f"ビデオライターを {video_filename} に対して開けませんでした")

            print(f"ビデオ録画開始: {video_filename}")
            start_time = time.time()
            end_time = start_time + self.duration if self.duration > 0 else float("inf")
            last_frame_time = time.time()

            while not self.stop_event.is_set() and time.time() < end_time:
                current_time = time.time()
                sleep_duration = (1 / fps) - (current_time - last_frame_time)
                if sleep_duration > 0:
                    time.sleep(sleep_duration)
                last_frame_time = time.time()

                img = pyautogui.screenshot(region=region)
                frame = np.array(img)
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

                # --- 修正: リサイズとパディング処理をヘルパーメソッドに委譲 ---
                processed_frame = self._resize_and_pad_frame(frame, target_size, shorts_format)
                out.write(processed_frame)

            self.video_success = True

        except Exception as e:
            print(f"ビデオ録画エラー: {e}")
            self.stop()  # エラー時に停止を通知
        finally:
            if out and out.isOpened():
                out.release()
            print(f"ビデオ録画停止: {video_filename}")

    def _process_output(self):
        """録画停止後のマージまたはオーディオ変換を処理します。"""
        # --- === オーディオテスト: WAVからMP3への変換 === ---
        mp3_output_filename = os.path.splitext(self.output_filename_final)[0] + ".mp3"

        # --- 修正: self.converter インスタンスを使用 ---
        conversion_success = self.converter.convert_wav_to_mp3(
            self.audio_filename_temp, mp3_output_filename
        )
        if conversion_success:
            print(f"オーディオ変換成功。 {mp3_output_filename} を確認してください。")
        else:
            print("オーディオ変換に失敗またはスキップされました。")

        # 一時的なビデオファイルのクリーンアップ
        if os.path.exists(self.video_filename_temp):
            try:
                os.remove(self.video_filename_temp)
                print(
                    f"未使用の一時的なビデオファイルをクリーンアップしました: {self.video_filename_temp}"
                )
            except OSError as e:
                print(
                    f"一時的なビデオファイル {self.video_filename_temp} のクリーンアップ中にエラー: {e}"
                )

        print(f"オーディオテストプロセスが完了しました。{mp3_output_filename} を確認してください。")

        # --- === TODO: オリジナルのマージロジックを復元 === ---
        # オーディオテストが成功したら、上のブロックを以下に置き換え：
        # if self.video_success and os.path.exists(...) ... :
        #    ffmpeg_command = [...] # AVIとWAVをMP4にマージするため
        #    # ... マージロジックの残り ...

    def start(self):
        """オーディオとビデオの録画プロセスを開始します。"""
        print("レコーダー開始中...")
        self.stop_event.clear()  # 停止が設定されていないことを確認

        # 別スレッドでオーディオ録音を開始
        self.audio_recording_thread = threading.Thread(target=self._audio_record, daemon=True)
        self.audio_recording_thread.start()

        # 画面録画を開始（現在は簡略化のためこのスレッドで）
        # 必要に応じて別スレッドに移動可能
        self._screen_record()

        # --- 録画後の処理 ---
        # stop_eventは_screen_recordエラーまたは外部のstop()によって設定された可能性がある
        self.stop_event.set()  # オーディオスレッドが確実に停止するようにする

        # オーディオスレッドが書き込みを完了するのを待つ
        if self.audio_recording_thread and self.audio_recording_thread.is_alive():
            print("オーディオスレッドが終了するのを待機中...")
            self.audio_recording_thread.join(timeout=5.0)
            if self.audio_recording_thread.is_alive():
                print("警告: オーディオスレッドの結合がタイムアウトしました。")

        # 出力の処理（マージまたはオーディオ変換）
        self._process_output()

        print("レコーダーが終了しました。")

    def stop(self):
        """録画プロセスを停止するよう通知します。"""
        print("レコーダーの停止が通知されました。")
        self.stop_event.set()
