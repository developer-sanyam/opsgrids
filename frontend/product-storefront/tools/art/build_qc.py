"""Emit QuickCart's six category tiles and twenty-three product pictures."""
import os
import sys

sys.path.insert(0, "/tmp/art")
from kit import ell, f, path, rect, svg
from qc2 import eggs, flatpack, gable, orb, tin
from qc3 import bar, crescent, cup, loaf, puff
from quickcart import BASE, bottle, box3d, ground, pouch, sack, scene, tub

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/art/qc"
os.makedirs(OUT, exist_ok=True)


def emit(name, label, parts):
    d, b = [], []
    for pd, pb in parts:
        d += pd
        b += pb
    open(os.path.join(OUT, name + ".svg"), "w").write(svg(label, d, b))


# ---- a palette per aisle, so the objects belong to their department ----------
WHEAT = ("#FFF6E2", "#F2DCA8", "#BE9B58")
CREAM = ("#FFFFFF", "#F2F6FB", "#C2D6E9")
BLUE = "#1F5FA8"
BROWN = "#8A5A1E"
CORAL = ("#FFDCC3", "#F4B487", "#BE753C")
TEAL = ("#DFF3F0", "#A9D8D0", "#5E9E95")
LEAF = ("#E3F4D4", "#B4DE8E", "#6FA845")
VIOLET = ("#E7EAFD", "#BFC7F4", "#7C89D8")

# =========================================================== staples =========
emit("p-atta", "Pack of whole wheat chakki atta", [
    scene("staples", halo=(0.5, 0.42)),
    ground("sh", 204, 94),
    pouch("g1", 204, 52, 146, 180, *WHEAT, BROWN, BROWN, "#FFE9B8"),
])

emit("p-rice", "Sack of aged basmati rice", [
    scene("staples", halo=(0.44, 0.44)),
    ground("sh", 192, 100),
    # A deeper dark than the other staples: at card size the first values were so close that
    # the sack flattened into a pale blob.
    sack("g1", 192, 48, 166, 184, "#FFF6DE", "#E8D2A2", "#9A7838", "#6B3E10", "#FFFFFF",
         BROWN),
])

emit("p-dal", "Pack of toor dal", [
    scene("staples", halo=(0.52, 0.46)),
    ground("sh", 210, 86),
    pouch("g1", 210, 66, 132, 166, "#FFEFD0", "#F6D27C", "#C69A2A", "#6B4A12", "#C97F1A",
          "#FFF3DA"),
])

emit("p-oil", "Bottle of sunflower cooking oil", [
    scene("staples", halo=(0.46, 0.36)),
    ground("sh", 196, 78),
    bottle("g1", 196, 42, 110, 190, "#FFEAA6", "#F3CB4E", "#BC8F10", "#2B2118", "#FFFFFF",
           BROWN),
])

# ============================================================= dairy =========
emit("p-milk", "Pouch of toned milk", [
    scene("dairy", halo=(0.45, 0.44)),
    ground("sh", 190, 92),
    pouch("g1", 190, 56, 138, 176, *CREAM, BLUE, BLUE, "#FFFFFF"),
])

emit("p-curd", "Tub of set curd", [
    scene("dairy", halo=(0.5, 0.48)),
    ground("sh", 204, 92),
    tub("g1", 204, 94, 156, 138, "#FFFFFF", "#F0F5FB", "#BFD3E7", BLUE, BLUE),
])

emit("p-paneer", "Vacuum pack of fresh paneer", [
    scene("dairy", halo=(0.48, 0.5)),
    ground("sh", 200, 96),
    flatpack("g1", 200, 96, 178, 136, "#FFFFFF", "#F6F1E4", "#D8CDB4", "#E8EEF6", BLUE,
             "#FFFFFF"),
])

emit("p-bread", "Loaf of sandwich bread", [
    scene("dairy", halo=(0.44, 0.48)),
    ground("sh", 196, 100),
    loaf("g1", 176, 70, 172, 162, "#FFF4DC", "#F7E3B4", "#D8B76E", "#C07A2E", "#C9A05A"),
])

emit("p-eggs", "Tray of farm eggs", [
    scene("dairy", halo=(0.5, 0.5)),
    ground("sh", 200, 104),
    eggs("g1", 106, 118, 190, 114, "#E6EDF5", "#D2DEEA", "#9EB2C6", "#FBF3E2", "#D9C9AA"),
])

