import os
import queue
import subprocess
import threading  # Recorder の初期化に stop_event が必要なため
import time
from unittest.mock import ANY, MagicMock, patch

import cv2  # cv2 は比較のためにインポート
import numpy as np
import pyautogui
import pytest
import sounddevice as sd
import soundfile as sf

from media_utils.recorder import AudioConverter, Recorder

# --- Fixtures (Global Scope) ---


@pytest.fixture
def recorder_instance():
    stop_event = threading.Event()
    return Recorder(
        video_filename_temp="dummy.avi",
        audio_filename_temp="dummy.wav",
        output_filename_final="dummy.mp4",
        stop_event_ref=stop_event,
    )


@pytest.fixture
def sample_frame():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame.fill(255)
    return frame


@pytest.fixture
def converter_instance():  # デフォルト cleanup=True
    return AudioConverter(ffmpeg_path="ffmpeg", cleanup_temp_files=True)


@pytest.fixture
def converter_no_cleanup():  # cleanup=False
    return AudioConverter(ffmpeg_path="ffmpeg", cleanup_temp_files=False)


# --- Test Classes ---


class TestRecorderResizeAndPadFrameMethod:
    """Recorder._resize_and_pad_frame メソッドのテストクラス"""

    # --- Fixtures はグローバルスコープへ移動 ---

    # --- Test Methods ---
    def test_resize_no_shorts(self, recorder_instance, sample_frame):
        """shorts_format=False の場合、単純なリサイズ"""
        target_size = (320, 240)
        processed_frame = recorder_instance._resize_and_pad_frame(
            sample_frame, target_size, shorts_format=False
        )
        assert processed_frame.shape == (target_size[1], target_size[0], 3)

    def test_resize_and_pad_shorts_landscape_source(self, recorder_instance):
        """shorts_format=True、入力が横長の場合"""
        source_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        source_frame.fill(255)
        target_size = (1080, 1920)
        processed_frame = recorder_instance._resize_and_pad_frame(
            source_frame, target_size, shorts_format=True
        )
        assert processed_frame.shape == (target_size[1], target_size[0], 3)
        assert np.all(processed_frame[0:656, :, :] == 0)
        assert np.all(processed_frame[1920 - 657 : 1920, :, :] == 0)
        assert np.all(processed_frame[656 : 1920 - 657, :, :] == 255)

    def test_resize_and_pad_shorts_portrait_source(self, recorder_instance):
        """shorts_format=True、入力が縦長の場合"""
        source_frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
        source_frame.fill(255)
        target_size = (1080, 1920)
        processed_frame = recorder_instance._resize_and_pad_frame(
            source_frame, target_size, shorts_format=True
        )
        assert processed_frame.shape == (target_size[1], target_size[0], 3)
        assert np.all(processed_frame == 255)

    def test_resize_and_pad_shorts_square_source(self, recorder_instance):
        """shorts_format=True、入力が正方形の場合"""
        source_frame = np.zeros((1000, 1000, 3), dtype=np.uint8)
        source_frame.fill(255)
        target_size = (1080, 1920)
        processed_frame = recorder_instance._resize_and_pad_frame(
            source_frame, target_size, shorts_format=True
        )
        assert processed_frame.shape == (target_size[1], target_size[0], 3)
        assert np.all(processed_frame[0:420, :, :] == 0)
        assert np.all(processed_frame[1920 - 420 : 1920, :, :] == 0)
        assert np.all(processed_frame[420 : 1920 - 420, :, :] == 255)

    def test_resize_and_pad_shorts_extra_portrait_source(self, recorder_instance):
        """shorts_format=True、入力がターゲットよりさらに縦長の場合"""
        # アスペクト比 1:2 のフレーム (例: 540x1080)
        source_frame = np.zeros((1080, 540, 3), dtype=np.uint8)
        source_frame.fill(255)  # 白で初期化
        target_size = (1080, 1920)  # (width, height) 9:16 縦長

        processed_frame = recorder_instance._resize_and_pad_frame(
            source_frame, target_size, shorts_format=True
        )

        # --- 検証 ---
        # 最終的なサイズはターゲットサイズと同じはず
        assert processed_frame.shape == (target_size[1], target_size[0], 3)

        # source_aspect = 540 / 1080 = 0.5
        # target_aspect = 1080 / 1920 = 0.5625
        # source_aspect < target_aspect の分岐に入るはず
        # new_h = 1920
        # new_w = int(1920 * 0.5) = 960
        # pad_left = (1080 - 960) // 2 = 60
        # pad_right = 1080 - 960 - 60 = 60

        # 左右に黒帯(0)、中央は白(255)のはず
        assert np.all(processed_frame[:, 0:60, :] == 0)  # 左側黒帯
        assert np.all(processed_frame[:, 1080 - 60 : 1080, :] == 0)  # 右側黒帯
        assert np.all(processed_frame[:, 60 : 1080 - 60, :] == 255)  # 中央は白


