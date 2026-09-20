"""QuickCart's art world: a bright quick-commerce shop.

Four things are identical in every frame, and they are what make six aisles read as one shop:
one light from the upper left, a white spotlight pooled behind the subject, a **marigold shelf**
the subject stands on, and a soft contact shadow where it meets that shelf. Only the *wall tint*
changes — a hue per aisle, all at the same pastel value, so a row of tiles reads as six
departments of one store rather than as six unrelated pictures.

Everything is composed inside x 60..340 so that `ImagePlate`'s `object-fit: cover` can crop to
1:1 without taking a bite out of the subject.
"""

from kit import backdrop, cast, ell, f, lin, path, rad, rect, svg


def gloss(d_attr, op=None):
    """A specular face, faded into the form by the shared `gl` ramp."""
    return path(d_attr, "url(#gl)")


def sshade(d_attr):
    """The falling-away side, faded by the shared `sd` ramp."""
    return path(d_attr, "url(#sd)")

MARIGOLD = "#F5B92E"
MARIGOLD_DK = "#C98C14"
BASE = 232          # where every subject meets the shelf
SHELF_H = 14

# wall top, wall bottom, shelf-underside
AISLE = {
    "staples":   ("#FFFBF2", "#F7E7C6", "#E8D3A8"),
    "dairy":     ("#F8FCFF", "#DCEAFA", "#C5DAF0"),
    "snacks":    ("#FFF7F1", "#FBDBC8", "#F0C5A8"),
    "beverages": ("#F1FAF8", "#CFE9E4", "#B7DBD4"),
    "fresh":     ("#F5FBEE", "#D9F0C9", "#C2E5AC"),
    "household": ("#F6F7FF", "#DEE2FB", "#C8CFF6"),
}


def scene(aisle, halo=(0.46, 0.44), halo_r=0.46):
    """The ground every QuickCart frame shares: graded wall, spotlight, marigold shelf."""
    top, bot, under = AISLE[aisle]
    d, b = backdrop("bg", top, bot)
    d.append(rad("hl", ((0, "#FFFFFF", "0.95"), (1, "#FFFFFF", "0")),
                 cx=halo[0], cy=halo[1], r=halo_r))
    b.append(rect(0, 0, 400, 300, "url(#hl)"))
    # the shelf, seen just below eye level: a lit top lip, the marigold face, a shaded underside
    d.append(lin("sf", ((0, MARIGOLD, None), (1, MARIGOLD_DK, None)), 0, 0, 0, 1))
    b.append(rect(0, BASE, 400, SHELF_H, "url(#sf)"))
    b.append(rect(0, BASE, 400, 3, "#FFD98A"))
    b.append(rect(0, BASE + SHELF_H, 400, 300 - BASE - SHELF_H, under))
    b.append(rect(0, BASE + SHELF_H, 400, 4, "#000", op="0.12"))
    d.append(lin("gl", ((0, "#FFFFFF", "0.62"), (1, "#FFFFFF", "0")), 0, 0, 1, 0))
    d.append(lin("sd", ((0, "#000000", "0.26"), (1, "#000000", "0")), 1, 0, 0, 0))
    return d, b


def ground(gid, cx, rx, ry=9, op=0.34):
    """The contact shadow. It sits on the shelf, so it is warm, not neutral."""
    return cast(gid, cx, BASE + 3, rx, ry, "#6B3F00", op)


# ------------------------------------------------------------------ recipes

def pouch(gid, cx, top, w, h, light, mid, dark, seal, band, ink):
    """A stand-up pouch. The sides bow outward; a straight-sided bag reads as a diagram."""
    hw = w / 2
    l, r = cx - hw, cx + hw
    d = [lin(gid, ((0, light, None), (0.42, mid, None), (1, dark, None)), 0, 0, 1, 0.3)]
    b = [
        path("M%s %s C%s %s %s %s %s %s H%s C%s %s %s %s %s %s Z" % (
            f(l + 7), f(BASE),
            f(l + 1), f(BASE - h * 0.34), f(l + 2), f(top + h * 0.28), f(l + 9), f(top + 15),
            f(r - 9),
            f(r - 2), f(top + h * 0.28), f(r - 1), f(BASE - h * 0.34), f(r - 7), f(BASE)),
             "url(#%s)" % gid),
        path("M%s %s L%s %s H%s L%s %s Z" % (f(l + 9), f(top + 15), f(l + 16), f(top),
                                             f(r - 16), f(r - 9), f(top + 15)), seal),
        gloss("M%s %s C%s %s %s %s %s %s H%s C%s %s %s %s %s %s Z" % (
            f(l + 17), f(BASE - 12),
            f(l + 11), f(BASE - h * 0.4), f(l + 12), f(top + h * 0.34), f(l + 19), f(top + 22),
            f(l + 30),
            f(l + 23), f(top + h * 0.34), f(l + 22), f(BASE - h * 0.4), f(l + 28),
            f(BASE - 12)), "0.46"),
        path("M%s %s C%s %s %s %s %s %s H%s C%s %s %s %s %s %s Z" % (
            f(r - 14), f(BASE - 12),
            f(r - 9), f(BASE - h * 0.4), f(r - 10), f(top + h * 0.34), f(r - 16), f(top + 22),
            f(r - 25),
            f(r - 19), f(top + h * 0.34), f(r - 18), f(BASE - h * 0.4), f(r - 23),
            f(BASE - 12)), "url(#sd)"),
        rect(l + 8, BASE - h * 0.6, w - 16, h * 0.34, band, rx=6),
        rect(l + 20, BASE - h * 0.53, (w - 40) * 0.92, 9, ink, rx=4.5),
        rect(l + 20, BASE - h * 0.4, (w - 40) * 0.55, 7, ink, rx=3.5, op="0.72"),
    ]
    return d, b


