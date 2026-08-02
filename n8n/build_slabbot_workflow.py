"""Assemble n8n/PILOT_CONSOLE_SLABBOT.json from the production export.

Transplants SLABBOT's battle-tested OCR (Read Slip prompt + Parse Slip with
the 17-supplier canon, per-slab serial reading, validation math) and the
Drive tree (Year -> Supplier, pointed at the owner's TEST folder), replaces
Airtable with the console's slab hooks, and REPLACES the whole WhatsApp
question dialog with nothing: the manager assigns jobs on the card
(typeahead/poll/stock) and the console calls this workflow's second webhook
(slab-decision) with the complete mapping -> one Moraware note per job.

One slip = one push (the update hook's "needs a job"); everything after is
quiet.

Run:  python n8n/build_slabbot_workflow.py
"""

import copy
import json
import re
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "n8n" / "SLABBOT (10).json"
TARGET = REPO / "n8n" / "PILOT_CONSOLE_SLABBOT.json"

src = json.loads(SOURCE.read_text(encoding="utf-8"))
src_by_name = {n["name"]: n for n in src["nodes"]}

_key = re.search(r"sk-ant-[A-Za-z0-9_\-]+", json.dumps(src))
LIVE_KEY = _key.group(0) if _key else None

PROD_DRIVE_ROOT = "1N6tVWrWGamfg_36K-tW2ZTL_yIIKK-Kc"

GLOBAL_SWAPS = [
    # WhatsApp-era inputs -> console trigger context
    ("$('Get Image Base64').first().json.base64",
     "$('Trigger Context').first().json.image_base64"),
    ("$('Receive Message').item.json.body.data.message.imageMessage?.caption",
     "$('Trigger Context').item.json.caption"),
    ("const wb=$('Receive Message').item.json.body.data;",
     "const wb={pushName:$('Trigger Context').item.json.submitted_by_name,"
     "key:{id:$('Trigger Context').item.json.review_item_id,participant:''},"
     "message:{imageMessage:{caption:null}}};"),
    ("https://bridge.emgcheckbot.us",
     "{{ $('Slab Config').item.json.bridge_base_url }}"),
]
if LIVE_KEY:
    GLOBAL_SWAPS.insert(0, (LIVE_KEY, "={{ $('Slab Config').item.json.anthropic_api_key }}"))

NODE_SWAPS = {
    # Drive root -> the owner's TEST folder from config. Two different
    # syntaxes: inside Search Year's ={{ ... }} expression the id must be
    # CONCATENATED in JS; in Create Year's resource-locator the whole value
    # must become its own ={{ ... }} expression.
    "Search Year": [
        ("and '" + PROD_DRIVE_ROOT + "' in parents",
         "and '\" + $('Slab Config').item.json.drive_folder_id + \"' in parents"),
    ],
    "Create Year": [
        (PROD_DRIVE_ROOT, "={{ $('Slab Config').item.json.drive_folder_id }}"),
    ],
    "Read Slip": [
        ('"data": "{{ $json.base64 }}"',
         '"data": "{{ $(\'Trigger Context\').item.json.image_base64 }}"'),
    ],
    # Pilot uploads into Year/Supplier directly (FOR JOBS/STOCK layer deferred).
    "Upload to Drive": [
        ("$('Set ForJobs Folder').item.json.forjobs_folder_id",
         "$('Set Supplier Folder').item.json.supplier_folder_id"),
    ],
    # Full quality: the binary comes from the console's original, not the
    # downscaled OCR copy.
    "Prep Binary": [
        ("const b64=$('Get Image Base64').first().json.base64;\n"
         "const fn=$('Parse Slip').first().json.photo_filename||'slip.jpg';\n"
         "return [{ json:{ fileName:fn }, binary:{ data:{ data:b64, mimeType:'image/jpeg', fileName:fn } } }];",
         "const src=$('Fetch Original Photo').item.binary.data;\n"
         "const fn=$('Parse Slip').first().json.photo_filename||'slip.jpg';\n"
         "return [{ json:{ fileName:fn }, binary:{ data: Object.assign({}, src, { fileName: fn }) } }];"),
    ],
}


def walk_swap(value, swaps):
    if isinstance(value, dict):
        return {k: walk_swap(v, swaps) for k, v in value.items()}
    if isinstance(value, list):
        return [walk_swap(v, swaps) for v in value]
    if isinstance(value, str):
        for old, new_ in swaps:
            value = value.replace(old, new_)
        return value
    return value


