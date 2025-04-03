"""
recorder.py - 画面録画およびオーディオ録音機能を提供するモジュール
=============================================================

概要
----
このモジュールは、画面キャプチャとオーディオ録音を同時に行い、
それらを後処理（現在は一時ファイルの変換・削除、将来的にはマージ）する機能を提供します。

画面録画には `OpenCV <https://opencv.org/>`_ と `PyAutoGUI <https://pyautogui.readthedocs.io/>`_ を、
オーディオ録音には `sounddevice <https://python-sounddevice.readthedocs.io/>`_ と
`SoundFile <https://python-soundfile.readthedocs.io/>`_ を使用しています。
FFmpeg の呼び出しには標準ライブラリの `subprocess` を使用します。

主なクラス
----------
* :class:`Recorder`: 画面と音声の録画プロセス全体を管理・実行するメインクラス。

主な機能
--------
* スクリーンキャプチャ（全画面または指定領域）
* 指定デバイスからのオーディオストリーミング録音（WAV形式で一時保存）
* ショート動画形式（縦長 9:16）への自動リサイズ・パディング対応
* 録画後の処理:
    * 現在: 一時WAVファイルをMP3に変換（オーディオテスト目的）
    * TODO: 一時AVIビデオファイルと一時WAVオーディオファイルを結合して最終MP4ファイルを作成
* 外部からの録画停止制御（`threading.Event` を使用）

使用例
------
::

    import threading
    from media_utils.recorder import Recorder
    import time

    stop_event = threading.Event()

    recorder = Recorder(
        video_filename_temp="temp_video.avi",
        audio_filename_temp="temp_audio.wav",
        output_filename_final="final_recording.mp4",
        stop_event_ref=stop_event,
        duration=0, # 0 で stop() が呼ばれるまで録画
        shorts_format=True
    )

    # 別スレッドで録画を開始する場合
    rec_thread = threading.Thread(target=recorder.start, daemon=True)
    rec_thread.start()

    # 10秒後に録画を停止
    time.sleep(10)
    recorder.stop()

    # スレッドの終了を待つ (必要であれば)
    rec_thread.join()

注意点
------
* 現在の実装では、`Recorder` クラスは外部から `stop_event` (`threading.Event`) を受け取る必要があります。
  これは `app.py` のようなアプリケーション側で管理されることを想定しています。
* 外部コマンドとして `ffmpeg` が実行可能である必要があります。
* `_screen_record` メソッドは現在、`start` メソッドを呼び出したスレッドと同じスレッドで実行されます。
  長時間または高FPSの録画では、GUIの応答性などに影響を与える可能性があります。

依存ライブラリ
--------------
* opencv-python
* numpy
* pyautogui
* sounddevice
* soundfile
* pytest (テスト用)
* coverage (テスト用)
"""

import functools  # For partial in audio callback setup
import logging
import os
import queue
import subprocess
import sys  # For stderr in _process_output
import threading
import time

import cv2
import numpy as np
import pyautogui
import sounddevice as sd
import soundfile as sf

# --- ロガー設定 ---
logger = logging.getLogger(__name__)


