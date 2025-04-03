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
* :class:`AudioConverter`: FFmpeg を使用して音声ファイルを変換するユーティリティクラス。
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

import logging
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

# --- ロガー設定 ---
logger = logging.getLogger(__name__)

# 注意: このモジュールは共有のaudio_queueとstop_eventの管理のためにapp.pyに依存しています。
# 将来的にはクラスベースのアプローチでこれをより良くカプセル化できるかもしれません。


# --- FFmpeg 音声変換クラス ---
class AudioConverter:
    """FFmpeg を使用して音声ファイルを変換する機能を提供するクラス。

    主に一時的に保存された WAV ファイルを MP3 に変換する用途を想定しています。
    (将来的には、ビデオとのマージ処理にも FFmpeg を利用する可能性があります)
    """

    def __init__(self, ffmpeg_path="ffmpeg", cleanup_temp_files=True):
        """`AudioConverter` のインスタンスを初期化します。

        :param ffmpeg_path: FFmpeg 実行可能ファイルへのパス。
                            環境変数 PATH に含まれている場合は "ffmpeg" のままで動作します。
        :type ffmpeg_path: str
        :param cleanup_temp_files: 変換が成功した場合に、元の入力ファイル (WAV) を削除するかどうかを示すフラグ。
                                   デフォルトは True (削除する)。テスト時などに False に設定すると便利です。
        :type cleanup_temp_files: bool
        """
        self.ffmpeg_path = ffmpeg_path
        self.cleanup_temp_files = cleanup_temp_files

    def convert_wav_to_mp3(self, wav_path, mp3_path, bitrate="192k"):
        """指定された WAV ファイルを MP3 ファイルに変換します。

        `ffmpeg` コマンドをサブプロセスとして実行します。
        入力ファイルが存在しないか、サイズが小さすぎる (1024 バイト以下) 場合は、
        変換をスキップして `False` を返します。

        :param wav_path: 入力となる WAV ファイルのパス。
        :type wav_path: str
        :param mp3_path: 出力する MP3 ファイルのパス。
        :type mp3_path: str
        :param bitrate: 出力 MP3 ファイルのオーディオビットレート (例: "192k", "128k")。
                        FFmpeg の `-ab` オプションに渡されます。
        :type bitrate: str
        :return: 変換が成功した場合は True、スキップまたは失敗した場合は False。
        :rtype: bool
        :raises FileNotFoundError: `ffmpeg_path` で指定された FFmpeg コマンドが見つからない場合。
                                 (厳密には `subprocess.run` が発生させる)
        :raises subprocess.CalledProcessError: FFmpeg の実行がエラーで終了した場合。
                                             (終了コードが 0 以外の場合)
        """
        if not os.path.exists(wav_path) or os.path.getsize(wav_path) <= 1024:
            logger.info(f"オーディオ変換をスキップ: WAVファイルが存在しないか空です: {wav_path}")
            return False

        logger.info(
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
            logger.debug(f"FFmpegコマンドを実行: {' '.join(ffmpeg_command)}")
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
            logger.info("FFmpegによるWAVからMP3への変換が成功しました！")
            success = True
            if self.cleanup_temp_files:
            try:
                    os.remove(wav_path)
                    logger.info(f"一時的なオーディオファイルを削除しました: {wav_path}")
            except OSError as e:
                    logger.warning(f"一時的なオーディオファイル {wav_path} の削除中にエラー: {e}")
            else:
                logger.info(f"一時ファイル削除スキップ: {wav_path}")

        except subprocess.CalledProcessError as e:
            logger.error("!!!!!!!! FFmpegによるWAVからMP3への変換が失敗しました !!!!!!!!")
            logger.error(f"コマンド: {' '.join(e.cmd)}")
            logger.error(f"リターンコード: {e.returncode}")
            logger.error(f"エラー出力 (stderr):\n{e.stderr}")
        except FileNotFoundError:
            logger.error(f"エラー: '{self.ffmpeg_path}' コマンドが見つかりません。")
        except Exception as e:
            logger.exception(f"FFmpeg変換中に予期せぬエラーが発生しました: {e}")
        finally:
            if not success and not self.cleanup_temp_files:
                logger.warning("変換失敗のため、一時的なWAVファイルは削除されませんでした。")
            elif not success and self.cleanup_temp_files:
                # cleanup_temp_files=True でも失敗時は削除しない方がデバッグしやすいかも
                logger.warning("変換失敗のため、一時的なWAVファイルは削除されませんでした。")

        return success


class Recorder:
    """画面録画と音声録音のプロセスを管理・実行するクラス。

    インスタンス化時に録画設定を受け取り、`start()` メソッドで録画を開始し、
    `stop()` メソッドまたは指定された録画時間 (`duration`) に基づいて録画を停止します。

    音声録音 (`_audio_record`) はバックグラウンドスレッドで実行されます。
    画面録画 (`_screen_record`) は `start()` を呼び出したスレッドで実行されます。
    録画停止後、後処理メソッド (`_process_output`) が呼び出されます。

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
    DEFAULT_CHANNELS = 1
    DEFAULT_FPS = 30
    DEFAULT_FFMPEG_PATH = "ffmpeg"

    def __init__(
        self,
        video_filename_temp,
        audio_filename_temp,
        output_filename_final,
        stop_event_ref,
        duration=10,
        fps=DEFAULT_FPS,
        region=None,
        shorts_format=True,
        samplerate=DEFAULT_SAMPLERATE,
        channels=DEFAULT_CHANNELS,
        audio_device_index=None,
        ffmpeg_path=DEFAULT_FFMPEG_PATH,
    ):
        """`Recorder` のインスタンスを初期化します。

        :param video_filename_temp: 画面録画データ（AVI形式）を一時的に保存するファイルパス。
        :type video_filename_temp: str
        :param audio_filename_temp: 音声録音データ（WAV形式）を一時的に保存するファイルパス。
        :type audio_filename_temp: str
        :param output_filename_final: 最終的な出力ファイル（MP4形式を想定）のパス。
                                     現在の実装では、`_process_output` はこのパスから
                                     MP3 ファイル名を生成して使用します。
        :type output_filename_final: str
        :param stop_event_ref: 録画プロセスを外部から停止させるための `threading.Event` オブジェクト。
                               `start()` 実行前に `clear()` され、`stop()` で `set()` されます。
                               `_audio_record` と `_screen_record` のループはこのイベントを監視します。
        :type stop_event_ref: threading.Event
        :param duration: 最大録画時間（秒）。0 を指定すると `stop()` が呼ばれるまで録画を続けます。
                         デフォルトは 10 秒。
        :type duration: int
        :param fps: 画面録画のフレームレート（フレーム/秒）。デフォルトは `Recorder.DEFAULT_FPS`。
        :type fps: int
        :param region: 画面録画を行う領域を指定するタプル `(left, top, width, height)`。
                       `None` の場合は全画面を録画します。デフォルトは `None`。
        :type region: tuple[int, int, int, int] | None
        :param shorts_format: ショート動画形式（縦長 9:16、1080x1920）で出力するかどうか。
                              True の場合、`_resize_and_pad_frame` によってリサイズとパディングが行われます。
                              デフォルトは True。
        :type shorts_format: bool
        :param samplerate: 音声録音のサンプルレート (Hz)。デフォルトは `Recorder.DEFAULT_SAMPLERATE`。
        :type samplerate: int
        :param channels: 音声録音のチャンネル数。デフォルトは `Recorder.DEFAULT_CHANNELS`。
        :type channels: int
        :param audio_device_index: 使用するオーディオ入力デバイスのインデックス。
                                  `sounddevice` が認識するインデックスを指定します。
                                  `None` の場合はデフォルトの入力デバイスを使用します。
                                  デフォルトは `None`。
        :type audio_device_index: int | None
        :param ffmpeg_path: FFmpeg 実行可能ファイルへのパス。内部の `AudioConverter` に渡されます。
                            デフォルトは `Recorder.DEFAULT_FFMPEG_PATH`。
        :type ffmpeg_path: str
        """
        self.video_filename_temp = video_filename_temp
        self.audio_filename_temp = audio_filename_temp
        self.output_filename_final = output_filename_final
        self.stop_event = stop_event_ref
        self.duration = duration
        self.fps = fps
        self.region = region
        self.shorts_format = shorts_format
        self.samplerate = samplerate
        self.channels = channels
        self.audio_device_index = audio_device_index
        self.ffmpeg_path = ffmpeg_path

        #: 音声変換処理を行うための :class:`AudioConverter` インスタンス。
        self.converter = AudioConverter(ffmpeg_path=self.ffmpeg_path)

        #: オーディオコールバックからデータを受け取るためのキュー。
        self.audio_queue = queue.Queue()
        #: オーディオ録音を実行するスレッドオブジェクト。`start()` で生成されます。
        self.audio_recording_thread = None
        #: 画面録画が正常に完了したかどうかを示すフラグ。
        #: (現在は `_screen_record` の最後に True に設定されるのみ)
        self.video_success = False

    def _audio_callback(self, indata, frames, time, status):
        """`sounddevice.InputStream` から呼び出されるコールバック関数。

        受け取ったオーディオデータ (`indata`) のコピーを `self.audio_queue` に追加します。
        ステータス情報があればコンソールに出力します。
        このメソッドはオーディオ入力スレッドのコンテキストで実行されます。

        :param indata: 録音されたオーディオデータを含む NumPy 配列。
                       形状は `(frames, channels)`。
        :type indata: numpy.ndarray
        :param frames: `indata` に含まれるフレーム数。
        :type frames: int
        :param time: コールバックが呼び出された時刻情報 (詳細は sounddevice ドキュメント参照)。
                     このメソッド内では現在使用されていません。
        :type time: ???
        :param status: ストリームの状態を示すフラグ (詳細は sounddevice ドキュメント参照)。
                       問題が発生した場合 (例: オーバーフロー) に情報が含まれます。
        :type status: sounddevice.CallbackFlags
        """
        if status:
            print(status, flush=True)
        self.audio_queue.put(indata.copy())

    def _audio_record(self):
        """オーディオデバイスから録音を開始し、一時 WAV ファイルに書き込む内部メソッド。

        `start()` メソッドからバックグラウンドスレッドで実行されることを想定しています。
        `sounddevice.InputStream` を開き、`_audio_callback` を登録します。
        `self.stop_event` がセットされるまで、`self.audio_queue` からデータを取得し、
        `soundfile.SoundFile` を使って一時 WAV ファイルに書き込み続けます。

        :raises sd.PortAudioError: 指定されたオーディオデバイスが開けないなど、PortAudio関連のエラーが発生した場合。
        :raises ValueError: 指定された `audio_device_index` やパラメータが無効な場合。
        :raises Exception: その他の予期せぬエラー (ファイル書き込みエラーなど)。
        """
        logger.info(f"オーディオ録音開始: {self.audio_filename_temp}")
        try:
            device_info = sd.query_devices(self.audio_device_index, "input")
            logger.debug(f"選択されたデバイス情報: {device_info.get('name', 'N/A')}")  # type: ignore
            actual_samplerate = int(device_info.get("default_samplerate", 0))  # type: ignore
            actual_channels = int(device_info.get("max_input_channels", 0))  # type: ignore
            logger.debug(
                f"デバイスがサポートするサンプルレート: {actual_samplerate}, 最大チャンネル数: {actual_channels}"
            )
            logger.debug(
                f"使用するサンプルレート: {self.samplerate}, チャンネル数: {self.channels}"
            )

            # soundfile で一時 WAV ファイルを開く (追記ではなく新規作成: mode='xb')
            with sf.SoundFile(
                self.audio_filename_temp,
                mode="xb",
                samplerate=self.samplerate,
                channels=self.channels,
                format="WAV",  # 明示的にWAVフォーマットを指定
            ) as file:
                # sounddevice で入力ストリームを開く (エラーがあれば PortAudioError)
                with sd.InputStream(
                    samplerate=self.samplerate,
                    channels=self.channels,
                    callback=self._audio_callback,  # データ受信時のコールバックを登録
                    device=self.audio_device_index,  # 使用するデバイスインデックス
                ):
                    # 停止イベントがセットされるまでループ
                    while not self.stop_event.is_set():
                        try:
                            # キューからデータを取得 (タイムアウト付き)
                            # タイムアウト (queue.Empty) は無視してループを継続
                            data = self.audio_queue.get(timeout=0.1)
                            # 取得したデータをファイルに書き込み
                            file.write(data)
                        except queue.Empty:
                            # キューが空でも stop_event をチェックするためループ継続
                            pass
        except sd.PortAudioError as e:
            logger.error(f"デバイス {self.audio_device_index} の選択時にPortAudioエラーが発生: {e}")
        except ValueError as e:
            logger.error(
                f"無効なデバイスインデックス {self.audio_device_index} またはパラメータ: {e}"
            )
        except Exception as e:
            # ファイルオープン失敗などもここに含まれる可能性
            logger.exception(f"オーディオ録音エラー: {e}")
        finally:
            # 正常終了、エラー発生に関わらず最後に実行
            logger.info(f"オーディオ録音停止: {self.audio_filename_temp}")

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
                # 横幅をターゲットに合わせ、縦幅を計算してリサイズ
                new_w = target_w
                new_h = int(new_w / source_aspect)
                resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
                # 上下に黒帯を追加
                pad_v = target_h - new_h
                pad_top = pad_v // 2
                pad_bottom = pad_v - pad_top
                final_frame = cv2.copyMakeBorder(
                    resized, pad_top, pad_bottom, 0, 0, cv2.BORDER_CONSTANT, value=[0, 0, 0]
                )
            elif source_aspect < target_aspect:  # ソースがターゲットより縦長 (例: 1:2 < 9:16)
                # 縦幅をターゲットに合わせ、横幅を計算してリサイズ
                new_h = target_h
                new_w = int(new_h * source_aspect)
                resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
                # 左右に黒帯を追加
                pad_h = target_w - new_w
                pad_left = pad_h // 2
                pad_right = pad_h - pad_left
                final_frame = cv2.copyMakeBorder(
                    resized, 0, 0, pad_left, pad_right, cv2.BORDER_CONSTANT, value=[0, 0, 0]
                )
            else:  # アスペクト比が同じ (例: 9:16 == 9:16)
                # 単純にターゲットサイズにリサイズ
                final_frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
            return final_frame
        else:
            # shorts_format=False の場合: target_size (録画領域サイズ) に単純リサイズ
            current_h, current_w = frame.shape[:2]
            if current_w != output_width or current_h != output_height:
                # サイズが異なる場合のみリサイズ実行
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

        現在の実装（オーディオテスト目的）:
        1. 最終出力ファイル名に基づいて、`.mp3` の拡張子を持つパスを生成します。
        2. `self.converter.convert_wav_to_mp3` を呼び出し、一時 WAV ファイルを MP3 に変換します。
        3. 一時ビデオファイル (`self.video_filename_temp`) が存在すれば削除します。

        TODO:
        本来は、ここで `self.video_success` を確認し、成功していれば
        一時 AVI ビデオファイルと一時 WAV オーディオファイルを FFmpeg でマージし、
        `self.output_filename_final` (MP4) として保存する処理を実装する必要があります。
        マージ成功後に一時ファイル (AVI, WAV) を削除します。
        """
        # --- === オーディオテスト: WAVからMP3への変換 === ---
        mp3_output_filename = os.path.splitext(self.output_filename_final)[0] + ".mp3"

        # AudioConverter を使って変換を実行
        conversion_success = self.converter.convert_wav_to_mp3(
            self.audio_filename_temp, mp3_output_filename
        )
        if conversion_success:
            logger.info(f"オーディオ変換成功。 {mp3_output_filename} を確認してください。")
        else:
            # スキップまたは失敗した場合
            logger.warning("オーディオ変換に失敗またはスキップされました。")

        # 一時的なビデオファイルのクリーンアップ (変換成否に関わらず実行)
        if os.path.exists(self.video_filename_temp):
            try:
                os.remove(self.video_filename_temp)
                logger.info(
                    f"未使用の一時的なビデオファイルをクリーンアップしました: {self.video_filename_temp}"
                )
            except OSError as e:
                logger.warning(
                    f"一時的なビデオファイル {self.video_filename_temp} のクリーンアップ中にエラー: {e}"
                )

        logger.info(
            f"オーディオテストプロセスが完了しました。{mp3_output_filename} を確認してください。"
        )

        # --- === TODO: オリジナルのマージロジックを復元 === ---
        # if self.video_success and conversion_success: # conversion_success はマージ処理に置き換わる
        #     print(f"ビデオとオーディオのマージを開始: {self.output_filename_final}")
        #     # ffmpeg -i video.avi -i audio.wav -c:v copy -c:a aac output.mp4 のようなコマンド
        #     merge_command = [
        #         self.ffmpeg_path, "-y",
        #         "-i", self.video_filename_temp,
        #         "-i", self.audio_filename_temp,
        #         "-c:v", "copy", # ビデオは再エンコードしない (必要なら調整)
        #         "-c:a", "aac", # オーディオエンコーダ (MP4標準)
        #         "-strict", "experimental", # 古いFFmpegでaacを使う場合に必要かも
        #         self.output_filename_final
        #     ]
        #     try:
        #         # ... subprocess.run でマージ実行 ...
        #         print("マージ成功！")
        #         # マージ成功後に一時ファイルを削除
        #         # os.remove(self.video_filename_temp)
        #         # os.remove(self.audio_filename_temp)
        #     except Exception as e:
        #         print(f"マージ処理中にエラー: {e}")
        # elif not self.video_success:
        #     print("ビデオ録画に失敗したため、マージ処理をスキップしました。")
        # else: # not conversion_success (またはオーディオファイルがない場合)
        #     print("オーディオ処理に失敗またはオーディオがないため、マージ処理をスキップしました。")
        #     # ビデオのみ成功した場合は、一時ビデオを最終ファイル名にリネームするなどの代替案も考えられる
        #     # if os.path.exists(self.video_filename_temp):
        #     #    os.rename(self.video_filename_temp, os.path.splitext(self.output_filename_final)[0] + ".avi")

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

        # オーディオ録音スレッドの準備と開始
        self.audio_recording_thread = threading.Thread(target=self._audio_record, daemon=True)
        self.audio_recording_thread.start()

        # 画面録画を開始 (ブロッキング)
        self._screen_record()

        # --- 録画後の処理 ---
        # 画面録画が終わったら、オーディオスレッドも確実に止めるためにイベントをセット
        self.stop_event.set()

        # オーディオスレッドがキューの書き込みを終えるのを待つ
        # is_alive() チェックは必須 (既に終了している場合があるため)
        if self.audio_recording_thread and self.audio_recording_thread.is_alive():
            logger.debug("オーディオスレッドが終了するのを待機中...")
            # join() でスレッドの終了を待つ。タイムアウトを設定。
            self.audio_recording_thread.join(timeout=5.0)
            # タイムアウト後もまだ生きていたら警告表示
            if self.audio_recording_thread.is_alive():
                logger.warning("警告: オーディオスレッドの結合がタイムアウトしました。")

        # 後処理（音声変換、一時ファイル削除、将来的にはマージ）を実行
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
