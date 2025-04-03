import json
import os
import threading  # threading.Event のため
from unittest.mock import ANY, MagicMock, patch

import pytest

# グローバル変数をテスト間でリセットするために import
import app as app_module
from app import app as flask_app  # Flask アプリケーションインスタンスをインポート

# --- Fixtures ---


@pytest.fixture
def app():
    """Flask アプリケーションのテスト用インスタンスを作成"""
    flask_app.config.update(
        {
            "TESTING": True,
            # テスト用に一時フォルダや録画フォルダを変更することも可能
            "RECORDINGS_FOLDER": "test_recordings",
            "TEMP_FOLDER": "test_temp",
        }
    )
    # テスト用ディレクトリ作成
    os.makedirs(flask_app.config["RECORDINGS_FOLDER"], exist_ok=True)
    os.makedirs(flask_app.config["TEMP_FOLDER"], exist_ok=True)

    yield flask_app

    # クリーンアップ: テスト後にグローバル状態をリセット
    app_module.recording = False
    app_module.recorder_instance = None
    app_module.main_recording_thread = None
    app_module.stop_event = threading.Event()  # イベントも再生成
    app_module.current_status_info = {"final_output": None}
    # テスト用ディレクトリ削除 (中身も)
    # import shutil
    # shutil.rmtree(flask_app.config["RECORDINGS_FOLDER"], ignore_errors=True)
    # shutil.rmtree(flask_app.config["TEMP_FOLDER"], ignore_errors=True)
    # Note: shutil を使う場合は import が必要。また、Windowsでの削除権限に注意。
    # 簡単のため、ここではディレクトリ自体は残す。


@pytest.fixture
def client(app):
    """Flask テストクライアントを作成"""
    return app.test_client()


# --- Test Classes ---

# --- 修正: エンドポイントごとにクラスを分割 ---


