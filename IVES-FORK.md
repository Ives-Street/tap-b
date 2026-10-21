# Ives Street fork of tap-b

This is [Ives Street](https://ives.st)'s fork of [`spartalab/tap-b`](https://github.com/spartalab/tap-b),
vendored for use as a bush-based user-equilibrium engine inside
[Connectome](https://github.com/Ives-Street/connectome)'s Stage 3.5 traffic
assignment. Upstream is MIT-licensed; so is this fork.

Almost nothing here changes the algorithm. The patches are two build fixes,
three performance fixes, the parameters that make the switchable ones
switchable, one genuine feature — a per-link fixed background flow that
upstream has no way to express — and two correctness fixes: the default
bush-update scan is `LONGEST_BUSH_PATH`, because the upstream default can build
a cyclic bush and crash (section 10); and multi-class runs no longer switch one
class's tolls in under another class's queued work (section 14). Section 16 is
a memory fix: the parallel build's scratch is per worker thread, not per
origin. Section 17 is a second input feature: an optional per-class toll on
every link, the seam an exempt class (HOV) will use.

**Base commit:** `040135a20c771fbb84766df6a97cff981fa5df4b` (upstream `master`,
2024-09-22). **Our branch:** `ives/main`. Upstream stays as the `upstream`
remote; we do not rebase automatically — the pin is the point.

## Build

```sh
make            # parallel build (default), gcc 15 clean
make serial     # single-threaded build
make test       # direct checks on the preloaded performance functions
make check      # L1 regression ladder: golden flows + per-class oracle (§15b)
make check-quick  # the same without the ChicagoRegional cases
```

The binary lands at `bin/tap`. No cmake, no autotools, no Python environment —
Connectome shells out to this binary and does not link against it.

## A note on reproducibility

The parallel build is **not** bit-reproducible run to run: flow updates
accumulate into shared arc records in thread-completion order. Measured on
SiouxFalls at convergence gap 1e-8, three repeats of an identical input spanned
iteration counts 18–32 and link flows ~2e-2 vph. That is far inside any
tolerance a study cares about, but it makes a single-run A/B **unattributable** —
two runs of the same binary differ by about as much as two runs of different
ones. Every equivalence claim below is therefore measured at
`<NUMBER OF THREADS> 1`, where pool dispatch order is fixed and the build is
deterministic.

## What we changed, and why

### 1. `Makefile` — pin the C dialect to gnu17

gcc 15 defaults to C23, where `bool` is a keyword; `include/utils.h` typedefs
its own, which is a hard error in every translation unit. `-std=gnu17` is the
dialect the source was written for. One line, no source change.

### 2. `src/bush.c` — pooled per-origin SPTT in `bushSPTT`

Upstream's gap evaluation is a serial per-origin loop (`scanBushes` +
`BellmanFord_NoLabel`), while the *solve* it measures is fully pooled. On a
Washington DC network (1,814 origins × ~85k nodes, 195k arcs) we measured the
gap evaluation at **~35 minutes** against 14–52 s of actual solver work per
iteration — the instrument cost 40× the measurand.

Each origin's work is independent in the parallel build (`scanBushes_par` and
`BellmanFord_NoLabel` write only that origin's `SPcost_par` row; the queue is
function-local), so we dispatch it through tap-b's own thread pool in exactly
the shape `updateBatchBushes` already uses. Measured on the same DC network:
gap evaluation **~35 min → ~18 s average**.

The fast path is taken only when it is provably equivalent — bins off
(below). It was single-class only (`numClasses == 1`) until section 15 made it
class-aware. Anything else falls through to the stock serial loop, unmodified. Serial builds
(`make serial`) are untouched: the whole block is inside `#ifdef PARALLELISM`.

Each engagement prints `Parallel SPTT path engaged (N origins)` at
`LOW_NOTIFICATIONS`. That line is deliberate: its *absence* from a log means the
serial loop ran, which is the only cheap way to tell a fast path from a no-op.

**Equivalence check** (SiouxFalls, convergence gap 1e-13, pooled vs. the stock
serial loop forced on via `<CALCULATE BINS>`): link flows agree to 1.0e-6 vph
absolute / 4.3e-11 relative, aggregate TSTT to 12 significant digits. The
residual is floating-point non-associativity — the pooled reduction sums SPTT in
thread-completion order — and it shows up as a differing iteration count near
1e-13, where the gap trajectory is already numerical noise. At the tolerances
anyone solves at (1e-4 … 1e-6) it is invisible.

**Re-checked with the preload active** (2026-08-19). The preload below changes
the link costs `bushSPTT` evaluates, and the two patches compose inside the same
function, so equivalence of each alone does not imply equivalence together. At
`<NUMBER OF THREADS> 1` — see the reproducibility note above; at 8 threads the
same comparison showed 22–84 vph "differences" that were entirely the build's
own run-to-run spread — pooled and stock-serial `bushSPTT` produce **bit-identical
flows** (max |diff| 0.000e+00 vph) on SiouxFalls, Anaheim, ChicagoSketch and
Barcelona, each at preload 0 %, 10 % and 30 % of link capacity.

One case worth recording: Anaheim at 30 % preload stalls at gap ~1.9e-8 and never
reaches 1e-12, in *both* arms identically. That is a precision floor under heavy
background flow, not a divergence between the two paths, and it sits four orders
of magnitude past any target a study uses.

### 3. `src/bush.c` + `src/fileio.c` — `calculateBins` becomes a parameter, default off

Reduced-cost bins feed nothing but `displayMessage(DEBUG, ...)` output in
`bushSPTT`, but computing them is O(origins × arcs) *and* their upstream default
of `TRUE` silently routes around the pooled path above — the bins need the
per-origin arc scan the fast path skips. We default them **off** and add a
parameters-file keyword to switch them back on:

```
<CALCULATE BINS>
```

which restores upstream behaviour exactly (bins computed, serial `bushSPTT`).

### 4. `Makefile` — actually include the generated dependency files

The dependency-tracking rule ended with

```make
include $(wildcard $(patsubst %,$(DEPDIR)/%.d,$(basename $(SRCS))))
```

but `SRCS` is never defined anywhere — the variable holding the source list is
`SOURCES`. So every `.d` file was written and none was ever read, and an
incremental build ignored header changes completely.

This is worse than a stale object file. Editing `include/networks.h`, which is
where `arc_type` lives, recompiled only the `.c` files touched in the same edit
and linked them against objects still using the *previous* struct layout — every
arc field read at the wrong offset, and a segfault somewhere with no relation to
the edit. We lost an afternoon to it. Fixed to `$(basename $(notdir $(SOURCES)))`;
touching `networks.h` now rebuilds the seven objects that include it.

### 5. `src/bush.c` + `src/parallel_bush.c` — pooled initial bush construction

`initializeBushesB` builds one shortest-path tree per origin in a serial loop,
while the update phase it feeds is fully pooled. On the DC network that cold
start was measured at **14.8 minutes** — paid once per scenario entry and after
every topology-changing mutation.