@patch("media_utils.recorder.os.remove")
@patch("media_utils.recorder.subprocess.run")
@patch("media_utils.recorder.os.path.getsize")
@patch("media_utils.recorder.os.path.exists")
class TestAudioConverterConvertWAVToMP3Method:
    """AudioConverter.convert_wav_to_mp3 メソッドのテストクラス"""

    # --- Fixtures はグローバルスコープへ移動 ---

    # --- Test Methods ---
    def test_convert_wav_to_mp3_success_cleanup(
        self, mock_exists, mock_getsize, mock_run, mock_remove, converter_instance
    ):
        """正常系: 変換成功、一時ファイル削除あり"""
        wav_path = "input.wav"
        mp3_path = "output.mp3"
        mock_exists.return_value = True
        mock_getsize.return_value = 2048
        mock_run.reset_mock()
        mock_remove.reset_mock()

        result = converter_instance.convert_wav_to_mp3(wav_path, mp3_path)

        assert result is True
        mock_exists.assert_called_once_with(wav_path)
        mock_getsize.assert_called_once_with(wav_path)
        mock_run.assert_called_once_with(
            [
                "ffmpeg",
                "-y",
                "-i",
                wav_path,
                "-vn",
                "-acodec",
                "libmp3lame",
                "-ab",
                "192k",
                mp3_path,
            ],
            check=True,
            capture_output=True,
            text=True,
            startupinfo=ANY,
        )
        mock_remove.assert_called_once_with(wav_path)

    def test_convert_wav_to_mp3_success_no_cleanup(
        self, mock_exists, mock_getsize, mock_run, mock_remove, converter_no_cleanup
    ):
        """正常系: 変換成功、一時ファイル削除なし"""
        wav_path = "input.wav"
        mp3_path = "output.mp3"
        mock_exists.return_value = True
        mock_getsize.return_value = 2048
        mock_run.reset_mock()
        mock_remove.reset_mock()

        result = converter_no_cleanup.convert_wav_to_mp3(wav_path, mp3_path)

        assert result is True
        mock_exists.assert_called_once_with(wav_path)
        mock_getsize.assert_called_once_with(wav_path)
        mock_run.assert_called_once()
        mock_remove.assert_not_called()

    def test_convert_wav_to_mp3_file_not_exist(
        self, mock_exists, mock_getsize, mock_run, mock_remove, converter_instance
    ):
        """異常系: 入力ファイルが存在しない"""
        wav_path = "input.wav"
        mp3_path = "output.mp3"
        mock_exists.return_value = False
        mock_getsize.reset_mock()
        mock_run.reset_mock()

        result = converter_instance.convert_wav_to_mp3(wav_path, mp3_path)

        assert result is False
        mock_exists.assert_called_once_with(wav_path)
        mock_getsize.assert_not_called()
        mock_run.assert_not_called()

    def test_convert_wav_to_mp3_file_too_small(
        self, mock_exists, mock_getsize, mock_run, mock_remove, converter_instance
    ):
        """異常系: 入力ファイルサイズが小さい"""
        wav_path = "input.wav"
        mp3_path = "output.mp3"
        mock_exists.return_value = True
        mock_getsize.return_value = 512
        mock_run.reset_mock()

        result = converter_instance.convert_wav_to_mp3(wav_path, mp3_path)

        assert result is False
        mock_exists.assert_called_once_with(wav_path)
        mock_getsize.assert_called_once_with(wav_path)
        mock_run.assert_not_called()

    def test_convert_wav_to_mp3_ffmpeg_called_process_error(
        self, mock_exists, mock_getsize, mock_run, mock_remove, converter_instance
    ):
        """異常系: FFmpeg実行でCalledProcessError"""
        wav_path = "input.wav"
        mp3_path = "output.mp3"
        mock_exists.return_value = True
        mock_getsize.return_value = 2048
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["ffmpeg", "..."], stderr="ffmpeg error"
        )
        mock_remove.reset_mock()

        result = converter_instance.convert_wav_to_mp3(wav_path, mp3_path)

        assert result is False
        mock_run.assert_called_once()
        mock_remove.assert_not_called()
        mock_run.side_effect = None

    def test_convert_wav_to_mp3_ffmpeg_file_not_found_error(
        self, mock_exists, mock_getsize, mock_run, mock_remove, converter_instance
    ):
        """異常系: FFmpeg実行でFileNotFoundError"""
        wav_path = "input.wav"
        mp3_path = "output.mp3"
        mock_exists.return_value = True
        mock_getsize.return_value = 2048
        mock_run.side_effect = FileNotFoundError("ffmpeg not found")
        mock_remove.reset_mock()

        result = converter_instance.convert_wav_to_mp3(wav_path, mp3_path)

        assert result is False
        mock_run.assert_called_once()
        mock_remove.assert_not_called()
        mock_run.side_effect = None