class TestIndexRoute:
    """/ ルートのテストクラス"""

    def test_index_route(self, client):
        """GET / : ルートにアクセスできるか、基本的なコンテンツが含まれるか"""
        response = client.get("/")
        assert response.status_code == 200
        assert b"Screen Recorder" in response.data
        assert b"Start Recording" in response.data

    # --- クリーンアップ処理のテスト ---
    @patch("app.render_template")  # render_template は呼び出されるが結果は不要
    @patch("app.os.remove")
    @patch("app.os.path.isfile")
    @patch("app.os.listdir")
    @patch("app.os.path.isdir")
    @patch("app.os.path.exists")
    def test_index_cleans_temp_files(
        self,
        mock_exists,
        mock_isdir,
        mock_listdir,
        mock_isfile,
        mock_remove,
        mock_render_template,
        client,
        app,
    ):
        """GET / : 一時ファイルが正しくクリーンアップされるか"""
        # --- モック準備 ---
        temp_dir = app.config["TEMP_FOLDER"]
        rec_dir = app.config["RECORDINGS_FOLDER"]

        # ディレクトリは存在し、ディレクトリであるとする
        mock_exists.return_value = True
        mock_isdir.return_value = True

        # 各ディレクトリの内容
        temp_files = ["screen_recording_1.avi", "other.txt", "screen_recording_2.mp3"]
        rec_files = ["screen_recording_3.wav", "final.mp4", "subdir"]

        def listdir_side_effect(path):
            if path == temp_dir:
                return temp_files
            if path == rec_dir:
                return rec_files
            return []

        mock_listdir.side_effect = listdir_side_effect

        # isfile の挙動 (subdir のみ False)
        def isfile_side_effect(path):
            return not path.endswith("subdir")

        mock_isfile.side_effect = isfile_side_effect

        # remove は成功する
        mock_remove.return_value = None

        # --- テスト実行 ---
        response = client.get("/")

        # --- 検証 ---
        assert response.status_code == 200  # クリーンアップ成否に関わらず 200
        mock_render_template.assert_called_once_with("index.html")

        # listdir が両方のディレクトリで呼ばれたか
        assert mock_listdir.call_count == 2
        mock_listdir.assert_any_call(temp_dir)
        mock_listdir.assert_any_call(rec_dir)

        # isfile がすべてのエントリに対して呼ばれたか
        assert mock_isfile.call_count == len(temp_files) + len(rec_files)

        # remove が削除対象ファイルに対してのみ呼ばれたか
        expected_removed = [
            os.path.join(temp_dir, "screen_recording_1.avi"),
            os.path.join(temp_dir, "screen_recording_2.mp3"),
            os.path.join(rec_dir, "screen_recording_3.wav"),
        ]
        assert mock_remove.call_count == len(expected_removed)
        for path in expected_removed:
            mock_remove.assert_any_call(path)

    @patch("app.render_template")
    @patch("app.os.remove")
    @patch("app.os.path.isfile")
    @patch("app.os.listdir")
    @patch("app.os.path.isdir")
    @patch("app.os.path.exists")
    def test_index_cleanup_handles_oserror(
        self,
        mock_exists,
        mock_isdir,
        mock_listdir,
        mock_isfile,
        mock_remove,
        mock_render_template,
        client,
        app,
    ):
        """GET / : ファイル削除中に OSError が発生しても処理が継続されるか"""
        temp_dir = app.config["TEMP_FOLDER"]
        rec_dir = app.config["RECORDINGS_FOLDER"]
        mock_exists.return_value = True
        mock_isdir.return_value = True

        def listdir_side_effect(path):
            if path == temp_dir:
                return ["screen_recording_err.avi"]
            if path == rec_dir:
                return ["screen_recording_err.avi"]
            return []

        mock_listdir.side_effect = listdir_side_effect
        mock_isfile.return_value = True
        mock_remove.side_effect = OSError("Permission denied")

        response = client.get("/")

        assert response.status_code == 200
        assert mock_remove.call_count == 2
        mock_remove.assert_any_call(os.path.join(temp_dir, "screen_recording_err.avi"))
        mock_remove.assert_any_call(os.path.join(rec_dir, "screen_recording_err.avi"))

    @patch("app.render_template")
    @patch("app.os.remove")
    @patch("app.os.path.isfile")
    @patch("app.os.listdir")
    @patch("app.os.path.isdir")
    @patch("app.os.path.exists")
    def test_index_cleanup_skips_nonexistent_dir(
        self,
        mock_exists,
        mock_isdir,
        mock_listdir,
        mock_isfile,
        mock_remove,
        mock_render_template,
        client,
        app,
    ):
        """GET / : 存在しないディレクトリはスキップされるか"""
        temp_dir = app.config["TEMP_FOLDER"]
        rec_dir = app.config["RECORDINGS_FOLDER"]

        def exists_side_effect(path):
            if path == temp_dir:
                return False  # TEMP_FOLDER が存在しない
            if path == rec_dir:
                return True
            return False

        mock_exists.side_effect = exists_side_effect
        mock_isdir.return_value = True  # rec_dir はディレクトリとする
        mock_listdir.return_value = []  # rec_dir は空とする

        response = client.get("/")

        assert response.status_code == 200
        # exists が TEMP_FOLDER と RECORDINGS_FOLDER で呼ばれる
        assert mock_exists.call_count == 2
        # isdir は TEMP_FOLDER(exists=False) では呼ばれず、REC_FOLDER でのみ呼ばれる
        mock_isdir.assert_called_once_with(rec_dir)
        # listdir は TEMP_FOLDER では呼ばれず、REC_FOLDER でのみ呼ばれる
        mock_listdir.assert_called_once_with(rec_dir)
        mock_isfile.assert_not_called()
        mock_remove.assert_not_called()


class TestStatusRoute:
    """/status ルートのテストクラス"""

    def test_status_route_initially(self, client):
        """GET /status : 初期状態で recording: False が返るか"""
        response = client.get("/status")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["recording"] is False
        assert data["current_file"] is None


