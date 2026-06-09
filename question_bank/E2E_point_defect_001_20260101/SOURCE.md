# Source Provenance

**Question ID:** `E2E_point_defect_001_20260101`

**Project ID:** `0b8b29bb-2e8b-46c9-ae2b-fb9d824aaa97`

**Projection ID:** `90a54d26-4fe1-4813-bbc2-375233d6b733`

**Frame ID:** `ecbf44d1-1c92-4661-9bdd-13c9c2ff4141`


## Paper / Project Notes

Paper: Broberg et al., npj Comput. Mater. 2023 — benchmarks high-throughput GGA-PBE point defect calculations with a-posteriori corrections against 245 hybrid-functional reference calculations. Key reproducible workflows: (1) VASP INCAR generation for defect calculations, (2) defect formation energy computation with corrections, (3) thermodynamic transition level calculation, (4) Fermi level from charge neutrality, (5) full end-to-end defect analysis. Will extract ~4-5 questions across easy/medium/hard.
Extraction complete. 5 questions extracted from Broberg et al. (2023) npj Comput. Mater. 9:72 on high-throughput point defect DFT calculations:

1. SIM_vasp_001_20260101 (easy): VASP INCAR generation for GGA-PBE defect calculations — tests tool_utilization, data_handling.
2. SIM_point_defect_001_20260101 (medium): Defect formation energy computation with three correction schemes (no_bes, bes, bes_free) — tests scientific_reasoning, tool_utilization, data_handling.
3. MCH_point_defect_001_20260101 (medium): Thermodynamic transition level calculation and deep/shallow classification — tests scientific_reasoning, data_handling, tool_utilization.
4. SIM_point_defect_002_20260101 (medium): Fermi level determination from charge neutrality equation — tests scientific_reasoning, data_handling, tool_utilization.
5. E2E_point_defect_001_20260101 (hard): End-to-end point defect analysis workflow (structure → defect creation → formation energies → transition levels → Fermi level → dopability) — tests all 6 capabilities including workflow_orchestration and structure_manipulation.

Difficulty distribution: 1 easy (20%), 3 medium (60%), 1 hard (20%). All questions are self-contained with explicit data_files, reference_answers, and scoring_checklists grounded in the paper's equations (Eqs. 1-4), correction schemes (Table 1), and benchmark results (Figs. 4-10, Table 2).


## Source Evidence

Full paper workflow: (1) supercell construction from bulk, (2) defect creation and relaxation, (3) formation energy computation with corrections (Table 1: no_bes, bes, bes_free), (4) transition level analysis (Eq. 2, Fig. 5), (5) Fermi level from charge neutrality (Eqs. 3-4, Fig. 8), (6) dopability limits (Fig. 9-10). Table 2 summarizes bes_free correction performance.


---
_Extracted at: 2026-06-01T07:39:07.202293+00:00_
