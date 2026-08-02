"""Assemble n8n/PILOT_CONSOLE_CHECK-BOT.json from the production export.

Reads the production EMG CHECK-BOT export (gitignored, contains a live key),
transplants the battle-tested nodes byte-faithfully (only the input
expressions are swapped), adds the console-facing nodes (trigger, hooks,
Wait-based decision loop), wires the graph, and validates the result.

Run:  python n8n/build_pilot_workflow.py
"""

import copy
import json
import re
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "EMG CHECK-BOT (21).json"
TARGET = REPO / "n8n" / "PILOT_CONSOLE_CHECK-BOT.json"

src = json.loads(SOURCE.read_text(encoding="utf-8"))
src_by_name = {n["name"]: n for n in src["nodes"]}

# The live key, read from the export at runtime so it never appears here.
_key_match = re.search(r"sk-ant-[A-Za-z0-9_\-]+", json.dumps(src))
LIVE_KEY = _key_match.group(0) if _key_match else None

CONFIG = "Pilot Config"
CTX = "Trigger Context"

# ---------------------------------------------------------------------------
# String swaps applied to every transplanted node (order matters: specific
# swaps run before generic ones).
# ---------------------------------------------------------------------------
GLOBAL_SWAPS = [
    # WhatsApp-era inputs -> console trigger context
    ("const wh=$('Receive WhatsApp Photo').item.json;",
     "const wh={body:{data:{key:{id:$('Trigger Context').item.json.review_item_id,remoteJid:'console'}}}};"),
    ("$('Get Full Image').item.json.base64",
     "$('Trigger Context').item.json.image_base64"),
    ("$('Receive WhatsApp Photo').item.json.body.data.message.imageMessage?.caption",
     "$('Trigger Context').item.json.caption"),
    ("$('Receive WhatsApp Photo').item.json.body.data.key.id",
     "$('Trigger Context').item.json.review_item_id"),
    # Reply-era inputs -> decision parser / row context
    ("$('Extract Reply Text').item.json.text",
     "$('Parse Decision').item.json.text"),
    ("($('Find Pending Check').item.json.fields) || {}",
     "$('Row Context').item.json || {}"),
    ("$('Find Pending Check').item.json.fields.InvoiceNumber",
     "$('Row Context').item.json.InvoiceNumber"),
    # QuickBooks company URL -> config (code node first, then generic URLs)
    ("'https://quickbooks.api.intuit.com/v3/company/000000000000000/query?query='",
     "($('Pilot Config').first().json.qb_company_url + '/query?query=')"),
    ("https://quickbooks.api.intuit.com/v3/company/000000000000000",
     "{{ $('Pilot Config').item.json.qb_company_url }}"),
    # Bridge base URL -> config
    ("https://bridge.emgcheckbot.us",
     "{{ $('Pilot Config').item.json.bridge_base_url }}"),
    # Drive folder -> config placeholder
    ("1cVZyZjc4cITdS240gkTv9hlh1MuSrbXh",
     "={{ $('Pilot Config').item.json.drive_folder_id }}"),
    # Cosmetic: the register is the console now
    ("Saved to Airtable", "Recorded in the console"),
    ("recorded in Airtable and noted on the job",
     "recorded in the console and noted on the job"),
]
if LIVE_KEY:
    GLOBAL_SWAPS.insert(0, (LIVE_KEY, "={{ $('Pilot Config').item.json.anthropic_api_key }}"))

# Per-node extra swaps.
NODE_SWAPS = {
    # OWNER RULE (2026-07-20): QuickBooks analysis WINS over the caption/memo
    # hint — a 75%-qty invoice is a deposit no matter what the memo says.
    "Classify Payment": [
        ("// ----- caption hint wins; QB fills in / flags disagreement -----\n"
         "const hint = (captionHint && captionHint !== 'unknown') ? canon(captionHint) : null;\n"
         "let paymentType;\n"
         "if (hint && hint !== 'unknown') {\n"
         "  paymentType = hint;\n"
         "  if (qbType && qbType !== hint) flags.push(\"caption hint '\" + hint + \"' disagrees with QuickBooks analysis ('\" + qbType + \"')\");\n"
         "} else {\n"
         "  paymentType = qbType ? canon(qbType) : 'unknown';\n"
         "}",
         "// ----- QuickBooks is the truth; the caption hint only fills gaps -----\n"
         "const hint = (captionHint && captionHint !== 'unknown') ? canon(captionHint) : null;\n"
         "let paymentType;\n"
         "if (qbType) {\n"
         "  paymentType = canon(qbType);\n"
         "  if (hint && hint !== 'unknown' && hint !== qbType) flags.push(\"the check/caption said '\" + hint + \"' but QuickBooks shows a '\" + qbType + \"' - using QuickBooks\");\n"
         "} else if (hint && hint !== 'unknown') {\n"
         "  paymentType = hint;\n"
         "} else {\n"
         "  paymentType = 'unknown';\n"
         "}"),
        ("source: (hint && hint !== 'unknown') ? 'caption' : 'quickbooks',",
         "source: qbType ? 'quickbooks' : 'caption',"),
    ],
    "AC Build Row": [
        ("const ptype=(hintType!=='unknown')?hintType:(qbType||'unknown');",
         "const ptype=qbType||((hintType!=='unknown')?hintType:'unknown');"),
    ],
    # The OCR node's input item used to be Get Full Image's output.
    "Read Check with Claude": [
        ("{{ $json.base64 }}", "{{ $('Trigger Context').item.json.image_base64 }}"),
    ],
    "Cash Has Data?": [
        ("Number($json.cash_amount)", "Number($('Cash Data').item.json.cash_amount)"),
        ("String($json.cash_invoice||'')", "String($('Cash Data').item.json.cash_invoice||'')"),
    ],
    "Cash Job By Invoice": [
        ("$('Parse Check Data').item.json.cash_invoice",
         "$('Cash Data').item.json.cash_invoice"),
    ],
    "Cash Build Row": [
        ("const p=$('Parse Check Data').item.json;",
         "const p=Object.assign({},$('Parse Check Data').item.json,$('Cash Data').item.json);"),
    ],
    "Prepare Photo File": [
        # Job name for the filename, whichever outcome path archived the photo.
        ("const pick = $('Pick Best Match').item.json;",
         "let jobNameR='';"
         "try{jobNameR=$('Select Job').item.json.job_name||'';}catch(e){}"
         "if(!jobNameR){try{jobNameR=$('AC Build Row').item.json.JobName||'';}catch(e){}}"
         "if(!jobNameR){try{jobNameR=$('Cash Build Row').item.json.JobName||'';}catch(e){}}"
         "const pick = { job_name: jobNameR };"),
    ],
    "Get Invoice Number": [],
}


def walk_swap(value, swaps):
    if isinstance(value, dict):
        return {k: walk_swap(v, swaps) for k, v in value.items()}
    if isinstance(value, list):
        return [walk_swap(v, swaps) for v in value]
    if isinstance(value, str):
        for old, new in swaps:
            value = value.replace(old, new)
        return value
    return value


nodes = []
connections = {}
_used_names = set()


def _nid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "pilot-checkbot/" + name))


