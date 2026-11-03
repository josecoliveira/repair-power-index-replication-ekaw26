# Minimal reproductions of two algorithmic limitations

Two small ontologies that reproduce, in isolation, the two failure modes observed
while building and running the no-cleanup corpus (`ekaw26-nocleanup-K1`). Both are
limitations of the algorithms, not of the corpus, and should be reported as such.

All commands assume the bundled jar and the package root:

```bash
P=/path/to/repair-power-index-replication-ekaw26
J=$P/lib/shaded-ontologyutils-0.1.0.jar
```

## 1. `case1-fixedpoint-top.owl` — `MakeInconsistent` never terminates

A consistent ontology with a single axiom `owl:Thing ⊑ A`, `A` a leaf class.

Strengthening a subsumption generalises its left side and/or specialises its right
side. Here the left side is `owl:Thing` (no proper superclass) and the only
right-hand candidate is `owl:Nothing`, giving `owl:Thing ⊑ owl:Nothing`, which is
discarded by the "too strong" filter because it is inconsistent on its own. The
candidate set is therefore always empty, nothing is added, and the ontology never
becomes inconsistent. The loop

```java
// MakeInconsistent.java:99
while (isConsistent || iter < minIter || iterSinceInconsistency < minAfter) {
    ...
    if (!strongerAxioms.isEmpty()) {          // :123 - never true here
        ontology.addAxioms(strongerAxiom);
    }
    isConsistent = ontology.isConsistent();
}
```

has no iteration cap and no no-progress check (`minIter = minAfter = 0`), so it
spins until the process is killed or the heap is exhausted.

```bash
timeout 90 java -Xms1g -Xmx16g -cp $J www.ontologyutils.apps.MakeInconsistent \
  --normalize --basic-cache --strict-sroiq --strict-simple-roles \
  --simple-ria-weakening --strict-owl2 --verbose \
  -o /tmp/c1.owl limitations/case1-fixedpoint-top.owl
```

Observed (16 GB heap):

```
exit=124  wall=93s        # killed by the timeout
"adding axiom" lines: 0   # no progress at all
"BEGIN RESULT" marker: 0  # the loop never exited
```

Observed (4 GB heap): the same run dies after ~39 s with
`java.lang.OutOfMemoryError: Java heap space` inside `OWLAPIStreamUtils.streamFromSorted`,
i.e. the loop also grows memory while making no progress. Which of the two
happens (timeout or OOM) depends only on the heap size and the external cap.

### Negative results (worth knowing)

* A TBox-only ontology **can** still be made inconsistent by a chain of
  strengthenings, e.g. `⊤ ⊑ D` followed by `D ⊑ ∃r.⊥`; the single-axiom
  "too strong" filter does not catch inconsistencies that need two axioms. Such
  ontologies terminate, so they are not reproducers.
* `⊤ ⊑ ⊥` (a one-axiom inconsistent ontology) **is** repairable: the weakener
  produced the tautology `⊤ ⊑ ⊤`. It is therefore *not* a good reproducer for
  limitation 2.

## 2. `case2-noweakening-top-assertion.owl` — weakening repair aborts

Inconsistent ontology with three axioms: `owl:Thing(a)` (a tautology, but a
*refutable* axiom), `A ⊑ owl:Nothing` and `A(b)`.

The power-index bad-axiom selectors do not restrict themselves to axioms of a MUS,
so the tautology `owl:Thing(a)` can be chosen as the bad axiom. It cannot be
weakened — its type would have to be generalised, and `owl:Thing` has no proper
superclass — and the repair throws instead of skipping the axiom:

```java
// OntologyRepairWeakening.java:186,193-195
while (!isRepaired(ontology)) {
    ...
    var maybeWeaker = weakerSelector.selectBest(badAxiom, axiomWeakener, ...);
    if (maybeWeaker.isEmpty()) {
        throw new IllegalStateException("Could not find a weakening for axiom: " + ...);
    }
```

