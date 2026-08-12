"""Drake agent definitions for CrewAI orchestration."""

import importlib.util
import sys
from pathlib import Path

_agents_dir = Path(__file__).resolve().parent

def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

_po = _load_module("product_owner", _agents_dir / "product_owner.py")
_el = _load_module("engineering_lead", _agents_dir / "engineering_lead.py")
_rm = _load_module("release_manager", _agents_dir / "release_manager.py")

create_po_agent = _po.create_po_agent
create_el_agent = _el.create_el_agent
create_rm_agent = _rm.create_rm_agent

def create_agents():
    return {
        "product_owner": create_po_agent(),
        "engineering_lead": create_el_agent(),
        "release_manager": create_rm_agent(),
    }
