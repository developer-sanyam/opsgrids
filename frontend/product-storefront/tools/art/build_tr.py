"""Emit one wide hero per trade theme, two supporting plates, and Atelier's wide hero."""
import os
import sys

sys.path.insert(0, "/tmp/art")
from kit import ell, f, lin, path, rad, rect, svg
from trade import (GROUND, SAFE_BOT, SAFE_TOP, bag, bolt, breaker, coil, ring, room, shadow,
                   slab)

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/art/tr"
os.makedirs(OUT, exist_ok=True)


def emit(name, label, parts):
    d, b = [], []
    for pd, pb in parts:
        d += pd
        b += pb
    open(os.path.join(OUT, name + ".svg"), "w").write(svg(label, d, b))


# ======================================================== foundry ============
# Steel on a dark bench, with the theme's safety red on exactly one thing.
STEEL = ("#D2D9DF", "#8E979F", "#3E464E")
emit("hero-foundry", "Bearings and fasteners on a workbench", [
    room("r", "#242A30", "#0D1114", "#9FB4C4", glow_at=(0.3, 0.3),
         floor_near="#1A1F24", floor_far="#0B0E11"),
    shadow("s1", 112, 208, 84, 19, 0.55),
    shadow("s2", 286, 212, 104, 16, 0.5),
    ring("g1", 110, 154, 58, *STEEL, "#11161A"),
    # the two heads are staggered left-to-right: stacked flush, the rear one vanished behind
    # the front one and the pair read as a single grey smear
    bolt("g2", 196, 160, 124, 26, *STEEL, "#B3261E"),
    bolt("g3", 176, 204, 112, 21, "#B9C2CA", "#79828A", "#343C44", "#98A1A9"),
    ([], [rect(0, GROUND - 3, 400, 3, "#B3261E", op="0.85")]),
])

# ========================================================== depot ============
# A despatch bay: two pallet stacks, blue banding, everything bright and square.
CARTON = ("#E4CFA8", "#C9AE7E", "#9A8050")
emit("hero-depot", "Palletised cartons in a despatch bay", [
    # Dark, for the same reason as Carton's: Depot's hero is an overlay under a 58% #0B57D0 scrim
    # with white type. A bright bay under that scrim leaves the copy at about 3:1.
    room("r", "#1B2A3E", "#0A1220", "#5E86C4", glow_at=(0.3, 0.28), glow_r=0.6,
         floor_near="#111C2A", floor_far="#0A1220"),
    shadow("s1", 128, 216, 92, 16, 0.55, "#000000"),
    shadow("s2", 282, 218, 84, 14, 0.5, "#000000"),
    slab("g1", 66, 118, 124, 68, 24, "#8A7448", "#5E4E2E", "#332913"),
    slab("g2", 66, 150, 124, 62, 24, "#7E6A40", "#544628", "#2C2410"),
    ([], [rect(66, 132, 124, 9, "#2E72E0"), rect(66, 164, 124, 9, "#2E72E0")]),
    slab("g3", 236, 136, 112, 58, 22, "#8A7448", "#5E4E2E", "#332913"),
    slab("g4", 236, 164, 112, 48, 22, "#7E6A40", "#544628", "#2C2410"),
    ([], [rect(236, 148, 112, 8, "#2E72E0"), rect(236, 176, 112, 8, "#2E72E0"),
          # the pallets both stacks sit on
          rect(58, 212, 146, 9, "#4E4028"), rect(58, 212, 146, 3, "#6B5836"),
          rect(228, 212, 132, 8, "#4E4028"), rect(228, 212, 132, 3, "#6B5836"),
          rect(72, 221, 16, 10, "#3A2F1C"), rect(168, 221, 16, 10, "#3A2F1C"),
          rect(242, 220, 14, 9, "#3A2F1C"), rect(330, 220, 14, 9, "#3A2F1C"),
          rect(0, GROUND - 2, 400, 2, "#2E72E0", op="0.8")]),
])