nodes, connections, _used = [], {}, set()


def _nid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "pilot-slabbot/" + name))


def add(node):
    if node["name"] in _used:
        raise SystemExit(f"duplicate node name: {node['name']}")
    _used.add(node["name"])
    nodes.append(node)
    return node["name"]


def port(name):
    orig = src_by_name[name]
    params = copy.deepcopy(orig.get("parameters", {}))
    params = walk_swap(params, NODE_SWAPS.get(name, []) + GLOBAL_SWAPS)
    if isinstance(params.get("url"), str) and "{{" in params["url"] and not params["url"].startswith("="):
        params["url"] = "=" + params["url"]
    return add({"id": _nid(name), "name": name, "type": orig["type"],
                "typeVersion": orig.get("typeVersion", 1), "position": [0, 0],
                "parameters": params})


def new(name, type_, tv, params, webhook_id=False):
    node = {"id": _nid(name), "name": name, "type": type_, "typeVersion": tv,
            "position": [0, 0], "parameters": params}
    if webhook_id:
        node["webhookId"] = _nid(name + "/webhook")
    return add(node)


def wire(frm, to, output=0):
    outs = connections.setdefault(frm, {"main": []})["main"]
    while len(outs) <= output:
        outs.append([])
    outs[output].append({"node": to, "type": "main", "index": 0})


def if_node(name, left_expr):
    return new(name, "n8n-nodes-base.if", 2.2, {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "",
                        "typeValidation": "loose", "version": 3},
            "conditions": [{"id": _nid(name + "/c")[:8],
                            "leftValue": "={{ " + left_expr + " }}",
                            "rightValue": "={{ true }}",
                            "operator": {"type": "boolean", "operation": "true",
                                         "singleValue": True}}],
            "combinator": "and"},
        "options": {},
    })


def no_op(name):
    return new(name, "n8n-nodes-base.noOp", 1, {})


def code(name, js):
    return new(name, "n8n-nodes-base.code", 2, {"jsCode": js})


SECRET_HEADER = {"name": "X-Pilot-Secret",
                 "value": "={{ $('Slab Config').item.json.pilot_hook_secret }}"}
HOOK_BASE = "{{ $('Slab Config').item.json.console_base_url }}/api/hooks"


def hook_post(name, path, json_body):
    return new(name, "n8n-nodes-base.httpRequest", 4.4, {
        "method": "POST", "url": "=" + HOOK_BASE + path,
        "sendHeaders": True,
        "headerParameters": {"parameters": [dict(SECRET_HEADER),
                                            {"name": "Content-Type", "value": "application/json"}]},
        "sendBody": True, "specifyBody": "json", "jsonBody": json_body,
        "options": {},
    })


# ===========================================================================
# INGEST FLOW: photo -> OCR -> dedup -> Drive -> details + THE one push
# ===========================================================================
new("Slab Trigger", "n8n-nodes-base.webhook", 2.1,
    {"httpMethod": "POST", "path": "pilot-slabbot", "options": {}},
    webhook_id=True)

new("Slab Config", "n8n-nodes-base.set", 3.4, {
    "assignments": {"assignments": [
        {"id": "c1", "name": "console_base_url", "type": "string",
         "value": "PASTE_CONSOLE_BASE_URL"},
        {"id": "c2", "name": "pilot_hook_secret", "type": "string",
         "value": "PASTE_PILOT_HOOK_SECRET"},
        {"id": "c3", "name": "anthropic_api_key", "type": "string",
         "value": "PASTE_ANTHROPIC_KEY"},
        {"id": "c4", "name": "bridge_base_url", "type": "string",
         "value": "https://bridge.emgcheckbot.us"},
        # Owner's TEST folder for slab-slip archiving (Year -> Supplier tree).
        {"id": "c5", "name": "drive_folder_id", "type": "string",
         "value": "1-dfgL6KaAkEt1tTEobxYOT_z0N94x5w0"},
    ]},
    "includeOtherFields": True, "options": {},
})

if_node("Verify Secret",
        "$('Slab Trigger').item.json.body.secret === $json.pilot_hook_secret")
no_op("Drop (Bad Secret)")