def add(node):
    if node["name"] in _used_names:
        raise SystemExit(f"duplicate node name: {node['name']}")
    _used_names.add(node["name"])
    nodes.append(node)
    return node["name"]


def port(name, new_name=None, extra_swaps=()):
    """Transplant a node from the source export, expressions swapped."""
    orig = src_by_name[name]
    params = copy.deepcopy(orig.get("parameters", {}))
    params = walk_swap(params, list(extra_swaps) + NODE_SWAPS.get(name, []) + GLOBAL_SWAPS)
    # A URL that gained a {{ }} must become an expression.
    if isinstance(params.get("url"), str) and "{{" in params["url"] and not params["url"].startswith("="):
        params["url"] = "=" + params["url"]
    node = {
        "id": _nid(new_name or name),
        "name": new_name or name,
        "type": orig["type"],
        "typeVersion": orig.get("typeVersion", 1),
        "position": [0, 0],
        "parameters": params,
    }
    # QB nodes keep their credential TYPE (user attaches on import); the
    # concrete credential ids from prod are stripped.
    return add(node)


def new(name, type_, type_version, params, webhook_id=False):
    node = {
        "id": _nid(name),
        "name": name,
        "type": type_,
        "typeVersion": type_version,
        "position": [0, 0],
        "parameters": params,
    }
    if webhook_id:
        node["webhookId"] = _nid(name + "/webhook")
    return add(node)


def wire(frm, to, output=0):
    """Connect frm's output N to to's input 0."""
    outs = connections.setdefault(frm, {"main": []})["main"]
    while len(outs) <= output:
        outs.append([])
    outs[output].append({"node": to, "type": "main", "index": 0})


def if_node(name, left_expr):
    return new(name, "n8n-nodes-base.if", 2.2, {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "",
                        "typeValidation": "loose", "version": 3},
            "conditions": [{
                "id": _nid(name + "/cond")[:8],
                "leftValue": "={{ " + left_expr + " }}",
                "rightValue": "={{ true }}",
                "operator": {"type": "boolean", "operation": "true", "singleValue": True},
            }],
            "combinator": "and",
        },
        "options": {},
    })


SECRET_HEADER = {"name": "X-Pilot-Secret",
                 "value": "={{ $('Pilot Config').item.json.pilot_hook_secret }}"}
HOOK_BASE = "{{ $('Pilot Config').item.json.console_base_url }}/api/hooks/pilot"
RIID = "$('Trigger Context').item.json.review_item_id"


def hook_post(name, path, json_body):
    return new(name, "n8n-nodes-base.httpRequest", 4.4, {
        "method": "POST",
        "url": "=" + HOOK_BASE + path,
        "sendHeaders": True,
        "headerParameters": {"parameters": [dict(SECRET_HEADER),
                                            {"name": "Content-Type", "value": "application/json"}]},
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": json_body,
        "options": {},
    })


def hook_get_find(name, query_expr):
    return new(name, "n8n-nodes-base.httpRequest", 4.4, {
        "method": "GET",
        "url": "=" + HOOK_BASE + "/find?" + query_expr,
        "sendHeaders": True,
        "headerParameters": {"parameters": [dict(SECRET_HEADER)]},
        "options": {},
    })


def _push_json(push):
    """true/false literal, or an n8n expression producing a boolean."""
    if push is True:
        return "true"
    if push is False:
        return "false"
    return "{{ " + push + " }}"


def ask(name, body_expr, candidates_expr="[]", allowed_freeform=True,
        format_hint="null", push=True):
    """A question hook call: candidates + resume_url for the Wait node.
    ONE push per submission: only the FIRST question pushes; retries are
    silent (the answering manager is already on the card)."""
    # {{ null }} interpolates to EMPTY TEXT in n8n and breaks the JSON body —
    # a literal null must be emitted without expression braces.
    hint = "null" if format_hint == "null" else "{{ " + format_hint + " }}"
    return hook_post(name, "/question", (
        "={\n"
        '  "review_item_id": "{{ ' + RIID + ' }}",\n'
        '  "body": {{ ' + body_expr + ' }},\n'
        '  "candidates": {{ ' + candidates_expr + ' }},\n'
        '  "resume_url": "{{ $execution.resumeUrl }}",\n'
        '  "allowed_freeform": ' + ("true" if allowed_freeform else "false") + ',\n'
        '  "format_hint": ' + hint + ',\n'
        '  "push": ' + _push_json(push) + '\n'
        "}"
    ))


def update(name, body_expr, fields_expr=None):
    parts = ['  "review_item_id": "{{ ' + RIID + ' }}"',
             '  "body": {{ ' + body_expr + ' }}']
    if fields_expr:
        parts.append('  "fields": {{ ' + fields_expr + ' }}')
    return hook_post(name, "/update", "={\n" + ",\n".join(parts) + "\n}")


def final(name, body_expr, status, push=True):
    return hook_post(name, "/final", (
        "={\n"
        '  "review_item_id": "{{ ' + RIID + ' }}",\n'
        '  "body": {{ ' + body_expr + ' }},\n'
        '  "status": "' + status + '",\n'
        '  "push": ' + _push_json(push) + '\n'
        "}"
    ))


def wait_node(name):
    return new(name, "n8n-nodes-base.wait", 1.1,
               {"resume": "webhook", "httpMethod": "POST", "options": {}},
               webhook_id=True)


def code(name, js):
    return new(name, "n8n-nodes-base.code", 2, {"jsCode": js})


def no_op(name):
    return new(name, "n8n-nodes-base.noOp", 1, {})


# ===========================================================================
# ENTRY
# ===========================================================================
new("Console Trigger", "n8n-nodes-base.webhook", 2.1,
    {"httpMethod": "POST", "path": "pilot-checkbot", "options": {}},
    webhook_id=True)

new(CONFIG, "n8n-nodes-base.set", 3.4, {
    "assignments": {"assignments": [
        {"id": "a1", "name": "console_base_url", "type": "string",
         "value": "PASTE_CONSOLE_BASE_URL"},
        {"id": "a2", "name": "bridge_base_url", "type": "string",
         "value": "https://bridge.emgcheckbot.us"},
        {"id": "a3", "name": "pilot_hook_secret", "type": "string",
         "value": "PASTE_PILOT_HOOK_SECRET"},
        {"id": "a4", "name": "anthropic_api_key", "type": "string",
         "value": "PASTE_ANTHROPIC_KEY"},
        {"id": "a5", "name": "qb_verification_enabled", "type": "boolean",
         "value": True},
        # Owner's QuickBooks SANDBOX company (realm 9341457257917923) — never
        # a production realm here.
        {"id": "a6", "name": "qb_company_url", "type": "string",
         "value": "https://sandbox-quickbooks.api.intuit.com/v3/company/9341457257917923"},
        {"id": "a7", "name": "drive_archive_enabled", "type": "boolean",
         "value": True},
        # Owner's temporary test-checks folder (drive.google.com/drive/folders/…)
        {"id": "a8", "name": "drive_folder_id", "type": "string",
         "value": "1x26XpqkIS6qKJNh4OLyeC-a05uyF8k0G"},
        # Job page URL prefix — a tapped candidate's decision carries only
        # {label, job_id}, so the link is reconstructed from the id.
        {"id": "a9", "name": "moraware_job_base", "type": "string",
         "value": "https://granite-marble-tops.moraware.net/sys/job/"},
    ]},
    "includeOtherFields": True,
    "options": {},
})

