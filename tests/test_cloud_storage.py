from urllib.parse import parse_qs, urlparse

import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from scripts import cloud_storage as cs


def _r2():
    return cs.R2Storage("acct123", "bkt", "AKIAFAKEFAKEFAKE", "fakesecretfakesecretfakesecret12345678")


def test_key_layout():
    assert cs.upload_key("u1", "up1") == "uploads/u1/up1"
    assert cs.export_key("u1", "p1", "out.mp4") == "exports/u1/p1/out.mp4"
    assert cs.zip_key("u1", "b1") == "zips/u1/b1.zip"


@pytest.mark.parametrize("bad", ["a/b", "..", "", "a/../b"])
def test_key_parts_validated(bad):
    with pytest.raises(ValueError):
        cs.upload_key(bad, "x")
    with pytest.raises(ValueError):
        cs.export_key("u", "p", bad)
    with pytest.raises(ValueError):
        cs.zip_key("u", bad)


def test_memory_round_trip(tmp_path):
    m = cs.MemoryStorage()
    src = tmp_path / "a.bin"
    src.write_bytes(b"hello")
    m.upload(src, "uploads/u/x", "video/mp4")
    assert m.size("uploads/u/x") == 5
    assert m.size("nope") is None
    dest = tmp_path / "sub" / "b.bin"
    m.download("uploads/u/x", dest)
    assert dest.read_bytes() == b"hello"
    assert m.presign_put("k", "video/mp4", 5).startswith("mem://")
    assert m.presign_get("uploads/u/x", "f.mp4").startswith("mem://")
    m.upload(src, "uploads/u/y", "video/mp4")
    m.upload(src, "exports/u/z", "video/mp4")
    assert m.usage_bytes("uploads/u/") == 10
    assert m.delete_prefix("uploads/u/") == 2
    assert m.size("uploads/u/x") is None
    assert m.size("exports/u/z") == 5
    m.delete("exports/u/z")
    assert m.size("exports/u/z") is None
    with pytest.raises(KeyError):
        m.download("exports/u/z", dest)


def test_presign_put_signs_type_and_length():
    url = _r2().presign_put("uploads/u/x", "video/mp4", 1234)
    p = urlparse(url)
    assert p.hostname == "acct123.r2.cloudflarestorage.com"
    q = parse_qs(p.query)
    signed = q["X-Amz-SignedHeaders"][0].split(";")
    assert "content-length" in signed and "content-type" in signed
    assert q["X-Amz-Expires"] == ["3600"]


def test_presign_get_attachment_filename():
    url = _r2().presign_get("exports/u/p/o.mp4", "my video.mp4")
    q = parse_qs(urlparse(url).query)
    assert q["X-Amz-Expires"] == ["86400"]
    assert q["response-content-disposition"][0] == 'attachment; filename="my video.mp4"; filename*=UTF-8\'\'my%20video.mp4'
    url2 = _r2().presign_get("k", 'é"x.mp4')
    cd = parse_qs(urlparse(url2).query)["response-content-disposition"][0]
    assert "filename*=UTF-8''%C3%A9%22x.mp4" in cd
    assert '"' not in cd.split("filename=")[1].split(";")[0].strip('"')


def test_size_found_and_not_found_and_error():
    r = _r2()
    with Stubber(r._client) as st:
        st.add_response("head_object", {"ContentLength": 42}, {"Bucket": "bkt", "Key": "k"})
        assert r.size("k") == 42
        st.add_client_error("head_object", service_error_code="404", http_status_code=404)
        assert r.size("k") is None
        st.add_client_error("head_object", service_error_code="500", http_status_code=500)
        with pytest.raises(ClientError):
            r.size("k")


def test_delete():
    r = _r2()
    with Stubber(r._client) as st:
        st.add_response("delete_object", {}, {"Bucket": "bkt", "Key": "k"})
        r.delete("k")
        st.assert_no_pending_responses()


def test_delete_prefix_pages_and_batches():
    r = _r2()
    with Stubber(r._client) as st:
        page1 = [{"Key": f"p/{i}", "Size": 1} for i in range(1000)]
        page2 = [{"Key": "p/last", "Size": 1}]
        st.add_response("list_objects_v2", {"Contents": page1, "IsTruncated": True, "NextContinuationToken": "t1"},
                        {"Bucket": "bkt", "Prefix": "p/"})
        st.add_response("delete_objects", {}, {"Bucket": "bkt", "Delete": {"Objects": [{"Key": o["Key"]} for o in page1], "Quiet": True}})
        st.add_response("list_objects_v2", {"Contents": page2, "IsTruncated": False},
                        {"Bucket": "bkt", "Prefix": "p/", "ContinuationToken": "t1"})
        st.add_response("delete_objects", {}, {"Bucket": "bkt", "Delete": {"Objects": [{"Key": "p/last"}], "Quiet": True}})
        assert r.delete_prefix("p/") == 1001
        st.assert_no_pending_responses()


def test_delete_prefix_empty():
    r = _r2()
    with Stubber(r._client) as st:
        st.add_response("list_objects_v2", {"IsTruncated": False}, {"Bucket": "bkt", "Prefix": "p/"})
        assert r.delete_prefix("p/") == 0


def test_usage_bytes_pages():
    r = _r2()
    with Stubber(r._client) as st:
        st.add_response("list_objects_v2", {"Contents": [{"Key": "a", "Size": 5}, {"Key": "b", "Size": 7}],
                                            "IsTruncated": True, "NextContinuationToken": "t"},
                        {"Bucket": "bkt", "Prefix": "u/"})
        st.add_response("list_objects_v2", {"Contents": [{"Key": "c", "Size": 3}], "IsTruncated": False},
                        {"Bucket": "bkt", "Prefix": "u/", "ContinuationToken": "t"})
        assert r.usage_bytes("u/") == 15


def test_upload_and_download_call_client(tmp_path):
    r = _r2()
    calls = []
    r._client.upload_file = lambda *a, **k: calls.append(("up", a, k))
    r._client.download_file = lambda *a, **k: calls.append(("down", a, k))
    r.upload(tmp_path / "f", "k", "video/mp4")
    r.download("k", tmp_path / "d" / "f")
    assert calls[0] == ("up", (str(tmp_path / "f"), "bkt", "k"), {"ExtraArgs": {"ContentType": "video/mp4"}})
    assert calls[1][1] == ("bkt", "k", str(tmp_path / "d" / "f"))
    assert (tmp_path / "d").is_dir()
