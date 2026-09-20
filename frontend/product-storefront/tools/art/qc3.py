"""Replacements for the four QuickCart recipes that did not read as their object.

Each of these failed the 88px test in a specific way, noted on the function.
"""

from kit import ell, f, lin, path, rad, rect
from quickcart import BASE


def puff(gid, cx, top, w, h, light, mid, dark, seal, band, ink):
    """A crisp packet.

    The first attempt bulged into an amphora. A packet is a *pillow*: two wide crimped seals with
    a shallow bulge between them, never wider than about 1.2x the seal.
    """
    # The seal is the *widest* part of a packet and the waist is narrower. Having it the other
    # way round was what made the first attempt read as a barrel with hoops.
    sw = w * 0.50        # half-width of the crimped seal
    bw = w * 0.43        # half-width at the waist
    ty, by = top + 15, BASE - 13
    my = (ty + by) / 2
    d = [lin(gid, ((0, light, None), (0.4, mid, None), (1, dark, None)), 0, 0, 1, 0.25)]
    b = [
        path("M%s %s C%s %s %s %s %s %s H%s C%s %s %s %s %s %s Z" % (
            f(cx - sw), f(ty),
            f(cx - bw), f(my - h * 0.22), f(cx - bw), f(my + h * 0.22), f(cx - sw), f(by),
            f(cx + sw),
            f(cx + bw), f(my + h * 0.22), f(cx + bw), f(my - h * 0.22), f(cx + sw), f(ty)),
             "url(#%s)" % gid),
        # the crimped seals, with teeth — the detail that says "packet" and not "bag"
        rect(cx - sw, top, sw * 2, 13, seal, rx=2),
        rect(cx - sw, BASE - 11, sw * 2, 13, seal, rx=2),
    ]
    n = 11
    for i in range(n):
        tx = cx - sw + 5 + i * (sw * 2 - 10) / (n - 1)
        b.append(rect(tx, top + 2, 2, 9, "#000", rx=1, op="0.16"))
        b.append(rect(tx, BASE - 9, 2, 9, "#000", rx=1, op="0.16"))
    b += [
        path("M%s %s C%s %s %s %s %s %s H%s C%s %s %s %s %s %s Z" % (
            f(cx - sw + 8), f(ty + 6),
            f(cx - bw + 6), f(my - h * 0.16), f(cx - bw + 6), f(my + h * 0.16),
            f(cx - sw + 10), f(by - 6),
            f(cx - sw + 26),
            f(cx - sw + 22), f(my + h * 0.16), f(cx - sw + 22), f(my - h * 0.16),
            f(cx - sw + 24), f(ty + 6)), "url(#gl)"),
        path("M%s %s C%s %s %s %s %s %s H%s C%s %s %s %s %s %s Z" % (
            f(cx + sw - 8), f(ty + 8),
            f(cx + bw - 6), f(my - h * 0.14), f(cx + bw - 6), f(my + h * 0.14),
            f(cx + sw - 10), f(by - 8),
            f(cx + sw - 24),
            f(cx + sw - 20), f(my + h * 0.14), f(cx + sw - 20), f(my - h * 0.14),
            f(cx + sw - 22), f(ty + 8)), "url(#sd)"),
        rect(cx - w * 0.36, my - h * 0.13, w * 0.72, h * 0.28, band, rx=6),
        rect(cx - w * 0.27, my - h * 0.06, w * 0.54, 10, ink, rx=5),
        rect(cx - w * 0.27, my + h * 0.07, w * 0.32, 7, ink, rx=3.5, op="0.72"),
    ]
    return d, b


def crescent(gid, cx, cy, w, h, light, mid, dark, tip):
    """A banana.

    The first attempt was a sliver — the two arcs were too close and it read as a smile. The
    outer and inner arcs are now pulled far enough apart to give the fruit real body.
    """
    d = [lin(gid, ((0, light, None), (0.4, mid, None), (1, dark, None)), 0, 0, 0.35, 1)]
    b = [
        path("M%s %s C%s %s %s %s %s %s C%s %s %s %s %s %s Z" % (
            f(cx - w / 2), f(cy - h * 0.34),
            f(cx - w * 0.3), f(cy + h * 0.98), f(cx + w * 0.3), f(cy + h * 0.98),
            f(cx + w / 2), f(cy - h * 0.34),
            f(cx + w * 0.3), f(cy + h * 0.42), f(cx - w * 0.3), f(cy + h * 0.42),
            f(cx - w / 2), f(cy - h * 0.34)), "url(#%s)" % gid),
        # the lit ridge that runs along the top of the fruit
        path("M%s %s C%s %s %s %s %s %s C%s %s %s %s %s %s Z" % (
            f(cx - w * 0.38), f(cy - h * 0.18),
            f(cx - w * 0.22), f(cy + h * 0.66), f(cx + w * 0.22), f(cy + h * 0.66),
            f(cx + w * 0.38), f(cy - h * 0.18),
            f(cx + w * 0.22), f(cy + h * 0.46), f(cx - w * 0.22), f(cy + h * 0.46),
            f(cx - w * 0.38), f(cy - h * 0.18)), "url(#gl)"),
        rect(cx - w / 2 - 4, cy - h * 0.44, 12, 18, tip, rx=5),
        rect(cx + w / 2 - 8, cy - h * 0.44, 12, 18, tip, rx=5),
    ]
    return d, b