if_node("Verify Secret",
        "$('Console Trigger').item.json.body.secret === $json.pilot_hook_secret")
no_op("Drop (Bad Secret)")

code(CTX, """\
// Console outbound payload (backend outbound.py):
// { review_item_id, image_base64, qb_invoice, submitted_by, secret }
const b = $('Console Trigger').item.json.body || {};
const qb = String(b.qb_invoice == null ? '' : b.qb_invoice).trim();
const submitted = String(b.submitted_by || '').trim();
const local = submitted.split('@')[0] || '';
return { json: {
  review_item_id: String(b.review_item_id || ''),
  image_base64: String(b.image_base64 || ''),
  qb_invoice: qb || null,
  submitted_by: submitted || null,
  submitted_by_name: local ? (local.charAt(0).toUpperCase() + local.slice(1)) : 'a manager',
  // Synthesized caption: keeps the transplanted OCR prompt + parser working
  // exactly as in production (caption_invoice, cash_invoice fall out of it).
  caption: qb ? ('invoice ' + qb) : ''
}};
""")

# ===========================================================================
# OCR CORE (transplants)
# ===========================================================================
port("Read Check with Claude")
port("Parse Check Data")
if_node("Readable?", "$json.readable === true")

PARSE = "$('Parse Check Data').item.json"
update(
    "Update: Read Result",
    "JSON.stringify('Read the ' + (" + PARSE + ".document_type === 'cash' ? 'photo (cash)' : 'check') + ': ' + (" + PARSE + ".amount != null ? ('$' + " + PARSE + ".amount) : 'amount unreadable') + (" + PARSE + ".check_number ? (', #' + " + PARSE + ".check_number) : '') + (" + PARSE + ".payer_name ? (', ' + " + PARSE + ".payer_name) : '') + '.' + (" + PARSE + ".confidence_flags ? (' \\u26a0 ' + " + PARSE + ".confidence_flags) : ''))",
    "JSON.stringify(Object.fromEntries(Object.entries({amount: " + PARSE + ".amount, check_number: " + PARSE + ".check_number, payer_name: " + PARSE + ".payer_name, caption_name: " + PARSE + ".caption_name}).filter(function(e){return e[1] !== null && e[1] !== undefined && e[1] !== '';})))",
)

ask("Ask: Unreadable",
    "JSON.stringify(\"I couldn't read a usable name or address off this photo. \" + 'Type the customer/job name, or the 4-digit QB invoice #.')",
    "[]", True, "JSON.stringify('job name or 4-digit invoice #')")

# ===========================================================================
# CASH BRANCH (transplants + console asks)
# ===========================================================================
CASH_FIRST = "$('Cash Data').item.json._asked ? false : true"
port("Is Cash Photo?")

code("Cash Data", """\
// Merge OCR-caption cash data with anything the manager typed in an answer.
const p = $('Parse Check Data').item.json;
let extra = {};
try { extra = $('Parse Cash Info').item.json || {}; } catch (e) { extra = {}; }
return { json: {
  cash_amount: (extra.cash_amount != null ? extra.cash_amount : p.cash_amount),
  cash_invoice: (extra.cash_invoice != null ? extra.cash_invoice : p.cash_invoice),
  _asked: Object.keys(extra).length > 0
}};
""")

port("Cash Has Data?")
ask("Ask: Cash Info",
    "JSON.stringify('\\ud83d\\udcb5 That looks like cash. I need the amount and the 4-digit QB invoice # to record it \\u2014 type both.')",
    "[]", True, "JSON.stringify('amount and invoice, e.g. 500 1042')",
    push=CASH_FIRST)

CASHD = "$('Cash Data').item.json"
ask("Ask: Cash Invoice Not Found",
    "JSON.stringify('\\ud83d\\udcb5 I couldn\\u2019t find invoice ' + (" + CASHD + ".cash_invoice || '?') + ' in Moraware \\u2014 double-check the number and type it again (amount + invoice).')",
    "[]", True, "JSON.stringify('amount and invoice, e.g. 500 1042')",
    push=CASH_FIRST)

wait_node("Wait: Cash")

code("Parse Cash Info", """\
// Resume payload from the console decision endpoint: {secret, text|choice}.
const b = $json.body || {};
const ok = String(b.secret || '') === $('Pilot Config').first().json.pilot_hook_secret;
const t = ok ? String(b.text != null ? b.text : (b.choice && b.choice.label) || '') : '';
const invList = t.match(/\\b(\\d{3,6})\\b/g) || [];
const inv = invList.length ? invList[invList.length - 1] : null;
let amt = null;
const nums = (t.match(/\\d+(?:\\.\\d{1,2})?/g) || []).map(Number);
for (const n of nums) { if (String(n) !== String(inv)) { amt = n; break; } }
return { json: { cash_amount: amt, cash_invoice: inv } };
""")

port("Cash Job By Invoice")
port("Cash Job Found?")

hook_get_find("Cash Find Duplicates",
              "invoice_number={{ encodeURIComponent(" + CASHD + ".cash_invoice || '') }}&amount={{ encodeURIComponent(" + CASHD + ".cash_amount || '') }}")
if_node("Cash Duplicate?",
        "(($json.items || []).filter(function(i){ return i.review_item_id !== " + RIID + "; })).length > 0")
CASH_DUP_CARD = ("$('Pilot Config').item.json.console_base_url + '/payments/item/' + "
                 "((($('Cash Find Duplicates').item.json.items || []).filter(function(i){ return i.review_item_id !== " + RIID + "; })[0] || {}).review_item_id || '')")
final("Final: Cash Already Recorded",
      "JSON.stringify('\\u2139 Cash for invoice #' + (" + CASHD + ".cash_invoice || '?') + ' was already recorded \\u2014 see the finished card: ' + (" + CASH_DUP_CARD + ") + '. Nothing recorded twice.')",
      "duplicate", push=CASH_FIRST)

port("Cash Build Row")
CASHROW = "$('Cash Build Row').item.json"
update("Update: Cash Details",
       "JSON.stringify('Cash matched to ' + (" + CASHROW + ".JobName || 'job') + ' by invoice #' + (" + CASHROW + ".InvoiceNumber || '?') + '.')",
       "JSON.stringify(Object.fromEntries(Object.entries({amount: " + CASHROW + ".Amount, payer_name: " + CASHROW + ".PayerName, payment_method: 'cash', invoice_number: " + CASHROW + ".InvoiceNumber, matched_job_id: String(" + CASHROW + ".JobId || ''), matched_job_name: " + CASHROW + ".JobName, moraware_url: " + CASHROW + ".MorawareURL}).filter(function(e){return e[1] !== null && e[1] !== undefined && e[1] !== '';})))")
port("Cash Post Note")
final("Final: Cash Recorded",
      "JSON.stringify(" + CASHROW + ".messageText)", "confirmed",
      push=CASH_FIRST)

