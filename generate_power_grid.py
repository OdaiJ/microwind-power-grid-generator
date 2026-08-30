#!/usr/bin/env python3
"""
generate_power_grid.py
=======================

Generates a Microwind ``.MSK`` layout file containing a structured power
grid (rows + columns of metal, connected by vias at matching-voltage
intersections), from a single ``config.json`` file describing:

  * which of metal1..metal6 are used in the grid and how wide each used
    layer is,
  * column count / row count,
  * column-to-column and row-to-row spacing (pitch, i.e. center-to-center
    distance between adjacent rails on that axis),
  * a repeating voltage pattern (e.g. ["VDD","VSS"] or
    ["VDD","FLOAT","VSS","FLOAT"]) applied, by position, to both rows and
    columns (the same pattern for both axes),
  * a "hanging edge" length: how far each rail overhangs past the grid it
    would otherwise be flush with (rows overhang LEFT/RIGHT, columns
    overhang BOTTOM/TOP),
  * (optional) output path, SIMU value, and whether to add net-name labels.

Convention (per project spec): odd metals (1,3,5) are vertical COLUMNS,
even metals (2,4,6) are horizontal ROWS. Only intersections where the
row's assigned voltage and the column's assigned voltage are BOTH "VDD"
or BOTH "VSS" get a via stack; anything else (FLOAT, or a VDD/VSS
mismatch) is left unconnected.

--------------------------------------------------------------------------
FORMAT FACTS USED, AND THEIR CONFIDENCE LEVEL
--------------------------------------------------------------------------
This script is built strictly from what was verified in the companion
reference doc "microwind_msk_format_notes.md" (built by diffing real
example .MSK files from Microwind). Anything not directly confirmed is
marked HYPOTHESIS below and also flagged at runtime if actually used.

CONFIRMED:
  - File skeleton: VERSION / FIG #<path> / BB(...) / SIMU #<val> /
    ZOOM(...) / <body> / FFIG <path>
  - BB(x1,y1,x2,y2) = union bounding box of every shape in the file.
  - Shapes: REC(x, y, width, height, LAYER_CODE), (x,y) = lower-left corner.
  - Layer codes: ME=Metal1, M2=Metal2, M3=Metal3, M4=Metal4.
  - Via codes: VI=Metal1<->Metal2, V2=Metal2<->Metal3, V3=Metal3<->Metal4.
  - Minimum legal via structure: a 2x2 lambda via centered inside a 4x4
    lambda pad on each of the two connected metal layers (1-lambda
    enclosure margin on every side). Microwind's own via-drop tool stamps
    this pad+via unit fresh at the via location even when a larger metal
    shape already exists there (i.e. duplicate/overlapping REC lines for
    the same layer are normal, not an error).
  - Net naming: "TITLE x y  #<name>" followed by "$<code> 1000 0 ", where
    (x,y) is any point inside the target shape (point-in-shape hit test).
    $1 = VDD, $0 = VSS. No confirmed code exists for a "FLOAT" net, so
    FLOAT rails are intentionally left unlabeled.

HYPOTHESIS (unconfirmed - flagged at runtime if used):
  - Layer codes M5, M6 for Metal5/Metal6 (never observed; guessed from
    the M<n> pattern of M2-M4).
  - Via codes V4 (Metal4<->Metal5), V5 (Metal5<->Metal6) (guessed from
    the V<n> pattern of V2/V3).
  - The exact meaning of "SIMU #<value>" (kept at the same default,
    2.50, seen in every real user-authored example file).
  - Whether a via at a matching intersection should be a single fixed
    2x2 via (what this script does, matching Microwind's own observed
    minimum-legal via/pad unit) or an array of vias tiling a larger
    overlap for wide rails. This was an open question at the time this
    script was written and has NOT been resolved with the user -- if
    your rails are much wider than 4 lambda, you'll get one small via
    per intersection, not a via array. Revisit this if that's wrong.

Everything else (grid geometry: rail placement, spacing-as-pitch,
hanging edges, label placement on the hanging edges, labeling one point
per rail, coordinate normalization to avoid negative coordinates,
contiguous-layer-stack validation) is this script's own design choice
layered on top of the confirmed primitives above, not a Microwind
requirement -- called out inline where relevant.
"""

import argparse
import json
import os
import sys
from datetime import datetime


