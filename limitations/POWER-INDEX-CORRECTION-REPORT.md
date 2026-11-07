# Power-index approximation defect — correction report and handoff

Status: factual record + work order. Written 2026-11-05 on the ARM dev container
(`/workspaces/ontologyutils-ekaw26`), to be executed on a machine where FaCT++ is
available so the published configuration can be rebuilt and re-run.

Audience: an engineer/agent with the repositories but **no access to the conversation
this report came from**. Everything needed is stated here.

> **Read §12 before acting on this document.** The correction applied in §5 fixed the
> bad-axiom stage correctly but also changed the weakening stage, which was *not*
> defective: §3.4 and §7 point 1 are superseded, and §5.2 is reverted. The re-run in §6
> therefore does not measure the paper's weakening rule.

---

## 0. TL;DR

1. The published paper (*Guiding Ontology Repair with Axiom Weakening and Shapley
   Values*, EKAW 2026, LNAI 16897, pp. 208–223, `doi:10.1007/978-3-032-41614-8_12`)
   claims that **both** the bad axiom and the weaker axiom are selected with Shapley
   values, and reports an overall IIC of 0.77 against the default weakening baseline
   (0.95 against removal, 0.76 against MCS removal).
2. In the published build (`0ab066b`, 2026-05-23) the **approximate** Shapley
   estimator never computes the Shapley value when the target set is the whole
   universe — which is exactly the bad-axiom call. All bad-axiom scores are
   identically `0.0`, so `selectBadAxiom` degenerates to its alphabetical tie-break.
   The same class of defect affects the batched Banzhaf estimator.
3. The defect entered in commit `13ba45a` (2026-05-13), which introduced the adaptive
   estimator **and** its wiring in the same commit. It is present in the published
   build, unchanged.
4. The published numbers are genuine outputs of that code and reproduce, but they
   measure the tie-break policy, not the power index.
5. A fix is prepared (`0c03114`, then `c2d5b2e`, on branch
   `fix/power-index-adaptive-sampling` of the outer repo). On the paper's own corpus
   (`ontologies.publication`, axiom counts identical to Table 1; 8 ontologies, 800
   trials) the paper's arm gives **0.5447 [0.520, 0.570] against the default weakening
   baseline instead of 0.77** — significantly better, but a much smaller effect; six of
   the eight arms are significantly above 0.5, none significantly below. Against
   removal 0.80, against MCS removal 0.57. An intermediate state (estimator fixed but
   the weaker-axiom selector still scoring against an inconsistent context) gave parity
   or worse; the second fix is what restored the effect.
6. The plausible reason is objective misalignment, not sampling noise (see §8).
   Candidate replacement policies are listed there.

---

## 1. Repositories and commits

| what | where |
| --- | --- |
| library source (this defect) | `/workspaces/ontologyutils-ekaw26`, branch `fix/power-index-adaptive-sampling`, commit `0c03114`, based on `develop` @ `6e2caa5` |
| published build | `0ab066b` (2026-05-23, `origin/master`) |
| replication package (corpus, CLI, analysis) | `/workspaces/ontologyutils-ekaw26/repair-power-index-replication-ekaw26` |
| defect introduced | `13ba45a` (2026-05-13 18:32) — adaptive estimator + wiring |
| parallel sampling added | `25abc26` (2026-05-13, same day) |
| Shapley arm read out | `735` samples, seed `13` (`DEFAULT_APPROXIMATION_SAMPLES`, `DEFAULT_APPROXIMATION_SEED`) |

Note: `develop` has **no FaCT++ dependency** and defaults to Openllet
(`Ontology.defaultFactory`). The published build defaults to FaCT++ and has
`--reasoner {fact++,hermit,jfact,openllet}`. That is why the re-run must happen on a
FaCT++-capable machine if you want to match the paper exactly.

---

## 2. The claim at stake

Quoted from the paper:

- *"Both bad axiom and weaker axiom are selected using Shapley values."*
- *"As for the implementation of the Shapley inconsistency measure, the exact
  computation … is computationally expensive. Therefore, we have implemented the
  Monte Carlo approximative algorithm that computes the Shapley inconsistency value
  by sampling a number of permutations of the axioms in the ontology and computing
  the marginal contribution of the axiom for each permutation. Since we are
  interested in finding the axiom with the highest or the lowest Shapley
  inconsistency value, we set the number of samples to 50, and then sample more 50.
  If the top 5 axioms and the bottom 5 axioms are not the same in both samplings, we
  sample more 50 and repeat this process until we have the same top 5 and bottom 5
  axioms in two consecutive samplings."*
- Result: mean IIC overall 0.95 / 0.76 / 0.77 against random removal / not-in-largest-MCS
  removal / default weakening (in-MUS + random weakening); *"statistically
  significantly better"*; decision rule stated in the paper: an arm performs better
  if the **lower bound of the 95% CI is above 0.5**.
- Runtime: *"less than 20 seconds to repair ontologies up to 350+ axioms"*.

The **definition and the algorithm described above are correct**. The defect is in the
implementation of the sampler.

---

## 3. The defect — evidence chain in the published build (`0ab066b`)

### 3.1 The Shapley (bad-axiom) arm

1. `OntologyRepairWithPowerIndexes.java:281` passes the **same set twice**:

```java
return powerIndexBadAxiom.computeScoresAdaptive(currentAxioms, currentAxioms, 50, 1000, false);
```

2. `ShapleyInconsistencyValueApproximate.java:123-125` therefore builds
   `universe == targets`:

```java
Set<OWLAxiom> universe = new HashSet<>(axioms);
universe.addAll(targets);
```

3. `ShapleyInconsistencyValueApproximate.java:220-230` — the core defect. The prefix
   only receives **non-target** axioms:

```java
Set<OWLAxiom> prefixBase = new HashSet<>();
for (OWLAxiom current : permutation) {
    if (targets.contains(current)) {
        Set<OWLAxiom> prefixWith = new HashSet<>(prefixBase);
        prefixWith.add(current);
        double marginal = drasticInconsistencyValue(prefixWith) - drasticInconsistencyValue(prefixBase);
        localTotals.merge(current, marginal, Double::sum);
    } else {
        prefixBase.add(current);          // <- never executed when targets == universe
    }
}
```

4. `:200-203` — the value function is the drastic inconsistency value:

```java
private int drasticInconsistencyValue(Set<OWLAxiom> subset) {
    Set<OWLAxiom> key = Set.copyOf(subset);
    return drasticCache.computeIfAbsent(key, k -> Utils.isConsistent(k) ? 0 : 1);
}
```

5. **Consequence.** With `targets == universe`, `prefixBase` stays empty for the whole
   permutation, so every marginal is `v({phi}) - v({}) = 0 - 0 = 0` for every axiom
   that is not itself unsatisfiable. The averaged map is all zeros.

6. `OntologyRepairWithPowerIndexes.java:284-289` — selection then falls through to the
   string comparison:

```java
return badAxiomScores.entrySet().stream()
        .max(Comparator.<Map.Entry<OWLAxiom, Double>>comparingDouble(entry -> entry.getValue())
                .thenComparing(entry -> Utils.prettyPrintAxiomDL(entry.getKey())))
```

⇒ **the "bad axiom" is the alphabetically greatest pretty-printed axiom**, at every
iteration, on every ontology.

The one qualification: an axiom that is *itself* unsatisfiable (e.g. `⊤ ⊑ ⊥`,
`DisjointClasses(A A)`) scores `1.0` and wins. Otherwise the rule is exactly
"greatest string".

### 3.2 Why the exact path and the single-target path were fine

- `ShapleyInconsistencyValueExact` does **not** override `computeScoresAdaptive`; it
  inherits the interface default (`PowerIndex.java:55`, `:64`) which delegates to
  `computeScores` → `computeScore` → `sampleSingleTargetInParallel`, and that method
  adds **every** earlier axiom to the prefix. Exact Shapley is therefore correct.
- `computeScore` on the approximate class is also correct for the same reason.
- Only the batched "share one permutation across all targets" path drew the line in
  the wrong place.

### 3.3 The Banzhaf arm has the mirror-image defect

`BanzhafInconsistencyValueApproximate.java:217-226` with the helper at `:267-276`:

```java
Set<OWLAxiom> coalitionBase = randomCoalitionExcludingTargets(orderedUniverse, targets, random);
...
private static Set<OWLAxiom> randomCoalitionExcludingTargets(...) {
    for (OWLAxiom candidate : orderedUniverse) {
        if (!targets.contains(candidate) && random.nextBoolean()) { ... }
    }
}
```

The Banzhaf index averages over **all** coalitions of the other axioms, other
candidates included. Excluding every target makes the coalition empty whenever
`targets == universe`, so the batched Banzhaf scores were also identically `0.0`.
`computeScore` (single target, `randomCoalitionExcludingTarget`, singular) was correct.

### 3.4 The weakening arm is also wrong, more subtly

> **Superseded by §12.** The game described below *is* the paper's game for weakenings,
> so this section's premise is wrong; the weakening stage was correct in the published
> build. Kept for the record, because the "most marginals are `0`" observation in the
> last sentence is still true and still matters for tie-breaking.

For weakenings, `targets = W ⊂ universe = context ∪ W`. The prefix therefore contains
only a random subset of the *context*, never the other candidate weakenings. That is
not the Shapley value in the game on the universe either: it ignores interactions among
candidates and, because a random fragment of a context is almost always consistent,
most marginals are `0` — so `selectWeakening` (argmin) is again mostly a tie-break.

---

## 4. Measurements that establish the defect

Ontologies: `leftpolicies-small.owl` (6 axioms, from the repo), `EXON.owl` (14) and
`MFOMD.owl` (21), both from `repair-power-index-replication-ekaw26/ontologies/inconsistent-nocleanup`.

| check | published build | fixed build |
| --- | --- | --- |
| `leftpolicies-small`, exact Shapley (bad axiom) | three MUS axioms `1/3, 1/3, 1/3`; `LeftPolicy(Sweden)` `0` → correct | unchanged |
| `leftpolicies-small`, approximate | 1 distinct value, all `0.0`; selects `RaiseWelfare(Sweden)` (also the alphabetical max) | non-degenerate |
| `EXON` approximate | 1 distinct value, max `0.0`, selected == alphabetical max | 6 distinct values, max `0.48` = `Culture_medium ⊑ ⊥` (per-axiom `computeScore` gives `0.4585`, same axiom) |
| `MFOMD` approximate | 1 distinct value, max `0.0`, selected == alphabetical max | 5 distinct values, max `0.56` = `OGMS_0000061 ⊑ ⊥` (per-axiom `0.5646`, same axiom) |
| `MFOMD` Banzhaf batched | 1 distinct value, max `0.0` (per-axiom `computeScore` = `0.615`) | 5 distinct values, max `0.667`, same culprit |
| full repair loop replay, `MFOMD` | 5 iterations, `isLexMax=true` in all 5 | terminates, correct culprit |
| full repair loop replay, `EXON` | 12 iterations, `isLexMax=true` in all 12, **oscillates** `Other_medium ⊑ Culture_medium` ↔ `Other_medium ⊑ Broth_medium`, never repairs | terminates in 1 iteration, 1.0 s / 2,955 reasoner calls |
| `leftpolicies-small` full app, exact preset | reproduces the paper's Example 3 (bad axiom `RaiseWelfare(Sweden)` → weakening `LeftPolicy(Sweden)`) | unchanged |

Useful invariant that would have caught the bug immediately:
`Σ_i φ_i = v(N) − v(∅) = 1` for an inconsistent ontology. The defective estimator
returned `Σ_i 0 = 0`.

Cost note: the defective path returns after ~100 samples (the ranking is constant), so
it is fast; the corrected path does real work and is several times slower.

---

## 5. The fix

### 5.1 Shapley — make the prefix contain every predecessor

```java
Set<OWLAxiom> prefixBase = new HashSet<>();
for (OWLAxiom current : permutation) {
    if (targets.contains(current)) {
        Set<OWLAxiom> prefixWith = new HashSet<>(prefixBase);
        prefixWith.add(current);
        double marginal = drasticInconsistencyValue(prefixWith) - drasticInconsistencyValue(prefixBase);
        localTotals.merge(current, marginal, Double::sum);
    }
    prefixBase.add(current);          // unconditional
}
```

### 5.2 Banzhaf — draw the coalition over the whole universe, remove the target from it

