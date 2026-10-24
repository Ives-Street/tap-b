#!/usr/bin/env python3
"""Derive the IVES FORK regression fixtures from the stock networks in net/.

Stdlib-only Python 3.  Writes into an output directory (default test/data/,
gitignored); nothing derived is committed -- the ChicagoRegional class trips
files are ~73 MB each.  Every fixture is a deterministic function of the stock
files, so `make check` regenerates them on demand.

Fixtures (IVES-FORK.md sections 14-15):

  SiouxFalls tolled (scoping memo 2026-10-21, section 2.1)
    SiouxFalls_tolled_net.txt   stock net with Toll = 5 on 16 links (1-based
                                network-file positions TOLLED_SF below)
    SiouxFalls_k_lo.txt         stock trips, <TOLL FACTOR> 0.5 min/$
    SiouxFalls_k_hi.txt         stock trips, <TOLL FACTOR> 4.0
    SiouxFalls_k_free.txt       stock trips, <TOLL FACTOR> 0.0
    (2-class = lo, hi; 3-class = lo, hi, free; each class carries the full
    stock demand, as in the memo's runs)

  ChicagoRegional class splits (memo section 2.3)
    ChicagoRegional_h2lo/h2hi.txt          stock demand / 2, toll factor 2 / 10
    ChicagoRegional_t3lo/t3hi/t3free.txt   stock demand / 3, toll factor 2 / 10 / 0

  Preloads (the fork's standing with/without-preload check, section 2)
    <Net>_preload10.txt         10 % of each link's capacity, positional rows

  Class toll files (<CLASS TOLL FILE>, section 17), over the 76 SiouxFalls
  links; "Toll" is the tolled network's Toll column, copied as written
    SiouxFalls_ct_exempt.txt    2 classes: class 1 Toll; class 2 Toll but 0 on
                                the 8 tolled links incident to node 16 (the
                                HOV-exempt pattern; the 8 incident only to
                                node 10 still charge $5)
    SiouxFalls_ct_same2.txt     2 classes, both = Toll  (identity check)
    SiouxFalls_ct_zero2.txt     class 1 Toll, class 2 zero everywhere
                                (equivalence with a toll-factor-0 class)
    SiouxFalls_ct_same1.txt     1 class = the stock network's Toll column
    SiouxFalls_ct_bad_*.txt     malformed variants of ct_same2 that tap-b must
                                refuse: count (stated 75 links), short (75 rows),
                                extra (77 rows), swapped (row 22 as (16,8)),
                                cols (row 30 with 3 toll columns), classes
                                (<NUMBER OF CLASSES> 3), nan (a non-finite toll)

  Empty-origin fixtures (IVES-FORK.md 18), over the tolled SiouxFalls network;
  each origin block is the stock one or absent, demand is never rescaled
    SiouxFalls_dj2_a.txt        origins 1-12, plus origin 13 with intrazonal
                                demand only (13 : 100 -- active, not empty),
                                <TOLL FACTOR> 0.5
    SiouxFalls_dj2_b.txt        origins 13-24, <TOLL FACTOR> 4.0
    SiouxFalls_dj3_0/1/2.txt    origin o in class (o - 1) mod 3, interleaved,
                                <TOLL FACTOR> 0.5 / 4.0 / 0.0
    SiouxFalls_empty_lo/hi.txt  no demand at all, <TOLL FACTOR> 0.5 / 4.0
    SiouxFalls_warm_add.txt     stock trips with origin 7 removed (the warm
                                base) -- and SiouxFalls_warm_full.txt, stock
                                trips: adding origin 7's demand back
    SiouxFalls_warm_drop.txt    stock trips with origins 7 and 11 removed:
                                zeroing a non-empty origin of warm_add
    SiouxFalls_warm_hi_add.txt  origins 12-24 at <TOLL FACTOR> 4.0: dj2_b
                                with origin 12 added
    (the other warm_* at <TOLL FACTOR> 0.5; the warm cases pair them with
    dj2_b / warm_hi_add as a second class)

  ChicagoRegional disjoint classes (memory measurement, IVES-FORK.md 18)
    ChicagoRegional_dj3_0/1/2.txt  origin o in class (o - 1) mod 3, stock
                                   demand unscaled, toll factor 2 / 10 / 0

Usage: make_fixtures.py [--net NETDIR] [--out OUTDIR] [--no-chicago]
"""
import argparse
import os
import re
import sys

