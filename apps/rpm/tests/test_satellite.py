import io
import urllib.error
from unittest import mock

from django.test import SimpleTestCase

from apps.rpm import satellite


class PushRpmTests(SimpleTestCase):
    @staticmethod
    def _ok_response():
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        return response

    @mock.patch("pathlib.Path.read_bytes", return_value=b"rpm-bytes")
    @mock.patch("pathlib.Path.exists", return_value=True)
    @mock.patch("apps.rpm.satellite.urllib.request.urlopen")
    @mock.patch("apps.rpm.satellite._request")
    @mock.patch("apps.rpm.satellite.post")
    def test_import_payload_excludes_size(
        self,
        mock_post,
        mock_request,
        mock_urlopen,
        _mock_exists,
        _mock_read_bytes,
    ):
        mock_post.return_value = ({"upload_id": "u1"}, "")
        mock_request.return_value = ({}, "")
        mock_urlopen.return_value = self._ok_response()

        err = satellite.push_rpm("/tmp/test.rpm", "https://sat.example", "svc", "token", 7, True)

        self.assertEqual(err, "")
        self.assertEqual(mock_request.call_count, 1)
        import_payload = mock_request.call_args.args[4]
        self.assertNotIn("size", import_payload["uploads"][0])

    @mock.patch("pathlib.Path.read_bytes", return_value=b"rpm-bytes")
    @mock.patch("pathlib.Path.exists", return_value=True)
    @mock.patch("apps.rpm.satellite.urllib.request.urlopen")
    @mock.patch("apps.rpm.satellite._request")
    @mock.patch("apps.rpm.satellite.post")
    def test_checksum_mismatch_retries_with_fresh_raw_upload(
        self,
        mock_post,
        mock_request,
        mock_urlopen,
        _mock_exists,
        _mock_read_bytes,
    ):
        mock_post.side_effect = [({"upload_id": "u1"}, ""), ({"upload_id": "u2"}, "")]
        mock_request.side_effect = [
            (
                None,
                "HTTP 400: {'non_field_errors': ['The sha256 checksum did not match.']}",
            ),
            ({}, ""),
        ]
        mock_urlopen.return_value = self._ok_response()

        err = satellite.push_rpm("/tmp/test.rpm", "https://sat.example", "svc", "token", 7, True)

        self.assertEqual(err, "")
        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(mock_urlopen.call_count, 2)
        self.assertEqual(mock_request.call_count, 2)

    @mock.patch("pathlib.Path.read_bytes", return_value=b"rpm-bytes")
    @mock.patch("pathlib.Path.exists", return_value=True)
    @mock.patch("apps.rpm.satellite.urllib.request.urlopen")
    @mock.patch("apps.rpm.satellite._request")
    @mock.patch("apps.rpm.satellite.post")
    def test_raw_failure_falls_back_to_multipart(
        self,
        mock_post,
        mock_request,
        mock_urlopen,
        _mock_exists,
        _mock_read_bytes,
    ):
        mock_post.return_value = ({"upload_id": "u1"}, "")
        mock_request.return_value = ({}, "")

        raw_http_error = urllib.error.HTTPError(
            url="https://sat.example/upload",
            code=500,
            msg="server error",
            hdrs=None,
            fp=io.BytesIO(b"upload failed"),
        )
        mock_urlopen.side_effect = [raw_http_error, self._ok_response()]

        err = satellite.push_rpm("/tmp/test.rpm", "https://sat.example", "svc", "token", 7, True)

        self.assertEqual(err, "")
        self.assertEqual(mock_urlopen.call_count, 2)
        self.assertEqual(mock_request.call_count, 1)