The only thing making the origins dependent was the single shared `bushes->SPcost`
scratch vector; `initialBushShortestPath_par` writes this origin's `SPcost_par`
row instead, and everything else the build touches (`pred[origin]`,
`bushOrder[origin]`, `numMerges[origin]`, `lastMerge[origin]`) is already
per-origin upstream. `arcIndexBellmanFord` and `genericTopologicalOrder` allocate
their own queues and read the network read-only. The work then goes through the
same thread pool, in the same shape `updateBatchBushes` uses.

The fast path is taken only where it is provably the same computation: both
`createInitialBush` and `topologicalOrder` still pointing at the stock
implementations — the ones audited for per-origin independence. (It was also
single-class only, so the serial loop's `changeFixedCosts` sequencing collapsed
to one hoisted call, until section 15 made it class-aware.)
Anything else falls through to the untouched loop, as do serial builds. Each
engagement prints `Parallel bush init engaged (N origins)`.

```
<SERIAL BUSH INIT>
```

forces the stock loop in a parallel build, so the two can be compared with one
variable changed.

**Equivalence check.** At `<NUMBER OF THREADS> 1`, pooled and serial
initialisation give **identical gap trajectories to the last printed digit and
identical link flows** (max |diff| 0.000e+00 vph) on SiouxFalls, Braess, Anaheim,
ChicagoSketch, Barcelona and Austin_sdb (18,710 links), each with and without
preload. This is a stronger result than patch 2's, and expected to be: the two
paths build the *same* set of independent per-origin trees, so unlike a summation
they have no association to reassociate.

**Measured, 12 threads, wall clock:**

| network | origins × nodes × arcs | serial init | pooled init | |
|---|---|---|---|---|
| Austin_sdb | 1,117 × 7,466 × 18,710 | 7.47 s | 0.90 s | **8.3×** |
| ChicagoRegional | 1,790 × 12,982 × 39,018 | 6.76 s | 1.10 s | **6.1×** |

Note that CPU time is roughly *constant* across the two arms (7.5 → 8.8 s,
6.8 → 7.7 s) — the same work, spread — which is why patch 7 below exists.

### 6. `src/tap.c`, `src/fileio.c`, `include/networks.h` — per-link background preload

The one genuine feature. Connectome's Stage 3.5 carries a fixed background flow
per link (external-through traffic and PCE-weighted trucks) that congests the
network but belongs to no OD matrix and never reroutes. Upstream has no way to
express it, and it is not reachable by rewriting per-link α/β: the BPR curve has
to be evaluated at `assigned + preload` while the *assignment* still sees only
the assigned flow.

`arc_type` gains a `preload` field, default 0, and the nine BPR functions
(`general`/`linear`/`quartic` × cost/derivative/integral) evaluate at
`arc->flow + arc->preload`. It enters **only** there. Flow conservation, bush
flows, `TSTT` (which sums `classFlow × cost`) and the demand side of `SPTT` all
keep seeing assigned flow alone — which is exactly what makes the reported
relative gap the gap of the preloaded problem, with no separate patch to the gap
computation. It also matches AequilibraE's `add_preload` semantics, so the two
engines can be compared directly.

Two subtleties:

- The **derivative** needs no chain-rule factor (`d(x+p)/dx = 1`) but must be
  evaluated at `x + p`; a der that dropped the preload would still converge to
  the same equal-cost fixed point, just more slowly. That is why it cannot be
  validated by a solve — see the test note below.
- The **integral** is the one place preload is not a substitution. Beckmann's
  objective is `∫₀ˣ t(p + w) dw = T(p + x) − T(p)`, not `T(p + x)`. Written in
  that difference form, it reduces to upstream's expression at `p = 0`.

The preload is supplied by a new optional parameter,

```
<PRELOAD FILE> mynet_preload.txt
```

whose format is one row per link **in network-file order**:

```
<NUMBER OF LINKS> 4916
<END OF METADATA>
~ Init Term Preload ;
1 2 12.5 ;
```

The `(tail, head)` on each row is *verified* against the arc it lands on rather
than used as a key — real networks carry genuine parallel arcs between the same
node pair, so the format has to be positional, and a file shifted by one row
would otherwise put someone else's trucks on this link and converge beautifully
to the wrong problem. A link carrying nonzero preload under a non-BPR performance
function is refused rather than silently ignored. Loading prints
`Preload loaded: N of M links carrying background flow, X veh total.`

**Tests.** `make test` runs `test/bpr_preload_test.c` — 214 direct checks on the
nine functions: closed forms, the substitution property `cost(x, p) == cost(x+p, 0)`,
central finite differences of cost against each derivative, quadrature against
each integral, and explicit *rejections* of the two plausible wrong integrals
(`T(p+x)` and `T(x)`). Five deliberate mutations (preload dropped from
`effectiveFlow`, from `generalBPRder`, from `quarticBPRder`; `generalBPRint`
returning `T(p+x)`; `linearBPRint` dropping the cross term) each fail between 12
and 51 checks, so the test sees what it claims to.

A solve alone could not have done this. The converged flows are moved by the
*cost*, so they test that; but a derivative or an integral that ignored the
preload produces the identical converged answer, which is the "no-op change with
a perfect A/B" shape.

End to end, Connectome's harness solves a two-route network with a closed-form
equilibrium at β ∈ {1, 4, 2.5} and preload ∈ {0, 50, 150}: all nine cases match a
root-found reference to ~1e-7 vph, with the preload moving the split by 50–76
vehicles.

### 7. `src/bush.c` — hoist the cost update out of the per-origin loading loop

`initializeAlgorithmB`'s second loop evaluated every arc's performance function
once per origin — `numOrigins × numArcs` `pow()` calls, 3.5e8 on the DC network —
of which only the last pass survives. `calculateCost` is a pure function of the
arc's own flow and parameters and nothing in `rectifyBushFlows` reads arc costs,
so one pass after the loop leaves exactly the same values behind.

Verified against a build with the hoist reverted: identical gap trajectories and
**byte-identical flows** across SiouxFalls, Braess, Anaheim and ChicagoSketch,
with and without preload. Worth ~7 % of cold-start initialisation, which is
smaller than it looks — the remaining serial term is the loading loop itself.

### 8. `src/bush.c` — report wall-clock initialisation time

`Initialization done in %.3f s` was measured with `clock()`, which is processor
time summed across threads. Once initialisation is pooled that number *rises*
while the run gets faster — on ChicagoRegional it read 6.65 s serial and 8.00 s
pooled, i.e. exactly backwards. The line now reads
`Initialization done in W s (C s CPU)`, wall clock first; their ratio is achieved
parallelism.

### 9. `src/bush.c` — warm-start marker line

