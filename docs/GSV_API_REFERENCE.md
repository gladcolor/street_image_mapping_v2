# Google Street View download reference (for SIM)

What we download from Google Street View, how each request is built, and
what every part of the answer means.

- Everything here was tested on 2026-09-29.
- The evidence (tests, counts, figures) is summarised in
  [GSV_FINDINGS_20260929.md](GSV_FINDINGS_20260929.md), called "the notes"
  below.
- These requests are **undocumented** by Google and can change at any time.
  Re-run `python -m pytest tests -q`, and one live request, before relying
  on them after a long break.

Code:

- `gsv_pano/photometa.py` (new, optional): request builders and the full
  answer parser (import as `g`).
- `gsv_pano/gsv_pb.py` (new, optional): reads and writes the `pb=` text
  (import as `pb`).
- SIM's own code, `gsv_pano/pano.py` (`GSV_pano`) and `gsv_pano/utils.py`
  (`refactorJson`, `getLinks`, `getTimeMachine`), is unchanged apart from the
  bug fixes in section 10.
- Tests: `tests/test_photometa.py` and `tests/test_fixes_20260929.py`; run
  them with `python -m pytest tests -q`.

## 1. Quick start

```python
import sys; sys.path.insert(0, "gsv_pano")
import requests, photometa as g
H = g.HEADERS          # SIM's own name tag; Python's default names are refused (section 7)

rec = g.parse(requests.get(g.request_url("kptnDLilHehf76nzhq3m-w"), headers=H).content)
rec["pose"], rec["address"], rec["history"], rec["depth_planes"]    # every field by name (section 5)

labels = g.parse(requests.get(g.labels_url("kptnDLilHehf76nzhq3m-w"), headers=H).content)["labels"]
# 256 x 512 array of Google's label numbers (section 6.3), or None

found = g.parse(requests.get(g.search_url(40.79966, -73.96792, 50), headers=H).content)
# the photo nearest to a point, as a full record
```

Command line:

- `python gsv_pano/photometa.py PANO_ID --labels --out DIR` prints a summary
  and saves the label, depth and click-to-go pictures as PNG.
- Add `--file SAVED_ANSWER` to parse a saved answer instead of downloading.

**Rules:**

- Never send a download tool's default name tag (`User-Agent`). SIM sends
  `Mozilla/5.0 (Street View; street_image_mapping_v2)` (`photometa.HEADERS`,
  `utils.open_url()`). Google refuses `python-requests`, `Python-urllib`,
  `curl` and similar names: HTTP 403 for pictures, HTTP 500 for the spot
  search and an 80-byte stripped record for photo info (2026-10-07). Any
  other tag works, even "Street View" alone.
- Space requests about 1-1.5 s apart.
- Build URLs with the functions, never by editing the text. A wrong piece
  count gives HTTP 400 (section 2).

## 2. The `pb=` text

Google flattens a nested record into one line of pieces
`!<field number><type><value>`.

| Type | Meaning |
|---|---|
| `m` | a group; the value is the number of **all** pieces inside it, counted down to the bottom |
| `s` | text (URL-encoded) |
| `e` | a choice number |
| `b` | yes/no (1/0) |
| `i`, `j`, `u` | whole numbers |
| `d`, `f` | decimal numbers |

The functions:

- `pb.loads(text)` turns the text into a list of `(field, type, value)`.
- `pb.dumps(tree)` writes it back and recounts every group. This round
  trip is exact for all 18 strings captured from the browser.
- `pb.pretty(tree)` prints the tree readably.

Below, "part 4.2.1" means field 1 inside field 2 inside field 4.

## 3. The addresses

