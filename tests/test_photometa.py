"""Named photometa requests and the full answer parser (gsv_pano/photometa.py).

Fixtures are two real answers for panorama kptnDLilHehf76nzhq3m-w (228 W 104th
St, 2024-08), fetched 2026-09-29: SIM's request plus section 18 (label
picture), and SIM's request with the click-to-go map of kind 2 only.
"""
from __future__ import annotations

import gzip
import math
import sys
from pathlib import Path

import numpy as np
import pytest

from conftest import FIXTURES as FIX, REPO

import gsv_pb as pb  # noqa: E402
import photometa as g  # noqa: E402
PANO = "kptnDLilHehf76nzhq3m-w"
# SIM's request (GSV_pano.getJsonfrmPanoID) with language "en"; SIM itself sends "zh-CN" (test below)
SIM_URL = (
    "https://www.google.com/maps/photometa/v1?authuser=0&hl=en&pb=!1m4!1smaps_sv.tactile!11m2!2m1!1b1"
    "!2m2!1sen!2sus!3m3!1m2!1e2!2s{}!4m57!1e1!1e2!1e3!1e4!1e5!1e6!1e8!1e12!2m1!1e1!4m1!1i48!5m1!1e1"
    "!5m1!1e2!6m1!1e1!6m1!1e2!9m36!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3!1m3!1e3!2b1!3e2!1m3!1e3!2b0!3e3"
    "!1m3!1e8!2b0!3e3!1m3!1e1!2b0!3e3!1m3!1e4!2b0!3e3!1m3!1e10!2b1!3e2!1m3!1e10!2b0!3e3")
SEARCH_PB = ("!1m5!1sapiv3!5sUS!11m2!1m1!1b0!2m4!1m2!3d40.7997!4d-73.9679!2d50!3m10!2m2!1sen!2sGB!9m1!1e2"
             "!11m4!1m3!1e2!2b1!3e2!4m10!1e1!1e2!1e3!1e4!1e8!1e6!5m1!1e2!6m1!1e2")
WEB_PEGMAN_PB = "!1e1!3s2026-09-29T14%3A50%3A57.043-04%3A00!5m2!1sagi8ap2UJbqOwbkPn8SHoQ0!7e81"


def _answer(name):
    return gzip.decompress((FIX / name).read_bytes())


@pytest.mark.parametrize("s", [SIM_URL.split("pb=")[1].format(PANO), SEARCH_PB, WEB_PEGMAN_PB])
def test_pb_round_trip_is_exact(s):
    assert pb.dumps(pb.loads(s)) == s


def test_pb_tree_names_sim_parts():
    tree = pb.loads(SIM_URL.split("pb=")[1].format(PANO))
    opts = next(v for k, t, v in tree if k == 4)
    assert [v for k, t, v in opts if k == 1] == [1, 2, 3, 4, 5, 6, 8, 12]
    assert next(v for k, t, v in tree if k == 3) == [(1, "m", [(1, "e", 2), (2, "s", PANO)])]


def test_named_request_equals_sim_string():
    assert g.request_url(PANO) == SIM_URL.format(PANO)


def test_named_request_equals_the_string_in_pano_py():
    import re
    src = (REPO / "gsv_pano" / "pano.py").read_text(encoding="utf-8")
    sim = re.search(r'url = "(https://www.google.com/maps/photometa/v1\?[^"]+)"', src).group(1)
    assert g.request_url(PANO, language="zh-CN") == sim.format(PANO)


def test_named_request_adds_labels_and_drops_nav():
    url = g.request_url(PANO, sections=g.SIM_SECTIONS + ("labels",), nav_kinds=())
    opts = next(v for k, t, v in pb.loads(url.split("pb=")[1]) if k == 4)
    assert 18 in [v for k, t, v in opts if k == 1]
    assert not [x for x in opts if x[0] == 6]


@pytest.fixture(scope="module")
def rec():
    return g.parse(_answer("photometa_kptn_labels.json.gz"))