A `<WARM START>` run that silently falls back to a cold bush build produces the
same equilibrium and shows up only as lost minutes — invisible in the results,
exactly the shape of failure the preload and pooled-init markers exist for. The
warm-start branch of `initializeAlgorithmB` now prints
`Warm start: bushes loaded from <file>` at `LOW_NOTIFICATIONS` after
`readBushes` returns, so a caller can assert the warm start actually engaged
rather than infer it from initialisation time. (`readBushes` itself only
validates the stored batch size, not the network topology — a caller feeding it
bushes from a different network of the same origin count gets undefined
behaviour, so callers must key stored bushes on the network they came from.)

### 10. `src/bush.c`, `src/parallel_bush.c`, `src/fileio.c` — full LP labels by default, loose criterion decoupled

The one correctness fix, and the only patch that changes the algorithm's
default behaviour. Upstream's default `updateBushScanType` is
`LONGEST_USED_OR_SP`, under which `updateBushB` can build a **cyclic bush** and
die in `genericTopologicalOrder` with `Graph given to bushTopologicalOrder
contains a cycle`. We hit this reproducibly on a Washington DC network
(1,814 origins × 195k arcs, preloaded): three independent runs, three different
reporting origins, the same 70 unorderable nodes.

**The mechanism.** All three add/preserve branches in `updateBushB` justify
themselves through LP labels. The strict criterion's `LPcost[i] < LPcost[j]`
guard excludes cycles only if LP order is a topological order of the *whole*
bush — i.e. only if every bush arc constrains `LPcost`. Under
`LONGEST_USED_OR_SP`, a node with no longest-*used* path falls back to its SP
label, so LP labels on different nodes sit on different scales and comparing
them means nothing. The hole is widest in the shortest-path-tree re-add branch
(preserve an in-bush zero-flow arc on SP equality), which tests **nothing about
LP order at all**: in the DC crash it preserved an arc running backwards in LP
(70.3366 → 64.2778), and the strict criterion then closed an 8-arc cycle
through it under a valid-looking guard. With `LONGEST_BUSH_PATH` every bush arc
constrains `LPcost`, LP order is a genuine topological order, and all three
branches become sound at once.

Upstream has been here twice — `topcyclebug` (c946e08) commented the loose
fallback criterion out entirely over an "updateBush Cycle issue", and
`updatebushcycle` (7ceb5c0) re-gated it on
`newArcs == 0 && updateBushScanType == LONGEST_BUSH_PATH` — closing the
soundness condition on one branch while leaving it open on the other two.

**Why the loose criterion is decoupled rather than left riding on the scan
type.** Upstream's gate makes switching to `LONGEST_BUSH_PATH` *silently
activate* the loose criterion, and that combination **hard-stalls SiouxFalls**:
gap pinned at exactly 2.1100e-3 from iteration ~20 onward, identical shift
count every iteration, no progress. So the obvious one-line fix — flip the
default scan type — walks into a convergence failure through a gate that looks
like a safety condition. The loose criterion now hangs on its own parameter,
`useLooseCriterion`, default off; changing the scan type moves nothing else.
A future reader who "simplifies" this back onto the scan type re-introduces
the stall.

**Cost of the fix,** one thread, deterministic, `<CONVERGENCE GAP> 1e-4` —
iteration counts and final gaps, old default → new default:

| network | `LONGEST_USED_OR_SP` | `LONGEST_BUSH_PATH` |
|---|---|---|
| SiouxFalls | 6 iters, 6.6e-5 | 7 iters, 5.1e-5 |
| Anaheim | 3 iters, 2.0e-5 | 4 iters, 8.0e-5 |
| ChicagoSketch | 5 iters, 3.7e-5 | 5 iters, 7.7e-5 |
| ChicagoRegional | 9 iters, 5.3e-5 | 11 iters, 6.1e-5 |

Two new parameters-file tags keep every prior behaviour reachable without a
rebuild:

```
<UPDATE BUSH SCAN> LONGEST_USED_OR_SP    ~ or LONGEST_BUSH_PATH,
                                         ~ LONGEST_USED_PATH, NO_LONGEST_PATH
<LOOSE CRITERION>
```

The scan type was previously not settable from a parameters file at all. The
non-default scans reproduce the unsound-label condition above; they exist for
A/B against the old behaviour, not for production use.

### 11. `src/bush.c`, `src/parallel_bush.c` — cycle diagnostics and a self-test hook

Permanent test tooling, built to diagnose the crash in section 10 and kept
because the next cycle bug should cost minutes, not another instrumented DC
run. Two pieces, both inert in normal operation:

- **A cycle dump at the failure site.** `genericTopologicalOrder` previously
  died reporting only a placement count, which cannot distinguish a genuine
  directed cycle from corrupted merge bookkeeping, and named no links. It now
  writes `f1_cycle_dump.txt` before dying: the unplaced set with labels, an
  actual cycle walked out of it arc by arc (SP/LP labels, flow, preload, which
  add/preserve branch added each arc this call), and summary verdict inputs.
  The walk is guaranteed to terminate in a cycle if one exists, and reports
  corrupted bookkeeping explicitly if it stalls instead.
- **`<SELFTEST INJECT CYCLE>`** — a parameters-file tag that, once per run,
  injects a genuine two-cycle into a bush by adding the reverse of an in-bush
  arc. `reconstructMerges` accepts it, the topological order fails, and the
  dump path runs end to end on real data structures in seconds on SiouxFalls.
  Breaks the solve by design; never enabled by a normal run. This is how the
  dump itself is validated — a diagnostic you cannot trigger on demand is a
  diagnostic you cannot trust.

Instrumentation cost was measured before promotion: diag and stock binaries at
one thread produce identical gap, objective and shift count at every iteration
on SiouxFalls, Anaheim and ChicagoSketch, and per-iteration solver times on DC
within 1–4%.

### 12. `src/bush.c` — single-batch `<STORE BUSHES>` writes once, at the end