| What | Address | Answer | Builder |
|---|---|---|---|
| **Photo info** | `https://www.google.com/maps/photometa/v1?authuser=0&hl=en&pb=...` | full record, ~360 KB text (13-19 KB as sent) | `g.request_url(pano_id, ...)`; SIM: `GSV_pano.getJsonfrmPanoID()` |
| **Labels only** | same address, part 18 only | ~4 KB | `g.labels_url(pano_id)` |
| **Spot search** (Google's page) | `https://www.google.com/maps/photometa/si/v1?authuser=0&hl=en&gl=us&pb=...` | `[[], record, [bearing]]` | `g.search_url(lat, lon, radius_m, ...)` |
| ~~Spot search (old SIM)~~ | `https://maps.googleapis.com/maps/api/js/GeoPhotoService.SingleImageSearch?pb=...&callback=_xdc_._x` | **turned off by Google on 2026-10-05** ("decommissioned and turned down"); `GSV_pano.getPanoIDfrmLonlat()` now uses the spot search above (section 11) | |
| **Panorama tile** | `https://streetviewpixels-pa.googleapis.com/v1/tile?cb_client=maps_sv.tactile&panoid=ID&x=X&y=Y&zoom=Z&nbt=1&fover=2` | 512 x 512 JPEG | |
| **Flat view** | `https://streetviewpixels-pa.googleapis.com/v1/thumbnail?cb_client=maps_sv.tactile&panoid=ID&w=W&h=H&yaw=YAW&pitch=P&thumbfov=FOV` | JPEG, up to 1024 x 768 | |
| Old image forms (SIM) | `https://geo{0-3}.ggpht.com/cbk?cb_client=maps_sv.tactile&output=tile` / `&output=thumbnail` | same pictures (identical bytes) | `download_panorama()`, `getImagefrmAngle()` |

`g.parse()` reads all three record shapes: photo info, the page's spot
search and SIM's old JSONP spot search (saved answers only; it is turned off).

Google Maps itself also downloads several things we don't need:

- road-map tiles (`maps/vt`, layer `m`);
- the Street View coverage lines (layer `svv`, in Google's deliberately
  scrambled format: **not decoded on purpose**);
- places in view (`preview/lp`);
- special collections (`preview/pegman`);
- the nearby-photo strip (`batchexecute rpcids=hspqX`).

The notes list them.

## 4. Request settings

### 4.1 Photo info (`request_url`)

| Part | Default (SIM) | Meaning | Builder argument |
|---|---|---|---|
| URL `hl=` | `en` | **Language of the address and street names** (`fr` gave "31 Av. des Champs-Élysées") | `hl` |
| 1.1 | `maps_sv.tactile` | client name (`apiv3` also works; unknown names get an empty answer) | |
| 1.11.2.1 | yes | nearby places on/off | `places_flag` |
| 2.1 / 2.2 | `en` / `us` (SIM: `zh-CN` / `us`) | language and region; only change **which places** are listed | `language`, `region` |
| 3.1.1 | 2 | **photo type: 2 = Google's own, 10 = photo sphere uploaded by the public** (ids `CIHM0og...`, `CIABIh...`); must match the id | `photo_type="google"/"public"` |
| 3.1.2 | | the photo id | `pano_id` |
| 4.1 (repeated) | 1, 2, 3, 4, 5, 6, 8, 12 | which blocks to send (table below) | `sections` |
| 4.2.1 | 1 | how much of the surroundings: 1 = all; 0 or 3 = only the 2 road-linked neighbours + depth map; 2 = none | `surroundings="full"/"links_only"/"none"` |
| 4.4.1 | 48 | pixel size of the uploader's picture link (`s48`) | `avatar_px` |
| 4.5 (repeated) | 1, 2 | depth map: kind 2 = plane depth map; kind 1 adds nothing | `depth_kinds` |
| 4.6 (repeated) | 1, 2 | click-to-go map: kind 1 = numbers into the neighbour list; kind 2 = own photo table, **sent only when asked alone** | `nav_kinds` |
| 4.9 | 9 photo-type filters | **no visible effect** (even beside public photos) | `type_filter` |

The blocks chosen by part 4.1:

| 4.1 value | Name in `g.SECTIONS` | Sends |
|---|---|---|
| 1 | `image` | `m[2]` image and tile sizes |
| 2 | `capture` | `m[3]` camera address, `m[6]` status, source and date |
| 3 | `attribution` | `m[4]` copyright and uploader |
| 4 | `unknown_4` | spot search: `m5[1]`, the found photo's position (2026-10-07); photo info: nothing visible |
| 5 | `places` | `m5[9]` nearby places |
| 6 | `surroundings` | `m5[3]` neighbours, `m5[5]` depth + click-to-go maps, `m5[6]` road links, `m5[8]` history. **Without it the record is 2.6 KB.** |
| 8 | `street_names` | `m5[12]` street names and road directions |
| 12 | `flag_12` | `m[12]`, a 2-byte flag |
| **18** | **`labels`** | **`m[20]` Google's label picture (section 6.3), +3.3 KB as sent. Not sent by SIM or Google's page.** |
| 7, 9-11, 13-17, 19-40 | | nothing (at most empty lists) |

Google's page sends the same request plus 4.1 = 17 and part 4.11.3.4 = yes.
Neither adds anything.

### 4.2 Spot search (`search_url`)

| Part | Google's page sends | Meaning | Builder argument |
|---|---|---|---|
| 2.1.3 / 2.1.4 | lat / lon | point to search from | `lat`, `lon` |
| 2.2 | 50 | **search radius in metres, a hard limit** (nothing found = 13-byte answer) | `radius_m` |
| 3.1.1.1 | 2 | photo type: no visible effect | |
| 3.2 | `en` / `us` | language, region | `language`, `region` |
| 3.9.1 | 2 | changes which photo is picked (1 gave a different photo; 3 = 2); rule unknown | |
| 3.11 | (2, yes, 2), (3, yes, 2) | **photo types allowed in the answer**: 2 = Google's own; 3 or 10 also allow public photo spheres | `google_only=True` keeps only 2 |
| 4 | | same as photo info part 4; `("image", "unknown_4")` = the panorama ID and its position only, about 650 bytes | `sections` (`LOCATE_SECTIONS`), ... |

`search_found(body)` reads an answer: `(pano_id, lat, lon)`, None for the
13-byte "nothing within the radius" answer, and ValueError for anything else
(an error page, a shutdown message), so an outage is never taken for a place
without photos.

The number in `[[], record, [bearing]]` is the **compass direction from the
found photo toward the searched point**. It was within about 1 degree at
12-15 m, and unreliable under 1 m.

### 4.3 Tiles and flat views

| Setting | Meaning |
|---|---|
| `cb_client=maps_sv.tactile` | **required** (otherwise HTTP 403) |
| `zoom`, `x`, `y` | zoom z = `m[2]` level z. Newer camera: zoom 0 = 512 x 256 (top half of one tile, bottom half black) up to zoom 5 = 16,384 x 8,192 (32 x 16 tiles). Older camera: 13,312 px at the top level. |
| `nbt=1` | "no blank tile": outside the picture, HTTP 400 instead of a black filler tile |
| `fover=2` | no visible effect |
| `w`, `h` | size, capped at 1024 x 768 |
| `yaw` | **compass direction** of the view (0 = north) |
| `pitch` | up/down, **positive = looking down** |
| `thumbfov` | field of view in degrees (smaller = zoomed in) |

## 5. The answer, field by field

`m` = the photo record (`answer[1][0]` for photo info), `m5 = m[5][0]`.
`g.parse()` gives these names; `g.summary()` makes a printable version.

| Path | Parser name | Content |
|---|---|---|
| `m[0][0]` | `answer_kind` | 1 (SIM's request), 3 (Google's page) |
| `m[1]` | `frontend`, `pano_id` | [photo type, id] |
| `m[2]` | `image` | height, width, zoom levels, tile size |
| `m[3][2]` | `address` | camera address lines, e.g. "228 W 104th St", "New York" |
| `m[4]` | `copyright`, `uploader` | "© 2026 Google"; uploader name, link, picture |
| `m[6][0:3]` | `status_code` | (3,4,1) normal; (3,2,1) no history list; (1,1,1) older photo that no history list links to (only reachable through neighbour lists) |
| `m[6][5]` | `source`, `source_code` | "launch", [6] for every NYC Google photo |
| `m[6][7]` | `capture` | year, month |
| `m[7][0]` | `report_link` | "report a problem" link |
| `m[19]` | `photo_reference` | GEO_PHOTO_REFERENCE, "IMAGE_ALLEYCAT\|id" |
| `m[20][0]` | `labels` | label picture (section 6.3) |
| `m5[1]` | `pose` | lat, lon, EGM96 height, ellipsoid height, heading, **pitch = value - 90**, **roll** |
| `m5[1][4]` | `country` | "US" |
| `m5[3][0]` | `neighbours` | 45-133 nearby photos with full pose; [0] = this photo; `frontend` 2 = Google, 10 = public; `level` = building-level id, floor number and name for indoor or multi-level photos (320 of about 44,000 neighbour entries in 600 NYC records) |
| `m5[5]` | `depth_planes`, `nav_map` | [[2], depth block, [kind], click-to-go block] |
| `m5[6]` | `links` | road links: neighbour number, direction |
| `m5[8]` | `history` | other dates: neighbour number, year, month, and **day** for very few dates (17 in 600 records) |
| `m5[9]` | `places` | name, type, icon; `feature_id` = Google Maps' own place id (`0x...:0x...`); `cid` = its public number (same as the second part of the feature id); sometimes a marker (x share, y share, distance m); `token` = an 11-character code of unknown meaning |
| `m5[12]` | `street_names` | name, road directions (2 or more at crossings) and the road's Google Maps `feature_id` |

Not read on purpose, because they were the same in all 2,000 stored NYC
answers:

- `m[2][0:2]` = 2, 2;
- `m[2][9]` = the photo's own id again;
- `m5[0][0]` = 1;
- `m5[8][k][5]` = 2.

Nothing else in the answers is left unread.

**`refactorJson()` names the two tilt numbers `Projection.tilt_yaw_deg`
and `Projection.tilt_pitch_deg`, but they are pitch + 90 and roll.**

- The same holds for `Time_machine[].yaw_deg` and `pitch_deg`.
- A road-slope test gave r = +0.74 for pitch.
- SIM's rotation code uses them correctly; only the names mislead. They are
  kept for compatibility and are now explained by comments.

### 5.1 How parsing works, step by step

Google's answer is not a normal named JSON object. It is **nested lists
with no field names**. The meaning of each position is what this reference
and the notes worked out. `g.parse(body)` does these steps:

1. **Keep every byte.** Decode the answer with
   `body.decode("utf-8", "surrogateescape")`. The label picture's bytes
   travel as characters and would be damaged by a normal decode.
2. **Remove the wrapper.** Drop the guard line `)]}'` (and, for SIM's
   search, the `_xdc_._x( ... )` JSONP wrapper). Keep the text from the
   first `[` to the last `]`.
3. **Read the lists** with `json.loads`.
4. **Find the photo record** `m`. It is `answer[1][0]` for photo info,
   `answer[1]` for the page's spot search, and the second item for SIM's
   search. `load_answer()` recognises it by its `[type, id]` second entry.
5. **Read each field by position** (section 5 table), for example
   `m[5][0][1][2][0]` = heading. `_dig()` returns None instead of failing
   when a part is missing (sections not asked for, older photos).
6. **Decode the pictures:**
   - base64 (URL-safe) to bytes, zlib-unpacked when packed;
   - then the 8-byte header;
   - then numpy arrays (depth map, click-to-go map), or a WebP file
     opened with Pillow (labels).
7. **Turn the numbers into names:**
   - pitch = value - 90;
   - feature ids written in hex;
   - label numbers named by `g.LABEL_NAMES`;
   - history and link entries turned from neighbour numbers into photo
     ids.

```python
rec = g.parse(body)                 # everything, by name (numpy arrays for the pictures)
print(g.summary(rec))               # printable overview
depth_m = g.depth_image(rec["depth_planes"])       # metres per pixel
label_at = rec["labels"][row, col]                  # Google's label number at a pixel (256 x 512)
```

## 6. The three hidden pictures

### 6.1 Depth map (`m5[5]`, kind 2)

- The string is base64 (URL-safe), sometimes also zlib-packed.
- Inside: an 8-byte header `<BHHHB` = (header size, number of planes n,
  width, height, offset), then a width x height `uint8` plane number per
  pixel, then n x 4 `float32` (normal x, y, z, distance).
- `g.depth_image()` gives metres along each pixel's ray (SIM's pixel
  convention); 0 means sky.
