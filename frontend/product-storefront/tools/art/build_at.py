"""Emit Atelier's four room tiles and nine furniture pictures."""
import os
import sys

sys.path.insert(0, "/tmp/art")
from atelier import (BASE, BRASS, bed, carcase, chair, floorlamp, floorshadow, pendant, scene,
                     sofa, table)
from kit import svg

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/art/at"
os.makedirs(OUT, exist_ok=True)


def emit(name, label, parts):
    d, b = [], []
    for pd, pb in parts:
        d += pd
        b += pb
    open(os.path.join(OUT, name + ".svg"), "w").write(svg(label, d, b))


# Three timbers and two cloths, used across the whole showroom so it reads as one workshop.
ROSEWOOD = ("#7A4A2E", "#54301B", "#2E1809")
TEAK = ("#B08050", "#8A5C33", "#54341A")
WALNUT = ("#8A5E3C", "#603D22", "#36200E")
OLIVE = "#4E5F46"
INDIGO = "#2E3A52"
LINEN = "#D8CBB4"
LINEN_DK = "#B4A488"

# ============================================================ seating ========
emit("p-sofa", "Three-seater sofa in solid rosewood", [
    scene("seating", glow=(0.3, 0.26)),
    floorshadow("sh", 186, rx=168, ry=19),
    sofa("g1", 190, 96, 292, *ROSEWOOD, OLIVE, "#2E1809"),
])

emit("p-armchair", "Hand-carved armchair", [
    scene("seating", glow=(0.34, 0.28)),
    floorshadow("sh", 196, rx=116, ry=16),
    chair("g1", 196, 76, 186, *TEAK, INDIGO, "#3E2410"),
])

# ============================================================= dining ========
emit("p-diningtable", "Six-seater dining table", [
    scene("dining", glow=(0.3, 0.3)),
    floorshadow("sh", 196, rx=162, ry=17),
    table("g1", 196, 120, 300, *ROSEWOOD, "#2E1809"),
])

emit("p-coffeetable", "Round marble-topped coffee table", [
    scene("dining", glow=(0.34, 0.34)),
    floorshadow("sh", 202, rx=128, ry=16),
    table("g1", 202, 150, 224, "#EDE6D8", "#CDC2AE", "#9A8D77", "#6B5E48", round_top=True,
          legs=3),
])

# =============================================================== beds ========
emit("p-bed", "King bed in solid teak", [
    scene("beds", glow=(0.3, 0.24)),
    floorshadow("sh", 192, rx=176, ry=19),
    bed("g1", 192, 66, 300, *TEAK, LINEN, LINEN_DK),
])

emit("p-wardrobe", "Two-door wardrobe", [
    scene("beds", glow=(0.32, 0.24)),
    floorshadow("sh", 198, rx=126, ry=16),
    carcase("g1", 198, 52, 204, *WALNUT, "#4A2E18", doors=2),
])

emit("p-bookshelf", "Open bookshelf in teak", [
    scene("beds", glow=(0.34, 0.26)),
    floorshadow("sh", 200, rx=122, ry=16),
    carcase("g1", 200, 48, 196, *TEAK, "#3E2410", shelves=4),
])

# =========================================================== lighting ========
emit("p-pendantlamp", "Brass pendant lamp", [
    scene("lighting", glow=(0.5, 0.42), glow_r=0.5),
    pendant("g1", 200, 40, 132, "#E8C05A", "#8A6A16", "#3A2A08"),
])

emit("p-floorlamp", "Teak and linen floor lamp", [
    scene("lighting", glow=(0.46, 0.4), glow_r=0.52),
    floorshadow("sh", 204, rx=96, ry=13, op=0.45),
    floorlamp("g1", 204, 54, 124, "#E4D5AE", "#9A8556", "#3A2E12", "#54341A"),
])

# ======================================================== room tiles =========
# One piece each, larger in frame than the product shots and pushed off-centre: at 88px a room
# tile has to be a silhouette, and two pieces of furniture at that size is a smear.

emit("cat-seating", "Sofas, armchairs and benches", [
    scene("seating", glow=(0.28, 0.24)),
    floorshadow("sh", 178, rx=174, ry=20),
    sofa("g1", 182, 82, 306, *ROSEWOOD, OLIVE, "#2E1809"),
])

emit("cat-dining", "Dining tables and consoles", [
    scene("dining", glow=(0.28, 0.28)),
    floorshadow("sh", 190, rx=172, ry=18),
    table("g1", 190, 104, 318, *ROSEWOOD, "#2E1809"),
])

emit("cat-beds", "Beds, wardrobes and shelving", [
    scene("beds", glow=(0.28, 0.22)),
    floorshadow("sh", 186, rx=182, ry=20),
    bed("g1", 186, 54, 316, *TEAK, LINEN, LINEN_DK),
])

emit("cat-lighting", "Lighting and décor", [
    scene("lighting", glow=(0.46, 0.4), glow_r=0.54),
    pendant("g1", 198, 26, 158, "#E8C05A", "#8A6A16", "#3A2A08"),
])

print("atelier:", len(os.listdir(OUT)), "files ->", OUT)