def test_parse_identity_pose_and_address(rec):
    assert rec["pano_id"] == PANO and rec["frontend"] == 2
    assert rec["image"]["width"] == 16384 and rec["image"]["levels"][0] == (256, 512)
    assert rec["address"] == ["228 W 104th St", "New York"]
    assert rec["capture"] == {"year": 2024, "month": 8} and rec["status_code"] == [3, 4, 1]
    p = rec["pose"]
    assert p["lat"] == pytest.approx(40.799662, abs=1e-6) and p["heading_deg"] == pytest.approx(119.044, abs=1e-3)
    assert p["pitch_deg"] == pytest.approx(0.955, abs=1e-3) and p["roll_deg"] == pytest.approx(0.947, abs=1e-3)
    assert p["elevation_egm96_m"] - p["ellipsoid_height_m"] == pytest.approx(32.5, abs=0.2)   # NYC geoid
    assert rec["unparsed_top"] == {}


def test_parse_graph_history_streets_places(rec):
    nodes = rec["neighbours"]
    assert len(nodes) == 58 and nodes[0]["pano_id"] == PANO
    assert [l["pano_id"] for l in rec["links"]] == ["rWY2F7wuSAt45ec_S54anQ", "kv6x74eoX3dMUwkczW09dg"]
    assert len(rec["history"]) == 12 and (2009, 4) in [(h["year"], h["month"]) for h in rec["history"]]
    assert all(h["pano_id"] for h in rec["history"])
    assert rec["street_names"][0]["name"] == "W 104th St"
    assert rec["street_names"][0]["bearings_deg"][0] == pytest.approx(rec["pose"]["heading_deg"], abs=1.0)
    assert any(p["type"] == "Post office" and p["marker"]["distance_m"] > 0 for p in rec["places"])


def test_parse_depth_planes(rec):
    dp = rec["depth_planes"]
    assert dp["index"].shape == (256, 512) and len(dp["planes"]) == 112
    depth = g.depth_image(dp)
    assert 1.5 < depth[-1, 256] < 3.5                      # straight down: the ground under the camera
    assert depth[0, 256] == 0                              # straight up: sky


def test_parse_nav_map_kind1_points_into_neighbour_list(rec):
    nv = rec["nav_map"]
    assert nv["kind"] == 1 and nv["map"].shape == (256, 512)
    assert int(nv["map"].max()) < len(rec["neighbours"])


def test_parse_label_picture(rec):
    lb = rec["labels"]
    assert lb.shape == (256, 512)
    share = {int(v): c / lb.size for v, c in zip(*np.unique(lb, return_counts=True))}
    assert share[1] > 0.15 and share[0] > 0.15            # sky; ground next to the car
    assert share[4] > 0.05                                 # road
    assert np.mean(lb[:40] == 1) > 0.8                     # top rows are sky
    assert 3 in share                                      # trees


def test_parse_nav_map_kind2_table():
    rec = g.parse(_answer("photometa_kptn_navtable.json.gz"))
    nv = rec["nav_map"]
    assert nv["kind"] == 2 and len(nv["photos"]) == 46 and nv["photos"][0] == PANO
    assert tuple(nv["offsets_m"][0]) == (0.0, 0.0)
    assert 1 <= int(nv["map"].min()) and int(nv["map"].max()) <= 46
    # the table's offsets are the neighbours' distances (rotated into the car's frame)
    import pyproj
    geod = pyproj.Geod(ellps="WGS84")
    by_id = {n["pano_id"]: n for n in rec["neighbours"]}
    p0 = rec["pose"]
    checked = 0
    for pid, (x, y) in zip(nv["photos"][1:], nv["offsets_m"][1:]):
        n = by_id.get(pid)
        if n:
            _, _, dist = geod.inv(p0["lon"], p0["lat"], n["lon"], n["lat"])
            assert math.hypot(x, y) == pytest.approx(dist, abs=0.5)
            checked += 1
    assert checked >= 40
    assert rec["labels"] is None and rec["depth_planes"] is not None


