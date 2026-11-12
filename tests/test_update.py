"""In-app updates: the check against GitHub (and its 12-hour memory), the download with its host, size and checksum
checks, the hand-over file the launcher polls, the CLI line and the banner in the app. No network: the one function
that opens a connection is replaced, and a test that reaches the real one fails."""
from __future__ import annotations

import ast
import datetime as dt
import hashlib
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import gridsim
from gridsim import update

APP = Path(__file__).resolve().parent.parent / "app"
PAYLOAD = b"MZ" + bytes(range(256)) * 3000             # the "installer": 768,002 bytes, several chunks
URL = "https://github.com/Dierks27/GameSim/releases/download/v2026.11.20/GridSim-Setup.exe"
CDN = "https://release-assets.githubusercontent.com/github-production-release-asset/1/abc?sig=x"
PAGE = "https://github.com/Dierks27/GameSim/releases/tag/v2026.11.20"


class Resp:
    """What urlopen hands back, as far as gridsim.update uses it."""

    def __init__(self, body=b"", url=update.API_URL, status=200, headers=None, drop_after=None):
        self._io, self._url, self.status, self.headers = io.BytesIO(body), url, status, dict(headers or {})
        self._drop_after, self._reads = drop_after, 0

    def read(self, n=-1):
        self._reads += 1
        if self._drop_after is not None and self._reads > self._drop_after:
            raise ConnectionResetError("connection reset by peer")
        return self._io.read(n)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def release(tag="v2026.11.20", name="GridSim-Setup.exe", state="uploaded", digest="auto", size=None, **extra) -> dict:
    asset = {"name": name, "state": state, "size": len(PAYLOAD) if size is None else size, "browser_download_url": URL,
             "digest": "sha256:" + hashlib.sha256(PAYLOAD).hexdigest() if digest == "auto" else digest}
    return {"tag_name": tag, "draft": False, "prerelease": False, "html_url": PAGE,
            "assets": [{"name": "notes.txt", "state": "uploaded", "size": 3, "browser_download_url": URL + ".txt"},
                       asset], **extra}


def api(rel, etag=None) -> Resp:
    body = rel if isinstance(rel, bytes) else json.dumps(rel).encode()
    return Resp(body, headers={"ETag": etag} if etag else {})


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(update.API_URL, code, "error", {}, io.BytesIO(b""))


