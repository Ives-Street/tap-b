#!/usr/bin/env python3
"""IVES FORK regression ladder (L1) -- driven by `make check`.

Stdlib-only Python 3.  Runs tap-b on the stock TNTP networks and the derived
fixtures of test/make_fixtures.py, and checks:

  single-class  flows files byte-identical to the committed goldens in
                test/golden/ (small networks: the file itself; Barcelona and
                ChicagoRegional: an md5 in test/golden/checksums.txt), at
                <NUMBER OF THREADS> 1, where the parallel build is
                deterministic (IVES-FORK.md, "A note on reproducibility").
  multi-class   every class's relative gap, recomputed by the independent
                oracle tools/mcgap.py, is at or below the case's convergence
                target; and, with --serial-bin, total link flows within
                --flow-tol of the serial build's (per-class flows are not
                unique in multi-class UE, so their difference is reported,
                not gated).
  empty origins every case asserts the skip's counter line
                (IVES-FORK.md 18), "Empty origins skipped: N of M", with N
                the case's known count of (origin, class) pairs without
                demand -- a no-op skip would pass every identity check
                below.  "ident" cases (the empty-origin fixtures and the
                small multi-class ones) byte-compare, at 1 thread, the flows
                file, the <ORIGIN VEH LENGTH FILE> output and the log's
                CLASS_GAP and Iteration lines (time and shift count
                stripped) with goldens written by 81d8e14, the binary before
                the skip (test/golden/ident/).
  class tolls   <CLASS TOLL FILE> (IVES-FORK.md 17): an HOV-exempt-pattern
                case checked per class by the oracle (which reads the file);
                files that must not change the answer, byte-compared at 1
                thread with the run they must reproduce ("same"); and
                malformed files tap-b must refuse ("reject": nonzero exit and
                the expected message).

The goldens were generated once by the shared binary that predates the
multi-class work (md5 a68c72f066aa9e019f273ea9ef711774, tap-b @3837de3) with
`run_check.py golden --bin <that binary>`; regenerate them only for a change
that is meant to move single-class flows, and say so in IVES-FORK.md.

`identity --ref-bin REF` is G-ID1 of the skip-empty-origins work run against
a live reference binary rather than goldens: every non-reject case at 1
thread through both binaries, with `cmp` on the flows file, the origin
veh-length file and the log extract above.

Usage:
  run_check.py check  [--bin BIN] [--threads N] [--serial-bin SBIN]
                      [--only NAME[,NAME]] [--skip-large] [--work DIR]
  run_check.py identity --bin BIN --ref-bin REF [--only ...] [--skip-large]
                         [--solve-gap G]
  run_check.py golden --bin BIN [--only ...] [--work DIR]
  run_check.py list
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NET = os.path.join(ROOT, "net")
DATA = os.path.join(HERE, "data")
GOLDEN = os.path.join(HERE, "golden")
ORACLE = os.path.join(ROOT, "tools", "mcgap.py")

# name, network, [trips...], preload or None, gap, max iterations, kind,
# large (needs the Chicago fixtures / minutes of compute), extra param lines.
# kind: "file" = golden flows file, "md5" = golden checksum, "oracle" = multi.
CASES = []


def case(name, net, trips, preload, gap, maxit, kind, large=False, extra=(),
         solve_gap=None, totals_gate=True, class_toll=None, golden=None,
         ref=None, expect=None, rows_only=False, empties=0):
    """gap: the target.  For single-class cases it is also the solve's
    <CONVERGENCE GAP>.  For multi-class cases it is the per-class gate the
    oracle applies to *every* class, and solve_gap is the (tighter) aggregate
    <CONVERGENCE GAP> tap-b stops on: tap-b's stopping rule pools all classes,
    weighted by each class's SPTT, so a class can sit above the aggregate (see
    IVES-FORK.md 15a).

    class_toll: a <CLASS TOLL FILE>.  golden: the golden a "file" case is
    compared with (default: its own name).  ref: for kind "same", the case
    (a dict from spec()) whose flows this one must reproduce byte for byte.
    expect: for kind "reject", a regex the log must match.  rows_only: a
    "same" case compares the link rows only, not the summary block.
    empties: the number of (origin, class) pairs with no demand -- the N the
    skip's counter line must print (IVES-FORK.md 18)."""
    c = spec(name, net, trips, preload, gap, maxit, kind, large, extra,
             solve_gap, totals_gate, class_toll, golden, ref, expect,
             rows_only, empties)
    CASES.append(c)
    return c