code("Trigger Context", """\
// Console outbound payload (deliveries.py):
// { review_item_id, image_base64, submitted_by, secret }
const b = $('Slab Trigger').item.json.body || {};
const submitted = String(b.submitted_by || '').trim();
const local = submitted.split('@')[0] || '';
return { json: {
  review_item_id: String(b.review_item_id || ''),
  image_base64: String(b.image_base64 || ''),
  submitted_by: submitted || null,
  submitted_by_name: local ? (local.charAt(0).toUpperCase() + local.slice(1)) : 'a manager',
  caption: ''
}};
""")

port("Read Slip")
port("Parse Slip")

# --- dedup via the console register (SLABBOT's exact keys) -----------------
new("Dedup Lookup", "n8n-nodes-base.httpRequest", 4.4, {
    "method": "GET",
    "url": ("=" + HOOK_BASE + "/slab/find?supplier={{ encodeURIComponent($json.supplier_canonical || '') }}"
            "{{ $json.document_number ? ('&document_number=' + encodeURIComponent($json.document_number)) "
            ": ('&total=' + encodeURIComponent($json.total ?? 0) + '&slab_count=' + encodeURIComponent($json.slab_count ?? 0)) }}"),
    "sendHeaders": True,
    "headerParameters": {"parameters": [dict(SECRET_HEADER)]},
    "options": {},
})

code("Dedup Decide", """\
const me = $('Trigger Context').item.json.review_item_id;
const hits = ($json.items || []).filter(r => r.review_item_id !== me
  && r.status !== 'duplicate');
return { json: Object.assign({}, $('Parse Slip').item.json,
  { _dupId: hits.length ? hits[0].review_item_id : '' }) };
""")

if_node("Already Uploaded?", "($json._dupId || '') !== ''")

RIID = "$('Trigger Context').item.json.review_item_id"
PARSE = "$('Parse Slip').item.json"
DUP_CARD = ("$('Slab Config').item.json.console_base_url + '/deliveries/item/' + "
            "($('Dedup Decide').item.json._dupId || '')")

hook_post("Final: Duplicate Slip", "/slab/final", (
    "={\n"
    '  "review_item_id": "{{ ' + RIID + ' }}",\n'
    '  "body": {{ JSON.stringify(\'\\u26a0 This slip was already uploaded \\u2014 \' + (' + PARSE + '.supplier_canonical || \'supplier\') + \' \' + (' + PARSE + '.document_number || \'\') + \' is on an earlier card. See it here: \' + (' + DUP_CARD + ') + \'. Nothing recorded twice.\') }},\n'
    '  "status": "duplicate",\n'
    '  "push": true\n'
    "}"
))

# --- Drive tree (Year -> Supplier under the TEST folder) -------------------
new("Fetch Original Photo", "n8n-nodes-base.httpRequest", 4.4, {
    "method": "GET",
    "url": "=" + HOOK_BASE + "/pilot/photo/{{ " + RIID + " }}",
    "sendHeaders": True,
    "headerParameters": {"parameters": [dict(SECRET_HEADER)]},
    "options": {"response": {"response": {"responseFormat": "file"}}},
})

port("Search Year")
nodes[-1]["alwaysOutputData"] = True  # empty TEST folder must not kill the chain
port("Year Exists?")
port("Create Year")
port("Set Year Folder")
port("Search Supplier")
nodes[-1]["alwaysOutputData"] = True
port("Supplier Exists?")
port("Create Supplier")
port("Set Supplier Folder")
port("Prep Binary")
port("Upload to Drive")

