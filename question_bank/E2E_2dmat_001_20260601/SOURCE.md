# Source Provenance

**Question ID:** `E2E_2dmat_001_20260601`

**Project ID:** `160327b8-d37d-4c11-b690-b999143e2250`

**Projection ID:** `27917fa8-4da2-4cf4-93fa-10ee97813341`

**Frame ID:** `16a6bf7f-8b77-4b01-aa92-f1633eae12a1`


## Paper / Project Notes

Paper: "Strain-modulated defect engineering of two-dimensional materials" (Nature npj Comput Mater 2024). DFT study of h-BN, graphene, MoSe2, phosphorene with substitutional impurities under biaxial strain. Contains concrete VASP parameters, supercell sizes, k-point meshes, defect formation energy formula, and strain-dependent results. Planning 4 tasks: 1 easy (VASP INCAR setup), 2 medium (defect formation energy computation, k-point mesh generation), 1 hard (end-to-end strain-dependent defect energetics workflow).
Extraction complete. 4 benchmark tasks projected from "Strain-modulated defect engineering of two-dimensional materials" (npj Comput Mater 2024).

Task summary:
1. SIM_2dmat_001_20260601 (easy): VASP INCAR generation for 2D defect calculations — tests tool_utilization + scientific_reasoning
2. SIM_2dmat_002_20260601 (medium): Defect formation energy computation using Eq. 2 from paper — tests scientific_reasoning + data_handling + tool_utilization
3. SIM_vasp_003_20260601 (medium): KPOINTS mesh generation for 4 material supercells — tests tool_utilization + scientific_reasoning + data_handling
4. E2E_2dmat_001_20260601 (hard): End-to-end strain-dependent defect energetics for C_B/C_N in h-BN — tests all 6 capabilities including workflow_orchestration

Key paper parameters extracted: VASP PBE, 450 eV cutoff, 0.01 eV/Å force tolerance (h-BN), 4×4×1 k-points for supercells, 16 Å vacuum for h-BN, N-rich limit chemical potentials, defect formation energy formula (Eq. 2).

All tasks are self-contained with explicit data_files. Difficulty distribution: 25% easy, 50% medium, 25% hard (close to target 20/50/30). All capability lists match scoring_checklist capability tags. Each task includes grounding-axis llm_binary_judge item.


## Source Evidence

Results section: 'For C_B, ΔE_f increases with ε independent of the choice of the chemical potential for B... and for C_N the trend is the opposite.' Figure 1 shows ΔE_f vs strain for C_B (increases linearly ~0.2 eV/% strain) and C_N (decreases linearly ~0.2 eV/% strain). Methods section describes the DFT setup (VASP, PBE, 450 eV cutoff, 16 Å vacuum for h-BN, force tolerance 0.01 eV/Å, 4×4×1 k-points for 160-atom supercells).


---
_Extracted at: 2026-06-01T07:21:34.253996+00:00_
