#ifdef PARALLELISM
#include "parallel_bush.h"

threadpool thpool;
pthread_mutex_t shift_lock;

/*
 * scanBushes_par -- simultaneously find shortest and longest paths for a given
 * bush by a thread.  If the longestUsed argument is TRUE, the longest path search will
 * restrict attention to longest *used* paths.  If FALSE, longest bush paths
 * will be calculated regardless of whether there is flow on them.
*/
void scanBushes_par(int origin, network_type *network, bushes_type *bushes,
                    algorithmBParameters_type *parameters, scan_type LPrule) {
    const int t = thpool_worker_id; /* IVES FORK: scratch row, IVES-FORK.md 16 */
    int h, i, hi, m, curnode, curarc;
    double tempcost;
    merge_type *merge;

    for (i = 0; i < network->numNodes; i++) {
        bushes->LPcost_par[t][i] = -INFINITY;
        bushes->SPcost_par[t][i] = INFINITY;
    }

    /* Ensure costs are up to date */
    if (parameters->linkShiftB != &exactCostUpdate) updateAllCosts(network);

    bushes->SPcost_par[t][origin2node(network, origin)] = 0;
    bushes->LPcost_par[t][origin2node(network, origin)] = 0;
    for (curnode = 1; curnode < network->numNodes; curnode++) {
        i = bushes->bushOrder[origin][curnode];
        /* Iterate over incoming links */
        if (isMergeNode(origin, i, bushes) == TRUE) {
            m = pred2merge(bushes->pred[origin][i]);
            merge = bushes->merges[origin][m];
            for (curarc = 0; curarc < merge->numApproaches; curarc++) {
                hi = merge->approach[curarc];
                h = network->arcs[hi].tail;
                tempcost = bushes->SPcost_par[t][h] + network->arcs[hi].cost;
                if (tempcost < bushes->SPcost_par[t][i]) {
                    bushes->SPcost_par[t][i] = tempcost;
                    merge->SPlink = curarc;
                }
            }
            /* Find longest (need to separate out depending on LPrule) */
            if (LPrule == NO_LONGEST_PATH) continue;
            for (curarc = 0; curarc < merge->numApproaches; curarc++) {
                hi = merge->approach[curarc];
                h = network->arcs[hi].tail;
                tempcost = bushes->LPcost_par[t][h] + network->arcs[hi].cost;     
                if (tempcost > bushes->LPcost_par[t][i]
                  && (LPrule == LONGEST_BUSH_PATH
                    || (LPrule == LONGEST_USED_PATH
                      && merge->approachFlow[curarc] > parameters->minLinkFlow)
                    || (LPrule == LONGEST_USED_OR_SP
                      &&(merge->approachFlow[curarc] > parameters->minLinkFlow
                      || merge->SPlink == curarc)))) {
                    
                    bushes->LPcost_par[t][i] = tempcost;
                    merge->LPlink = curarc;
                }
            }
        } else { /* Only one incoming bush link, not much to do */
            hi = bushes->pred[origin][i];
            h = network->arcs[hi].tail;
            bushes->LPcost_par[t][i] = bushes->LPcost_par[t][h] + network->arcs[hi].cost;
            bushes->SPcost_par[t][i] = bushes->SPcost_par[t][h] + network->arcs[hi].cost;
        }
    }
}

/*
 * updateBushB_par -- updates the topology of a bush: deleting unused links, and
 * adding links which can serve as shortcuts.  Follows Nie (2009) for how this
 * is done, first checking a stronger criterion for adding bush links, then as
 * a fallback checking a weaker one.
 *
 * New links are marked using the NEW_LINK constant, for use in
 * reconstructMerges (the function which rebuilds the merge data structures
 * after bush updating.)
 */