- Flat walls and ground only, **no trees**.
- The ground of older-camera photos is often a made-up level plane 2.5 m
  below the camera (`DEPTHMAP_GROUND_20260929.md`).

### 6.2 Click-to-go map (`m5[5]`, part 4.6): not depth

For each pixel, it names the neighbouring photo Street View jumps to when
you click there.

- **Kind 1** (what SIM downloads): 256 x 512 `uint8`. The value is a
  position in the neighbour list; 0 = this photo.
- **Kind 2**: a header, the map, then a table of 22-character photo ids,
  each followed by 2 `float32` numbers. These are the photo's offset in
  metres, turned to the car's heading, with no height. Map value v means
  `photos[v - 1]`.

For trees: at a trunk base it points to a photo **closer to the tree than
our camera in 95 %** of cases (median 6.1 m vs 9.5 m). That makes it a good
close-up picker, but it gives no position.

### 6.3 Google's label picture (`m[20][0]`, part 18)

- **Format:** a WebP file whose bytes arrive as characters inside the JSON.
  Read the answer body with `decode("utf-8", "surrogateescape")`, take the
  string, `encode("utf-8", "surrogateescape")` it, and open the bytes as a
  WebP image.
- **Size:** 512 x 256 for the whole 360 x 180 degree view, **0.70 degrees
  per pixel** (12 cm at 10 m). A 30 cm trunk at 10 m is about 2.4 label
  pixels wide.