emit("p-ghee", "Jar of cow ghee", [
    scene("dairy", halo=(0.47, 0.42)),
    ground("sh", 198, 84),
    tin("g1", 198, 66, 132, 166, "#FFF0C4", "#F7DC8E", "#C9A63C", "#2E5F2A", "#2E5F2A",
        "#FFF3D2"),
])

# ============================================================ snacks =========
emit("p-chips", "Packet of salted potato crisps", [
    scene("snacks", halo=(0.46, 0.42)),
    ground("sh", 194, 92),
    puff("g1", 194, 52, 168, 180, "#FFE3B0", "#F6C062", "#C5861C", "#C4741A", "#B3261E",
         "#FFFFFF"),
])

emit("p-namkeen", "Packet of mixed namkeen", [
    scene("snacks", halo=(0.52, 0.44)),
    ground("sh", 208, 90),
    puff("g1", 208, 60, 158, 172, "#FFD8C4", "#F0A175", "#B55C22", "#A04C18", "#2E7D5B",
         "#FFFFFF"),
])

emit("p-biscuits", "Pack of cream biscuits", [
    scene("snacks", halo=(0.44, 0.46)),
    ground("sh", 188, 96),
    box3d("g1", 112, 88, 142, 144, 38, *CORAL, "#FFE9D7", "#FFFFFF", "#9C4A1A"),
])

emit("p-chocolate", "Bar of milk chocolate", [
    scene("snacks", halo=(0.5, 0.44)),
    ground("sh", 202, 76),
    bar("g1", 198, 76, 168, 156, "#8A5A2E", "#5E3616", "#331A04", "#7A1430", "#F0B23A",
        "#4A2A0A"),
])

# ========================================================= beverages =========
emit("p-tea", "Carton of Assam leaf tea", [
    scene("beverages", halo=(0.46, 0.44)),
    ground("sh", 190, 92),
    box3d("g1", 116, 84, 138, 148, 36, "#D8EFE6", "#9FD4C0", "#4F8F76", "#E8F7F1", "#FFFFFF",
          "#1F6B4F"),
])

emit("p-coffee", "Jar of instant filter coffee", [
    scene("beverages", halo=(0.5, 0.42)),
    ground("sh", 202, 80),
    tin("g1", 202, 62, 126, 170, "#C98A4E", "#8A5424", "#4E2B0A", "#1E1A16", "#E8C88A",
        "#3A2008"),
])

emit("p-cola", "Bottle of chilled cola", [
    scene("beverages", halo=(0.44, 0.36)),
    ground("sh", 192, 72),
    bottle("g1", 192, 38, 100, 194, "#7A4A28", "#4A240C", "#1E0E04", "#B3261E", "#B3261E",
           "#FFFFFF", neck=0.34),
])

emit("p-juice", "Carton of mixed fruit juice", [
    scene("beverages", halo=(0.52, 0.42)),
    ground("sh", 206, 78),
    gable("g1", 206, 56, 122, 176, "#FFD9A8", "#F5A93E", "#BC6C0C", "#C94F1A", "#FFFFFF",
          "#8A3D12"),
])

# ============================================================= fresh =========
emit("p-tomato", "Vine tomatoes", [
    scene("fresh", halo=(0.46, 0.46)),
    ground("sh", 178, 60, 8, 0.3),
    ground("s2", 248, 44, 7, 0.24),
    orb("g2", 248, 198, 42, "#F08A6E", "#D2381F", "#7E1408", calyx="#3F7A22", squash=0.9),
    orb("g1", 178, 178, 58, "#F58C6A", "#D8351A", "#7A1105", calyx="#3F7A22", stem="#3F6B22", squash=0.88),
])

emit("p-onion", "Red onions", [
    scene("fresh", halo=(0.48, 0.46)),
    ground("sh", 182, 58, 8, 0.3),
    ground("s2", 250, 42, 7, 0.24),
    orb("g2", 250, 200, 40, "#D8A6C2", "#A2497C", "#5A1A3E"),
    orb("g1", 182, 180, 56, "#DEA8C6", "#A63F78", "#551238", stem="#8A7A4E"),
])

emit("p-banana", "Bunch of bananas", [
    scene("fresh", halo=(0.48, 0.5)),
    ground("sh", 200, 96, 9, 0.28),
    crescent("g2", 212, 142, 186, 66, "#FFE9A0", "#EFC02C", "#A87808", "#5E4A12"),
    crescent("g1", 190, 176, 206, 76, "#FFF6CE", "#F3CE3E", "#BC8A0C", "#4E3C0E"),
])