void updateBushB_par(int origin, network_type *network, bushes_type *bushes,
                 algorithmBParameters_type *parameters) {
    const int t = thpool_worker_id; /* IVES FORK: scratch row, IVES-FORK.md 16 */

    int ij, i, j, newArcs = 0;

    diagResetNewArcs(); /* IVES F1 DIAGNOSTIC */

    /* First update labels... ignoring longest unused paths since those will be
     * removed in the next step. */
    calculateBushFlows_par(origin, network, bushes);
    scanBushes_par(origin, network, bushes, parameters, parameters->updateBushScanType);

    /* Make a first pass... */
    for (ij = 0; ij < network->numArcs; ij++) {
        /* Mark links with near-zero contribution for removal by setting to
         * exactly zero */
        if (bushes->flow_par[t][ij] < parameters->minLinkFlow) {
            if (isInBush(origin, ij, network, bushes) == TRUE)
            bushes->flow_par[t][ij] = 0;
        }
        if (bushes->flow_par[t][ij] > 0) continue; /* Link is already in the bush, no
                                               need to add it again */
        /* See if the link provides a shortcut using the strict criterion */
        i = network->arcs[ij].tail;
        j = network->arcs[ij].head;
        if (bushes->LPcost_par[t][i] == -INFINITY && bushes->LPcost_par[t][j] > -INFINITY)
            continue; /* No path to extend */
        if (bushes->SPcost_par[t][i] + network->arcs[ij].cost < bushes->SPcost_par[t][j]
            && bushes->LPcost_par[t][i] < bushes->LPcost_par[t][j]
            && (network->arcs[ij].tail == origin2node(network, origin)
                || network->arcs[ij].tail >= network->firstThroughNode))
        {
            bushes->flow_par[t][ij] = NEW_LINK;
            diagRecordNewArc(ij, 1); /* IVES F1 DIAGNOSTIC */
            newArcs++;
            /* Never delete shortest path tree... should be OK with floating point
             * comparison since this is how SPcost is calculated */
        } else if (bushes->SPcost_par[t][i]+network->arcs[ij].cost==bushes->SPcost_par[t][j]
                   && bushes->flow_par[t][ij] == 0
                   && isInBush(origin, ij, network, bushes) == TRUE) {
            bushes->flow_par[t][ij] = NEW_LINK;
            diagRecordNewArc(ij, 2); /* IVES F1 DIAGNOSTIC */
        }
    }

    /* If strict criterion fails, try a looser one.  IVES FORK: gated on its
     * own parameter, not on updateBushScanType == LONGEST_BUSH_PATH as
     * upstream has it -- that coupling hard-stalls SiouxFalls now that the
     * full-LP scan is the default (IVES-FORK.md section 10). */
    if (newArcs == 0 && parameters->useLooseCriterion == TRUE) {
        for (ij = 0; ij < network->numArcs; ij++) {
            i = network->arcs[ij].tail;
            j = network->arcs[ij].head;
            if (bushes->LPcost_par[t][i]==-INFINITY && bushes->LPcost_par[t][j]>-INFINITY)
                continue; /* No path to extend */
            if (bushes->flow_par[t][ij] == 0 && bushes->LPcost_par[t][i] < bushes->LPcost_par[t][j]
                && (network->arcs[ij].tail == origin2node(network, origin)
                    || network->arcs[ij].tail >= network->firstThroughNode))
            {
                bushes->flow_par[t][ij] = NEW_LINK;
                diagRecordNewArc(ij, 3); /* IVES F1 DIAGNOSTIC */
            }
        }
    }

    /* IVES FORK test hook, self-test only.  With <SELFTEST INJECT CYCLE> in
     * the parameters file, deliberately close a two-cycle in this bush by
     * adding the reverse of an in-bush arc.  reconstructMerges accepts it (it
     * only requires each non-origin node to have >= 1 incoming contributing
     * link), so genericTopologicalOrder then fails exactly as it does on the
     * DC crash -- which is what exercises diagReportCycle end to end on a
     * small network.  Never enabled by a normal run. */
    if (parameters->selftestInjectCycle == TRUE)
        diagInjectTwoCycle(origin, network, bushes);

    /* Finally update bush data structures: delete/add merges, find a new
     * topological order, rectify approach proportions */
    reconstructMerges_par(origin, network, bushes);
    parameters->topologicalOrder(origin, network, bushes, parameters);
}

/*
 * reconstructMerges_par -- update the merge data structures after a bush is
 * updated by adding and removing links.  In particular, for each node we do
 * the following...
 *
 * 1. Normalize approach proportions for incoming links -- if there are zero
 *    such links for a non-origin, throw an error.
 * 2. Set pred if there is just one incoming link, otherwise create a merge.
 *
 * Stores merges in a linked list at first, so they can be created in one pass.
 * Then transfer into an array for fast indexing.
 */