class TestStartRecordingRoute:
    """/start_recording ルートのテストクラス"""

    @patch("app.threading.Thread")
    @patch("app.sd.query_hostapis")
    @patch("app.sd.query_devices")
    @patch("app.Recorder")
    def test_start_recording_success_valid_json(
        self, mock_Recorder, mock_query_devices, mock_query_hostapis, mock_Thread, client, app
    ):
        """POST /start_recording : 正常系 (マイクが見つかり、ステミキが見つからないケース)"""
        # --- モック準備 ---
        mock_recorder_instance = mock_Recorder.return_value
        mock_thread_instance = mock_Thread.return_value
        
        # MME API のモック
        mock_query_hostapis.return_value = [
            {"name": "MME", "devices": [3, 4, 5]}
        ]
        
        # デバイス一覧のモック
        mock_devices = [
            {
                "index": 3,
                "name": "Microphone Array",
                "hostapi": 0,  # MME APIのインデックス
                "max_input_channels": 1,
            "default_samplerate": 48000,
            },
            {
                "index": 4,
                "name": "マイク (Some Other Device)",
                "hostapi": 0,  # MME APIのインデックス
            "max_input_channels": 1,
                "default_samplerate": 44100,
            },
            {
                "index": 5,
                "name": "Some Output Device",
                "hostapi": 0,  # MME APIのインデックス
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 48000,
            }
        ]
        
        mock_query_devices.return_value = mock_devices

        request_data = {
            "duration": 15,
            "fps": 24,
            "shorts_format": False,
            "region_enabled": False,
        }
        response = client.post("/start_recording", json=request_data)

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "success"
        assert app_module.recording is True
        mock_Recorder.assert_called_once()
        args, kwargs = mock_Recorder.call_args

        # --- 修正: Recorder 引数の検証 ---
        assert kwargs["duration"] == 15
        assert kwargs["fps"] == 24
        assert kwargs["shorts_format"] is False
        assert kwargs["region"] is None
        # Mic settings - インデックス3のデバイスが見つかるはず
        assert kwargs["mic_device_index"] == 3
        assert kwargs["mic_samplerate"] == 48000
        assert kwargs["mic_channels"] == 1
        assert "_mic_temp.wav" in kwargs["mic_audio_filename_temp"]
        # Sys settings - システム音声デバイスは見つからないはず
        assert kwargs["sys_device_index"] is None
        assert kwargs["sys_samplerate"] is not None  # Default value is set
        assert kwargs["sys_channels"] is not None  # Default value is set
        assert "_sys_temp.wav" in kwargs["sys_audio_filename_temp"]
        # Other args
        assert "output_filename_final" in kwargs
        assert "stop_event_ref" in kwargs
        assert "ffmpeg_path" in kwargs

        mock_Thread.assert_called_once()
        mock_thread_instance.start.assert_called_once()

    @patch("app.threading.Thread")
    @patch("app.sd.query_hostapis")
    @patch("app.sd.query_devices")
    @patch("app.pyautogui.size")
    @patch("app.Recorder")
    def test_start_recording_success_target_device(
        self, mock_Recorder, mock_pyautogui_size, mock_query_devices, mock_query_hostapis, mock_Thread, client, app
    ):
        """POST /start_recording : 正常系 (マイクとステミキの両方が見つかるケース)"""
        # --- モック準備 ---
        mock_recorder_instance = mock_Recorder.return_value
        mock_thread_instance = mock_Thread.return_value
        mock_query_devices.reset_mock()
        mock_Recorder.reset_mock()
        mock_Thread.reset_mock()

        # MME API のモック
        mock_query_hostapis.return_value = [
            {"name": "MME", "devices": [1, 2, 3]}
        ]
        
        # デバイス一覧のモック
        mock_devices = [
            {
                "index": 1,
                "name": "Microphone Array",
                "hostapi": 0,  # MME APIのインデックス
                "max_input_channels": 2,
            "default_samplerate": 44100,
            },
            {
                "index": 2,
                "name": "Stereo Mix",
                "hostapi": 0,  # MME APIのインデックス
            "max_input_channels": 2,
                "default_samplerate": 48000,
            },
            {
                "index": 3,
                "name": "Some Output Device",
                "hostapi": 0,  # MME APIのインデックス
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 48000,
            }
        ]
        
        mock_query_devices.return_value = mock_devices

        request_data = {"duration": 10, "fps": 30, "shorts_format": True, "region_enabled": False}
        response = client.post("/start_recording", json=request_data)

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "success"
        assert app_module.recording is True

        mock_Recorder.assert_called_once()
        args, kwargs = mock_Recorder.call_args
        # --- 修正: Recorder 引数の検証 ---
        assert kwargs["duration"] == 10
        assert kwargs["fps"] == 30
        assert kwargs["shorts_format"] is True
        assert kwargs["region"] is None
        # Mic settings - インデックス1のデバイスが見つかるはず
        assert kwargs["mic_device_index"] == 1
        assert kwargs["mic_samplerate"] == 44100
        assert kwargs["mic_channels"] == 2
        assert "_mic_temp.wav" in kwargs["mic_audio_filename_temp"]
        # Sys settings - インデックス2のデバイスが見つかるはず
        assert kwargs["sys_device_index"] == 2
        assert kwargs["sys_samplerate"] == 48000
        assert kwargs["sys_channels"] == 2
        assert "_sys_temp.wav" in kwargs["sys_audio_filename_temp"]
        # Other args
        assert "output_filename_final" in kwargs
        assert "stop_event_ref" in kwargs
        assert "ffmpeg_path" in kwargs

        mock_Thread.assert_called_once()
        mock_thread_instance.start.assert_called_once()

    # --- TODO: Add more tests for /start_recording ---


