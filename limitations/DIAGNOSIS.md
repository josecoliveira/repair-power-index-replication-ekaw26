# Full diagnosis of the repair-by-power-indices failures

Scope: the repair by power indices only (Algorithm 1 and Algorithm 2 of the
EKAW paper). The `MakeInconsistent` fixed-point issue is a separate matter and is
not covered here.

Arm correspondence (verified in `SingleTrialExperiment.buildRepairPlans` /
`createWeakeningRepair` / `createBRepair`):

| arm | bad-axiom rule | weakening rule | algorithm |
|---|---|---|---|
| A1 / A2 | — | random / not-in-largest-MCS removal | removal baselines |
| **A3** | in-some-MUS | random | **Algorithm 1** |
| B1 | in-some-MUS | random | identical to A3 (excluded from the analysis) |
| B2 / B3 | in-some-MUS | Shapley / Banzhaf | Alg. 1 with a power-index weakener |
| B4 / B6 | Shapley | random / Banzhaf | Alg. 2's bad-axiom rule + other weakeners |
| **B5** | Shapley | Shapley | **Algorithm 2** |
| B7 / B8 / B9 | Banzhaf | random / Shapley / Banzhaf | Alg. 2 with Banzhaf |

## 1. What the paper specifies

* Def. 7: `Id(S) = 0` if `S` is consistent, `1` otherwise.
* Def. 8: `S_phi(O) = 1/|O|! * sum over permutations pi of
  [Id(P_pi(phi) U {phi}) - Id(P_pi(phi))]`, `P_pi(phi)` = axioms preceding `phi`.
* Alg. 1 (= A3): `O_ref <- ONE_MCS(O)`; while `O` inconsistent:
  `phi_bad <- FindBadAxiom(O)`; `Phi <- g_{O_ref,O_full}(phi_bad)`;
  `phi_w <- SelectWeakerAxiom(Phi)`; `O <- O \ {phi_bad} U {phi_w}`.
* Alg. 2 (= B5): `FindBadAxiom(O) = argmax_{phi in O} S_phi(O)` and
  `SelectWeakerAxiom(Phi,O,phi_bad) = argmin_{phi in Phi\{phi_bad}} S_phi(O \ {phi_bad} U {phi})`.
* Termination clause: "if the set of weakenings `Phi` is empty, the repair process
  cannot continue and the algorithm terminates".
* Lemma 2: consistent `O` => every value 0. Lemma 3: a tautology has value 0.
* Implementation note in the paper: Monte Carlo approximation, samples in chunks
  of 50, stop when the top-5 / bottom-5 rankings stabilise.

## 2. What the code actually computes

### Glossary (implementation terms)

* **refutable axioms** (`Ontology.refutableAxioms`) = exactly
  `ontology.logicalAxioms()`, i.e. every axiom for which OWLAPI's
  `isLogicalAxiom()` is true: TBox, RBox and ABox axioms, datatype axioms, keys,
  `SameIndividual`/`DifferentIndividuals`, and SWRL rules (`SWRLRule extends
  OWLLogicalAxiom`).  They are the axioms the repair is allowed to replace: the
  candidate set of `FindBadAxiom`, the search space of the MUS computation
  (`MinimalSubsets.allMinimalSubsets(refutableAxioms, ...)`) and the target of
  `replaceAxiom`.  Axioms added at runtime (`addAxioms`) also become refutable.
  **Tautologies such as `owl:Thing(a)`, `C ⊑ owl:Thing` or `owl:Nothing ⊑ C` are
  logical axioms, hence refutable, hence selectable as a bad axiom.**
* **static / non-refutable axioms** (`Ontology.staticAxioms`) = everything with
  `isLogicalAxiom() == false`: declarations, annotation assertions (all the
  `rdfs:label`s of the corpus), sub-annotation-property and annotation
  property domain/range axioms, import declarations, ontology annotations.  They
  are "not to be changed": never candidates, never weakened, invisible to the MUS
  search.  The constructor additionally enforces
  `refutableAxioms.removeAll(staticAxioms)`.
* **targets** `T` = the axioms whose Shapley value is being computed in one call
  of `computeScoresAdaptive(U, T, ...)`.
* **non-target axioms** `U \ T` = axioms that are part of the universe but are not
  scored.  In `sampleTargetsInParallel` they are the only axioms that get
  accumulated into `prefixBase`; a target is scored against that base and is
  itself never added to it.  Hence `T = U` ⇒ the base is always empty.

The loop matches Alg. 1/2 (`OntologyRepairWeakening.repair`), but the two
selectors call `computeScoresAdaptive`, whose marginal is

```
S_impl_tau(U,T) = E_pi[ Id(B_pi(tau) U {tau}) - Id(B_pi(tau)) ],
B_pi(tau) = { alpha in U \ T : alpha precedes tau in pi }        // only NON-targets
```

i.e. the base contains only the **non-target** axioms, whereas Def. 8 uses *all*
preceding axioms. The routine that does implement Def. 8 correctly exists
(`computeScore` -> `sampleSingleTargetInParallel`, which accumulates every
preceding axiom into `prefix`) but the selectors do not use it.

Selectors:

* bad: candidates `R = refutableAxioms()`, `computeScoresAdaptive(R, R, 50, 1000, false)`,
  then argmax by `(score, prettyPrintAxiomDL)`.
* weaker: `W = weakerAxioms(phi_bad) \ {phi_bad}`; if empty return empty;
  `computeScoresAdaptive(R, W, 50, 1000, true)`, then argmin by
  `(score, prettyPrintAxiomDL)`.

## 3. Defect A - the bad-axiom score is identically zero (arms B4-B9)