void reconstructMerges_par(int origin, network_type *network, bushes_type *bushes){
    const int t = thpool_worker_id; /* IVES FORK: scratch row, IVES-FORK.md 16 */
//    displayMessage(FULL_NOTIFICATIONS, "Top of update reconstructmerg %d\n", t_id);

    int i, hi, lastApproach, m, arc, numApproaches;
    arcListElt *curArc;
    merge_type *merge;
    mergeDLL *mergeList = createMergeDLL();
    mergeDLLelt *curMerge;


    /* Create necessary merges */
    for (i = 0; i < network->numNodes; i++) {
        if (i == origin2node(network, origin)) continue;
        numApproaches = 0;
        for (curArc = network->nodes[i].reverseStar.head; curArc != NULL;
             curArc = curArc->next) {
            hi = ptr2arc(network, curArc->arc);
            if (bushes->flow_par[t][hi] > 0 || bushes->flow_par[t][hi] == NEW_LINK) {
                numApproaches++;
                lastApproach = hi;
            }
        }
        if (numApproaches == 0)
            fatalError("Cannot have non-origin node %d in bush %d without"
                       "incoming contributing links", i+1, origin+1);
        if (numApproaches == 1) { /* No merge */
            bushes->pred[origin][i] = lastApproach;
            if (bushes->flow_par[t][lastApproach] == NEW_LINK) bushes->flow_par[t][lastApproach] = 0;
        } else { /* Must create a merge */
            merge = createMerge(numApproaches);
            arc = 0;
            for (curArc = network->nodes[i].reverseStar.head; curArc != NULL;
                 curArc = curArc->next) {
                hi = ptr2arc(network, curArc->arc);
                if (bushes->flow_par[t][hi] > 0 || bushes->flow_par[t][hi] == NEW_LINK) {
                    if (bushes->flow_par[t][hi] == NEW_LINK) bushes->flow_par[t][hi] = 0;
                    merge->approach[arc] = hi;
                    merge->approachFlow[arc] = bushes->flow_par[t][hi];
                    arc++;
                }
            }
            insertMergeDLL(mergeList, merge, i, mergeList->tail);
        }
    }

    /* Now transfer to array, deleting old merges and replacing with new ones*/
    for (m = 0; m < bushes->numMerges[origin]; m++) {
        deleteMerge(bushes->merges[origin][m]);
    }
    deleteVector(bushes->merges[origin]);
    bushes->numMerges[origin] = mergeList->size;
    bushes->merges[origin] = newVector(mergeList->size, merge_type *);
    m = 0;
    for (curMerge = mergeList->head; curMerge != NULL;
         curMerge = curMerge->next) {
        bushes->merges[origin][m] = curMerge->merge;
        bushes->pred[origin][curMerge->node] = merge2pred(m);
        m++;
    }

    deleteMergeDLL(mergeList);
}

/*
 * updateFlowsB -- shift flows on a bush to move closer to equilibrium.
 * This function is fairly short, as it mainly just manages a few other
 * functions that do the actual work.
 * - calculateBushFlows gives current flows (remember, these are not stored
 *   persistently).
 * - rescanAndCheck both updates longest/shortest path labels, but also checks
 *   whether these are different enough to actually do anything -- depending
 *   on parameters we can skip bushes which are closer to equilibrium, to
 *   spend more time on bushes further away.
 * - updateFlowPass then does the actual flow shifting, inside of a loop in
 *   case we want to do multiple shifts per origin.
 */
