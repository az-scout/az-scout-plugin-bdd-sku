"""Static mapping tables for Azure VM SKU classification."""

# Family prefix → category.
# Order matters for multi-char families: check longer prefixes first in code.
FAMILY_CATEGORY: dict[str, str] = {
    "B": "burstable",
    "D": "general",
    "E": "memory",
    "F": "compute",
    "L": "storage",
    "M": "memory",
    # GPU families (N prefix)
    "NC": "gpu",
    "ND": "gpu",
    "NV": "gpu",
    "NP": "gpu",
    "N": "gpu",  # fallback for unknown N-families
    # HPC families
    "HB": "hpc",
    "HC": "hpc",
    "HX": "hpc",
    "H": "hpc",  # fallback for unknown H-families
}

# Multi-letter family prefixes that must be matched before single-letter ones.
MULTI_LETTER_FAMILIES: tuple[str, ...] = (
    "NC",
    "ND",
    "NV",
    "NP",
    "HB",
    "HC",
    "HX",
    "DC",
    "EC",
)

# Suffix character → workload tag (best-effort).
SUFFIX_TAGS: dict[str, str] = {
    "s": "premium-storage",
    "d": "local-disk",
    "a": "amd",
    "i": "isolated",
    "m": "memory-intensive",
    "l": "low-memory",
    "r": "rdma",
    "p": "arm",
    "b": "block-storage",
    "t": "tiny",
    "c": "confidential",
}


def category_for_family(family: str) -> str:
    """Return the workload category for a given VM family prefix.

    Checks multi-letter keys first, then falls back to single-letter, then 'other'.
    """
    upper = family.upper()
    # Try exact match (handles multi-letter like NC, HB, DC, etc.)
    if upper in FAMILY_CATEGORY:
        return FAMILY_CATEGORY[upper]
    # Try first letter fallback
    if upper and upper[0] in FAMILY_CATEGORY:
        return FAMILY_CATEGORY[upper[0]]
    return "other"