`T = U` makes `B_pi(tau)` empty for every permutation, so
`S_impl_tau = Id({tau}) - Id(empty) = 0` for every axiom that is not inconsistent
on its own. Measured:

* `ADCAD.owl` (267 refutable axioms): **0 / 267** non-zero scores.
  With `T` a strict subset of `U` the same call gives the correct `0.54` for the
  guilty axiom, and per-axiom `computeScore` matches Def. 8.
* Paper's Example 3 (rebuilt, 5 refutable axioms):

  | axiom | correct value (`computeScore`) | value used by B5 |
  |---|---|---|
  | `DisjointClasses(RaiseWages, RaiseWelfare)` | 0.305 | **0.0** |
  | `RaiseWages(Brazil)` | 0.337 | **0.0** |
  | `RaiseWelfare(Brazil)` | 0.333 | **0.0** |
  | `RaiseWelfare sqsubseteq LeftPolicy` | 0.0 | 0.0 |
  | `RaiseWages sqsubseteq LeftPolicy` | 0.0 | 0.0 |

Consequence: `argmax` over a complete tie, resolved by
`Utils.prettyPrintAxiomDL`. Since `⊤` (U+22A4), `⊥`... `⊤`, `∃` (U+2203) and `⊥`
(U+22A5) sort after ASCII letters, the tie-break preferentially returns
tautologies and `⊤`/`⊥`-shaped axioms.

Trace on the paper's Example 3 (`SingleTrialExperiment --seed 0`, arm B4 - same
bad selector as B5):

```
Selected bad axiom: RaiseWelfare(Brazil)
Selected bad axiom: RaiseWelfare sqsubseteq not RaiseWages
Selected bad axiom: RaiseWelfare sqsubseteq top        <- tautology
Selected bad axiom: bottom sqsubseteq top              <- tautology
... IllegalStateException: Could not find a weakening for axiom: bottom sqsubseteq top
```

Corpus: **96 of the 113** weakening failures are in exactly the arms whose bad
selector is a power index (B4 44, B6 17, B5 15, B9 8, B7 6, B8 6), and the shapes
mirror the Unicode ordering (`⊤ ⊑ C` 40, `⊤(x)` 31, `∃ r.C` 20, `⊥ ⊑ C` 15).

## 4. Defect B - the weaker-axiom score is degenerate (arms B2,B3,B5,B6,B8,B9)

The paper scores each candidate in the *candidate ontology*
`O \ {phi_bad} U {phi}`, so the universe changes per candidate. The code instead
passes the fixed universe `R` = the (still inconsistent) ontology and treats the
candidates as targets. The sampled base is then a subset of an inconsistent
ontology, so `Id(B) = 1` and `Id(B U {phi}) = 1` for almost every sample.
Measured: `0.0` for all candidates.

Consequence: `argmin` over ties -> lexicographic minimum -> an arbitrary
weakening. This does not itself raise the exception (the emptiness check happens
before scoring), but it means the *choice* is not the paper's rule, so the
quality/runtime numbers of those arms do not measure Alg. 2.

## 5. Defect C - spec deviation when `Phi` is empty

The paper states the algorithm **terminates**; the implementation throws
`IllegalStateException`, aborting the trial. Genuine instances of the empty case
(the bad axiom comes from a MUS, so the selector is not at fault):
A3 5, B1 4, B3 5, B2 3 = **17 of the 113**.

## 6. Defect D - unspecified tie-breaking

The paper removes non-determinism by strictly ranking but says nothing about
ties. The implementation resolves them by pretty-printed string, which biases the
choice towards `⊤`/`⊥`/`∃` axioms whenever scores tie - and with Defect A they
always tie.

## 7. Split of the 113 failures

| cause | arms | count |
|---|---|---|
| Defect A (zero bad-axiom scores -> tautology selected -> empty `Phi`) | B4, B5, B6, B7, B8, B9 | **96** |
| Genuine empty `Phi` with in-MUS selection (Defect C turns it into an abort) | A3, B1, B2, B3 | **17** |

## 8. Status of the reported numbers

* Invalid (not measuring the paper's rule): every B-arm quantity - IIC, runtime,
  outcome. In particular A3-vs-B5 (Alg. 1 vs Alg. 2), Shapley-vs-Banzhaf and
  "power index vs random weakening" are **not** established by the current run.
* Still valid: A1/A2/A3 (no power index involved), the corpus build statistics,
  the two minimal reproducers in this folder, and the 17 genuine empty-`Phi` cases.

## 9. The published results are at risk

The paper's own implementation note describes exactly this Monte Carlo + 50-sample
adaptive routine, and `OntologyRepairBuilder.withPowerIndex` (used by the paper's
`RepairWithPowerIndexes` app) wires exactly these two selectors. The defect was
introduced on 2026-05-13 (`computeScoresAdaptive` / `sampleTargetsInParallel`),
before the repair-strategy refactor of 2026-05-28. On the paper's Example 3 the
implementation selects `RaiseWelfare(Brazil)`, not the reported `RaiseWages(Brazil)`
with value 0.83, and 0.83 is not obtainable from the routine at all (it returns 0
for every axiom). Action item: confirm which build produced the published numbers;
if it contained this routine, the published Shapley-guided results need to be
re-examined.

## 10. Fixes

1. Bad axiom: score against the full universe (per-axiom `computeScore`, or a
   batched sampler whose base is the prefix over the whole universe).
2. Weaker axiom: use the paper's per-candidate universe `O\{phi_bad}U{phi}`.
   A single batched call with one shared, inconsistent base cannot implement it.
3. Empty `Phi`: terminate and report the status instead of throwing.
4. Make ties explicit (random or documented) so zero-ties cannot systematically
   favour `⊤`/`⊥`/`∃`.
5. Re-run every B arm after 1-4; only then are the ablations comparable with A3.