bool updateFlowsB_par(int origin, network_type *network, bushes_type *bushes,
                  algorithmBParameters_type *parameters) {
    int i;

    /* Recompute bush flows for this origin */
    calculateBushFlows_par(origin, network, bushes);


    /* Update longest/shortest paths, check whether there is work to do */
    if (rescanAndCheck_par(origin, network, bushes, parameters) == FALSE) {
        bushes->updateBush[origin] = FALSE;
        return FALSE;
    };

    /* Now do (possibly multiple) shifts per origin */
    for (i = 0; i < parameters->shiftReps; i++) {
        updateFlowPass_par(origin, network, bushes, parameters);

        // Too lazy to make this atomic rn
        pthread_mutex_lock(&shift_lock);
        parameters->numFlowShifts++;
        pthread_mutex_unlock(&shift_lock);

        /* Uncomment next line for extra validation checking */
//        checkFlows_par(network, bushes, thpool_worker_id);
        if (parameters->rescanAfterShift == TRUE
            && i + 1 < parameters->shiftReps) {
            if (rescanAndCheck_par(origin, network, bushes, parameters) == FALSE)
                return TRUE;
        }
    }

    return TRUE;

}

/*
 * rescanAndCheck -- calls functions updating the longest/shortest path labels
 * on the bush, along with divergence nodes; and then checks whether the bush
 * is close enough to equilibrium to skip for now (in which case the function
 * returns FALSE).  If we need to shift flows, it returns TRUE.
 */
bool rescanAndCheck_par(int origin, network_type *network, bushes_type *bushes,
                    algorithmBParameters_type *parameters) {
    const int t = thpool_worker_id; /* IVES FORK: scratch row, IVES-FORK.md 16 */
    int i, j, ij;
    double maxgap = 0;
    double bushSPTT = 0, bushExcess = 0;

    scanBushes_par(origin, network, bushes, parameters, LONGEST_USED_PATH);
    findDivergenceNodes(origin, network, bushes);
    for (i = 0; i < network->numZones; i++) {
        bushSPTT += network->demand[origin][i] * bushes->SPcost_par[t][i];
        maxgap = max(maxgap, fabs(bushes->LPcost_par[t][i] -bushes->SPcost_par[t][i]));
    }
    for (; i < network->numNodes; i++) {
        maxgap = max(maxgap, fabs(bushes->LPcost_par[t][i] -bushes->SPcost_par[t][i]));
    }
    for (ij = 0; ij < network->numArcs; ij++) {
        i = network->arcs[ij].tail;
        j = network->arcs[ij].head;
        bushExcess += bushes->flow_par[t][ij] * (network->arcs[ij].cost +
                                          bushes->SPcost_par[t][i] - bushes->SPcost_par[t][j]);
    }
    //displayMessage(DEBUG, "Scanning %d, gap is %f\n", origin,
    //               bushExcess / bushSPTT);
    //displayMessage(DEBUG, "Max gap, threshold, threshold AEC: %f %f %f\n",
    //               maxgap, parameters->thresholdGap, parameters->thresholdAEC);
    if (maxgap < parameters->thresholdGap) return FALSE;
    if (bushExcess / bushSPTT < parameters->thresholdAEC) return FALSE;

    return TRUE;
}

/*
 * updateFlowPass_par -- does the actual work of shifting flows from longest to
 * shortest paths, through a pass in descending topological order.
 */
void updateFlowPass_par(int origin, network_type *network, bushes_type *bushes,
                    algorithmBParameters_type *parameters) {
    const int t = thpool_worker_id; /* IVES FORK: scratch row, IVES-FORK.md 16 */
    int i, j, m, node;
    merge_type *merge;


    /* Initialize node flows with OD matrix */
    for (i = 0; i < network->numZones; i++)
        bushes->nodeFlow_par[t][i] = network->demand[origin][i];

    for (; i < network->numNodes; i++) bushes->nodeFlow_par[t][i] = 0;


    /* Descending pass for flow shifts */
    for (node = network->numNodes - 1; node > 0; node--) {
        j = bushes->bushOrder[origin][node];
        if (isMergeNode(origin, j, bushes) == FALSE) { /* Nothing to do */
            pushBackFlowSimple_par(j, origin, network, bushes);
        } else {
            m = pred2merge(bushes->pred[origin][j]);
            merge = bushes->merges[origin][m];
            if (merge->LPlink == merge->SPlink
                || (fabs(bushes->LPcost_par[t][j] - bushes->SPcost_par[t][j])
                        < parameters->minCostDifference)
                || bushes->nodeFlow_par[t][j] < parameters->minLinkFlowShift
                || merge->LPlink == NO_PATH_EXISTS)
            { /* Also nothing to do */
                pushBackFlowMerge_par(merge, network, bushes, t);
            } else {
                newtonFlowShift_par(j,merge,origin,network,bushes,parameters);
            }
        }
    }
}

