"""GSV_pano.get_DOM() fixes of 2026-10-08, offline: a real photometa record
(tests/fixtures, kptnDLilHehf76nzhq3m-w, New York) with its depth map, and a
synthetic panorama whose green channel grows from the top row (sky) to the
bottom row (ground under the car), so no picture is downloaded.

Before the fix get_DOM() raised without set_segmentation_path() and crs_local,
returned None on a second call with another resolution, and could return a
saved segmentation DOM when a colour DOM was asked for (same file name).
"""
import gzip
import json

import numpy as np
import pytest
from PIL import Image

from conftest import FIXTURES

import utils  # noqa: E402  (gsv_pano/utils.py, path set in conftest)

PANO_ID = "kptnDLilHehf76nzhq3m-w"


@pytest.fixture()
def pano_module():
    try:
        import pano
    except ImportError as e:            # pano.py needs cv2, pykrige, sklearn ...
        pytest.skip(f"pano.py dependencies missing: {e}")
    return pano


@pytest.fixture()
def record(tmp_path):
    body = gzip.decompress((FIXTURES / "photometa_kptn_navtable.json.gz").read_bytes()).decode("utf-8", "surrogateescape")
    jdata = utils.refactorJson(utils.compressJson(json.loads(body[body.find("["):])))
    path = tmp_path / "record.json"
    path.write_text(json.dumps(jdata))
    return path, jdata


ZOOM = 1


def make_pano(pano_module, record, tmp_path, **kw):
    path, jdata = record
    p = pano_module.GSV_pano(json_file=str(path), saved_path=str(tmp_path), **kw)
    h, w = jdata["Data"]["level_sizes"][ZOOM][0]
    rows, cols = np.mgrid[0:h, 0:w]
    image = np.stack([cols * 255 // w, rows * 255 // h, np.full_like(rows, 128)], axis=-1).astype(np.uint8)
    p.panorama = {"image": image, "zoom": ZOOM}          # get_panorama() then downloads nothing
    return p


def test_utm_epsg():
    assert utils.utm_epsg(-73.96792, 40.79966) == 32618      # New York
    assert utils.utm_epsg(-81.0011, 33.9945) == 32617        # Columbia, SC
    assert utils.utm_epsg(151.2, -33.9) == 32756             # Sydney
    assert utils.utm_epsg(180.0, 10.0) == 32601


def test_colour_dom_without_segmentation_or_crs(pano_module, record, tmp_path):
    import pyproj
    p = make_pano(pano_module, record, tmp_path)
    assert p.crs_local is None and p.segmenation["full_path"] is None
    dom = p.get_DOM(width=20, height=20, resolution=0.5, zoom=ZOOM)
    assert dom is not None and dom["DOM"].shape == (40, 40, 3)
    assert p.crs_local == 32618                              # UTM 18N chosen and kept
    tif, tfw = tmp_path / f"{PANO_ID}_DOM_0.50.tif", tmp_path / f"{PANO_ID}_DOM_0.50.tfw"
    assert tif.exists() and tfw.exists()
    x0, y0 = [float(v) for v in tfw.read_text().split()[4:6]]
    x, y = pyproj.Transformer.from_crs(4326, 32618).transform(p.lat, p.lon)
    assert abs(x0 + 10 - x) < 1e-6 and abs(y0 - 10 - y) < 1e-6    # world file centred on the camera
    # Geometry: green = how far down the panorama a pixel came from.  Under the camera
    # (2 x 2 centre, <= 0.7 m away, camera ~2.5 m high) the ground is seen >= 74 deg down
    # (green ~ 230+); the corners (~14 m away) only ~10 deg below the horizon (green ~ 140).
    g = dom["DOM"][:, :, 1].astype(float)
    centre, corners = g[19:21, 19:21].mean(), np.mean([g[0, 0], g[0, -1], g[-1, 0], g[-1, -1]])
    assert centre > 220 and corners < 170, (centre, corners)


def test_given_crs_local_is_kept(pano_module, record, tmp_path):
    p = make_pano(pano_module, record, tmp_path, crs_local=32617)
    assert p.get_DOM(width=20, height=20, resolution=0.5, zoom=ZOOM) is not None
    assert p.crs_local == 32617


def test_second_call_with_other_settings_computes_again(pano_module, record, tmp_path):
    p = make_pano(pano_module, record, tmp_path)
    first = p.get_DOM(width=20, height=20, resolution=0.5, zoom=ZOOM)["DOM"].copy()
    second = p.get_DOM(width=20, height=20, resolution=1.0, zoom=ZOOM)       # returned None before
    assert first.shape == (40, 40, 3) and second is not None and second["DOM"].shape == (20, 20, 3)
    again = p.get_DOM(width=20, height=20, resolution=0.5, zoom=ZOOM)        # back to 0.5: the saved file
    assert np.array_equal(again["DOM"], first)


def test_bad_requests_are_clear_errors(pano_module, record, tmp_path):
    p = make_pano(pano_module, record, tmp_path)
    with pytest.raises(ValueError, match="set_segmentation_path"):
        p.get_DOM(resolution=0.5, zoom=ZOOM, img_type="segmentation")
    with pytest.raises(ValueError, match="img_type"):
        p.get_DOM(resolution=0.5, zoom=ZOOM, img_type="colour")


def test_saved_file_of_right_kind_is_reused(pano_module, record, tmp_path, monkeypatch):
    make_pano(pano_module, record, tmp_path).get_DOM(width=20, height=20, resolution=0.5, zoom=ZOOM)
    p = make_pano(pano_module, record, tmp_path)
    monkeypatch.setattr(p, "calculate_DOM", lambda **kw: pytest.fail("recomputed instead of reading the file"))
    assert p.get_DOM(width=20, height=20, resolution=0.5, zoom=ZOOM)["DOM"].shape == (40, 40, 3)


def test_saved_file_of_the_other_kind_is_neither_returned_nor_overwritten(pano_module, record, tmp_path, monkeypatch):
    shared = tmp_path / f"{PANO_ID}_DOM_0.50.tif"
    seg = np.arange(1600, dtype=np.uint8).reshape(40, 40) % 7               # a 1-channel class picture
    Image.fromarray(seg, "P").save(shared)
    p = make_pano(pano_module, record, tmp_path)
    dom = p.get_DOM(width=20, height=20, resolution=0.5, zoom=ZOOM)
    assert dom["DOM"].shape == (40, 40, 3)                                   # not the class picture
    assert np.array_equal(np.array(Image.open(shared)), seg)                 # left untouched
    typed = tmp_path / f"{PANO_ID}_DOM_DOM_0.50.tif"
    assert typed.exists() and (tmp_path / f"{PANO_ID}_DOM_DOM_0.50.tfw").exists()
    p2 = make_pano(pano_module, record, tmp_path)                            # found again by name
    monkeypatch.setattr(p2, "calculate_DOM", lambda **kw: pytest.fail("recomputed instead of reading the file"))
    assert np.array_equal(p2.get_DOM(width=20, height=20, resolution=0.5, zoom=ZOOM)["DOM"], dom["DOM"])