> **Reverted by §12.** Drawing the coalition over `axioms ∪ targets` makes a candidate
> weakening a player in the game, which is not the paper's definition. The coalition
> must be drawn over `axioms` only. The Shapley change in §5.1 needs the same
> qualification. What survives from §5.1/§5.2 is the requirement that a target which
> *is* part of `axioms` sees every predecessor in its prefix/coalition — that part is
> correct and is kept.

```java
Set<OWLAxiom> coalition = randomCoalition(orderedUniverse, random);   // each axiom w.p. 1/2
for (OWLAxiom target : orderedTargets) {
    Set<OWLAxiom> coalitionBase = new HashSet<>(coalition);
    coalitionBase.remove(target);
    Set<OWLAxiom> coalitionWithTarget = new HashSet<>(coalitionBase);
    coalitionWithTarget.add(target);
    double marginal = drasticInconsistencyValue(coalitionWithTarget)
            - drasticInconsistencyValue(coalitionBase);
    localTotals.merge(target, marginal, Double::sum);
}

private static Set<OWLAxiom> randomCoalition(List<OWLAxiom> orderedUniverse, Random random) {
    Set<OWLAxiom> coalition = new HashSet<>();
    for (OWLAxiom candidate : orderedUniverse) {
        if (random.nextBoolean()) { coalition.add(candidate); }
    }
    return coalition;
}
```

`randomCoalitionExcludingTargets` (plural) becomes unused and should be deleted; keep
`randomCoalitionExcludingTarget` (singular) for `sampleSingleTargetInParallel`.

### 5.3 Do **not** change the stopping rule

The `ascending` flag already orients the comparator, so the compared end of the ranking
is the end that decides the selection (largest for bad axioms, smallest for
weakenings). Keep `limit(5)` as it is. An earlier attempt to make both ends stable was
reverted deliberately.

### 5.4 Tests added (commit `0c03114`)

- `src/test/java/www/ontologyutils/repair/powerindex/ShapleyInconsistencyValueApproximateTest.java`
  — asserts >1 distinct score when `targets == universe`, MUS axioms > 0.15,
  `LeftPolicy(Sweden)` ≈ 0, selected axiom inside the MUS, `computeScore ≈ 1/3`.
- `src/test/java/www/ontologyutils/repair/powerindex/BanzhafInconsistencyValueApproximateTest.java`
  — same shape, expected value `1/4`.

Full suite after the fix: 2118 tests, 0 failures, 0 errors, `BUILD SUCCESS`.

---

## 6. What the fix did to the published claim (full re-run)

> **Invalid for the weakening arms — see §12.** The run below scored the weakening
> candidates in a game that contains the other candidates and the bad axiom, so every
> `B` arm in the table differs from the paper in *two* respects, not one. Only the
> bad-axiom effect is isolated here (`B1`→`B4`/`B7` with random weakening, and the
> `A3` baseline, are the closest to usable). The 3×3 grid at the end of this section
> must be re-measured — done in **6.1** below.

### 6.1 `publication-K1` — the clean re-run (estimator `c2d5b2e`)

Run on `ontologies.publication` (the eight ontologies taken from the published branch;
the classifier reports bctt 207, co-wheat 377, elig 74, hom 106, ogr 82, pe 229,
taxrank 137, xeo 329 — **identical to Table 1**), jar built from `c2d5b2e`, `-n 100`,
`-K 3`, `-t 8`, Openllet. 800 successes in 801 attempts (one memory limit on elig) in
22,151 s (6.2 h).

Mean IIC and 95% CI, overall, n = 800 (bold: CI strictly above 0.5 vs A3):

| arm | vs removal (A1) | vs MCS removal (A2) | vs default weakening (A3) |
| --- | --- | --- | --- |
| in-MUS + Shapley (B2) | 0.8064 | 0.5760 | **0.5528 [0.528; 0.578]** |
| in-MUS + Banzhaf (B3) | 0.7980 | 0.5678 | **0.5513 [0.527; 0.576]** |
| Shapley + random (B4) | 0.7806 | 0.5259 | 0.5056 [0.481; 0.530] |
| **Shapley + Shapley (B5)** | 0.8025 | 0.5682 | **0.5447 [0.520; 0.570]** |
| Shapley + Banzhaf (B6) | 0.8143 | 0.5735 | **0.5509 [0.526; 0.576]** |
| Banzhaf + random (B7) | 0.7777 | 0.5351 | 0.5025 [0.478; 0.527] |
| Banzhaf + Shapley (B8) | 0.8207 | 0.5725 | **0.5584 [0.533; 0.584]** |
| Banzhaf + Banzhaf (B9) | 0.8228 | 0.5902 | **0.5673 [0.542; 0.592]** |

Against the published figures (0.95 / 0.76 / 0.77 for Shapley+Shapley):

- **The method works; the effect is real but roughly halved.** B5 gives 0.5447 against
  the baseline it claims to beat (published 0.77), 0.8025 against removal (0.95) and
  0.5682 against MCS removal (0.76). Direction preserved, magnitude much smaller.
- **Six of eight arms are significantly better** than the default weakening baseline;
  **none is significantly worse**. Best: B9 0.5673, B8 0.5584, B2 0.5528.
- Every arm remains far better than removal (0.78–0.82) and better than MCS removal
  (0.53–0.59), most significantly.
- **Heterogeneity, absent from the published table.** For B5 per ontology: ogr 0.6162,
  bctt 0.6149, hom 0.5787, taxrank 0.5522, pe 0.5491, xeo 0.5263, elig 0.4914
  (parity), **co-wheat 0.4288 [0.360; 0.498] — significantly worse than the baseline**.
- **The weakening stage now helps, reversing §6.2.** Within each bad-axiom family the
  order is random < Shapley ≈ Banzhaf: in-MUS 0.500 (by definition) < B3 0.5513 ≤ B2
  0.5528; with a Shapley bad axiom, B4 0.5056 < B5 0.5447 < B6 0.5509; with a Banzhaf
  bad axiom, B7 0.5025 < B8 0.5584 < B9 0.5673. This is the empirical confirmation of
  §12.2: the defect in the weakening stage was in the **selector** (candidates scored
  against a context that still contained the axiom being replaced), not in the
  estimator.

Runtime (overall mean per trial): A1 176 ms, A3 997 ms, B2 3,571 ms, B3 2,370 ms,
B4 14,154 ms, **B5 11,387 ms**, B6 12,393 ms, B7 10,500 ms, B8 8,479 ms, B9 8,539 ms,
A2 7,428 ms. B5 is comparable to the published Shapley column of Table 3 (10,458 ms for
elig), and 11× the baseline.

