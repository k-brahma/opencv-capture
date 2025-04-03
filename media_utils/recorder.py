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

# --- モジュールレベル変数 ---
# 共有キュー（app.pyで管理され、audio_recordで設定される）
audio_queue = None

# デフォルトのオーディオ設定
default_samplerate = 44100
default_channels = 1


# --- オーディオ録音コールバック ---
def audio_callback(indata, frames, time, status):
    """オーディオストリームのコールバック。モジュールレベルのaudio_queueを使用します。"""
    global audio_queue  # モジュールレベル変数にアクセスするために必要
    if audio_queue is None:
        print("エラー: audio_queueが録音モジュールで初期化されていません。")
        return
    if status:
        print(status, flush=True)
    audio_queue.put(indata.copy())


# --- オーディオ録音関数 ---
def audio_record(
    output_filename,
    samplerate,
    channels,
    stop_event_ref,
    audio_queue_ref,
    device_index=None,
):
    """オーディオをWAVファイルに録音します。"""
    global audio_queue
    audio_queue = audio_queue_ref

    print(
        f"デバイスインデックス {device_index if device_index is not None else 'デフォルト'} からのオーディオ録音を試みます"
    )
    print(f"オーディオ録音開始: {output_filename}")
    try:
        device_info = sd.query_devices(device_index if device_index is not None else None, "input")
        print(f"選択されたデバイス情報: {device_info['name']}")
        actual_samplerate = int(device_info["default_samplerate"])
        actual_channels = device_info["max_input_channels"]
        print(
            f"デバイスがサポートするサンプルレート: {actual_samplerate}, 最大チャンネル数: {actual_channels}"
        )
        print(f"使用するサンプルレート: {samplerate}, チャンネル数: {channels}")

        with sf.SoundFile(
            output_filename, mode="xb", samplerate=samplerate, channels=channels, format="WAV"
        ) as file:
            with sd.InputStream(
                samplerate=samplerate,
                channels=channels,
                callback=audio_callback,
                device=device_index,
            ):
                while not stop_event_ref.is_set():
                    try:
                        file.write(audio_queue_ref.get(timeout=0.1))
                    except queue.Empty:
                        pass
    except sd.PortAudioError as e:
        print(f"デバイス {device_index} の選択時にPortAudioエラーが発生: {e}")
    except ValueError as e:
        print(f"無効なデバイスインデックス {device_index} またはパラメータ: {e}")
    except Exception as e:
        print(f"オーディオ録音エラー: {e}")
    finally:
        print(f"オーディオ録音停止: {output_filename}")
        audio_queue = None