class Recorder:
    """画面録画と複数音声（マイク、システム）録音のプロセスを管理・実行するクラス。

    :cvar DEFAULT_SAMPLERATE: デフォルトのオーディオサンプルレート (Hz)。
    :type DEFAULT_SAMPLERATE: int
    :cvar DEFAULT_CHANNELS: デフォルトのオーディオチャンネル数。
    :type DEFAULT_CHANNELS: int
    :cvar DEFAULT_FPS: デフォルトのビデオフレームレート (フレーム/秒)。
    :type DEFAULT_FPS: int
    :cvar DEFAULT_FFMPEG_PATH: デフォルトの FFmpeg 実行可能ファイルパス。
    :type DEFAULT_FFMPEG_PATH: str
    """

    DEFAULT_SAMPLERATE = 44100
    DEFAULT_CHANNELS = 1  # デフォルトはモノラルだが、デバイスによって上書きされる
    DEFAULT_FPS = 30
    DEFAULT_FFMPEG_PATH = "ffmpeg"

    def __init__(
        self,
        video_filename_temp,
        # --- 修正: オーディオ関連の引数をマイクとシステム音声用に変更 ---
        mic_audio_filename_temp,
        sys_audio_filename_temp,
        mic_device_index,
        mic_samplerate,
        mic_channels,
        sys_device_index,  # システム音声が見つからない場合は None
        sys_samplerate,
        sys_channels,
        # --- ここまで修正 ---
        output_filename_final,
        stop_event_ref,
        duration=10,
        fps=DEFAULT_FPS,
        region=None,
        shorts_format=True,
        ffmpeg_path=DEFAULT_FFMPEG_PATH,
    ):
        """`Recorder` のインスタンスを初期化します。

        :param video_filename_temp: 画面録画データ（AVI形式）を一時的に保存するファイルパス。
        :type video_filename_temp: str
        :param mic_audio_filename_temp: マイク音声録音データ（WAV形式）を一時的に保存するファイルパス。
        :type mic_audio_filename_temp: str
        :param sys_audio_filename_temp: システム音声録音データ（WAV形式）を一時的に保存するファイルパス。
        :type sys_audio_filename_temp: str
        :param mic_device_index: 使用するマイク入力デバイスのインデックス。
        :type mic_device_index: int | None
        :param mic_samplerate: マイク音声録音のサンプルレート (Hz)。
        :type mic_samplerate: int
        :param mic_channels: マイク音声録音のチャンネル数。
        :type mic_channels: int
        :param sys_device_index: 使用するシステム音声入力デバイスのインデックス。
        :type sys_device_index: int | None
        :param sys_samplerate: システム音声録音のサンプルレート (Hz)。
        :type sys_samplerate: int
        :param sys_channels: システム音声録音のチャンネル数。
        :type sys_channels: int
        :param output_filename_final: 最終的な出力ファイル（MP4形式を想定）のパス。
        :type output_filename_final: str
        :param stop_event_ref: 録画プロセスを外部から停止させるための `threading.Event` オブジェクト。
        :type stop_event_ref: threading.Event
        :param duration: 最大録画時間（秒）。0 を指定すると `stop()` が呼ばれるまで録画を続けます。
        :type duration: int
        :param fps: 画面録画のフレームレート（フレーム/秒）。デフォルトは `Recorder.DEFAULT_FPS`。
        :type fps: int
        :param region: 画面録画を行う領域を指定するタプル `(left, top, width, height)`。
        :type region: tuple[int, int, int, int] | None
        :param shorts_format: ショート動画形式（縦長 9:16、1080x1920）で出力するかどうか。
        :type shorts_format: bool
        # :param ffmpeg_path: FFmpeg 実行可能ファイルへのパス。内部の `AudioConverter` に渡されます。
        # :type ffmpeg_path: str
        """
        self.video_filename_temp = video_filename_temp
        self.output_filename_final = output_filename_final
        self.stop_event = stop_event_ref
        self.duration = duration
        self.fps = fps
        self.region = region
        self.shorts_format = shorts_format
        self.ffmpeg_path = ffmpeg_path

        # --- 修正: マイクとシステム音声の情報を保持 ---
        self.mic_audio_filename_temp = mic_audio_filename_temp
        self.mic_device_index = mic_device_index
        self.mic_samplerate = mic_samplerate
        self.mic_channels = mic_channels

        self.sys_audio_filename_temp = sys_audio_filename_temp
        self.sys_device_index = sys_device_index
        self.sys_samplerate = sys_samplerate
        self.sys_channels = sys_channels
        # システム音声デバイスが見つからない (index is None) 場合は録音しないフラグ
        self.record_sys_audio = self.sys_device_index is not None

        #: オーディオコールバックからマイクデータを受け取るキュー。
        self.mic_audio_queue = queue.Queue()
        #: オーディオコールバックからシステム音声データを受け取るキュー。
        self.sys_audio_queue = queue.Queue() if self.record_sys_audio else None

        #: マイク音声録音を実行するスレッドオブジェクト。
        self.mic_audio_thread = None
        #: システム音声録音を実行するスレッドオブジェクト。
        self.sys_audio_thread = None
        # --- ここまで修正 ---

        #: 画面録画が正常に完了したかどうかを示すフラグ。
        self.video_success = False
        #: マイク録音が正常に完了したか（ファイルが生成され、データが書き込まれたか）
        self.mic_audio_success = False
        #: システム音声録音が正常に完了したか
        self.sys_audio_success = False

    def _audio_callback(self, indata, frames, time, status, target_queue):
        """`sounddevice.InputStream` から呼び出されるコールバック関数。

        指定されたキュー (`target_queue`) にデータを追加します。
        """
        if status:
            logger.warning(f"Audio Callback Status: {status}")
        # --- 修正: 指定されたキューにデータを追加 ---
        if target_queue:
            target_queue.put(indata.copy())
        # --- ここまで修正 ---

    # --- 新規: 汎用的な単一オーディオストリーム録音メソッド ---
    def _record_single_audio_stream(
        self, device_index, samplerate, channels, target_queue, temp_filename
    ):
        """指定されたデバイスから録音し、一時ファイルに書き込む内部メソッド。

        バックグラウンドスレッドで実行されることを想定。
        :return: 録音に成功したか (ファイルが生成され、データが書き込まれたか) どうかの bool 値
        """
        success_flag = False
        stream = None  # finally で使うために外で宣言
        file = None  # finally で使うために外で宣言
        logger.info(f"オーディオ録音開始 (Device: {device_index}): {temp_filename}")
        try:
            # mode='w' は追記ではなく上書き。一時ファイルなのでこれで良い。
            file = sf.SoundFile(
                temp_filename,
                mode="w",
                samplerate=samplerate,
                channels=channels,
                format="WAV",
            )
            # functools.partial を使ってコールバックに関数を部分適用する
            callback_with_queue = functools.partial(self._audio_callback, target_queue=target_queue)

            stream = sd.InputStream(
                samplerate=samplerate,
                channels=channels,
                callback=callback_with_queue,
                device=device_index,
            )
            stream.start()  # ストリームを開始
            logger.info(f"オーディオストリーム開始 (Device: {device_index})")

            # --- 修正: whileループ内のインデント修正 ---
            while not self.stop_event.is_set():
                try:
                    # キューからデータを取得し、ファイルに書き込む
                    data = target_queue.get(timeout=0.1)
                    file.write(data)
                    # 最初の書き込みが成功したらフラグを立てる (ファイルが空でなくなる目安)
                    if not success_flag:
                        success_flag = True
                except queue.Empty:
                    # タイムアウトは正常なので無視してループ継続
                    pass
                except Exception as write_e:
                    logger.error(f"Error writing audio data to {temp_filename}: {write_e}")
                    # 書き込みエラーが発生したらループを抜けるか？ -> 一旦継続
            # --- ここまで修正 ---

        except sd.PortAudioError as pae:
            logger.error(
                f"PortAudioError starting stream on device {device_index} ({temp_filename}): {pae}"
            )
            success_flag = False
        except Exception as e:
            logger.exception(
                f"予期せぬオーディオ録音エラー (Device: {device_index}, File: {temp_filename}): {e}"
            )
            success_flag = False
        finally:
            # ストリームとファイルを確実に閉じる
            if stream is not None:
                try:
                    if not stream.closed:
                        stream.stop()
                        stream.close()
                        logger.debug(f"Audio stream closed for device {device_index}.")
                except Exception as close_e:
                    logger.error(f"Error closing audio stream for device {device_index}: {close_e}")
            if file is not None:
                try:
                    file.close()
                    logger.debug(f"Audio file closed: {temp_filename}")
                except Exception as file_close_e:
                    logger.error(f"Error closing audio file {temp_filename}: {file_close_e}")

            logger.info(
                f"オーディオ録音停止 (Device: {device_index}, Success: {success_flag}): {temp_filename}"
            )
            # 成功フラグ（データが書き込まれたか）を返す
            return success_flag

    # --- ここまで新規メソッド ---

    def _resize_and_pad_frame(self, frame, target_size, shorts_format):
        """キャプチャしたフレームを指定された出力サイズに合わせてリサイズ・パディングする内部メソッド。

        `shorts_format` が True の場合、ターゲットのアスペクト比 (9:16) を維持するように
        リサイズし、不足する部分には黒い帯 (パディング) を追加します。
        False の場合は、単純に `target_size` にリサイズします。

        :param frame: 入力となるビデオフレーム (OpenCV で扱える NumPy 配列、BGR 想定)。
        :type frame: numpy.ndarray
        :param target_size: 目標とする出力フレームサイズ `(width, height)`。
        :type target_size: tuple[int, int]
        :param shorts_format: ショート動画形式 (9:16 パディング) を適用するかどうか。
        :type shorts_format: bool
        :return: 処理後のビデオフレーム。
        :rtype: numpy.ndarray
        """
        # target_size は (width, height) なので注意
        output_height, output_width = target_size[1], target_size[0]

        if shorts_format:
            h, w = frame.shape[:2]
            target_h, target_w = output_height, output_width  # 縦長 (e.g., 1920, 1080)
            source_aspect = w / h
            target_aspect = target_w / target_h  # e.g., 1080 / 1920 = 0.5625

            if source_aspect > target_aspect:  # ソースがターゲットより横長 (例: 16:9 > 9:16)
                new_w = target_w
                new_h = int(new_w / source_aspect)
                resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
                pad_v = target_h - new_h
                pad_top = pad_v // 2
                pad_bottom = pad_v - pad_top
                final_frame = cv2.copyMakeBorder(
                    resized, pad_top, pad_bottom, 0, 0, cv2.BORDER_CONSTANT, value=[0, 0, 0]
                )
            elif source_aspect < target_aspect:  # ソースがターゲットより縦長 (例: 1:2 < 9:16)
                new_h = target_h
                new_w = int(new_h * source_aspect)
                resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
                pad_h = target_w - new_w
                pad_left = pad_h // 2
                pad_right = pad_h - pad_left
                final_frame = cv2.copyMakeBorder(
                    resized, 0, 0, pad_left, pad_right, cv2.BORDER_CONSTANT, value=[0, 0, 0]
                )
            else:  # アスペクト比が同じ (例: 9:16 == 9:16)
                final_frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
            return final_frame
        else:
            current_h, current_w = frame.shape[:2]
            if current_w != output_width or current_h != output_height:
                frame = cv2.resize(
                    frame, (output_width, output_height), interpolation=cv2.INTER_AREA
                )
            return frame

    def _screen_record(self):
        """画面を指定された設定でキャプチャし、一時 AVI ファイルに書き込む内部メソッド。

        `start()` メソッド内で実行されます (現在の実装では同スレッド)。
        `pyautogui.screenshot` で画面（または指定領域）を取得し、必要に応じて
        `_resize_and_pad_frame` で加工した後、`cv2.VideoWriter` を使って
        一時 AVI ファイル (`self.video_filename_temp`) に書き込みます。

        `self.stop_event` がセットされるか、指定された `self.duration` が経過するまで
        ループ処理を続けます。ループ内では FPS を維持するために `time.sleep` を使用します。

        正常にフレームを書き込めた場合は `self.video_success` を True に設定します。

        :raises IOError: `cv2.VideoWriter` を開けなかった場合。
        :raises Exception: スクリーンショット取得、画像処理、ファイル書き込みなどで予期せぬエラーが発生した場合。
                         エラー発生時には `self.stop()` を呼び出して録画停止を試みます。
        """
        out = None
        self.video_success = False
        video_filename = self.video_filename_temp
        region = self.region
        fps = self.fps
        shorts_format = self.shorts_format

        try:
            # 録画領域が指定されていなければ全画面を取得
            if region is None:
                screen_width, screen_height = pyautogui.size()
                region = (0, 0, screen_width, screen_height)

            # コーデックと出力サイズを決定
            fourcc = cv2.VideoWriter_fourcc(*"DIVX")  # type: ignore
            output_width, output_height = region[2], region[3]
            if shorts_format:
                target_size = (1080, 1920)  # ショート固定サイズ (width, height)
            else:
                target_size = (output_width, output_height)  # 録画領域サイズ (width, height)

            # VideoWriter を初期化
            out = cv2.VideoWriter(video_filename, fourcc, fps, target_size)
            if not out.isOpened():
                # VideoWriter を開けなかった場合は IOError を発生
                raise IOError(f"ビデオライターを {video_filename} に対して開けませんでした")

            logger.info(f"ビデオ録画開始: {video_filename}")
            start_time = time.time()
            # duration=0 なら無限大 (inf) に設定し、stop_event でのみ停止するように
            end_time = start_time + self.duration if self.duration > 0 else float("inf")
            last_frame_time = time.time()  # FPS制御用の最終フレーム時刻

            # 停止イベントがセットされるか、指定時間を超えるまでループ
            while not self.stop_event.is_set() and time.time() < end_time:
                current_time = time.time()
                # FPS に基づいて次のフレームまでの待機時間を計算
                sleep_duration = (1 / fps) - (current_time - last_frame_time)
                if sleep_duration > 0:
                    time.sleep(sleep_duration)
                last_frame_time = time.time()

                # スクリーンショットを取得
                img = pyautogui.screenshot(region=region)
                # NumPy 配列に変換し、色空間を RGB から BGR に変換 (OpenCV 用)
                frame = np.array(img)
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

                # フレームをリサイズ・パディング
                processed_frame = self._resize_and_pad_frame(frame, target_size, shorts_format)
                # フレームを AVI ファイルに書き込み
                out.write(processed_frame)

            # ループが正常に終了した場合（時間経過または stop() 呼び出し）
            self.video_success = True

        except Exception as e:
            logger.exception(f"ビデオ録画エラー: {e}")
            # エラー発生時は stop_event をセットしてオーディオスレッドも停止させる
            self.stop()
        finally:
            # 正常終了、エラー発生に関わらず VideoWriter を解放
            if out and out.isOpened():
                out.release()
            logger.info(f"ビデオ録画停止: {video_filename}")

    def _process_output(self):
        """録画停止後の後処理を実行する内部メソッド。

        ビデオファイル、マイク音声ファイル、システム音声ファイルを FFmpeg でマージし、
        最終的な MP4 ファイルとして保存します。
        """
        logger.info("録画後処理を開始します...")

        # --- 入力ファイルの存在と有効性を確認 ---
        # video_success は _screen_record 内で設定される
        video_valid = self.video_success and os.path.exists(self.video_filename_temp)
        # 各音声の success フラグは _record_single_audio_stream の戻り値で設定される
        mic_audio_valid = (
            self.mic_audio_success
            and os.path.exists(self.mic_audio_filename_temp)
            and os.path.getsize(self.mic_audio_filename_temp) > 1024
        )
        sys_audio_valid = (
            self.record_sys_audio
            and self.sys_audio_success
            and os.path.exists(self.sys_audio_filename_temp)
            and os.path.getsize(self.sys_audio_filename_temp) > 1024
        )

        if not video_valid:
            logger.warning("ビデオ録画に失敗またはファイルが見つからないため、処理を中止します。")
            # 残っている一時音声ファイルがあれば削除
            if os.path.exists(self.mic_audio_filename_temp):
                os.remove(self.mic_audio_filename_temp)
            if self.record_sys_audio and os.path.exists(self.sys_audio_filename_temp):
                os.remove(self.sys_audio_filename_temp)
            return

        # --- FFmpeg コマンドの構築 ---
        ffmpeg_command = [self.ffmpeg_path, "-y"]  # 出力上書き

        # 入力ファイルの追加 (ビデオは常に最初)
        ffmpeg_command.extend(["-i", self.video_filename_temp])
        input_map = {"video": "0:v:0"}
        audio_inputs_for_filter = []
        input_count = 1  # 次の入力ファイルのインデックス (0はビデオ)

        if mic_audio_valid:
            ffmpeg_command.extend(["-i", self.mic_audio_filename_temp])
            input_map["mic_audio"] = f"{input_count}:a:0"
            audio_inputs_for_filter.append(f"[{input_count}:a]")
            input_count += 1
            logger.info("マイク音声をマージ対象に追加します。")
        else:
            logger.warning("マイク音声が無効または見つからないため、マージから除外します。")

        if sys_audio_valid:
            ffmpeg_command.extend(["-i", self.sys_audio_filename_temp])
            input_map["sys_audio"] = f"{input_count}:a:0"
            audio_inputs_for_filter.append(f"[{input_count}:a]")
            input_count += 1
            logger.info("システム音声をマージ対象に追加します。")
        else:
            # record_sys_audio が False の場合もここに該当
            logger.info("システム音声は録音されなかったか無効なため、マージから除外します。")

        # オーディオフィルターとマッピングの決定
        audio_output_map_label = None
        if len(audio_inputs_for_filter) >= 2:
            # 2つ以上の有効なオーディオ入力がある場合、より高度な同期処理を適用
            # 1. 全ストリーム（ビデオ含む）のPTSを同じ原点にリセット
            # 2. システム音声に少し早め開始の調整を適用
            # 3. asynctsフィルターで自動同期
            filter_complex = (
                # ビデオのPTSをリセット（参照点として設定）
                f"[0:v:0]setpts=PTS-STARTPTS[v_synced];"
                # マイク音声のPTSをリセット、さらに非同期対応
                f"{audio_inputs_for_filter[0]}asetpts=PTS-STARTPTS,asetnsamples=n=1024,aresample=async=1000[a_mic];"
                # システム音声のPTSをリセットし、遅延を前倒しで補正、さらに非同期対応
                f"{audio_inputs_for_filter[1]}asetpts=PTS-STARTPTS-0.5/TB,asetnsamples=n=1024,aresample=async=1000[a_sys];"
                # 両方のオーディオをミックス
                f"[a_mic][a_sys]amix=inputs=2:duration=longest:normalize=0[a_mix]"
            )
            ffmpeg_command.extend(["-filter_complex", filter_complex])
            # 同期済みビデオとオーディオをマッピング
            ffmpeg_command.extend(["-map", "[v_synced]", "-map", "[a_mix]"])
            # audioマッピングラベルをNoneに設定（すでにマッピング完了のため）
            audio_output_map_label = None
            logger.info("高度な同期処理でマイク音声とシステム音声をミックスします。")
        elif len(audio_inputs_for_filter) == 1:
            # 有効なオーディオ入力が1つだけの場合も同期処理を適用
            filter_complex = (
                # ビデオのPTSをリセット
                f"[0:v:0]setpts=PTS-STARTPTS[v_synced];"
                # オーディオのPTSをリセット、非同期対応
                f"{audio_inputs_for_filter[0]}asetpts=PTS-STARTPTS,asetnsamples=n=1024,aresample=async=1000[a_synced]"
            )
            ffmpeg_command.extend(["-filter_complex", filter_complex])
            # 同期済みビデオとオーディオをマッピング
            ffmpeg_command.extend(["-map", "[v_synced]", "-map", "[a_synced]"])
            # audioマッピングラベルをNoneに設定（すでにマッピング完了のため）
            audio_output_map_label = None
            logger.info("単一オーディオストリームを同期処理しました。")
        else:
            # 有効なオーディオ入力がない場合
            # ビデオのPTSのみリセット
            filter_complex = f"[0:v:0]setpts=PTS-STARTPTS[v_synced]"
            ffmpeg_command.extend(["-filter_complex", filter_complex])
            ffmpeg_command.extend(["-map", "[v_synced]"])
            ffmpeg_command.extend(["-an"])  # オーディオなし
            logger.warning("有効なオーディオ入力がないため、音声なし (-an) で処理します。")

        # コーデック設定
        ffmpeg_command.extend(["-c:v", "libx264"])  # ビデオはコピーではなくエンコード（同期のため）

        if audio_output_map_label:
            # この分岐は使用されなくなったが、念のため残す
            ffmpeg_command.extend(
                ["-map", audio_output_map_label]
            )
            ffmpeg_command.extend(["-c:a", "aac", "-b:a", "192k"])
        elif len(audio_inputs_for_filter) > 0:
            # オーディオがある場合のコーデック設定
            ffmpeg_command.extend(["-c:a", "aac", "-b:a", "192k"])

        # ffmpeg_command.extend(["-shortest"]) # オプション: 最も短い入力に合わせて出力を終了
        ffmpeg_command.extend([self.output_filename_final])  # 出力ファイル

        # --- FFmpeg 実行 ---
        logger.info(f"FFmpeg マージコマンドを実行します:")
        # コマンドが見やすいようにスペースで連結して表示 (デバッグ用)
        logger.info(" ".join(ffmpeg_command))
        merge_success = False
        try:
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
                encoding="utf-8",
                errors="ignore",
            )
            logger.info(f"FFmpegによるマージが成功しました！ 出力: {self.output_filename_final}")
            logger.debug(f"FFmpeg stderr:\n{process.stderr}")  # stderr は通常進捗など
            merge_success = True
        except subprocess.CalledProcessError as e:
            logger.error("!!!!!!!! FFmpegによるマージ処理が失敗しました !!!!!!!!")
            logger.error(f"コマンド: {' '.join(e.cmd)}")
            logger.error(f"リターンコード: {e.returncode}")
            logger.error(f"エラー出力 (stderr):\n{e.stderr}")  # エラー内容
        except FileNotFoundError:
            logger.error(f"エラー: '{self.ffmpeg_path}' コマンドが見つかりません。")
        except Exception as e:
            logger.exception(f"FFmpegマージ中に予期せぬエラーが発生しました: {e}")

        # --- 一時ファイルのクリーンアップ ---
        # 成功・失敗に関わらず、存在する一時ファイルリストを作成
        temp_files_to_remove = []
        if os.path.exists(self.video_filename_temp):
            temp_files_to_remove.append(self.video_filename_temp)
        if os.path.exists(self.mic_audio_filename_temp):
            temp_files_to_remove.append(self.mic_audio_filename_temp)
        # sys_audio_filename_temp は record_sys_audio が True の場合のみ考慮
        if self.record_sys_audio and os.path.exists(self.sys_audio_filename_temp):
            temp_files_to_remove.append(self.sys_audio_filename_temp)

        if merge_success:
            logger.info("マージ成功のため、一時ファイルを削除します。")
            for f in temp_files_to_remove:
                try:
                    # ここでは再度 exists チェックは不要 (リスト作成時に確認済み)
                    os.remove(f)
                    logger.debug(f"一時ファイルを削除しました: {f}")
                except OSError as e:
                    logger.warning(f"一時ファイル {f} の削除中にエラー: {e}")
        else:
            logger.warning(
                "マージに失敗またはスキップされたため、一時ファイルは削除されませんでした。"
            )
            if temp_files_to_remove:
                logger.warning("以下のファイルを確認してください:")
                for f in temp_files_to_remove:
                    logger.warning(f"  - {f}")
            else:
                logger.warning("(削除対象の一時ファイルはありませんでした)")

        logger.info("録画後処理が完了しました。")

    def start(self):
        """録画プロセスを開始します。

        1. `stop_event` をクリアします。
        2. `_audio_record` をターゲットとするデーモンスレッドを開始します。
        3. 同じスレッドで `_screen_record` を実行します。
        4. `_screen_record` が終了した後、`stop_event` をセットします。
        5. オーディオスレッドの終了を待ちます (タイムアウト付き)。
        6. `_process_output` を呼び出して後処理を実行します。
        """
        logger.info("レコーダー開始中...")
        self.stop_event.clear()
        self.video_success = False  # リセット
        self.mic_audio_success = False
        self.sys_audio_success = False

        # --- 修正: 2つのオーディオスレッドを開始 ---
        self.mic_audio_thread = threading.Thread(
            target=lambda: setattr(
                self,
                "mic_audio_success",
                self._record_single_audio_stream(
                    device_index=self.mic_device_index,
                    samplerate=self.mic_samplerate,
                    channels=self.mic_channels,
                    target_queue=self.mic_audio_queue,
                    temp_filename=self.mic_audio_filename_temp,
                ),
            ),
            daemon=True,
            name="MicAudioThread",  # スレッドに名前をつける
        )
        self.mic_audio_thread.start()
        logger.debug("マイクオーディオスレッドを開始しました。")

        if self.record_sys_audio and self.sys_audio_queue:
            self.sys_audio_thread = threading.Thread(
                target=lambda: setattr(
                    self,
                    "sys_audio_success",
                    self._record_single_audio_stream(
                        device_index=self.sys_device_index,
                        samplerate=self.sys_samplerate,
                        channels=self.sys_channels,
                        target_queue=self.sys_audio_queue,
                        temp_filename=self.sys_audio_filename_temp,
                    ),
                ),
                daemon=True,
                name="SysAudioThread",
            )
            self.sys_audio_thread.start()
            logger.debug("システムオーディオスレッドを開始しました。")
        else:
            logger.info("システム音声録音はスキップされます。")
            self.sys_audio_thread = None
        # --- ここまで修正 ---

        # 画面録画を開始 (ブロッキング)
        self._screen_record()

        # --- 録画後の処理 ---
        logger.info("画面録画が終了しました。オーディオスレッドの停止を試みます...")
        self.stop_event.set()  # オーディオスレッドに停止を通知

        # --- 修正: 両方のオーディオスレッドの終了を待つ ---
        threads_to_join = []
        if self.mic_audio_thread:
            threads_to_join.append(self.mic_audio_thread)
        if self.sys_audio_thread:
            threads_to_join.append(self.sys_audio_thread)

        if threads_to_join:
            logger.debug(f"{len(threads_to_join)} 個のオーディオスレッドの終了を待機します...")
            for t in threads_to_join:
                if t.is_alive():  # スレッドが開始していれば join を試みる
                    # logger.debug(f"オーディオスレッド ({t.name}) が終了するのを待機中...")
                    t.join(timeout=5.0)
                    if t.is_alive():
                        logger.warning(
                            f"警告: オーディオスレッド ({t.name}) の結合がタイムアウトしました。"
                        )
                else:
                    logger.debug(f"オーディオスレッド ({t.name}) はすでに終了しています。")
        else:
            logger.debug("待機対象のオーディオスレッドはありませんでした。")
        # --- ここまで修正 ---

        # 後処理（FFmpegマージ、一時ファイル削除）を実行
        # この時点で self.mic_audio_success と self.sys_audio_success が設定されているはず
        self._process_output()

        logger.info("レコーダーが終了しました。")

    def stop(self):
        """現在実行中の録画プロセスに対して停止を要求します。

        内部で管理している `stop_event` をセットします。
        これにより、`_audio_record` と `_screen_record` のループが停止します。
        実際の停止処理は `start` メソッドの呼び出しが完了する際に行われます。
        """
        logger.info("レコーダーの停止が通知されました。")
        self.stop_event.set()
