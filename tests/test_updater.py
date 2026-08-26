import pytest

from app.updater import _is_allowed_download, _parse_sha256_checksum, is_newer_version


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


def test_sha256_checksum_requires_matching_filename():
    digest = "a" * 64
    assert _parse_sha256_checksum(
        f"{digest} *SashikomiMail-Setup-v1.exe", "SashikomiMail-Setup-v1.exe") == digest
    with pytest.raises(ValueError):
        _parse_sha256_checksum(f"{digest} *other.exe", "SashikomiMail-Setup-v1.exe")
    with pytest.raises(ValueError):
        _parse_sha256_checksum("not-a-checksum", "SashikomiMail-Setup-v1.exe")
