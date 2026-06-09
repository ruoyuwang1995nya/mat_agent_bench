# Source Provenance

**Question ID:** `E2E_neb_001_20250110`

**Project ID:** `533ddf85-45b2-407f-ab69-561256e65cb2`

**Projection ID:** `49665a84-74e7-4bf7-bf73-9f2a4b71186f`

**Frame ID:** `b832b98c-b6c3-4f3e-b811-ec5bc2c14f5c`


## Paper / Project Notes

Paper: "Systematic softening in universal machine learning interatomic potentials" by Deng et al. (2025). Domain: computational materials science / ML interatomic potentials. The paper benchmarks M3GNet, CHGNet, and MACE-MP-0 across surface energies, defect energies, solid-solution energetics, ion migration barriers, phonon properties, and high-energy states. Key finding: systematic PES softening (force underprediction) across all uMLIPs, correctable via linear fine-tuning with minimal data. I will extract 4 tasks: 1 easy (VASP INCAR generation), 2 medium (surface energy calculation, PES softening scale computation), and 1 hard (end-to-end ApproxNEB ion migration barrier workflow).


## Source Evidence

Results section 'Ion migration barriers': 'We employ uMLIPs and DFT to conduct a comprehensive assessment of 470 Mg-ion migration pathways in 110 distinct structures including oxides, halides, and sulfides.' 'Figure 3a presents the energy landscape of one Mg ion migration path in V2O3(SO4)2 (mp-28207)...The kinetically resolved activation (KRA) migration barrier is defined as the highest energy along the reaction coordinate after the reference.' Methods section: 'ApproxNEB is different from regular NEB in that it does not perform a relaxation of the pathway but solely evaluates the energy along the predefined trajectory.'


---
_Extracted at: 2026-06-01T11:50:06.256831+00:00_