# ---------------------------------------------------------------------------
# Confirmed / hypothesis format tables
# ---------------------------------------------------------------------------

LAYER_CODE = {
    1: "ME",  # confirmed
    2: "M2",  # confirmed
    3: "M3",  # confirmed
    4: "M4",  # confirmed
    5: "M5",  # HYPOTHESIS
    6: "M6",  # HYPOTHESIS
}

VIA_CODE = {
    (1, 2): "VI",  # confirmed
    (2, 3): "V2",  # confirmed
    (3, 4): "V3",  # confirmed
    (4, 5): "V4",  # HYPOTHESIS
    (5, 6): "V5",  # HYPOTHESIS
}

HYPOTHESIS_LAYERS = {5, 6}
HYPOTHESIS_VIA_PAIRS = {(4, 5), (5, 6)}

VIA_SIZE = 2          # confirmed: minimum via edge length, lambda
VIA_PAD_SIZE = 4      # confirmed: minimum via reinforcement pad edge length, lambda
VIA_PAD_MARGIN = (VIA_PAD_SIZE - VIA_SIZE) // 2  # = 1 lambda enclosure, confirmed

NET_DOLLAR_CODE = {"VDD": "$1", "VSS": "$0"}  # confirmed
CONNECTABLE_VOLTAGES = {"VDD", "VSS"}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def warn(msg):
    print(f"WARNING: {msg}", file=sys.stderr)


def to_lambda_int(value, name):
    """Coerce a numeric input to an integer lambda value, warning if it
    wasn't already a whole number (no confirmed example ever showed a
    fractional lambda coordinate, so we don't know if Microwind accepts
    one -- safest is to round and tell the user).
    """
    fval = float(value)
    ival = round(fval)
    if abs(fval - ival) > 1e-9:
        warn(f"{name} = {value} is not a whole number of lambda; "
             f"rounding to {ival} (fractional lambda has never been "
             f"observed in a confirmed MSK example).")
    return ival


def format_version_timestamp(dt):
    """Reproduce the exact observed VERSION timestamp style:
    'M/D/YYYY H:MM:SS AM/PM' (no leading zero on month/day/hour,
    zero-padded minute/second)."""
    hour12 = dt.hour % 12
    if hour12 == 0:
        hour12 = 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.month}/{dt.day}/{dt.year} {hour12}:{dt.minute:02d}:{dt.second:02d} {ampm}"


# ---------------------------------------------------------------------------
# config.json loading / validation
# ---------------------------------------------------------------------------
#
# Everything the generator needs lives in one JSON file (default name
# "config.json"). Shape:
#
# {
#   "layers": {
#     "metal1": {"used": true,  "width": 4},
#     "metal2": {"used": true,  "width": 4},
#     "metal3": {"used": true,  "width": 4},
#     "metal4": {"used": true,  "width": 6},
#     "metal5": {"used": false, "width": 4},
#     "metal6": {"used": false, "width": 4}
#   },
#   "columns": 4,
#   "rows": 3,
#   "column_spacing": 20,
#   "row_spacing": 20,
#   "voltage_pattern": ["VDD", "VSS"],
#   "hanging_edge_length": 3,
#   "output": "power_grid.MSK",
#   "sim_value": 2.50,
#   "label_nets": true
# }
#
# "layers", "columns", "rows", "column_spacing", "row_spacing",
# "voltage_pattern", and "hanging_edge_length" are required.
# "output", "sim_value", and "label_nets" are optional and fall back to
# the defaults below if omitted.

REQUIRED_CONFIG_KEYS = [
    "layers", "columns", "rows", "column_spacing", "row_spacing",
    "voltage_pattern", "hanging_edge_length",
]

OPTIONAL_CONFIG_DEFAULTS = {
    "output": "power_grid.MSK",
    "sim_value": 2.50,
    "label_nets": True,
}

EXAMPLE_CONFIG = {
    "layers": {
        "metal1": {"used": True, "width": 4},
        "metal2": {"used": True, "width": 4},
        "metal3": {"used": True, "width": 4},
        "metal4": {"used": True, "width": 6},
        "metal5": {"used": False, "width": 4},
        "metal6": {"used": False, "width": 4},
    },
    "columns": 4,
    "rows": 3,
    "column_spacing": 20,
    "row_spacing": 20,
    "voltage_pattern": ["VDD", "VSS"],
    "hanging_edge_length": 3,
    "output": "power_grid.MSK",
    "sim_value": 2.50,
    "label_nets": True,
}