- **Which photos have it:** mostly the photo Google currently shows at a
  spot, taken 2022 or later.
  - **85 %** of current NYC photos from 2022 on (434 of 508);
  - **27 %** from 2019-2021 (22 of 82);
  - older history photos rarely.
- **Accuracy** (blind check of 120 points, notes 5.5):
  - **86 %** agreement, 90 % when a point on an edge may count for either
    side;
  - the tree label is the main label at 17 of 24 published NYC trunks.
- **Numbering:** Google's own, **not ADE20K**, Cityscapes or Mapillary
  Vistas. The closest public list is Mapillary Vistas (about 34 of the 44
  have a match).

All 44 numbers (`g.LABEL_NAMES`; "?" = likely). The groups are
`g.LABEL_GROUPS`, used for cleaning tree records:

| # | Name | Group |
|---|---|---|
| 0 | ground next to the car (~24 % of every picture) | ground |
| 1 | sky | |
| 2 | building | |
| **3** | **tree / vegetation, canopy and trunk** | **tree** |
| 4 | road | ground |
| 5 | sand / dirt ground | ground |
| 6 | sidewalk | ground |
| 7 | crosswalk | ground |
| 8 | driveway | ground |
| 9 | grass / planting bed | ground |
| 10 | gravel / rocks (incl. rail track bed) | ground |
| 11 | curb / low ledge | ground |
| 12 | fence | look-alike |
| 13 | wall | look-alike |
| 14 | road barrier / guard rail | base hider |
| 15 | tunnel / covered area | |
| 16 | bridge / overpass | |
| 17 | bus shelter / kiosk | look-alike |
| 18 | call box / booth (fire-alarm box) | look-alike |
| 19 | traffic sign | look-alike |
| 20 | traffic light | look-alike |
| 21 | street lamp | look-alike |
| 22 | parking pay station | look-alike |
| 23 | mailbox / utility box | look-alike |
| 24 | fire hydrant | look-alike |
| 25 | pole | look-alike |
| 26 | banner / flag / sign board | look-alike |
| 27 | billboard | |
| 28 | other structure (shed, canopy, scaffolding, container, rock) | look-alike |
| 29 | temporary / construction object | look-alike, base hider |
| 30 | portable sign / delineator post | look-alike |
| 31 | traffic cone | base hider |
| 32 | car | base hider |
| 33 | bus | base hider |
| 34 | truck / van | base hider |
| 35 | train | base hider |
| 36 | motorcycle / scooter | base hider |
| 37 | bicycle | base hider |
| 38 | other vehicle (machine, trailer, cart) | base hider |
| 39 | person | look-alike, base hider |
| 40 | rider | look-alike, base hider |
| 41 | animal | base hider |
| 42 | water | |
| 43 | overhead wires? | |