def spec(name, net, trips, preload, gap, maxit, kind, large=False, extra=(),
         solve_gap=None, totals_gate=True, class_toll=None, golden=None,
         ref=None, expect=None, rows_only=False, empties=0):
    return dict(name=name, net=net, trips=trips, preload=preload,
                gap=gap, maxit=maxit, kind=kind, large=large,
                extra=list(extra), solve_gap=solve_gap or gap,
                totals_gate=totals_gate, class_toll=class_toll,
                golden=golden or name, ref=ref, expect=expect,
                rows_only=rows_only, empties=empties)


# Origins without demand in the stock trips files (IVES-FORK.md 18): Braess
# 3 of 4, ChicagoSketch 1 of 387, Barcelona 13 of 110, ChicagoRegional 19 of
# 1790, Anaheim2's class 2 34 of 38; SiouxFalls and Anaheim none.
for _n, _kind, _e in [("SiouxFalls", "file", 0), ("Braess", "file", 3),
                      ("Anaheim", "file", 0), ("ChicagoSketch", "file", 1),
                      ("Barcelona", "md5", 13)]:
    case(_n, "%s_net.txt" % _n, ["%s_trips.txt" % _n], None, 1e-10, 300, _kind,
         empties=_e)
    case(_n + "_preload10", "%s_net.txt" % _n, ["%s_trips.txt" % _n],
         "%s_preload10.txt" % _n, 1e-10, 300, _kind, empties=_e)
case("ChicagoRegional", "ChicagoRegional_net.txt",
     ["ChicagoRegional_trips.txt"], None, 1e-4, 100, "md5", large=True,
     empties=19)

SF = "SiouxFalls_tolled_net.txt"
# Multi-class: per-class gate `gap`, aggregate solve target `solve_gap`.
case("SF_tolled_2class", SF, ["SiouxFalls_k_lo.txt", "SiouxFalls_k_hi.txt"],
     None, 1e-8, 300, "oracle", solve_gap=1e-9)
case("SF_tolled_3class", SF, ["SiouxFalls_k_lo.txt", "SiouxFalls_k_hi.txt",
                              "SiouxFalls_k_free.txt"], None, 1e-8, 300,
     "oracle", solve_gap=1e-9)
case("SF_tolled_2class_preload10", SF,
     ["SiouxFalls_k_lo.txt", "SiouxFalls_k_hi.txt"],
     "SiouxFalls_preload10.txt", 1e-8, 300, "oracle", solve_gap=1e-9)
# Upstream's own two-class sample, run unbatched: its stock parameters use
# <NUMBER OF BATCHES> 2, the batch mode connectome does not use.  Class 2
# prices distance at 2/ft, so its generalized cost (~7.6e8) is ~500x class
# 1's and the pooled gap is almost entirely class 2's (an all-or-nothing 0):
# at the stock 1e-6 tap-b stops after 2 iterations with class 1 at ~4e-4.
# The solve target is set far below the gate: at 1e-10 class 1 reaches
# ~3e-8, but the totals on its lightly loaded links (flat BPR at ~0.3 v/c)
# still sit ~10 veh from the serial build's; at 1e-13 they agree to 2e-4.
case("Anaheim2_1batch", "Anaheim2_net.tntp",
     ["Anaheim2_trips1.tntp", "Anaheim2_trips2.tntp"], None, 1e-6, 300,
     "oracle", solve_gap=1e-13, empties=34)