# 1-based positions, in network-file order, of the 16 links tolled at $5 in
# the memo's SiouxFalls fixture: every link incident to node 10 or node 16.
TOLLED_SF = [22, 25, 26, 27, 28, 29, 30, 32, 43, 47, 48, 49, 50, 51, 52, 55]
SF_TOLL = 5
SF_CLASSES = [("lo", "0.5"), ("hi", "4.0"), ("free", "0.0")]
CHI_SPLITS = [("h2lo", 2, "2"), ("h2hi", 2, "10"),
              ("t3lo", 3, "2"), ("t3hi", 3, "10"), ("t3free", 3, "0")]
PRELOAD_NETS = ["SiouxFalls", "Braess", "Anaheim", "ChicagoSketch",
                "Barcelona"]
PRELOAD_SHARE = 0.10
# Class-toll exemption: class 2 pays nothing on the tolled links incident to
# this node.
CT_EXEMPT_NODE = "16"


def read_text(path):
    with open(path, newline="") as f:
        return f.read().replace("\r\n", "\n")


def link_rows(lines):
    """Indices of the data rows (one per link) in a TNTP network file."""
    out, in_data = [], False
    for k, line in enumerate(lines):
        if "<END OF METADATA>" in line:
            in_data = True
            continue
        if not in_data:
            continue
        s = line.strip()
        if not s or s.startswith("~"):
            continue
        out.append(k)
    return out


def tolled_siouxfalls(netdir, out):
    lines = read_text(os.path.join(netdir, "SiouxFalls_net.txt")).split("\n")
    rows = link_rows(lines)
    for pos in TOLLED_SF:
        k = rows[pos - 1]
        f = lines[k].split("\t")
        # leading tab, then init term cap length fft B power speed toll type ;
        assert f[0] == "" and len(f) >= 11, lines[k]
        assert float(f[9]) == 0, ("already tolled", lines[k])
        f[9] = str(SF_TOLL)
        lines[k] = "\t".join(f)
    with open(os.path.join(out, "SiouxFalls_tolled_net.txt"), "w") as g:
        g.write("\n".join(lines))
    trips = read_text(os.path.join(netdir, "SiouxFalls_trips.txt"))
    for tag, tf in SF_CLASSES:
        t = trips.replace("<TOTAL OD FLOW> 360600.0\n",
                          "<TOTAL OD FLOW> 360600\n<TOLL FACTOR> %s\n" % tf, 1)
        assert t != trips
        with open(os.path.join(out, "SiouxFalls_k_%s.txt" % tag), "w") as g:
            g.write(t)


_NUM = re.compile(r"(\d+)(\s*:\s*)([0-9.eE+-]+)")


def chicago_splits(netdir, out):
    src = read_text(os.path.join(netdir, "ChicagoRegional_trips.txt"))
    head, sep, body = src.partition("<END OF METADATA>\n")
    assert sep
    m = re.search(r"<TOTAL OD FLOW>\s*([0-9.eE+-]+)", head)
    total = float(m.group(1))
    for tag, k, tf in CHI_SPLITS:
        new_body = _NUM.sub(
            lambda mm: "%s%s%.4f" % (mm.group(1), mm.group(2),
                                     float(mm.group(3)) / k), body)
        h = ("<NUMBER OF ZONES> 1790\n<TOTAL OD FLOW> %r\n<TOLL FACTOR> %s\n"
             "<END OF METADATA>\n\n" % (total / k, tf))
        with open(os.path.join(out, "ChicagoRegional_%s.txt" % tag), "w") as g:
            g.write(h + new_body)