Caveats: reasoner is Openllet rather than the published FaCT++, and the machine differs,
so absolute values are not bit-identical to the paper; the comparison to Table 1 is now
like-for-like in corpus and protocol.

### 6.2 `old8-fixed-K1` — intermediate fix (superseded, invalid for the weakening arms)

Run `old8-fixed-K1` on `ontologies.old/inconsistent` (the paper's 8 ontologies),
`-n 100`, `-K 3`, `-t 8`, Openllet. **Complete: 8 of 8 ontologies, 800 successful
trials in 828 attempts (28 timeouts: TAXRANK 13, HOM 11, XEO 4); wall clock 29,360 s
(8.2 h) with 3 trials in flight.**

Mean IIC and 95% CI, overall, n = 800:

| arm | vs random removal (A1) | vs not-in-largest-MCS removal (A2) | vs default weakening (A3) |
| --- | --- | --- | --- |
| in-MUS + Shapley (B2) | 0.7777 [0.755; 0.801] | 0.5209 [0.496; 0.546] | **0.4697 [0.444; 0.496]** |
| in-MUS + Banzhaf (B3) | 0.7826 [0.759; 0.806] | 0.5343 [0.509; 0.560] | 0.4902 [0.464; 0.516] |
| Shapley + random (B4) | 0.8063 [0.785; 0.828] | 0.5687 [0.543; 0.594] | 0.5203 [0.495; 0.545] |
| **Shapley + Shapley (B5)** | 0.7931 [0.771; 0.816] | 0.5429 [0.517; 0.568] | **0.4990 [0.473; 0.525]** |
| Shapley + Banzhaf (B6) | 0.8142 [0.793; 0.836] | 0.5563 [0.531; 0.582] | 0.5084 [0.482; 0.535] |
| Banzhaf + random (B7) | 0.8140 [0.793; 0.835] | 0.5891 [0.564; 0.615] | **0.5320 [0.507; 0.557]** |
| Banzhaf + Shapley (B8) | 0.8123 [0.791; 0.834] | 0.5643 [0.539; 0.590] | 0.5217 [0.496; 0.547] |
| Banzhaf + Banzhaf (B9) | 0.8005 [0.779; 0.822] | 0.5477 [0.522; 0.573] | 0.5037 [0.478; 0.530] |

Paper for comparison (Table 2, overall): 0.95 / 0.76 / 0.77 for Shapley+Shapley,
reported as statistically significantly better.

Bold = CI strictly on one side of 0.5. Against the default weakening baseline (A3),
using the paper's own criterion (lower CI bound > 0.5):

- **B2 is significantly worse** (0.4697, CI [0.444, 0.496]) — the upper bound is
  below 0.5.
- **B5, the paper's headline arm, is at parity**: 0.4990, CI [0.473, 0.525]. Published
  0.77; now indistinguishable from the baseline it claims to beat.
- **B7 (Banzhaf bad axiom + random weakening) is the only arm significantly better**
  than the baseline: 0.5320, CI [0.507, 0.557].
- Against removal (A1) and MCS removal (A2) every arm is still better, so the
  weakening-based repairs do retain more information than removal. That part of the
  paper stands.

The ordering is monotone and consistent across all three bad-axiom policies (§7):

| bad axiom rule | random weakening | Banzhaf weakening | Shapley weakening |
| --- | --- | --- | --- |
| in-MUS (= baseline A3) | 0.500 (by definition) | 0.4902 | **0.4697** |
| Shapley | 0.5203 | 0.5084 | 0.4990 |
| Banzhaf | **0.5320** | 0.5217 | 0.5037 |

Reading down a column: using a power index on the *weakening* is always worse than
random, in the same order everywhere (Shapley worst). Reading across: using a power
index on the *bad axiom* helps slightly, Banzhaf more than Shapley. The two uses of the
index have opposite effects, and the paper's configuration (both) cancels them out.

Runtime (overall mean per trial, n = 800): A1 199 ms, A3 930 ms, B2 6,147 ms,
B3 3,854 ms, B4 9,941 ms, **B5 12,280 ms**, B6 10,760 ms, B7 8,042 ms, B8 11,533 ms,
B9 10,044 ms, A2 6,688 ms. B5 is 13× the baseline. The paper's "less than 20 seconds
for ontologies up to 350 axioms" survives only in the loosest sense, and the corrected
estimator dominates the cost.

Caveats that must be resolved before a like-for-like comparison with Table 2:

- The corpus is **not** exactly the paper's preprocessing: this folder gives HOM 133
  axioms vs 106 in Table 1, OGR 72 vs 82, CO-WHEAT 401 vs 377, XEO 303 vs 329
  (ELIG 74 = 74, BCTT 201 vs 207, PE 233 vs 229, TAXRANK 136 vs 137). Confirm which
  files back Table 1 before quoting these differences as corrections.
- 28 of 828 trials timed out (TAXRANK 13, HOM 11, XEO 4), all in B arms; only
  successful trials are analysed, so the B arms are measured on a slightly easier
  subset.

---

## 7. Why the corrected method does *worse* (diagnosis, to be verified)

> **Superseded by §12.** Both misalignments below are derived from the incorrect
> shared-game weakening scores of §6, so point 1 and the "3×3 grid" argument do not
> stand. Point 2 (bad axiom: `argmax` selects hubs) is not affected by §12 and remains
> a live hypothesis.

Two misalignments, both masked by the degenerate estimator:

1. **Weakening: argmin selects the most destructive replacement.** The Shapley value of
   a candidate weakening increases with the *strength* of that axiom (a stronger axiom
   more often tips a prefix into inconsistency). `selectWeakening` takes the **smallest**
   value, i.e. systematically the weakest / most information-destroying candidate. This
   predicts that Shapley-selected weakenings do worse than random ones — consistent with
   B2 (`0.4572`) being the worst arm.
2. **Bad axiom: argmax selects hubs.** The axiom with the highest Shapley value is the
   one in the most MUSes, typically a hub that also carries many entailments. Weakening
   it destroys more information in one step. This predicts that changing only the
   bad-axiom rule does not help — consistent with B4 (`0.4963`) ≈ baseline,
   whereas the same arm with a random bad-axiom rule is the baseline itself.

Both objectives optimise *responsibility for inconsistency*; IIC measures *entailments
preserved*. On these ontologies they appear anti-correlated.

**Direct evidence (full run, §6).** The corrected estimator gives a clean 3×3 grid of
bad-axiom rule × weakening rule. Within every bad-axiom family the weakening policy
orders identically — random best, then Banzhaf, then Shapley (e.g. in-MUS: 0.500 /
0.4902 / 0.4697; Shapley: 0.5203 / 0.5084 / 0.4990; Banzhaf: 0.5320 / 0.5217 / 0.5037).
That is a monotone effect of the weakening objective, not sampling noise, and it
matches the prediction of point (1). The bad-axiom side shows the opposite sign: with
random weakenings, in-MUS 0.500 → Shapley 0.5203 → Banzhaf 0.5320, only the last being
significantly above 0.5.

Still to confirm for (1): the rank correlation between a candidate weakening's index
and its information loss $d(\varphi,\varphi')$ on a sample of steps.

---

## 8. Policies to try next

Shared pre-condition: the index is monotone in strength, and the drastic value produces
massive ties. Any policy must produce an *informative* score, not merely a correct one.

- **P1 (cheapest, highest expected payoff) — repair-constrained argmax.**
  `argmax π(φ')` over `φ' ∈ W(φ)` with `v((O \ {φ}) ∪ {φ'}) = 0`. Two lines in the weaker
  selector. Tests finding (1) directly.
- **P2 — cost-aware scalarisation.** `score(φ') = π(φ') − λ · d(φ, φ')`, with `d` the
  refinement-lattice depth or the size difference of normal forms. Sweep λ; `λ = 0`
  recovers P1, `λ → ∞` recovers "nearest weakening".
- **P3 — graded value function.** Replace the drastic `{0,1}` with e.g. `|MUS(S)|` or a
  hitting-set size. Shrinks tie mass and makes "minimise residual inconsistency"
  meaningful at intermediate steps. Cost: MUS counting is expensive.
- **P4 — score the substitution as one action:** `π(φ → φ')` over
  `(O \ {φ}) ∪ {φ'}`, with a greedy ratio `Δinconsistency / Δinformation` per step.
- **P5 — control and report the tie-break.** Replace `prettyPrintAxiomDL` with a
  defensible rule (nearest weakening / shortest axiom / fewest entailments lost) and
  publish the tie fraction per step as a first-class metric.
- **Baseline that must be added: "nearest valid weakening, no power index".** If the
  index cannot beat it, that is the honest finding.

Suggested sequence: (i) diagnostic on one ontology logging score distribution, tie
fraction, chosen candidate and `d(φ, φ')`, and the rank correlation between `π` and `d`;
(ii) screen P1, P2 (two λ), and the nearest-weakening baseline on ELIG/HOM/OGR at
`-n 100`; (iii) full 8 ontologies for survivors.

---

## 9. Work order for the FaCT++ machine

### 9.1 Environment

- Java 17, Maven. FaCT++ must resolve (`ontologyutils:factplusplus:1.7.0.3`, or the
  in-repo `lib/factplusplus-1.7.0.3.jar`).
- Python 3.12 venv for the orchestrator (`repair-power-index-replication-ekaw26/.venv`).

### 9.2 Baselines to capture before touching anything

```bash
git -C <repo> checkout 0ab066b            # published build, FaCT++ default
mvn -q -B clean package -DskipTests       # ALWAYS clean: stale JDT classes otherwise
cp target/shaded-ontologyutils-0.1.0.jar <pkg>/lib/shaded-ontologyutils-0.1.0.jar
```

Reproduce the defect on two or three ontologies and record the numbers:

```bash
java -cp <pkg>/lib/shaded-ontologyutils-0.1.0.jar \
  www.ontologyutils.apps.RepairWithPowerIndexes \
  --preset troquard2018 --power-index-shapley-approximate --reasoner fact++ \
  -o /tmp/out.owl <pkg>/ontologies.old/inconsistent/EXON.owl
```

Acceptance: the bad-axiom score map has a single distinct value (`0.0`) and the chosen
axiom equals the alphabetical maximum of `Utils.prettyPrintAxiomDL`; on `EXON` the loop
oscillates and never repairs.

### 9.3 Fix on main

Apply §5.1 and §5.2 to `master`/`main` (the classes there are the pre-strategy versions,
so the patch goes directly into `OntologyRepairWithPowerIndexes`-facing code), delete the
now-unused plural coalition helper, add the two regression tests from §5.4 (adapting
package/paths: on main they live in `www.ontologyutils.repair.powerindex`), then:

```bash
mvn -q -B clean package -DskipTests
mvn -B test                                # expect 0 failures
```

Acceptance checks after the fix, all on the same jar:

- `Σ_i φ_i ≈ 1` for an inconsistent ontology with `targets == universe`.
- `leftpolicies-small` exact: `1/3, 1/3, 1/3` on the MUS axioms, `0` elsewhere;
  selection → `RaiseWelfare(Sweden)` → `LeftPolicy(Sweden)` (matches the paper's
  Example 3).
- `EXON` / `MFOMD` approximate: >1 distinct value, top axiom `Culture_medium ⊑ ⊥` /
  `OGMS_0000061 ⊑ ⊥`, not the alphabetical maximum, repair terminates.
- Banzhaf batched: >1 distinct value, same culprits as `computeScore`.

### 9.4 Re-run the experiments

Two runs, same 8 ontologies, same seeds, same `-n 100`, differing only in the jar:

```bash
# control: pre-fix jar in its own lib dir (no need to overwrite lib/)
mkdir -p lib-prefix && cp lib/backup/shaded-ontologyutils-0.1.0.prefix-fix-*.jar lib-prefix/shaded-ontologyutils-0.1.0.jar

python -m cli.run_trials_parallel --inconsistent-dir ontologies.old/inconsistent \
  --lib-dir lib-prefix --run-id old8-broken-K1 \
  -n 100 -K 3 -t 8 --java-mem '-Xms2g -Xmx6g -Xss8m' --notify

python -m cli.run_trials_parallel --inconsistent-dir ontologies.old/inconsistent \
  --run-id old8-fixed-K1 \
  -n 100 -K 3 -t 8 --java-mem '-Xms2g -Xmx6g -Xss8m' --notify
```

Notes:
- Keep `K * t` within the CPU budget (`-K 3 -t 8` = 24 threads) and `K * Xmx` well
  below RAM; check the cgroup `oom_kill` counter to distinguish OOM from starvation.
- Do **not** pass `--no-notify` when Telegram credentials exist (`--notify` forces it
  on; credentials come from `.env` or the environment).
- The orchestrator resumes when pointed at an existing `--run-id`.
- Interim analysis runs after every ontology; results land in
  `data/results-<run-id>/` (CSV + `combined_report.md`).

### 9.5 Analysis

```bash
python -m cli.analyze_results data/results-old8-fixed-K1 data/results-old8-fixed-K1
```

Then compare, per ontology and overall: IIC against A1/A2/A3, success rates, runtimes,
and the **tie fraction** of each power-index step.

---

## 10. Reporting requirements (academic integrity)

The article is in the version of record (Springer LNAI 16897, with a DOI). The
classification of what happened matters:

- **Not fabrication or falsification.** The numbers are genuine outputs of the released
  code and reproduce. The definition and the described algorithm are correct.
- **An honest implementation error plus a validation failure.** The harness computed a
  different quantity than described, and no check existed on the intermediate quantity
  (the `Σφ = 1` invariant or a `targets == universe` unit test would have caught it).
- **But one specific claim in the record is unsupported**, and it is the empirical
  justification of the paper's thesis. That obliges a correction.

What the correction must state:

1. The released code for the approximate power index did not compute the quantity
   described; bad-axiom scores were identically zero for every ontology, and selection
   fell back to the alphabetical tie-break.
2. The reported figures are reproduced as outputs of that code, so the effect measured
   was the tie-break policy, not power-index guidance.
3. Therefore the claim that Shapley/Banzhaf guidance improves the repair is not
   supported as published.
4. Corrected numbers under the same protocol (with the caveats in §6), including the
   negative result.
5. What remains valid: the formalisation, the exact-Shapley variant (including
   Example 3, verified), the weakening operator and its OWL 2 DL engineering, the
   corpus construction and the harness.

What must **not** be done: retracting the method or the definitions, describing the
episode more strongly than the evidence supports, or quietly re-running without a
notice. Route: a published correction linked to the DOI (Springer has a
"Correction to: …" article type), coordinated with the volume editors; an expression of
concern or retraction-with-replacement if the editors judge the central finding void.

Evidence to archive now, before anything is rewritten:

- commit `13ba45a` (2026-05-13) — where the estimator and its wiring entered together;
  `0ab066b` (2026-05-23) — the published build containing it unchanged;
- the run directories in the replication package timestamped **2026-05-30**
  (`data/data-20260530053655536655` and siblings), which date the evaluation to the
  defective code and close the "maybe an uncommitted local variant" gap;
- the defect demonstration logs from §4 (score dumps, `isLexMax` replays).

Disclose the correction in the follow-up submission; most venues have an explicit field
for it, and proactive disclosure is strongly to the authors' credit.

---

## 11. Open items

- Confirm which input files back Table 1 of the paper (the current
  `ontologies.old/inconsistent` differs on HOM, OGR, CO-WHEAT, XEO).
- Run the pre-fix control (`old8-broken-K1`, using `--lib-dir lib-prefix` as in §9.4)
  for an apples-to-apples A/B on the estimator alone; `old8-fixed-K1` is complete
  (8/8, 800 trials) and its results are in `data/results-old8-fixed-K1/`. Note (§12.4)
  that the useful control is the pre-fix build, which is degenerate at the bad-axiom
  step but correct at the weakening step; `old8-fixed-K1` is not that control.
- Verify the monotonicity claim of §7 empirically (rank correlation between a weakening's
  power-index score and its information loss) before building P1/P2 on it.
- Decide whether the follow-up reports the corrected negative result alone or together
  with one of the P1–P5 policies.

---

## 12. Addendum — 2026-11-06: the weakening stage was never defective, and §5 broke it

Written on the Windows machine after checking the definitions against the paper source
(`main-flattened.tex`, EKAW 2026 camera-ready).

### 12.1 The paper fixes both games explicitly

| Stage | Paper | Line |
| --- | --- | --- |
| `FindBadAxiom` | `argmax_{φ ∈ O} S_φ(O)` | `main-flattened.tex:296` |
| `SelectWeakerAxiom` | `argmin_{φ' ∈ Φ\{φ_bad}} S_{φ'}(O \ {φ_bad} ∪ {φ'})` | `main-flattened.tex:299` |

Prose to the same effect: *"the Shapley inconsistency value of each weakening
`φ' ∈ Φ_weaker` with respect to its candidate ontology `O \ {φ_bad} ∪ {φ'}`"*
(`:287`) and *"`SelectWeakerAxiom` also needs the ontology `O` as input to compute the
Shapley inconsistency value of each weakening with respect to the candidate ontology
`O \ {φ_bad} ∪ {φ'}`"* (`:289`). The definition `S_φ(O)` (`:269`) quantifies over
subontologies of one fixed universe, so the game is `N = O` for bad axioms and
`N = O \ {φ_bad} ∪ {φ'}` — a **different game per candidate** — for weakenings.

Consequences, in the form of three rules the implementation must satisfy:

- **R1** every target is scored in `axioms ∪ {target}`, so a batch call must agree with
  `PowerIndex.computeScore(axioms, target)` — this is also the contract that
  `PowerIndex.computeScores`' default implementation and its Javadoc state;
- **R2** the bad-axiom call therefore uses `axioms = O`, and every axiom of `O` is a
  player, so the prefix/coalition of an axiom must contain *all* its predecessors —
  this is the part of §5 that is correct;
- **R3** the weakening call must use `axioms = O \ {φ_bad}` and no candidate may be a
  player of another candidate's game.

### 12.2 §3.4 and §7 point 1 are wrong: the published weakening stage was correct

The published build (`0ab066b`) routes its experiments through
`OntologyRepairWithPowerIndexes` (`apps/SingleTrialExperiment.java:createPowerIndexRepair`,
no strategy classes existed then), and that class already scored weakenings on
`currentAxioms \ {badAxiom}` (`axiomSetWithoutBadAxiom`).

In the pre-fix batched sampler, for `targets ∩ axioms = ∅`, `prefixBase` accumulated
only non-target axioms. A target's position in a permutation of
`universe = axioms ∪ targets` is uniform, so the set of non-target axioms preceding it
is a **uniform random subset of `axioms`**, and the estimator is exactly the Shapley
marginal of that candidate in `axioms ∪ {φ'}`. Same argument for
`randomCoalitionExcludingTargets` and Banzhaf. With `axioms = O \ {φ_bad}` this is the
paper's game, by R1.

So in the published build:

- the **bad-axiom stage was defective** (all scores `0.0`, alphabetical tie-break) —
  §3.1–3.3 stand;
- the **weakening stage was correct** per the paper — §3.4 and §7 point 1 do not;
- the published Table 2 still measures a tie-break at the bad-axiom step, so §10's
  reporting requirement is unchanged: one claim in the record is unsupported.

One nuance from §3.4 survives and should be measured, not asserted: with the drastic
value on a mostly-consistent `O \ {φ_bad}`, most weakening marginals are `0`, so
`argmin` is frequently decided by the alphabetical tie-break too. The paper's own
Example 3 selects a weakening whose value is `0.00`. That is a property of `I_d` plus
`argmin`, not an implementation defect. Report the tie fraction per step.

### 12.3 What `0c03114` actually did, and the corrected patch

`0c03114` fixed the bad-axiom stage (R2) but, in the same change, made both samplers
draw the permutation/coalition over `axioms ∪ targets`. With `targets = Φ_weaker` that
violates R3: each candidate becomes a player in its siblings' game, the score stops
depending only on the candidate and the ontology, `Σ_i φ_i = v(N) − v(∅)` over that
game must be shared with the context *and* the siblings, and the approximate arm stops
agreeing with the exact arm (which goes through the interface default →
`computeScore` → per-target game).

The corrected patch keeps R2 and enforces R1/R3:

- `ShapleyInconsistencyValueApproximate.sampleTargetsInParallel` samples permutations
  of the **context**; targets that belong to the context take their prefix from the
  permutation (bad-axiom case), and every other target is inserted at a uniformly drawn
  cut of that permutation, which is a uniform permutation of `axioms ∪ {target}`
  (weakening case);
- `BanzhafInconsistencyValueApproximate.sampleTargetsInParallel` draws the coalition
  over the context only and uses `coalition \ {target}` as the base, which is uniform
  over `(axioms ∪ {target}) \ {target}` in both cases;
- `PowerIndexWeakerAxiomSelector` removes the bad axiom from the scoring context, so the
  experiments' path also uses `O \ {φ_bad}` (it was passing `refutableAxioms()`, which
  still contains `φ_bad`; `OntologyRepairWithPowerIndexes` already did this correctly);
- the Javadoc of `PowerIndex.computeScores`/`computeScoresAdaptive` and of both
  approximate classes now states R1.

Regression tests (`batchScoresUseTheGameOfEachTargetAlone`, one per index) use a
two-axiom context in which the candidates are harmless alone: the batch must return
exactly `0`, whereas the shared game returns `1/2`. A second assertion checks
`batch == computeScore` and the analytic value (`1/3` Shapley, `1/4` Banzhaf). Targeted
suite after the change: 38 tests, 0 failures.

Each batch sample still costs two reasoner calls per candidate, so the correction does
not change the asymptotic cost of a step.

### 12.4 The §6 re-run is invalid for the weakening arms

`old8-fixed-K1` was produced with `0c03114` *and* with the strategy selectors, i.e. its
weakening candidates were scored in `O ∪ Φ_weaker` with `φ_bad` still a player. Every
`B` arm in §6 therefore differs from the paper in two independent ways. In particular
the monotone "random > Banzhaf > Shapley" ordering across weakening policies in §6 and
§7 — the main diagnostic argument of the report — is an artefact of that game and must
be re-measured. The bad-axiom conclusion is not affected.

What this means for the correction:

- the conclusion "Shapley guidance does not beat the default weakening baseline as
  published" still stands, because the published bad-axiom step was a tie-break;
- but the *magnitude* and any statement about the weakening rule need a clean re-run
  with the paper's game — see 12.5;
- §7 point 2 (bad axiom `argmax` selects hubs) and §8 (P1–P5) remain open hypotheses.

### 12.5 The clean re-run

Run id `old8-paperdef-K1`, on the Windows machine
(`C:\Users\josecoliveira\projects\ekaw26\ontologyutils`), with the jar built from the
corrected patch:

```bash
cd repair-power-index-replication-ekaw26
# jar: ../target/shaded-ontologyutils-0.1.0.jar -> lib/shaded-ontologyutils-0.1.0.jar
#      (previous jar backed up in lib/backup/)
python -m cli.run_trials_parallel --run-id old8-paperdef-K1 \
  -n 100 -K 2 -t 8 --java-mem "-Xms2g -Xmx5g -Xss8m" --notify
```

Corpus: `ontologies/inconsistent` (the same 8 ontologies; `elig` = 74 axioms confirmed
by the classifier). `-K 2 -t 8` instead of `-K 3 -t 8` because the machine has 16
logical cores and 15.3 GB RAM; use `-K 3 -t 8` only on a machine with ≥ 24 cores and
≥ 20 GB. Progress in `data/data-old8-paperdef-K1/`, interim report in
`data/results-old8-paperdef-K1/combined_report.md`, Telegram notifications on.

Before any of this the Python environment had to be rebuilt: the package's `.venv` was
a Linux venv created in `/workspaces/...` and could not run on Windows. Recreate it with
`python -m venv .venv` and `python -m pip install -r requirements.txt`; `requests` and
`rich` are enough for the notifier and the runner, the full set is needed because
`analysis.orchestrator` imports `analysis.analyzer` (numpy/pandas/scipy/statsmodels).

Also fixed while preparing the run: `.env` contained `TELEGRAM_CHAT_ID=123456789`, a
placeholder, so the notifier failed with `Bad Request: chat not found`. The real ID is
obtained with `python -m cli.telegram_notify chat-id` (the bot is `@jcolabwatcherbot`
and must have received a message first).

### 12.6 Residual deviations from the paper (minor, unchanged)

- `O` in the code is `ontology.refutableAxioms()`; static axioms are players in no
  coalition. The paper's `O = {T, A}`. Consistent across the exact and approximate
  indices, but worth one sentence in an implementation section.
- `main-flattened.tex:296` writes `argmax_{φ ∈ O'}` — presumably a typo for `O`.
- The paper's stopping rule is "same top 5 **and** bottom 5 in two consecutive
  samplings" (§4 of the paper); the code compares only the end of the ranking that
  decides the selection. Sample-count effect only.
- The adaptive stopping rule inspects the sampled values, so the returned averages are
  not unbiased estimates of `S_φ`; they are usable for ordering, not to be reported as
  estimates with sampling error.

---

## 13. What remains true of the published claim — 2026-11-07

All figures below are from `publication-K1` (§6.1): corpus identical to Table 1,
estimator `c2d5b2e`, Openllet, 8 ontologies, 800 trials, 95% CI, n = 800.

### 13.1 Verdict per claim

| # | claim in the paper | status | evidence |
| --- | --- | --- | --- |
| 1 | the Shapley inconsistency value, and using power indexes to guide repair, is formally sound | **holds** | untouched by the defect; §3.2 |
| 2 | the Monte Carlo estimator with 50+50 adaptive sampling implements that definition | **only now** | the published build did not (§3.1); `c2d5b2e` does |
| 3 | Example 3 illustrates the mechanism | **holds** | exact Shapley, reproduces exactly (§4) |
| 4 | power-index repair preserves much more information than removal | **holds, strong** | B5 0.8025 vs published 0.95 |
| 5 | …than MCS removal | **holds, weaker** | B5 0.5682 vs published 0.76 |
| 6 | …than the default weakening baseline, overall IIC **0.77** | **direction holds, number does not** | B5 0.5447 [0.520; 0.570]; significant, about half the published size |
| 7 | selecting **both** the bad axiom and the weakening by Shapley values is what produces the improvement | **not supported** | 13.2: the weakening carries it, the bad-axiom index does not measurably contribute |
| 8 | the improvement is uniform across ontologies | **false** | co-wheat 0.4288 [0.360; 0.498], significantly worse than the baseline |
| 9 | runtime is practical; "less than 20 seconds … up to 350+ axioms" | **broadly holds, not literally** | 13.3: xeo 47 s, co-wheat 20 s; same order as Table 3 |

### 13.2 The weakening selection carries the effect; the bad-axiom selection does not

Mean IIC against the default weakening baseline, by stage:

| bad axiom rule | random weakening | Shapley weakening | Banzhaf weakening |
| --- | --- | --- | --- |
| in-MUS (= baseline) | 0.500 (by definition) | 0.5528 [0.528; 0.578] | 0.5513 [0.527; 0.576] |
| Shapley | 0.5056 [0.481; 0.530] | 0.5447 [0.520; 0.570] | 0.5509 [0.526; 0.576] |
| Banzhaf | 0.5025 [0.478; 0.527] | 0.5584 [0.533; 0.584] | 0.5673 [0.542; 0.592] |

- **Down a column** (effect of the weakening index, bad axiom held fixed): +0.04 to
  +0.06, every interval clear of 0.5.
- **Across a row** (effect of the bad-axiom index, weakening held fixed): +0.003 to
  +0.02, every interval overlapping 0.5. With n = 800 the CI half-width is about
  ±0.025, so an effect of 0.01 would be undetectable; "no evidence that it helps" is
  the correct statement, not "it has no effect".

This inverts the narrative of the paper, which is *identify the axiom responsible for
the inconsistency, then weaken it*: the discriminating decision is which replacement to
install, not which axiom to blame.

### 13.3 Runtime (B5, per ontology) against Table 3

| ontology | this run (ms) | Table 3, Shapley column (ms) |
| --- | --- | --- |
| elig | 1,841 | 10,458 |
| ogr | 1,116 | 1,656 |
| hom | 2,331 | 2,391 |
| taxrank | 3,278 | 8,764 |
| bctt | 5,529 | 2,835 |
| pe | 9,748 | 6,625 |
| xeo | 47,237 | 57,623 |
| co-wheat | 20,016 | 15,546 |

Overall mean 11,387 ms against a baseline of 997 ms: about 11×. Same order as the
published figures (sometimes faster, sometimes slower), so the feasibility claim stands
and the "under 20 s" phrasing does not: xeo exceeds it in both the published run and
this one.

### 13.4 The negative outlier, and what it does to §7

`co-wheat` is significantly worse than the baseline for B5 (0.4288 [0.360; 0.498]),
while the published table had no such case. Two consequences:

- The method is **not uniformly beneficial**, and a per-ontology analysis (MUS structure,
  number of candidate weakenings, tie rate) becomes a real research question rather
  than a footnote.
- **§7 point 1 does not survive this run.** It predicted that minimising the power index
  of a weakening selects the most destructive replacement; with the corrected game the
  index in fact helps. How much of the gain comes from the score ordering, and how much
  from eliminating candidates that fail to repair (which now score above zero), is not
  measured. The concrete diagnostic is the **tie fraction at weakening steps** together
  with the rank correlation between candidate score and information loss
  $d(\varphi,\varphi')$ — neither is recorded yet.

### 13.5 Consequences: correction versus follow-up

**Correction** (for the version of record): claims 2, 6, 8, 9 — the implementation did
not compute the index as described; with the corrected code the advantage over the
default weakening baseline is 0.54 rather than 0.77 and is not uniform; the runtime
phrasing needs adjusting.

**Follow-up**: claim 7, the mechanism. The supported statement is *choosing the
weakening by power index improves information retention over random weakening by about
4–6 IIC points at roughly 2× the runtime; choosing the bad axiom by power index does
not measurably help*. That is smaller but cleaner, and it comes with a reusable
estimator semantics (`axioms ∪ {target}` per candidate), regression tests, and a
negative outlier worth explaining.

### 13.6 Still open

- **Reasoner.** All figures here are Openllet; the paper used FaCT++. The erratum needs
  the same run on the FaCT++ machine, otherwise the difference can be attributed to the
  reasoner.
- **Control.** The pre-fix jar on `ontologies.publication` has not been run. Until it
  is, "the published 0.77 came from the defective code" rests on code analysis plus the
  `old8-fixed-K1` comparison, not on a like-for-like number.
- **Power.** The bad-axiom question needs many more trials, or a targeted experiment
  with a strong bad-axiom baseline and the weakening fixed.
- **Generality.** Eight ontologies from one preprocessing pipeline; the heterogeneity
  argues for a larger corpus before any universal claim.