# --- details + the delivery's ONE push --------------------------------------
MATS = PARSE + ".materials"
hook_post("Update: Slip Details", "/slab/update", (
    "={\n"
    '  "review_item_id": "{{ ' + RIID + ' }}",\n'
    '  "body": {{ JSON.stringify(\'Read the slip: \' + (' + PARSE + '.supplier_canonical || \'unknown supplier\') + \' \\u2014 \' + (' + PARSE + '.document_number || \'no #\') + \' \\u00b7 \' + (' + PARSE + '.slab_count ?? \'?\') + \' slabs \\u00b7 \' + (' + PARSE + '.materials_summary || \'\') + \'. \' + (' + PARSE + '.validation_note || \'\')) }},\n'
    '  "details": {{ JSON.stringify({supplier: ' + PARSE + '.supplier_canonical, supplier_confidence: ' + PARSE + '.supplier_confidence, document_number: ' + PARSE + '.document_number, order_date: ' + PARSE + '.order_date_iso, subtotal: ' + PARSE + '.subtotal, tax: ' + PARSE + '.tax, total: ' + PARSE + '.total, slab_count: ' + PARSE + '.slab_count, hand_notes: ' + PARSE + '.hand_notes, validation_note: ' + PARSE + '.validation_note, validation_ok: ' + PARSE + '.validation_ok, materials: (' + MATS + ' || []).map(function(m){return {material: m.material, finish: m.finish, thickness: m.thickness, area: m.area, slab_count: m.slab_count, total_sf: m.total_sf, serials: m.serials, barcodes: m.barcodes, lot: m.lot, unit_price: m.unit_price, extended_price: m.extended_price};}), drive_file_id: $(\'Upload to Drive\').item.json.id, drive_url: \'https://drive.google.com/file/d/\' + $(\'Upload to Drive\').item.json.id + \'/view\', supplier_folder_id: $(\'Set Supplier Folder\').item.json.supplier_folder_id}) }},\n'
    '  "status": "needs_job",\n'
    '  "push": true,\n'
    '  "push_title": {{ JSON.stringify(\'Delivery from \' + (' + PARSE + '.supplier_canonical || \'a supplier\') + \' \\u2014 \' + ((' + MATS + ' || []).length) + \' material\' + ((' + MATS + ' || []).length === 1 ? \'\' : \'s\') + \' \\u2014 needs a job\') }}\n'
    "}"
))

# ===========================================================================
# DECISION FLOW: console confirm -> Moraware notes -> quiet final
# ===========================================================================
new("Slab Decision Webhook", "n8n-nodes-base.webhook", 2.1,
    {"httpMethod": "POST", "path": "slab-decision", "options": {}},
    webhook_id=True)

# The decision flow is a SEPARATE execution — the ingest flow's Slab Config
# node never ran there, so decision nodes need their own copy.
CONFIG_D_ASSIGNMENTS = None
for _n in nodes:
    if _n["name"] == "Slab Config":
        CONFIG_D_ASSIGNMENTS = copy.deepcopy(_n["parameters"])
new("Slab Config D", "n8n-nodes-base.set", 3.4, CONFIG_D_ASSIGNMENTS)

code("Parse Confirm", """\
// Console confirm payload (deliveries.py confirm_delivery):
// { secret, review_item_id, supplier, document_number, received, slab_count,
//   drive_file_id, supplier_folder_id, all_stock, jobs:[{job_id, job_name,
//   moraware_url, materials:[{material, slab_count, total_sf}]}],
//   stock_materials:[...], confirmed_by }
const b = $('Slab Decision Webhook').first().json.body || {};
const ok = String(b.secret || '') === $('Slab Config D').first().json.pilot_hook_secret;
const jobs = Array.isArray(b.jobs) ? b.jobs : [];
const stock = Array.isArray(b.stock_materials) ? b.stock_materials : [];
function matLine(list) {
  return list.map(m => (m.material || 'material')
    + (m.slab_count != null ? (' \\u00d7' + m.slab_count) : '')
    + (m.total_sf != null ? (' (' + m.total_sf + ' sf)') : '')).join(', ');
}
const who = String(b.confirmed_by || '').split('@')[0] || 'a manager';
const summary = jobs.map(j => matLine(j.materials) + ' \\u2192 ' + (j.job_name || ('job #' + j.job_id)))
  .concat(stock.length ? [matLine(stock) + ' \\u2192 Stock'] : []).join('; ');
return { json: {
  valid: ok && !!b.review_item_id,
  review_item_id: String(b.review_item_id || ''),
  supplier: b.supplier || '', document_number: b.document_number || '',
  received: b.received || '', all_stock: !!b.all_stock,
  confirmed_by: who, summary: summary,
  jobs: jobs.map(j => ({
    job_id: j.job_id, job_name: j.job_name || '',
    note: (b.supplier || 'Supplier') + ' \\u2014 '
      + matLine(j.materials)
      + ' \\u00b7 received ' + (b.received || '')
      + (b.document_number ? (' \\u00b7 slip ' + b.document_number) : '')
      + '. Assigned by ' + who + ' via the console (slab-bot).'
  }))
}};
""")

if_node("Confirm Valid?", "$json.valid === true")
no_op("Drop (Bad Confirm)")

if_node("Any Jobs?", "($json.jobs || []).length > 0")

code("Fan Jobs", """\
const p = $('Parse Confirm').first().json;
return p.jobs.map(j => ({ json: j }));
""")