# ChicagoRegional: the serial-totals comparison is report-only.  At solve 1e-6
# both builds are at per-class equilibrium by the oracle (<= 5e-7), yet their
# total flows differ by up to 7.2 veh (median 0.018, diffuse, on flat-BPR
# links): a gap of ~4e-7 on 1.4M trips does not pin totals to 0.1 veh.  The
# 0.1-veh gate is defined for the small networks only (IVES-FORK.md 15b).
case("Chicago_2class", "ChicagoRegional_net.txt",
     ["ChicagoRegional_h2lo.txt", "ChicagoRegional_h2hi.txt"], None, 1e-5,
     100, "oracle", large=True, solve_gap=1e-6, totals_gate=False,
     empties=38)
case("Chicago_3class", "ChicagoRegional_net.txt",
     ["ChicagoRegional_t3lo.txt", "ChicagoRegional_t3hi.txt",
      "ChicagoRegional_t3free.txt"], None, 1e-5, 100, "oracle", large=True,
     solve_gap=1e-6, totals_gate=False, empties=57)

# Empty origins (IVES-FORK.md 18).  At 1 thread the skip must leave every
# result byte-identical to 81d8e14, which still built, updated and scored a
# bush for every (origin, class) pair: "ident" cases compare with 81d8e14's
# own output (test/golden/ident/), and each multi-class one is also an oracle
# case at THREADS.  Classes with *different* toll factors are the point:
# 81d8e14's empty bushes moved results only through the class-cost switch
# their dispatch triggered (changeFixedCosts is incremental), so a skip that
# drops a switch fails here and nowhere else (R0 F1: SiouxFalls K = 1 plus an
# all-empty class at another toll factor moves 71 of 76 links by up to 3.5e-4).
#   dj2       disjoint origin halves, 0.5 / 4.0 min/$; origin 13 in class 1
#             has intrazonal demand only, which is active (23 of 48 empty)
#   dj3       interleaved thirds, 0.5 / 4.0 / 0.0 (48 of 72 empty)
#   emptyclass_last/first  stock demand at 0.5 plus a class with no demand
#             at all at 4.0, after / before it (the reference is 81d8e14 on
#             the same input, not the K = 1 run, which differs)
#   allempty  no demand anywhere, 1 and 2 classes: must not crash
SFD = {"dj2": (["SiouxFalls_dj2_a.txt", "SiouxFalls_dj2_b.txt"], 23),
       "dj3": (["SiouxFalls_dj3_0.txt", "SiouxFalls_dj3_1.txt",
                "SiouxFalls_dj3_2.txt"], 48),
       "emptyclass_last": (["SiouxFalls_k_lo.txt",
                            "SiouxFalls_empty_hi.txt"], 24),
       "emptyclass_first": (["SiouxFalls_empty_hi.txt",
                             "SiouxFalls_k_lo.txt"], 24)}
for _n, (_t, _e) in SFD.items():
    case("SF_" + _n, SF, _t, None, 1e-8, 300, "ident", solve_gap=1e-9,
         empties=_e)
    case("SF_" + _n + "_oracle", SF, _t, None, 1e-8, 300, "oracle",
         solve_gap=1e-9, empties=_e)
case("SF_allempty_1", SF, ["SiouxFalls_empty_lo.txt"], None, 1e-9, 300,
     "ident", empties=24)
case("SF_allempty_2", SF, ["SiouxFalls_empty_lo.txt",
                           "SiouxFalls_empty_hi.txt"], None, 1e-9, 300,
     "ident", empties=48)
# The pre-existing multi-class cases, byte-compared with 81d8e14 as well
# (their oracle cases above stay): no empties, so the skip must be a no-op.
_lohi = ["SiouxFalls_k_lo.txt", "SiouxFalls_k_hi.txt"]
for _n, _t, _p in [("SF_tolled_2class", _lohi, None),
                   ("SF_tolled_3class", _lohi + ["SiouxFalls_k_free.txt"],
                    None),
                   ("SF_tolled_2class_preload10", _lohi,
                    "SiouxFalls_preload10.txt")]:
    case(_n + "_ident", SF, _t, _p, 1e-9, 300, "ident")
