"""Tests for the gsv_pano bug fixes of 2026-09-29.

Each test builds the failing case from a real photometa answer (tests/fixtures,
panorama kptnDLilHehf76nzhq3m-w, 228 W 104th St, New York, 2024-08) and checks
that the fixed code handles it.  Run:  python -m pytest tests -q
"""
import copy
import gzip
import json
import logging

import pytest

from conftest import FIXTURES

import utils  # noqa: E402  (gsv_pano/utils.py, path set in conftest)


@pytest.fixture()
def raw():
    body = gzip.decompress((FIXTURES / "photometa_kptn_labels.json.gz").read_bytes()).decode("utf-8", "surrogateescape")
    return json.loads(body[body.find("["):])


def _refactor(raw_answer):
    return utils.refactorJson(utils.compressJson(copy.deepcopy(raw_answer)))


def test_time_machine_keeps_dates_after_a_photo_without_elevation(raw):
    m5 = raw[1][0][5][0]
    first = m5[8][0][0]
    node = m5[3][0][first]
    node[0] = [10, "CIHM0ogKEICAgICEosj0NQ"]          # a public photo sphere ...
    node[2][1] = None                                  # ... without elevation, as seen in real answers
    tm = utils.getTimeMachine(raw)
    assert len(tm) == len(m5[8]) == 12                 # before the fix: 0 (everything after it was dropped)
    assert tm[0]["elevation_egm96_m"] is None and tm[0]["panoId"].startswith("CIHM0og")
    assert all(t["elevation_egm96_m"] is not None for t in tm[1:])


def test_links_missing_does_not_stop_refactor(raw):
    raw[1][0][5][0][6] = None                          # a photo without road links
    out = _refactor(raw)
    assert out["Links"] == []
    assert len(out["Time_machine"]) == 12              # before the fix: refactorJson stopped early
    assert "depth_map" in out["model"] and out["Location"]["description"] == "228 W 104th St"


def test_missing_depth_map_keeps_address(raw):
    raw[1][0][5][0][5] = None
    out = _refactor(raw)
    assert "depth_map" not in out["model"]
    assert out["Location"]["description"] == "228 W 104th St"    # before the fix: ""
    assert out["Location"]["region"] == "New York" and out["Location"]["country"] == "US"


def test_compress_without_click_to_go_map_still_packs_depth(raw):
    raw[1][0][5][0][5][3] = None                       # no click-to-go map
    out = _refactor(raw)
    data = utils.parse(out["model"]["depth_map"])        # before the fix: zlib error, depth unreadable
    header = utils.parseHeader(data)
    assert (header["width"], header["height"]) == (512, 256)


def test_elevation_wgs84_is_filled(raw):
    loc = _refactor(raw)["Location"]
    assert loc["elevation_egm96_m"] - loc["elevation_wgs84_m"] == pytest.approx(32.5, abs=0.2)   # NYC geoid


def test_unchanged_fields(raw):
    out = _refactor(raw)
    assert out["Location"]["panoId"] == "kptnDLilHehf76nzhq3m-w"
    assert out["Projection"]["tilt_yaw_deg"] == pytest.approx(90.955, abs=1e-3)   # pitch + 90 (name kept)
    assert out["Projection"]["tilt_pitch_deg"] == pytest.approx(0.947, abs=1e-3)  # roll (name kept)
    assert len(out["Links"]) == 2 and out["Data"]["image_width"] == 16384


class _Resp:
    def __init__(self, text):
        self.text, self.content, self.status_code = text, text.encode(), 200

    def raise_for_status(self):
        pass


def test_search_bad_answer_logs_the_real_error(monkeypatch, caplog):
    import pano
    # 2026-10-07: the search is Google's photometa/si now (SingleImageSearch was turned off)
    bad = ")]}'\n" + "[" * 600 + "x" * 600                                         # long but not JSON
    monkeypatch.setattr(pano.requests, "get", lambda *a, **k: _Resp(bad))
    obj = pano.GSV_pano.__new__(pano.GSV_pano)
    with caplog.at_level(logging.ERROR):
        assert obj.getPanoIDfrmLonlat(-73.9679, 40.7997) == (0, 0, 0)
    text = " ".join(r.getMessage() for r in caplog.records)
    # before the fix the bare "except:" logged math.e (2.718...) from "from math import *"
    assert "2.718" not in text and "Expecting value" in text


def test_image_from_headings_calls_without_type_error(monkeypatch):
    import pano
    calls = []
    real = pano.GSV_pano.getImagefrmAngle

    def spy(self, *args, **kwargs):
        calls.append(kwargs)
        import inspect
        inspect.signature(real).bind(self, *args, **kwargs)    # raises TypeError on an unknown keyword
        return 0, 0
    monkeypatch.setattr(pano.GSV_pano, "getImagefrmAngle", spy)
    obj = pano.GSV_pano.__new__(pano.GSV_pano)
    obj.panoId = "kptnDLilHehf76nzhq3m-w"
    obj.jdata = {"Projection": {"pano_yaw_deg": 119.0}}
    obj.get_image_from_headings(saved_path="", heading_list=[0, 90])   # before the fix: TypeError
    assert len(calls) == 2 and all("override" not in c for c in calls)


def test_download_loops_log_their_error(monkeypatch, caplog):
    import pano
    obj = pano.GSV_pano.__new__(pano.GSV_pano)
    obj.panoId = "kptnDLilHehf76nzhq3m-w"
    obj.jdata = {"Time_machine": [{"image_date": None}], "Links": [{}]}   # both loops raise inside
    with caplog.at_level(logging.ERROR):
        obj.download_time_machine_jsons(saved_path="")
        obj.download_pano_json_links(saved_path="")
    messages = [r.getMessage() for r in caplog.records]
    assert any("panorama loop:" in m for m in messages) and any("link loop:" in m for m in messages)