# ===========================================================================
# FAST PATH (invoice on the item / in the caption)
# ===========================================================================
port("Photo Has Invoice?")
port("Photo Job By Invoice")
port("Photo Job Found?")

hook_get_find("AC Find Duplicates",
              "check_number={{ encodeURIComponent(" + PARSE + ".check_number || '') }}&invoice_number={{ encodeURIComponent(" + PARSE + ".caption_invoice || '') }}")
if_node("Fast Path Duplicate?",
        "(($json.items || []).filter(function(i){ return i.review_item_id !== " + RIID + "; })).length > 0")

PHOTOJOB = "$('Photo Job By Invoice').item.json"
AC_DUP_CARD = ("$('Pilot Config').item.json.console_base_url + '/payments/item/' + "
               "((($('AC Find Duplicates').item.json.items || []).filter(function(i){ return i.review_item_id !== " + RIID + "; })[0] || {}).review_item_id || '')")
ask("Ask: Duplicate (Fast Path)",
    "JSON.stringify('\\u26a0 This check was already uploaded \\u2014 check #' + (" + PARSE + ".check_number || '?') + ' for invoice #' + (" + PARSE + ".caption_invoice || '?') + ' is on an earlier card. See the finished card: ' + (" + AC_DUP_CARD + ") + ' \\u2014 record this one anyway?')",
    "JSON.stringify([{label: 'Record anyway: ' + (" + PHOTOJOB + ".CustomerName || 'this job'), sublabel: 'It is a different payment', job_id: String(" + PHOTOJOB + ".JobId), moraware_url: " + PHOTOJOB + ".LeadUrl || null}, {label: 'Ignore \\u2014 duplicate', sublabel: 'Nothing will be recorded', job_id: null, moraware_url: null}])",
    False, "null")

if_node("QB Gate (Fast Path)", "$('Pilot Config').item.json.qb_verification_enabled === true")
port("AC Get QB Invoice")
port("AC Build Payment Query")
port("AC Get QB Payments")
port("AC Build Row")
ACROW = "$('AC Build Row').item.json"
update("Update: Fast Path Details",
       "JSON.stringify('Matched to ' + (" + ACROW + ".JobName || 'job') + ' by invoice #' + (" + ACROW + ".InvoiceNumber || '?') + '.')",
       "JSON.stringify(Object.fromEntries(Object.entries({amount: " + ACROW + ".Amount, check_number: " + ACROW + ".CheckNumber, payer_name: " + ACROW + ".PayerName, caption_name: " + ACROW + ".CaptionName, invoice_number: " + ACROW + ".InvoiceNumber, payment_type: (" + ACROW + ".PaymentType !== 'unknown' ? " + ACROW + ".PaymentType : null), matched_job_id: String(" + ACROW + ".JobId || ''), matched_job_name: " + ACROW + ".JobName, moraware_url: " + ACROW + ".MorawareURL}).filter(function(e){return e[1] !== null && e[1] !== undefined && e[1] !== '';})))")
port("AC Post Note")
final("Final: Fast Path Recorded",
      "JSON.stringify(" + ACROW + ".messageText)", "confirmed")

# ===========================================================================
# MATCHING BRAIN (transplants, QB reads gated)
# ===========================================================================
port("Smart Search")
port("AV Seed")
port("AV Fallback Find")
port("AV Merge Fanout")
if_node("QB Gate (AV)", "$('Pilot Config').first().json.qb_verification_enabled === true")
port("AV Get Job Invoices")
port("AV Build QB Query")
port("AV Get QB Amounts")
port("AV Collector")
if_node("QB Gate (Vet)", "$('Pilot Config').first().json.qb_verification_enabled === true")
port("Vet Prep")
port("Vet Has Candidate?")
port("Vet Get Invoice")
port("Vet QB Invoice")
port("Vet Decide")
port("Vet Skip?")
port("Rescue Find Jobs")
port("Rescue Prep")
port("Rescue Has Candidate?")
port("Rescue Get Invoice")
port("Rescue QB Invoice")
port("Rescue Decide")
port("Rescue None")
port("Pick Best Match")
port("Match Result")

# --- outcomes ---
hook_get_find("Find Duplicates",
              "check_number={{ encodeURIComponent(" + PARSE + ".check_number || '') }}&amount={{ encodeURIComponent(" + PARSE + ".amount || '') }}")
if_node("Duplicate?",
        "(($json.items || []).filter(function(i){ return i.review_item_id !== " + RIID + "; })).length > 0")

PICK = "$('Pick Best Match').item.json"
DUP_CARD = ("$('Pilot Config').item.json.console_base_url + '/payments/item/' + "
            "((($('Find Duplicates').item.json.items || []).filter(function(i){ return i.review_item_id !== " + RIID + "; })[0] || {}).review_item_id || '')")
ask("Ask: Duplicate (Match)",
    "JSON.stringify('\\u26a0 This check was already uploaded \\u2014 check #' + (" + PARSE + ".check_number || '?') + ' for $' + (" + PARSE + ".amount || '?') + ' is on an earlier card. See the finished card: ' + (" + DUP_CARD + ") + ' \\u2014 record this one anyway?')",
    "JSON.stringify([{label: 'Record anyway: ' + (" + PICK + ".job_name || 'this job'), sublabel: 'It is a different payment', job_id: String(" + PICK + ".job_id), moraware_url: " + PICK + ".lead_url || null}, {label: 'Ignore \\u2014 duplicate', sublabel: 'Nothing will be recorded', job_id: null, moraware_url: null}])",
    False, "null")

port("Get Invoice Number")
INVN = "$('Get Invoice Number').item.json"
ask("Ask: Confirm Match",
    "JSON.stringify('Read check #' + (" + PARSE + ".check_number || '?') + ' from ' + (" + PARSE + ".payer_name || 'unknown') + ' for $' + (" + PARSE + ".amount || '?') + '.' + ((" + PARSE + ".amount_verified && " + PARSE + ".check_number_verified) ? '' : ' \\u26a0 Double-check the amount and check number.') + (" + PICK + ".skipped_note ? (' (' + " + PICK + ".skipped_note + ')') : '') + ' Is this the right job?')",
    "JSON.stringify([{label: " + PICK + ".job_name || 'Matched job', sublabel: ((" + PICK + "._amountInvoice || " + INVN + ".InvoiceNumber) ? ('Invoice #' + (" + PICK + "._amountInvoice || " + INVN + ".InvoiceNumber) + ({full: ' \\u2014 pays in full', remainder: ' \\u2014 equals remaining balance', deposit: ' \\u2014 the 75% deposit'}[" + PICK + "._amountKind] || '')) : 'No invoice in Moraware yet'), job_id: String(" + PICK + ".job_id), moraware_url: " + PICK + ".lead_url || null}])",
    True, "JSON.stringify('correct job name or 4-digit invoice #')")