## 7. Traps

| Trap | What happens | Do this |
|---|---|---|
| Hand-edited `pb` text | a wrong `!<n>m<k>` count gives HTTP 400 | build with the functions |
| Headless Chrome's own name ("HeadlessChrome") | empty records, blocked tiles | send another tag, e.g. `photometa.HEADERS` |
| A download tool's default name tag, even inside a longer tag: `python-requests`, `Python-urllib`, `curl`, `Wget`, `Go-http-client`, `Java`, `okhttp` | HTTP 403 for tiles and flat views, HTTP 500 for the spot search, an 80-byte stripped record for photo info. Tags such as "Street View", "Python", "my-python-script" or an empty tag work (tested 2026-10-07). | `photometa.HEADERS` / `utils.open_url()` |
| Photo info with an accepted tag | now and then still the 80-byte stripped record: the ID only, no position, date or depth (1 of about 10 on 2026-10-07; 0 of 30 later the same day) | ask again; `getJsonfrmPanoID()` tries 3 times |
| Public photo ids (`CIHM0og...`, `CIABIh...`) with photo type 2 | empty answer | use `photo_type="public"`. Tree work skips them anyway: low resolution, no depth map, no labels. |
| Spot search without a type limit | may return a public photo sphere | `search_url(..., google_only=True)` (the default) |
| Flat-view `pitch` | positive looks **down** | flip the sign when coming from the Maps JavaScript API |
| SIM's tilt names | `tilt_yaw_deg` is pitch + 90, `tilt_pitch_deg` is roll | use `rec["pose"]["pitch_deg"]` / `roll_deg` |
| Labels on older photos | usually missing (`labels is None`) | treat labels as an extra, never as a requirement |
| Label picture read as normal text | the WebP bytes get damaged | use `surrogateescape` (section 6.3) |
| Neighbour lists | hold about 1 % of photos (status (1,1,1), 2010-2013) that no link or history list reaches | `getLinks()` and `getTimeMachine()` do not list them; read `rec["neighbours"]` |