def preload(netdir, out, net):
    lines = read_text(os.path.join(netdir, "%s_net.txt" % net)).split("\n")
    rows = link_rows(lines)
    res = ["<NUMBER OF LINKS> %d" % len(rows), "<END OF METADATA>",
           "~ Init Term Preload ;"]
    for k in rows:
        f = lines[k].split()
        res.append("%s %s %.6f ;" % (f[0], f[1], PRELOAD_SHARE * float(f[2])))
    with open(os.path.join(out, "%s_preload10.txt" % net), "w") as g:
        g.write("\n".join(res) + "\n")


def _network_links(path):
    lines = read_text(path).split("\n")
    return [lines[k].split()[:2] + [lines[k].split()[8]]
            for k in link_rows(lines)]


def _write_class_toll(path, links, cols, nlinks=None, nclasses=None):
    """cols: one list of toll strings per class, one entry per link."""
    res = ["<NUMBER OF LINKS> %d" % (len(links) if nlinks is None else nlinks),
           "<NUMBER OF CLASSES> %d" % (len(cols) if nclasses is None
                                       else nclasses),
           "<END OF METADATA>",
           "~ Init Term " + " ".join("Class%d" % (c + 1)
                                     for c in range(len(cols))) + " ;"]
    for i, (t, h, _toll) in enumerate(links):
        res.append(" ".join([t, h] + [col[i] for col in cols]) + " ;")
    with open(path, "w") as g:
        g.write("\n".join(res) + "\n")
    return res


def class_tolls(netdir, out):
    links = _network_links(os.path.join(out, "SiouxFalls_tolled_net.txt"))
    toll = [l[2] for l in links]
    assert sum(float(x) != 0 for x in toll) == len(TOLLED_SF)
    exempt = [("0" if CT_EXEMPT_NODE in (t, h) and float(x) != 0 else x)
              for t, h, x in links]
    assert sum(a != b for a, b in zip(toll, exempt)) == 8
    w = lambda name, *a, **k: _write_class_toll(
        os.path.join(out, "SiouxFalls_ct_%s.txt" % name), *a, **k)
    w("exempt", links, [toll, exempt])
    w("same2", links, [toll, toll])
    w("zero2", links, [toll, ["0"] * len(links)])
    stock = _network_links(os.path.join(netdir, "SiouxFalls_net.txt"))
    w("same1", stock, [[l[2] for l in stock]])
    # Negative fixtures, each one defect away from ct_same2.
    w("bad_count", links, [toll, toll], nlinks=len(links) - 1)
    w("bad_classes", links, [toll, toll], nclasses=3)
    rows = w("bad_short", links, [toll, toll])
    with open(os.path.join(out, "SiouxFalls_ct_bad_short.txt"), "w") as g:
        g.write("\n".join(rows[:-1]) + "\n")
    with open(os.path.join(out, "SiouxFalls_ct_bad_extra.txt"), "w") as g:
        g.write("\n".join(rows + [rows[-1]]) + "\n")
    hdr = 4  # metadata + comment lines before the first data row
    sw = list(rows)
    t, h, *rest = sw[hdr + 21].split()
    assert (t, h) == ("8", "16")
    sw[hdr + 21] = " ".join([h, t] + rest)
    with open(os.path.join(out, "SiouxFalls_ct_bad_swapped.txt"), "w") as g:
        g.write("\n".join(sw) + "\n")
    co = list(rows)
    co[hdr + 29] = co[hdr + 29].replace(" ;", " 5 ;")
    with open(os.path.join(out, "SiouxFalls_ct_bad_cols.txt"), "w") as g:
        g.write("\n".join(co) + "\n")
    na = list(rows)
    na[hdr + 29] = na[hdr + 29].replace(" ;", "").rsplit(" ", 1)[0] + " nan ;"
    with open(os.path.join(out, "SiouxFalls_ct_bad_nan.txt"), "w") as g:
        g.write("\n".join(na) + "\n")