def bottle(gid, cx, top, w, h, light, mid, dark, cap, band, ink, neck=0.3, shoulder=0.2):
    """A moulded bottle: a wide soft gloss on the lit side, a narrow dark edge opposite."""
    hw, nw = w / 2, w * neck / 2
    l, r = cx - hw, cx + hw
    sy = top + h * shoulder + 15
    d = [lin(gid, ((0, light, None), (0.4, mid, None), (1, dark, None)), 0, 0, 1, 0.3)]
    b = [
        path("M%s %s V%s C%s %s %s %s %s %s V%s Q%s %s %s %s H%s Q%s %s %s %s V%s "
             "C%s %s %s %s %s %s Z" % (
                 f(cx - nw), f(top + 14), f(top + h * 0.1),
                 f(cx - nw), f(top + h * 0.16), f(l), f(sy - 14), f(l), f(sy),
                 f(BASE - 10),
                 f(l), f(BASE), f(l + 11), f(BASE),
                 f(r - 11),
                 f(r), f(BASE), f(r), f(BASE - 10),
                 f(sy),
                 f(r), f(sy - 14), f(cx + nw), f(top + h * 0.16), f(cx + nw), f(top + h * 0.1)),
             "url(#%s)" % gid),
        rect(cx - nw - 4, top, nw * 2 + 8, 17, cap, rx=4),
        rect(cx - nw - 4, top + 11, nw * 2 + 8, 6, "#000", op="0.2"),
        gloss("M%s %s Q%s %s %s %s H%s Q%s %s %s %s Z" % (
            f(l + 13), f(sy + 6), f(l + 7), f((sy + BASE) / 2), f(l + 13), f(BASE - 14),
            f(l + 25), f(l + 19), f((sy + BASE) / 2), f(l + 25), f(sy + 6)), "0.5"),
        path("M%s %s Q%s %s %s %s H%s Q%s %s %s %s Z" % (
            f(r - 12), f(sy + 10), f(r - 6), f((sy + BASE) / 2), f(r - 12), f(BASE - 16),
            f(r - 22), f(r - 16), f((sy + BASE) / 2), f(r - 22), f(sy + 10)), "url(#sd)"),
        rect(l + 1, BASE - h * 0.46, w - 2, h * 0.3, band, rx=3),
        rect(l + 13, BASE - h * 0.4, w - 26, 9, ink, rx=4.5),
        rect(l + 13, BASE - h * 0.28, (w - 26) * 0.56, 7, ink, rx=3.5, op="0.72"),
    ]
    return d, b


def box3d(gid, x, top, w, h, dep, light, mid, dark, lid, face, ink, second=True):
    """A carton in three-quarter view. Three faces off one hue is the cheapest honest depth."""
    dy = dep * 0.58
    d = [lin(gid, ((0, light, None), (0.5, mid, None), (1, dark, None)), 0, 0, 1, 0.25)]
    b = [
        rect(x, top, w, h, "url(#%s)" % gid),
        path("M%s %s l%s -%s h%s l-%s %s Z" % (f(x), f(top), f(dep), f(dy), f(w), f(dep), f(dy)),
             lid),
        path("M%s %s l%s -%s v%s l-%s %s Z" % (f(x + w), f(top), f(dep), f(dy), f(h), f(dep),
                                               f(dy)), dark),
        rect(x + w * 0.11, top + h * 0.14, w * 0.78, h * 0.44, face, rx=4),
        rect(x + w * 0.19, top + h * 0.23, w * 0.62, 9, ink, rx=4.5),
        rect(x + w * 0.19, top + h * 0.37, w * 0.38, 7, ink, rx=3.5, op="0.72"),
        gloss("M%s %s h12 v%s h-12 Z" % (f(x + 6), f(top + 7), f(h - 14)), "0.22"),
    ]
    if second:
        b.insert(-1, rect(x + w * 0.17, top + h * 0.72, w * 0.66, 8, face, rx=4, op="0.9"))
    return d, b


