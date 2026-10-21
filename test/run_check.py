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

The goldens were generated once by the shared binary that predates the
multi-class work (md5 a68c72f066aa9e019f273ea9ef711774, tap-b @3837de3) with
`run_check.py golden --bin <that binary>`; regenerate them only for a change
that is meant to move single-class flows, and say so in IVES-FORK.md.

Usage:
  run_check.py check  [--bin BIN] [--threads N] [--serial-bin SBIN]
                      [--only NAME[,NAME]] [--skip-large] [--work DIR]
  run_check.py golden --bin BIN [--work DIR]
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
         solve_gap=None):
    """gap: the target.  For single-class cases it is also the solve's
    <CONVERGENCE GAP>.  For multi-class cases it is the per-class gate the
    oracle applies to *every* class, and solve_gap is the (tighter) aggregate
    <CONVERGENCE GAP> tap-b stops on: tap-b's stopping rule pools all classes,
    weighted by each class's SPTT, so a class can sit above the aggregate (see
    IVES-FORK.md 15a)."""
    CASES.append(dict(name=name, net=net, trips=trips, preload=preload,
                      gap=gap, maxit=maxit, kind=kind, large=large,
                      extra=list(extra), solve_gap=solve_gap or gap))


for _n, _kind in [("SiouxFalls", "file"), ("Braess", "file"),
                  ("Anaheim", "file"), ("ChicagoSketch", "file"),
                  ("Barcelona", "md5")]:
    case(_n, "%s_net.txt" % _n, ["%s_trips.txt" % _n], None, 1e-10, 300, _kind)
    case(_n + "_preload10", "%s_net.txt" % _n, ["%s_trips.txt" % _n],
         "%s_preload10.txt" % _n, 1e-10, 300, _kind)
case("ChicagoRegional", "ChicagoRegional_net.txt",
     ["ChicagoRegional_trips.txt"], None, 1e-4, 100, "md5", large=True)

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
     "oracle", solve_gap=1e-13)
case("Chicago_2class", "ChicagoRegional_net.txt",
     ["ChicagoRegional_h2lo.txt", "ChicagoRegional_h2hi.txt"], None, 1e-5,
     100, "oracle", large=True, solve_gap=1e-6)
case("Chicago_3class", "ChicagoRegional_net.txt",
     ["ChicagoRegional_t3lo.txt", "ChicagoRegional_t3hi.txt",
      "ChicagoRegional_t3free.txt"], None, 1e-5, 100, "oracle", large=True,
     solve_gap=1e-6)


def locate(fname):
    for d in (DATA, NET):
        p = os.path.join(d, fname)
        if os.path.exists(p):
            return p
    sys.exit("missing input %s (run test/make_fixtures.py)" % fname)


def write_run(c, rundir, threads):
    if os.path.isdir(rundir):
        shutil.rmtree(rundir)
    os.makedirs(rundir)
    files = [c["net"]] + c["trips"] + ([c["preload"]] if c["preload"] else [])
    for f in files:
        os.symlink(locate(f), os.path.join(rundir, f))
    lines = ["<NETWORK FILE> %s" % c["net"]]
    lines += ["<TRIPS FILE> %s" % t for t in c["trips"]]
    if c["preload"]:
        lines.append("<PRELOAD FILE> %s" % c["preload"])
    lines += ["<FILE PATH> ./", "<DATA PATH> ./",
              "<CONVERGENCE GAP> %g" % c["solve_gap"],
              "<MAX ITERATIONS> %d" % c["maxit"],
              "<NUMBER OF THREADS> %d" % threads,
              "<FLOWS FILE> flows.txt"] + c["extra"]
    with open(os.path.join(rundir, "params.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")


def run(binary, c, rundir, threads):
    write_run(c, rundir, threads)
    t0 = time.time()
    with open(os.path.join(rundir, "log.txt"), "w") as log:
        rc = subprocess.call([binary, "params.txt"], cwd=rundir, stdout=log,
                             stderr=subprocess.STDOUT)
    wall = time.time() - t0
    log = open(os.path.join(rundir, "log.txt")).read()
    its = re.findall(r"Iteration (\d+):(?: est\.)? gap ([0-9.eE+-]+)", log)
    return rc, wall, log, (int(its[-1][0]), float(its[-1][1])) if its else None


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
        if c["kind"] == "oracle":
            continue
        rd = os.path.join(a.work, "golden", c["name"])
        rc, wall, log, last = run(a.bin, c, rd, 1)
        if rc != 0:
            sys.exit("%s: tap-b exited %d" % (c["name"], rc))
        fl = os.path.join(rd, "flows.txt")
        if c["kind"] == "file":
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
        rc, wall, log, last = run(a.bin, c, rd, threads)
        fl = os.path.join(rd, "flows.txt")
        tag = "%-28s t=%-2d" % (c["name"], threads)
        if rc != 0 or last is None:
            print("FAIL %s tap-b exited %d" % (tag, rc))
            fails += 1
            continue
        info = "iter %d gap %.3e %.1f s" % (last[0], last[1], wall)
        if c["kind"] == "file":
            g = os.path.join(GOLDEN, c["name"] + ".flows")
            ok = open(fl, "rb").read() == open(g, "rb").read()
            print("%s %s flows %s golden  %s" % ("ok  " if ok else "FAIL", tag,
                  "==" if ok else "!=", info))
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
                ok = len(A) == len(B) and dtot <= a.flow_tol
                print("%s %s vs serial: max |total diff| %.3e (tol %g); "
                      "max |class diff| %.3e (not unique, reported only); "
                      "serial iter %d gap %.3e %.1f s"
                      % ("ok  " if ok else "FAIL", tag, dtot, a.flow_tol, dcls,
                         slast[0], slast[1], swall))
                fails += not ok
    print("%d failure(s)" % fails)
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["check", "golden", "list"])
    ap.add_argument("--bin", default=os.path.join(ROOT, "bin", "tap"))
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