# --- 画面録画とマージ機能 ---
def screen_record_and_merge(
    video_filename_temp,
    audio_filename_temp,
    output_filename_final,
    stop_event_ref,
    audio_queue_ref,
    duration=10,
    fps=30,
    region=None,
    shorts_format=True,
    samplerate=default_samplerate,
    channels=default_channels,
    audio_device_index=None,
    ffmpeg_path="ffmpeg",
):
    """画面とオーディオを録画し、その後マージします（またはテスト用にオーディオのみ変換）。"""

    # --- オーディオ録音スレッドの開始 ---
    audio_recording_thread = threading.Thread(
        target=audio_record,
        args=(
            audio_filename_temp,
            samplerate,
            channels,
            stop_event_ref,
            audio_queue_ref,
            audio_device_index,
        ),
        daemon=True,
    )
    audio_recording_thread.start()
    print("オーディオ録音スレッドを開始しました。")

    # --- 画面録画の開始（FFmpegステップでは出力は無視される） ---
    out = None
    video_success = False
    try:
        if region is None:
            screen_width, screen_height = pyautogui.size()
            region = (0, 0, screen_width, screen_height)

        fourcc = cv2.VideoWriter_fourcc(*"DIVX")
        output_width, output_height = region[2], region[3]
        target_size = (output_width, output_height)

        if shorts_format:
            target_size = (1080, 1920)

        out = cv2.VideoWriter(video_filename_temp, fourcc, fps, target_size)
        if not out.isOpened():
            raise IOError(f"ビデオライターを開けませんでした: {video_filename_temp}")

        print(f"ビデオ録画開始（オーディオテスト用に出力は無視）: {video_filename_temp}")
        start_time = time.time()
        end_time = start_time + duration if duration > 0 else float("inf")
        last_frame_time = time.time()

        while not stop_event_ref.is_set() and time.time() < end_time:
            current_time = time.time()
            sleep_duration = (1 / fps) - (current_time - last_frame_time)
            if sleep_duration > 0:
                time.sleep(sleep_duration)
            last_frame_time = time.time()
            img = pyautogui.screenshot(region=region)
            frame = np.array(img)
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

            if shorts_format:
                h, w = frame.shape[:2]
                target_h, target_w = target_size[1], target_size[0]
                source_aspect = w / h
                target_aspect = target_w / target_h
                if source_aspect > target_aspect:
                    new_w = target_w
                    new_h = int(new_w / source_aspect)
                    resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
                    pad_top = (target_h - new_h) // 2
                    pad_bottom = target_h - new_h - pad_top
                    final_frame = cv2.copyMakeBorder(
                        resized, pad_top, pad_bottom, 0, 0, cv2.BORDER_CONSTANT, value=[0, 0, 0]
                    )
                elif source_aspect < target_aspect:
                    new_h = target_h
                    new_w = int(new_h * source_aspect)
                    resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
                    pad_left = (target_w - new_w) // 2
                    pad_right = target_w - new_w - pad_left
                    final_frame = cv2.copyMakeBorder(
                        resized, 0, 0, pad_left, pad_right, cv2.BORDER_CONSTANT, value=[0, 0, 0]
                    )
                else:
                    final_frame = cv2.resize(
                        frame, (target_w, target_h), interpolation=cv2.INTER_AREA
                    )
                out.write(final_frame)
            else:
                if frame.shape[1] != output_width or frame.shape[0] != output_height:
                    frame = cv2.resize(
                        frame, (output_width, output_height), interpolation=cv2.INTER_AREA
                    )
                out.write(frame)
        video_success = True

    except Exception as e:
        print(f"ビデオ録画エラー: {e}")
        stop_event_ref.set()
    finally:
        if out and out.isOpened():
            out.release()
        print(f"ビデオ録画停止（オーディオテスト用に出力は無視）: {video_filename_temp}")

    # --- オーディオ録音スレッドの停止 ---
    stop_event_ref.set()
    if audio_recording_thread and audio_recording_thread.is_alive():
        print("オーディオスレッドの終了を待機中...")
        audio_recording_thread.join(timeout=5.0)
        if audio_recording_thread.is_alive():
            print("警告: オーディオスレッドがタイムアウトしました。")

    # --- === オーディオテスト: WAVからMP3への変換（FFmpeg使用） === ---
    mp3_output_filename = os.path.splitext(output_filename_final)[0] + ".mp3"

    if os.path.exists(audio_filename_temp) and os.path.getsize(audio_filename_temp) > 1024:
        print(
            f"--- オーディオテスト --- {audio_filename_temp} から {mp3_output_filename} への変換を試みます"
        )

        ffmpeg_command = [
            ffmpeg_path,
            "-y",
            "-i",
            audio_filename_temp,
            "-vn",
            "-acodec",
            "libmp3lame",
            "-ab",
            "192k",
            mp3_output_filename,
        ]
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
            try:
                os.remove(audio_filename_temp)
                print(f"一時的なオーディオファイルを削除しました: {audio_filename_temp}")
            except OSError as e:
                print(f"一時的なオーディオファイル {audio_filename_temp} の削除中にエラー: {e}")
        except subprocess.CalledProcessError as e:
            print("!!!!!!!! FFmpegによるWAVからMP3への変換が失敗しました !!!!!!!!")
            print(f"コマンド: {' '.join(e.cmd)}")
            print(f"リターンコード: {e.returncode}")
            print(f"エラー出力 (stderr):\n{e.stderr}")
            print("一時的なWAVファイルは削除されませんでした。")
        except FileNotFoundError:
            print(f"エラー: '{ffmpeg_path}' コマンドが見つかりません。")
            print("一時的なWAVファイルは削除されませんでした。")
        except Exception as e:
            print(f"FFmpeg変換中に予期せぬエラーが発生しました: {e}")
            print("一時的なWAVファイルは削除されませんでした。")
    else:
        print("オーディオ変換をスキップ: 一時的なオーディオファイルが存在しないか空です。")

    # 一時的なビデオファイルのクリーンアップ
    if os.path.exists(video_filename_temp):
        try:
            os.remove(video_filename_temp)
            print(f"未使用の一時的なビデオファイルをクリーンアップしました: {video_filename_temp}")
        except OSError as e:
            print(f"一時的なビデオファイル {video_filename_temp} のクリーンアップ中にエラー: {e}")

    print(f"オーディオテストプロセスが完了しました。{mp3_output_filename} を確認してください。")