port("Build Address Options")
ask("Ask: Candidates",
    "JSON.stringify('Read check #' + (" + PARSE + ".check_number || '?') + ' from ' + (" + PARSE + ".payer_name || 'unknown') + ' for $' + (" + PARSE + ".amount || '?') + '. ' + $('Build Address Options').item.json.header_text)",
    "JSON.stringify((" + PICK + ".all_matches || []).slice(0, 4).map(function(m){ return {label: m.CustomerName || ('Job ' + m.JobId), sublabel: [(m.AddressLine2 ? ('Unit ' + m.AddressLine2) : null), (m._hitInvoice ? ('Invoice #' + m._hitInvoice + (m._hitKind ? (' (' + m._hitKind + ')') : '')) : null)].filter(Boolean).join(' \\u00b7 ') || null, job_id: String(m.JobId), moraware_url: m.LeadUrl || null}; }))",
    True, "JSON.stringify('correct job name or 4-digit invoice #')")

ask("Ask: Not Found",
    "JSON.stringify('Read check #' + (" + PARSE + ".check_number || '?') + ' from ' + (" + PARSE + ".payer_name || 'unknown') + ' \\u2014 $' + (" + PARSE + ".amount || '?') + '. \\u26a0 No Moraware job matched name \\u201c' + (" + PARSE + ".last_name || '(none)') + '\\u201d or address \\u201c' + [(" + PARSE + ".street_number || ''), (" + PARSE + ".street_name || '')].filter(Boolean).join(' ') + '\\u201d. Type the correct job name or a 4-digit invoice #.')",
    "[]", True, "JSON.stringify('job name or 4-digit invoice #')")

ask("Ask: Still Not Found",
    "JSON.stringify('Still couldn\\u2019t find a job for \\u201c' + ($('Parse Decision').item.json.text || '?') + '\\u201d. Type the EXACT Moraware job name, a 4-digit invoice #, or paste the Moraware lead URL.')",
    "[]", True, "JSON.stringify('exact job name / invoice # / lead URL')",
    push=False)

# ===========================================================================
# DECISION LOOP (Wait resume -> parse -> route)
# ===========================================================================
wait_node("Wait: Decision")

code("Parse Decision", """\
// Resume payload from the console decision endpoint (decisions.py):
// {secret, choice: {label, job_id}} or {secret, text}.
const b = $json.body || {};
const ok = String(b.secret || '') === $('Pilot Config').first().json.pilot_hook_secret;
const choice = b.choice || null;
const text = (b.text != null) ? String(b.text).trim() : '';
const label = choice ? String(choice.label || '') : '';
const jobId = (choice && choice.job_id != null && String(choice.job_id).trim() !== '')
  ? String(choice.job_id).trim() : null;
const invoiceText = (!choice && /^#?\\s*\\d{3,6}\\s*$/.test(text))
  ? text.replace(/[^0-9]/g, '') : null;
return { json: {
  valid: ok,
  kind: choice ? 'choice' : 'text',
  label: label,
  job_id: jobId,
  is_ignore: !!choice && !jobId && /^ignore/i.test(label),
  text: text,
  invoice_text: invoiceText
}};
""")

if_node("Decision Valid?", "$json.valid === true")
no_op("Drop (Bad Resume)")

new("Decision Router", "n8n-nodes-base.switch", 3.4, {
    "rules": {"values": [
        {"conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                    "typeValidation": "loose", "version": 3},
                        "conditions": [{"id": "dr1", "leftValue": "={{ $json.is_ignore }}",
                                        "rightValue": "={{ true }}",
                                        "operator": {"type": "boolean", "operation": "true",
                                                     "singleValue": True}}],
                        "combinator": "and"},
         "renameOutput": True, "outputKey": "IgnoreDuplicate"},
        {"conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                    "typeValidation": "loose", "version": 3},
                        "conditions": [{"id": "dr2", "leftValue": "={{ $json.kind === 'choice' && $json.job_id !== null }}",
                                        "rightValue": "={{ true }}",
                                        "operator": {"type": "boolean", "operation": "true",
                                                     "singleValue": True}}],
                        "combinator": "and"},
         "renameOutput": True, "outputKey": "ChoseJob"},
        {"conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                    "typeValidation": "loose", "version": 3},
                        "conditions": [{"id": "dr3", "leftValue": "={{ $json.invoice_text || '' }}",
                                        "rightValue": "",
                                        "operator": {"type": "string", "operation": "notEmpty",
                                                     "singleValue": True}}],
                        "combinator": "and"},
         "renameOutput": True, "outputKey": "InvoiceText"},
    ]},
    "options": {"fallbackOutput": "extra"},
})

final("Final: Ignored Duplicate",
      "JSON.stringify('Marked as duplicate by ' + ($('Trigger Context').item.json.submitted_by_name) + ' \\u2014 nothing was recorded.')",
      "duplicate", push=False)

new("Text Job By Invoice", "n8n-nodes-base.httpRequest", 4.4, {
    "method": "POST",
    "url": "={{ $('Pilot Config').item.json.bridge_base_url }}/api/checkbot/job-by-invoice",
    "sendHeaders": True,
    "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
    "sendBody": True,
    "specifyBody": "json",
    "jsonBody": "={\n  \"invoiceNumber\": {{ JSON.stringify($('Parse Decision').item.json.invoice_text || '') }}\n}",
    "options": {},
})
if_node("Text Job Found?", "$json.Found === true")

port("Parse Correction")
if_node("Has Moraware Link?", "$json.has_link === true")
port("Search Corrected Job")
port("Pick Corrected Match")
port("Corrected Found?")

code("Select Job", """\
// Normalize the chosen job, whichever route it came from.
const d = $('Parse Decision').item.json;
let job_id = null, job_name = '', lead_url = '';
if (d.kind === 'choice' && d.job_id) {
  job_id = d.job_id;
  job_name = d.label.replace(/^record anyway:\\s*/i, '');
} else {
  let srcJob = null;
  try { const t = $('Text Job By Invoice').item.json;
        if (t && t.Found === true) srcJob = { id: t.JobId, name: t.CustomerName, url: t.LeadUrl }; } catch (e) {}
  if (!srcJob) { try { const pc = $('Parse Correction').item.json;
        if (pc && pc.has_link) srcJob = { id: pc.job_id_from_link, name: 'job #' + pc.job_id_from_link, url: '' }; } catch (e) {} }
  if (!srcJob) { try { const c = $('Pick Corrected Match').item.json;
        if (c && c.found) srcJob = { id: c.job_id, name: c.job_name, url: c.lead_url }; } catch (e) {} }
  if (srcJob) { job_id = String(srcJob.id); job_name = srcJob.name || ''; lead_url = srcJob.url || ''; }
}
// A tapped button's decision has no URL — rebuild it from the job id.
if (!lead_url && job_id && /^\\d+$/.test(job_id)) {
  lead_url = $('Pilot Config').first().json.moraware_job_base + job_id;
}
return { json: { job_id: job_id, job_name: job_name, lead_url: lead_url } };
""")

new("Get Chosen Invoice", "n8n-nodes-base.httpRequest", 4.4, {
    "method": "POST",
    "url": "={{ $('Pilot Config').item.json.bridge_base_url }}/api/checkbot/invoice-by-job",
    "sendHeaders": True,
    "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
    "sendBody": True,
    "specifyBody": "json",
    "jsonBody": "={\n  \"jobId\": {{ $('Select Job').item.json.job_id }}\n}",
    "options": {},
})