## 8. What exists but we don't read, use or get yet

**In the answers, meaning still unknown** (all parsed, so nothing is lost):

- `places[].token`: an 11-character code on most places.
- `answer_kind` (1 or 3) and the `m[12]` flag.
- Label 43: only "likely" overhead wires.

**Request settings with no visible effect or an unknown rule:**

- `fover` in tile URLs;
- part 4.9 (photo-type filters);
- 4.1 = 4;
- search part 3.9.1;
- `gl=` (not tested).

**We can get it now, but don't use it yet:**

| What | How to get it | Why not used yet |
|---|---|---|
| Google's label picture | `labels_url()` or part 18 | SIM does not request part 18 |
| ~1 % more older photos (2010-2013), found only in neighbour lists | the neighbour ids in `rec["neighbours"]` | not in `Links` or `Time_machine` |
| Photo spheres uploaded by the public | `photo_type="public"` | low resolution, no depth map, no labels |
| Place and road feature ids | `places[].feature_id`, `street_names[].feature_id` | could link an object to the nearest shop or road segment on Google Maps |
| Floor and level of indoor photos | `neighbours[].level` | street work is outdoors |
| Exact capture day | `history[].day` | only a handful of dates carry it |
| Close-up photo choice from the click-to-go map | `nav_map` | could pick the best close-up of an object (for trees: closer than the camera in 95 %) |
| Places, nearby-photo strip and special collections on the Maps side | notes | not needed |

**It exists, but we can't get it now:**

| What | Why not |
|---|---|
| Street View coverage lines as data (`svv` tiles) | Google's deliberately scrambled format; not decoded on purpose. The crawler reaches the same photos by following links. |
| Labels on most older photos | Google does not send them (27 % of 2019-2021 current photos, rarely older ones). |
| A sharper label picture | no request setting for it was found (512 x 256 only) |
| Depth for trees | Google's depth map has only flat walls and ground. |
| The exact capture day for most photos | Google gives year and month only. |
| Google's official metadata (Map Tiles API) and the 3D city mesh (Photorealistic 3D Tiles) | need an API key and billing. The metadata adds only a structured address; the 3D mesh is untested. |
| What model and data Google's labels come from | not published |

## 9. Ideas for SIM (not implemented)

1. **Ask for part 18 (labels) in `getJsonfrmPanoID()`** (+3.3 KB per answer).
   It gives free per-pixel labels for recent photos:
   - tree or pole checks for detections;
   - car-hidden bases;
   - where an object stands (sidewalk, grass, road).