def _validate_layers(raw_layers):
    """Same validation as before, just now operating on the 'layers'
    sub-object of config.json instead of a standalone file.

    Expected shape:
        {
          "metal1": {"used": true,  "width": 4},
          ...
          "metal6": {"used": false, "width": 4}
        }
    """
    layers = {}
    for n in range(1, 7):
        key = f"metal{n}"
        if key not in raw_layers:
            raise ValueError(f"config.json 'layers' is missing required key '{key}'.")
        entry = raw_layers[key]
        if "used" not in entry or "width" not in entry:
            raise ValueError(f"'layers.{key}' must have both 'used' and 'width' keys.")
        used = bool(entry["used"])
        width = to_lambda_int(entry["width"], f"layers.{key}.width") if used else None
        if used and width <= 0:
            raise ValueError(f"'layers.{key}'.width must be positive, got {entry['width']}.")
        layers[n] = {"used": used, "width": width}
    return layers


def _parse_voltage_pattern(raw):
    """Accepts either a JSON array (preferred, e.g. ["VDD","VSS"]) or a
    comma-separated string (e.g. "VDD,VSS"), for convenience when hand
    editing config.json."""
    if isinstance(raw, str):
        pattern = [tok.strip().upper() for tok in raw.split(",") if tok.strip() != ""]
    else:
        pattern = [str(tok).strip().upper() for tok in raw if str(tok).strip() != ""]
    if not pattern:
        raise ValueError("'voltage_pattern' must contain at least one entry.")
    return pattern


def validate_layer_stack(layers):
    """Returns (column_layers, row_layers, full_chain) as sorted lists of
    layer numbers, after checking the enabled set is contiguous (required
    because we can only bridge ADJACENT metal numbers with a confirmed
    via code -- e.g. metal1+metal3 enabled with metal2 disabled can't be
    connected with anything in our confirmed via table)."""
    enabled = sorted(n for n, cfg in layers.items() if cfg["used"])

    if enabled:
        span = enabled[-1] - enabled[0] + 1
        if span != len(enabled):
            raise ValueError(
                f"Enabled metal layers {enabled} are not contiguous. "
                f"Only adjacent metal numbers have a confirmed via code "
                f"(VI/V2/V3/...), so a gap (e.g. metal1+metal3 without "
                f"metal2) can't be bridged. Enable the missing layer(s) "
                f"in between, or remove the higher/lower one."
            )

    for n in enabled:
        if n in HYPOTHESIS_LAYERS:
            warn(f"metal{n} uses layer code '{LAYER_CODE[n]}', which is an "
                 f"UNCONFIRMED hypothesis (never observed in a real MSK "
                 f"file). Verify the generated file opens correctly in "
                 f"Microwind before trusting it.")

    for a, b in zip(enabled, enabled[1:]):
        if (a, b) in HYPOTHESIS_VIA_PAIRS:
            warn(f"Via between metal{a} and metal{b} uses code "
                 f"'{VIA_CODE[(a, b)]}', which is an UNCONFIRMED "
                 f"hypothesis. Verify in Microwind before trusting it.")

    column_layers = [n for n in enabled if n % 2 == 1]  # odd = vertical
    row_layers = [n for n in enabled if n % 2 == 0]      # even = horizontal
    return column_layers, row_layers, enabled