code("Row Context", """\
// The row shape Classify Payment / the note builders expect.
const p = $('Parse Check Data').item.json;
const sel = $('Select Job').item.json;
// Invoice for QB verification: Moraware's answer first, then the invoice the
// manager typed at submit, then the one OCR'd off the check/caption.
let inv = '';
try { inv = String($('Get Chosen Invoice').item.json.InvoiceNumber || ''); } catch (e) { inv = ''; }
if (!inv) inv = String($('Trigger Context').item.json.qb_invoice || '');
if (!inv) inv = String(p.caption_invoice || '');
return { json: {
  Amount: p.amount, CheckNumber: p.check_number || '', InvoiceNumber: inv,
  JobId: sel.job_id, JobName: sel.job_name, MatchMethod: 'console',
  MorawareURL: sel.lead_url || '', PayerName: p.payer_name || p.display_name || '',
  PaymentType: p.payment_hint || 'unknown', QBPaymentId: ''
}};
""")

ROW = "$('Row Context').item.json"
update("Update: Confirmed",
       "JSON.stringify('Job set: ' + (" + ROW + ".JobName || ('job #' + " + ROW + ".JobId)) + (" + ROW + ".InvoiceNumber ? (' \\u00b7 invoice #' + " + ROW + ".InvoiceNumber) : ' \\u00b7 no invoice in Moraware yet') + '.')",
       "JSON.stringify(Object.fromEntries(Object.entries({amount: " + ROW + ".Amount, check_number: " + ROW + ".CheckNumber, payer_name: " + ROW + ".PayerName, invoice_number: " + ROW + ".InvoiceNumber, payment_type: (" + ROW + ".PaymentType !== 'unknown' ? " + ROW + ".PaymentType : null), matched_job_id: String(" + ROW + ".JobId || ''), matched_job_name: " + ROW + ".JobName, moraware_url: " + ROW + ".MorawareURL}).filter(function(e){return e[1] !== null && e[1] !== undefined && e[1] !== '';})))")

if_node("QB Gate (Confirm)", "$('Pilot Config').item.json.qb_verification_enabled === true")
port("Get QB Invoice")
port("Build Payment Query")
port("Get QB Payments")
port("Classify Payment")

CLS = "$('Classify Payment').item.json"
update("Update: Classified",
       "JSON.stringify('Payment type: ' + " + CLS + ".typeLabel + (" + CLS + ".paymentContext ? (' \\u2014 ' + " + CLS + ".paymentContext) : '') + '. ' + String(" + CLS + ".qbBalanceBlock || '').split('\\n')[0])",
       "JSON.stringify(Object.fromEntries(Object.entries({payment_type: (" + CLS + ".paymentType !== 'unknown' ? " + CLS + ".paymentType : null)}).filter(function(e){return e[1] !== null && e[1] !== undefined && e[1] !== '';})))")

port("Build Payment Note")

code("Simple Note", """\
// No-QuickBooks variant of Build Payment Note (same output shape).
const r = $('Row Context').item.json;
function fmt(v){const n=parseFloat(String(v==null?'':v).replace(/[^0-9.\\-]/g,''));if(isNaN(n))return '?';let q=(Math.round(n*100)/100).toFixed(2).split('.');q[0]=q[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g,',');return q[0]+'.'+q[1];}
const amtStr = '$' + fmt(r.Amount);
const typeLabel = r.PaymentType === 'PIF' ? 'paid in full' : (r.PaymentType === 'unknown' ? 'payment' : r.PaymentType);
const notes = 'Payment received \\u2014 ' + (r.PayerName || 'customer') + ' paid ' + amtStr + ' (' + typeLabel + ')' + (r.CheckNumber ? (' \\u2014 check #' + r.CheckNumber) : '') + '. Logged automatically by check-bot (console pilot).';
return { json: { jobId: r.JobId, notes: notes } };
""")

port("Create Moraware Activity")

code("Build Final Message", """\
// The resolution line: becomes the final-hook body AND the push title.
const r = $('Row Context').item.json;
const mor = $json;
const morOk = !!(mor && (mor.ActivityId || mor.Success === true || mor.Success === 'true'));
let c = null; try { c = $('Classify Payment').item.json; } catch (e) { c = null; }
function fmt(v){const n=parseFloat(String(v==null?'':v).replace(/[^0-9.\\-]/g,''));if(isNaN(n))return '?';let q=(Math.round(n*100)/100).toFixed(2).split('.');q[0]=q[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g,',');return q[0]+'.'+q[1];}
const amtStr = c ? c.amountStr : ('$' + fmt(r.Amount));
const typeLabel = c ? c.typeLabel : (r.PaymentType === 'PIF' ? 'paid in full' : (r.PaymentType === 'unknown' ? 'payment' : r.PaymentType));
const inv = (c && c.invoiceNum && c.invoiceNum !== 'pending') ? c.invoiceNum : (r.InvoiceNumber || '');
const L = [];
L.push('\\u2713 ' + amtStr + ' \\u2192 ' + (r.JobName || 'job') + ' \\u2014 ' + typeLabel);
if (r.CheckNumber) L.push('Check #' + r.CheckNumber + (inv ? (' \\u00b7 Invoice #' + inv) : ' \\u00b7 Invoice pending'));
if (c && c.qbBalanceBlock) L.push(String(c.qbBalanceBlock));
L.push(morOk ? 'Note posted to the Moraware job file.' : '\\u26a0 Moraware note FAILED \\u2014 add it to the job manually.');
if (c && c.classifierFlags) L.push('\\u26a0 ' + c.classifierFlags);
if (r.MorawareURL) L.push(r.MorawareURL);
return { json: { message: L.join('\\n') } };
""")

final("Final: Confirmed",
      "JSON.stringify($('Build Final Message').item.json.message)", "confirmed",
      push=False)

# ===========================================================================
# DRIVE ARCHIVE (flagged off by default)
# ===========================================================================
if_node("Drive Gate", "$('Pilot Config').item.json.drive_archive_enabled === true")

# Full quality: the trigger payload only carries the downscaled OCR copy —
# archive the ORIGINAL from the console's photo hook instead.
new("Fetch Original Photo", "n8n-nodes-base.httpRequest", 4.4, {
    "method": "GET",
    "url": "=" + HOOK_BASE + "/photo/{{ " + RIID + " }}",
    "sendHeaders": True,
    "headerParameters": {"parameters": [dict(SECRET_HEADER)]},
    "options": {"response": {"response": {"responseFormat": "file"}}},
})

port("Prepare Photo File")
nodes[-1]["parameters"]["jsCode"] = """\
// Filename only — the binary rides from Fetch Original Photo (full quality).
let jobNameR = '';
try { jobNameR = $('Select Job').item.json.job_name || ''; } catch (e) {}
if (!jobNameR) { try { jobNameR = $('AC Build Row').item.json.JobName || ''; } catch (e) {} }
if (!jobNameR) { try { jobNameR = $('Cash Build Row').item.json.JobName || ''; } catch (e) {} }
const parse = $('Parse Check Data').item.json;
function safe(s){ return (s || '').toString().replace(/[^a-zA-Z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60); }
const src = $('Fetch Original Photo').item.binary.data;
const ext = ((src.mimeType || '').includes('png')) ? '.png' : '.jpg';
const fname = (safe(jobNameR) || 'unknown-job') + '_' + (safe(parse.check_number) || 'nochk') + '_' + (safe(String(parse.amount)) || 'noamt') + ext;
return { json: { filename: fname, year: String(new Date().getFullYear()) } };
"""

