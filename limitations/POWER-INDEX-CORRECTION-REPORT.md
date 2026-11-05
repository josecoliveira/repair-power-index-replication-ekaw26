# Power-index approximation defect — correction report and handoff

Status: factual record + work order. Written 2026-11-05 on the ARM dev container
(`/workspaces/ontologyutils-ekaw26`), to be executed on a machine where FaCT++ is
available so the published configuration can be rebuilt and re-run.

Audience: an engineer/agent with the repositories but **no access to the conversation
this report came from**. Everything needed is stated here.

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
5. A fix is prepared (`0c03114` on branch `fix/power-index-adaptive-sampling` of the
   outer repo). With the fix, a partial re-run (3 of 8 ontologies, 100 trials each)
   gives **≈0.50 against the default weakening baseline instead of 0.77**, i.e. no
   advantage; by the paper's own criterion (lower CI bound > 0.5) no arm qualifies.
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

## 6. What the fix did to the published claim (partial re-run)

Run `old8-fixed-K1` on `ontologies.old/inconsistent` (the paper's 8 ontologies),
`-n 100`, `-K 3`, `-t 8`, Openllet. Partial status at the time of writing: **3 of 8
ontologies complete** (ELIG, HOM, OGR; 300 successful trials, 11 timeouts on HOM in B
arms). Remaining: TAXRANK, BCTT, PE, XEO, CO-WHEAT.

Mean IIC and 95% CI, overall over the three ontologies:

| arm | vs random removal (A1) | vs not-in-largest-MCS removal (A2) | vs default weakening (A3) |
| --- | --- | --- | --- |
| in-MUS + Shapley (B2) | 0.7246 [0.688; 0.761] | 0.5226 [0.481; 0.565] | **0.4572 [0.414; 0.501]** |
| in-MUS + Banzhaf (B3) | 0.7365 [0.699; 0.774] | 0.5379 [0.495; 0.581] | 0.4791 [0.435; 0.523] |
| Shapley + random (B4) | 0.7470 [0.712; 0.782] | 0.5486 [0.505; 0.592] | 0.4963 [0.453; 0.540] |
| **Shapley + Shapley (B5)** | 0.7305 [0.693; 0.768] | 0.5422 [0.499; 0.585] | **0.4954 [0.452; 0.539]** |
| Shapley + Banzhaf (B6) | 0.7682 [0.734; 0.803] | 0.5488 [0.504; 0.593] | 0.5263 [0.480; 0.572] |
| Banzhaf + random (B7) | 0.7541 [0.717; 0.791] | 0.5882 [0.545; 0.632] | 0.5398 [0.499; 0.580] |
| Banzhaf + Shapley (B8) | 0.7725 [0.737; 0.808] | 0.5723 [0.528; 0.616] | 0.5243 [0.482; 0.567] |
| Banzhaf + Banzhaf (B9) | 0.7464 [0.711; 0.782] | 0.5724 [0.529; 0.616] | 0.5275 [0.484; 0.571] |

Paper for comparison (Table 2, overall over 8 ontologies): 0.95 / 0.76 / 0.77 for
Shapley+Shapley, and it is stated to be statistically significantly better.

Interpretation, stated carefully:

- By the paper's own criterion (**lower CI bound > 0.5**) **no arm qualifies** on this
  partial set. The published improvement is gone.
- B5 vs the default weakening baseline is `0.4954`, i.e. the point estimate is *below*
  0.5: the corrected Shapley repair retains slightly less information than the baseline.
  On three ontologies this is not yet significant at 95% (the CI straddles 0.5), so the
  accurate statement is "no evidence of improvement, point estimate below the
  baseline". B2's interval `[0.414, 0.501]` is the closest to significantly worse.
- Runtime went up as expected: overall mean per trial `A3 = 529 ms`, `B5 = 3067 ms`.

Caveats that must be resolved before comparing to Table 2:

- Only 3 of 8 ontologies, and they are the three smallest.
- The corpus is **not** exactly the paper's preprocessing: this folder gives HOM 133
  axioms vs 106 in Table 1, OGR 72 vs 82, CO-WHEAT 401 vs 377, XEO 303 vs 329
  (ELIG 74 = 74, BCTT 201 vs 207, PE 233 vs 229, TAXRANK 136 vs 137). Confirm which
  files back Table 1 before drawing conclusions.
- HOM had 11 timeouts, all in B arms (B5 5, B9 3, B8 2, B6 1); only successful trials
  are analysed.

---

## 7. Why the corrected method does *worse* (diagnosis, to be verified)

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
- Finish `old8-fixed-K1` (5 ontologies outstanding) and then run the pre-fix control
  (`old8-broken-K1`) for an apples-to-apples A/B on the estimator alone.
- Verify the monotonicity claim of §7 empirically (rank correlation between a weakening's
  power-index score and its information loss) before building P1/P2 on it.
- Decide whether the follow-up reports the corrected negative result alone or together
  with one of the P1–P5 policies.