new("Post Moraware Note", "n8n-nodes-base.httpRequest", 4.4, {
    "method": "POST",
    "url": "={{ $('Slab Config').item.json.bridge_base_url }}/api/slabbot/add-activity",
    "sendHeaders": True,
    "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
    "sendBody": True, "specifyBody": "json",
    "jsonBody": "={{ JSON.stringify({ jobId: Number($json.job_id), note: $json.note }) }}",
    "options": {},
})

code("Collect Notes", """\
const all = $input.all();
return [{ json: { notes_posted: all.length } }];
""")

PC = "$('Parse Confirm').first().json"
hook_post("Final: Filed", "/slab/final", (
    "={\n"
    '  "review_item_id": "{{ ' + PC + '.review_item_id }}",\n'
    '  "body": {{ JSON.stringify(\'\\u2713 \' + (' + PC + '.supplier || \'Delivery\') + (' + PC + '.document_number ? (\' \' + ' + PC + '.document_number) : \'\') + \' filed: \' + (' + PC + '.summary || \'done\') + \'. \' + ((' + PC + '.jobs || []).length ? \'Moraware note\' + ((' + PC + '.jobs || []).length === 1 ? \'\' : \'s\') + \' added.\' : \'Stock \\u2014 no Moraware note needed.\')) }},\n'
    '  "status": "{{ ' + PC + ".all_stock ? 'stock' : 'confirmed' }}\",\n"
    '  "push": false\n'
    "}"
))

# ===========================================================================
# CONNECTIONS
# ===========================================================================
wire("Slab Trigger", "Slab Config")
wire("Slab Config", "Verify Secret")
wire("Verify Secret", "Trigger Context", 0)
wire("Verify Secret", "Drop (Bad Secret)", 1)
wire("Trigger Context", "Read Slip")
wire("Read Slip", "Parse Slip")
wire("Parse Slip", "Dedup Lookup")
wire("Dedup Lookup", "Dedup Decide")
wire("Dedup Decide", "Already Uploaded?")
wire("Already Uploaded?", "Final: Duplicate Slip", 0)
wire("Already Uploaded?", "Fetch Original Photo", 1)
wire("Fetch Original Photo", "Search Year")
wire("Search Year", "Year Exists?")
wire("Year Exists?", "Set Year Folder", 0)
wire("Year Exists?", "Create Year", 1)
wire("Create Year", "Set Year Folder")
wire("Set Year Folder", "Search Supplier")
wire("Search Supplier", "Supplier Exists?")
wire("Supplier Exists?", "Set Supplier Folder", 0)
wire("Supplier Exists?", "Create Supplier", 1)
wire("Create Supplier", "Set Supplier Folder")
wire("Set Supplier Folder", "Prep Binary")
wire("Prep Binary", "Upload to Drive")
wire("Upload to Drive", "Update: Slip Details")

wire("Slab Decision Webhook", "Slab Config D")
wire("Slab Config D", "Parse Confirm")
wire("Parse Confirm", "Confirm Valid?")
wire("Confirm Valid?", "Any Jobs?", 0)
wire("Confirm Valid?", "Drop (Bad Confirm)", 1)
wire("Any Jobs?", "Fan Jobs", 0)
wire("Any Jobs?", "Final: Filed", 1)
wire("Fan Jobs", "Post Moraware Note")
wire("Post Moraware Note", "Collect Notes")
wire("Collect Notes", "Final: Filed")

# ===========================================================================
# DECISION-FLOW CONFIG REFERENCES + LAYOUT + VALIDATION
# ===========================================================================
# Every decision-flow node must read Slab Config D (the ingest config never
# runs in a decision execution).
DECISION_NODES = {"Parse Confirm", "Confirm Valid?", "Drop (Bad Confirm)",
                  "Any Jobs?", "Fan Jobs", "Post Moraware Note",
                  "Collect Notes", "Final: Filed"}
for n in nodes:
    if n["name"] in DECISION_NODES:
        n["parameters"] = walk_swap(n["parameters"],
                                    [("$('Slab Config')", "$('Slab Config D')")])

from collections import deque

depth = {"Slab Trigger": 0, "Slab Decision Webhook": 0}
q = deque(["Slab Trigger", "Slab Decision Webhook"])
while q:
    cur = q.popleft()
    for branch in connections.get(cur, {}).get("main", []):
        for c in branch:
            if c["node"] not in depth:
                depth[c["node"]] = depth[cur] + 1
                q.append(c["node"])
