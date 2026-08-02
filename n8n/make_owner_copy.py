"""Merge the owner's live settings onto freshly generated workflows.

For each workflow: take the fresh build, carry over the owner's credential
attachments, node enable/disable choices, and the config values they filled
by hand — producing an import-ready "ver N" file. Outputs contain real
secrets and match gitignore patterns; never commit them.

Run AFTER the build scripts:  python n8n/make_owner_copy.py
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

JOBS = [
    {
        "fresh": HERE / "PILOT_CONSOLE_CHECK-BOT.json",
        "owner": HERE / "PILOT CONSOLE CHECK-BOT latest.json",
        "owner_fallback": HERE / "PILOT CONSOLE CHECK-BOT ver 2.json",
        "out": HERE / "PILOT CONSOLE CHECK-BOT ver 4.json",
        "out_name": "PILOT CONSOLE CHECK-BOT ver 4",
        "config_node": "Pilot Config",
        # Everything else (QB realm, drive folder, flags) ships as fresh
        # defaults, which already match the sandbox setup.
        "keep": {"console_base_url", "pilot_hook_secret", "anthropic_api_key"},
    },
    {
        "fresh": HERE / "PILOT_CONSOLE_PAYMENT-SWEEP.json",
        "owner": HERE / "PILOT CONSOLE PAYMENT-SWEEP latest.json",
        "owner_fallback": None,
        "out": HERE / "PILOT CONSOLE PAYMENT-SWEEP ver 3.json",
        "out_name": "PILOT CONSOLE PAYMENT-SWEEP ver 3",
        "config_node": "Sweep Config",
        "keep": {"console_base_url", "pilot_hook_secret"},
    },
    {
        "fresh": HERE / "PILOT_CONSOLE_SLABBOT.json",
        "owner": HERE / "PILOT CONSOLE SLABBOT latest.json",
        "owner_fallback": None,
        "out": HERE / "PILOT CONSOLE SLABBOT ver 3.json",
        "out_name": "PILOT CONSOLE SLABBOT ver 3",
        "config_node": "Slab Config",
        # drive_folder_id ships fresh (the TEST folder default).
        "keep": {"console_base_url", "pilot_hook_secret", "anthropic_api_key"},
    },
]

# Values shared by every workflow (same console, same secret, same key):
# collected from whichever owner export has them filled, then used to fill
# any PASTE placeholder left in another workflow's config.
SHARED_KEYS = {"console_base_url", "pilot_hook_secret", "anthropic_api_key"}
shared: dict = {}
for job in JOBS:
    path = job["owner"] if job["owner"].exists() else job["owner_fallback"]
    if not path or not path.exists():
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    for node in data["nodes"]:
        if node["name"] == job["config_node"]:
            for a in node["parameters"]["assignments"]["assignments"]:
                v = a.get("value")
                if (a["name"] in SHARED_KEYS and isinstance(v, str)
                        and v and not v.startswith("PASTE")):
                    shared.setdefault(a["name"], v)

for job in JOBS:
    owner_path = job["owner"]
    if not owner_path.exists() and job["owner_fallback"]:
        owner_path = job["owner_fallback"]
    if not owner_path.exists():
        print(f"SKIP {job['out'].name}: no owner export found")
        continue

    fresh = json.loads(job["fresh"].read_text(encoding="utf-8"))
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    owner_by_name = {n["name"]: n for n in owner["nodes"]}

    carried, still_need = [], []
    for node in fresh["nodes"]:
        prev = owner_by_name.get(node["name"])
        if prev is None:
            # New node this build (e.g. "<Config> D"): still fill its config
            # values from the owner's main config node.
            if node["name"] == job["config_node"] + " D":
                src_cfg = owner_by_name.get(job["config_node"])
                if src_cfg:
                    prev_vals = {a["name"]: a.get("value")
                                 for a in src_cfg["parameters"]["assignments"]["assignments"]}
                    for a in node["parameters"]["assignments"]["assignments"]:
                        if a["name"] in job["keep"] and a["name"] in prev_vals:
                            a["value"] = prev_vals[a["name"]]
                        if (isinstance(a.get("value"), str)
                                and a["value"].startswith("PASTE")
                                and a["name"] in shared):
                            a["value"] = shared[a["name"]]
            continue
        if prev.get("credentials"):
            node["credentials"] = prev["credentials"]
            carried.append(node["name"])
        if prev.get("disabled"):
            node["disabled"] = True
        elif "disabled" in node and not prev.get("disabled"):
            del node["disabled"]
        if node["name"] in (job["config_node"], job["config_node"] + " D"):
            src_cfg = owner_by_name.get(job["config_node"]) or prev
            prev_vals = {a["name"]: a.get("value")
                         for a in src_cfg["parameters"]["assignments"]["assignments"]}
            for a in node["parameters"]["assignments"]["assignments"]:
                if a["name"] in job["keep"] and a["name"] in prev_vals:
                    a["value"] = prev_vals[a["name"]]
                # A blank the owner never filled in THIS workflow gets the
                # shared value from a sibling export.
                if (isinstance(a.get("value"), str)
                        and a["value"].startswith("PASTE")
                        and a["name"] in shared):
                    a["value"] = shared[a["name"]]

    for node in fresh["nodes"]:
        needs_cred = (
            node["type"] == "n8n-nodes-base.googleDrive"
            or node.get("parameters", {}).get("nodeCredentialType") == "quickBooksOAuth2Api"
        )
        if needs_cred and not node.get("credentials") and not node.get("disabled"):
            still_need.append(node["name"])

    fresh["name"] = job["out_name"]
    job["out"].write_text(json.dumps(fresh, indent=1, ensure_ascii=False),
                          encoding="utf-8")
    print(f"OK -> {job['out'].name}  (settings from {owner_path.name})")
    print("   credentials carried:", len(carried),
          "| enabled nodes still needing a credential:",
          ", ".join(still_need) or "(none)")
