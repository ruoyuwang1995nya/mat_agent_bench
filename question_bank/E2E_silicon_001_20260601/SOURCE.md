# Source Provenance

**Question ID:** `E2E_silicon_001_20260601`

**Project ID:** `64bf4148-b794-4988-813c-9240e55a1e14`

**Projection ID:** `5b2416e8-ec41-4bcc-b47b-e9ce9c8dfd07`

**Frame ID:** `1a29c872-3854-40bb-8060-c0ef0400e5bc`


## Paper / Project Notes

Paper: "Common workflows for computing material properties using different quantum engines" (npj Computational Materials, 2021). Extracted 3 benchmark tasks: 1 easy (VASP relaxation INCAR generation), 1 medium (EOS workflow setup for Si with volume-scaled structures), 1 hard (end-to-end EOS computation and Birch-Murnaghan fitting for Si). All tasks grounded in the common relax workflow interface and EOS workflow described in the paper. Added efficiency budget items (turn_budget, no_retries, duration_budget, token_budget_total) to all scoring_checklists. Difficulty distribution: ~33% easy, ~33% medium, ~33% hard (close to target 20/50/30 but only 3 items extracted from single paper).


## Source Evidence

Section 'Code-agnostic workflows': 'The EquationOfStateWorkflow takes a structure as input (S0 in Fig. 6) and scales the volume a number of times (Ni)... The workflow calls the common relax workflow for each scaled structure to compute its total energy.' Also: 'The total energies and optimized structure, as produced by the common relax workflow runs, are collected and returned by the EquationOfStateWorkflow as its outputs.' Figure 7 shows EOS results for Si with energy minimum at ~11.4 A3/atom.


---
_Extracted at: 2026-06-01T07:41:44.456757+00:00_