lanes = {}
for n in nodes:
    d = depth.get(n["name"], 0)
    lane = lanes.get(d, 0)
    lanes[d] = lane + 1
    decision_side = n["name"] in {"Slab Decision Webhook", "Slab Config D", "Parse Confirm",
                                  "Confirm Valid?", "Drop (Bad Confirm)",
                                  "Any Jobs?", "Fan Jobs", "Post Moraware Note",
                                  "Collect Notes", "Final: Filed"}
    n["position"] = [d * 260, (760 if decision_side else 0) + lane * 170]

workflow = {"name": "PILOT CONSOLE SLABBOT", "nodes": nodes,
            "connections": connections, "active": False,
            "settings": {"executionOrder": "v1"}, "pinData": {}}

errors, report = [], []
names = [n["name"] for n in nodes]
name_set = set(names)
if len(name_set) != len(names):
    errors.append("duplicate names")
for frm, outs in connections.items():
    if frm not in name_set:
        errors.append(f"connection from unknown: {frm}")
    for branch in outs["main"]:
        for c in branch:
            if c["node"] not in name_set:
                errors.append(f"connection to unknown: {frm} -> {c['node']}")
blob = json.dumps(workflow)
refs = sorted(set(re.findall(r"\$\('([^']+)'\)", blob)))
dangling = [r for r in refs if r not in name_set]
if dangling:
    errors.append(f"dangling refs: {dangling}")
report.append(f"$() refs: {len(refs)}, all resolve" if not dangling else "DANGLING")
for bad, why in [("sk-ant-", "live key"), ("bot.emgcheckbot.us", "WhatsApp"),
                 ("checkbot_9k3m", "Evolution key"), ("appXXXXXXXXXXXXXX", "Airtable"),
                 (PROD_DRIVE_ROOT, "prod Drive root")]:
    if bad in blob:
        errors.append(f"forbidden: {why}")
for n in nodes:
    if n["type"] == "n8n-nodes-base.airtable":
        errors.append(f"airtable leaked: {n['name']}")
    if n["type"] == "n8n-nodes-base.httpRequest":
        url = str(n["parameters"].get("url", ""))
        if "/api/hooks" in url and "X-Pilot-Secret" not in json.dumps(
            n["parameters"].get("headerParameters", {})
        ):
            errors.append(f"hook without secret: {n['name']}")
# same-execution reference check (the bug class that stranded 'filing')
adj = {}
for frm, outs in connections.items():
    for branch in outs["main"]:
        for c in branch:
            adj.setdefault(frm, set()).add(c["node"])
def reach(start):
    seen, stack = set(), [start]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(adj.get(cur, ()))
    return seen
ingest_set = reach("Slab Trigger")
decision_set = reach("Slab Decision Webhook")
for n in nodes:
    blob_n = json.dumps(n["parameters"])
    for ref in set(re.findall(r"\$\('([^']+)'\)", blob_n)):
        in_ingest = n["name"] in ingest_set and ref in ingest_set
        in_decision = n["name"] in decision_set and ref in decision_set
        if not (in_ingest or in_decision):
            errors.append(f"cross-execution reference: {n['name']} -> {ref}")

ported = [n for n in nodes if n["name"] in src_by_name]
drift = [n["name"] for n in ported
         if n["typeVersion"] != src_by_name[n["name"]].get("typeVersion")]
if drift:
    errors.append(f"typeVersion drift: {drift}")
types = {}
for n in nodes:
    t = n["type"].split(".")[-1]
    types[t] = types.get(t, 0) + 1
report.append(f"nodes: {len(nodes)} ({len(ported)} transplanted, {len(nodes) - len(ported)} new)")
report.append("types: " + ", ".join(f"{t}={c}" for t, c in sorted(types.items())))
report.append("triggers: webhook pilot-slabbot + webhook slab-decision | active: false")

print("=== VALIDATION REPORT ===")
for line in report:
    print(" ", line)
if errors:
    print("=== ERRORS ===")
    for e in errors:
        print(" !", e)
    sys.exit(1)

TARGET.write_text(json.dumps(workflow, indent=1, ensure_ascii=False), encoding="utf-8")
print(f"\nOK -> {TARGET} ({TARGET.stat().st_size:,} bytes)")
