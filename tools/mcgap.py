#!/usr/bin/env python3
"""Independent per-class equilibrium check for tap-b output (IVES FORK).

Stdlib-only Python 3: no numpy, no connectome environment.  It shares no code
with tap-b -- it reads the same input files and the flows file tap-b wrote,
and recomputes every class's gap from scratch -- so it can check the solver's
own convergence report rather than repeat it.

For each class k (one per <TRIPS FILE>, in parameters-file order):

    cost_k(a) = t_a(x_a + p_a) + tollFactor_k * toll_a + distFactor_k * length_a

  x_a   total assigned flow on arc a (all classes), from the flows file
  p_a   <PRELOAD FILE> background flow on a (0 without --preload); it enters
        only the travel-time function, exactly as in the fork (IVES-FORK.md 6)
  t_a   BPR: fft * (1 + B * ((x + p) / cap) ** power)
  tollFactor_k / distFactor_k   the trips file's <TOLL FACTOR> / <DISTANCE
        FACTOR>, defaulting to the network file's (as tap-b does)

  TSTT_k = sum_a x^k_a * cost_k(a)           (class-k flow, real arcs)
  SPTT_k = sum_od d^k_od * shortest_k(o, d)  (Dijkstra on cost_k; zone nodes
                                              below <FIRST THRU NODE> are not
                                              passed through)
  gap_k  = (TSTT_k - SPTT_k) / SPTT_k        (tap-b's RELATIVE GAP, per class)

Assumptions, stated because each one could make the check wrong:

  * Artificial arcs.  tap-b's makeStronglyConnectedNetwork appends artificial
    arcs (cost 99999) to make the network strongly connected.  The oracle
    knows only the network file's real arcs: it assumes no flow is assigned to
    an artificial arc, and that every OD pair with demand is connected by real
    arcs.  The multi-class flows file lists artificial arcs after the real ones
    and the oracle checks their flow is zero; the single-class file omits them,
    so there the assumption is not checked.  An unreachable OD pair is an error.
  * Arc identity is positional: the i-th real row of the flows file is the
    i-th link of the network file (repeated (tail, head) pairs are matched in
    order), and the preload file's rows are verified against (tail, head).
  * BPR only.  Every arc is evaluated with the BPR form above (all TNTP test
    networks and Connectome's export use it).
  * Demand is read as written; <DEMAND MULTIPLIER> is not applied.
  * Flows are as printed by tap-b (%f, 6 decimals), so the oracle's TSTT
    carries that rounding (~1e-11 relative on the L1 networks).

Usage:
    mcgap.py NET FLOWS TRIPS [TRIPS ...] [--preload FILE] [--max-gap G]

Prints one human-readable line and one parseable line per class:
    ORACLE_CLASS_GAP <class 1-based> <TSTT> <SPTT> <gap>
With --max-gap, exits 1 if any class's gap exceeds G.
"""
import argparse
import collections
import heapq
import re
import sys

_META = re.compile(r"\s*<([^>]+)>\s*(.*)")


def read_network(path):
    meta, arcs, in_data = {}, [], False
    with open(path) as f:
        for line in f:
            m = _META.match(line)
            if m and not in_data:
                meta[m.group(1).strip()] = m.group(2).strip()
                if m.group(1).strip() == "END OF METADATA":
                    in_data = True
                continue
            if not in_data:
                continue
            s = line.strip()
            if not s or s.startswith("~"):
                continue
            t = s.replace(";", " ").split()
            tail, head = int(t[0]), int(t[1])
            cap, length, fft, b, power, _speed, toll = map(float, t[2:9])
            arcs.append((tail, head, cap, length, fft, b, power, toll))
    return meta, arcs


def read_flows(path, arcs):
    """Total and per-class flow for each real arc, positionally."""
    rows = []
    with open(path) as f:
        for line in f:
            if line.startswith("("):
                key, *vals = line.split()
                rows.append((key, [float(v) for v in vals]))
    n = len(arcs)
    if len(rows) < n:
        sys.exit("flows file has %d arc rows, network has %d" % (len(rows), n))
    for i, (a, b, *_ ) in enumerate(arcs):
        if rows[i][0] != "(%d,%d)" % (a, b):
            sys.exit("flows row %d is %s, network link is (%d,%d)"
                     % (i + 1, rows[i][0], a, b))
    extra = rows[n:]   # artificial arcs (multi-class file only)
    for key, vals in extra:
        if abs(vals[0]) > 1e-9:
            sys.exit("artificial arc %s carries flow %g: the oracle's "
                     "real-arcs-only assumption does not hold" % (key, vals[0]))
    return [vals for _, vals in rows[:n]]


