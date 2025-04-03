import os
from unittest.mock import MagicMock, patch

import cv2
import pytest

from media_utils.check_codecs import check_available_codecs


# テストケース
class TestVideoCodecs:

    def test_fourcc_conversion(self):
        """FourCCコードが正しく変換されるかテスト"""
        fourcc = cv2.VideoWriter_fourcc(*"DIVX")  # type: ignore
        assert isinstance(fourcc, int)
        # FourCCコードは4バイト整数値に変換される
        assert fourcc != 0, "FourCCコードが正しく変換されていません"

    @patch("media_utils.check_codecs.cv2.VideoWriter")
    def test_codec_availability_check_all_available(self, mock_videowriter):
        """すべてのコーデックが利用可能な場合のテスト"""
        # VideoWriterのモックを設定
        mock_instance = MagicMock()
        mock_instance.isOpened.return_value = True
        mock_videowriter.return_value = mock_instance

        test_codecs = ["DIVX", "XVID"]
        result = check_available_codecs(test_codecs)

        assert result["available"] == test_codecs
        assert result["unavailable"] == []
        assert mock_instance.release.call_count == len(test_codecs)

    @patch("media_utils.check_codecs.cv2.VideoWriter")
    def test_codec_availability_check_none_available(self, mock_videowriter):
        """すべてのコーデックが利用できない場合のテスト"""
        # VideoWriterのモックを設定
        mock_instance = MagicMock()
        mock_instance.isOpened.return_value = False
        mock_videowriter.return_value = mock_instance

        test_codecs = ["DIVX", "XVID"]
        result = check_available_codecs(test_codecs)

        assert result["available"] == []
        assert result["unavailable"] == test_codecs
        # isOpened() が False の場合は release() は呼ばれないはず
        assert mock_instance.release.call_count == 0

    @patch("media_utils.check_codecs.cv2.VideoWriter")
    def test_codec_availability_check_mixed(self, mock_videowriter):
        """一部のコーデックのみ利用可能な場合のテスト"""
        # 異なる結果を返すために、side_effect を使用
        mock_instances = []
        for codec in ["DIVX", "XVID", "MJPG"]:
            mock_instance = MagicMock()
            # DIVXとMJPGは利用可能、XVIDは利用不可と仮定
            mock_instance.isOpened.return_value = codec != "XVID"
            mock_instances.append(mock_instance)

        mock_videowriter.side_effect = mock_instances

        test_codecs = ["DIVX", "XVID", "MJPG"]
        result = check_available_codecs(test_codecs)

        assert result["available"] == ["DIVX", "MJPG"]
        assert result["unavailable"] == ["XVID"]
        # 利用可能なコーデックの数だけrelease()が呼ばれるはず
        assert mock_instances[0].release.call_count == 1
        assert mock_instances[1].release.call_count == 0
        assert mock_instances[2].release.call_count == 1

    @patch("media_utils.check_codecs.os.path.exists")
    @patch("media_utils.check_codecs.os.remove")
    def test_file_cleanup(self, mock_remove, mock_exists):
        """一時ファイルが正しく削除されるかテスト"""
        # ファイルが存在すると仮定
        mock_exists.return_value = True

        # VideoWriterのモックを設定（簡略化のため）
        with patch("media_utils.check_codecs.cv2.VideoWriter") as mock_videowriter:
            mock_instance = MagicMock()
            mock_instance.isOpened.return_value = True
            mock_videowriter.return_value = mock_instance

            check_available_codecs(["DIVX"])

        # os.removeが呼ばれたことを確認
        mock_remove.assert_called_once()

    @patch("media_utils.check_codecs.os.path.exists")
    def test_no_file_cleanup_if_not_exists(self, mock_exists):
        """ファイルが存在しない場合は削除処理が呼ばれないことをテスト"""
        # ファイルが存在しないと仮定
        mock_exists.return_value = False

        # VideoWriterのモックを設定（簡略化のため）
        with patch("media_utils.check_codecs.cv2.VideoWriter") as mock_videowriter:
            mock_instance = MagicMock()
            mock_instance.isOpened.return_value = True
            mock_videowriter.return_value = mock_instance

            with patch("media_utils.check_codecs.os.remove") as mock_remove:
                check_available_codecs(["DIVX"])

                # os.removeが呼ばれないことを確認
                mock_remove.assert_not_called()

    @patch("media_utils.check_codecs.os.path.exists")
    @patch("media_utils.check_codecs.os.remove")
    def test_file_removal_exception_handling(self, mock_remove, mock_exists):
        """ファイル削除時の例外処理が正しく機能するかテスト"""
        # ファイルが存在すると仮定
        mock_exists.return_value = True
        # 削除時に例外を発生させる
        mock_remove.side_effect = PermissionError("Access denied")

        # VideoWriterのモックを設定（簡略化のため）
        with patch("media_utils.check_codecs.cv2.VideoWriter") as mock_videowriter:
            mock_instance = MagicMock()
            mock_instance.isOpened.return_value = True
            mock_videowriter.return_value = mock_instance

            # 例外が補足され、処理が続行されることを確認
            result = check_available_codecs(["DIVX"])
            assert result["available"] == ["DIVX"]

    def test_integration_with_real_codec(self):
        """実際のコーデックを使用した統合テスト（環境依存なのでスキップ可能）"""
        # このテストは環境によって結果が異なるため、必要に応じてスキップ
        # pytest.skip("このテストは環境に依存するため、必要に応じてスキップしてください")

        # 単一のコーデックでテスト実行
        result = check_available_codecs(["MJPG"])  # MJPGは多くの環境で利用可能

        # 特定の結果を断言するのではなく、結果の形式が正しいことを確認
        assert "available" in result
        assert "unavailable" in result
        assert isinstance(result["available"], list)
        assert isinstance(result["unavailable"], list)
        assert len(result["available"]) + len(result["unavailable"]) == 1