def test_load_answer_accepts_search_shapes():
    import json
    text = _answer("photometa_kptn_labels.json.gz").decode("utf-8", "surrogateescape")
    m = json.loads(text[text.find("["):])[1][0]
    si = ")]}'\n" + json.dumps([[], m, [81.1]])                      # photometa/si/v1 (bearing)
    jsonp = "/**/_xdc_._x && _xdc_._x( " + json.dumps([[0], m]) + " )"   # SIM SingleImageSearch
    for body in (si, jsonp):
        rec = g.parse(body.encode("utf-8", "surrogateescape"))
        assert rec["pano_id"] == PANO and len(rec["neighbours"]) == 58


def test_labels_url_is_the_short_tested_request():
    # tested against Google 2026-09-29: HTTP 200, ~4 KB answer with m[20] (a hand-typed !4m6 gave HTTP 400)
    assert g.labels_url(PANO) == (g.ENDPOINT + "!1m1!1smaps_sv.tactile!2m2!1sen!2sus!3m3!1m2!1e2!2s"
                                  + PANO + "!4m1!1e18")


def test_search_url_structure():
    url = g.search_url(40.7997, -73.9679, 50, sections=g.SIM_SECTIONS + ("labels",))
    assert url.startswith(g.SEARCH_ENDPOINT)
    s = url.split("pb=")[1]
    assert pb.dumps(pb.loads(s)) == s
    assert "!2m4!1m2!3d40.7997!4d-73.9679!2d50" in s                   # where and how far
    part3 = next(v for k, t, v in pb.loads(s) if k == 3)
    kinds = [dict((a, c) for a, b_, c in x[2])[1] for k, t, v in part3 if k == 11 for x in v]
    assert kinds == [2]                                                # Google photos only (no type 3)
    assert "!1e18" in s


def test_label_names_cover_all_seen_numbers():
    assert sorted(g.LABEL_NAMES) == list(range(44))
    assert g.LABEL_NAMES[3].startswith("tree") and g.LABEL_NAMES[25] == "pole" and g.LABEL_NAMES[4] == "road"
    grouped = {v for vs in g.LABEL_GROUPS.values() for v in vs}
    assert grouped <= set(g.LABEL_NAMES) and 3 not in g.LABEL_GROUPS["tree_lookalike"]


def test_named_switches_photo_type_surroundings_language():
    assert g.request_url(PANO) == SIM_URL.format(PANO)                       # defaults unchanged
    pub = g.request_url("CIHM0ogKEICAgICE44uGRA", photo_type="public")
    assert "!3m3!1m2!1e10!2sCIHM0ogKEICAgICE44uGRA" in pub                   # tested: opens a public photo sphere
    assert "!2m1!1e2" in g.request_url(PANO, surroundings="none").split("!4m")[1]
    assert "!2m1!1e0" in g.request_url(PANO, surroundings="links_only").split("!4m")[1]
    assert "hl=fr" in g.request_url(PANO, hl="fr") and "hl=en" not in g.request_url(PANO, hl="fr")
    assert "!1e10!2s" in g.labels_url("CIHM0ogKEICAgICE44uGRA", photo_type="public")


def test_parse_feature_ids_and_optional_fields(rec):
    import re
    places = [p for p in rec["places"] if p["feature_id"]]
    assert places and all(re.fullmatch(r"0x[0-9a-f]+:0x[0-9a-f]+", p["feature_id"]) for p in places)
    assert all(p["cid"] is None or int(p["cid"]) == int(p["feature_id"].split(":0x")[1], 16) for p in places)
    assert re.fullmatch(r"0x[0-9a-f]+:0x[0-9a-f]+", rec["street_names"][0]["feature_id"])
    assert all("day" in h for h in rec["history"]) and all("level" in n for n in rec["neighbours"])