# --- /stop_recording のテストクラス ---


class TestStopRecordingRoute:
    """/stop_recording ルートのテストクラス"""

    @patch("app.recorder_instance")  # グローバル変数をモックする
    def test_stop_recording_success(self, mock_recorder_global, client, app):
        """POST /stop_recording : 正常系 (録画中に停止)"""
        # --- 前提条件: 録画中の状態にする ---
        app_module.recording = True
        # recorder_instance に stop メソッドを持つモックを設定
        mock_recorder_instance = MagicMock()
        app_module.recorder_instance = mock_recorder_instance
        # または @patch('app.Recorder') を使ってインスタンスを注入しても良い
        # mock_recorder_global = mock_Recorder.return_value # この場合

        # --- テスト実行 ---
        response = client.post("/stop_recording")

        # --- 検証 ---
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "success"
        assert "録画停止リクエストを送信しました" in data["message"]

        # Recorder の stop() が呼ばれたか
        mock_recorder_instance.stop.assert_called_once()
        # 注意: recording フラグはこのリクエストハンドラ内では変更されず、
        #       バックグラウンドスレッド (run_recording_process) の finally で False になる想定。
        #       ここでは True のままであることを確認（必要なら）。
        # assert app_module.recording is True

    # --- 修正: このテストでは recorder_instance のモックは不要 ---
    # @patch('app.recorder_instance')
    def test_stop_recording_not_recording(self, client, app):
        """POST /stop_recording : エラー系 (録画中でない場合)"""
        # モックオブジェクトを引数から削除 (mock_recorder_global)
        # --- 前提条件: 録画中でない状態 (fixture のクリーンアップ後) ---
        assert app_module.recording is False
        assert app_module.recorder_instance is None

        # --- テスト実行 ---
        response = client.post("/stop_recording")

        # --- 検証 ---
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "error"
        assert "録画していません" in data["message"]

        # Recorder の stop() が呼ばれないこと (recorder_instance が None なので当然呼ばれない)


# --- /recordings のテストクラス ---