class Recorder:
    def __init__(
        self,
        video_filename_temp,
        audio_filename_temp,
        output_filename_final,
        stop_event_ref,  # app.pyからのイベントを使用
        duration=10,
        fps=30,
        region=None,
        shorts_format=True,
        samplerate=default_samplerate,
        channels=default_channels,
        audio_device_index=None,
        ffmpeg_path="ffmpeg",
    ):
        """
        レコーダークラスの初期化。

        引数:
            video_filename_temp: 一時的な動画ファイルのパス
            audio_filename_temp: 一時的な音声ファイルのパス
            output_filename_final: 最終出力ファイルのパス
            stop_event_ref: 録画停止用の共有イベント
            duration: 録画時間（秒）
            fps: フレームレート
            region: 録画する画面領域（None=全画面）
            shorts_format: ショート動画フォーマットを使用するかどうか
            samplerate: オーディオのサンプルレート
            channels: オーディオのチャンネル数
            audio_device_index: 使用するオーディオデバイスのインデックス
            ffmpeg_path: FFmpegの実行ファイルパス
        """
        self.video_filename_temp = video_filename_temp
        self.audio_filename_temp = audio_filename_temp
        self.output_filename_final = output_filename_final
        self.stop_event = stop_event_ref  # 共有イベントを使用
        self.duration = duration
        self.fps = fps
        self.region = region
        self.shorts_format = shorts_format
        self.samplerate = samplerate
        self.channels = channels
        self.audio_device_index = audio_device_index
        self.ffmpeg_path = ffmpeg_path

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
            print(f"選択されたデバイス情報: {device_info['name']}")
            actual_samplerate = int(device_info["default_samplerate"])
            actual_channels = device_info["max_input_channels"]
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

    def _screen_record(self):
        """一時的なAVIファイルに画面を録画します。"""
        out = None
        self.video_success = False
        video_filename = self.video_filename_temp  # 明確にするため
        region = self.region
        fps = self.fps
        shorts_format = self.shorts_format

        try:
            if region is None:
                screen_width, screen_height = pyautogui.size()
                region = (0, 0, screen_width, screen_height)

            fourcc = cv2.VideoWriter_fourcc(*"DIVX")
            output_width, output_height = region[2], region[3]
            target_size = (output_width, output_height)

            if shorts_format:
                target_size = (1080, 1920)

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

                # リサイズロジック
                if shorts_format:
                    h, w = frame.shape[:2]
                    target_h, target_w = target_size[1], target_size[0]
                    source_aspect = w / h
                    target_aspect = target_w / target_h
                    if source_aspect > target_aspect:
                        new_w = target_w
                        new_h = int(new_w / source_aspect)
                        r = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
                        pad_t = (target_h - new_h) // 2
                        pad_b = target_h - new_h - pad_t
                        f = cv2.copyMakeBorder(
                            r, pad_t, pad_b, 0, 0, cv2.BORDER_CONSTANT, value=[0, 0, 0]
                        )
                    elif source_aspect < target_aspect:
                        new_h = target_h
                        new_w = int(new_h * source_aspect)
                        r = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
                        pad_l = (target_w - new_w) // 2
                        pad_r = target_w - new_w - pad_l
                        f = cv2.copyMakeBorder(
                            r, 0, 0, pad_l, pad_r, cv2.BORDER_CONSTANT, value=[0, 0, 0]
                        )
                    else:
                        f = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
                    out.write(f)
                else:
                    if frame.shape[1] != output_width or frame.shape[0] != output_height:
                        frame = cv2.resize(
                            frame, (output_width, output_height), interpolation=cv2.INTER_AREA
                        )
                    out.write(frame)

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
        # --- === オーディオテスト: WAVからMP3への変換（FFmpeg使用） === ---
        mp3_output_filename = os.path.splitext(self.output_filename_final)[0] + ".mp3"

        if (
            os.path.exists(self.audio_filename_temp)
            and os.path.getsize(self.audio_filename_temp) > 1024
        ):
            print(
                f"--- オーディオテスト --- {self.audio_filename_temp} から {mp3_output_filename} への変換を試みます"
            )
            ffmpeg_command = [
                self.ffmpeg_path,
                "-y",
                "-i",
                self.audio_filename_temp,
                "-vn",
                "-acodec",
                "libmp3lame",
                "-ab",
                "192k",
                mp3_output_filename,
            ]
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
                try:
                    os.remove(self.audio_filename_temp)
                    print(f"一時的なオーディオファイルを削除しました: {self.audio_filename_temp}")
                except OSError as e:
                    print(
                        f"一時的なオーディオファイル {self.audio_filename_temp} の削除中にエラー: {e}"
                    )
            except subprocess.CalledProcessError as e:
                print("!!!!!!!! FFmpegによるWAVからMP3への変換が失敗しました !!!!!!!!")
                print(f"コマンド: {' '.join(e.cmd)}")
                print(f"リターンコード: {e.returncode}")
                print(f"エラー出力 (stderr):\n{e.stderr}")
                print("一時的なWAVファイルは削除されませんでした。")
            except FileNotFoundError:
                print(f"エラー: '{self.ffmpeg_path}' コマンドが見つかりません。")
                print("一時的なWAVファイルは削除されませんでした。")
            except Exception as e:
                print(f"FFmpeg変換中に予期せぬエラーが発生しました: {e}")
                print("一時的なWAVファイルは削除されませんでした。")
        else:
            print("オーディオ変換をスキップ: 一時的なオーディオファイルが存在しないか空です。")

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