2. **Drop the click-to-go map (part 4.6) from the request.** `compressJson()`
   deletes it anyway, and leaving it out saves 23 % of each download.
3. **Build the request with `photometa.request_url()`** instead of the pasted
   string. The output is identical (tested for SIM's `zh-CN` string), and
   later changes become one named switch.
4. **Offer the neighbour-list photos** that `Links` and `Time_machine` miss
   (~1 % more, mostly 2010-2013).

## 10. Bugs fixed in SIM on 2026-09-29

Each fix is marked in the code with `# Fix (2026-09-29)`. Each one has a test
in `tests/test_fixes_20260929.py` that fails on the old code and passes on
the fixed code.

| Where | Bug | Seen in real data |
|---|---|---|
| `utils.getTimeMachine()` | One history entry without elevation or heading (a public photo sphere) raised an error, and every later date was **dropped**. | 6 of 3,000 NYC answers; one lost a 2024 Google photo |
| `utils.getLinks()` | With no road links, the error handler logged a variable that did not exist yet. That raised again, and `refactorJson()` stopped early (no `Time_machine`, no depth map). | not in 3,000 NYC answers |
| `utils.refactorJson()` | A missing depth map raised an error and skipped the country, description and region. | not in 3,000 NYC answers |
| `utils.refactorJson()` | `elevation_wgs84_m` was always `""`, although the answer carries it at `m5[1][1][2]`. | every answer |
| `utils.compressJson()` | Without a click-to-go map, the `del` raised before the depth map was packed, so `utils.parse()` could not read the stored depth map later. | not in 3,000 NYC answers |
| `GSV_pano.getPanoIDfrmLonlat()` | A bare `except:` logged `e`, which was `math.e` (2.718..., from `from math import *`) instead of the real error. | any unreadable answer |
| `GSV_pano.get_image_from_headings()` | Passed `override=` to `getImagefrmAngle()`, which has no such parameter: a TypeError on every call. | every call |
| `GSV_pano.download_time_machine_jsons()`, `download_pano_json_links()` | `logging.error("...", e)` without `%s`, so the logger printed a "Logging error" instead of the message. | every logged error |

**Regression check.** On 3,000 real NYC answers, the fixed
`compressJson()` + `refactorJson()` output is identical to the old output,
except for two things:

- `elevation_wgs84_m`, which is now filled;
- the 6 `Time_machine` lists that get their lost dates back. The old
  entries are unchanged and the lost ones are added after them.

The stored depth maps are byte-for-byte the same.

## 11. Changes on 2026-10-07: the old spot search is gone

Google turned off `GeoPhotoService.SingleImageSearch` on 2026-10-05 (answer:
"... is decommissioned and turned down"). Every coordinate -> panorama lookup
in SIM failed. At the same time, SIM's downloads with Python's default name
tags failed too (section 7).

| Where | Change |
|---|---|
| `GSV_pano.getPanoIDfrmLonlat()` | Uses Google Maps' own spot search: `photometa.search_url(lat, lon, 50, sections=LOCATE_SECTIONS)`, read by `photometa.search_found()`. Same 50 m hard limit, Google's own photos only, same return value `(panoId, lon, lat)` or `(0, 0, 0)`. The answer is about 650 bytes instead of about 480 KB. |
| `GSV_pano.getJsonfrmPanoID()` | Sends `HEADERS`; asks up to 3 times while the answer is the 80-byte stripped record. |
| `GSV_pano.download_panorama()` (tiles), `getImagefrmAngle()`, `utils` flat views | Download with `utils.open_url()` (SIM's name tag, 60 s timeout). |
| `photometa.py` | New `LOCATE_SECTIONS`, `HEADERS` and `search_found()`. `HEADERS` = `{"User-Agent": "Mozilla/5.0 (Street View; street_image_mapping_v2)"}`: an honest name instead of a copied Chrome name, tested equal on the search, photo info, tiles and flat views. `BROWSER_HEADERS` is kept as its old name. |

Tests: `tests/test_coordinate_search_20261007.py`, offline from a real answer
(`tests/fixtures/search_newark_locate.txt`). The live checks run with
`SIM_LIVE=1 python -m pytest tests -q -k live`: coordinates -> panorama ID ->
full record -> flat view.
