from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .config import CURRENT_RUNTIME_CONTRACT


SCRIPT_PATHS = ("scripts/query.py", "scripts/wiki_graph.py")
KNOWN_LEGACY_HASHES = {
    "scripts/query.py": {
        "43238f69829528f4023fd7aa09e145a6dfc397cbc3da53ac77329cd85870bb68",
    },
    "scripts/wiki_graph.py": {
        "0a683810a517f44b1465820df2892694fd21982259c208a5bb752955dba3d90f",
    },
}
KNOWN_CUSTOMIZATION_HASHES = {
    "scripts/query.py": {
        "0c1f3c16ae0c7258b63c3747addc71c2d699c5a2787e1d3ef9c78af5ab824b49": (
            "exclude_directory:.agents",
        ),
    },
    "scripts/wiki_graph.py": {
        "523ab3fe69f617c99939b2a308eba0fbd13cf7697b8a71793230f2b562f53273": (
            "exclude_directory:.agents",
        ),
    },
}
ADAPTER_MARKER = f"# llm-wiki-adapter runtime_contract={CURRENT_RUNTIME_CONTRACT}"
KNOWN_PREVIOUS_ADAPTER_HASHES = {
    "scripts/query.py": {
        "9f03b60619f0f038ab455426560e047271669f3054cb7ebb24bf4aa7564a4f02",
    },
    "scripts/wiki_graph.py": {
        "5bb0ee0e6007acb2f8acd3969657a6c2f245f484a265e253c3793470857eada9",
    },
}

KNOWN_ADAPTER_HASHES = {
    "scripts/query.py": {
        "99aa2ae7b0936034284d1ab1ac8addd39c8960d92792672cebdf8097afd70b24",
    },
    "scripts/wiki_graph.py": {
        "9f3de8a822e1bd0c01f9b77f97cbf2f02865d14bb407b65a5e92bcc9f2f4fc24",
    },
}


def inspect_scripts(wiki_root: str | Path) -> dict[str, dict]:
    root = Path(wiki_root).expanduser().resolve()
    return {relative: inspect_script(root / relative, relative) for relative in SCRIPT_PATHS}


def inspect_script(path: Path, relative: str) -> dict:
    if not path.is_file():
        return {"status": "missing", "sha256": None}
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    text = content.decode("utf-8", errors="replace")
    if digest in KNOWN_ADAPTER_HASHES.get(relative, set()):
        status = "compatible_adapter"
        customizations: tuple[str, ...] = ()
    elif digest in KNOWN_PREVIOUS_ADAPTER_HASHES.get(relative, set()):
        status = "outdated_adapter"
        customizations = ()
    elif digest in KNOWN_LEGACY_HASHES.get(relative, set()):
        status = "canonical_legacy_copy"
        customizations = ()
    elif digest in KNOWN_CUSTOMIZATION_HASHES.get(relative, {}):
        status = "supported_customization"
        customizations = KNOWN_CUSTOMIZATION_HASHES[relative][digest]
    else:
        status = "modified_unknown"
        customizations = ()
    result = {"status": status, "sha256": digest, "customizations": list(customizations)}
    marker = re.search(r"(?m)^# llm-wiki-adapter runtime_contract=([0-9]+)$", text)
    if marker is not None and status == "modified_unknown":
        result["claimed_adapter_contract"] = marker.group(1)
    return result