# ========================================================= household =========
emit("p-detergent", "Box of washing powder", [
    scene("household", halo=(0.46, 0.44)),
    ground("sh", 190, 96),
    box3d("g1", 114, 82, 146, 150, 38, "#E6EAFE", "#B6C0F2", "#6470C4", "#F0F3FF", "#FFFFFF",
          "#3A47A8"),
])

emit("p-floorcleaner", "Bottle of floor cleaner", [
    scene("household", halo=(0.5, 0.38)),
    ground("sh", 202, 76),
    bottle("g1", 202, 40, 106, 192, "#C8E8F6", "#6FBEDC", "#1E7CA0", "#2A3A66", "#FFFFFF",
           "#1E5A8A"),
])

# ===================================================== category tiles ========
# Bolder and simpler than the product pictures: these are read at 88px on a phone, where a
# third object is noise rather than information.

emit("cat-staples", "Atta, rice and dal", [
    scene("staples", halo=(0.44, 0.42), halo_r=0.52),
    ground("sh", 134, 76),
    ground("s2", 254, 70),
    sack("g2", 254, 78, 132, 154, "#FBEFD0", "#E0C99A", "#94722F", "#6B3E10", "#FFFFFF",
         BROWN),
    pouch("g1", 134, 62, 128, 170, *WHEAT, BROWN, BROWN, "#FFE9B8"),
])

emit("cat-dairy", "Milk, bread and eggs", [
    scene("dairy", halo=(0.42, 0.42), halo_r=0.52),
    ground("sh", 132, 70),
    ground("s2", 252, 86),
    loaf("g2", 244, 112, 118, 120, "#FFF4DC", "#F7E3B4", "#D8B76E", "#C07A2E", "#C9A05A"),
    pouch("g1", 132, 62, 124, 170, *CREAM, BLUE, BLUE, "#FFFFFF"),
])

emit("cat-snacks", "Chips, namkeen and biscuits", [
    scene("snacks", halo=(0.44, 0.42), halo_r=0.52),
    ground("sh", 138, 78),
    ground("s2", 258, 70),
    box3d("g2", 208, 116, 106, 116, 30, *CORAL, "#FFE9D7", "#FFFFFF", "#9C4A1A"),
    puff("g1", 134, 54, 152, 178, "#FFE3B0", "#F6C062", "#C5861C", "#C4741A", "#B3261E",
         "#FFFFFF"),
])

emit("cat-beverages", "Tea, coffee and cold drinks", [
    scene("beverages", halo=(0.44, 0.4), halo_r=0.52),
    ground("sh", 136, 64),
    ground("s2", 254, 74),
    tin("g2", 254, 96, 112, 136, "#C98A4E", "#8A5424", "#4E2B0A", "#1E1A16", "#E8C88A",
        "#3A2008"),
    cup("g1", 136, 128, 104, 104, "#FFFFFF", "#EAF2F0", "#9EC4BC", "#6B3F1C", "#CFE4E0"),
])

emit("cat-fresh", "Fruit and vegetables", [
    scene("fresh", halo=(0.44, 0.44), halo_r=0.52),
    ground("sh", 138, 62, 9, 0.3),
    ground("s2", 254, 80, 9, 0.26),
    crescent("g2", 264, 172, 150, 62, "#FFF6CE", "#F3CE3E", "#BC8A0C", "#4E3C0E"),
    orb("g1", 138, 176, 58, "#F58C6A", "#D8351A", "#7A1105", stem="#3F6B22", leaf="#4C7C26"),
])

emit("cat-household", "Cleaning and household", [
    scene("household", halo=(0.44, 0.4), halo_r=0.52),
    ground("sh", 140, 74),
    ground("s2", 256, 62),
    bottle("g2", 256, 74, 88, 158, "#C8E8F6", "#6FBEDC", "#1E7CA0", "#2A3A66", "#FFFFFF",
           "#1E5A8A"),
    box3d("g1", 82, 92, 128, 140, 34, "#E6EAFE", "#B6C0F2", "#6470C4", "#F0F3FF", "#FFFFFF",
          "#3A47A8"),
])

print("quickcart:", len(os.listdir(OUT)), "files ->", OUT)
