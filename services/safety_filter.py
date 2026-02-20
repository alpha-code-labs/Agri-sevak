"""
Safety filter for banned/restricted pesticides.

Loads data/banned_pesticides.json and provides:
1. Evidence scanning — detect banned chemicals in RAG evidence for a given crop
2. RAG warning injection — add warnings to RAG results before Gemini sees them
3. Auditor safety instruction — generate crop-specific banned list for the auditor prompt
"""

import json
import os
import re
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from services.config import Config

logger = logging.getLogger("safety_filter")

_BANNED_DATA: Optional[Dict[str, Any]] = None
_BANNED_DATA_PATH = os.path.join(Config.data_dir, "banned_pesticides.json")


def _load_banned_data() -> Dict[str, Any]:
    global _BANNED_DATA
    if _BANNED_DATA is not None:
        return _BANNED_DATA

    try:
        with open(_BANNED_DATA_PATH, "r", encoding="utf-8") as f:
            _BANNED_DATA = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.error("Failed to load banned_pesticides.json: %s", exc)
        _BANNED_DATA = {}

    return _BANNED_DATA


def _normalize(s: str) -> str:
    return (s or "").strip().lower()


def _all_names_for_chemical(chem: Dict[str, Any]) -> List[str]:
    """Return all searchable names (primary + aliases) for a chemical entry."""
    names = [chem.get("name", "")]
    names.extend(chem.get("aliases", []))
    return [n for n in names if n]


def _crop_matches(crop: str, banned_crop: str) -> bool:
    """Check if a crop matches a banned_crop entry (case-insensitive)."""
    c = _normalize(crop)
    b = _normalize(banned_crop)
    if not c or not b:
        return False
    # Direct match
    if c == b:
        return True
    # Substring match for multi-word entries like "fruits consumed raw"
    if b in c or c in b:
        return True
    return False


def get_banned_chemicals_for_crop(crop: str) -> List[Dict[str, Any]]:
    """
    Returns all chemicals that must NOT be recommended for this crop.
    Includes:
    - All universally banned chemicals
    - All refused registration chemicals
    - All withdrawn chemicals
    - Restricted chemicals where this crop is in the banned_crops list
    """
    data = _load_banned_data()
    if not data:
        return []

    result = []

    # Universally banned — never recommend for any crop
    for chem in data.get("banned", {}).get("chemicals", []):
        result.append({
            "name": chem.get("name", ""),
            "reason": "Completely banned in India",
            "category": "banned",
        })

    # Banned for export only — also banned for domestic farmer use
    for chem in data.get("banned_for_export_only", {}).get("chemicals", []):
        result.append({
            "name": chem.get("name", ""),
            "reason": "Banned for domestic use in India",
            "category": "banned",
        })

    # Withdrawn
    for chem in data.get("withdrawn", {}).get("chemicals", []):
        result.append({
            "name": chem.get("name", ""),
            "reason": "Withdrawn from use in India",
            "category": "withdrawn",
        })

    # Refused registration
    for chem in data.get("refused_registration", {}).get("chemicals", []):
        result.append({
            "name": chem.get("name", ""),
            "reason": "Never registered in India",
            "category": "refused",
        })

    # Restricted — only if this crop is in the banned_crops list
    for chem in data.get("restricted", {}).get("chemicals", []):
        banned_crops = chem.get("banned_crops", [])
        restriction = chem.get("restriction", "")

        # Check if this crop matches any banned_crop entry
        crop_match = any(_crop_matches(crop, bc) for bc in banned_crops)

        if crop_match:
            result.append({
                "name": chem.get("name", ""),
                "reason": f"Restricted: {restriction}",
                "category": "restricted",
                "notification": chem.get("notification", ""),
            })

    return result


