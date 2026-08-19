/*
 * IVES FORK: bpr_preload_test.c -- direct checks on the preloaded link
 * performance functions.
 *
 * Why a C test rather than an end-to-end one.  The preloaded *cost* is visible
 * in a solve (it moves the equilibrium, and a two-route analytic case pins the
 * shift exactly).  The preloaded *derivative* is not: Newton's method converges
 * to the same equal-cost fixed point whether or not its step scaling knows
 * about the background flow, so a der that ignored the preload would produce
 * identical converged flows and only cost iterations.  The same goes for the
 * Beckmann integral, which nothing in the RELATIVE GAP path reads.  A converged
 * solve therefore cannot tell a patched der/int from an unpatched one -- which
 * is exactly the "no-op change that produces a perfect A/B" shape.  So they are
 * checked here, at the function, against closed forms.
 *
 * Three families are checked (linear beta=1, quartic beta=4, general
 * beta=2.5), each on three axes:
 *   1. cost(x, p) equals the closed form, and equals cost(x + p, 0) -- the
 *      substitution property that makes the preload a congestion term.
 *   2. der(x, p) equals a central finite difference of cost with respect to
 *      the *assigned* flow x, holding p fixed.  This is the quantity Newton's
 *      method actually needs, and finite-differencing cost catches a der that
 *      dropped the preload (the two differ by a factor of ((x+p)/x)^(beta-1)).
 *   3. int(x, p) equals T(p + x) - T(p) for the family's antiderivative T, and
 *      equals a fine trapezoid rule of cost over [0, x] at fixed p.  A common
 *      wrong answer, T(p + x), is checked to be *rejected*.
 *
 * At p = 0 every assertion also pins the upstream values, so the test doubles
 * as a regression guard on the un-preloaded path.
 *
 * Build and run:  make test
 */

#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#include "tap.h"
#include "networks.h"

static int failures = 0;
static int checks = 0;

static void check(const char *what, double got, double want, double tol) {
    checks++;
    double err = fabs(got - want);
    double scale = fabs(want) > 1 ? fabs(want) : 1.0;
    if (err / scale > tol) {
        failures++;
        printf("FAIL  %-58s got %.12g, want %.12g (rel %.3g > %.3g)\n",
               what, got, want, err / scale, tol);
    }
}

static void checkDiffers(const char *what, double got, double wrong) {
    checks++;
    double scale = fabs(wrong) > 1 ? fabs(wrong) : 1.0;
    if (fabs(got - wrong) / scale < 1e-6) {
        failures++;
        printf("FAIL  %-58s equals the value it must NOT equal (%.12g)\n",
               what, wrong);
    }
}

/* A bare arc carrying only what the BPR functions read. */
static arc_type makeArc(double fft, double cap, double alpha, double beta,
                        double flow, double preload) {
    arc_type a;
    a.freeFlowTime = fft;
    a.capacity = cap;
    a.alpha = alpha;
    a.beta = beta;
    a.flow = flow;
    a.preload = preload;
    a.fixedCost = 0;
    return a;
}

/* Closed forms, written independently of tap.c. */
static double bprCost(double fft, double cap, double alpha, double beta,
                      double y) {
    return fft * (1 + alpha * pow(y / cap, beta));
}

/* Antiderivative of bprCost with respect to flow. */
static double bprAntiderivative(double fft, double cap, double alpha,
                                double beta, double y) {
    return fft * (y + alpha / (beta + 1) * pow(y / cap, beta) * y);
}

/* Trapezoid rule for int_0^x cost(p + w) dw -- an independent second opinion
 * on the antiderivative, in case the closed form above shares a mistake with
 * the implementation. */
static double trapezoid(double fft, double cap, double alpha, double beta,
                        double x, double p) {
    const int N = 2000000;
    double h = x / N, sum = 0;
    int i;
    for (i = 0; i <= N; i++) {
        double w = i * h;
        double c = bprCost(fft, cap, alpha, beta, p + w);
        sum += (i == 0 || i == N) ? 0.5 * c : c;
    }
    return sum * h;
}