port("Find Year Folder")
# Production's Drive root always had year subfolders; an EMPTY folder made
# this search emit nothing and silently kill the chain. Always emit.
nodes[-1]["alwaysOutputData"] = True
port("Year Folder Exists?")
port("Create Year Folder")

ATTACH_JS = """\
// Re-attach the ORIGINAL photo binary (folder-lookup steps dropped it).
const src = $('Fetch Original Photo').item.binary.data;
const fname = $('Prepare Photo File').item.json.filename;
return { json: { ...$json }, binary: { data: Object.assign({}, src, { fileName: fname }) } };
"""
port("Attach Photo_00")
nodes[-1]["parameters"]["jsCode"] = ATTACH_JS
port("Attach Photo_01")
nodes[-1]["parameters"]["jsCode"] = ATTACH_JS
port("Upload Check Photo")

# ===========================================================================
# CONNECTIONS
# ===========================================================================
wire("Console Trigger", CONFIG)
wire(CONFIG, "Verify Secret")
wire("Verify Secret", CTX, 0)
wire("Verify Secret", "Drop (Bad Secret)", 1)
wire(CTX, "Read Check with Claude")
wire("Read Check with Claude", "Parse Check Data")
wire("Parse Check Data", "Readable?")
wire("Readable?", "Update: Read Result", 0)
wire("Readable?", "Ask: Unreadable", 1)
wire("Update: Read Result", "Is Cash Photo?")

# cash
wire("Is Cash Photo?", "Cash Data", 0)
wire("Is Cash Photo?", "Photo Has Invoice?", 1)
wire("Cash Data", "Cash Has Data?")
wire("Cash Has Data?", "Cash Job By Invoice", 0)
wire("Cash Has Data?", "Ask: Cash Info", 1)
wire("Ask: Cash Info", "Wait: Cash")
wire("Ask: Cash Invoice Not Found", "Wait: Cash")
wire("Wait: Cash", "Parse Cash Info")
wire("Parse Cash Info", "Cash Data")
wire("Cash Job By Invoice", "Cash Job Found?")
wire("Cash Job Found?", "Cash Find Duplicates", 0)
wire("Cash Job Found?", "Ask: Cash Invoice Not Found", 1)
wire("Cash Find Duplicates", "Cash Duplicate?")
wire("Cash Duplicate?", "Final: Cash Already Recorded", 0)
wire("Cash Duplicate?", "Cash Build Row", 1)
wire("Cash Build Row", "Update: Cash Details")
wire("Update: Cash Details", "Cash Post Note")
wire("Cash Post Note", "Final: Cash Recorded")

# fast path
wire("Photo Has Invoice?", "Photo Job By Invoice", 0)
wire("Photo Has Invoice?", "Smart Search", 1)
wire("Photo Job By Invoice", "Photo Job Found?")
wire("Photo Job Found?", "AC Find Duplicates", 0)
wire("Photo Job Found?", "Smart Search", 1)
wire("AC Find Duplicates", "Fast Path Duplicate?")
wire("Fast Path Duplicate?", "Ask: Duplicate (Fast Path)", 0)
wire("Fast Path Duplicate?", "QB Gate (Fast Path)", 1)
wire("Ask: Duplicate (Fast Path)", "Wait: Decision")
wire("QB Gate (Fast Path)", "AC Get QB Invoice", 0)
wire("QB Gate (Fast Path)", "AC Build Row", 1)
wire("AC Get QB Invoice", "AC Build Payment Query")
wire("AC Build Payment Query", "AC Get QB Payments")
wire("AC Get QB Payments", "AC Build Row")
wire("AC Build Row", "Update: Fast Path Details")
wire("Update: Fast Path Details", "AC Post Note")
wire("AC Post Note", "Final: Fast Path Recorded")

# matching brain
wire("Smart Search", "AV Seed")
wire("AV Seed", "AV Fallback Find")
wire("AV Fallback Find", "AV Merge Fanout")
wire("AV Merge Fanout", "QB Gate (AV)")
wire("QB Gate (AV)", "AV Get Job Invoices", 0)
wire("QB Gate (AV)", "AV Collector", 1)
wire("AV Get Job Invoices", "AV Build QB Query")
wire("AV Build QB Query", "AV Get QB Amounts")
wire("AV Get QB Amounts", "AV Collector")
wire("AV Collector", "QB Gate (Vet)")
wire("QB Gate (Vet)", "Vet Prep", 0)
wire("QB Gate (Vet)", "Pick Best Match", 1)
wire("Vet Prep", "Vet Has Candidate?")
wire("Vet Has Candidate?", "Vet Get Invoice", 0)
wire("Vet Has Candidate?", "Pick Best Match", 1)
wire("Vet Get Invoice", "Vet QB Invoice")
wire("Vet QB Invoice", "Vet Decide")
wire("Vet Decide", "Vet Skip?")
wire("Vet Skip?", "Rescue Find Jobs", 0)
wire("Vet Skip?", "Pick Best Match", 1)
wire("Rescue Find Jobs", "Rescue Prep")
wire("Rescue Prep", "Rescue Has Candidate?")
wire("Rescue Has Candidate?", "Rescue Get Invoice", 0)
wire("Rescue Has Candidate?", "Rescue None", 1)
wire("Rescue Get Invoice", "Rescue QB Invoice")
wire("Rescue QB Invoice", "Rescue Decide")
wire("Rescue Decide", "Pick Best Match")
wire("Rescue None", "Pick Best Match")
wire("Pick Best Match", "Match Result")

# outcomes
wire("Match Result", "Find Duplicates", 0)
wire("Match Result", "Build Address Options", 1)
wire("Match Result", "Ask: Not Found", 2)
wire("Match Result", "Ask: Not Found", 3)
wire("Find Duplicates", "Duplicate?")
wire("Duplicate?", "Ask: Duplicate (Match)", 0)
wire("Duplicate?", "Get Invoice Number", 1)
wire("Ask: Duplicate (Match)", "Wait: Decision")
wire("Get Invoice Number", "Ask: Confirm Match")
wire("Ask: Confirm Match", "Wait: Decision")
wire("Build Address Options", "Ask: Candidates")
wire("Ask: Candidates", "Wait: Decision")
wire("Ask: Not Found", "Wait: Decision")
wire("Ask: Unreadable", "Wait: Decision")
wire("Ask: Still Not Found", "Wait: Decision")