/*
 * calculateBushFlows_par -- a key feature of this implementation is that bush
 *
 * flows are not persistently stored, but generated as needed.  This saves
 * memory at the expense of a little extra computation.  calculateBushFlows
 * does this by making a pass in descending topological order, loading flows
 * from the OD matrix, and splitting flows as needed.
 */
void calculateBushFlows_par(int origin,network_type *network,bushes_type *bushes) {
    const int t = thpool_worker_id; /* IVES FORK: scratch row, IVES-FORK.md 16 */

    int i, j, ij, m, node;
    merge_type *merge;


    /* Initialize node flows with OD matrix */
    for (i = 0; i < network->numZones; i++) {
        bushes->nodeFlow_par[t][i] = network->demand[origin][i];
    }
    for (; i < network->numNodes; i++) {
        bushes->nodeFlow_par[t][i] = 0;
    }
    for (ij = 0; ij < network->numArcs; ij++) {
        bushes->flow_par[t][ij] = 0;
    }

    /* Descending pass for flow calculations  */
    for (node = network->numNodes - 1; node > 0; node--) {

        j = bushes->bushOrder[origin][node];
        if (isMergeNode(origin, j, bushes) == FALSE) { /* Nothing to do */
            pushBackFlowSimple_par(j, origin, network, bushes);
        } else {
            m = pred2merge(bushes->pred[origin][j]);
            merge = bushes->merges[origin][m];
            pushBackFlowMerge_par(merge, network, bushes, t);
        }
    }

}

/*
 * pushBackFlowSimple_par -- the easy way to "split" flow.  For a non-merge node,
 * just push flow onto the predecessor.
 */
inline void pushBackFlowSimple_par(int j, int origin, network_type *network,
                        bushes_type *bushes) {
    const int t = thpool_worker_id; /* IVES FORK: scratch row, IVES-FORK.md 16 */

    int i, ij;

    ij = bushes->pred[origin][j];
    i = network->arcs[ij].tail;
    bushes->flow_par[t][ij] = bushes->nodeFlow_par[t][j];
    bushes->nodeFlow_par[t][i] += bushes->nodeFlow_par[t][j];
}

/*
 * pushBackFlowMerge_par -- the harder way to split flow, when there are multiple
 * approaches to a merge node.  IVES FORK: t_id is the scratch row, i.e. the
 * worker index (thpool_worker_id); upstream's callers passed the origin, which
 * was the same row only while rows were per origin (IVES-FORK.md 16).
 */
inline void pushBackFlowMerge_par(merge_type *merge, network_type *network,
                       bushes_type *bushes, int t_id) {

//    displayMessage(FULL_NOTIFICATIONS, "top pushback merge\n");

    int i, ij, arc;
    double flow;

    for (arc = 0; arc < merge->numApproaches; arc++) {
        ij = merge->approach[arc];
        i = network->arcs[ij].tail;
        flow = merge->approachFlow[arc];
        bushes->flow_par[t_id][ij] = flow;
        bushes->nodeFlow_par[t_id][i] += flow;
    }
}

/*
 * rectifyMerge_par -- to guard against numerical errors at a particular merge
 * node, recalculates the approach proportions.  In the degenerate case of zero
 * node flow, push everything onto the shortest path.  IVES FORK: t_id is the
 * worker index, as for pushBackFlowMerge_par.  (No caller.)
 */
void rectifyMerge_par(int j, merge_type *merge, bushes_type *bushes, int t_id) {

    int arc;
    double totalFlow = 0;

    for (arc = 0; arc < merge->numApproaches; arc++) {
        totalFlow += merge->approachFlow[arc];
    }

    if (totalFlow > 0) {
        for (arc = 0; arc < merge->numApproaches; arc++) {
            merge->approachFlow[arc] *= bushes->nodeFlow_par[t_id][j] / totalFlow;
        }
    } else {
        for (arc = 0; arc < merge->numApproaches; arc++) {
            merge->approachFlow[arc] = 0;
        }
        merge->approachFlow[merge->SPlink] = bushes->nodeFlow_par[t_id][j];
    }

}