case("Anaheim2_1batch_ident", "Anaheim2_net.tntp",
     ["Anaheim2_trips1.tntp", "Anaheim2_trips2.tntp"], None, 1e-13, 300,
     "ident", empties=34)
# The disjoint 3-class ChicagoRegional the memory measurement uses: origin o
# in class (o - 1) mod 3, stock demand unscaled, toll factors 2 / 10 / 0.
# Empty: 5,370 pairs less the 1,771 stock origins with demand = 3,599.
case("Chicago_dj3", "ChicagoRegional_net.txt",
     ["ChicagoRegional_dj3_0.txt", "ChicagoRegional_dj3_1.txt",
      "ChicagoRegional_dj3_2.txt"], None, 1e-5, 100, "oracle", large=True,
     solve_gap=1e-6, totals_gate=False, empties=3599)

# <CLASS TOLL FILE> (IVES-FORK.md 17).  SF_ct_exempt: class 2 (4 min/$) is
# exempt on the 8 tolled links incident to node 16 and pays $5 on the 8
# incident only to node 10; class 1 (0.5 min/$) pays the Toll column.  The
# oracle reads the same file.
SF2 = ["SiouxFalls_k_lo.txt", "SiouxFalls_k_hi.txt"]
case("SF_ct_exempt", SF, SF2, None, 1e-8, 300, "oracle", solve_gap=1e-9,
     class_toll="SiouxFalls_ct_exempt.txt")
# A file equal to the Toll column changes nothing (1 thread, byte-identical
# to the same run without the file) ...
case("SF_ct_same2", SF, SF2, None, 1e-9, 300, "same",
     class_toll="SiouxFalls_ct_same2.txt",
     ref=spec("SF_tolled_2class_nofile", SF, SF2, None, 1e-9, 300, "same"))
# ... single-class too, against the committed golden of stock SiouxFalls ...
case("SF_ct_same1", "SiouxFalls_net.txt", ["SiouxFalls_trips.txt"], None,
     1e-10, 300, "file", class_toll="SiouxFalls_ct_same1.txt",
     golden="SiouxFalls")
# ... and a class whose file column is zero everywhere routes as the
# toll-factor-0 class: lo + hi(zeroed by the file) == lo + free, every link
# row byte for byte -- the file is applied per class, not ignored.  Only the
# summary block differs, and must: the free class pays the Toll column (at
# zero weight), the zeroed class pays nothing, so their Toll revenue differs.
case("SF_ct_zero2", SF, SF2, None, 1e-9, 300, "same", rows_only=True,
     class_toll="SiouxFalls_ct_zero2.txt",
     ref=spec("SF_lo_free_nofile", SF,
              ["SiouxFalls_k_lo.txt", "SiouxFalls_k_free.txt"], None, 1e-9,
              300, "same"))
for _bad, _msg in [
        ("count", r"covers 75 links but the network has 76"),
        ("short", r"ended .* after 75 of 76 links"),
        ("extra", r"has data after its 76 links"),
        ("swapped", r"misaligned with the network at row 22: the file has "
                    r"\(16,8\), the network has \(8,16\)"),
        ("cols", r"row 30 has 3 toll columns; the run has 2 classes"),
        ("classes", r"has 3 classes but the parameters file gives 2"),
        ("nan", r"row 30: \"nan\" is not a finite toll")]:
    case("SF_ct_bad_" + _bad, SF, SF2, None, 1e-9, 300, "reject",
         class_toll="SiouxFalls_ct_bad_%s.txt" % _bad, expect=_msg)


def locate(fname):
    for d in (DATA, NET):
        p = os.path.join(d, fname)
        if os.path.exists(p):
            return p
    sys.exit("missing input %s (run test/make_fixtures.py)" % fname)


VEHLEN = "vehlen.txt"
COUNTER = re.compile(r"^Empty origins skipped: (\d+) of (\d+)", re.M)


