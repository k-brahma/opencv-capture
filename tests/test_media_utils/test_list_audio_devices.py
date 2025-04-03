import io
import sys
from unittest.mock import MagicMock, patch

import pytest

# --- 修正: 新しいモジュールから関数をインポート ---
from media_utils.list_audio_devices import display_audio_devices, query_audio_devices


class TestAudioDeviceQuery:

    def test_successful_query(self):
        """デバイス情報が正常に取得できる場合のテスト"""
        with patch("media_utils.list_audio_devices.sd.query_devices") as mock_query_devices, patch(
            "media_utils.list_audio_devices.sd.query_hostapis"
        ) as mock_query_hostapis:

            # モックデータの準備
            mock_devices = [
                {
                    "name": "Default",
                    "index": 0,
                    "hostapi": 0,
                    "max_input_channels": 2,
                    "max_output_channels": 0,
                    "default_input": True,
                },
                {
                    "name": "Microphone",
                    "index": 1,
                    "hostapi": 0,
                    "max_input_channels": 2,
                    "max_output_channels": 0,
                },
                {
                    "name": "Speakers",
                    "index": 2,
                    "hostapi": 0,
                    "max_input_channels": 0,
                    "max_output_channels": 2,
                    "default_output": True,
                },
                {
                    "name": "Stereo Mix",
                    "index": 3,
                    "hostapi": 0,
                    "max_input_channels": 2,
                    "max_output_channels": 0,
                },
            ]

            mock_hostapis = [
                {
                    "name": "Windows WASAPI",
                    "devices": [0, 1, 2, 3],
                    "default_input_device": 0,
                    "default_output_device": 2,
                }
            ]

            # モックの戻り値を設定
            mock_query_devices.return_value = mock_devices
            mock_query_hostapis.return_value = mock_hostapis

            # 関数を実行
            result = query_audio_devices()

            # アサーション
            assert result["success"] is True
            assert result["devices"] == mock_devices
            assert result["hostapis"] == mock_hostapis
            assert len(result["devices"]) == 4
            assert len(result["hostapis"]) == 1

    def test_exception_handling(self):
        """例外が発生した場合のエラーハンドリングをテスト"""
        with patch("media_utils.list_audio_devices.sd.query_devices") as mock_query_devices:
            # 例外を発生させる
            mock_query_devices.side_effect = RuntimeError("Device query failed")

            # 関数を実行
            result = query_audio_devices()

            # アサーション
            assert result["success"] is False
            assert "Device query failed" in result["error"]

    def test_display_output(self):
        """表示関数の標準出力をテスト"""
        with patch("media_utils.list_audio_devices.sd.query_devices") as mock_query_devices, patch(
            "media_utils.list_audio_devices.sd.query_hostapis"
        ) as mock_query_hostapis, patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:

            # モックデータを実装に合わせて拡張
            mock_devices = [
                {
                    "name": "Microphone",
                    "index": 0,
                    "hostapi": 0,
                    "max_input_channels": 2,
                    "max_output_channels": 0,
                },
                {
                    "name": "Stereo Mix",
                    "index": 1,
                    "hostapi": 0,
                    "max_input_channels": 2,
                    "max_output_channels": 0,
                }
            ]
            mock_hostapis = [{"name": "MME", "index": 0}]
            mock_query_devices.return_value = mock_devices
            mock_query_hostapis.return_value = mock_hostapis

            # 表示関数を実行
            display_audio_devices()

            output = mock_stdout.getvalue()
            # 新しい出力メッセージに対応
            assert "--- すべてのオーディオデバイス ---" in output
            assert "--- 利用可能なホスト API ---" in output
            assert "【マイク候補】:" in output

    def test_display_error_output(self):
        """エラー発生時の表示関数の標準エラー出力をテスト"""
        with patch("media_utils.list_audio_devices.sd.query_devices") as mock_query_devices, patch(
            "sys.stderr", new_callable=io.StringIO
        ) as mock_stderr:

            mock_query_devices.side_effect = RuntimeError("Device query failed")

            display_audio_devices()

            output = mock_stderr.getvalue()
            # 新しいエラーメッセージに対応
            assert "デバイス情報の取得中にエラーが発生しました" in output
            assert "Device query failed" in output

    @patch("media_utils.list_audio_devices.sd.query_devices")
    def test_empty_device_list(self, mock_query_devices):
        """デバイスリストが空の場合のテスト"""
        mock_query_devices.return_value = []

        with patch("media_utils.list_audio_devices.sd.query_hostapis") as mock_query_hostapis:
            mock_query_hostapis.return_value = []

            result = query_audio_devices()

            assert result["success"] is True
            assert result["devices"] == []
            assert result["hostapis"] == []

    @pytest.mark.skipif(
        True, reason="実際のハードウェアとの統合テスト - 環境に依存するためデフォルトでスキップ"
    )
    def test_integration_with_real_hardware(self):
        """実際のハードウェアを使用した統合テスト（オプション）"""
        result = query_audio_devices()

        # 特定の結果を断言するのではなく、正常に実行されることだけを確認
        assert result["success"] is True
        assert "devices" in result
        assert "hostapis" in result