/*
 * newtonFlowShift -- For a given merge node, use Newton's method to shift flow
 * from the longer segment to the shorter one.
 */
void newtonFlowShift_par(int j, merge_type *merge, int origin,
                     network_type *network, bushes_type *bushes,
                     algorithmBParameters_type *parameters) {
    const int t = thpool_worker_id; /* IVES FORK: scratch row, IVES-FORK.md 16 */
    double flow1, flow2, cost1, cost2, der1, der2, shift;
    int i, hi, m, c;
    merge_type *segmentMerge;


    c = origin2class(network, origin);
    bool* links = newVector(network->numArcs, bool);
    for (int k = 0; k < network->numArcs; ++k) {
        links[k] = FALSE;
    }
    i = j;
    while (i != merge->divergenceNode) {
        if (isMergeNode(origin, i, bushes) == FALSE) {
            hi = bushes->pred[origin][i];
        } else {
            m = pred2merge(bushes->pred[origin][i]);
            segmentMerge = bushes->merges[origin][m];
            hi = segmentMerge->approach[segmentMerge->LPlink];
        }
        links[hi] = TRUE;
        i = network->arcs[hi].tail;
    }
    i = j;
    while (i != merge->divergenceNode) {
        if (isMergeNode(origin, i, bushes) == FALSE) {
            hi = bushes->pred[origin][i];
        } else {
            m = pred2merge(bushes->pred[origin][i]);
            segmentMerge = bushes->merges[origin][m];
            hi = segmentMerge->approach[segmentMerge->SPlink];
        }
        links[hi] = TRUE;
        i = network->arcs[hi].tail;
    }

    for (int k = 0; k < network->numArcs; ++k) {
        if(links[k] == TRUE) {
            pthread_mutex_lock(&network->arc_muts[k]);
        }
    }

    /* Calculate information for longest path segment */
    i = j;
    cost1 = 0;
    der1 = 0;
    flow1 = merge->approachFlow[merge->LPlink];
    while (i != merge->divergenceNode) {
        if (isMergeNode(origin, i, bushes) == FALSE) {
            hi = bushes->pred[origin][i];
        } else {
            m = pred2merge(bushes->pred[origin][i]);
            segmentMerge = bushes->merges[origin][m];
            hi = segmentMerge->approach[segmentMerge->LPlink];
            flow1=min(flow1,segmentMerge->approachFlow[segmentMerge->LPlink]);
        }
        cost1 += network->arcs[hi].cost;
        der1 += network->arcs[hi].der;
        i = network->arcs[hi].tail;
    }

    /* Do same for shortest path segment */
    i = j;
    cost2 = 0;
    der2 = 0;
    flow2 = merge->approachFlow[merge->SPlink];
    while (i != merge->divergenceNode) {
        if (isMergeNode(origin, i, bushes) == FALSE) {
            hi = bushes->pred[origin][i];
        } else {
            m = pred2merge(bushes->pred[origin][i]);
            segmentMerge = bushes->merges[origin][m];
            hi = segmentMerge->approach[segmentMerge->SPlink];
            flow2=min(flow2,segmentMerge->approachFlow[segmentMerge->SPlink]);
        }
        cost2 += network->arcs[hi].cost;
        der2 += network->arcs[hi].der;
        i = network->arcs[hi].tail;
    }

    /* Determine Newton shift, truncating if need be to ensure feasibility */
    if (der1 + der2 == 0) der1 = parameters->minDerivative;
    shift = parameters->newtonStep * (cost1 - cost2) / (der1 + der2);
    shift = min(shift, flow1);
    shift = max(shift, -flow2);

    /* Now apply shift */
    i = j;
    while (i != merge->divergenceNode) {
        if (isMergeNode(origin, i, bushes) == FALSE) {
            hi = bushes->pred[origin][i];
        } else {
            m = pred2merge(bushes->pred[origin][i]);
            segmentMerge = bushes->merges[origin][m];
            hi = segmentMerge->approach[segmentMerge->LPlink];
            segmentMerge->approachFlow[segmentMerge->LPlink] -= shift;
        }
        exactCostUpdate_par(hi, -shift, network, c);
        bushes->flow_par[t][hi] -= shift;
        i = network->arcs[hi].tail;
    }
    i = j;
    while (i != merge->divergenceNode) {
        if (isMergeNode(origin, i, bushes) == FALSE) {
            hi = bushes->pred[origin][i];
        } else {
            m = pred2merge(bushes->pred[origin][i]);
            segmentMerge = bushes->merges[origin][m];
            hi = segmentMerge->approach[segmentMerge->SPlink];
            segmentMerge->approachFlow[segmentMerge->SPlink] += shift;
        }
        exactCostUpdate_par(hi, shift, network, c);
        bushes->flow_par[t][hi] += shift;
        i = network->arcs[hi].tail;
    }
    for (int k = 0; k < network->numArcs; ++k) {
        if(links[k] == TRUE) {
            pthread_mutex_unlock(&network->arc_muts[k]);
        }
    }
    deleteVector(links);
}