With one batch, nothing reads the stored bushes back during a run (`loadBatch`
skips the read at `numBatches == 1`), so only the final state matters — it is
what the warm-start cache consumes. Upstream's in-loop `storeBatch` call
nevertheless rewrote the full bush file every iteration, inside the timed
region: 1.35 GB × 40 iterations ≈ **54 GB written per DC solve**. The write is
now deferred to after the convergence loop for the single-batch case;
multi-batch runs still store in-loop, where the write is load-bearing (each
batch's state must persist while the others occupy memory).

Explicitly **not** a performance claim: measured via `/proc/PID/io` against a
disk benchmarking at 965 MB/s, the waste cost ~11 s of disk time per solve, 0
major faults, 0 swap. This is hygiene — SSD wear and disk pressure, not speed.
A side effect worth knowing: a run that dies mid-solve now leaves no
freshly-written batch file rather than the previous iteration's — absence is
cleaner than a mid-convergence snapshot for anything keying a warm-start cache
on it.

### 13. `src/bush.c`, `src/fileio.c` — per-origin assigned vehicle-length

A reporting output, no change to the solve. Connectome attributes vehicle-miles
to the residents who drive them, but its trip-based figure prices each OD trip
at one fastest-path skim distance and so cannot see equilibrium route
splitting; the bushes can. An opt-in parameters tag,

```
<ORIGIN VEH LENGTH FILE> origin_vehlen.txt
```

writes, after convergence, one row per origin (× class, in origin order):

```
<NUMBER OF ORIGINS> 24
<NUMBER OF ZONES> 24
<NUMBER OF CLASSES> 1
<DESTINATIONS COUNTED> 24
<END OF METADATA>

~	Origin	Class	Demand	VehLength	ZoneArcVehLength	;
1	1	8800	0	144169.53549175855	;
```

`VehLength` is Σ over arcs of that origin's bush flow × the arc's `Length`, on
arcs with neither end at a zone node; `ZoneArcVehLength` is the same sum on
arcs touching a zone. Under a layout where zones are pure centroids
(Connectome's export, `<FIRST THRU NODE> = zones + 1`) the second column is
exactly the centroid connectors; on SiouxFalls, where every node is a zone,
everything lands there. Units are the network file's flow and length units.
Bush flows are assigned flow alone, so `<PRELOAD FILE>` background flow is
excluded by construction.

```
<ORIGIN VEH LENGTH DESTINATIONS> n
```

restricts each row to the demand bound for destinations 1..n (default: every
zone). Connectome folds internal-to-external trips (zone → gateway centroid)
into the same origins' bushes as residents' trips, and numbers analysis zones
before gateways, so n = analysis zones keeps only the trips Connectome's
residents make. The restricted flows come from a descending pass that loads
only that demand and splits it at each merge in the proportions of the bush's
own approach flows — the proportional decomposition, under which all of an
origin's destinations share its merge splits. With every zone counted it is
the pass `calculateBushFlows` makes. Checked against the independent
`<PATH FLOWS FILE>` decomposition on SiouxFalls with n = 12 of 24: every
origin agrees to 5e-10 relative (the path file prints flows at `%f`).

The pass costs the same as the `calculateBushFlows` pass `bushTSTT` runs
every iteration — O(origins × (nodes + arcs)), serial, once. The end-of-run line
`Origin veh-length written: N origins, A network + B zone-arc veh-length
(aggregate link flows: C)` reports A + B against the aggregate Σ flow × Length
so a caller can assert the reconciliation (it holds only with every zone
counted). Measured: A + B = C to all ten
printed digits on SiouxFalls single-class (24 origins) and two-class (48 rows).
Single batch only, like `<PATH FLOWS FILE>`.

### 14. `src/bush.c`, `src/fileio.c` — multi-class: the class-switch race, and three bookkeeping fixes

tap-b's multi-class model is upstream's and unchanged: one `<TRIPS FILE>` per
class, in parameters-file order, each with its own `<TOLL FACTOR>` and
`<DISTANCE FACTOR>`; one pseudo-origin and one bush per (zone, class); one
shared link travel time evaluated on total flow. Each class's fixed cost
(`tollFactor_c × toll + distanceFactor_c × length`) is precomputed per arc, and
`changeFixedCosts(c)` copies it into the single shared `arc.fixedCost`, shifting
`arc.cost` by the difference. The algorithm therefore works on one class at a
time, against a network whose cost fields hold that class's prices.

**The race.** The parallel dispatchers (`updateBatchBushes`, both passes, and
`updateBatchFlows`) queued all of class 0's origins on the thread pool and then
called `changeFixedCosts(1)` immediately — while class 0's origins were still
queued or running. Those origins then updated their bushes and shifted flow
against class 1's tolls. It does not take a thread race: the pool is
asynchronous to the dispatcher, so it happens at `<NUMBER OF THREADS> 1` too.
Two classes with *identical* prices converge (the swap changes nothing), so
the defect needs classes that differ in price — which is the use case.

Measured on a tolled SiouxFalls (16 links at $5 — every link incident to node
10 or 16; classes at 0.5 and 4 min/$; `<CONVERGENCE GAP> 1e-8`; per-class gaps
from the independent oracle `tools/mcgap.py`, §15b):

| build | 2 classes: iterations | class 1 / 2 gap | 3 classes (0.5 / 4 / 0): iterations | class 1 / 2 / 3 gap |
|---|---|---|---|---|
| 3837de3, 1 thread | 300 (cap) | 1.0e-3 / 1.2e-2 | 300 (cap) | 4.1e-4 / 3.9e-4 / 1.5e-3 |
| 3837de3, 12 threads | 300 (cap) | 7.0e-4 / 1.0e-2 | 300 (cap) | 1.1e-4 / 6.8e-5 / 4.0e-4 |
| serial build (`make serial`), this branch | 16 | 1.4e-8 / 5.1e-9 | 26 | 9.8e-9 / 6.6e-9 / 2.6e-9 |
| this branch, 1 thread | 14 | 4.9e-9 / 3.5e-9 | 25 | 1.0e-8 / 1.1e-8 / 6.8e-9 |
| this branch, 12 threads (two runs) | 17 / 16 | ≤ 6.9e-9 | 27 / 22 | ≤ 1.7e-8 |

(Per-class gaps a little above the 1e-8 target at an aggregate gap below it are
expected — the stopping rule is the aggregate; see §15a.) On a 2-class
ChicagoRegional (1,790 zones, 39,018 arcs, 92 tolled links) the race does not
stall but **crashes**, in iteration 2: `Cannot have non-origin node 6322 in bush
… without incoming contributing links` (scoping memo 2026-10-21, §2.3, 8 threads). Reproduced on 3837de3 at 12 threads: `Cannot have non-origin node 1961 in bush 6 …` before the first iteration completed — the node varies run to run; this branch solves the same input to per-class gaps ≤ 8.6e-7.

**Fix (T1).** `thpool_wait()` before every `changeFixedCosts` reached from a
pooled path, so the previous class's work drains before the next class's
prices go in. Origins are class-ordered (`origin = class × zones + zone`), so
this is one drain per class per pass, not per origin.

**Three bookkeeping fixes found alongside.**

- `updateBatchFlows`' parallel branch never advanced `lastClass`, so every
  origin of a class other than the one the loop entered with re-ran the
  O(arcs) `changeFixedCosts` — O(origins × arcs) per inner iteration. It now
  advances it, as the serial branch always did.
- The gap evaluation (`bushRelativeGap` → `bushSPTT`, and `TSTT` through
  `classGeneralizedCost`) switches class costs itself and leaves the network on
  the *last* class's costs, without telling `AlgorithmB`'s `lastClass`. When the
  last origin `updateBatchFlows` touched belonged to an earlier class — the
  last class's bushes had all converged, so `updateBush` skipped them — the next
  iteration's first class was updated under the last class's tolls. This one is
  in the serial build too. `lastClass` is now reset after each gap evaluation,
  so the next dispatch re-applies the class. It demonstrably fired: on tolled
  2-class SiouxFalls the reset changes the trajectory (18 → 14 iterations at 1
  thread; serial build 18 → 16; 3 classes, serial, 24 → 26). Since re-applying
  the class already in place is an exact no-op, any change in trajectory means
  some update had run under the wrong class's prices.
- `network->totalODFlow` was reset inside the per-class trips-reading loop, so
  it held only the last class's demand. It feeds only the AEC/MEC gap functions
  (Connectome asks for `RELATIVE GAP`), so it was harmless to us; it now
  accumulates across classes.

**Single-class identity.** `changeFixedCosts` on the class already in place
computes `cost += fixedCost − fixedCost`, i.e. adds exactly `0.0`, so every new
call these patches add is bit-exact for one class. Checked, not assumed: flows
byte-identical to the 3837de3 binary (md5 `a68c72f0…`) at 1 thread on
SiouxFalls, Braess, Anaheim, ChicagoSketch and Barcelona, each with and without
a 10 %-of-capacity preload, and on ChicagoRegional — now the committed goldens
of `make check` (§15b).

**Upstream.** The race is upstream `master`'s: the dispatchers above are
unchanged from the fork base `040135a`. Upstream `develop` fixes it
structurally — the class-cost-refactor merge (`5eda205`, 2025-02-27) passes the
class's cost into the bush routines as an argument, so there is no shared
`fixedCost` to swap and no `changeFixedCosts` in `bush.c` at all. A rebase onto
`develop` would therefore **drop** this section's T1 and section 15's T2 rather
than port them, while sections 2, 5, 6 and 10–13 (and section 16's per-worker
scratch) would have to be ported onto the refactored code. There is no
rebase in this project; the pin is still the point. Reporting the race on
`master` upstream is an outward-facing call, not made here.

### 15. `src/bush.c` — class-aware pooled SPTT and bush init

Sections 2 and 5 pooled the gap evaluation's per-origin SPTT and the initial
bush build, but only for `numClasses == 1`: with several classes both fell
back to the serial loops, because the serial loops' `changeFixedCosts`
sequencing had not been made safe to pool. At Washington DC scale that is ~35
min × classes per gap evaluation (section 2) and a serial cold start.

With the drain of section 14 the same pattern applies: walk the class-ordered
origins, and at each class boundary `thpool_wait()`, `changeFixedCosts(c)`, then
queue that class's origins. The `numClasses == 1` gates are gone; the pooled
SPTT keeps its bins-off condition and the pooled init its stock-function-pointer
condition. `Parallel SPTT path engaged (N origins, …)` and `Parallel bush init
engaged (N origins)` now print for multi-class runs, with N = zones × classes.

Measured on ChicagoRegional (gap 1e-5, demand split evenly across classes;
scoping memo §2.3, 8 threads, dev1 under load so wall ±30 %): at 2 classes the
serial fallback took 208 s wall (init 15.6 s), the pooled paths 124 s (init 3.0
s); at 3 classes 282 s → 184 s (init 21.0 → 4.2 s). Iterations stay at 14 for 1,
2 and 3 classes; peak RSS scales linearly (1.90 / 3.80 / 5.65 GB).

### 15a. `src/bush.c` — per-class convergence report

tap-b's relative gap pools every class: `Σ_c TSTT_c / Σ_c SPTT_c − 1`, i.e. the
classes' gaps averaged with weights SPTT_c. A class whose generalized cost is
small next to the others' can sit far above the target while the run
"converges". Upstream's own two-class sample shows it at its extreme: Anaheim2's
class 2 prices distance at 2 per foot, so its generalized cost (~7.6e8) is ~500×
class 1's, and the pooled gap is almost all class 2's (an all-or-nothing 0). At
the sample's 1e-6 target tap-b stops after 2 iterations with **class 1 at
4.0e-4**.

After the convergence loop the fork now prints one line per class, from the
final iteration's gap evaluation — the state the flows file reports:

```
CLASS_GAP <class, 1-based> <TSTT> <SPTT> <(TSTT − SPTT) / SPTT>
```

TSTT and SPTT are generalized costs in that class's own prices (time + toll ×
toll factor + length × distance factor), so their gap is the class's relative
gap. Printed at `LOW_NOTIFICATIONS`, the level of the `Iteration` lines; for
single-class runs too, where the one line's gap equals the last iteration's.
With several batches the per-batch figures are the batch-mode estimate and the
tag reads `CLASS_GAP_EST`. The accumulators are separate from the aggregate
ones, so the aggregate's summation order — and every existing output — is
unchanged. Connectome's log parser (`tapb_backend.run_tapb`) reads `Iteration`
lines by regex and the prove-it-fired markers by substring; the new lines match
neither.

Checked against the oracle (§15b): the report agrees with the independent
recomputation to ~1e-11 relative in the gap on preloaded single-class
SiouxFalls, ChicagoSketch and Anaheim at gap 1e-4, and to 2–3 significant digits
of the (1e-10-scale) gaps on the multi-class cases — the residual there is the
flows file's 6-decimal rounding, which the oracle reads and tap-b does not.
On ChicagoRegional at 12 threads the report and the oracle agree to the three
digits compared: 4.86e-7 / 8.61e-7 (2 classes) and 6.02e-7 / 8.08e-7 / 7.14e-7
(3 classes).

The stopping rule is unchanged: still the aggregate gap. Whether a caller
should judge a run by its worst class is the caller's call (Connectome records
the lines and warns).

