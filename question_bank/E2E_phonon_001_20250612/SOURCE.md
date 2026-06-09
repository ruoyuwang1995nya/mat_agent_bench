# Source Provenance

**Question ID:** `E2E_phonon_001_20250612`

**Project ID:** `2814072e-c1c3-49c8-a46a-aef40f0ce70d`

**Projection ID:** `c42aaff0-4772-4d3d-bc7a-7552bb074e12`

**Frame ID:** `93923061-ce7c-45ca-bacf-69db744ca78a`


## Paper / Project Notes

Seeding projection for paper "Universal machine learning interatomic potentials are ready for phonons" (10.1038/s41524-025-01650-1). The paper benchmarks 7 uMLIPs on ~10,000 semiconductor phonon calculations. Relevant workflows: geometry relaxation with ASE/FIRE, PHONOPY phonon calculations, thermodynamic property extraction, dynamical stability assessment. Planning 5 tasks: 1 easy (simulation script), 3 medium (thermo computation, phonon setup, benchmark interpretation), 1 hard (end-to-end phonon benchmarking).
Added 5 questions extracted from the paper: 1 easy (SIM_phonon_001 - ASE relaxation script), 3 medium (MCH_phonon_001 - thermo from DOS, SIM_phonon_002 - PHONOPY setup, SRI_phonon_001 - benchmark interpretation), 1 hard (E2E_phonon_001 - complete phonon benchmarking workflow). Distribution: 20% easy, 60% medium, 20% hard. All tasks are self-contained with proper data_files, reference_answers, and scoring_checklists. The hard task spans 4+ phases (relaxation → phonon → stability → thermodynamics) and exercises 5 capabilities.


## Source Evidence

Results + Methods: Full workflow described across the paper — geometry relaxation with ASE/FIRE (force convergence 0.005 eV/Å), phonon force constants via PHONOPY finite displacement, Fourier interpolation to 20×20×20 q-grid, thermodynamic properties at 300 K, dynamical stability via imaginary mode analysis with -50 K threshold.


---
_Extracted at: 2026-06-01T07:28:01.359510+00:00_