# ========================================================= quarry ============
# Cement in paper sacks, an aggregate heap, warm site sand.
emit("hero-quarry", "Cement sacks and aggregate at the site gate", [
    room("r", "#F0E7D5", "#D6C6A6", "#FFFFFF", glow_at=(0.3, 0.32),
         floor_near="#BFAF8E", floor_far="#D2C2A2"),
    shadow("s1", 130, 214, 104, 15, 0.26, "#4A3A20"),
    # the heap, behind everything
    ([], [path("M28 214 C64 150 118 138 170 214 Z", "#9A9086"),
          path("M28 214 C60 156 104 144 140 178 L118 214 Z", "#B4AA9E"),
          path("M46 214 C68 176 96 168 118 190 L104 214 Z", "#83786B")]),
    bag("g1", 244, 214, 158, 48, "#EFE3C6", "#D6C69F", "#A48F67", "#8A4B22", "#F4EBD6"),
    bag("g2", 236, 182, 146, 44, "#F2E7CE", "#DCCBA6", "#AB966E", "#8A4B22", "#F4EBD6"),
    bag("g3", 250, 152, 134, 42, "#F6EDD8", "#E2D2AE", "#B29D74", "#8A4B22", "#F4EBD6"),
    ([], [rect(0, GROUND - 2, 400, 2, "#8A4B22", op="0.5")]),
])

# ======================================================== circuit ============
# A panel builder's rail: breakers in a row, a coil of cable, lime on the live one.
emit("hero-circuit", "Switchgear on a panel rail", [
    room("r", "#141B21", "#06090C", "#4E6470", glow_at=(0.36, 0.34),
         floor_near="#0E1317", floor_far="#080B0E"),
    shadow("s1", 296, 212, 72, 15, 0.6),
    # the DIN rail the breakers clip to
    ([], [rect(38, 148, 214, 7, "#5E6A74"), rect(38, 148, 214, 2, "#98A6B0"),
          rect(38, 196, 214, 7, "#48535C")]),
    breaker("g1", 46, 116, 38, 84, "#E4E9ED", "#B4BEC6", "#77828B", "#2B3238", "#C7CFD6"),
    breaker("g2", 90, 116, 38, 84, "#E4E9ED", "#B4BEC6", "#77828B", "#2B3238", "#C7CFD6"),
    breaker("g3", 134, 116, 38, 84, "#E4E9ED", "#B4BEC6", "#77828B", "#C7F03A", "#C7CFD6"),
    breaker("g4", 178, 116, 38, 84, "#E4E9ED", "#B4BEC6", "#77828B", "#2B3238", "#C7CFD6"),
    coil("g5", 300, 168, 62, "#3E4A54", "#28323A", "#141B21", "#C87A35", turns=5),
    ([], [rect(0, GROUND - 2, 400, 2, "#C7F03A", op="0.7")]),
])

# ========================================================= carton ============
# Corrugation: three boxes, one open enough to show the flute, and a strapping reel.
KRAFT = ("#E0BE8A", "#C19758", "#8E6A34")
emit("hero-carton", "Corrugated cases on a despatch bench", [
    # Deliberately the dark member of the kraft family. This picture is only ever seen under
    # Carton's 58%-opacity accent scrim with white type over it; on the light kraft ground the
    # first version measured about 2.8:1, which is a fail. Lit low, it clears 8:1.
    room("r", "#3A2A18", "#150E07", "#C98A3A", glow_at=(0.3, 0.3), glow_r=0.58,
         floor_near="#241909", floor_far="#150E07"),
    shadow("s1", 138, 216, 100, 15, 0.55, "#000000"),
    shadow("s2", 292, 214, 74, 13, 0.5, "#000000"),
    slab("g1", 62, 120, 140, 92, 28, "#9A7038", "#754F22", "#3E2A0E"),
    ([], [path("M62 120 l28 -14 h140 l-28 14 Z", "#A87C42"),
          path("M76 113 l-22 -22 h56 l18 18 Z", "#8A6230"),
          path("M202 113 l24 -22 h-56 l-16 18 Z", "#6B4A1E"),
          rect(84, 128, 96, 8, "#2E1E08", op="0.45"),
          rect(84, 128, 96, 3, "#C4682A")]),
    slab("g2", 226, 154, 96, 58, 22, "#8A6230", "#66441C", "#33220B"),
    ([], [rect(226, 172, 96, 7, "#C4682A")]),
    coil("g3", 350, 178, 44, "#A8703A", "#7A4A1E", "#412510", "#C99A5E", turns=4),
    ([], [rect(0, GROUND - 2, 400, 2, "#C4682A", op="0.8")]),
])

