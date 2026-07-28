from app.updater import _is_allowed_download, is_newer_version


def test_is_newer_version():
    assert is_newer_version("1.0.0", "v1.1.0")
    assert not is_newer_version("1.1.0", "v1.1.0")
    assert not is_newer_version("1.1.0", "v1.0.9")
    assert not is_newer_version("invalid", "v2.0.0")


def test_download_url_is_restricted_to_github():
    assert _is_allowed_download("https://github.com/mozu93/sashikomimail/releases/a.exe")
    assert _is_allowed_download("https://objects.githubusercontent.com/a.exe")
    assert not _is_allowed_download("http://github.com/a.exe")
    assert not _is_allowed_download("https://example.com/a.exe")