def loaf(gid, cx, top, w, h, light, mid, dark, crust, crumb):
    """A cut loaf, the sliced face towards us.

    The first attempt was a domed mound with a pale sticker on it and read as a bun. An arch of
    crumb inside a crust rim, with the rest of the loaf receding to the right, reads as bread.
    """
    hw = w / 2
    ty = top + h * 0.44
    d = [lin(gid, ((0, light, None), (0.45, mid, None), (1, dark, None)), 0, 0, 1, 0.3)]

    def arch(l, r, t, ay):
        return ("M%s %s L%s %s Q%s %s %s %s Q%s %s %s %s L%s %s Z" % (
            f(l), f(BASE), f(l), f(ay), f(l), f(t), f((l + r) / 2), f(t),
            f(r), f(t), f(r), f(ay), f(r), f(BASE)))

    b = [
        # the loaf receding to the right: the same arch, pushed back and up
        path("M%s %s L%s %s Q%s %s %s %s L%s %s L%s %s Z" % (
            f(cx - hw + 10), f(top + 6), f(cx + hw + 34), f(top + 6),
            f(cx + hw + 46), f(top + 10), f(cx + hw + 46), f(ty + 6),
            f(cx + hw + 46), f(BASE), f(cx + hw), f(BASE)), crust),
        path(arch(cx - hw, cx + hw, top, ty), crust),
        path(arch(cx - hw + 11, cx + hw - 11, top + 11, ty), "url(#%s)" % gid),
    ]
    # the crumb's open texture, and the seam between the first two slices
    for dx, dy, rr in ((-30, 34, 7), (-4, 20, 6), (24, 40, 8), (10, 62, 6), (-22, 76, 7),
                       (28, 82, 5)):
        b.append(ell(cx + dx, top + 42 + dy, rr, rr * 0.82, crumb, op="0.55"))
    b += [
        rect(cx + hw - 30, top + 16, 18, BASE - top - 20, "url(#sd)", rx=4),
        rect(cx - hw + 11, top + 18, 16, BASE - top - 24, "url(#gl)", rx=4),
    ]
    return d, b


def bar(gid, cx, top, w, h, light, mid, dark, wrapper, band, ink):
    """A slab of chocolate in front of its wrapper.

    The first attempt stood a narrow wrapper on end and read as a coffee jar. A wide slab with
    moulded segments is unmistakable at any size.
    """
    hw = w / 2
    sy = top + h * 0.26
    d = [lin(gid, ((0, light, None), (0.42, mid, None), (1, dark, None)), 0, 0, 1, 0.3)]
    b = [
        # the wrapper, standing behind and offset so both objects read
        rect(cx - hw + 16, top, w - 10, BASE - top - 22, wrapper, rx=5),
        rect(cx - hw + 16, top, 16, BASE - top - 22, "url(#gl)", rx=5),
        rect(cx + hw - 12, top, 14, BASE - top - 22, "url(#sd)", rx=5),
        rect(cx - w * 0.2, top + h * 0.16, w * 0.62, 10, band, rx=5),
        rect(cx - w * 0.2, top + h * 0.3, w * 0.36, 8, band, rx=4, op="0.75"),
        # the slab
        rect(cx - hw, sy, w, BASE - sy, "url(#%s)" % gid, rx=5),
    ]
    cols, rows = 4, 3
    gw = (w - 16) / cols
    gh = (BASE - sy - 14) / rows
    for r in range(rows):
        for c in range(cols):
            x = cx - hw + 8 + c * gw
            y = sy + 7 + r * gh
            b.append(rect(x + 1.5, y + 1.5, gw - 3, gh - 3, "#FFF", rx=2, op="0.13"))
            b.append(rect(x + 1.5, y + gh * 0.42, gw - 3, gh * 0.56 - 3, "#000", rx=2,
                          op="0.16"))
    b.append(rect(cx - hw, sy, w, 4, "#FFF", rx=2, op="0.26"))
    b.append(rect(cx + hw - 13, sy + 5, 12, BASE - sy - 10, "url(#sd)", rx=4))
    return d, b


def cup(gid, cx, top, w, h, light, mid, dark, inner, saucer):
    """A cup on a saucer. The steam came out invisible on a pale wall, so it is gone."""
    hw = w / 2
    bw = w * 0.76 / 2
    d = [lin(gid, ((0, light, None), (0.42, mid, None), (1, dark, None)), 0, 0, 1, 0.2)]
    b = [
        path("M%s %s Q%s %s %s %s H%s Q%s %s %s %s Z" % (
            f(cx + hw + 2), f(top + 12), f(cx + hw + 32), f(top + 28),
            f(cx + hw + 13), f(top + h * 0.62), f(cx + hw + 2),
            f(cx + hw + 21), f(top + h * 0.48), f(cx + hw - 4), f(top + 24)), mid),
        path("M%s %s L%s %s Q%s %s %s %s L%s %s Z" % (
            f(cx - hw), f(top + 6), f(cx - bw), f(BASE - 15),
            f(cx), f(BASE - 6), f(cx + bw), f(BASE - 15), f(cx + hw), f(top + 6)),
             "url(#%s)" % gid),
        ell(cx, top + 6, hw, 10, inner),
        ell(cx, top + 8, hw - 7, 7, dark, op="0.4"),
        path("M%s %s L%s %s H%s L%s %s Z" % (f(cx - w * 0.3), f(top + 18), f(cx - w * 0.26),
                                             f(BASE - 22), f(cx - w * 0.14), f(cx - w * 0.18),
                                             f(top + 18)), "url(#gl)"),
        ell(cx, BASE - 3, hw + 32, 12, saucer),
        ell(cx, BASE - 7, hw + 32, 11, light),
    ]
    return d, b