# ======================================================== harvest ============
# Field edge: two input sacks and a coil of drip lateral, on turned soil.
emit("hero-harvest", "Fertiliser sacks and drip lateral at the field edge", [
    room("r", "#EDF6E8", "#C9E0C2", "#FFFFFF", glow_at=(0.32, 0.28),
         floor_near="#7E6B4A", floor_far="#A0906C"),
    shadow("s1", 146, 216, 96, 14, 0.3, "#2A2010"),
    shadow("s2", 300, 214, 66, 12, 0.28, "#2A2010"),
    # a hedge line, well behind, to put the sacks somewhere rather than nowhere
    ([], [path("M0 206 C46 176 96 182 140 204 C184 178 236 184 286 202 C324 182 366 186 400 204 "
               "V214 H0 Z", "#8FB487", "0.55")]),
    bag("g1", 140, 214, 172, 56, "#F6FAF2", "#DCE6D6", "#A2B49C", "#1F7A3D", "#FFFFFF"),
    bag("g2", 132, 176, 158, 50, "#FAFDF7", "#E4ECDE", "#A9BAA3", "#1F7A3D", "#FFFFFF"),
    # Out past the right edge of a 40rem centred copy column, and two steps lighter:
    # Harvest's hero paints muted body text straight over this picture.
    coil("g3", 356, 180, 52, "#6E9464", "#4E7046", "#37522F", "#9EC494", turns=5),
    ([], [rect(0, GROUND - 2, 400, 2, "#1F7A3D", op="0.5")]),
])

# ======================================== supporting plates (image-with-text) =
emit("plate-quarry", "A batch of cement under test", [
    room("r", "#EFE5D2", "#D2C09E", "#FFFFFF", glow_at=(0.34, 0.34),
         floor_near="#BCAB88", floor_far="#CFBE9C"),
    shadow("s1", 196, 218, 120, 17, 0.26, "#4A3A20"),
    bag("g1", 182, 216, 210, 62, "#F2E6CA", "#DBCBA6", "#A8936C", "#8A4B22", "#F6EDD8"),
    bag("g2", 176, 172, 190, 56, "#F6EDD8", "#E2D2AE", "#B29D74", "#8A4B22", "#F6EDD8"),
    ([], [rect(0, GROUND - 2, 400, 2, "#8A4B22", op="0.5")]),
])

emit("plate-carton", "A case on the strapping bench", [
    room("r", "#F5ECDC", "#D8C6A6", "#FFFFFF", glow_at=(0.34, 0.32),
         floor_near="#C4B18E", floor_far="#D6C6A6"),
    shadow("s1", 190, 220, 116, 16, 0.26, "#4A3418"),
    slab("g1", 108, 108, 168, 108, 32, *KRAFT),
    ([], [path("M108 108 l32 -16 h168 l-32 16 Z", "#EBCFA4"),
          rect(132, 120, 120, 10, "#9C4A1A"),
          rect(184, 108, 16, 108, "#9C4A1A", op="0.9"),
          rect(108, 160, 168, 9, "#8E6A34", op="0.3")]),
    coil("g2", 344, 186, 42, "#E4A56C", "#C07A38", "#8A5220", "#F2E4CC", turns=4),
])

# ===================================================== atelier, wide =========
# Atelier's hero is a 3:1 band, which ate the top and bottom of the 4:3 room tile. This is the
# same showroom composed for that band: the sofa low and wide, a lit pendant to one side.
emit("hero-atelier", "A lit corner of the showroom", [
    room("r", "#2A2018", "#0B0805", "#C9A227", glow_at=(0.72, 0.3), glow_r=0.5,
         floor_near="#3A2C1F", floor_far="#1E1710", floor_y=222),
    ([], [rect(0, 220, 400, 2, "#C9A227", op="0.3")]),
    shadow("s1", 150, 222, 150, 15, 0.5),
    # the sofa, reduced to its mass: this is a 135px band, not a product shot
    ([], [rect(52, 140, 196, 78, "#5A3720", rx=12),
          rect(64, 150, 84, 58, "#4E5F46", rx=10),
          rect(154, 150, 84, 58, "#4E5F46", rx=10),
          rect(40, 156, 34, 68, "#6B4228", rx=11),
          rect(226, 156, 34, 68, "#3E2413", rx=11),
          rect(64, 196, 174, 26, "#4E5F46", rx=9),
          rect(64, 196, 174, 6, "#FFFFFF", rx=3, op="0.1"),
          rect(46, 162, 12, 54, "#FFFFFF", rx=6, op="0.1")]),
    # the pendant that lights it
    ([], [rect(300, 60, 3, 44, "#8A6A16"),
          path("M262 152 L288 104 H316 L342 152 Z", "#E8C05A"),
          path("M262 152 L288 104 H300 L282 152 Z", "#FFFFFF", "0.16"),
          ell(302, 152, 40, 7, "#3A2A08"),
          ell(302, 154, 15, 4, "#FFE9B8"),
          ell(302, 168, 11, 11, "#FFF3D2")]),
    ([], []),
])

print("trade:", len(os.listdir(OUT)), "files ->", OUT)