static void exercise(const char *name, double fft, double cap, double alpha,
                     double beta, double x, double p,
                     double (*costFn)(struct arc_type *),
                     double (*derFn)(struct arc_type *),
                     double (*intFn)(struct arc_type *, bool)) {
    char label[256];
    arc_type a = makeArc(fft, cap, alpha, beta, x, p);

    /* 1. cost */
    snprintf(label, sizeof(label), "%s cost(x=%g, p=%g)", name, x, p);
    check(label, costFn(&a), bprCost(fft, cap, alpha, beta, x + p), 1e-12);

    /* ...and the substitution property: preload is indistinguishable from the
     * same amount of assigned flow, as far as congestion goes. */
    arc_type shifted = makeArc(fft, cap, alpha, beta, x + p, 0);
    snprintf(label, sizeof(label), "%s cost(x,p) == cost(x+p,0)", name);
    check(label, costFn(&a), costFn(&shifted), 1e-12);

    /* 2. derivative vs a central difference of cost in the assigned flow */
    double h = 1e-4 * (x > 1 ? x : 1);
    arc_type up = makeArc(fft, cap, alpha, beta, x + h, p);
    arc_type dn = makeArc(fft, cap, alpha, beta, x - h, p);
    double fd = (costFn(&up) - costFn(&dn)) / (2 * h);
    snprintf(label, sizeof(label), "%s der(x=%g, p=%g) vs finite difference",
             name, x, p);
    check(label, derFn(&a), fd, 1e-6);

    /* A der that ignored the preload would return this instead. */
    if (p > 0 && beta != 1) {
        arc_type noPreload = makeArc(fft, cap, alpha, beta, x, 0);
        snprintf(label, sizeof(label), "%s der(x,p) is not der(x,0)", name);
        checkDiffers(label, derFn(&a), derFn(&noPreload));
    }

    /* 3. integral: T(p + x) - T(p), cross-checked by quadrature */
    double want = bprAntiderivative(fft, cap, alpha, beta, x + p)
                - bprAntiderivative(fft, cap, alpha, beta, p);
    snprintf(label, sizeof(label), "%s int(x=%g, p=%g) vs T(p+x)-T(p)",
             name, x, p);
    check(label, intFn(&a, FALSE), want, 1e-12);

    snprintf(label, sizeof(label), "%s int(x=%g, p=%g) vs quadrature", name,
             x, p);
    check(label, intFn(&a, FALSE), trapezoid(fft, cap, alpha, beta, x, p),
          1e-8);

    if (p > 0) {
        snprintf(label, sizeof(label), "%s int(x,p) is not T(p+x)", name);
        checkDiffers(label, intFn(&a, FALSE),
                     bprAntiderivative(fft, cap, alpha, beta, x + p));
        snprintf(label, sizeof(label), "%s int(x,p) is not T(x)", name);
        checkDiffers(label, intFn(&a, FALSE),
                     bprAntiderivative(fft, cap, alpha, beta, x));
    }

    /* fixedCost enters the integral once per unit of assigned flow. */
    a.fixedCost = 3.25;
    snprintf(label, sizeof(label), "%s int with fixedCost", name);
    check(label, intFn(&a, TRUE), want + 3.25 * x, 1e-12);
    a.fixedCost = 0;
}

int main(void) {
    double preloads[] = {0.0, 137.5, 900.0};
    double flows[] = {0.5, 250.0, 1800.0};
    size_t ip, ix;

    for (ip = 0; ip < sizeof(preloads) / sizeof(preloads[0]); ip++) {
        for (ix = 0; ix < sizeof(flows) / sizeof(flows[0]); ix++) {
            double p = preloads[ip], x = flows[ix];
            exercise("linear ", 12.0, 1000.0, 0.15, 1.0, x, p,
                     &linearBPRcost, &linearBPRder, &linearBPRint);
            exercise("quartic", 12.0, 1000.0, 0.15, 4.0, x, p,
                     &quarticBPRcost, &quarticBPRder, &quarticBPRint);
            exercise("general", 12.0, 1000.0, 0.15, 2.5, x, p,
                     &generalBPRcost, &generalBPRder, &generalBPRint);
        }
    }

    /* Zero assigned flow with a live preload: the link is congested even
     * though nobody was assigned to it.  This is the case upstream's
     * "flow <= 0 => free flow time" guard would get wrong. */
    arc_type idle = makeArc(12.0, 1000.0, 0.15, 4.0, 0.0, 500.0);
    check("quartic cost(x=0, p=500) is congested, not free-flow",
          quarticBPRcost(&idle), bprCost(12.0, 1000.0, 0.15, 4.0, 500.0),
          1e-12);
    checkDiffers("quartic cost(x=0, p=500) is not free-flow time",
                 quarticBPRcost(&idle), 12.0);
    check("quartic int(x=0, p=500) is zero (no assigned flow)",
          quarticBPRint(&idle, FALSE), 0.0, 1e-12);

    arc_type idleGeneral = makeArc(12.0, 1000.0, 0.15, 2.5, 0.0, 500.0);
    check("general cost(x=0, p=500) is congested, not free-flow",
          generalBPRcost(&idleGeneral),
          bprCost(12.0, 1000.0, 0.15, 2.5, 500.0), 1e-12);

    printf("%s: %d checks, %d failures\n",
           failures == 0 ? "PASS" : "FAIL", checks, failures);
    return failures == 0 ? 0 : 1;
}