### 15b. `test/`, `tools/` — the L1 regression ladder (`make check`)

`make check` runs, at `<NUMBER OF THREADS> 1` where the parallel build is
deterministic:

- **Single-class identity**: SiouxFalls, Braess, Anaheim, ChicagoSketch and
  Barcelona, each with and without a 10 %-of-capacity preload, at
  `<CONVERGENCE GAP> 1e-10`; and ChicagoRegional at 1e-4. The flows files are
  byte-compared with goldens in `test/golden/` (the file itself for the four
  small networks, an md5 for Barcelona and ChicagoRegional). The goldens were
  generated once by the binary these sections were built on (3837de3, md5
  `a68c72f066aa9e019f273ea9ef711774`) with `run_check.py golden`. Regenerate
  them only for a change *meant* to move single-class flows, and record it here.
- **Multi-class correctness**: tolled SiouxFalls 2- and 3-class (also 2-class
  with preload), Anaheim2 unbatched, ChicagoRegional 2- and 3-class. Every
  class's gap is recomputed by the oracle and gated at the case's per-class
  target. Because the stopping rule is the aggregate (§15a), each case solves
  at a tighter aggregate target than its per-class gate: 1e-9 for a 1e-8 gate on
  SiouxFalls, 1e-6 for a 1e-5 gate on Chicago, and 1e-13 for Anaheim2's 1e-6.
  `make check THREADS=12` runs the multi-class cases at 12 threads.

`run_check.py check --serial-bin <serial build>` adds a comparison of total
link flows against the serial build (tolerance 0.1 veh). Per-class link flows
are not unique in a multi-class equilibrium — only the totals are, given
strictly increasing link times — so their difference is reported, not gated
(thousands of vehicles on SiouxFalls, where the classes share path sets).