# --- Recorder._audio_record のテストクラス ---


# sd.query_devices, sf.SoundFile, sd.InputStream をモック
@patch("media_utils.recorder.sd.InputStream")
@patch("media_utils.recorder.sf.SoundFile")
@patch("media_utils.recorder.sd.query_devices")
class TestRecorderAudioRecordMethod:
    """Recorder._audio_record メソッドのテストクラス"""

    # テストメソッドがモックオブジェクトとグローバル fixture を引数で受け取る
    def test_audio_record_success_stops_on_event(
        self, mock_query_devices, mock_SoundFile, mock_InputStream, recorder_instance
    ):
        """正常系: ストリームを開始し、stop_event で停止、ファイル書き込みを行う"""

        # --- メソッド開始時にモックをリセット ---
        mock_query_devices.reset_mock()
        mock_SoundFile.reset_mock()
        mock_InputStream.reset_mock()
        # デフォルトの戻り値を設定 (必要なら)
        mock_query_devices.return_value = {
            "name": "Mock Input Device",
            "default_samplerate": recorder_instance.samplerate,  # recorder_instance の値を使用
            "max_input_channels": recorder_instance.channels,
        }
        mock_query_devices.side_effect = None
        mock_SoundFile.side_effect = None
        mock_InputStream.side_effect = None

        stop_event = recorder_instance.stop_event
        audio_queue = recorder_instance.audio_queue
        audio_file_path = recorder_instance.audio_filename_temp
        samplerate = recorder_instance.samplerate
        channels = recorder_instance.channels
        device_index = recorder_instance.audio_device_index

        # Mock SoundFile context manager and write method
        mock_sf_cm = mock_SoundFile.return_value.__enter__.return_value

        # Mock InputStream context manager
        mock_is_cm = mock_InputStream.return_value.__enter__.return_value

        # Simulate data arriving in the queue via the callback
        dummy_data_chunk_1 = np.array([[0.1, 0.2]], dtype=np.float32)
        dummy_data_chunk_2 = np.array([[0.3, 0.4]], dtype=np.float32)

        # --- モックの設定 ---
        # 1. audio_queue.get() の動作をシミュレート
        call_count = 0

        def queue_get_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return dummy_data_chunk_1
            elif call_count == 2:
                return dummy_data_chunk_2
            else:
                # stop_event がセットされるまで Empty を発生させる
                if not stop_event.is_set():
                    raise queue.Empty
                else:
                    # イベントがセットされたらテストを終了させるために例外を投げる
                    # (実際のコードはループを抜けるだけ)
                    raise TimeoutError("Stop event set")

        recorder_instance.audio_queue.get = MagicMock(side_effect=queue_get_side_effect)

        # --- テスト実行 ---
        # _audio_record はブロッキングするため別スレッドで実行
        thread = threading.Thread(target=recorder_instance._audio_record)
        thread.start()

        # スレッドがキューのデータを処理するのを少し待つ
        time.sleep(0.5)

        # 停止イベントをセット
        stop_event.set()

        # スレッドの終了を待つ (TimeoutErrorで抜けることを期待)
        thread.join(timeout=2.0)

        # --- 検証 ---
        assert not thread.is_alive(), "スレッドが時間内に終了しませんでした"

        # query_devices が呼ばれたか
        mock_query_devices.assert_called_once_with(device_index, "input")

        # SoundFile が正しい引数で呼ばれたか
        mock_SoundFile.assert_called_once_with(
            audio_file_path, mode="xb", samplerate=samplerate, channels=channels, format="WAV"
        )

        # InputStream が正しい引数で呼ばれたか
        mock_InputStream.assert_called_once_with(
            samplerate=samplerate,
            channels=channels,
            callback=recorder_instance._audio_callback,  # Ensure the instance method is used
            device=device_index,
        )

        # SoundFile の write メソッドが呼ばれたか (データと共に)
        assert mock_sf_cm.write.call_count == 2
        # np.array_equal で比較するために call_args を取得
        call_args_list = mock_sf_cm.write.call_args_list
        assert np.array_equal(call_args_list[0][0][0], dummy_data_chunk_1)
        assert np.array_equal(call_args_list[1][0][0], dummy_data_chunk_2)

        # queue.get が複数回呼ばれたか (Empty 含め)
        assert recorder_instance.audio_queue.get.call_count > 2