/*
 * checkFlows_par -- an error-checking routine which can be invoked to see if flow
 * conservation is properly maintained on the bushes.  In the release
 * implementation, all calls to this function are commented out.  IVES FORK:
 * t_id is the calling worker's index (thpool_worker_id): calculateBushFlows_par
 * writes that worker's scratch row, which the checks below then read.
 */
#define FLOW_TOLERANCE 1e-5
void checkFlows_par(network_type *network, bushes_type *bushes, int t_id) {
    int r, i, ij, kl;
    arcListElt *curArc;
    double balance;

    declareVector(double, flowCheck, network->numArcs);
    for (ij = 0; ij < network->numArcs; ij++) {
        flowCheck[ij] = 0;
    }

    for (r = 0; r < network->numZones; r++) {
        if (network->isActive[r] == FALSE) continue; /* IVES FORK 18 */
        calculateBushFlows_par(r, network, bushes);
        /* First check bush flow consistency */
        for (ij = 0; ij < network->numArcs; ij++) {
            flowCheck[ij] += bushes->flow_par[t_id][ij];
            if (bushes->flow_par[t_id][ij] < 0) {
                for (kl = 0; kl < network->numArcs; kl++) {
                    displayMessage(DEBUG, "(%ld,%ld) %f\n",
                                   network->arcs[kl].tail + 1,
                                   network->arcs[kl].head + 1,
                                   bushes->flow[kl]);
                }
                fatalError("Flow validation failed: origin %d, link (%ld,%ld)"
                           "has negative flow.", r, network->arcs[ij].tail + 1,
                           network->arcs[ij].head + 1);
            }
        }
        for (i = 0; i < network->numNodes; i++) {
            if (i == r) continue;
            balance = 0;
            for (curArc = network->nodes[i].reverseStar.head; curArc != NULL;
                 curArc = curArc->next) {
                balance += bushes->flow_par[t_id][ptr2arc(network, curArc->arc)];
            }
            for (curArc = network->nodes[i].forwardStar.head; curArc != NULL;
                 curArc = curArc->next) {
                balance -= bushes->flow_par[t_id][ptr2arc(network, curArc->arc)];
            }
            if (i < network->numZones) balance -= network->demand[r][i];
            if (fabs(balance) > FLOW_TOLERANCE) {
                for (kl = 0; kl < network->numArcs; kl++) {
                    displayMessage(DEBUG, "(%ld,%ld) %f\n",
                                   network->arcs[kl].tail + 1,
                                   network->arcs[kl].head + 1,
                                   bushes->flow_par[t_id][kl]);
                }
                fatalError("Flow validation failed: origin %d, node %ld"
                           "violates conservation.", r, i + 1);

            }
        }

    }

    /* Then check overall link flow consistency */
    for (ij = 0; ij < network->numArcs; ij++) {
        if (fabs(flowCheck[ij] - network->arcs[ij].flow) > FLOW_TOLERANCE) {
            fatalError("Flow validation failed: link (%ld,%ld) has aggregate"
                       "flow %f, but %f when disaggregated.",
                       network->arcs[ij].tail + 1,
                       network->arcs[ij].head + 1,
                       network->arcs[ij].flow, flowCheck[ij]);
        }
    }

    deleteVector(flowCheck);
}