Fixtures are derived, not committed: `test/make_fixtures.py` writes the tolled
SiouxFalls network and class trips files, the ChicagoRegional splits (stock
demand / 2 at toll factors 2 and 10 min/$; / 3 at 2, 10 and 0) and the preload
files into `test/data/` (gitignored; the Chicago files are ~73 MB each).
`make check-quick` skips the ChicagoRegional cases. Runtimes on a 12-core box
(dev1, shared and loaded): check-quick ~10 s; ChicagoRegional single-class ~5
min; the ChicagoRegional 2- and 3-class cases ~2.5 and ~3 min each with
`THREADS=12` (tap-b peak RSS 3.8 / 5.7 GiB before section 16, 1.7 / 2.5 GiB
after). At the default 1 thread, 2-class
measured 1,018 s (19 iterations) against 461 s for the serial build (25
iterations): the parallel build at 1 thread is ~2.2× slower than serial. The
3-class case at 1 thread was not run (Taylor, 2026-10-21); it is covered by the
12-thread oracle pass and the SiouxFalls 3-class comparison with serial.

**ChicagoRegional totals are report-only.** 2-class at 1 thread (solve 1e-6):
the oracle puts both builds at per-class equilibrium (parallel 4.37e-7 /
4.99e-7; serial 3.51e-7 / 3.39e-7), but their total link flows differ by up to
7.2 veh, median 0.018 veh, 191 of 39,033 links above 1 veh, diffuse and on
flat-BPR (lightly loaded) links. This is not a defect signal at solve 1e-6, and
the 0.1-veh totals gate is defined for the small networks only, so
`run_check.py` reports the Chicago comparison without failing on it. The
explanation is an unproven hypothesis: it reads as convergence-tolerance spread
(a ~4e-7 gap on 1.4M trips does not pin totals to 0.1 veh, as Anaheim2 shows at
1e-10 vs 1e-13), but the tighter-solve confirmation was not run.

**The oracle**, `tools/mcgap.py`, is stdlib-only Python 3 — no numpy, no
Connectome environment — and shares no code with tap-b. Per class it evaluates
`cost_k(a) = t_a(x_a + p_a) + tollFactor_k · toll_a + distanceFactor_k ·
length_a` on the flows tap-b wrote (`p` = the `<PRELOAD FILE>` flow, with
`--preload`; it enters only the travel time, as in section 6; with
`--class-toll`, `toll_a` is class k's column of the `<CLASS TOLL FILE>`,
section 17), runs Dijkstra
from every origin (zones below `<FIRST THRU NODE>` are not passed through),
and reports `(TSTT_k − SPTT_k) / SPTT_k`. Its stated assumptions:

- **Artificial arcs.** tap-b appends artificial arcs (cost 99,999) to make the
  network strongly connected; the oracle knows only the network file's arcs. It
  assumes no flow on an artificial arc and that every OD pair with demand is
  connected by real arcs. The multi-class flows file lists the artificial arcs
  after the real ones, and the oracle checks they carry zero flow; the
  single-class file omits them, so there the assumption is unchecked. An
  unreachable OD pair is an error.
- Arcs are matched positionally (the i-th flows row is the i-th network link,
  `(tail, head)` verified), BPR is assumed for every arc, and
  `<DEMAND MULTIPLIER>` is not applied.

The preload extension was validated on preloaded single-class cases at gap 1e-4,
where tap-b's own gap is the preloaded problem's (section 6): oracle vs tap-b
3.4812946051e-5 vs 3.4812946061e-5 (SiouxFalls), 7.7010117802e-5 vs
7.7010117393e-5 (ChicagoSketch), 1.5366444004e-5 vs 1.5366446902e-5 (Anaheim).
Without `--preload` the oracle reads 2.5e-3, 1.9e-3 and 6.4e-4 on the same
flows — the extension is doing the work.

### 16. `src/bush.c`, `src/parallel_bush.c`, `src/thpool.c` — per-worker scratch rows

The parallel build kept four scratch matrices — `LPcost_par`, `SPcost_par`,
`nodeFlow_par` (`[·][node]`) and `flow_par` (`[·][arc]`) — with one row per
**origin**, i.e. per (zone, class): 24 B/node + 8 B/arc per origin. At
Washington DC (~85k nodes, ~195k arcs) that is ~3.6 MB per origin, ~6.5 GB
per class at 1,814 zones — more than the bushes themselves. Nothing needs it:
every pool task writes its row in full before it reads it, so a row need only
live as long as the task. The rows are now one per **pool thread**.

- `thpool` gives each worker a thread-local index, `thpool_worker_id` (0 ..
  threads−1; −1 on any other thread), set once when the worker starts, and
  `thpool_num_threads()`. No task signature changed: the `_par` functions read
  `t = thpool_worker_id` and index `[t]` where they indexed `[origin]`.
  `createBushes` allocates `thpool_num_threads(thpool)` rows
  (`bushes->numScratchRows`); each pool entry (`initBushPool`,
  `updateBushPool`, `updateFlowsPool`, `bushSPTTPool`) checks it is on a
  worker whose row exists.
- The places that read a row across tasks, or were indexed wrongly, were
  changed rather than moved:
  - **bins mode** (`<CALCULATE BINS>`, off by default, §3): the serial SPTT
    loop `memcpy`'d `flow_par[r]` — whatever the last pooled task for origin
    `r` left. It now recomputes the origin's flows serially
    (`calculateBushFlows` into `bushes->flow`). The bins feed only DEBUG
    output; flows are byte-identical with bins on (SiouxFalls 1-class and
    tolled 2-class, 1 thread, vs 3808cb3).
  - **`printBush`** (DEBUG dump at the end of `AlgorithmB`) read the
    `[origin]` rows. It now takes the flow, node-flow and label vectors to
    print; the call site recomputes flows serially first (only at DEBUG
    verbosity — it is O(origins × arcs)) and, in the parallel build, passes no
    labels (printed `nan`: no labels are current after the loop). One
    implementation for both builds. Exercised with a DEBUG-verbosity build on
    Braess.
  - `pushBackFlowMerge_par`, `rectifyMerge_par`, `checkFlows_par` already
    indexed `[t_id]`, but callers passed the origin; they now pass the worker
    index (`rectifyMerge_par` has no caller; the `checkFlows_par` call is
    commented out upstream).
  - `nodeFlowshift_par`, declared and never allocated or used, is deleted.
- The cycle diagnostics (§11) read the worker's row on a pool thread, and the
  serial scratch vectors on the main thread — the serial-init fallback, where
  3808cb3 read an `[origin]` row that path never writes. The
  `<SELFTEST INJECT CYCLE>` dump is byte-identical to 3808cb3's.

The bush file (`<STORE BUSHES>` / `<WARM START>`) is unchanged: it never held
scratch.

**Site table — also the port map** for any rebase onto upstream `develop`
(§14): every `_par` scratch access at 3808cb3, by function. WBR = written
before read within the same pool task (moved to `[t]` as is); XT = read
across tasks (changed as above).