# decision loop
wire("Wait: Decision", "Parse Decision")
wire("Parse Decision", "Decision Valid?")
wire("Decision Valid?", "Decision Router", 0)
wire("Decision Valid?", "Drop (Bad Resume)", 1)
wire("Decision Router", "Final: Ignored Duplicate", 0)
wire("Decision Router", "Select Job", 1)
wire("Decision Router", "Text Job By Invoice", 2)
wire("Decision Router", "Parse Correction", 3)
wire("Text Job By Invoice", "Text Job Found?")
wire("Text Job Found?", "Select Job", 0)
wire("Text Job Found?", "Ask: Still Not Found", 1)
wire("Parse Correction", "Has Moraware Link?")
wire("Has Moraware Link?", "Select Job", 0)
wire("Has Moraware Link?", "Search Corrected Job", 1)
wire("Search Corrected Job", "Pick Corrected Match")
wire("Pick Corrected Match", "Corrected Found?")
wire("Corrected Found?", "Select Job", 0)
wire("Corrected Found?", "Ask: Still Not Found", 1)
wire("Select Job", "Get Chosen Invoice")
wire("Get Chosen Invoice", "Row Context")
wire("Row Context", "Update: Confirmed")
wire("Update: Confirmed", "QB Gate (Confirm)")
wire("QB Gate (Confirm)", "Get QB Invoice", 0)
wire("QB Gate (Confirm)", "Simple Note", 1)
wire("Get QB Invoice", "Build Payment Query")
wire("Build Payment Query", "Get QB Payments")
wire("Get QB Payments", "Classify Payment")
wire("Classify Payment", "Update: Classified")
wire("Update: Classified", "Build Payment Note")
wire("Build Payment Note", "Create Moraware Activity")
wire("Simple Note", "Create Moraware Activity")
wire("Create Moraware Activity", "Build Final Message")
wire("Build Final Message", "Final: Confirmed")
wire("Final: Confirmed", "Drive Gate")

# drive archive — every recorded outcome archives its photo
wire("Final: Fast Path Recorded", "Drive Gate")
wire("Final: Cash Recorded", "Drive Gate")
wire("Drive Gate", "Fetch Original Photo", 0)
wire("Fetch Original Photo", "Prepare Photo File")
wire("Prepare Photo File", "Find Year Folder")
wire("Find Year Folder", "Year Folder Exists?")
wire("Year Folder Exists?", "Attach Photo_00", 0)
wire("Year Folder Exists?", "Create Year Folder", 1)
wire("Create Year Folder", "Attach Photo_01")
wire("Attach Photo_00", "Upload Check Photo")
wire("Attach Photo_01", "Upload Check Photo")

# ===========================================================================
# LAYOUT — columns by BFS depth, rows by arrival order per column.
# ===========================================================================
from collections import deque

depth = {"Console Trigger": 0}
q = deque(["Console Trigger"])
while q:
    cur = q.popleft()
    for branch in connections.get(cur, {}).get("main", []):
        for c in branch:
            nxt = c["node"]
            if nxt not in depth:
                depth[nxt] = depth[cur] + 1
                q.append(nxt)
lanes = {}
for n in nodes:
    d = depth.get(n["name"], 0)
    lane = lanes.get(d, 0)
    lanes[d] = lane + 1
    n["position"] = [d * 260, lane * 170]

# Credential-needing nodes behind the default-off flags ship DISABLED so the
# workflow activates cleanly without QB/Drive credentials attached. Enabling
# QB or Drive later = attach credentials + re-enable these + flip the flag.
FLAG_OFF_DISABLED = {
    "AC Get QB Invoice", "AC Get QB Payments", "AV Get QB Amounts",
    "Vet QB Invoice", "Rescue QB Invoice", "Get QB Invoice", "Get QB Payments",
    "Find Year Folder", "Create Year Folder", "Upload Check Photo",
}
for n in nodes:
    if n["name"] in FLAG_OFF_DISABLED:
        n["disabled"] = True

workflow = {
    "name": "PILOT CONSOLE CHECK-BOT",
    "nodes": nodes,
    "connections": connections,
    "active": False,
    "settings": {"executionOrder": "v1"},
    "pinData": {},
}

# ===========================================================================
# VALIDATION
# ===========================================================================
report = []
errors = []

names = [n["name"] for n in nodes]
ids = [n["id"] for n in nodes]
if len(set(names)) != len(names):
    errors.append("duplicate node names")
if len(set(ids)) != len(ids):
    errors.append("duplicate node ids")

name_set = set(names)
for frm, outs in connections.items():
    if frm not in name_set:
        errors.append(f"connection from unknown node: {frm}")
    for branch in outs.get("main", []):
        for c in branch:
            if c["node"] not in name_set:
                errors.append(f"connection to unknown node: {frm} -> {c['node']}")

blob = json.dumps(workflow)
refs = sorted(set(re.findall(r"\$\('([^']+)'\)", blob)))
dangling = [r for r in refs if r not in name_set]
if dangling:
    errors.append(f"dangling $() references: {dangling}")
report.append(f"$() references checked: {len(refs)}, all resolve" if not dangling
              else f"$() references: {len(refs)}, DANGLING: {dangling}")

for bad, why in [
    ("sk-ant-", "live Anthropic key"),
    ("checkbot_9k3m", "Evolution API key"),
    ("bot.emgcheckbot.us", "WhatsApp send endpoint"),
    ("000000000000000", "production QB company id"),
    ("appXXXXXXXXXXXXXX", "Airtable base id"),
    ("1cVZyZjc4cITdS240gkTv9hlh1MuSrbXh", "production Drive folder id"),
]:
    if bad in blob:
        errors.append(f"forbidden string in output: {why} ({bad})")

for n in nodes:
    if n["type"] == "n8n-nodes-base.airtable":
        errors.append(f"airtable node leaked: {n['name']}")
    if n["type"] == "n8n-nodes-base.httpRequest":
        url = str(n["parameters"].get("url", ""))
        if "/api/hooks/pilot" in url:
            headers = json.dumps(n["parameters"].get("headerParameters", {}))
            if "X-Pilot-Secret" not in headers:
                errors.append(f"hook call without X-Pilot-Secret: {n['name']}")

for n in nodes:
    if n["name"].startswith("Ask:"):
        body = str(n["parameters"].get("jsonBody", ""))
        if "$execution.resumeUrl" not in body:
            errors.append(f"question without resume_url: {n['name']}")
for n in nodes:
    if n["type"] == "n8n-nodes-base.wait":
        if n["parameters"].get("resume") != "webhook":
            errors.append(f"wait node not webhook-resumable: {n['name']}")

ported = [n for n in nodes if n["name"] in src_by_name]
mismatched = [n["name"] for n in ported
              if n["typeVersion"] != src_by_name[n["name"]].get("typeVersion")]
if mismatched:
    errors.append(f"typeVersion drift on ported nodes: {mismatched}")

positions = [tuple(n["position"]) for n in nodes]
if len(set(positions)) != len(positions):
    errors.append("overlapping node positions")

types = {}
for n in nodes:
    types[n["type"].split(".")[-1]] = types.get(n["type"].split(".")[-1], 0) + 1
report.append(f"nodes: {len(nodes)} ({len(ported)} transplanted, {len(nodes) - len(ported)} new)")
report.append("types: " + ", ".join(f"{t}={c}" for t, c in sorted(types.items())))
report.append(f"connections: {sum(len(b) for o in connections.values() for b in o['main'])}")
report.append(f"webhook path: pilot-checkbot | active: false | executionOrder: v1")
report.append(f"pre-disabled (flag-off, no credentials needed): {len([n for n in nodes if n.get('disabled')])}")

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
