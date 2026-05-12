"""Programmatic validators for plain-text workspace artifacts."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any


def _resolve_file(
    workspace: Path,
    filename: str,
    *,
    workspace_resolve: str = 'recursive',
) -> Path | None:
    if workspace_resolve == 'root':
        if len(Path(filename).parts) != 1:
            return None
        candidate = workspace / filename
        if candidate.is_file():
            return candidate
        return None

    exact = workspace / filename
    if exact.is_file():
        return exact
    hits = [
        p
        for p in workspace.rglob("*")
        if p.is_file() and fnmatch.fnmatch(p.name, filename)
    ]
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime)


def _normalize(text: str, *, case_sensitive: bool, normalize_whitespace: bool) -> str:
    if normalize_whitespace:
        text = re.sub(r'\s+', ' ', text).strip()
    if not case_sensitive:
        text = text.lower()
    return text


def check_text_file_contains_all(
    workspace_dir: str | Path,
    *,
    filename: str,
    tokens: list[str],
    case_sensitive: bool = False,
    normalize_whitespace: bool = True,
    workspace_resolve: str = 'recursive',
) -> tuple[bool, str]:
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename, workspace_resolve=workspace_resolve)
    if fpath is None:
        return False, f'no file matching {filename!r} in {root}'
    try:
        raw = fpath.read_text(encoding='utf-8')
    except Exception as exc:
        return False, f'failed reading {fpath.name}: {exc}'

    haystack = _normalize(
        raw, case_sensitive=case_sensitive, normalize_whitespace=normalize_whitespace
    )
    missing: list[str] = []
    for token in tokens:
        needle = _normalize(
            str(token),
            case_sensitive=case_sensitive,
            normalize_whitespace=normalize_whitespace,
        )
        if needle and needle not in haystack:
            missing.append(str(token))
    if missing:
        return False, f'{fpath.name}: missing tokens: {missing}'
    return True, f'{fpath.name}: all {len(tokens)} tokens found'


def check_text_file_regex(
    workspace_dir: str | Path,
    *,
    filename: str,
    pattern: str,
    flags: str = '',
    workspace_resolve: str = 'recursive',
) -> tuple[bool, str]:
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename, workspace_resolve=workspace_resolve)
    if fpath is None:
        return False, f'no file matching {filename!r} in {root}'
    try:
        raw = fpath.read_text(encoding='utf-8')
    except Exception as exc:
        return False, f'failed reading {fpath.name}: {exc}'

    flag_mask = 0
    flag_table = {'i': re.IGNORECASE, 'm': re.MULTILINE, 's': re.DOTALL}
    for ch in str(flags).lower():
        if ch in flag_table:
            flag_mask |= flag_table[ch]
    try:
        compiled = re.compile(pattern, flag_mask)
    except re.error as exc:
        return False, f'invalid regex pattern: {exc}'

    if compiled.search(raw) is None:
        return False, f'{fpath.name}: regex not matched: {pattern!r}'
    return True, f'{fpath.name}: regex matched'


def _parse_numeric_from_text(text: str) -> float | None:
    text = text.replace('\u2212', '-')
    m = re.search(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', text)
    if m is None:
        return None
    try:
        return float(m.group(0))
    except (TypeError, ValueError):
        return None


def _collect_key_values(raw: str, key: str) -> list[str]:
    key_lower = key.strip().lower()
    values: list[str] = []
    for line in raw.splitlines():
        line_no_comment = re.split(r'[#!]', line, maxsplit=1)[0].strip()
        if not line_no_comment:
            continue
        parts = line_no_comment.split()
        if len(parts) < 2:
            continue
        if parts[0].strip().lower() != key_lower:
            continue
        values.append(' '.join(parts[1:]).strip())
    return values


def _check_one_numeric_rule(raw: str, rule: dict[str, Any]) -> tuple[bool, str]:
    key = str(rule.get('key', '')).strip()
    if not key:
        return False, "numeric rule missing non-empty 'key'"

    values = _collect_key_values(raw, key)
    if not values:
        return False, f'key {key!r} not found'

    occurrence = str(rule.get('occurrence', 'last')).lower()
    if occurrence not in {'first', 'last'}:
        return False, f'invalid occurrence={occurrence!r}, expected first/last'
    raw_value = values[0] if occurrence == 'first' else values[-1]

    allowed_values = rule.get('allowed_values')
    if allowed_values is not None:
        if not isinstance(allowed_values, list) or not allowed_values:
            return False, f'key {key!r}: allowed_values must be a non-empty list'
        actual = raw_value.split()[0].strip().lower()
        allowed = [str(v).strip().lower() for v in allowed_values]
        hit = actual in allowed
        return hit, (
            f"key {key!r}: value={actual!r}, allowed={allowed}"
            if hit
            else f"key {key!r}: value={actual!r} not in allowed={allowed}"
        )

    value_tokens = re.findall(
        r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?',
        raw_value.replace('\u2212', '-'),
    )
    values_numeric: list[float] = []
    for token in value_tokens:
        try:
            values_numeric.append(float(token))
        except (TypeError, ValueError):
            continue
    if not values_numeric:
        value = _parse_numeric_from_text(raw_value)
        if value is None:
            return False, f'key {key!r}: no numeric value found in {raw_value!r}'
        values_numeric = [value]

    allowed_counts = rule.get('component_count_allowed')
    if allowed_counts is not None:
        if not isinstance(allowed_counts, list) or not allowed_counts:
            return (
                False,
                f'key {key!r}: component_count_allowed must be non-empty list[int]',
            )
        allowed_int = []
        for c in allowed_counts:
            try:
                allowed_int.append(int(c))
            except (TypeError, ValueError):
                return (
                    False,
                    f'key {key!r}: invalid component_count_allowed entry {c!r}',
                )
        if len(values_numeric) not in set(allowed_int):
            return (
                False,
                f'key {key!r}: component_count={len(values_numeric)} not in allowed={allowed_int}',
            )

    apply_counts = rule.get('if_component_count_in')
    if apply_counts is not None:
        if not isinstance(apply_counts, list) or not apply_counts:
            return (
                False,
                f'key {key!r}: if_component_count_in must be non-empty list[int]',
            )
        apply_int = []
        for c in apply_counts:
            try:
                apply_int.append(int(c))
            except (TypeError, ValueError):
                return (
                    False,
                    f'key {key!r}: invalid if_component_count_in entry {c!r}',
                )
        if len(values_numeric) not in set(apply_int):
            return (
                True,
                f'key {key!r}: skipped for component_count={len(values_numeric)}',
            )

    component = rule.get('component')
    if component is None:
        values_for_check = values_numeric
    else:
        try:
            comp_idx = int(component) - 1
        except (TypeError, ValueError):
            return False, f'key {key!r}: component must be int (1-based)'
        if comp_idx < 0 or comp_idx >= len(values_numeric):
            return (
                False,
                f'key {key!r}: component={component} out of range for {len(values_numeric)} values',
            )
        values_for_check = [values_numeric[comp_idx]]

    all_components = bool(rule.get('all_components', False))
    if all_components and component is not None:
        return False, f"key {key!r}: do not set both 'all_components' and 'component'"
    if all_components:
        values_for_check = values_numeric

    has_min = 'min' in rule
    has_max = 'max' in rule
    has_expected = 'expected' in rule
    has_tolerance = 'tolerance' in rule
    if has_expected and has_tolerance:
        target = float(rule['expected'])
        tol = float(rule['tolerance'])
        hit = all(abs(v - target) <= tol for v in values_for_check)
        scope = 'all_components' if all_components else 'value'
        return (
            hit,
            f"key {key!r}: {scope}={values_for_check}, expected={target}±{tol}",
        )
    if has_min or has_max:
        lo = float(rule['min']) if has_min else float('-inf')
        hi = float(rule['max']) if has_max else float('inf')
        hit = all(lo <= v <= hi for v in values_for_check)
        scope = 'all_components' if all_components else 'value'
        return (
            hit,
            f"key {key!r}: {scope}={values_for_check}, range=[{lo}, {hi}]",
        )
    return (
        False,
        f"key {key!r}: provide either (expected+tolerance) or (min/max)",
    )


def check_text_file_numeric_range(
    workspace_dir: str | Path,
    *,
    filename: str,
    checks: list[dict[str, Any]],
    workspace_resolve: str = 'recursive',
) -> tuple[bool, str]:
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename, workspace_resolve=workspace_resolve)
    if fpath is None:
        return False, f'no file matching {filename!r} in {root}'
    try:
        raw = fpath.read_text(encoding='utf-8')
    except Exception as exc:
        return False, f'failed reading {fpath.name}: {exc}'

    if not isinstance(checks, list) or not checks:
        return False, "reference answer must provide non-empty 'checks' list"

    details: list[str] = []
    for idx, rule in enumerate(checks, start=1):
        if not isinstance(rule, dict):
            return False, f'checks[{idx}] must be a dict'
        ok, reason = _check_one_numeric_rule(raw, rule)
        details.append(reason)
        if not ok:
            return False, f'{fpath.name}: {reason}'
    return True, f"{fpath.name}: all {len(checks)} numeric checks passed; " + '; '.join(
        details
    )


def check_text_file_kpt_path(
    workspace_dir: str | Path,
    *,
    filename: str,
    required_points: list[list[float]],
    tolerance: float = 1.0e-6,
    require_line_mode: bool = True,
    require_order: bool = True,
    workspace_resolve: str = 'recursive',
) -> tuple[bool, str]:
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename, workspace_resolve=workspace_resolve)
    if fpath is None:
        return False, f'no file matching {filename!r} in {root}'
    try:
        raw = fpath.read_text(encoding='utf-8')
    except Exception as exc:
        return False, f'failed reading {fpath.name}: {exc}'

    if not isinstance(required_points, list) or not required_points:
        return False, "reference answer must provide non-empty 'required_points' list"
    points: list[tuple[float, float, float]] = []
    for idx, item in enumerate(required_points, start=1):
        if not isinstance(item, list) or len(item) != 3:
            return False, f'required_points[{idx}] must be [x, y, z]'
        try:
            points.append((float(item[0]), float(item[1]), float(item[2])))
        except (TypeError, ValueError):
            return False, f'required_points[{idx}] must be numeric'

    try:
        tol = float(tolerance)
    except (TypeError, ValueError):
        return False, f'invalid tolerance={tolerance!r}'
    if tol < 0:
        return False, 'tolerance must be >= 0'

    lines = raw.splitlines()
    if require_line_mode:
        line_mode_hit = any(
            re.search(r'\b(line|line_mode|line-mode)\b', line, flags=re.IGNORECASE)
            for line in lines
        )
        if not line_mode_hit:
            return False, f'{fpath.name}: no line-mode marker found'

    parsed: list[tuple[float, float, float]] = []
    for line in lines:
        line_no_comment = re.split(r'[#!]', line, maxsplit=1)[0].strip()
        if not line_no_comment:
            continue
        nums: list[float] = []
        for token in re.findall(
            r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?',
            line_no_comment.replace('\u2212', '-'),
        ):
            try:
                nums.append(float(token))
            except (TypeError, ValueError):
                continue
        if len(nums) >= 3:
            parsed.append((nums[0], nums[1], nums[2]))
        if len(nums) >= 4:
            parsed.append((nums[1], nums[2], nums[3]))
    if not parsed:
        return False, f'{fpath.name}: no coordinate points parsed'

    def _close(a: tuple[float, float, float], b: tuple[float, float, float]) -> bool:
        return (
            abs(a[0] - b[0]) <= tol
            and abs(a[1] - b[1]) <= tol
            and abs(a[2] - b[2]) <= tol
        )

    indices: list[int] = []
    for req in points:
        found = -1
        for i, got in enumerate(parsed):
            if _close(got, req):
                found = i
                break
        if found < 0:
            return (
                False,
                f'{fpath.name}: required point {req} not found within tol={tol}',
            )
        indices.append(found)

    if require_order:
        for i in range(1, len(indices)):
            if indices[i] < indices[i - 1]:
                return (
                    False,
                    f'{fpath.name}: required points found but order violated (indices={indices})',
                )

    return (
        True,
        f'{fpath.name}: matched required points {points} with tol={tol}, indices={indices}',
    )