# --- Recorder._screen_record のテストクラス ---


# モック対象が多い: pyautogui, cv2, time, _resize_and_pad_frame
@patch("media_utils.recorder.Recorder._resize_and_pad_frame")  # メソッド自体をモック
@patch("media_utils.recorder.time.sleep")
@patch("media_utils.recorder.time.time")
@patch("media_utils.recorder.cv2.VideoWriter")  # cv2.VideoWriter をモック
@patch("media_utils.recorder.pyautogui.size")
@patch("media_utils.recorder.pyautogui.screenshot")
class TestRecorderScreenRecordMethod:
    """Recorder._screen_record メソッドのテストクラス"""

    def test_screen_record_success_stops_on_event(
        self,
        mock_screenshot,
        mock_size,
        mock_VideoWriter,
        mock_time,
        mock_sleep,
        mock_resize_and_pad,  # モックされたメソッド
        recorder_instance,  # fixture
    ):
        """正常系: スクリーンショット取得、リサイズ、書き込みを行い、イベントで停止"""
        # --- モックの準備 ---
        mock_screenshot.reset_mock()
        mock_size.reset_mock()
        mock_VideoWriter.reset_mock()
        mock_time.reset_mock()  # time は sleep 計算等で呼ばれるのでモック自体は残す
        mock_sleep.reset_mock()
        mock_resize_and_pad.reset_mock()

        mock_size.return_value = (1920, 1080)
        dummy_screenshot_frame = np.full((1080, 1920, 3), 128, dtype=np.uint8)

        # --- 修正: screenshot の side_effect でループ回数を制御 ---
        loop_count_target = 3
        screenshot_call_count = 0
        stop_event = recorder_instance.stop_event
        stop_event.clear()

        def screenshot_side_effect(*args, **kwargs):
            nonlocal screenshot_call_count
            screenshot_call_count += 1
            print(f"Screenshot called: {screenshot_call_count}")  # デバッグ用
            if screenshot_call_count >= loop_count_target:
                print("Setting stop event")  # デバッグ用
                stop_event.set()  # 目標回数に達したら停止イベントをセット
            return dummy_screenshot_frame

        mock_screenshot.side_effect = screenshot_side_effect

        mock_writer_instance = mock_VideoWriter.return_value
        mock_writer_instance.isOpened.return_value = True

        # time.time は duration チェックでは使われなくなったが、sleep 計算で使われるため、適当な値を返すようにする
        mock_time.return_value = 100.0

        processed_dummy_frame = np.full((1920, 1080, 3), 100, dtype=np.uint8)
        mock_resize_and_pad.return_value = processed_dummy_frame

        recorder_instance.region = None
        recorder_instance.shorts_format = False
        recorder_instance.fps = 20
        recorder_instance.duration = (
            0  # duration=0 で無限ループになるように変更 (停止はイベント頼み)
        )

        # --- テスト実行 ---
        thread = threading.Thread(target=recorder_instance._screen_record)
        thread.start()

        # スレッドが終了するのを待つ (screenshot の side_effect で stop_event がセットされるはず)
        thread.join(timeout=2.0)  # タイムアウトを少し長めに

        # --- 検証 ---
        assert not thread.is_alive(), "スレッドが時間内に終了しませんでした"
        assert recorder_instance.video_success is True, "video_success が True になっていません"

        mock_size.assert_called_once()
        expected_width, expected_height = 1920, 1080
        expected_fps = recorder_instance.fps
        expected_fourcc = cv2.VideoWriter_fourcc(*"DIVX")  # type: ignore
        mock_VideoWriter.assert_called_once_with(
            recorder_instance.video_filename_temp,
            expected_fourcc,
            expected_fps,
            (expected_width, expected_height),
        )
        mock_writer_instance.isOpened.assert_called()

        # ループ回数 (screenshot 呼び出し回数) を確認
        assert mock_screenshot.call_count == loop_count_target
        assert mock_resize_and_pad.call_count == loop_count_target
        assert mock_writer_instance.write.call_count == loop_count_target

        # _resize_and_pad_frame の引数を確認 (最後の呼び出し)
        last_call_args, _ = mock_resize_and_pad.call_args
        assert last_call_args[1] == (expected_width, expected_height)
        assert last_call_args[2] is False

        # VideoWriter.write の引数を確認 (最後の呼び出し)
        last_write_args, _ = mock_writer_instance.write.call_args
        assert np.array_equal(last_write_args[0], processed_dummy_frame)

        # sleep が呼ばれたか (回数は実行タイミングによるので > 0 で確認)
        assert mock_sleep.call_count > 0

        mock_writer_instance.release.assert_called_once()


