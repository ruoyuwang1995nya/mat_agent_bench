"""AtomWorld-backed validators for active structure-editing tasks."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

_NP_AVAILABLE = False
try:
    import numpy as np

    _NP_AVAILABLE = True
except ImportError:
    np = None  # type: ignore[assignment]

_IMPORT_MSG = 'pymatgen not installed; install with: pip install mat-bench[validators]'

try:
    from pymatgen.analysis.structure_matcher import StructureMatcher
    from pymatgen.io.cif import CifParser
except ImportError:
    StructureMatcher = None
    CifParser = None


@dataclass
class EvaluateResult:
    correct: bool
    wrong_type: str | None = None
    rmsd: float | None = None
    max_dist: float | None = None


def _resolve_file(workspace: Path, pattern: str) -> Path | None:
    exact = workspace / pattern
    if exact.is_file():
        return exact
    hits = [
        path
        for path in workspace.rglob('*')
        if path.is_file() and fnmatch.fnmatch(path.name, pattern)
    ]
    if not hits:
        return None
    return max(hits, key=lambda path: path.stat().st_mtime)


def _parse_cif(cif_string: str):
    if CifParser is None:
        raise ImportError(_IMPORT_MSG)
    try:
        parser = CifParser.from_str(cif_string)
        structures = parser.parse_structures(primitive=False, check_occu=False)
        return structures[0] if structures else None
    except Exception as exc:
        raise ValueError(f'CIF parsing failed: {exc}') from exc


def _check_atom_counts(struct1, struct2) -> bool:
    if len(struct1) != len(struct2):
        return False
    return struct1.composition.as_dict() == struct2.composition.as_dict()


def _match_structures(struct1, struct2, primitive_cell: bool = False, stol: float = 0.5):
    if StructureMatcher is None:
        raise ImportError(_IMPORT_MSG)
    matcher = StructureMatcher(primitive_cell=primitive_cell, stol=stol)
    if matcher.fit(struct1, struct2):
        return matcher.get_rms_dist(struct1, struct2)
    return None, None


def _exact_match_metrics(struct1, struct2, stol: float = 0.5):
    if not _NP_AVAILABLE:
        raise ImportError('numpy not installed; install with: pip install mat-bench[validators]')
    if len(struct1) != len(struct2):
        return None, None

    for site1, site2 in zip(struct1.sites, struct2.sites):
        if site1.species_string != site2.species_string:
            return -1, -1

    if not np.allclose(struct1.lattice.matrix, struct2.lattice.matrix):
        return -1, -1

    frac1 = np.array([site.frac_coords for site in struct1.sites])
    frac2 = np.array([site.frac_coords for site in struct2.sites])
    diff = frac1 - frac2
    diff -= np.round(diff)

    cart_diff = diff @ struct1.lattice.matrix
    sq = np.sum(cart_diff**2, axis=1)
    norm = (len(struct1) / struct1.lattice.volume) ** (1 / 3)
    rmsd = float(np.sqrt(np.mean(sq)) * norm)
    max_dist = float(np.sqrt(np.max(sq)) * norm)

    if max_dist > stol:
        return -1, -1

    return rmsd, max_dist


def evaluate_atomworld_compatible(
    target_cif: str,
    generated_cif: str,
    *,
    action_name: str | None = None,
) -> EvaluateResult:
    target_struct = _parse_cif(target_cif)
    if target_struct is None:
        raise ValueError('Could not parse the target CIF string: no structures found.')

    try:
        gen_struct = _parse_cif(generated_cif)
    except Exception as exc:
        return EvaluateResult(correct=False, wrong_type=f'CIFParsingError: {exc}')
    if gen_struct is None:
        return EvaluateResult(correct=False, wrong_type='CIFParsingError: no structures found')

    if not _check_atom_counts(target_struct, gen_struct):
        return EvaluateResult(correct=False, wrong_type='AtomCountMismatch')

    use_exact = (action_name or '').lower() == 'move_all_action'
    if use_exact:
        rmsd, max_dist = _exact_match_metrics(target_struct, gen_struct)
    else:
        rmsd, max_dist = _match_structures(target_struct, gen_struct, primitive_cell=False)

    if rmsd is None or rmsd == -1:
        return EvaluateResult(correct=False, wrong_type='StructureMismatch')

    return EvaluateResult(correct=True, rmsd=rmsd, max_dist=max_dist)


def check_atomworld_active_task(
    workspace_dir: str | Path,
    *,
    filename: str,
    target_cif: str,
    action_name: str | None = None,
) -> tuple[bool, str]:
    if CifParser is None or StructureMatcher is None:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f'no file matching {filename!r} in {root}'

    try:
        generated_cif = fpath.read_text(encoding='utf-8')
    except Exception as exc:
        return False, f'could not read {fpath.name}: {exc}'

    try:
        result = evaluate_atomworld_compatible(
            target_cif=target_cif,
            generated_cif=generated_cif,
            action_name=action_name,
        )
    except Exception as exc:
        return False, f'atomworld evaluation failed: {exc}'

    if not result.correct:
        return False, (
            f'{fpath.name}: atomworld mismatch '
            f'(wrong_type={result.wrong_type or "unknown"})'
        )

    rmsd = 'n/a' if result.rmsd is None else f'{result.rmsd:.6f}'
    max_dist = 'n/a' if result.max_dist is None else f'{result.max_dist:.6f}'
    return True, f'{fpath.name}: atomworld match rmsd={rmsd}, max_dist={max_dist}'