# モック対象: os.listdir, os.path.isfile, os.path.getmtime
# これらの関数は app モジュール内で直接呼ばれているため、'app.os.listdir' のように指定
@patch("app.os.path.getmtime")
@patch("app.os.path.isfile")
@patch("app.os.listdir")
class TestRecordingsRoute:
    """/recordings ルートのテストクラス"""

    def test_list_recordings_success(self, mock_listdir, mock_isfile, mock_getmtime, client, app):
        """GET /recordings : 正常系 (ファイルが存在し、正しくフィルタ・ソートされる)"""
        # --- モック準備 ---
        recordings_dir = app.config["RECORDINGS_FOLDER"]
        # モックするファイルリスト (拡張子違い、ディレクトリ含む)
        mock_files = ["rec_2.mp4", "rec_1.mp3", "other.txt", "subdir", "rec_3.MP4"]
        mock_listdir.return_value = mock_files

        # isfile の挙動 (mp4/mp3 は True, 他は False)
        def isfile_side_effect(path):
            filename = os.path.basename(path)
            if filename in ["rec_2.mp4", "rec_1.mp3", "rec_3.MP4"]:
                return True
            return False

        mock_isfile.side_effect = isfile_side_effect

        # getmtime の挙動 (ソート順確認のため、逆順のタイムスタンプを返す)
        def getmtime_side_effect(path):
            filename = os.path.basename(path)
            if filename == "rec_3.MP4":
                return 100.0  # 一番新しい
            if filename == "rec_2.mp4":
                return 50.0
            if filename == "rec_1.mp3":
                return 10.0  # 一番古い
            return 0.0

        mock_getmtime.side_effect = getmtime_side_effect

        # --- テスト実行 ---
        response = client.get("/recordings")

        # --- 検証 ---
        assert response.status_code == 200
        data = json.loads(response.data)
        assert "recordings" in data
        # フィルタリング (mp4/mp3のみ) とソート (新しい順) が行われているか
        expected_list = ["rec_3.MP4", "rec_2.mp4", "rec_1.mp3"]
        assert data["recordings"] == expected_list

        # os.listdir が呼ばれたか
        mock_listdir.assert_called_once_with(recordings_dir)
        # isfile, getmtime が適切な回数呼ばれたか
        assert mock_isfile.call_count == len(
            expected_list
        )  # isfile は拡張子チェック通過後に呼ばれる
        assert mock_getmtime.call_count == len(expected_list)  # ソート対象のみ

    def test_list_recordings_empty(self, mock_listdir, mock_isfile, mock_getmtime, client, app):
        """GET /recordings : 正常系 (録画フォルダが空の場合)"""
        # --- モック準備 ---
        recordings_dir = app.config["RECORDINGS_FOLDER"]
        mock_listdir.return_value = []  # 空リストを返す
        mock_isfile.reset_mock()
        mock_getmtime.reset_mock()

        # --- テスト実行 ---
        response = client.get("/recordings")

        # --- 検証 ---
        assert response.status_code == 200
        data = json.loads(response.data)
        assert "recordings" in data
        assert data["recordings"] == []
        mock_listdir.assert_called_once_with(recordings_dir)
        mock_isfile.assert_not_called()
        mock_getmtime.assert_not_called()

    def test_list_recordings_dir_not_found(
        self, mock_listdir, mock_isfile, mock_getmtime, client, app
    ):
        """GET /recordings : 異常系 (録画フォルダが存在しない場合)"""
        # --- モック準備 ---
        recordings_dir = app.config["RECORDINGS_FOLDER"]
        mock_listdir.side_effect = FileNotFoundError  # エラーを発生させる
        mock_isfile.reset_mock()
        mock_getmtime.reset_mock()

        # --- テスト実行 ---
        response = client.get("/recordings")

        # --- 検証 ---
        assert response.status_code == 200  # 現在の実装ではエラーでも 200 を返す
        data = json.loads(response.data)
        assert "recordings" in data
        assert data["recordings"] == []
        assert "message" in data
        assert "録画フォルダが見つかりません" in data["message"]
        mock_listdir.assert_called_once_with(recordings_dir)
        mock_isfile.assert_not_called()
        mock_getmtime.assert_not_called()


# --- /download のテストクラス ---