| function (file) | arrays | class | why |
|---|---|---|---|
| `scanBushes_par` (parallel_bush.c) | LP, SP | WBR | initialises every node first |
| `updateBushB_par` | flow, LP, SP | WBR | after `calculateBushFlows_par` + `scanBushes_par` in the same call |
| `reconstructMerges_par` | flow | WBR | called by `updateBushB_par`; reads its NEW_LINK marks |
| `rescanAndCheck_par` | SP, LP, flow | WBR | labels from its own `scanBushes_par`; flow from `updateFlowsB_par`'s `calculateBushFlows_par` |
| `updateFlowPass_par` | nodeFlow, LP, SP | WBR | initialises nodeFlow; labels from `rescanAndCheck_par` |
| `calculateBushFlows_par` | nodeFlow, flow | WBR | initialises every node and arc |
| `pushBackFlowSimple_par` | flow, nodeFlow | WBR | inside the two above, after their init |
| `pushBackFlowMerge_par` | flow, nodeFlow | WBR | `[t_id]`; callers now pass the worker index |
| `newtonFlowShift_par` | flow | WBR | inside `updateFlowPass_par` |
| `initialBushShortestPath_par` | SP | WBR | `arcIndexBellmanFord` initialises the labels |
| `rectifyMerge_par`, `checkFlows_par` | nodeFlow, flow | dead | no live caller; `[t_id]` = worker index |
| `diagInjectTwoCycle`, `diagSPcost/LPcost/Flow` (bush.c) | flow, SP, LP | WBR | inside `updateBushB_par` / `initBushPool` tasks; main thread reads serial scratch |
| `bushSPTTPool` (bush.c) | SP | WBR | its own `scanBushes_par` writes the row |
| `bushSPTT` serial loop, bins mode (bush.c) | flow | **XT** | recomputes serially |
| `printBush` (bush.c) | all four | **XT** | takes the scratch explicitly |
| `createBushes` / `deleteBushes`, `bushes_type` | all | alloc | `batchSize` rows → `numScratchRows` |

74 indexed sites (56 in parallel_bush.c, 18 in bush.c); no `_par` scratch
access anywhere else in `src/`, `include/` or `test/`.

**Memory, measured** (ChicagoRegional: 12,982 nodes, 39,033 arcs with the 15
artificial ones, 1,790 zones; `/usr/bin/time -v` on the tap-b process alone;
12 threads; 3808cb3 and this section run back to back on the same inputs).
Predicted drop: (24 × 12,982 + 8 × 39,033) B = 623,832 B per origin, × 1,790
origins per class, less the 12 rows kept:

| classes | 3808cb3 peak RSS | this section | drop | predicted drop |
|---|---|---|---|---|
| 1 | 1,981,992 kB | 898,420 kB | 1,083,572 kB | 1,083,177 kB |
| 2 | 3,987,168 kB | 1,812,980 kB | 2,174,188 kB | 2,173,665 kB |
| 3 | 5,929,076 kB | 2,664,588 kB | 3,264,488 kB | 3,264,153 kB |

The model holds to 0.05 %. What remains grows ~0.88 GB per class here — the
bushes (pred, bushOrder, merges) and the class's demand — so per-class memory
is now about the bush file plus demand, not that plus 24 B/node + 8 B/arc ×
origins.

**Results unchanged.** Every task does the same arithmetic on a different
row, so at `<NUMBER OF THREADS> 1` — where the parallel build is deterministic
— flows are byte-identical: `make check-quick` against the committed goldens
(the five single-class networks with and without preload), ChicagoRegional
single-class md5 `57473a5a…` = golden, and the four small multi-class cases
byte-identical to 3808cb3 (also with `<NUMBER OF BATCHES> 2`, where
`readBushes` reallocates the rows every batch). Warm start: a bush file written
by the shared binary (md5 `a68c72f0…`) loads, and the warm solve's flows are
byte-identical to the shared binary's own warm solve (SiouxFalls,
ChicagoSketch, 1 thread); the bush files the two binaries write are
byte-identical too. At 12 threads the per-class oracle gaps on
ChicagoRegional are 4.80e-7 / 7.85e-7 (2 classes) and 7.36e-7 / 9.61e-7 /
8.60e-7 (3 classes), all under the 1e-5 gate.

**Wall time.** Same box, back to back, 12 threads, dev1 shared (load average
5–17 on 12 cores during the runs, so wall is ±30 %): 1 class 58 s vs 70 s
(3808cb3); 2 classes 231 s vs 214 s; 3 classes 219 s vs 179 s. User CPU,
less sensitive to other tenants, is flat or lower: 333 vs 324 s, 1,108 vs
1,130 s, 1,358 vs 1,370 s; iterations equal (11/10, 18/18, 17/17). No
regression is attributable beyond the noise.

### 17. `src/fileio.c` — per-class link tolls, `<CLASS TOLL FILE>`

Upstream's multi-class model gives every class the same toll on a link — the
network file's `Toll` column — and lets classes differ only in how they weigh
it (`<TOLL FACTOR>`, minutes per dollar). That cannot say "this class is
exempt here and pays there", which is what an HOV class on a mixed HOT/toll
network needs. The arc struct already has the slot: `classToll[c]`, which
`readOBANetwork` fills with the `Toll` column for every class and from which
`finalizeNetwork` precomputes each class's fixed cost,

```
classCost[c] = distanceFactor_c × length + tollFactor_c × classToll[c]
```

that `changeFixedCosts(c)` (§14) swaps into the shared `fixedCost`. Nothing
upstream ever writes `classToll` with anything else. A new optional parameter
does:

```
<CLASS TOLL FILE> mynet_classtoll.txt
```

resolved against `<FILE PATH>` like the network, trips and preload files.
Without the tag nothing changes: every class pays the `Toll` column, as
before.

**Format.** Positional, like the `<PRELOAD FILE>` (§6): one row per link in
network-file order, its `(tail, head)` verified against the arc it lands on,
then one toll column per class, in parameters-file `<TRIPS FILE>` order.
Tolls are in the `Toll` column's units (dollars); each class's `<TOLL FACTOR>`
converts them to minutes exactly as it converts the `Toll` column.

```
<NUMBER OF LINKS> 76
<NUMBER OF CLASSES> 2
<END OF METADATA>
~ Init Term Class1 Class2 ;
1 2 0 0 ;
8 16 5 0 ;
```

Both metadata tags are required. Blank lines and `~` comment lines are skipped
anywhere; a data row ends at its first `;` (the `;` is optional); fields are
whitespace-separated. The file *replaces* `classToll[c]` on every real arc
for every class — there is no "missing value means the Toll column": a class
that should pay the Toll column gets the Toll column written out.

**Validation — every mismatch is fatal**, with the file name, the row and the
offending line in the message (tap-b exits 1):

- `<NUMBER OF LINKS>` missing or ≠ the network's link count;
- `<NUMBER OF CLASSES>` missing or ≠ the number of `<TRIPS FILE>`s;
- fewer data rows than links (`ended … after N of M links`), or any data row
  after the last link (`has data after its M links`);
