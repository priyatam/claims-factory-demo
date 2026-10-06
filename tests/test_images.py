from types import SimpleNamespace

from claims.images import fetch_image, media_type, public_url

JPEG = b"\xff\xd8\xff" + b"\x00" * 8
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


def resolve(host, port):
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def test_media_type_sniffs_magic_bytes():
    assert media_type(JPEG) == "image/jpeg"
    assert media_type(PNG) == "image/png"
    assert media_type(b"GIF89a") is None
    assert media_type(b"") is None


def test_public_url_rejects_private_and_non_http():
    assert public_url("https://example.com/car.jpg", resolve=resolve)
    assert not public_url("https://example.com/car.jpg", resolve=lambda _h, port: [(2, 1, 6, "", ("10.0.0.5", port))])
    assert not public_url("http://127.0.0.1/a.jpg", resolve=resolve)
    assert not public_url("http://169.254.169.254/latest", resolve=lambda _h, port: [(2, 1, 6, "", ("169.254.169.254", port))])
    assert not public_url("file:///etc/passwd", resolve=resolve)


def test_fetch_returns_bytes_for_a_public_image():
    def get(url, **_kwargs):
        return SimpleNamespace(status_code=200, content=JPEG, url=url)

    loaded = fetch_image("https://example.com/car.jpg", get=get, resolve=resolve)
    assert loaded["media_type"] == "image/jpeg"
    assert loaded["data"].startswith(b"\xff\xd8\xff")


def test_fetch_rejects_oversized_and_non_images():
    def get(url, **_kwargs):
        return SimpleNamespace(status_code=200, content=JPEG, url=url)

    assert fetch_image("https://example.com/car.jpg", get=get, resolve=resolve, limit=4) is None

    def text(url, **_kwargs):
        return SimpleNamespace(status_code=200, content=b"hello", url=url)

    assert fetch_image("https://example.com/a.txt", get=text, resolve=resolve) is None
