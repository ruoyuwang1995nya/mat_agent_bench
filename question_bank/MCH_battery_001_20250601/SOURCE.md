# Source Provenance

**Question ID:** `MCH_battery_001_20250601`

**Project ID:** `68a34fd5-435b-4491-a4bf-3deaa106cdc5`

**Projection ID:** `e724410f-41eb-47eb-911b-ea9259b24b69`

**Frame ID:** `bbfa1da4-187e-4f1c-accd-ef77e6d0902e`


## Paper / Project Notes

Paper: "A Foundational Potential Energy Surface Dataset for Materials" (Kaplan et al., 2025). Describes the MatPES dataset (~400K structures) for training UMLIPs, VASP INCAR/POTCAR settings, UMLIP training hyperparameters, MD benchmarks for ionic conductivity, elastic moduli computation, and the 2DIRECT sampling workflow. Will extract 5 questions: 1 easy (VASP INCAR generation), 1 easy (cohesive energy calculation), 2 medium (UMLIP training config, ionic conductivity from MSD), 1 hard (end-to-end 2DIRECT sampling workflow).
Extraction complete. 5 questions extracted from "A Foundational Potential Energy Surface Dataset for Materials" (Kaplan et al., 2025):

1. SIM_vasp_001_20250601 (easy): Generate VASP INCAR file from Table S6 MatPES static calculation parameters. Tests tool_utilization + data_handling.
2. MCH_battery_001_20250601 (medium): Compute ionic conductivity from MSD data using Nernst-Einstein equation (Eq. 5-6). Tests data_handling + scientific_reasoning + tool_utilization.
3. SIM_uMLIP_001_20250601 (medium): Configure TensorNet UMLIP training with MatPES hyperparameters from Table 2. Tests tool_utilization + scientific_reasoning + data_handling.
4. E2E_sampling_001_20250601 (hard): End-to-end 2DIRECT sampling workflow (supercell → MD → encoding → PCA/clustering → selection). Tests all 5 capabilities including workflow_orchestration and structure_manipulation.
5. MCH_energy_001_20250601 (easy): Compute cohesive energy per atom using Eq. 1 formula. Tests data_handling + scientific_reasoning.

Difficulty distribution: 2 easy (40%), 2 medium (40%), 1 hard (20%). Slightly more easy than the ideal 20/50/30 split, but the paper's concrete reproducible workflows lean toward configuration/setup tasks.

All questions are self-contained with their own data_files where needed. Each has grounding-axis llm_binary_judge items. The hard question (E2E) includes workflow_orchestration and structure_manipulation rubric items as required.


## Source Evidence

Eq. 5: σ = (z²F²ρ/RT)D where D is diffusivity, ρ is number density of diffusing ions, T is absolute temperature, z=1 is ionic charge, F is Faraday's constant, R is universal gas constant. Eq. 6: D = lim_{Δt→∞} (1/(2dΔt))⟨|r_i(t+Δt) - r_i(t)|²⟩ where d=3 is dimensionality. The ionic conductivity is derived from a linear fit of MSD versus time.


---
_Extracted at: 2026-06-01T12:00:35.549186+00:00_