def tub(gid, cx, top, w, h, light, mid, dark, lid, band):
    """A tapered tub with a proud lid — curd, ghee, paneer."""
    hw = w / 2
    bw = w * 0.84 / 2
    d = [lin(gid, ((0, light, None), (0.42, mid, None), (1, dark, None)), 0, 0, 1, 0.3)]
    b = [
        path("M%s %s L%s %s Q%s %s %s %s L%s %s Z" % (
            f(cx - hw), f(top + 14), f(cx - bw), f(BASE - 3),
            f(cx), f(BASE + 6), f(cx + bw), f(BASE - 3), f(cx + hw), f(top + 14)),
             "url(#%s)" % gid),
        rect(cx - hw - 6, top, w + 12, 18, lid, rx=6),
        ell(cx, top + 2, hw + 6, 6, "#FFF", op="0.32"),
        gloss("M%s %s L%s %s H%s L%s %s Z" % (f(cx - w * 0.3), f(top + 24), f(cx - w * 0.26),
                                              f(BASE - 8), f(cx - w * 0.16), f(cx - w * 0.2),
                                              f(top + 24)), "0.4"),
        path("M%s %s L%s %s H%s L%s %s Z" % (f(cx + w * 0.26), f(top + 24), f(cx + w * 0.22),
                                             f(BASE - 8), f(cx + w * 0.34), f(cx + w * 0.38),
                                             f(top + 24)), "url(#sd)"),
        path("M%s %s L%s %s H%s L%s %s Z" % (f(cx - w * 0.44), f(top + h * 0.4),
                                             f(cx - w * 0.41), f(top + h * 0.72),
                                             f(cx + w * 0.41), f(cx + w * 0.44),
                                             f(top + h * 0.4)), band),
    ]
    return d, b


def sack(gid, cx, top, w, h, light, mid, dark, tie, panel, ink):
    """A gathered woven sack. The throat is what makes it read as cloth rather than as a box."""
    hw = w / 2
    nk = w * 0.15
    sy = top + 24
    d = [lin(gid, ((0, light, None), (0.4, mid, None), (1, dark, None)), 0, 0, 1, 0.3)]
    b = [
        path("M%s %s C%s %s %s %s %s %s V%s H%s V%s C%s %s %s %s %s %s Z" % (
            f(cx - nk), f(sy),
            f(cx - nk - w * 0.18), f(sy + h * 0.1), f(cx - hw), f(sy + h * 0.28),
            f(cx - hw), f(sy + h * 0.52),
            f(BASE),
            f(cx + hw), f(sy + h * 0.52),
            f(cx + hw), f(sy + h * 0.28), f(cx + nk + w * 0.18), f(sy + h * 0.1),
            f(cx + nk), f(sy)), "url(#%s)" % gid),
        path("M%s %s Q%s %s %s %s L%s %s H%s Z" % (
            f(cx - nk), f(sy), f(cx), f(sy - 15), f(cx + nk), f(sy),
            f(cx + nk - 4), f(sy - 11), f(cx - nk + 4)), mid),
        rect(cx - nk - 5, top + 8, nk * 2 + 10, 12, tie, rx=6),
        gloss("M%s %s C%s %s %s %s %s %s H%s C%s %s %s %s %s %s Z" % (
            f(cx - w * 0.33), f(BASE - 10),
            f(cx - w * 0.36), f(BASE - h * 0.42), f(cx - w * 0.3), f(sy + h * 0.16),
            f(cx - w * 0.19), f(sy + 12),
            f(cx - w * 0.09),
            f(cx - w * 0.2), f(sy + h * 0.16), f(cx - w * 0.24), f(BASE - h * 0.42),
            f(cx - w * 0.22), f(BASE - 10)), "0.34"),
        path("M%s %s C%s %s %s %s %s %s H%s C%s %s %s %s %s %s Z" % (
            f(cx + w * 0.3), f(BASE - 10),
            f(cx + w * 0.34), f(BASE - h * 0.42), f(cx + w * 0.28), f(sy + h * 0.16),
            f(cx + w * 0.18), f(sy + 14),
            f(cx + w * 0.1),
            f(cx + w * 0.19), f(sy + h * 0.16), f(cx + w * 0.23), f(BASE - h * 0.42),
            f(cx + w * 0.2), f(BASE - 10)), "url(#sd)"),
        rect(cx - w * 0.32, BASE - h * 0.46, w * 0.64, h * 0.28, panel, rx=4),
        rect(cx - w * 0.24, BASE - h * 0.4, w * 0.48, 9, ink, rx=4.5),
        rect(cx - w * 0.24, BASE - h * 0.28, w * 0.28, 7, ink, rx=3.5, op="0.72"),
    ]
    return d, b