# --- Recorder._process_output のテストクラス ---


@patch("media_utils.recorder.os.remove")
@patch("media_utils.recorder.os.path.exists")
# AudioConverter.convert_wav_to_mp3 を直接モック
@patch.object(AudioConverter, "convert_wav_to_mp3")
class TestRecorderProcessOutputMethod:
    """Recorder._process_output メソッドのテストクラス"""

    def test_process_output_success(
        self, mock_convert, mock_exists, mock_remove, recorder_instance
    ):
        """正常系: 音声変換成功、一時ビデオファイル削除"""
        # --- モックの準備 ---
        mock_convert.reset_mock()
        mock_exists.reset_mock()
        mock_remove.reset_mock()

        mock_convert.return_value = True  # 変換成功とする

        # 一時ビデオファイルは存在するものとする
        # os.path.exists が self.video_filename_temp で呼ばれたら True を返す
        def exists_side_effect(path):
            if path == recorder_instance.video_filename_temp:
                return True
            # 他のパス（例えば音声ファイル）のチェックには影響しないように
            # return os.path.exists(path) # 本物を呼ぶとテストが不安定になる可能性
            return False  # ここではシンプルに False を返す

        mock_exists.side_effect = exists_side_effect

        temp_video_file = recorder_instance.video_filename_temp
        temp_audio_file = recorder_instance.audio_filename_temp
        # 期待される mp3 出力パス
        expected_mp3_path = os.path.splitext(recorder_instance.output_filename_final)[0] + ".mp3"

        # --- テスト実行 ---
        recorder_instance._process_output()

        # --- 検証 ---
        # convert_wav_to_mp3 が呼ばれたか
        mock_convert.assert_called_once_with(temp_audio_file, expected_mp3_path)

        # os.path.exists が一時ビデオファイルのパスで呼ばれたか
        # mock_exists は複数回呼ばれる可能性があるので、 specific call を確認
        mock_exists.assert_any_call(temp_video_file)

        # os.remove が一時ビデオファイルのパスで呼ばれたか
        mock_remove.assert_called_once_with(temp_video_file)


# --- Recorder.start のテストクラス ---