def _build_search_patterns(crop: str) -> List[Tuple[re.Pattern, str, str]]:
    """
    Build regex patterns for all chemicals banned for this crop.
    Returns list of (compiled_pattern, chemical_name, reason).
    """
    banned = get_banned_chemicals_for_crop(crop)
    data = _load_banned_data()
    patterns = []

    for entry in banned:
        name = entry["name"]
        reason = entry["reason"]

        # Collect all name variants
        all_names = [name]

        # Find the original chemical entry to get aliases
        for section_key in ("banned", "banned_for_export_only", "withdrawn", "refused_registration", "restricted"):
            section = data.get(section_key, {})
            for chem in section.get("chemicals", []):
                if _normalize(chem.get("name", "")) == _normalize(name):
                    all_names.extend(chem.get("aliases", []))
                    break

        # Build case-insensitive pattern for each name variant
        for n in all_names:
            if not n:
                continue
            escaped = re.escape(n)
            pattern = re.compile(escaped, re.IGNORECASE)
            patterns.append((pattern, name, reason))

    return patterns


def scan_text_for_banned(text: str, crop: str) -> List[Dict[str, str]]:
    """
    Scan a text string for banned chemical names for a given crop.
    Returns list of {"name": ..., "reason": ...} for each match found.
    """
    if not text or not crop:
        return []

    patterns = _build_search_patterns(crop)
    found = []
    seen_names: Set[str] = set()

    for pattern, name, reason in patterns:
        if name in seen_names:
            continue
        if pattern.search(text):
            found.append({"name": name, "reason": reason})
            seen_names.add(name)

    return found


def inject_rag_warnings(rag_results: List[Dict[str, Any]], crop: str) -> List[Dict[str, Any]]:
    """
    Scan RAG evidence for banned chemicals and inject warnings.
    Modifies rag_results in place and returns them.
    """
    if not rag_results or not crop:
        return rag_results

    for entry in rag_results:
        evidence_list = entry.get("evidence", [])
        if not evidence_list:
            continue

        all_matches = []
        for evidence_text in evidence_list:
            matches = scan_text_for_banned(evidence_text, crop)
            all_matches.extend(matches)

        # Dedupe
        seen = set()
        unique_matches = []
        for m in all_matches:
            if m["name"] not in seen:
                seen.add(m["name"])
                unique_matches.append(m)

        if unique_matches:
            warnings = []
            for m in unique_matches:
                warnings.append(
                    f"⚠️ BANNED: {m['name']} is banned for {crop} per CIB&RC India. "
                    f"Reason: {m['reason']}. Do NOT recommend this chemical. "
                    f"Suggest a safe, registered alternative instead."
                )
            entry["safety_warnings"] = warnings

            logger.warning(
                "Banned chemicals found in RAG evidence for crop=%s: %s",
                crop,
                [m["name"] for m in unique_matches],
            )

    return rag_results


def get_auditor_safety_instruction(crop: str) -> str:
    """
    Generate a crop-specific safety instruction block for the auditor prompt.
    """
    banned = get_banned_chemicals_for_crop(crop)
    if not banned:
        return ""

    # Group by category for cleaner output
    fully_banned = [b for b in banned if b["category"] in ("banned", "refused", "withdrawn")]
    restricted = [b for b in banned if b["category"] == "restricted"]

    lines = [
        f"\n\nCRITICAL SAFETY RULE — BANNED PESTICIDES FOR {crop.upper()}:",
        "The following chemicals are BANNED by CIB&RC India. If ANY of these appear in the response, "
        "you MUST remove that recommendation entirely and suggest a safe, registered alternative.",
        "",
    ]

    if restricted:
        lines.append(f"BANNED SPECIFICALLY FOR {crop.upper()}:")
        for b in restricted:
            lines.append(f"  - {b['name']} ({b['reason']})")
        lines.append("")

    # Only include a summary count for universally banned, not the full list
    if fully_banned:
        lines.append(
            f"Additionally, {len(fully_banned)} chemicals are completely banned in India "
            f"(including Endosulfan, Methyl Parathion, Phorate, Dichlorovos, Carbaryl, etc.). "
            f"Do not recommend any of these."
        )
        lines.append("")

    lines.append(
        "If you remove a banned chemical, restructure the answer to maintain completeness — "
        "suggest an alternative treatment or direct the farmer to consult HAU/KVK experts."
    )

    return "\n".join(lines)