def wants_vehlen(c, identity=False):
    return identity or c["kind"] == "ident"


def write_run(c, rundir, threads, vehlen=False):
    if os.path.isdir(rundir):
        shutil.rmtree(rundir)
    os.makedirs(rundir)
    files = [c["net"]] + c["trips"] + ([c["preload"]] if c["preload"] else [])
    files += [c["class_toll"]] if c["class_toll"] else []
    for f in files:
        os.symlink(locate(f), os.path.join(rundir, f))
    lines = ["<NETWORK FILE> %s" % c["net"]]
    lines += ["<TRIPS FILE> %s" % t for t in c["trips"]]
    if c["preload"]:
        lines.append("<PRELOAD FILE> %s" % c["preload"])
    if c["class_toll"]:
        lines.append("<CLASS TOLL FILE> %s" % c["class_toll"])
    lines += ["<FILE PATH> ./", "<DATA PATH> ./",
              "<CONVERGENCE GAP> %g" % c["solve_gap"],
              "<MAX ITERATIONS> %d" % c["maxit"],
              "<NUMBER OF THREADS> %d" % threads]
    if vehlen:
        lines.append("<ORIGIN VEH LENGTH FILE> %s" % VEHLEN)
    # <FLOWS FILE> stays last before the extras: readParametersFile processes
    # the file's last tag twice (IVES-FORK.md 18, F7), harmless for it.
    lines += ["<FLOWS FILE> flows.txt"] + c["extra"]
    with open(os.path.join(rundir, "params.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")


def run(binary, c, rundir, threads, vehlen=False):
    write_run(c, rundir, threads, vehlen)
    t0 = time.time()
    with open(os.path.join(rundir, "log.txt"), "w") as log:
        rc = subprocess.call([binary, "params.txt"], cwd=rundir, stdout=log,
                             stderr=subprocess.STDOUT)
    wall = time.time() - t0
    log = open(os.path.join(rundir, "log.txt")).read()
    its = re.findall(r"Iteration (\d+):(?: est\.)? gap ([0-9.eE+-]+)", log)
    return rc, wall, log, (int(its[-1][0]), float(its[-1][1])) if its else None


def log_extract(log):
    """The log lines G-ID1 compares: CLASS_GAP, and Iteration with the time
    and shift-count fields stripped (wall time varies; the shift count may
    differ by design -- empty bushes no longer count their zero shifts)."""
    out = []
    for line in log.splitlines():
        if line.startswith("Iteration "):
            out.append(re.sub(r"time [0-9.]+, -?\d+ shifts", "", line))
        elif line.startswith("CLASS_GAP"):
            out.append(line)
    return "\n".join(out) + "\n"


def counter(log):
    """(N, M) from the skip's counter line, or None if absent."""
    m = COUNTER.search(log)
    return (int(m.group(1)), int(m.group(2))) if m else None


def check_counter(c, log):
    """'' if the counter line printed the case's known empties, else why."""
    got = counter(log)
    if got is None:
        return "no 'Empty origins skipped' line"
    if got[0] != c["empties"]:
        return "skip counter %d, expected %d" % (got[0], c["empties"])
    return ""


def cmp_files(a, b):
    """Byte comparison via cmp(1); (identical?, cmp's output)."""
    p = subprocess.run(["cmp", a, b], capture_output=True, text=True)
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def ident_golden(c, what):
    # the log extract is ".gaps": .gitignore ignores *.log
    return os.path.join(GOLDEN, "ident", "%s.%s" % (
        c["golden"], "gaps" if what == "log" else what))


def md5(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def read_checksums():
    p = os.path.join(GOLDEN, "checksums.txt")
    out = {}
    if os.path.exists(p):
        for line in open(p):
            s = line.split()
            if len(s) == 2 and not line.startswith("#"):
                out[s[1]] = s[0]
    return out


def flows_table(path):
    """(tail,head) rows -> lists of floats, in file order."""
    return [[float(v) for v in l.split()[1:]] for l in open(path)
            if l.startswith("(")]


def selected(a):
    cs = CASES
    if a.only:
        names = set(a.only.split(","))
        cs = [c for c in cs if c["name"] in names]
    if a.skip_large:
        cs = [c for c in cs if not c["large"]]
    return cs


def cmd_golden(a):
    os.makedirs(GOLDEN, exist_ok=True)
    sums = read_checksums()
    for c in selected(a):
        if c["kind"] not in ("file", "md5", "ident") or c["class_toll"]:
            continue
        rd = os.path.join(a.work, "golden", c["name"])
        rc, wall, log, last = run(a.bin, c, rd, 1, wants_vehlen(c))
        if rc != 0:
            sys.exit("%s: tap-b exited %d" % (c["name"], rc))
        fl = os.path.join(rd, "flows.txt")
        if c["kind"] == "ident":
            os.makedirs(os.path.join(GOLDEN, "ident"), exist_ok=True)
            shutil.copyfile(fl, ident_golden(c, "flows"))
            shutil.copyfile(os.path.join(rd, VEHLEN), ident_golden(c, "vehlen"))
            with open(ident_golden(c, "log"), "w") as f:
                f.write(log_extract(log))
        elif c["kind"] == "file":
            shutil.copyfile(fl, os.path.join(GOLDEN, c["name"] + ".flows"))
        else:
            sums[c["name"]] = md5(fl)
        print("golden %-28s %s iter %s gap %s  %.1f s"
              % (c["name"], c["kind"], last[0], last[1], wall))
    with open(os.path.join(GOLDEN, "checksums.txt"), "w") as f:
        f.write("# md5 of flows.txt at <NUMBER OF THREADS> 1 (run_check.py)\n")
        for k in sorted(sums):
            f.write("%s %s\n" % (sums[k], k))
    return 0


def cmd_check(a):
    sums = read_checksums()
    fails = 0
    for c in selected(a):
        threads = 1 if c["kind"] != "oracle" else a.threads
        rd = os.path.join(a.work, "check-t%d" % threads, c["name"])
        rc, wall, log, last = run(a.bin, c, rd, threads, wants_vehlen(c))
        fl = os.path.join(rd, "flows.txt")
        tag = "%-28s t=%-2d" % (c["name"], threads)
        if c["kind"] == "reject":
            m = re.search(c["expect"], log)
            ok = rc != 0 and m is not None
            print("%s %s exit %d, %s" % ("ok  " if ok else "FAIL", tag, rc,
                  ("message: " + log[log.rfind("Fatal error"):].strip()
                   .splitlines()[0]) if m else
                  "expected message /%s/ not in log" % c["expect"]))
            fails += not ok
            continue
        if rc != 0 or last is None:
            print("FAIL %s tap-b exited %d" % (tag, rc))
            fails += 1
            continue
        info = "iter %d gap %.3e %.1f s" % (last[0], last[1], wall)
        why = check_counter(c, log)
        if why:
            print("FAIL %s %s  %s" % (tag, why, info))
            fails += 1
            continue
        info += ", skipped %d" % c["empties"]
        if c["class_toll"] and "Class tolls loaded" not in log:
            print("FAIL %s class toll file given but not loaded  %s"
                  % (tag, info))
            fails += 1
            continue
        if c["kind"] == "same":
            r = c["ref"]
            rrd = os.path.join(a.work, "check-t1", r["name"])
            rrc, rwall, rlog, rlast = run(a.bin, r, rrd, 1)
            if rrc != 0 or rlast is None:
                print("FAIL %s reference %s exited %d" % (tag, r["name"], rrc))
                fails += 1
                continue
            def body(path):
                b = open(path, "rb").read()
                if c["rows_only"]:
                    b = b"".join(l for l in b.splitlines(True)
                                 if l.startswith(b"("))
                return b
            ok = body(fl) == body(os.path.join(rrd, "flows.txt"))
            fired = "Class tolls loaded" in log
            ok = ok and fired
            print("%s %s %s %s %s%s  %s" % (
                "ok  " if ok else "FAIL", tag,
                "link rows" if c["rows_only"] else "flows",
                "==" if ok else "!=", r["name"],
                "" if fired else " (class tolls NOT loaded)", info))
            fails += not ok
        elif c["kind"] == "ident":
            res = []
            for what, path in [("flows", fl),
                               ("vehlen", os.path.join(rd, VEHLEN))]:
                res.append((what,) + cmp_files(path, ident_golden(c, what)))
            elog = os.path.join(rd, "log_extract.txt")
            with open(elog, "w") as f:
                f.write(log_extract(log))
            res.append(("log",) + cmp_files(elog, ident_golden(c, "log")))
            ok = all(r[1] for r in res)
            print("%s %s %s  %s" % ("ok  " if ok else "FAIL", tag,
                  ", ".join("%s %s" % (w, "==" if same else "!= (" + out + ")")
                            for w, same, out in res) + " 81d8e14 golden",
                  info))
            fails += not ok
        elif c["kind"] == "file":
            g = os.path.join(GOLDEN, c["golden"] + ".flows")
            ok = open(fl, "rb").read() == open(g, "rb").read()
            print("%s %s flows %s golden%s  %s" % (
                "ok  " if ok else "FAIL", tag, "==" if ok else "!=",
                "" if c["golden"] == c["name"] else " " + c["golden"], info))
            fails += not ok
        elif c["kind"] == "md5":
            got, want = md5(fl), sums.get(c["name"])
            ok = got == want
            print("%s %s md5 %s (golden %s)  %s" % ("ok  " if ok else "FAIL",
                  tag, got, want, info))
            fails += not ok
        else:
            args = [sys.executable, ORACLE, os.path.join(rd, c["net"]), fl]
            args += [os.path.join(rd, t) for t in c["trips"]]
            if c["preload"]:
                args += ["--preload", os.path.join(rd, c["preload"])]
            if c["class_toll"]:
                args += ["--class-toll", os.path.join(rd, c["class_toll"])]
            args += ["--max-gap", "%g" % c["gap"]]
            p = subprocess.run(args, capture_output=True, text=True)
            open(os.path.join(rd, "oracle.txt"), "w").write(p.stdout + p.stderr)
            ok = p.returncode == 0
            gaps = re.findall(r"ORACLE_CLASS_GAP (\d+) \S+ \S+ (\S+)", p.stdout)
            own = re.findall(r"CLASS_GAP (\d+) \S+ \S+ (\S+)", log)
            print("%s %s oracle gaps %s (tap-b CLASS_GAP %s; per-class "
                  "target %g, solve %g)  %s"
                  % ("ok  " if ok else "FAIL", tag,
                     " ".join("%.2e" % float(g) for _, g in gaps),
                     " ".join("%.2e" % float(g) for _, g in own) or "-",
                     c["gap"], c["solve_gap"], info))
            if not ok:
                print("     " + (p.stdout + p.stderr).strip().splitlines()[-1])
            fails += not ok
            if a.serial_bin:
                srd = os.path.join(a.work, "serial", c["name"])
                src, swall, slog, slast = run(a.serial_bin, c, srd, 1)
                if src != 0 or slast is None:
                    print("FAIL %s serial build exited %d" % (tag, src))
                    fails += 1
                    continue
                A, B = flows_table(fl), flows_table(os.path.join(srd,
                                                                 "flows.txt"))
                dtot = max(abs(x[0] - y[0]) for x, y in zip(A, B))
                dcls = max(abs(u - v) for x, y in zip(A, B)
                           for u, v in zip(x[2:], y[2:]))
                ok = len(A) == len(B) and (dtot <= a.flow_tol
                                           or not c["totals_gate"])
                if not c["totals_gate"]:
                    tag += " (totals report-only)"
                print("%s %s vs serial: max |total diff| %.3e (tol %g); "
                      "max |class diff| %.3e (not unique, reported only); "
                      "serial iter %d gap %.3e %.1f s"
                      % ("ok  " if ok else "FAIL", tag, dtot, a.flow_tol, dcls,
                         slast[0], slast[1], swall))
                fails += not ok
    print("%d failure(s)" % fails)
    return 1 if fails else 0


def cmd_identity(a):
    """G-ID1 against a live reference binary: every non-reject case at 1
    thread, new and reference, `cmp` on flows, origin veh-length and the log
    extract; the new binary's counter line must print the case's empties."""
    fails = 0
    for c in selected(a):
        if c["kind"] == "reject":
            continue
        if a.solve_gap:
            # A looser solve for the large cases at 1 thread: the trajectory
            # is compared step for step, so identity is as sharp per
            # iteration; only fewer iterations are covered.
            c = dict(c, solve_gap=a.solve_gap)
        out = {}
        for who, b in (("new", a.bin), ("ref", a.ref_bin)):
            rd = os.path.join(a.work, "identity", who, c["name"])
            rc, wall, log, last = run(b, c, rd, 1, vehlen=True)
            elog = os.path.join(rd, "log_extract.txt")
            with open(elog, "w") as f:
                f.write(log_extract(log))
            out[who] = (rd, rc, log, last)
        tag = "%-28s" % c["name"]
        if out["new"][1] != 0 or out["ref"][1] != 0:
            print("FAIL %s exit new %d ref %d" % (tag, out["new"][1],
                                                  out["ref"][1]))
            fails += 1
            continue
        lines = []
        ok = True
        why = check_counter(c, out["new"][2])
        if why:
            ok = False
            lines.append("counter: " + why)
        else:
            m = COUNTER.search(out["new"][2])
            lines.append("counter: " + out["new"][2][m.start():].splitlines()[0])
        for what in ("flows.txt", VEHLEN, "log_extract.txt"):
            pa = os.path.join(out["new"][0], what)
            pb = os.path.join(out["ref"][0], what)
            same, txt = cmp_files(pa, pb)
            ok = ok and same
            lines.append("cmp %s: %s" % (what, "identical (%d bytes)"
                         % os.path.getsize(pa) if same else txt))
        if out["new"][3] != out["ref"][3]:
            ok = False
        lines.append("iterations new %s ref %s" % (out["new"][3][0],
                                                   out["ref"][3][0]))
        print("%s %s %s" % ("ok  " if ok else "FAIL", tag, lines[0]))
        for l in lines[1:]:
            print("     " + l)
        fails += not ok
    print("%d failure(s)" % fails)
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["check", "golden", "identity", "list"])
    ap.add_argument("--bin", default=os.path.join(ROOT, "bin", "tap"))
    ap.add_argument("--ref-bin", help="identity: the reference binary")
    ap.add_argument("--solve-gap", type=float,
                    help="identity: override every selected case's solve gap")
    ap.add_argument("--serial-bin")
    ap.add_argument("--threads", type=int, default=1,
                    help="threads for the multi-class cases (single-class "
                         "cases always run at 1)")
    ap.add_argument("--flow-tol", type=float, default=0.1)
    ap.add_argument("--only")
    ap.add_argument("--skip-large", action="store_true")
    ap.add_argument("--work", default=os.path.join(ROOT, "scratch", "check"))
    a = ap.parse_args()
    a.bin = os.path.abspath(a.bin)
    if a.mode == "identity":
        if not a.ref_bin:
            sys.exit("identity needs --ref-bin")
        a.ref_bin = os.path.abspath(a.ref_bin)
        return cmd_identity(a)
    if a.serial_bin:
        a.serial_bin = os.path.abspath(a.serial_bin)
    if a.mode == "list":
        for c in CASES:
            print("%-28s %-6s %s%s" % (c["name"], c["kind"], c["gap"],
                                       "  (large)" if c["large"] else ""))
        return 0
    return cmd_golden(a) if a.mode == "golden" else cmd_check(a)


if __name__ == "__main__":
    sys.exit(main())