```bash
java -Xms512m -Xmx4g -cp $J www.ontologyutils.apps.SingleTrialExperiment \
  --ontology limitations/case2-noweakening-top-assertion.owl \
  --seed 0 --run-id repro \
  --removal-timeout-secs 120 --weakening-timeout-secs 120 \
  --power-index-timeout-secs 120 --make-inconsistent-timeout-secs 120
```

Observed (deterministic; seeds 0-4 all behave identically):

```
trial_status:  failure
failure_stage: B4                      # Shapley bad-selector + random weakener
error_message: Repair failed: ExecutionException:
               java.lang.IllegalStateException:
               Could not find a weakening for axiom: ⊤(a)
```

The same ontology also fails with the standalone app, but only probabilistically,
because the default bad-axiom strategy (`in-some-mus`) never selects tautologies:

```bash
# ~1 run in 3 aborts, the others repair successfully
java -cp $J www.ontologyutils.apps.RepairWeakening --basic-cache --strict-sroiq \
  --strict-simple-roles --simple-ria-weakening --strict-owl2 --bad-axiom random \
  -o /tmp/c2.owl limitations/case2-noweakening-top-assertion.owl
```

## Arm / algorithm correspondence

The arms of `SingleTrialExperiment` (and of the analysis) map onto the two
algorithms as follows:

| arm | bad-axiom rule | weakening rule | algorithm |
|---|---|---|---|
| A1 | — | random removal | removal baseline |
| A2 | — | not-in-largest-MCS removal | removal baseline |
| **A3** | in-some-MUS | random | **Algorithm 1** (`createWeakeningRepair`) |
| B1 | in-some-MUS | random | identical to A3, hence excluded (`B_REPAIRS = B2..B9`) |
| B2 / B3 | in-some-MUS | Shapley / Banzhaf | Algorithm 1 with a power-index weakener |
| B4 / B6 | Shapley | random / Banzhaf | Algorithm 2's bad-axiom rule + other weakeners |
| **B5** | Shapley | Shapley | **Algorithm 2** |
| B7 / B8 / B9 | Banzhaf | random / Shapley / Banzhaf | Algorithm 2 with Banzhaf |

A3 and B1 are built by the same call (`ONE_MCS` reference, `IN_SOME_MUS` bad axiom,
random weaker axiom, identical strict flags), which is why the analysis drops B1;
measured over the 9 273 successful trials they agree closely (mean 1 798 ms vs
1 675 ms, median 691 ms vs 645 ms — RNG noise, not a different algorithm).

Consequence for the failure analysis: the power-index scoring defect affects only
the arms that use a power-index selector. **A3 (= Algorithm 1) is unaffected**, so
its 5 `Could not find a weakening` failures are genuine empty-`Φ_weaker` cases —
exactly the situation in which the paper says the algorithm must terminate.

## Correspondence with the corpus statistics

| minimal case | corpus evidence (159-ontology corpus, 9 479 attempts) |
|---|---|
| case 1 | 36 of the 153 `MakeInconsistent` failures hit the 600 s cap; `MAT` (99 KB) showed 148 axioms added in 45 s, then 105 s without any addition and no end-of-loop marker |
| case 2 | 113 `Could not find a weakening for axiom`, in 12 ontologies; shapes: `⊤ ⊑ C` 40, `⊤(x)` 31, `∃ r.C` 20, `⊥ ⊑ C` 15, other 7; by stage: **B4 44**, B6 17, B5 15, B9 8, B7 6, B8 6, B3 5, A3 5, B1 4, B2 3 |

The staged distribution matches the reproduction: the arms whose bad-axiom
selector is a power index and whose weakener is random (B4) or Banzhaf (B6) are
the ones that reach unweakenable axioms.

## Suggested fixes

1. `OntologyRepairWeakening`: treat an empty weaker-axiom set as *skip this
   axiom* (e.g. fall back to removing it, or pick the next bad axiom) instead of
   aborting the whole repair/trial.
2. `MakeInconsistent`: stop when a full iteration adds no axiom (or after a fixed
   number of iterations without growth in the axiom count) and report
   "cannot be made inconsistent" instead of looping.
3. Both fixes would turn the 36 timeouts and (most of) the 113 exceptions into
   explicit, reportable outcomes, which currently bias the per-arm comparison
   towards the fast arms.