- a row whose `(tail, head)` is not the arc it lands on (`misaligned with the
  network at row R`) — a shifted, re-sorted or other network's file;
- a row whose toll column count is not the class count;
- a field that does not parse in full, or a toll that is not finite (`nan`,
  `inf`).

Negative tolls are accepted, as they are in the network file's `Toll` column
(which only warns, at full verbosity); the class toll reader counts them and
warns once at `LOW_NOTIFICATIONS`. Loading prints the prove-it-fired lines

```
Class tolls loaded: 76 links x 2 classes from ./mynet_classtoll.txt.
Class 1 tolls: 16 links nonzero, 0 differ from the network Toll column.
Class 2 tolls: 8 links nonzero, 8 differ from the network Toll column.
```

(none contains `Iteration` or the other markers Connectome's log parser keys
on).

**Where it enters.** `readClassTollFile` runs in `readParametersFile` right
after the preload — after `readOBANetwork`, before `AlgorithmB`'s
`makeStronglyConnectedNetwork` — so the rows are matched against the real
arcs only. It writes `classToll[c]` and recomputes `classCost[c]` with
`finalizeNetwork`'s expression, term for term, so a file equal to the `Toll`
column reproduces `classCost` bit for bit. `arc.toll` (the `Toll` column) is
left as read; `writeOBANetwork` still echoes it. **Artificial arcs** are not
in the file (it covers the network file's links, in exported order); they are
created afterwards with `classToll[c] = 0` (upstream's "protect revenue"), and
the `finalizeNetwork` that `makeStronglyConnectedNetwork` re-runs recomputes
every arc's `classCost` from `classToll`, so the file's values survive it.
`classToll` also drives the per-class revenue (`classRevenue`, the `Toll`
column of `writeMulticlassFlows`' summary block), so revenue follows the
file: an exempt class's trips on a link pay nothing there.

**Tests** (`make check` / `check-quick`; fixtures from `make_fixtures.py`,
over the 76 links of the tolled SiouxFalls of §14):

- `SF_ct_exempt` — the HOV-exempt pattern. Class 1 (0.5 min/$) pays the `Toll`
  column; class 2 (4 min/$) is exempt on the 8 tolled links incident to node 16
  and pays $5 on the 8 incident only to node 10. Per-class oracle gate 1e-8
  (solve 1e-9). At 1 thread: oracle 3.04e-10 / 2.06e-10, tap-b `CLASS_GAP`
  3.07e-10 / 2.02e-10; at 12 threads 3.85e-10 / 5.40e-10. Total flows within
  2.2e-3 veh of the serial build.
- `SF_ct_same2` — a 2-class file equal to the `Toll` column: flows file
  byte-identical to the same run without the tag (1 thread).
- `SF_ct_same1` — the single-class equivalent on stock SiouxFalls: flows
  byte-identical to the committed `SiouxFalls` golden.
- `SF_ct_zero2` — class 2 (4 min/$) zeroed everywhere by the file routes as a
  toll-factor-0 class: every link row byte-identical to the 2-class run with
  the `free` trips file (0 min/$) in its place. Only the summary block differs,
  and must: the `free` class still pays the `Toll` column (at zero weight),
  the zeroed class pays nothing (class 2 revenue 1,244,075.875 vs 0). This is
  the test that the file is applied per class rather than ignored — the two
  identity tests above would pass on a binary that never read it.
- `SF_ct_bad_{count,short,extra,swapped,cols,classes,nan}` — seven malformed
  variants of `ct_same2`, each one defect away from it (stated 75 links; 75
  rows; 77 rows; row 22 written `(16,8)`; row 30 with three toll columns;
  `<NUMBER OF CLASSES> 3`; a `nan`). Each must exit nonzero *and* print its
  expected message.
- Every case that passes the tag also fails if the log lacks `Class tolls
  loaded`.

**The oracle** learns the file: `mcgap.py … --class-toll FILE` reads it
(positional, `(tail, head)` verified, column and row counts checked) and
prices class k at `tollFactor_k × classToll_k(a)`; without it, the `Toll`
column for every class, as before. Validated on the `SF_ct_exempt` flows
before it gated anything:

| check | class 1 gap | class 2 gap |
|---|---|---|
| oracle with `--class-toll` | 3.0447e-10 | 2.0592e-10 |
| tap-b's own `CLASS_GAP` (independent code) | 3.0656e-10 | 2.0234e-10 |
| oracle **without** `--class-toll`, same flows | 3.0447e-10 | **4.15e-3** |
| no-file equilibrium judged under the exempt tolls | 7.14e-10 | **5.18e-3** |
| no-file flows, oracle with a `Toll`-equal file vs without | identical to all printed digits | identical |

Class 2's TSTT agrees with tap-b's to ~2e-12 relative (64,181,396.1243 vs
.1244). The extension does the work (row 3), and the file moves the
equilibrium (row 4).

**Pattern, not built in: the soft ban.** A class can be kept off a link by
charging it a large *finite* toll there — never `ARTIFICIAL`. On tolled
SiouxFalls, $1,000 for class 2 (4 min/$, i.e. 4,000 min) on (10,16) and
(16,10) takes class 2's flow there from ~8,800 veh to 0.000000 while class 1
absorbs the links (gaps 8.7e-10 / 7.2e-10, oracle agreeing). Three cautions:

- It is soft. If a class has no other path, it still uses the link and pays;
  the charge then dominates that class's generalized cost and its revenue
  line (`classRevenue` counts it as toll paid). A hard prohibition would be a
  per-class link ban, which is a behaviour change this fork has not made.
- Keep `tollFactor_c × charge` far below `ARTIFICIAL` (99,999 min). Artificial
  arcs cost ~99,999 min of travel time, so a charge whose minutes approach that
  makes a detour through the artificial connector node competitive, and the
  class's flow goes onto arcs the flows file reports as artificial — wrong in a
  way that still converges. Charge enough to exceed any plausible detour (a
  few times the longest OD path's cost), no more.
- Never `ARTIFICIAL` itself, or `inf` (refused at load): the gap and the
  revenue are sums of `flow × cost`, so a sentinel-sized charge on even a
  sliver of residual flow swamps them.

Connectome does not use this pattern; it is documented for the future HOV
work only.

## Not upstreamed (yet)

The pooled `bushSPTT`, the pooled bush init, the `SRCS`/`SOURCES` Makefile bug,
the cost-update hoist and the wall-clock timing are all clean wins that would
apply to any user, and are reasonable pull requests whenever we have time to
write them against upstream's current `develop`. The cycle fix (section 10) is
the one upstream most needs — they have hit this class of bug twice — but it
changes their default, so it would need the conversation, not just the patch. The dialect pin and the bins
default are opinionated and probably belong to us. The preload is a feature
upstream may or may not want; we would offer it, and the class toll file (section 17) with it. The class-switch race (section 14) is a bug in
upstream `master` that `develop` has already fixed structurally; at most it is
a bug report against `master`, not a patch.

## Contact

D. Taylor Reich · Ives Street · <dtr@ives.st>