void exactCostUpdate_par(int ij, double shift, network_type *network, int class) {
    network->arcs[ij].classFlow[class] += shift;
    network->arcs[ij].flow += shift;
    network->arcs[ij].cost=network->arcs[ij].calculateCost(&network->arcs[ij]);
    network->arcs[ij].der = network->arcs[ij].calculateDer(&network->arcs[ij]);
}

void classUpdate_par(int hi, int class, double shift,  network_type *network) {
    pthread_mutex_lock(&network->arc_muts[hi]);
    network->arcs[hi].classFlow[class] -= shift;
    pthread_mutex_lock(&network->arc_muts[hi]);
}

/*
 * IVES FORK: initialBushShortestPath_par -- the per-origin half of the pooled
 * initializeBushesB (bush.c).  Identical to initialBushShortestPath except that
 * the shortest-path labels go into a SPcost_par row rather than the single
 * shared bushes->SPcost scratch vector, which is the only thing standing
 * between the stock loop and independence.  (The row is the running worker's
 * since IVES-FORK.md 16; it was this origin's.)  Everything else it writes
 * -- pred[origin], bushOrder[origin], numMerges[origin], lastMerge[origin] --
 * is already per-origin upstream, and arcIndexBellmanFord allocates its own
 * queue and reads the network read-only.
 *
 * The serial version dumps every node and arc at DEBUG before dying on a
 * disconnected bush.  From a worker thread that dump would be interleaved
 * nonsense, so this one names the origin and the unreachable node instead;
 * rerun with <NUMBER OF THREADS> 1 for upstream's full diagnostic.
 */
void initialBushShortestPath_par(int origin, network_type *network,
                                 bushes_type *bushes,
                                 algorithmBParameters_type *parameters) {
    const int t = thpool_worker_id; /* IVES FORK: scratch row, IVES-FORK.md 16 */
    int ij, originNode = origin2node(network, origin);

    arcIndexBellmanFord(originNode, bushes->SPcost_par[t],
                        bushes->pred[origin], network,
                        parameters->SPQueueDiscipline);
    for (ij = 0; ij < network->numNodes; ij++) {
        if (ij != originNode && bushes->pred[origin][ij] == IS_MISSING) {
            fatalError("Cannot find initial connected bush for origin %d "
                       "(node %d unreachable).", origin + 1, ij + 1);
        }
    }

    bushes->numMerges[origin] = 0;
    parameters->topologicalOrder(origin, network, bushes, parameters);
}

/* IVES FORK: the scratch rows are per worker (IVES-FORK.md 16); every pool
 * entry checks it is running on a pool thread whose row exists. */
void checkScratchRow(bushes_type *bushes) {
    if (thpool_worker_id < 0 || thpool_worker_id >= bushes->numScratchRows)
        fatalError("Bush task on thread index %d, outside the %d scratch rows",
                   thpool_worker_id, bushes->numScratchRows);
}

void initBushPool(void* pVoid) {
    struct thread_args *args = (struct thread_args *) pVoid;
    checkScratchRow(args->bushes);
    initialBushShortestPath_par(args->id, args->network, args->bushes,
                                args->parameters);
    calculateBushFlows_par(args->id, args->network, args->bushes);
}

void updateBushPool(void* pVoid) {
    struct thread_args *args = (struct thread_args *) pVoid;
    int id = args->id;
    bushes_type *bushes = args->bushes;
    network_type *network = args->network;
    algorithmBParameters_type *parameters = args->parameters;
    checkScratchRow(bushes);
    updateBushB_par(id, network, bushes, parameters);
}

void updateFlowsPool(void* pVoid) {
    struct thread_args *args = (struct thread_args *) pVoid;
    int id = args->id;
    bushes_type *bushes = args->bushes;
    network_type *network = args->network;
    algorithmBParameters_type *parameters = args->parameters;
    checkScratchRow(bushes);
    args->update_flows_ret |= updateFlowsB_par(id, network, bushes, parameters);
}
#else
int empty_var = 0;  /* Empty statement to avoid compiler warning */
#endif