def load_config(config_path):
    """Load, validate, and normalize config.json into a plain dict with
    all values coerced to their final types (layers dict keyed by int,
    voltage_pattern as a list, spacing/columns/rows/hanging_edge_length
    as ints, etc.)."""
    with open(config_path, "r") as f:
        raw = json.load(f)

    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in raw]
    if missing:
        raise ValueError(
            "config.json is missing required key(s): " + ", ".join(missing)
        )

    cfg = dict(raw)
    for key, default in OPTIONAL_CONFIG_DEFAULTS.items():
        cfg.setdefault(key, default)

    cfg["layers"] = _validate_layers(cfg["layers"])
    cfg["voltage_pattern"] = _parse_voltage_pattern(cfg["voltage_pattern"])

    if int(cfg["columns"]) < 0 or int(cfg["rows"]) < 0:
        raise ValueError("'columns' and 'rows' must be >= 0.")
    cfg["columns"] = int(cfg["columns"])
    cfg["rows"] = int(cfg["rows"])

    if float(cfg["column_spacing"]) <= 0 or float(cfg["row_spacing"]) <= 0:
        raise ValueError("'column_spacing' and 'row_spacing' must be > 0.")
    cfg["column_spacing"] = to_lambda_int(cfg["column_spacing"], "column_spacing")
    cfg["row_spacing"] = to_lambda_int(cfg["row_spacing"], "row_spacing")

    if float(cfg["hanging_edge_length"]) < 0:
        raise ValueError("'hanging_edge_length' must be >= 0.")
    cfg["hanging_edge_length"] = to_lambda_int(
        cfg["hanging_edge_length"], "hanging_edge_length")

    cfg["sim_value"] = float(cfg["sim_value"])
    cfg["label_nets"] = bool(cfg["label_nets"])
    cfg["output"] = str(cfg["output"])

    return cfg


# ---------------------------------------------------------------------------
# Geometry generation
# ---------------------------------------------------------------------------

class MskBuilder:
    """Accumulates REC / TITLE lines and tracks the running union bounding
    box (confirmed BB semantics), then normalizes coordinates so nothing
    is negative (a design choice for output cleanliness -- negative
    coordinates were never tested, so this sidesteps the question)."""

    def __init__(self):
        self._recs = []     # list of (x, y, w, h, layer)
        self._titles = []   # list of (x, y, name, code)
        self._min_x = None
        self._min_y = None
        self._max_x = None
        self._max_y = None

    def add_rec(self, x, y, w, h, layer):
        self._recs.append((x, y, w, h, layer))
        self._track(x, y)
        self._track(x + w, y + h)

    def add_title(self, x, y, name, code):
        self._titles.append((x, y, name, code))
        self._track(x, y)

    def _track(self, x, y):
        self._min_x = x if self._min_x is None else min(self._min_x, x)
        self._min_y = y if self._min_y is None else min(self._min_y, y)
        self._max_x = x if self._max_x is None else max(self._max_x, x)
        self._max_y = y if self._max_y is None else max(self._max_y, y)

    def bounding_box(self):
        if self._min_x is None:
            return (0, 0, 0, 0)
        return (self._min_x, self._min_y, self._max_x, self._max_y)

    def normalize(self, margin=0):
        """Shift everything so the minimum x/y is exactly `margin`."""
        min_x, min_y, _, _ = self.bounding_box()
        if min_x is None:
            return
        shift_x = margin - min_x if min_x < margin else 0
        shift_y = margin - min_y if min_y < margin else 0
        if shift_x == 0 and shift_y == 0:
            return
        self._recs = [(x + shift_x, y + shift_y, w, h, layer)
                       for (x, y, w, h, layer) in self._recs]
        self._titles = [(x + shift_x, y + shift_y, name, code)
                         for (x, y, name, code) in self._titles]
        self._min_x += shift_x
        self._max_x += shift_x
        self._min_y += shift_y
        self._max_y += shift_y

    def body_lines(self):
        lines = []
        for (x, y, w, h, layer) in self._recs:
            lines.append(f"REC({x},{y},{w},{h},{layer})")
        for (x, y, name, code) in self._titles:
            lines.append(f"TITLE {x} {y}  #{name}")
            lines.append(f"{code} 1000 0 ")
        return lines


