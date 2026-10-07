"""Coordinate -> panorama ID after Google turned off GeoPhotoService.SingleImageSearch
(2026-10-05), and SIM's own User-Agent: Google refuses the default names of download
tools (python-requests, Python-urllib, curl ...) on every download.

The fixture is a real answer (2026-10-07) of photometa.search_url() with
LOCATE_SECTIONS for SIM's test point in Newark, NJ.  Run:  python -m pytest tests -q
Live checks (network):  SIM_LIVE=1 python -m pytest tests -q -k live
"""
import os

import pytest

from conftest import FIXTURES

import photometa  # noqa: E402  (gsv_pano/photometa.py, path set in conftest)
import utils  # noqa: E402

NEWARK = (40.73031168738437, -74.18154077638651)          # (lat, lon), SIM's test case
FOUND = (FIXTURES / "search_newark_locate.txt").read_bytes()
EMPTY = b")]}'\n[[5],[]]"
live = pytest.mark.skipif(os.environ.get("SIM_LIVE") != "1", reason="set SIM_LIVE=1 for network tests")


def test_search_url_asks_for_id_and_position_only():
    url = photometa.search_url(*NEWARK, 50, sections=photometa.LOCATE_SECTIONS)
    assert url.startswith("https://www.google.com/maps/photometa/si/v1?")
    assert "!3d40.73031168738437!4d-74.18154077638651!2d50" in url
    assert "!1e1!1e4!" in url                                  # sections 1 (image) and 4 (position)


def test_found_answer_gives_id_and_position():
    pano_id, lat, lon = photometa.search_found(FOUND)
    assert pano_id == "hADLYHBTA0IUzJ6ynjTn6w"
    assert abs(lat - NEWARK[0]) < 2e-4 and abs(lon - NEWARK[1]) < 2e-4   # the photo is ~5 m from the point


def test_no_photo_within_the_radius():
    assert photometa.search_found(EMPTY) is None
    assert photometa.search_found(EMPTY.decode()) is None


@pytest.mark.parametrize("body", [
    b"<!DOCTYPE html><html>Error 500 (Server Error)</html>",                       # a refused tool name
    b'/**/_xdc_._v2mub5 && _xdc_._v2mub5( [[5,"generic","... is decommissioned and turned down."]] )',
    b")]}'\n[[],[[1],[2,\"short\"]],[0.0]]",                                        # no real panorama ID
])
def test_other_answers_are_errors_not_empty_places(body):
    with pytest.raises(ValueError):
        photometa.search_found(body)


REFUSED_NAMES = ("python-requests", "python-urllib", "curl", "wget", "go-http-client", "java", "okhttp")


def test_sim_names_itself_street_view_and_never_a_refused_tool():
    tag = photometa.HEADERS["User-Agent"]
    assert "Street View" in tag and "street_image_mapping_v2" in tag
    assert not any(name in tag.lower() for name in REFUSED_NAMES)
    assert photometa.BROWSER_HEADERS is photometa.HEADERS          # old name still works


def test_open_url_sends_sims_name(monkeypatch):
    import urllib.request
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["ua"], seen["url"], seen["timeout"] = req.get_header("User-agent"), req.full_url, timeout
        return "file"
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert utils.open_url("https://geo1.ggpht.com/cbk?output=tile") == "file"
    assert seen["ua"] == photometa.HEADERS["User-Agent"] and seen["timeout"] == 60


class _Resp:
    def __init__(self, status, content):
        self.status_code, self.content = status, content
        self.text = content.decode("utf-8", "replace")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture()
def pano_module():
    try:
        import pano
    except ImportError as e:            # pano.py needs cv2, pykrige, sklearn ...
        pytest.skip(f"pano.py dependencies missing: {e}")
    return pano


@pytest.mark.parametrize("status,body,expected", [
    (200, FOUND, ("hADLYHBTA0IUzJ6ynjTn6w", -74.18156319664435, 40.73035441887232)),   # (id, lon, lat)
    (200, EMPTY, (0, 0, 0)),
    (500, b"<html>Error 500</html>", (0, 0, 0)),
])
def test_getPanoIDfrmLonlat(pano_module, monkeypatch, status, body, expected):
    calls = []

    def fake_get(url, headers=None, proxies=None, timeout=None):
        calls.append((url, headers))
        return _Resp(status, body)
    monkeypatch.setattr(pano_module.requests, "get", fake_get)
    gsv = pano_module.GSV_pano()
    assert gsv.getPanoIDfrmLonlat(NEWARK[1], NEWARK[0]) == expected
    url, headers = calls[0]
    assert "photometa/si/v1" in url and "!3d40.73031168738437!4d-74.18154077638651" in url
    assert headers == photometa.HEADERS


def test_photo_record_is_asked_again_after_a_stripped_answer(pano_module, monkeypatch):
    import gzip
    full = gzip.decompress((FIXTURES / "photometa_kptn_navtable.json.gz").read_bytes())
    stripped = b")]}'\n[[],[[[2],[2,\"kptnDLilHehf76nzhq3m-w\"],null,null,null,null,null,[null,2]]]]"
    answers, headers_seen = [stripped, full], []

    def fake_get(url, headers=None, proxies=None, timeout=None):
        headers_seen.append(headers)
        return _Resp(200, answers.pop(0))
    monkeypatch.setattr(pano_module.requests, "get", fake_get)
    monkeypatch.setattr(pano_module.time, "sleep", lambda s: None)
    gsv = pano_module.GSV_pano()
    jdata = gsv.getJsonfrmPanoID("kptnDLilHehf76nzhq3m-w", saved_path="", json_override=True)
    assert len(headers_seen) == 2 and all(h == photometa.HEADERS for h in headers_seen)
    assert jdata["Location"]["panoId"] == "kptnDLilHehf76nzhq3m-w"


@live
def test_live_search_newark_and_nothing_at_sea():
    import requests
    r = requests.get(photometa.search_url(*NEWARK, 50, sections=photometa.LOCATE_SECTIONS),
                     headers=photometa.HEADERS, timeout=30)
    pano_id, lat, lon = photometa.search_found(r.content)
    assert len(pano_id) == 22 and abs(lat - NEWARK[0]) < 5e-4 and abs(lon - NEWARK[1]) < 5e-4
    r = requests.get(photometa.search_url(0.0, -30.0, 50, sections=photometa.LOCATE_SECTIONS),
                     headers=photometa.HEADERS, timeout=30)
    assert photometa.search_found(r.content) is None


@live
def test_live_gsv_pano_from_coordinates(pano_module):
    gsv = pano_module.GSV_pano(request_lon=NEWARK[1], request_lat=NEWARK[0])
    assert len(gsv.panoId) == 22
    assert gsv.jdata["Location"]["panoId"] == gsv.panoId                      # full record downloaded
    assert abs(gsv.lat - NEWARK[0]) < 5e-4 and abs(gsv.lon - NEWARK[1]) < 5e-4
    img, _name = gsv.getImagefrmAngle(saved_path="", yaw=0, pitch=0, width=256, height=256, fov=90)
    assert img != 0 and img.size == (256, 256)                                # flat view downloaded
