"""Google Street View "photometa": build the request by name, parse every field.

GSV_pano.getJsonfrmPanoID() sends one long pb string copied from the
browser, and utils.refactorJson() reads a few fixed list positions.  This
optional module writes the same request from named parts (see gsv_pb.py for
the pb format) and turns the whole answer into named fields, including
the four pictures hidden in it.  Every name below was tested on 2026-09-29 by
switching one request part at a time.  Reference: docs/GSV_API_REFERENCE.md;
evidence: docs/GSV_FINDINGS_20260929.md.

    python gsv_pano/photometa.py PANO_ID [--labels] [--nav-table] [--out DIR]
    python gsv_pano/photometa.py PANO_ID --file SAVED_ANSWER.json     # parse a saved answer, no download

Answer layout (m = answer[1][0], m5 = m[5][0]):
  m[1]  pano id          m[2]  image and tile sizes     m[3]  camera address
  m[4]  copyright and uploader                          m[6]  status code, source, capture date
  m[7]  report link      m[19] photo reference          m[20] label picture (section 18)
  m5[1] position, heights, heading, pitch + 90, roll     m5[3] neighbour list with poses
  m5[5] depth planes + click-to-go map                  m5[6] road links
  m5[8] history (other dates)                           m5[9] nearby places
  m5[12] street names with road bearings
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gsv_pb as pb  # noqa: E402

ENDPOINT = "https://www.google.com/maps/photometa/v1?authuser=0&hl=en&pb="

# Request part 4.1: which blocks of the answer to send.  Tested one by one.
SECTIONS = {
    "image": 1,            # m[2]: image size, tile size, zoom levels
    "capture": 2,          # m[3] address, m[6] status/source/date (and neighbour addresses)
    "attribution": 3,      # m[4]: copyright, uploader name, link and picture
    "unknown_4": 4,        # coordinate search: adds m5[1], the photo's position (tested 2026-10-07); else no visible effect
    "places": 5,           # m5[9]: nearby places (also needs places_flag)
    "surroundings": 6,     # m5[3] neighbours, m5[5] pictures, m5[6] links, m5[8] history
    "street_names": 8,     # m5[12]: street names and road bearings
    "flag_12": 12,         # m[12]: a 2-byte flag
    "labels": 18,          # m[20]: 512 x 256 label picture (road, sky, building, tree, pole ...)
}
SIM_SECTIONS = ("image", "capture", "attribution", "unknown_4", "places", "surroundings", "street_names", "flag_12")

# Request parts 4.5 and 4.6: which pictures come inside m5[5].
DEPTH_KINDS = (1, 2)       # 4.5: kind 2 = the plane depth map (kind 1 adds nothing seen)
NAV_KINDS = (1, 2)         # 4.6: kind 1 = click-to-go map indexed by the neighbour list,
                           #      kind 2 = the same map with its own photo table (sent only alone)

# Request part 4.9: photo types kept in the neighbour list (type, flag, level).  SIM's list.
SIM_TYPE_FILTER = ((2, True, 2), (2, False, 3), (3, True, 2), (3, False, 3), (8, False, 3),
                   (1, False, 3), (4, False, 3), (10, True, 2), (10, False, 3))

# Request part 3.1.1 (and neighbour-list entries m5[3][0][k][0][0]): the kind of photo.  Tested 2026-09-29:
# a Google id opens only with 2, a public photo-sphere id (CIHM0og..., CIABIh...) only with 10 (3 gives nothing).
PHOTO_TYPES = {"google": 2, "public": 10}
# Request part 4.2.1: how much of the surroundings to send.  1 = everything (all neighbours, history,
# depth map, click-to-go map, street names); 0 or 3 = only the road-linked neighbours + depth map
# (no history, click-to-go map or street names); 2 = none of it.
SURROUNDINGS = {"full": 1, "links_only": 0, "none": 2}


# Google's label picture (part 18): all 44 numbers (0-43), named 2026-09-29 by looking at outlined
# close-ups in 519 label pictures (New York + 12 other places), the rare ones in full-resolution
# zoom-4 tiles.  '?' = likely but not certain.
LABEL_NAMES = {
    0: "ground next to the car", 1: "sky", 2: "building", 3: "tree/vegetation (canopy and trunk)", 4: "road",
    5: "sand / dirt ground", 6: "sidewalk", 7: "crosswalk", 8: "driveway", 9: "grass / planting bed",
    10: "gravel / rocks (incl. rail track bed)", 11: "curb / low ledge", 12: "fence", 13: "wall",
    14: "road barrier / guard rail", 15: "tunnel / covered area", 16: "bridge / overpass", 17: "bus shelter / kiosk",
    18: "call box / booth (fire-alarm box)", 19: "traffic sign", 20: "traffic light", 21: "street lamp",
    22: "parking pay station", 23: "mailbox / utility box", 24: "fire hydrant", 25: "pole",
    26: "banner / flag / sign board", 27: "billboard", 28: "other structure (shed, canopy, scaffolding, container, rock)",
    29: "temporary / construction object", 30: "portable sign / delineator post", 31: "traffic cone", 32: "car",
    33: "bus", 34: "truck / van", 35: "train", 36: "motorcycle / scooter", 37: "bicycle",
    38: "other vehicle (machine, trailer, cart)", 39: "person", 40: "rider", 41: "animal", 42: "water",
    43: "overhead wires?",
}
# Groups for cleaning tree records (a detection is a sight line into this picture).
LABEL_GROUPS = {
    "tree": (3,),
    "tree_lookalike": (12, 13, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 28, 29, 30, 39, 40),  # upright things mistaken for trunks
    "base_hider": (14, 29, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41),                     # can hide a trunk base
    "ground": (0, 4, 5, 6, 7, 8, 9, 10, 11),                                               # where a trunk can stand
}


def _options(sections, depth_kinds, nav_kinds, avatar_px, type_filter, surroundings="full") -> list:
    """Request part 4 (what to send back), shared by the photo and search requests."""
    options = [(1, "e", SECTIONS[s] if isinstance(s, str) else int(s)) for s in sections]
    options += [(2, "m", [(1, "e", SURROUNDINGS[surroundings])]),   # 4.2.1: how much of the surroundings
                (4, "m", [(1, "i", avatar_px)])]
    options += [(5, "m", [(1, "e", k)]) for k in depth_kinds]
    options += [(6, "m", [(1, "e", k)]) for k in nav_kinds]
    options += [(9, "m", [(1, "m", [(1, "e", t), (2, "b", f), (3, "e", lv)]) for t, f, lv in type_filter])]
    return options


def request_url(pano_id: str, sections=SIM_SECTIONS, depth_kinds=DEPTH_KINDS, nav_kinds=NAV_KINDS,
                language: str = "en", region: str = "us", places_flag: bool = True,
                avatar_px: int = 48, type_filter=SIM_TYPE_FILTER, photo_type: str = "google",
                surroundings: str = "full", hl: str = "en") -> str:
    """The photometa URL, written from named parts.  With the defaults it is
    exactly SIM's string (checked in tests).  hl (URL) sets the language of
    addresses and street names; language/region (part 2) only change which
    nearby places are listed.  photo_type "public" opens photo spheres
    uploaded by the public (ids CIHM0og..., CIABIh...)."""
    tree = [(1, "m", [(1, "s", "maps_sv.tactile"), (11, "m", [(2, "m", [(1, "b", places_flag)])])]),
            (2, "m", [(1, "s", language), (2, "s", region)]),
            (3, "m", [(1, "m", [(1, "e", PHOTO_TYPES[photo_type]), (2, "s", pano_id)])]),
            (4, "m", _options(sections, depth_kinds, nav_kinds, avatar_px, type_filter, surroundings))]
    return ENDPOINT.replace("hl=en", f"hl={hl}") + pb.dumps(tree)


SEARCH_ENDPOINT = "https://www.google.com/maps/photometa/si/v1?authuser=0&hl=en&gl=us&pb="
# The shortest coordinate search that still gives the found photo's position:
# "image" brings the panorama ID (m[1][1]) and "unknown_4" the position
# (m5[1]); about 650 bytes instead of about 360 KB with SIM_SECTIONS.
LOCATE_SECTIONS = ("image", "unknown_4")
# The name tag (User-Agent) SIM sends.  Google refuses the default names of
# download tools, even inside a longer tag: python-requests, Python-urllib,
# curl, Wget, Go-http-client, Java, okhttp (tested 2026-10-07: HTTP 403 for
# tiles and flat views, HTTP 500 for the spot search, an 80-byte stripped
# record for photo info).  Any other tag works, so SIM names itself honestly
# instead of pretending to be a browser.
HEADERS = {"User-Agent": "Mozilla/5.0 (Street View; street_image_mapping_v2)"}
BROWSER_HEADERS = HEADERS      # old name (230ec13)


def search_url(lat: float, lon: float, radius_m: float = 50, sections=SIM_SECTIONS, google_only: bool = True,
               depth_kinds=DEPTH_KINDS, nav_kinds=NAV_KINDS, language: str = "en", region: str = "us",
               avatar_px: int = 48, type_filter=SIM_TYPE_FILTER) -> str:
    """Coordinate search as Google's page sends it (photometa/si): the full
    record of the photo nearest to (lat, lon) within radius_m, answer
    ``[[], record, [bearing]]``, bearing = compass direction from the found
    photo toward (lat, lon) (tested: within ~1 deg at 12-15 m).  radius_m is a
    hard limit (nothing found = a 13-byte answer).  Part 3.11 lists the photo
    types that may be returned: 2 = Google's own; allowing 3 or 10 also returns
    photo spheres uploaded by the public (no depth map, no labels), so
    google_only keeps only 2.  Part 3.9.1 = 2 as in Google's page (1 picked a
    different photo at Times Square; 3 behaved like 2)."""
    kinds = [(2, True, 2)] + ([] if google_only else [(3, True, 2)])
    tree = [(1, "m", [(1, "s", "maps_sv.tactile"), (11, "m", [(2, "m", [(1, "b", True)])])]),
            (2, "m", [(1, "m", [(3, "d", float(lat)), (4, "d", float(lon))]), (2, "d", float(radius_m))]),
            (3, "m", [(1, "m", [(1, "m", [(1, "e", 2)])]), (2, "m", [(1, "s", language), (2, "s", region)]),
                      (9, "m", [(1, "e", 2)]),
                      (11, "m", [(1, "m", [(1, "e", t), (2, "b", f), (3, "e", lv)]) for t, f, lv in kinds])]),
            (4, "m", _options(sections, depth_kinds, nav_kinds, avatar_px, type_filter))]
    return SEARCH_ENDPOINT + pb.dumps(tree)


def labels_url(pano_id: str, photo_type: str = "google") -> str:
    """The shortest request for Google's label picture only (answer ~4 KB
    instead of ~360 KB).  Mostly the photos Google currently shows (85 % of
    current photos from 2022 on) have one."""
    tree = [(1, "m", [(1, "s", "maps_sv.tactile")]), (2, "m", [(1, "s", "en"), (2, "s", "us")]),
            (3, "m", [(1, "m", [(1, "e", PHOTO_TYPES[photo_type]), (2, "s", pano_id)])]), (4, "m", [(1, "e", SECTIONS["labels"])])]
    return ENDPOINT + pb.dumps(tree)


# ------------------------------------------------------------------ parsing
def _dig(o, *path):
    for p in path:
        if not isinstance(o, list) or p >= len(o) or o[p] is None:
            return None
        o = o[p]
    return o


def _b64(s: str) -> bytes:
    s = s + "=" * (-len(s) % 4)
    d = base64.b64decode(s.replace("-", "+").replace("_", "/"))
    try:
        return zlib.decompress(d)
    except zlib.error:
        return d


def load_answer(body) -> list:
    """The photo message m.  Accepts the three answer shapes seen:
    photometa/v1 ``[[], [m]]``, the web's coordinate search photometa/si/v1
    ``[[], m, [bearing to the searched point]]`` and SIM's JSONP SingleImageSearch
    ``_xdc_._x([[0], m ...])``.  Binary parts (the label picture) stay
    byte-exact because bytes are decoded with surrogateescape."""
    text = body.decode("utf-8", "surrogateescape") if isinstance(body, (bytes, bytearray)) else body
    a = json.loads(text[text.find("["):text.rfind("]") + 1])

    def is_photo(o):
        return isinstance(o, list) and len(o) > 1 and isinstance(o[1], list) and len(o[1]) == 2 \
            and isinstance(o[1][1], str)
    if is_photo(a):
        return a
    if len(a) > 1 and is_photo(a[1]):
        return a[1]
    return a[1][0]


def search_found(body):
    """(pano_id, lat, lon) of a :func:`search_url` answer, or None when no
    photo is within the radius (the 13-byte answer ``)]}'`` + ``[[5],[]]``).
    lat/lon are None unless the request asked for section "unknown_4".
    Raises ValueError on any other answer (an error page, a shutdown message),
    so an outage is never mistaken for a place without photos."""
    text = body.decode("utf-8", "surrogateescape") if isinstance(body, (bytes, bytearray)) else body
    start = text.find("[")
    if not text.lstrip().startswith(")]}'") or start < 0:
        raise ValueError(f"not a coordinate-search answer: {text[:120]!r}")
    a = json.loads(text[start:])
    if isinstance(a, list) and len(a) > 1 and a[1] == []:
        return None
    m = load_answer(text)
    pano_id = _dig(m, 1, 1)
    if not isinstance(pano_id, str) or len(pano_id) < 20:
        raise ValueError(f"no panorama ID in the coordinate-search answer: {text[:120]!r}")
    pos = _dig(m, 5, 0, 1, 0) or []
    return pano_id, _dig(pos, 2), _dig(pos, 3)


def _pose(block) -> dict:
    heading, a, b = (list(_dig(block, 2) or []) + [None] * 3)[:3]
    return dict(lat=_dig(block, 0, 2), lon=_dig(block, 0, 3),
                elevation_egm96_m=_dig(block, 1, 0), ellipsoid_height_m=_dig(block, 1, 2),
                heading_deg=heading,
                pitch_deg=None if a is None else ((a - 90 + 180) % 360 - 180),   # road-slope test r=+0.74
                roll_deg=None if b is None else ((b + 180) % 360 - 180),
                raw_orientation=[heading, a, b])


def depth_planes(block) -> dict | None:
    """Plane depth map: per-pixel plane number (256 x 512) and planes (n x 4)."""
    if not block:
        return None
    d = _b64(block[2])
    hs, n, w, h, off = struct.unpack_from("<BHHHB", d, 0)
    return dict(index=np.frombuffer(d, np.uint8, w * h, off).reshape(h, w),
                planes=np.frombuffer(d, "<f4", n * 4, off + w * h).reshape(n, 4))


def depth_image(planes: dict) -> np.ndarray:
    """Metres along each pixel's ray (0 = sky/none), SIM's pixel convention."""
    idx, pl = planes["index"], planes["planes"]
    h, w = idx.shape
    yy, xx = np.mgrid[0:h, 0:w]
    th = (h - yy - 0.5) / h * np.pi
    ph = (w - xx - 0.5) / w * 2 * np.pi + np.pi / 2
    v = np.stack([np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph), np.cos(th)], -1)
    den = np.einsum("ijk,ijk->ij", v, pl[idx, :3])
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where((idx > 0) & (np.abs(den) > 1e-6), -pl[idx, 3] / den, 0.0)
    return out


def nav_map(kind: int, block, neighbours: list) -> dict | None:
    """Click-to-go map: for each pixel, the photo Street View moves to.
    Kind 1: value = position in the neighbour list (0 = this photo).
    Kind 2: 8-byte header, map, then a table of photo ids (22 characters
    each) and their east/north-like offsets in metres (car-heading frame)."""
    if not block:
        return None
    d = _b64(block[2])
    if kind == 1:
        h, w = block[0]
        arr = np.frombuffer(d, np.uint8, h * w).reshape(h, w)
        return dict(kind=1, map=arr, photos=[n["pano_id"] for n in neighbours])
    hs, n, w, h, off = struct.unpack_from("<BHHHB", d, 0)
    arr = np.frombuffer(d, np.uint8, w * h, off).reshape(h, w)
    tail = d[off + w * h:]
    count = (len(tail)) // (22 + 8)
    ids = [tail[i * 22:(i + 1) * 22].decode("ascii", "replace") for i in range(count)]
    xy = np.frombuffer(tail, "<f4", 2 * count, 22 * count).reshape(count, 2)
    return dict(kind=2, map=arr, photos=ids, offsets_m=xy)       # map value v -> photos[v - 1]


def label_picture(m) -> np.ndarray | None:
    s = _dig(m, 20, 0)
    if not isinstance(s, str):
        return None
    data = s.encode("utf-8", "surrogateescape")
    if data[:4] != b"RIFF":
        return None
    from PIL import Image
    arr = np.asarray(Image.open(io.BytesIO(data)))
    return arr[..., 0] if arr.ndim == 3 else arr


def feature_id(pair) -> str | None:
    """Google Maps feature id from its two decimal numbers, in Maps' own form
    "0x<hex>:0x<hex>" (the second number is the place's public "cid")."""
    try:
        hi, lo = pair
        return f"0x{int(hi):x}:0x{int(lo):x}"
    except (TypeError, ValueError):
        return None


def parse(body) -> dict:
    """Every field of one photometa answer, by name."""
    m = load_answer(body)
    m5 = _dig(m, 5, 0) or []
    status = _dig(m, 6) or []
    rec = dict(
        answer_kind=_dig(m, 0, 0),
        pano_id=_dig(m, 1, 1), frontend=_dig(m, 1, 0),
        image=dict(height=_dig(m, 2, 2, 0), width=_dig(m, 2, 2, 1),
                   levels=[tuple(_dig(x, 0) or []) for x in (_dig(m, 2, 3, 0) or [])],
                   tile=(_dig(m, 2, 3, 1, 0), _dig(m, 2, 3, 1, 1))),
        address=[_dig(x, 0) for x in (_dig(m, 3, 2) or [])],
        copyright=_dig(m, 4, 0, 0, 0, 0),
        uploader=dict(name=_dig(m, 4, 1, 0, 0, 0), link=_dig(m, 4, 1, 0, 1), picture=_dig(m, 4, 1, 0, 2)),
        status_code=list(status[:3]),              # (3,4,1) normal, (3,2,1) no history, (1,1,1) unlinked older photo
        source=_dig(m, 6, 5, 2), source_code=_dig(m, 6, 5, 3),
        capture=dict(year=_dig(m, 6, 7, 0), month=_dig(m, 6, 7, 1)),
        report_link=_dig(m, 7, 0), photo_reference=_dig(m, 19),
        pose=_pose(_dig(m5, 1) or []), country=_dig(m5, 1, 4),
    )
    nodes = []
    for nd in _dig(m5, 3, 0) or []:
        level = _dig(nd, 2, 3)          # indoor / multi-level photos: [level id, floor number, [name], [short name]]
        nodes.append(dict(pano_id=_dig(nd, 0, 1), frontend=_dig(nd, 0, 0), **_pose(_dig(nd, 2) or []),
                          street=_dig(nd, 3, 2, 0, 0),
                          level=None if not level else dict(id=_dig(level, 0), number=_dig(level, 1),
                                                            name=_dig(level, 2, 0), short_name=_dig(level, 3, 0))))
    rec["neighbours"] = nodes

    def node_id(i):
        return nodes[i]["pano_id"] if isinstance(i, int) and 0 <= i < len(nodes) else None
    rec["links"] = [dict(neighbour=_dig(l, 0), pano_id=node_id(_dig(l, 0)), yaw_deg=_dig(l, 1, 3))
                    for l in (_dig(m5, 6) or [])]
    rec["history"] = [dict(neighbour=_dig(h, 0), pano_id=node_id(_dig(h, 0)), year=_dig(h, 1, 0), month=_dig(h, 1, 1),
                           day=_dig(h, 1, 2))                  # the day is given for very few dates (29 of ~20,700)
                      for h in (_dig(m5, 8) or [])]
    rec["places"] = [dict(id=_dig(p, 0, 1), feature_id=feature_id(_dig(p, 0, 1)), cid=_dig(p, 0, 3),
                          name=_dig(p, 2, 0), type=_dig(p, 3, 0), icon=_dig(p, 4),
                          marker=dict(zip(("x_share", "y_share", "distance_m"), _dig(p, 1, 0, 0) or [])),
                          token=_dig(p, 5, 8), code=_dig(p, 7)) for p in (_dig(m5, 9) or [])]
    rec["street_names"] = [dict(name=_dig(s, 0, 0, 2, 0), bearings_deg=list(_dig(s, 1) or []),
                                feature_id=feature_id(_dig(s, 0, 0, 0, 1)))
                           for s in (_dig(m5, 12) or [])]
    # m5[5] = [[depth kind], depth block, [nav kind], nav block]; a part left out is null
    pictures = _dig(m5, 5) or []
    rec["depth_planes"] = depth_planes(_dig(pictures, 1)) if _dig(pictures, 0, 0) == 2 else None
    nav_kind = _dig(pictures, 2, 0)
    rec["nav_map"] = nav_map(nav_kind, _dig(pictures, 3), nodes) if nav_kind in (1, 2) else None
    rec["labels"] = label_picture(m)
    # Not read on purpose (constant in 2,000 stored New York answers): m[2][0:2] = 2, 2; m[2][9] = own id;
    # m5[0][0] = 1; m5[8][k][5] = 2.
    known = {0, 1, 2, 3, 4, 5, 6, 7, 19, 20}
    rec["unparsed_top"] = {i: json.dumps(x)[:80] for i, x in enumerate(m) if i not in known and x not in (None, [])}
    return rec


def summary(rec: dict) -> dict:
    """Printable overview (arrays reduced to shapes)."""
    out = {k: v for k, v in rec.items() if k not in ("neighbours", "links", "history", "places", "depth_planes", "nav_map", "labels")}
    out["neighbours"] = len(rec["neighbours"])
    out["links"] = [(l["pano_id"], l["yaw_deg"]) for l in rec["links"]]
    out["history"] = [(h["year"], h["month"], h["pano_id"]) for h in rec["history"]]
    out["places"] = [(p["name"], p["type"], p["marker"].get("distance_m")) for p in rec["places"]]
    dp = rec["depth_planes"]
    out["depth_planes"] = None if dp is None else dict(planes=len(dp["planes"]), size=list(dp["index"].shape))
    nv = rec["nav_map"]
    out["nav_map"] = None if nv is None else dict(kind=nv["kind"], photos=len(nv["photos"]), size=list(nv["map"].shape))
    lb = rec["labels"]
    out["labels"] = None if lb is None else {LABEL_NAMES.get(int(v), str(int(v))): round(float(c) / lb.size, 4)
                                             for v, c in zip(*np.unique(lb, return_counts=True))}
    return out


def fetch(pano_id: str, **kw) -> bytes:
    import requests
    r = requests.get(request_url(pano_id, **kw), headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.content


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pano_id")
    ap.add_argument("--labels", action="store_true", help="also ask for the label picture (section 18)")
    ap.add_argument("--nav-table", action="store_true", help="ask for the click-to-go map with its own photo table")
    ap.add_argument("--file", help="parse a saved raw answer (bytes as downloaded) instead of downloading")
    ap.add_argument("--out", help="folder for summary.json and the pictures as PNG")
    args = ap.parse_args(argv)
    if args.file:
        body = Path(args.file).read_bytes()
    else:
        sections = SIM_SECTIONS + (("labels",) if args.labels else ())
        body = fetch(args.pano_id, sections=sections, nav_kinds=(2,) if args.nav_table else NAV_KINDS)
    rec = parse(body)
    print(json.dumps(summary(rec), indent=1, default=str))
    if args.out:
        from PIL import Image
        out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
        (out / f"{args.pano_id}.summary.json").write_text(json.dumps(summary(rec), indent=1, default=str))
        if rec["labels"] is not None:
            Image.fromarray(rec["labels"].astype(np.uint8)).save(out / f"{args.pano_id}.labels.png")
        if rec["depth_planes"] is not None:
            d = depth_image(rec["depth_planes"])
            Image.fromarray(np.clip(d * 4, 0, 255).astype(np.uint8)).save(out / f"{args.pano_id}.depth_x4.png")
        if rec["nav_map"] is not None:
            Image.fromarray(rec["nav_map"]["map"].astype(np.uint8)).save(out / f"{args.pano_id}.nav.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