def build_power_grid(layers, columns, rows, column_spacing, row_spacing,
                      voltage_pattern, hanging_edge_length, label_nets=True):
    column_layers, row_layers, full_chain = validate_layer_stack(layers)

    if columns <= 0 and column_layers:
        warn("Column layers are enabled but 'columns' is 0; no column "
             "rails will be drawn.")
    if rows <= 0 and row_layers:
        warn("Row layers are enabled but 'rows' is 0; no row rails will "
             "be drawn.")

    b = MskBuilder()

    # --- rail center positions ---------------------------------------
    # Spacing is treated as PITCH: center-to-center distance between
    # adjacent rails on that axis. This is a design assumption, not
    # something confirmed from the MSK format itself -- flagged here in
    # case the user actually meant edge-to-edge gap.
    column_centers = [j * column_spacing for j in range(columns)] if column_layers else []
    row_centers = [i * row_spacing for i in range(rows)] if row_layers else []

    max_col_width = max((layers[n]["width"] for n in column_layers), default=0)
    max_row_width = max((layers[n]["width"] for n in row_layers), default=0)

    # Columns must span the full height covered by rows (including the
    # thickness of the outermost row rails); rows must span the full
    # width covered by columns (including the thickness of the outermost
    # column rails). If the opposite axis doesn't exist, fall back to a
    # single-rail-length span using the layer's own width.
    #
    # Per user spec: on top of that base span, each rail also gets a
    # "hanging edge" overhang of `hanging_edge_length` lambda at BOTH
    # ends -- columns overhang past the BOTTOM and TOP, rows overhang
    # past the LEFT and RIGHT.
    if row_centers:
        col_y_start = row_centers[0] - max_row_width / 2
        col_y_end = row_centers[-1] + max_row_width / 2
    else:
        col_y_start, col_y_end = 0, max_col_width
    col_y_start -= hanging_edge_length
    col_y_end += hanging_edge_length

    if column_centers:
        row_x_start = column_centers[0] - max_col_width / 2
        row_x_end = column_centers[-1] + max_col_width / 2
    else:
        row_x_start, row_x_end = 0, max_row_width
    row_x_start -= hanging_edge_length
    row_x_end += hanging_edge_length

    col_y_start = to_lambda_int(col_y_start, "computed column y-start")
    col_y_end = to_lambda_int(col_y_end, "computed column y-end")
    row_x_start = to_lambda_int(row_x_start, "computed row x-start")
    row_x_end = to_lambda_int(row_x_end, "computed row x-end")
    column_centers = [to_lambda_int(c, "column center") for c in column_centers]
    row_centers = [to_lambda_int(c, "row center") for c in row_centers]

    if column_spacing < VIA_PAD_SIZE and column_layers:
        warn(f"Column spacing ({column_spacing}) is smaller than the "
             f"minimum via reinforcement pad size ({VIA_PAD_SIZE}); "
             f"via pads at adjacent columns will overlap.")
    if row_spacing < VIA_PAD_SIZE and row_layers:
        warn(f"Row spacing ({row_spacing}) is smaller than the minimum "
             f"via reinforcement pad size ({VIA_PAD_SIZE}); via pads at "
             f"adjacent rows will overlap.")

    for n in column_layers:
        w = layers[n]["width"]
        if w < VIA_PAD_SIZE:
            warn(f"metal{n} width ({w}) is smaller than the minimum via "
                 f"reinforcement pad size ({VIA_PAD_SIZE}); via pads on "
                 f"this layer will extend past the rail's drawn edges.")
    for n in row_layers:
        w = layers[n]["width"]
        if w < VIA_PAD_SIZE:
            warn(f"metal{n} width ({w}) is smaller than the minimum via "
                 f"reinforcement pad size ({VIA_PAD_SIZE}); via pads on "
                 f"this layer will extend past the rail's drawn edges.")

    # Label points sit at the midpoint of each rail's hanging edge:
    # columns are labeled on their BOTTOM hanging edge, rows on their
    # LEFT hanging edge. Falls back to the rail's own start coordinate
    # when hanging_edge_length is 0 (still a valid in-shape point, just
    # right at the flush edge).
    col_label_y = col_y_start + hanging_edge_length // 2
    row_label_x = row_x_start + hanging_edge_length // 2

    # --- column rails ---------------------------------------------------
    column_voltage = {}
    for j, cx in enumerate(column_centers):
        voltage = voltage_pattern[j % len(voltage_pattern)]
        column_voltage[j] = voltage
        for n in column_layers:
            w = layers[n]["width"]
            b.add_rec(cx - w // 2, col_y_start, w, col_y_end - col_y_start, LAYER_CODE[n])
        if label_nets and voltage in NET_DOLLAR_CODE and column_layers:
            b.add_title(cx, col_label_y, voltage.title(), NET_DOLLAR_CODE[voltage])

    # --- row rails --------------------------------------------------------
    row_voltage = {}
    for i, ry in enumerate(row_centers):
        voltage = voltage_pattern[i % len(voltage_pattern)]
        row_voltage[i] = voltage
        for n in row_layers:
            w = layers[n]["width"]
            b.add_rec(row_x_start, ry - w // 2, row_x_end - row_x_start, w, LAYER_CODE[n])
        if label_nets and voltage in NET_DOLLAR_CODE and row_layers:
            b.add_title(row_label_x, ry, voltage.title(), NET_DOLLAR_CODE[voltage])

    # --- vias at matching intersections -----------------------------------
    via_count = 0
    if len(full_chain) >= 2 and column_centers and row_centers:
        for i, ry in enumerate(row_centers):
            rv = row_voltage[i]
            if rv not in CONNECTABLE_VOLTAGES:
                continue
            for j, cx in enumerate(column_centers):
                cv = column_voltage[j]
                if cv != rv:
                    continue
                # matching VDD-VDD or VSS-VSS intersection: connect the
                # FULL enabled layer stack here, one via per adjacent pair,
                # each with its own confirmed-minimum reinforcement pads.
                for a, bnum in zip(full_chain, full_chain[1:]):
                    via_layer = VIA_CODE[(a, bnum)]
                    b.add_rec(cx - VIA_SIZE // 2, ry - VIA_SIZE // 2,
                              VIA_SIZE, VIA_SIZE, via_layer)
                    b.add_rec(cx - VIA_PAD_SIZE // 2, ry - VIA_PAD_SIZE // 2,
                              VIA_PAD_SIZE, VIA_PAD_SIZE, LAYER_CODE[a])
                    b.add_rec(cx - VIA_PAD_SIZE // 2, ry - VIA_PAD_SIZE // 2,
                              VIA_PAD_SIZE, VIA_PAD_SIZE, LAYER_CODE[bnum])
                    via_count += 1

    b.normalize(margin=0)
    return b, via_count


# ---------------------------------------------------------------------------
# File writing
# ---------------------------------------------------------------------------

def write_msk_file(output_path, builder, sim_value):
    abspath = os.path.abspath(output_path)
    min_x, min_y, max_x, max_y = builder.bounding_box()
    timestamp = format_version_timestamp(datetime.now())

    # ZOOM: confirmed to be pure UI viewport state with no effect on
    # correctness, but a usability nicety -- fit it to the drawing (with
    # a margin) so the grid is actually visible when the file is opened,
    # rather than reusing the arbitrary fixed value seen in the examples.
    pad = max(10, int(0.1 * max(max_x - min_x, max_y - min_y, 1)))
    zoom = (min_x - pad, min_y - pad, max_x + pad, max_y + pad)

    lines = []
    lines.append(f"VERSION {timestamp}")
    lines.append(f"FIG #{abspath}")
    lines.append(f"BB({min_x},{min_y},{max_x},{max_y})")
    lines.append(f"SIMU #{sim_value:.2f}")
    lines.append(f"ZOOM({zoom[0]},{zoom[1]},{zoom[2]},{zoom[3]})")
    lines.extend(builder.body_lines())
    lines.append(f"FFIG {abspath}")

    with open(output_path, "w", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate a Microwind .MSK power grid layout from a "
                     "config.json file (all grid parameters live in the "
                     "config; see --write-example-config to get a starter "
                     "file).")
    parser.add_argument("--config", default="config.json",
                         help="Path to the config JSON file "
                              "(default: config.json).")
    parser.add_argument("--write-example-config", metavar="PATH",
                         help="Write an example config.json to PATH and exit.")
    args = parser.parse_args()

    if args.write_example_config:
        with open(args.write_example_config, "w") as f:
            json.dump(EXAMPLE_CONFIG, f, indent=2)
        print(f"Wrote example config to {args.write_example_config}")
        return

    cfg = load_config(args.config)

    builder, via_count = build_power_grid(
        layers=cfg["layers"],
        columns=cfg["columns"],
        rows=cfg["rows"],
        column_spacing=cfg["column_spacing"],
        row_spacing=cfg["row_spacing"],
        voltage_pattern=cfg["voltage_pattern"],
        hanging_edge_length=cfg["hanging_edge_length"],
        label_nets=cfg["label_nets"],
    )

    write_msk_file(cfg["output"], builder, cfg["sim_value"])

    min_x, min_y, max_x, max_y = builder.bounding_box()
    print(f"Wrote {cfg['output']}")
    print(f"  Bounding box: ({min_x},{min_y}) to ({max_x},{max_y})")
    print(f"  Via stacks placed at matching intersections: {via_count}")


if __name__ == "__main__":
    main()
