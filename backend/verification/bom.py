"""
Bill of materials for a generated assembly.

An engineer looking at an assembly asks "what's in it and how many?" before
anything else. The pipeline already computed exactly that data and threw it
away: ``group_parts_by_description`` establishes which parts are identical
(that is how duplicate builds are deduplicated), and the geometry certificate
carries per-part volume and mass. This module joins the two.

Deliberately not a manufacturing BOM: no supplier, no cost, no revision, no
make/buy. It reports what the model actually contains.
"""

from __future__ import annotations

import csv
import io
from typing import Any


def _mass_of(cert: dict | None) -> float | None:
    if not cert:
        return None
    mass = cert.get("mass_g")
    return mass if isinstance(mass, (int, float)) else None


def build_bom(
    manifest: Any,
    check_reports: dict[str, Any] | None = None,
    material: str | None = None,
) -> dict:
    """Assemble a parts list from the manifest and per-part certificates.

    Args:
        manifest: an AssemblyManifest (needs ``.parts`` with part_id/description).
        check_reports: part_id -> CheckReport, for per-part mass. Identical
            parts share one report, which is why quantity has to be counted
            from the manifest rather than from the reports.
        material: resolved material name from the certificate, applied to every
            line — the pipeline has no per-part material model.

    Returns a JSON-serialisable dict with ``lines``, ``total_parts``,
    ``total_mass_g`` and a ready-to-download ``csv`` string.
    """
    parts = list(getattr(manifest, "parts", []) or [])
    if not parts:
        return {"lines": [], "total_parts": 0, "total_mass_g": None, "csv": ""}

    reports = check_reports or {}

    # Group by description: identical descriptions are the same physical part.
    # Order follows first appearance so the list reads in planner build order
    # (base/foundation first), which is what an assembly drawing expects.
    order: list[str] = []
    groups: dict[str, list[Any]] = {}
    for part in parts:
        desc = part.description
        if desc not in groups:
            groups[desc] = []
            order.append(desc)
        groups[desc].append(part)

    lines: list[dict] = []
    total_mass: float | None = 0.0
    for desc in order:
        members = groups[desc]
        rep = reports.get(members[0].part_id)
        cert = getattr(rep, "geometry_cert", None) if rep is not None else None
        unit_mass = _mass_of(cert)
        qty = len(members)

        line_mass = unit_mass * qty if unit_mass is not None else None
        if line_mass is None:
            total_mass = None
        elif total_mass is not None:
            total_mass += line_mass

        lines.append({
            "part_id": members[0].part_id,
            # Every instance, so a reader can map the BOM back to the model
            # tree rather than guessing which leg is which.
            "instances": [p.part_id for p in members],
            "quantity": qty,
            "description": desc,
            "material": material,
            "mass_g": unit_mass,
            "line_mass_g": line_mass,
        })

    return {
        "lines": lines,
        "total_parts": len(parts),
        "unique_parts": len(lines),
        "total_mass_g": total_mass,
        "csv": to_csv(lines, total_mass),
    }


# Characters that make Excel / Google Sheets treat a cell as a formula.
_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: object) -> str:
    """Neutralise spreadsheet formula injection in a BOM cell.

    Part descriptions and ids are LLM output steered by the user's prompt
    (``PartDefinition.description`` is 2000 chars of unconstrained free text),
    and a BOM is a document made to be shared with colleagues. A description
    engineered to begin with '=' or '@' would execute as a formula — a
    HYPERLINK-style exfiltration payload, say — the moment a teammate opened
    the CSV. Prefixing with an apostrophe is the standard neutralisation: it
    forces text interpretation and is stripped on display.
    """
    text = "" if value is None else str(value)
    if text.startswith(_CSV_FORMULA_TRIGGERS):
        return "'" + text
    return text


def to_csv(lines: list[dict], total_mass_g: float | None = None) -> str:
    """Render BOM lines as CSV for download into a spreadsheet."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Item", "Part ID", "Qty", "Material",
        "Unit mass (g)", "Line mass (g)", "Instances", "Description",
    ])
    for i, line in enumerate(lines, 1):
        writer.writerow([
            i,
            _csv_safe(line.get("part_id", "")),
            line.get("quantity", 1),
            _csv_safe(line.get("material") or ""),
            _fmt(line.get("mass_g")),
            _fmt(line.get("line_mass_g")),
            _csv_safe(" ".join(line.get("instances", []))),
            _csv_safe((line.get("description") or "").replace("\n", " ")),
        ])
    if total_mass_g is not None:
        writer.writerow([])
        writer.writerow(["", "TOTAL", "", "", "", _fmt(total_mass_g), "", ""])
    return buf.getvalue()


def _fmt(value: float | None) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else ""
