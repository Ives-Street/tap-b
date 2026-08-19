# Ives Street fork of tap-b

This is [Ives Street](https://ives.st)'s fork of [`spartalab/tap-b`](https://github.com/spartalab/tap-b),
vendored for use as a bush-based user-equilibrium engine inside
[Connectome](https://github.com/Ives-Street/connectome)'s Stage 3.5 traffic
assignment. Upstream is MIT-licensed; so is this fork.

Nothing here changes the algorithm. The patches are two build fixes, three
performance fixes, the parameters that make the switchable ones switchable, and
one genuine feature — a per-link fixed background flow that upstream has no way
to express.

**Base commit:** `040135a20c771fbb84766df6a97cff981fa5df4b` (upstream `master`,
2024-09-22). **Our branch:** `ives/main`. Upstream stays as the `upstream`
remote; we do not rebase automatically — the pin is the point.

## Build

```sh
make            # parallel build (default), gcc 15 clean
make serial     # single-threaded build
make test       # direct checks on the preloaded performance functions
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

The fast path is taken only when it is provably equivalent — single class
(`numClasses == 1`, so no `changeFixedCosts` sequencing) and bins off (below).
Anything else falls through to the stock serial loop, unmodified. Serial builds
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

The fast path is taken only where it is provably the same computation: single
class (so the serial loop's `changeFixedCosts` sequencing collapses to the one
hoisted call), and both `createInitialBush` and `topologicalOrder` still pointing
at the stock implementations — the ones audited for per-origin independence.
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

## Not upstreamed (yet)

The pooled `bushSPTT`, the pooled bush init, the `SRCS`/`SOURCES` Makefile bug,
the cost-update hoist and the wall-clock timing are all clean wins that would
apply to any user, and are reasonable pull requests whenever we have time to
write them against upstream's current `develop`. The dialect pin and the bins
default are opinionated and probably belong to us. The preload is a feature
upstream may or may not want; we would offer it.

## Contact

D. Taylor Reich · Ives Street · <dtr@ives.st>