def origin_blocks(text):
    """Split a TNTP trips file into (metadata head, {origin: block text}),
    each block running from its "Origin" line to the next."""
    head, sep, body = text.partition("<END OF METADATA>\n")
    assert sep
    blocks, cur = {}, None
    for line in body.split("\n"):
        m = re.match(r"\s*Origin\s+(\d+)", line)
        if m:
            cur = int(m.group(1))
            blocks[cur] = [line]
        elif cur is not None:
            blocks[cur].append(line)
    return head, {o: "\n".join(b).rstrip() + "\n\n" for o, b in blocks.items()}


def block_total(block):
    return sum(float(m.group(3)) for m in _NUM.finditer(block.split("\n", 1)[1]))


def write_trips(path, nzones, toll_factor, blocks, origins, extra=""):
    """origins: the origin blocks kept, in ascending order.  extra: raw
    blocks appended after them (still in ascending origin order overall is
    not required by tap-b; the reader indexes by the Origin line)."""
    kept = [blocks[o] for o in sorted(origins)]
    total = sum(block_total(b) for b in kept) + (block_total(extra)
                                                 if extra else 0)
    with open(path, "w") as g:
        g.write("<NUMBER OF ZONES> %d\n<TOTAL OD FLOW> %r\n"
                "<TOLL FACTOR> %s\n<END OF METADATA>\n\n\n"
                % (nzones, total, toll_factor))
        g.write("".join(kept) + extra)


def empty_origin_fixtures(netdir, out):
    _, blocks = origin_blocks(read_text(os.path.join(netdir,
                                                     "SiouxFalls_trips.txt")))
    assert sorted(blocks) == list(range(1, 25))
    sf = lambda name: os.path.join(out, "SiouxFalls_%s.txt" % name)
    intrazonal13 = "Origin \t13 \n   13 :    100.0; \n\n"
    write_trips(sf("dj2_a"), 24, "0.5", blocks, range(1, 13),
                extra=intrazonal13)
    write_trips(sf("dj2_b"), 24, "4.0", blocks, range(13, 25))
    for k, tf in enumerate(["0.5", "4.0", "0.0"]):
        write_trips(sf("dj3_%d" % k), 24, tf, blocks,
                    [o for o in range(1, 25) if (o - 1) % 3 == k])
    write_trips(sf("empty_lo"), 24, "0.5", blocks, [])
    write_trips(sf("empty_hi"), 24, "4.0", blocks, [])
    write_trips(sf("warm_full"), 24, "0.5", blocks, range(1, 25))
    write_trips(sf("warm_add"), 24, "0.5", blocks,
                [o for o in range(1, 25) if o != 7])
    write_trips(sf("warm_drop"), 24, "0.5", blocks,
                [o for o in range(1, 25) if o not in (7, 11)])
    write_trips(sf("warm_hi_add"), 24, "4.0", blocks, range(12, 25))


def chicago_disjoint(netdir, out):
    _, blocks = origin_blocks(read_text(os.path.join(
        netdir, "ChicagoRegional_trips.txt")))
    for k, tf in enumerate(["2", "10", "0"]):
        write_trips(os.path.join(out, "ChicagoRegional_dj3_%d.txt" % k), 1790,
                    tf, blocks, [o for o in blocks if (o - 1) % 3 == k])


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", default=os.path.join(here, "..", "net"))
    ap.add_argument("--out", default=os.path.join(here, "data"))
    ap.add_argument("--no-chicago", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    tolled_siouxfalls(a.net, a.out)
    class_tolls(a.net, a.out)
    for net in PRELOAD_NETS:
        preload(a.net, a.out, net)
    empty_origin_fixtures(a.net, a.out)
    if not a.no_chicago:
        chicago_splits(a.net, a.out)
        chicago_disjoint(a.net, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