# start メソッドが呼び出すコンポーネントをモック
@patch("media_utils.recorder.Recorder._process_output")
@patch("media_utils.recorder.Recorder._screen_record")
@patch("media_utils.recorder.Recorder._audio_record")
@patch("media_utils.recorder.threading.Thread")  # threading.Thread をモック
class TestRecorderStartMethod:
    """Recorder.start メソッドのテストクラス"""

    def test_start_calls_methods_in_order(
        self,
        mock_Thread,
        mock_audio_record,
        mock_screen_record,
        mock_process_output,
        recorder_instance,
    ):
        """正常系: start が各メソッドを正しい順序で呼び出すか"""
        # --- モック準備 ---
        mock_Thread.reset_mock()
        mock_audio_record.reset_mock()
        mock_screen_record.reset_mock()
        mock_process_output.reset_mock()

        mock_thread_instance = mock_Thread.return_value
        # --- 修正: is_alive が最初は True, join 後に False を返すように ---
        is_alive_states = [True, False]  # 最初の is_alive() は True, join 後の is_alive() は False

        def is_alive_side_effect(*args, **kwargs):
            return is_alive_states.pop(0) if is_alive_states else False

        mock_thread_instance.is_alive.side_effect = is_alive_side_effect

        stop_event = recorder_instance.stop_event
        stop_event.clear()

        # --- テスト実行 ---
        recorder_instance.start()

        # --- 検証 ---
        # 1. stop_event がクリアされている (開始時に clear される)
        #    -> clear 自体の確認は難しいので、最後に set されるかで間接的に確認

        # 2. オーディオスレッドが開始される
        mock_Thread.assert_called_once_with(target=mock_audio_record, daemon=True)
        mock_thread_instance.start.assert_called_once()

        # 3. 画面録画が開始される (オーディオスレッド開始後)
        mock_screen_record.assert_called_once()

        # 4. 画面録画終了後 (またはエラー時) に stop_event がセットされる
        #    (start メソッドのロジック上、_screen_record の後に set される)
        #    ただし、_screen_record 内でエラーが発生し stop() が呼ばれた場合も set される
        #    ここでは正常系として _screen_record 後に set されることを期待
        assert stop_event.is_set(), "stop_event がセットされていません"

        # 5. オーディオスレッドの終了を待つ (is_alive が True の場合に join が呼ばれる)
        mock_thread_instance.join.assert_called_once_with(timeout=5.0)
        # is_alive が2回呼ばれたことも確認 (if 文の評価と、必要なら join 後の確認)
        assert mock_thread_instance.is_alive.call_count >= 1

        # 6. 後処理が呼ばれる
        mock_process_output.assert_called_once()


# --- Recorder.stop のテストクラス ---


class TestRecorderStopMethod:
    """Recorder.stop メソッドのテストクラス"""

    def test_stop_sets_event(self, recorder_instance):
        """stop() が stop_event をセットするか確認"""
        stop_event = recorder_instance.stop_event

        # 初期状態ではクリアされているはず (fixture で生成)
        assert not stop_event.is_set(), "テスト開始時にイベントがセットされています"

        # --- テスト実行 ---
        recorder_instance.stop()

        # --- 検証 ---
        assert stop_event.is_set(), "stop() 呼び出し後にイベントがセットされていません"


# --- Recorder._audio_callback のテストクラス ---


class TestRecorderAudioCallbackMethod:
    """Recorder._audio_callback メソッドのテストクラス"""

    def test_audio_callback_puts_data_in_queue(self, recorder_instance):
        """_audio_callback がデータをキューに追加することを確認"""
        audio_queue = recorder_instance.audio_queue
        # キューが空であることを確認
        assert audio_queue.empty()

        dummy_indata = np.array([[0.5, -0.5]], dtype=np.float32)
        frames = len(dummy_indata)
        current_time = time.time()
        status = None

        # --- テスト実行 ---
        recorder_instance._audio_callback(dummy_indata, frames, current_time, status)

        # --- 検証 ---
        assert not audio_queue.empty(), "データがキューに追加されていません"
        # キューから取得してデータが一致するか確認
        try:
            queued_data = audio_queue.get_nowait()
            assert np.array_equal(queued_data, dummy_indata)
            # 元データとコピーが別のオブジェクトであることも確認 (indata.copy() のため)
            assert queued_data is not dummy_indata
        except queue.Empty:
            pytest.fail("キューからデータを取得できませんでした")

    @patch("builtins.print")  # print 関数をモック
    def test_audio_callback_prints_status(self, mock_print, recorder_instance):  # 引数に mock_print
        """_audio_callback が status を print することを確認"""
        dummy_indata = np.array([[0.1]], dtype=np.float32)
        frames = len(dummy_indata)
        current_time = time.time()
        test_status = "Input overflowed"

        # --- テスト実行 ---
        recorder_instance._audio_callback(dummy_indata, frames, current_time, test_status)

        # --- 修正: print が呼ばれたことを検証 ---
        mock_print.assert_called_once_with(test_status, flush=True)
