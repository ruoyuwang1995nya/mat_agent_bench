# Source Provenance

**Question ID:** `SIM_neb_002_20260601`

**Project ID:** `7926449d-91ed-4ceb-b694-5edfde9afb4a`

**Projection ID:** `c0d10d05-3ef4-48b0-a6c9-548e54b67a73`

**Frame ID:** `7ae2095a-8272-482c-b9f2-fc7372d1ddf9`


## Paper / Project Notes

Paper: "Metallic W/WO₂ solid-acid catalyst boosts hydrogen evolution reaction in alkaline electrolyte" (Nature Communications, 2023). Contains DFT calculations (VASP), electrochemical characterization, and mechanism studies. Planning 4 benchmark tasks: 1 easy (data interpretation), 2 medium (DFT setup), 1 hard (end-to-end research workflow).
Added 4 benchmark tasks: (1) SRI_her_001: Easy task parsing electrochemical data from paper table, (2) SIM_vasp_001: Medium VASP DFT setup for H adsorption on W/WO₂ interface with specific parameters from paper, (3) SIM_neb_002: Medium VASP NEB setup for water dissociation barrier, (4) E2E_her_001: Hard end-to-end research task spanning structure building → DFT → property extraction → mechanism analysis. All tasks reference paper data and DFT methodology sections.
Fixed capability mismatches: Question 2 (SIM_vasp_001) now includes structure_manipulation since the checklist tests interface structure creation. Question 3 (SIM_neb_002) now includes structure_manipulation and workflow_orchestration since the checklist tests POSCAR construction and multi-image directory setup.


## Source Evidence

DFT calculations section: 'For evaluating the H₂O dissociation energy barrier, the transitional state was located using the Nudged Elastic Band method.' Energy barriers reported: W (0.84 eV), WO₂ (0.06 eV), W/WO₂ (0.02 eV). Standard DFT parameters: 'energy cut-off of 450 eV... DFT-D3 method... grid separation of 0.04 Å⁻¹... total energy convergence and interaction force were set to be 10⁻⁶ eV and 10⁻² eV/Å, respectively.'


---
_Extracted at: 2026-06-01T07:42:45.733859+00:00_