def read_preload(path, arcs):
    p, k = [], 0
    in_data = False
    with open(path) as f:
        for line in f:
            if "<END OF METADATA>" in line:
                in_data = True
                continue
            s = line.strip()
            if not in_data or not s or s.startswith("~"):
                continue
            t = s.replace(";", " ").split()
            a, b, v = int(t[0]), int(t[1]), float(t[2])
            if (a, b) != arcs[k][:2]:
                sys.exit("preload row %d is (%d,%d), network link is (%d,%d)"
                         % (k + 1, a, b, arcs[k][0], arcs[k][1]))
            p.append(v)
            k += 1
    if k != len(arcs):
        sys.exit("preload file has %d rows, network has %d" % (k, len(arcs)))
    return p


def read_trips(path):
    txt = open(path).read()
    meta = dict((m.group(1).strip(), m.group(2).strip())
                for m in re.finditer(r"<([^>]+)>[ \t]*([^\n]*)", txt))
    demand = {}
    for blk in re.split(r"Origin", txt)[1:]:
        o = int(blk.split()[0])
        d = {}
        for j, v in re.findall(r"(\d+)\s*:\s*([0-9.eE+-]+)", blk):
            d[int(j)] = d.get(int(j), 0.0) + float(v)
        demand[o] = d
    return meta, demand


def bpr(arc, x):
    _a, _b, cap, _len, fft, b, power, _toll = arc
    return fft * (1 + b * (x / cap) ** power)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("net")
    ap.add_argument("flows")
    ap.add_argument("trips", nargs="+")
    ap.add_argument("--preload")
    ap.add_argument("--max-gap", type=float)
    a = ap.parse_args()

    meta, arcs = read_network(a.net)
    ftn = int(meta.get("FIRST THRU NODE", "1"))
    tf0 = float(meta.get("TOLL FACTOR", "0") or 0)
    df0 = float(meta.get("DISTANCE FACTOR", "0") or 0)
    flows = read_flows(a.flows, arcs)
    pre = read_preload(a.preload, arcs) if a.preload else [0.0] * len(arcs)
    ncls = len(a.trips)
    time = [bpr(arcs[i], flows[i][0] + pre[i]) for i in range(len(arcs))]
    adj = collections.defaultdict(list)
    for i, arc in enumerate(arcs):
        adj[arc[0]].append(i)

    worst = 0.0
    for c, tpath in enumerate(a.trips):
        tmeta, demand = read_trips(tpath)
        tf = float(tmeta["TOLL FACTOR"]) if "TOLL FACTOR" in tmeta else tf0
        df = (float(tmeta["DISTANCE FACTOR"]) if "DISTANCE FACTOR" in tmeta
              else df0)
        cost = [time[i] + tf * arcs[i][7] + df * arcs[i][3]
                for i in range(len(arcs))]
        # multi-class rows: (t,h) total time class1 class2 ...; single: (t,h) x
        col = 2 + c if ncls > 1 else 0
        xk = [flows[i][col] for i in range(len(arcs))]
        tstt = sum(xk[i] * cost[i] for i in range(len(arcs)))
        sptt = dem = 0.0
        for o, dests in demand.items():
            if not any(v > 0 for j, v in dests.items() if j != o):
                continue
            dist = {o: 0.0}
            pq = [(0.0, o)]
            while pq:
                d, u = heapq.heappop(pq)
                if d > dist.get(u, float("inf")):
                    continue
                if u != o and u < ftn:
                    continue  # zones are not through nodes
                for i in adj[u]:
                    v = arcs[i][1]
                    nd = d + cost[i]
                    if nd < dist.get(v, float("inf")):
                        dist[v] = nd
                        heapq.heappush(pq, (nd, v))
            for j, v in dests.items():
                if v > 0 and j != o:
                    if j not in dist:
                        sys.exit("class %d: OD (%d,%d) has demand %g but no "
                                 "path over real arcs" % (c + 1, o, j, v))
                    sptt += v * dist[j]
                    dem += v
        gap = (tstt - sptt) / sptt
        worst = max(worst, gap)
        tolled = sum(xk[i] for i in range(len(arcs)) if arcs[i][7] > 0)
        print("class%d tollF=%g distF=%g demand=%.1f TSTT=%.10g SPTT=%.10g "
              "relgap=%.3e flow_on_tolled=%.1f"
              % (c + 1, tf, df, dem, tstt, sptt, gap, tolled))
        print("ORACLE_CLASS_GAP %d %.15g %.15g %.10e" % (c + 1, tstt, sptt, gap))
    if a.max_gap is not None and worst > a.max_gap:
        print("FAIL: worst class gap %.3e > %.3e" % (worst, a.max_gap))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
