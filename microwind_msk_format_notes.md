# Microwind `.MSK` File Format — Working Notes

**Context:** These notes were built up conversationally with a user working in **Microwind**, a CMOS VLSI layout editor and simulator (by Etienne Sicard, paired with the schematic/logic tool **DSCH**), by having the user export small, deliberately minimal `.MSK` files after each incremental edit and comparing the diffs. Every claim below is labeled by how it was established. Please preserve that labeling if you extend this document — don't upgrade a hypothesis to "confirmed" without new evidence.

**Confidence key:**
- ✅ **Confirmed (external doc)** — stated in Microwind documentation/manuals found via web search.
- 🔬 **Confirmed (observed)** — directly derived from diffing real example `.MSK` files in this conversation; strong confidence.
- ❓ **Hypothesis** — plausible inference, not yet tested. Treat with skepticism until verified.

---

## 1. General background

- ✅ The `.MSK` extension is heavily overloaded across unrelated industries (PaintShop Pro image masks, a Fallout 1/2 worldmap terrain bitmask, the Manuskript writing tool's ZIP project container, sea-ice/meteorological masks, embroidery pattern masks, etc.). **None of that is relevant here** — this document is scoped strictly to the Microwind CMOS-layout meaning of `.MSK`.
- ✅ In Microwind, `.MSK` is the native **layout file** format — described in Microwind's own manuals as "simple text files containing the list of boxes and layers, and the list of text declarations." This matches everything observed directly.
- ✅ Coordinates are in **lambda (λ) units** — a technology-relative unit equal to **half the minimum polysilicon gate length** of the currently loaded design-rule (`.RUL`) file. Example correspondences from the official design-rules table: 0.6µm process → λ=0.6µm... down to a 45nm process → λ=0.02µm. This means the same numeric MSK coordinates represent different physical sizes depending on which `.RUL` file is active — worth asking the user which technology they're targeting before assuming a physical scale.
- ✅ Microwind can convert an MSK layout to CIF (`File → Make CIF File`) for export to other VLSI CAD tools, and can generate a SPICE-compatible netlist (`File → Make SPICE File`, producing a `.CIR` file).
- ✅ `File → Insert Layout` merges another `.MSK` file into the current layout, positioned at the lower-right of the existing content, without renaming the current file.

---

## 2. File header/footer structure

🔬 Every example file (5 total, ranging from an empty layout to a 4-layer power-strip-with-vias layout) followed exactly this skeleton:

```
VERSION <timestamp>
FIG #<absolute path to this file>
BB(x1,y1,x2,y2)
SIMU #<value>
ZOOM(x1,y1,x2,y2)
...body: REC / TITLE / etc. lines...
FFIG <absolute path to this file>
```

| Field | Status | Notes |
|---|---|---|
| `VERSION <timestamp>` | 🔬 | Last-saved timestamp, format `M/D/YYYY H:MM:SS AM/PM`. |
| `FIG #<path>` | 🔬 | Self-referencing absolute file path, **with** a leading `#`. Appears once, right after `VERSION`. |
| `BB(x1,y1,x2,y2)` | 🔬 | **The union bounding box of every shape currently in the file**, recomputed on save. Verified three separate ways: (1) matched a single rectangle's extents exactly; (2) matched the union extents of two rectangles on different layers with no overlap; (3) matched the union extents of a via-stack where two shapes shared a footprint and a third was fully contained inside — in all cases `BB` = min/max of all shape corners, not something manually set. |
| `SIMU #<value>` | ❓ | Seen as `#2.50` in every user-authored file, and `#5.00` in an external reference file (`inverter.MSK`/`nand.MSK` from a public GitHub repo). Purpose **unconfirmed** — did not correlate with layout size, layer count, or anything else observed. Possibly a simulation timestep or an unrelated per-session default; do not guess further without testing. |
| `ZOOM(x1,y1,x2,y2)` | 🔬 | The layout editor's last on-screen viewport. Stayed **identical** (`-25,-20,55,55`) across every user file regardless of what was drawn or where — confirms this is pure UI/session state, unlike `BB`, and is *not* recomputed from geometry. |
| `FFIG <path>` | ❓ | Same path as the `FIG` line, but **without** the `#` prefix, appearing as the very last line. Purpose unconfirmed. Speculative: a closing/footer marker mirroring `FIG`, or possibly relevant to hierarchical sub-cell references (unverified — no example so far has contained a sub-cell/hierarchy reference to test this against). |

---

## 3. Shape primitive: `REC`

🔬 Confirmed syntax:

```
REC(x, y, width, height, LAYER_CODE)
```

- `(x, y)` = **lower-left corner** of the rectangle, in lambda units.
- `width, height` = extents in lambda units (so the shape spans `x`→`x+width`, `y`→`y+height`).
- Confirmed via multiple `BB` cross-checks: every rectangle's computed upper-right corner (`x+width`, `y+height`) matched what `BB` reported when that rectangle was the only shape, or was correctly unioned when several were present.
- No other shape primitive (polygon, circle, path, etc.) has been observed yet — only `REC`. Don't assume rectangles are the only primitive Microwind supports in general; it's just all that's appeared in the small test files so far.

### 3.1 Confirmed layer codes

| Code | Layer | Source |
|---|---|---|
| `NW` | N-Well | 🔬 external reference file (`nand.MSK`) |
| `DP` | P+ diffusion (PMOS source/drain) | 🔬 external reference file |
| `DN` | N+ diffusion (NMOS source/drain) | 🔬 external reference file |
| `PO` | Polysilicon (gate) | 🔬 external reference file |
| `CO` | Contact (poly/diffusion → metal1) | 🔬 external reference file |
| `ME` | **Metal1** | 🔬 user files + external reference file. Note the irregular naming — this is the *only* metal layer that doesn't follow the `M<n>` pattern. |
| `M2` | Metal2 | 🔬 user files |
| `M3` | Metal3 | 🔬 user files |
| `M4` | Metal4 | 🔬 user files |

❓ **Hypothesis, untested:** `M5`, `M6`, etc. likely exist for higher-metal-count technologies, following the same `M<n>` pattern. Not yet confirmed — would need a technology file with 5+ metal layers to test.

### 3.2 Confirmed via codes

| Code | Connects | Source |
|---|---|---|
| `VI` | Metal1 (`ME`) ↔ Metal2 (`M2`) | 🔬 dedicated via-stack test file |
| `V2` | Metal2 (`M2`) ↔ Metal3 (`M3`) | 🔬 4-metal power-strip test file |
| `V3` | Metal3 (`M3`) ↔ Metal4 (`M4`) | 🔬 same file |

- ❓ **Hypothesis:** the pattern for a via between metal-N and metal-(N+1) is `V(N)`, with `VI` as a legacy exception — likely originally meaning "VIA" literally (from back when Microwind only supported 2 metal layers) rather than "via level 1", and never renamed once multi-metal stacks were added. Under this theory, `V4` would exist for an M4↔M5 connection on a 5+ metal technology. **Not yet tested.**

### 3.3 Via/enclosure geometry (observed design rule)

🔬 The minimum legal via structure observed was:
```
REC(33,20,4,4,M2)   ← 4×4 metal pad
REC(33,20,4,4,ME)   ← 4×4 metal pad, same footprint
REC(34,21,2,2,VI)   ← 2×2 via, centered
```
The via is inset exactly **1 lambda** from the metal pad edge on all four sides (a 2λ×2λ via inside a 4λ×4λ pad). This is the classic lambda/Mead-Conway-style via enclosure rule: metal must extend ≥1λ beyond the via on every side. Both metal layers used the **identical footprint** rather than one being drawn larger — vias don't require offset pad shapes, just co-located same-size pads with the via layer between them.

🔬 **Via-drop tool behavior:** when a via is inserted at a point that already has a large existing metal shape underneath (e.g. dropping a via into the middle of a long 74×7 trunk strip), Microwind does **not** reuse the existing geometry. It stamps out its own fresh, independent 4×4 pad rectangles on the two adjacent metal layers, plus the 2×2 via — fully overlapping/duplicating whatever was already there. Confirmed order per via, from the 4-metal power-strip example (for a via between layer A and layer B): the file lists `REC(...,V<n>)`, then `REC(...,4,4,<layer A>)`, then `REC(...,4,4,<layer B>)` as three consecutive lines. Practical implication for parsing/generating files: **expect duplicate/overlapping same-layer rectangles wherever a via has been placed** — this is normal, not a data error. A connectivity parser should union same-layer rectangles rather than assume one `REC` per logical wire.

---

## 4. Net naming / power assignment: `TITLE`

🔬 Confirmed syntax:
```
TITLE x y  #<net_name>
$<code> 1000 0 
```

- `(x, y)`: a point that must land **inside** the target shape's geometry — Microwind does a point-in-shape hit test to determine which piece of geometry the label/net applies to. Confirmed with three different placements (dead-center of a shape, near its right edge, near its left edge) — all three correctly "connected" to their respective host shape per the user's description, despite very different relative (x,y) offsets within each shape.
- **No additional via or connecting geometry is required** to assign a net name — the `TITLE` + `$` pair alone does it. This is a purely logical/schematic annotation, not a physical connection.
- `#<net_name>`: human-readable label shown in the layout (e.g. `Vdd`, `Vss`).
- `$<code>`: net electrical role, on the line immediately following `TITLE`:

| Code | Meaning | Source |
|---|---|---|
| `$1` | VDD (power / logic high) | 🔬 user files |
| `$0` | VSS (ground) | 🔬 user files |
| `$c` | Clock | ✅ external reference file only — appeared with extra trailing parameters (`$c 1000 0 0.4750 0.5000 0.9750 1.0000`), likely period/rise/fall/duty-cycle related. **Not decoded.** |
| `$v` | Voltage probe / simulation monitor point | ✅ external reference file only |

- ❓ The trailing `1000 0` numbers after `$1`/`$0`/`$v` are unconfirmed — plausibly a default voltage (mV) and a delay/phase value, but this is a guess. Only the no-argument default case has been seen for `$1`/`$0`/`$v`; only `$c` has shown extra parameters, and those haven't been decoded either.

---

## 5. Confirmed example (annotated, minimal)

The smallest fully-understood file structure — a lone Metal1 rectangle:
```
VERSION 8/30/2026 7:05:52 AM
FIG #C:\Users\adeij\Desktop\ex.MSK
BB(20,8,61,37)              ← = union bbox of all shapes below (here just one)
SIMU #2.50                  ← unconfirmed meaning
ZOOM(-25,-20,55,55)         ← static UI viewport state, unrelated to content
REC(20,8,41,29,ME)          ← Metal1 rect, lower-left (20,8), 41×29 λ
FFIG C:\Users\adeij\Desktop\ex.MSK
```

---

## 6. In-progress task: parametric power grid generator

The user is working toward a script/generator that emits a full `.MSK` power grid from a small parameter set. Captured spec so far (this section is about the **user's design intent**, not the file format itself):

- **Inputs:**
  - Set of metals used (e.g. {M1/ME, M2, M3, M4}) — orientation is implied by parity: **odd metals are vertical columns, even metals are horizontal rows.**
  - Column count, row count.
  - Width **per metal layer** (one value per layer, applied uniformly whether that layer is acting as a row or column in a given grid).
  - Row-to-row spacing (pitch) and column-to-column spacing (pitch), independently.
  - A **voltage pattern**, a repeating sequence (e.g. `VDD, VSS, VDD, VSS, ...` or `VDD, FLOAT, VSS, FLOAT, ...`) — per the user's latest simplification, **the same pattern is applied to both rows and columns** (not independently).
  - Vias are placed at row/column intersections **only where both the row and column carry the same supply** (VDD-on-VDD or VSS-on-VSS) — this connection rule was proposed by Claude and has **not yet been explicitly confirmed by the user**. Flag this before relying on it.

- **Open question, not yet answered by the user:** when metal rail widths are large, should a via at a matching intersection be (a) a single via scaled up to fill the overlap, or (b) an array of minimum-size vias tiling the overlap (more realistic, since real processes cap maximum via size)? This was raised but not resolved.

- **Net naming for the grid:** use the confirmed `TITLE` + `$1`/`$0` mechanism (Section 4) to tag the outer VDD/VSS rails with net names, placing the `(x,y)` point anywhere inside the target rail's rectangle.

**Not yet started:** actually writing the generator code. Next step when resuming: nail down the via-sizing question above, then generate rail `REC` lines (rows/columns per the spacing+count+width params) and via `REC` lines at matching-voltage intersections, using the confirmed layer/via code tables in Sections 3.1–3.2.

---

## 7. Things NOT yet tested (don't assume)

- Non-rectangular shapes (polygons, arcs, etc.) in `REC`-equivalent form.
- MOS transistor macros (nMOS/pMOS) — these are placed via a dedicated tool in Microwind's UI and likely have their own keyword distinct from manually-stacked `REC` layers on `PO`/`DP`/`DN`; not observed in any example so far.
- Layer codes for wells beyond `NW` (e.g. a P-well `PW`?), higher metals (`M5`+), or higher vias (`V4`+).
- Comment syntax, if any exists.
- Whether `FFIG` relates to hierarchical/sub-cell instancing.
- Exact meaning of `SIMU #<value>` and the trailing numeric parameters on `$`-lines.