# モック対象: flask.send_from_directory (app.py 内で import されている)
@patch("app.send_from_directory")
class TestDownloadRoute:
    """/download/<filename> ルートのテストクラス"""

    def test_download_success(self, mock_send_from_directory, client, app):
        """GET /download/<filename> : 正常系 (ファイルダウンロード成功)"""
        # --- モック準備 ---
        mock_send_from_directory.reset_mock()
        # send_from_directory が成功したと仮定 (ダミーレスポンスを返す)
        # Flask の send_from_directory は Response オブジェクトを返すため、それを模倣
        mock_response = MagicMock()
        mock_response.status_code = 200
        # 必要であればヘッダーなども模倣できる
        mock_send_from_directory.return_value = mock_response
        recordings_dir = app.config["RECORDINGS_FOLDER"]
        test_filename = "my_recording.mp4"

        # --- テスト実行 ---
        response = client.get(f"/download/{test_filename}")

        # --- 検証 ---
        # send_from_directory が正しく呼ばれたか
        mock_send_from_directory.assert_called_once_with(
            recordings_dir, test_filename, as_attachment=True
        )
        # レスポンスの検証 (モックが返すオブジェクトに基づいて成功を確認)
        assert response.status_code == 200

    def test_download_not_found(self, mock_send_from_directory, client, app):
        """GET /download/<filename> : 異常系 (ファイルが見つからない)"""
        # --- モック準備 ---
        mock_send_from_directory.reset_mock()
        # send_from_directory が FileNotFoundError を発生させる
        # Flask では FileNotFoundError は通常 NotFound (404) に変換される
        # ここでは send_from_directory 自体がエラーを出すのではなく、
        # ルートハンドラ内の FileNotFoundError の except 節をテストする
        mock_send_from_directory.side_effect = FileNotFoundError
        test_filename = "not_found.mp4"

        # --- テスト実行 ---
        response = client.get(f"/download/{test_filename}")

        # --- 検証 ---
        assert response.status_code == 404
        data = json.loads(response.data)
        assert data["status"] == "error"
        assert "ファイルが見つかりません" in data["message"]
        # send_from_directory が呼ばれたかも確認
        recordings_dir = app.config["RECORDINGS_FOLDER"]
        mock_send_from_directory.assert_called_once_with(
            recordings_dir, test_filename, as_attachment=True
        )


# --- /delete のテストクラス ---


# モック対象: os.path.exists, os.path.isfile, os.remove
@patch("app.os.remove")
@patch("app.os.path.isfile")
@patch("app.os.path.exists")
class TestDeleteRoute:
    """/delete/<filename> ルートのテストクラス"""

    def test_delete_success(self, mock_exists, mock_isfile, mock_remove, client, app):
        """DELETE /delete/<filename> : 正常系 (ファイル削除成功)"""
        # --- モック準備 ---
        mock_exists.reset_mock()
        mock_isfile.reset_mock()
        mock_remove.reset_mock()

        mock_exists.return_value = True
        mock_isfile.return_value = True
        recordings_dir = app.config["RECORDINGS_FOLDER"]
        test_filename = "to_be_deleted.mp4"
        expected_path = os.path.join(recordings_dir, test_filename)

        # --- テスト実行 ---
        response = client.delete(f"/delete/{test_filename}")

        # --- 検証 ---
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "success"
        assert f"{test_filename} を削除しました" in data["message"]
        mock_exists.assert_called_once_with(expected_path)
        mock_isfile.assert_called_once_with(expected_path)
        mock_remove.assert_called_once_with(expected_path)

    def test_delete_not_found(self, mock_exists, mock_isfile, mock_remove, client, app):
        """DELETE /delete/<filename> : 異常系 (ファイルが見つからない)"""
        # --- モック準備 ---
        mock_exists.reset_mock()
        mock_isfile.reset_mock()
        mock_remove.reset_mock()

        mock_exists.return_value = False  # ファイルが存在しない
        recordings_dir = app.config["RECORDINGS_FOLDER"]
        test_filename = "not_found.mp4"
        expected_path = os.path.join(recordings_dir, test_filename)

        # --- テスト実行 ---
        response = client.delete(f"/delete/{test_filename}")

        # --- 検証 ---
        assert response.status_code == 404
        data = json.loads(response.data)
        assert data["status"] == "error"
        assert "ファイルが見つからないか" in data["message"]
        mock_exists.assert_called_once_with(expected_path)
        mock_isfile.assert_not_called()  # exists が False なら isfile は呼ばれない
        mock_remove.assert_not_called()  # remove も呼ばれない

    def test_delete_exception(self, mock_exists, mock_isfile, mock_remove, client, app):
        """DELETE /delete/<filename> : 異常系 (削除中にエラー)"""
        # --- モック準備 ---
        mock_exists.reset_mock()
        mock_isfile.reset_mock()
        mock_remove.reset_mock()

        mock_exists.return_value = True
        mock_isfile.return_value = True
        mock_remove.side_effect = OSError("Permission denied")  # 削除時にエラー発生
        recordings_dir = app.config["RECORDINGS_FOLDER"]
        test_filename = "error_file.mp4"
        expected_path = os.path.join(recordings_dir, test_filename)

        # --- テスト実行 ---
        response = client.delete(f"/delete/{test_filename}")

        # --- 検証 ---
        assert response.status_code == 500
        data = json.loads(response.data)
        assert data["status"] == "error"
        assert "ファイルの削除中にエラーが発生しました" in data["message"]
        assert "Permission denied" in data["message"]  # エラー詳細が含まれるか
        mock_exists.assert_called_once_with(expected_path)
        mock_isfile.assert_called_once_with(expected_path)
        mock_remove.assert_called_once_with(expected_path)  # remove は呼ばれる


