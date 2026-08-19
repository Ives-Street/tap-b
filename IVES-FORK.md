# Ives Street fork of tap-b

This is [Ives Street](https://ives.st)'s fork of [`spartalab/tap-b`](https://github.com/spartalab/tap-b),
vendored for use as a bush-based user-equilibrium engine inside
[Connectome](https://github.com/Ives-Street/connectome)'s Stage 3.5 traffic
assignment. Upstream is MIT-licensed; so is this fork. Nothing here changes the
algorithm — the patches are a build fix, a performance fix, and the parameter
that makes the performance fix switchable.

**Base commit:** `040135a20c771fbb84766df6a97cff981fa5df4b` (upstream `master`,
2024-09-22). **Our branch:** `ives/main`. Upstream stays as the `upstream`
remote; we do not rebase automatically — the pin is the point.

## Build

```sh
make            # parallel build (default), gcc 15 clean
make serial     # single-threaded build
```

The binary lands at `bin/tap`. No cmake, no autotools, no Python environment —
Connectome shells out to this binary and does not link against it.

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

## Not upstreamed (yet)

The pooled `bushSPTT` is a clean win that would apply to any parallel-build
user, and is a reasonable pull request whenever we have the time to write one
against upstream's current `develop`. The dialect pin and the bins default are
opinionated and probably belong to us, not to upstream.

## Contact

D. Taylor Reich · Ives Street · <dtr@ives.st>