@pytest.fixture()
def net(tmp_path, monkeypatch):
    """A fresh state folder, GridSim 2026.11.12 running, and a scripted network: net.answers is what the next
    requests get (a Resp, or an exception to raise); net.calls is what was asked."""
    monkeypatch.setenv("GRIDSIM_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("GRIDSIM_DATA", str(tmp_path / "GridSim Data"))
    monkeypatch.delenv("GRIDSIM_GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(gridsim, "__version__", "2026.11.12")
    monkeypatch.setattr(update, "_JOB", {"state": "idle"})
    monkeypatch.setattr(update, "_LAST", {})
    monkeypatch.setattr(update, "HANDOVER_DELAY_S", 0.0)

    class Net:
        answers: list = []
        calls: list = []
        now = dt.datetime(2026, 11, 12, 9, 0, tzinfo=dt.timezone.utc)

        def later(self, **delta):
            self.now += dt.timedelta(**delta)

    n = Net()
    n.answers, n.calls = [], []

    def fake_urlopen(req, timeout, allowed):
        n.calls.append((req, timeout, allowed))
        assert n.answers, f"unexpected network request to {req.full_url}"
        answer = n.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(update, "_urlopen", fake_urlopen)
    monkeypatch.setattr(update, "_now", lambda: n.now)
    return n


def info(**over) -> dict:
    base = {"version": "2026.11.20", "url": URL, "size": len(PAYLOAD), "page": PAGE,
            "digest": "sha256:" + hashlib.sha256(PAYLOAD).hexdigest()}
    return {**base, **over}


def leftovers() -> list[str]:
    return sorted(f.name for f in update.update_dir().iterdir() if f.name.startswith("GridSim-Setup"))


# ---- versions -------------------------------------------------------------------------------------------

def test_versions_are_compared_as_numbers_and_a_malformed_one_is_never_newer():
    newer = update.is_newer
    assert newer("v2026.11.13", "2026.11.12") and newer("2026.12.1", "2026.11.30") and newer("v2027.1.1", "2026.11.12")
    assert newer("v2026.11.12.1", "2026.11.12")
    assert not newer("v2026.11.12", "2026.11.12") and not newer("v2026.11.9", "2026.11.12")     # 9 < 12, not "9" > "1"
    assert not newer("v2026.11.12.0", "2026.11.12") and not newer("v2025.12.31", "2026.1.1")
    for bad in ("v2026.11.13-beta", "latest", "", "v", "2026.11.x", "v2026..13", "2026.11.13 ; rm", None, 20261113):
        assert not newer(bad, "2026.11.12"), bad
    assert not newer("v2026.11.13", "development")                 # the running version cannot be read: no claim
    assert update.version_tuple("v2026.11.12") == (2026, 11, 12)


# ---- the check ------------------------------------------------------------------------------------------

def test_a_newer_release_with_a_finished_installer_is_offered(net):
    net.answers = [api(release())]
    got = update.check()
    assert got == {"version": "2026.11.20", "url": URL, "size": len(PAYLOAD), "page": PAGE,
                   "digest": "sha256:" + hashlib.sha256(PAYLOAD).hexdigest()}
    req, timeout, allowed = net.calls[0]
    assert req.full_url == "https://api.github.com/repos/Dierks27/GameSim/releases/latest" and 0 < timeout <= 10
    assert req.get_header("User-agent") == "GridSim-Updater/2026.11.12"       # the program, nothing about the PC
    assert req.get_header("Authorization") is None and "Authorization" not in req.unredirected_hdrs
    assert allowed("https://api.github.com/repositories/1/releases/latest") and not allowed(URL)
    assert update.cached() == got and update.status()["available"] == "2026.11.20" and update.status()["ok"]
    assert update.status()["state"] == "available" and update.status()["listed"] == "v2026.11.20"


def test_the_same_or_an_older_release_is_not_an_update(net):
    for tag in ("v2026.11.12", "v2026.11.11", "v2025.12.30"):
        net.answers = [api(release(tag=tag))]
        assert update.check(force=True) is None
        assert update.status()["state"] == "newest" and update.status()["available"] is None


def test_a_malformed_tag_or_a_malformed_answer_is_not_an_update(net):
    for tag in ("nightly", "v2026.11.20-rc1", "", None):
        net.answers = [api(release(tag=tag))]
        assert update.check(force=True) is None and update.status()["ok"]
    for body in (b"<html>rate limited</html>", b"[1, 2]", b"", b"\xff\xfe",
                 b'{"tag_name": "v2026.11.20", "assets": 7}'):
        net.answers = [api(body)]
        assert update.check(force=True) is None
    net.answers = [api(release(size="big"))]
    assert update.check(force=True) is None
    net.answers = [api(release(extra_field={"nested": [1, 2, 3]}))]            # unknown fields do not matter
    assert update.check(force=True)["version"] == "2026.11.20"


def test_drafts_and_prereleases_are_ignored(net):
    for flag in ("draft", "prerelease"):
        net.answers = [api(release(**{flag: True}))]
        assert update.check(force=True) is None


def test_a_release_without_its_installer_or_with_one_still_uploading_is_not_offered_and_looked_at_again_soon(net):
    for rel in (release(name="GridSim-Setup-2026.11.20.exe"), release(name="gridsim-setup.exe"),
                release(state="open"), release(size=0), {**release(), "assets": []}):
        net.answers = [api(rel, etag='"half"')]
        assert update.check(force=True) is None and update.status()["state"] == "not_ready"
    net.later(minutes=59)
    assert update.check() is None and len(net.calls) == 5              # not yet
    net.later(minutes=2)
    net.answers = [api(release())]
    assert update.check()["version"] == "2026.11.20"                   # an hour later the installer is there
    assert net.calls[-1][0].get_header("If-none-match") is None        # (a half-made answer is not "unchanged")


def test_the_installer_must_come_from_github_over_https(net):
    for bad in ("http://github.com/Dierks27/GameSim/releases/download/v2026.11.20/GridSim-Setup.exe",
                "https://downloads.example.com/GridSim-Setup.exe", "https://github.com.example.com/x.exe", None):
        rel = release()
        rel["assets"][1]["browser_download_url"] = bad
        net.answers = [api(rel)]
        assert update.check(force=True) is None


def test_no_internet_an_http_error_or_a_timeout_say_nothing_and_keep_what_was_known(net):
    net.answers = [api(release())]
    known = update.check()
    for problem in (urllib.error.URLError("no route to host"), http_error(403), http_error(500),
                    TimeoutError("timed out"), OSError("certificate verify failed"),
                    update.UpdateError("redirect refused"),
                    Resp(b"{}", status=202), Resp(json.dumps(release()).encode(), url="https://example.com/x")):
        net.answers = [problem]
        assert update.check(force=True) == known                       # no exception, and the earlier answer stands
        assert not update.status()["ok"] and update.status()["error"] and update.status()["state"] == "available"
    net.answers = []
    assert update.cached() == known


def test_with_nothing_known_a_failed_check_is_simply_no_update(net):
    net.answers = [TimeoutError("timed out")]
    assert update.check() is None and update.cached() is None
    assert update.status() == {"state": "unknown", "available": None, "listed": None, "current": "2026.11.12",
                               "ok": False, "error": "TimeoutError: timed out",
                               "checked_utc": "2026-11-12T09:00:00+00:00"}


def test_no_release_published_yet_is_not_an_error(net):
    net.answers = [api(release(tag="v2026.11.12"))]
    assert update.check() is None and update.status()["listed"] == "v2026.11.12"
    net.answers = [http_error(404)]
    assert update.check(force=True) is None and update.status()["ok"] and update.status()["listed"] is None
    assert update.status()["state"] == "none_published"


def test_github_is_asked_at_most_every_12_hours(net):
    net.answers = [api(release())]
    first = update.check()
    for _ in range(3):
        assert update.check() == first and update.cached() == first
    net.later(hours=11, minutes=59)
    assert update.check() == first and len(net.calls) == 1             # still the remembered answer
    net.later(minutes=2)
    net.answers = [api(release(tag="v2026.11.21"))]
    assert update.check()["version"] == "2026.11.21" and len(net.calls) == 2
    net.answers = [api(release(tag="v2026.11.22"))]
    assert update.check(force=True)["version"] == "2026.11.22" and len(net.calls) == 3      # asked for by hand
    assert update.check()["version"] == "2026.11.22" and len(net.calls) == 3


def test_a_failed_check_is_tried_again_after_an_hour_not_on_every_page(net):
    net.answers = [urllib.error.URLError("offline")]
    assert update.check() is None
    for _ in range(5):
        assert update.check() is None
    assert len(net.calls) == 1
    net.later(minutes=61)
    net.answers = [api(release())]
    assert update.check()["version"] == "2026.11.20" and len(net.calls) == 2


def test_an_answer_that_cannot_be_written_down_is_still_remembered_while_gridsim_runs(net, monkeypatch):
    def full_disk(path, obj):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(update, "_write", full_disk)
    net.answers = [api(release())]
    first = update.check()
    assert first["version"] == "2026.11.20" and not (update.update_dir() / "check.json").exists()
    assert update.check() == first and update.cached() == first and update.check_soon() is False
    assert len(net.calls) == 1                                         # not asked again on every page


def test_a_clock_that_was_set_back_does_not_stop_the_checks(net):
    net.answers = [api(release())]
    update.check()
    net.later(days=-3)
    net.answers = [api(release())]
    assert update.check() and len(net.calls) == 2


def test_an_unchanged_answer_is_recognised_by_its_etag(net):
    net.answers = [api(release(), etag='W/"abc123"')]
    first = update.check()
    assert net.calls[0][0].get_header("If-none-match") is None
    net.later(hours=13)
    net.answers = [http_error(304)]
    assert update.check() == first and update.status()["ok"]
    assert net.calls[1][0].get_header("If-none-match") == 'W/"abc123"'
    net.later(hours=5)
    assert update.check() == first and len(net.calls) == 2             # the 304 counts as a check: 12 hours again


def test_once_the_update_is_installed_the_remembered_release_is_no_longer_offered(net, monkeypatch):
    net.answers = [api(release(), etag='"e1"')]
    assert update.check()["version"] == "2026.11.20"
    old = update.update_dir() / "GridSim-Setup-2026.11.20.exe"
    old.write_bytes(PAYLOAD)
    monkeypatch.setattr(gridsim, "__version__", "2026.11.20")          # ... installed and started again
    assert update.cached() is None and update.check() is None and len(net.calls) == 1
    net.later(hours=13)
    net.answers = [api(release(tag="v2026.11.20"))]
    assert update.check() is None
    assert net.calls[1][0].get_header("If-none-match") is None         # a new version asks afresh
    assert net.calls[1][0].get_header("User-agent") == "GridSim-Updater/2026.11.20"
    assert not old.exists()                                            # and the used installer is tidied away


def test_the_ci_token_is_sent_to_the_api_only_and_never_follows_a_redirect(net, monkeypatch):
    monkeypatch.setenv("GRIDSIM_GITHUB_TOKEN", "ghs_test")
    net.answers = [api(release())]
    update.check()
    req = net.calls[0][0]
    assert req.unredirected_hdrs == {"Authorization": "Bearer ghs_test"} and "Authorization" not in req.headers
    net.answers = [Resp(PAYLOAD, url=CDN)]
    update.download(info())
    assert "Authorization" not in net.calls[1][0].unredirected_hdrs and "Authorization" not in net.calls[1][0].headers


# ---- redirects ------------------------------------------------------------------------------------------

def test_only_https_github_addresses_are_accepted_for_the_download():
    ok = update._download_host_ok
    for good in (URL, CDN, "https://objects.githubusercontent.com/a/b", "https://GitHub.com/x",
                 "https://github.com:443/x"):
        assert ok(good), good
    for bad in ("http://github.com/x", "http://objects.githubusercontent.com/a", "https://github.com.example.com/x",
                "https://examplegithubusercontent.com/x", "https://githubusercontent.com.example.org/x",
                "https://user:pw@github.com/x", "https://github.com@example.com/x", "https://github.com:8443/x",
                "https://api.github.com/x", "ftp://github.com/x", "file:///C:/Windows/x.exe", "//github.com/x", "",
                None, "https://[::1/x"):
        assert not ok(bad), bad
    assert update._api_host_ok("https://api.github.com/repos/x") and not update._api_host_ok("https://github.com/x")


def test_a_redirect_to_another_host_is_refused_before_it_is_followed():
    handler = update._SafeRedirects(update._download_host_ok)
    req = urllib.request.Request(URL, headers={"User-Agent": "x"})
    followed = handler.redirect_request(req, io.BytesIO(), 302, "Found", {}, CDN)
    assert followed.full_url == CDN
    for bad in ("https://downloads.example.com/GridSim-Setup.exe", "http://objects.githubusercontent.com/x"):
        with pytest.raises(update.UpdateError, match="not GitHub's"):
            handler.redirect_request(req, io.BytesIO(), 302, "Found", {}, bad)
    opener = urllib.request.build_opener(handler)                     # ours replaces urllib's follow-anything handler
    redirectors = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
    assert redirectors == [handler]


def test_a_download_that_ends_on_another_host_is_refused(net):
    net.answers = [Resp(PAYLOAD, url="https://downloads.example.com/GridSim-Setup.exe")]
    with pytest.raises(update.UpdateError, match="not GitHub's"):
        update.download(info())
    assert leftovers() == []
    with pytest.raises(update.UpdateError, match="incomplete"):       # an address that is not GitHub's is not asked
        update.download(info(url="https://downloads.example.com/GridSim-Setup.exe"))
    assert len(net.calls) == 1


# ---- the download ---------------------------------------------------------------------------------------

def test_a_download_is_kept_when_its_size_and_checksum_are_what_the_release_lists(net):
    net.answers = [Resp(PAYLOAD, url=CDN, headers={"Content-Length": str(len(PAYLOAD))})]
    seen = []
    path = update.download(info(), progress=lambda done, total: seen.append((done, total)))
    assert path == update.update_dir() / "GridSim-Setup-2026.11.20.exe" and path.read_bytes() == PAYLOAD
    assert leftovers() == ["GridSim-Setup-2026.11.20.exe"]             # no .partial left
    assert [d for d, _ in seen] == sorted({d for d, _ in seen}) and len(seen) > 1
    assert seen[-1] == (len(PAYLOAD), len(PAYLOAD)) and {t for _, t in seen} == {len(PAYLOAD)}
    req, timeout, allowed = net.calls[0]
    assert req.full_url == URL and timeout > 0 and allowed(CDN) and not allowed("https://example.com/x")
    assert req.get_header("User-agent") == "GridSim-Updater/2026.11.12"


def test_without_a_published_checksum_the_size_decides(net):
    net.answers = [Resp(PAYLOAD, url=CDN)]
    assert update.download(info(digest=None)).read_bytes() == PAYLOAD
    net.answers = [Resp(PAYLOAD[:-1], url=CDN)]
    with pytest.raises(update.UpdateError, match="before it was complete"):
        update.download(info(digest=None, version="2026.11.21"))
    assert leftovers() == []


@pytest.mark.parametrize("answer, match", [
    (Resp(PAYLOAD[:-100], url=CDN), "before it was complete"),
    (Resp(PAYLOAD + b"extra", url=CDN), "not the size"),
    (Resp(PAYLOAD, url=CDN, headers={"Content-Length": "12"}), "not the size"),
    (Resp(PAYLOAD[:-2] + b"!!", url=CDN), "checksum"),
    (Resp(b"", url=CDN), "before it was complete"),
    (Resp(b"Not Found", url=CDN, status=404), "answered 404"),
    (http_error(404), "answered 404"),
    (urllib.error.URLError("offline"), "could not be reached"),
    (TimeoutError("timed out"), "could not be reached"),
    (Resp(PAYLOAD, url=CDN, drop_after=2), "connection dropped"),
], ids=["short", "long", "announced-size", "altered", "empty", "status", "http-error", "offline", "timeout", "dropped"])
def test_a_download_that_is_not_exactly_the_published_file_is_deleted_with_a_plain_reason(net, answer, match):
    net.answers = [answer]
    with pytest.raises(update.UpdateError, match=match) as err:
        update.download(info())
    assert leftovers() == [] and "Traceback" not in str(err.value) and str(err.value).endswith((".", ")"))


def test_a_leftover_partial_file_and_older_installers_are_replaced(net):
    d = update.update_dir()
    (d / "GridSim-Setup-2026.11.20.exe.partial").write_bytes(b"half of an earlier attempt")
    (d / "GridSim-Setup-2026.11.15.exe").write_bytes(b"an earlier update")
    (d / "install.log").write_text("kept")
    net.answers = [Resp(PAYLOAD, url=CDN)]
    assert update.download(info()).read_bytes() == PAYLOAD
    assert leftovers() == ["GridSim-Setup-2026.11.20.exe"] and (d / "install.log").exists()


def test_a_download_that_was_already_checked_is_not_fetched_again_but_an_altered_file_is(net):
    net.answers = [Resp(PAYLOAD, url=CDN)]
    path = update.download(info())
    seen = []
    assert update.download(info(), progress=lambda *a: seen.append(a)) == path and len(net.calls) == 1
    assert seen == [(len(PAYLOAD), len(PAYLOAD))]
    path.write_bytes(PAYLOAD[:-2] + b"!!")                              # same size, other content
    net.answers = [Resp(PAYLOAD, url=CDN)]
    assert update.download(info()).read_bytes() == PAYLOAD and len(net.calls) == 2


def test_a_download_is_not_started_when_the_disk_is_too_full_for_it(net, monkeypatch):
    import collections
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(update.shutil, "disk_usage", lambda path: usage(10**12, 10**12 - 10**6, 10**6))
    with pytest.raises(update.UpdateError, match="not enough free space"):
        update.download(info())
    assert net.calls == [] and leftovers() == []

    def unknown(path):
        raise OSError("this drive does not say how full it is")

    monkeypatch.setattr(update.shutil, "disk_usage", unknown)          # not knowing is no reason to refuse
    net.answers = [Resp(PAYLOAD, url=CDN)]
    assert update.download(info()).read_bytes() == PAYLOAD


def test_a_file_that_windows_holds_for_a_moment_is_put_in_place_on_a_later_try(net, monkeypatch):
    real, tries = update.os.replace, []

    def in_use_twice(src, dst):
        tries.append(Path(dst).name)
        if len(tries) <= 2:
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        real(src, dst)

    monkeypatch.setattr(update.os, "replace", in_use_twice)
    monkeypatch.setattr(update.time, "sleep", lambda s: None)
    net.answers = [Resp(PAYLOAD, url=CDN)]
    assert update.download(info()).read_bytes() == PAYLOAD and len(tries) == 3

    def always_in_use(src, dst):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(update.os, "replace", always_in_use)
    net.answers = [Resp(PAYLOAD, url=CDN)]
    with pytest.raises(update.UpdateError, match=r"could not save the update \(Access is denied\)"):
        update.download(info(version="2026.11.21"))
    assert leftovers() == []                                           # no installer and no .partial left behind
    with pytest.raises(update.UpdateError, match="could not save the request"):
        update.request_install(installer(), "2026.11.20")
    assert update.pending() is None
    assert [f.name for f in update.update_dir().iterdir() if f.name.endswith(".tmp")] == []


def test_a_page_that_is_redrawn_during_the_download_leaves_no_half_file(net):
    class Redrawn(BaseException):                                      # how Streamlit stops a script run
        pass

    def progress(done, total):
        raise Redrawn()

    net.answers = [Resp(PAYLOAD, url=CDN)]
    with pytest.raises(Redrawn):
        update.download(info(), progress=progress)
    assert leftovers() == []


def test_update_information_that_is_incomplete_is_refused_before_anything_is_fetched(net):
    for bad in ({}, None, info(version="../../evil"), info(version="latest"), info(size=0), info(size=-5),
                info(size="768002"), info(size=True), info(url="http://github.com/x.exe"), info(url=None)):
        with pytest.raises(update.UpdateError, match="incomplete"):
            update.download(bad)
    assert net.calls == [] and leftovers() == []


# ---- the hand-over file ---------------------------------------------------------------------------------

def installer(version="2026.11.20") -> Path:
    p = update.update_dir() / f"GridSim-Setup-{version}.exe"
    p.write_bytes(PAYLOAD)
    return p


def test_an_install_request_is_seen_by_the_launcher_until_it_is_cleared(net):
    assert update.pending() is None
    p = installer()
    update.request_install(p, "v2026.11.20")
    rec = json.loads((update.update_dir() / "pending.json").read_text())
    assert rec == {"installer": str(p), "version": "2026.11.20", "size": len(PAYLOAD),
                   "requested_utc": "2026-11-12T09:00:00+00:00"}
    for _ in range(3):                                                 # polled every couple of seconds
        assert update.pending() == {"installer": str(p), "version": "2026.11.20"}
    update.clear_pending()
    assert update.pending() is None and not (update.update_dir() / "pending.json").exists() and p.exists()
    update.clear_pending()                                             # nothing to clear: no error


def test_a_request_whose_installer_is_gone_or_changed_is_dropped(net):
    p = installer()
    update.request_install(p, "2026.11.20")
    p.write_bytes(PAYLOAD + b"x")
    assert update.pending() is None and not (update.update_dir() / "pending.json").exists()
    update.request_install(p, "2026.11.20")
    p.unlink()
    assert update.pending() is None and not (update.update_dir() / "pending.json").exists()


def test_a_request_for_the_version_that_now_runs_is_cleared_with_its_installer(net, monkeypatch):
    p = installer()
    update.request_install(p, "2026.11.20")
    monkeypatch.setattr(gridsim, "__version__", "2026.11.20")          # the installer has done its work
    assert update.pending() is None
    assert not (update.update_dir() / "pending.json").exists() and not p.exists()
    elsewhere = update.update_dir().parent / "GridSim-Setup.exe"       # a file that is not one of our downloads stays
    elsewhere.write_bytes(PAYLOAD)
    update.request_install(elsewhere, "2026.11.19")
    assert update.pending() is None and elsewhere.exists()


def test_a_request_that_was_not_picked_up_within_15_minutes_is_dropped(net):
    p = installer()
    update.request_install(p, "2026.11.20")
    net.later(minutes=14)
    assert update.pending() is not None
    net.later(minutes=2)
    assert update.pending() is None and not (update.update_dir() / "pending.json").exists() and p.exists()


def test_a_damaged_request_file_is_no_request(net):
    f = update.update_dir() / "pending.json"
    for text in ("", "not json", "[]", '{"installer": 5, "version": "2026.11.20"}', '{"version": "2026.11.20"}',
                 '{"installer": "", "version": "2026.11.20", "size": 1, "requested_utc": "2026-11-12T09:00:00+00:00"}'):
        f.write_text(text)
        assert update.pending() is None


def test_an_install_cannot_be_requested_for_a_missing_file_or_a_version_that_is_not_one(net):
    with pytest.raises(update.UpdateError, match="no longer on this PC"):
        update.request_install(update.update_dir() / "nothing.exe", "2026.11.20")
    with pytest.raises(update.UpdateError, match="version"):
        update.request_install(installer(), "the newest")
    assert update.pending() is None


def test_taking_a_request_away_never_raises_even_when_the_state_folder_cannot_be_used(net, monkeypatch):
    def no_folder():
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(update, "update_dir", no_folder)
    update.clear_pending()
    assert update.pending() is None and update.cached() is None and update.check_soon() is False
    assert update.check() is None and net.calls == [] and update.status()["state"] == "unknown"


# ---- background helpers for the app ---------------------------------------------------------------------

def test_the_app_checks_in_the_background_only_when_a_check_is_due(net):
    net.answers = [api(release())]
    assert update.check_soon() is True
    update._CHECK_THREAD.join(10)
    assert not update._CHECK_THREAD.is_alive() and update.cached()["version"] == "2026.11.20"
    assert update.check_soon() is False and len(net.calls) == 1        # remembered: no thread, no request


def test_update_now_downloads_in_the_background_and_then_asks_the_launcher(net):
    assert update.job()["state"] == "idle"
    net.answers = [Resp(PAYLOAD, url=CDN)]
    assert update.start_download(info()) is True
    update._JOB_THREAD.join(10)
    job = update.job()
    assert not update._JOB_THREAD.is_alive() and job["state"] == "ready" and job["version"] == "2026.11.20"
    assert (job["done"], job["total"]) == (len(PAYLOAD), len(PAYLOAD)) and job["age_s"] >= 0
    assert update.pending() == {"installer": str(update.update_dir() / "GridSim-Setup-2026.11.20.exe"),
                                "version": "2026.11.20"}


def test_a_download_that_fails_in_the_background_leaves_a_plain_sentence_and_can_be_tried_again(net):
    net.answers = [Resp(PAYLOAD[:-5], url=CDN), urllib.error.URLError("offline")]
    update.start_download(info())
    update._JOB_THREAD.join(10)
    job = update.job()
    assert job["state"] == "failed" and "before it was complete" in job["error"] and job["page"] == PAGE
    assert update.pending() is None and leftovers() == [] and len(net.calls) == 2
    net.answers = [Resp(PAYLOAD, url=CDN)]
    assert update.start_download(info()) is True
    update._JOB_THREAD.join(10)
    assert update.job()["state"] == "ready" and update.pending() is not None and len(net.calls) == 3


def test_a_release_that_was_replaced_since_the_last_check_is_looked_up_again_after_the_failed_download(net):
    net.answers = [api(release())]
    stale = update.check()                                             # remembered for 12 hours ...
    fixed = PAYLOAD + b"a corrected installer under the same tag"
    rel = release(size=len(fixed), digest="sha256:" + hashlib.sha256(fixed).hexdigest())
    net.answers = [Resp(fixed, url=CDN, headers={"Content-Length": str(len(fixed))}), api(rel)]
    update.start_download(stale)                                       # ... but GitHub now hands out another file
    update._JOB_THREAD.join(10)
    assert update.job()["state"] == "failed" and "not the size" in update.job()["error"] and leftovers() == []
    assert net.calls[-1][0].full_url == update.API_URL                 # so GitHub was asked what it lists now
    fresh = update.cached()
    assert fresh["size"] == len(fixed) and fresh != stale
    net.answers = [Resp(fixed, url=CDN)]
    update.start_download(fresh)                                       # the next "Update now" gets the right file
    update._JOB_THREAD.join(10)
    assert update.job()["state"] == "ready" and Path(update.pending()["installer"]).read_bytes() == fixed


def test_a_second_click_while_downloading_does_not_start_a_second_download(net, monkeypatch):
    monkeypatch.setattr(update, "_JOB", {"state": "downloading", "version": "2026.11.20", "done": 1, "total": 2})
    assert update.start_download(info()) is False and net.calls == []


# ---- the command line -----------------------------------------------------------------------------------

def test_the_update_command_says_what_it_found_and_always_exits_0(net, capsys):
    from gridsim.cli import build_parser
    args = build_parser().parse_args(["update", "--check"])
    net.answers = [api(release())]
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "GridSim 2026.11.20 is available (this is 2026.11.12)." in out and PAGE in out      # (a source folder)
    net.answers = [api(release(tag="v2026.11.12"))]
    assert args.func(args) == 0 and "GridSim 2026.11.12 is the newest version." in capsys.readouterr().out
    net.answers = [urllib.error.URLError("offline")]
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "Could not check for updates" in out and "Traceback" not in out
    net.answers = [http_error(404)]
    assert args.func(args) == 0 and "GitHub lists no GridSim release yet." in capsys.readouterr().out
    net.answers = [api(release(state="open"))]
    assert args.func(args) == 0
    assert "(v2026.11.20) has been released, but its installer is not on GitHub yet" in capsys.readouterr().out
    assert len(net.calls) == 5                                         # the command asks GitHub every time


def test_python_m_gridsim_update_check_ends_with_exit_code_0_with_or_without_internet(net, capsys, monkeypatch):
    from gridsim.cli import main
    net.answers = [api(release())]
    assert main(["update", "--check"]) == 0 and "GridSim 2026.11.20 is available" in capsys.readouterr().out
    net.answers = [TimeoutError("timed out")]
    assert main(["update", "--check"]) == 0
    out = capsys.readouterr().out
    assert "GridSim 2026.11.20 is available" in out and "Traceback" not in out     # what was known still stands
    net.answers = [TimeoutError("timed out")]
    update.update_dir().joinpath("check.json").unlink()                # a first run without internet
    monkeypatch.setattr(update, "_LAST", {})
    assert main(["update"]) == 0 and "Could not check for updates" in capsys.readouterr().out


def test_the_installed_program_is_told_to_update_from_inside_the_app(net, capsys, monkeypatch):
    from gridsim import installed
    from gridsim.cli import build_parser
    monkeypatch.setattr(installed, "is_installed", lambda: True)
    args = build_parser().parse_args(["update"])
    net.answers = [api(release())]
    assert args.func(args) == 0 and '"Update now"' in capsys.readouterr().out


# ---- the banner in the app ------------------------------------------------------------------------------

def app_function(name: str) -> str:
    """One function of app/streamlit_app.py as a script of its own (the whole app needs data and a slate)."""
    src = (APP / "streamlit_app.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == name)
    return f"import streamlit as st\n\n{ast.get_source_segment(src, fn)}\n\n{name}()\n"


@pytest.fixture()
def banner(net, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from gridsim import installed
    monkeypatch.setattr(installed, "is_installed", lambda: True)
    monkeypatch.setattr(update, "check_soon", lambda: False)           # no thread in these tests
    started = []
    monkeypatch.setattr(update, "start_download", lambda i: started.append(i) or True)

    def run():
        at = AppTest.from_string(app_function("update_banner"), default_timeout=30)
        at.run()
        assert not at.exception
        return at

    run.started = started
    return run


def texts(at) -> str:
    return " | ".join([m.value for m in at.markdown] + [w.value for w in at.warning] + [s.value for s in at.success])


def test_a_source_folder_shows_no_update_banner(banner, net, monkeypatch):
    from gridsim import installed
    net.answers = [api(release())]
    update.check()
    monkeypatch.setattr(installed, "is_installed", lambda: False)
    at = banner()
    assert len(at.button) == 0 and texts(at) == ""


def test_the_installed_program_shows_nothing_while_there_is_no_update(banner):
    at = banner()
    assert len(at.button) == 0 and texts(at) == ""


def test_the_banner_offers_the_update_in_one_line_and_update_now_starts_the_download(banner, net):
    net.answers = [api(release())]
    update.check()
    at = banner()
    assert "**GridSim 2026.11.20 is available.** You have 2026.11.12." in texts(at)
    assert [b.label for b in at.button] == ["Update now"]
    at.button[0].click().run()
    assert not at.exception and [i["version"] for i in banner.started] == ["2026.11.20"]
    assert net.calls and len(net.calls) == 1                           # drawing the page never asked GitHub


def test_the_banner_shows_the_progress_and_says_what_happens_next(banner, monkeypatch):
    monkeypatch.setattr(update, "_JOB", {"state": "downloading", "version": "2026.11.20", "done": 50_000_000,
                                         "total": 170_000_000, "page": PAGE, "error": ""})
    at = banner()
    bar = at.get("progress")
    assert len(bar) == 1 and bar[0].proto.value == 29 and len(at.button) == 0
    assert "Downloading GridSim 2026.11.20: 50 of 170 MB." in bar[0].proto.text
    assert "closes, updates itself and opens again in about a minute" in bar[0].proto.text


def test_when_the_download_is_done_the_banner_says_gridsim_closes_and_comes_back(banner, monkeypatch):
    import time
    monkeypatch.setattr(update, "_JOB", {"state": "ready", "version": "2026.11.20", "page": PAGE,
                                         "since": time.monotonic()})
    at = banner()
    assert "GridSim closes now, updates itself and opens again in about a minute" in texts(at)
    assert len(at.button) == 0 and len(at.warning) == 0
    # ... and if nothing closed it after a minute, it says what to do instead of waiting forever
    monkeypatch.setattr(update, "_JOB", {"state": "ready", "version": "2026.11.20", "page": PAGE, "asked": True,
                                         "since": time.monotonic() - 90})
    at = banner()
    assert "did not close by itself" in texts(at) and f"[the GridSim download page]({PAGE})" in texts(at)


def test_a_failed_update_is_a_plain_sentence_with_the_download_page_and_the_button_stays(banner, net, monkeypatch):
    net.answers = [api(release())]
    update.check()
    monkeypatch.setattr(update, "_JOB", {"state": "failed", "version": "2026.11.20", "page": PAGE,
                                         "error": "The download stopped before it was complete (3 of 170 MB)."})
    at = banner()
    assert len(at.warning) == 1 and [b.label for b in at.button] == ["Update now"]
    said = at.warning[0].value
    assert "The download stopped before it was complete (3 of 170 MB). Nothing was changed." in said
    assert f"[the GridSim download page]({PAGE})" in said and "Traceback" not in said
    net.answers = [http_error(404)]                                    # the release was withdrawn meanwhile:
    update.check(force=True)
    at = banner()                                                      # no button, so no "press Update now"
    assert len(at.button) == 0 and "Update now" not in at.warning[0].value
    assert f"You can download the installer from [the GridSim download page]({PAGE})" in at.warning[0].value


def test_a_fault_while_drawing_the_banner_shows_nothing_instead_of_a_traceback(banner, net, monkeypatch):
    net.answers = [api(release())]
    update.check()

    def broken():
        raise RuntimeError("something nobody thought of")

    monkeypatch.setattr(update, "current", broken)
    at = banner()                                                      # (banner() itself asserts: no exception)
    assert len(at.button) == 0 and len(at.get("exception")) == 0


def test_check_for_updates_in_the_menu_asks_github_now_and_says_what_it_found(net, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from gridsim import installed
    script = app_function("update_check_button")
    at = AppTest.from_string(script, default_timeout=30).run()
    assert not at.exception and len(at.sidebar.button) == 0           # a source folder has no such button
    monkeypatch.setattr(installed, "is_installed", lambda: True)
    at = AppTest.from_string(script, default_timeout=30).run()
    assert [b.label for b in at.sidebar.button] == ["Check for updates"] and net.calls == []
    for answer, said in ((api(release()), "GridSim 2026.11.20 is available: see the top of the page."),
                         (api(release(tag="v2026.11.12")), "You have the newest version."),
                         (urllib.error.URLError("offline"), "GitHub could not be reached just now."),
                         (http_error(404), "GitHub lists no GridSim release yet."),
                         (api(release(state="open")), "its installer is not on GitHub yet")):
        net.answers = [answer]
        net.later(minutes=1)
        at.sidebar.button[0].click().run()
        assert not at.exception and any(said in c.value for c in at.sidebar.caption), said
    assert len(net.calls) == 5