# --- run_recording_process 関数のテストクラス ---


# --- 修正: クラスレベルの @patch を削除 ---
# @patch('app.Recorder.start')
class TestRunRecordingProcess:
    """run_recording_process 関数のテストクラス"""

    # --- 修正: mock_recorder_start 引数を削除 ---
    def test_run_recording_process_resets_state_on_success(self, app):
        """正常系: recorder.start() 成功時に状態がリセットされる"""
        # --- 前提条件: 録画中の状態 ---
        app_module.recording = True
        mock_recorder = MagicMock()
        mock_recorder.stop_event = threading.Event()
        app_module.recorder_instance = mock_recorder
        app_module.current_status_info["final_output"] = "some_file.mp4"
        # モックのリセット (MagicMock のメソッド呼び出し履歴)
        mock_recorder.start.reset_mock()
        mock_recorder.start.side_effect = None

        # --- テスト実行 ---
        app_module.run_recording_process(mock_recorder)

        # --- 検証 ---
        # --- 修正: mock_recorder.start を検証 ---
        mock_recorder.start.assert_called_once()
        # 状態がリセットされたか
        assert app_module.recording is False
        assert app_module.recorder_instance is None
        assert app_module.current_status_info["final_output"] is None

    # --- 修正: mock_recorder_start 引数を削除 ---
    def test_run_recording_process_resets_state_on_error(self, app):
        """異常系: recorder.start() で例外発生時も状態がリセットされる"""
        # --- 前提条件: 録画中の状態 ---
        app_module.recording = True
        mock_recorder = MagicMock()
        mock_recorder.stop_event = threading.Event()
        app_module.recorder_instance = mock_recorder
        app_module.current_status_info["final_output"] = "another_file.mp4"
        # モックのリセット
        mock_recorder.start.reset_mock()

        # --- モック準備: start で例外を発生 ---
        test_exception = Exception("Recording failed!")
        # --- 修正: mock_recorder.start の side_effect を設定 ---
        mock_recorder.start.side_effect = test_exception

        # --- テスト実行 ---
        app_module.run_recording_process(mock_recorder)

        # --- 検証 ---
        # --- 修正: mock_recorder.start を検証 ---
        mock_recorder.start.assert_called_once()
        # 例外発生時でも状態がリセットされたか
        assert app_module.recording is False
        assert app_module.recorder_instance is None
        assert app_module.current_status_info["final_output"] is None
        # 例外発生時に stop_event がセットされるかも確認
        assert mock_recorder.stop_event.is_set()
