"""Assemble n8n/PILOT_CONSOLE_PAYMENT-SWEEP.json from the production export.

Transplants the sweep's battle-tested logic (Normalize Payments, Build Work
Items diff brain, tier builders with the 75%-contract math, NF fallback)
byte-faithfully; replaces Airtable with the console's list/items/update hooks,
WhatsApp with question cards + /notify pushes. Tier 1 (invoice matched to a
Moraware job) records everything automatically — push only, no questions.

Tier 2/3 questions resume to this workflow's second webhook
(/webhook/sweep-decision?item=<id>) because an hourly batch run cannot wait.

Run:  python n8n/build_sweep_workflow.py
"""

import copy
import json
import re
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "n8n" / "EMG PAYMENT-SWEEP BOT (21).json"
TARGET = REPO / "n8n" / "PILOT_CONSOLE_PAYMENT-SWEEP.json"

src = json.loads(SOURCE.read_text(encoding="utf-8"))
src_by_name = {n["name"]: n for n in src["nodes"]}

CONFIG = "Sweep Config"

GLOBAL_SWAPS = [
    # register source: console snapshot dressed in Airtable row shape
    ("$('Fetch Airtable Rows')", "$('Rows As Register')"),
    # QB company -> config (sandbox realm)
    ("https://quickbooks.api.intuit.com/v3/company/000000000000000",
     "{{ $('Sweep Config').item.json.qb_company_url }}"),
    # bridge -> config
    ("https://bridge.emgcheckbot.us",
     "{{ $('Sweep Config').item.json.bridge_base_url }}"),
]

NODE_SWAPS = {
    "QB Recent Payments": [
        ("$now.minus({ days: 3 })",
         "$now.minus({ days: $('Sweep Config').item.json.lookback_days })"),
    ],
    # Tier2 exposes its tentative candidate as plain fields for the question.
    "Build Tier2": [
        ("_postNote:false, jobId:pbm.job_id||0, groupJID:GROUP, noteText:'',",
         "_postNote:false, jobId:pbm.job_id||0, groupJID:GROUP, noteText:'', "
         "_candJobId:(pbm.job_id||''), _candJobName:(pbm.job_name||''), "
         "_candLeadUrl:(pbm.lead_url||''), _candStrategy:(pbm.strategy||''),"),
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


nodes = []
connections = {}
_used = set()


def _nid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "pilot-sweep/" + name))


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
    return add({
        "id": _nid(name),
        "name": name,
        "type": orig["type"],
        "typeVersion": orig.get("typeVersion", 1),
        "position": [0, 0],
        "parameters": params,
    })


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


def if_node(name, left_expr, tv=2.2):
    return new(name, "n8n-nodes-base.if", tv, {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "",
                        "typeValidation": "loose", "version": 3},
            "conditions": [{
                "id": _nid(name + "/c")[:8],
                "leftValue": "={{ " + left_expr + " }}",
                "rightValue": "={{ true }}",
                "operator": {"type": "boolean", "operation": "true", "singleValue": True},
            }],
            "combinator": "and",
        },
        "options": {},
    })


def no_op(name):
    return new(name, "n8n-nodes-base.noOp", 1, {})


def code(name, js):
    return new(name, "n8n-nodes-base.code", 2, {"jsCode": js})


SECRET_HEADER = {"name": "X-Pilot-Secret",
                 "value": "={{ $('Sweep Config').item.json.pilot_hook_secret }}"}
HOOK_BASE = "{{ $('Sweep Config').item.json.console_base_url }}/api/hooks/pilot"


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


def hook_get(name, path_and_query):
    return new(name, "n8n-nodes-base.httpRequest", 4.4, {
        "method": "GET",
        "url": "=" + HOOK_BASE + path_and_query,
        "sendHeaders": True,
        "headerParameters": {"parameters": [dict(SECRET_HEADER)]},
        "options": {},
    })


def update(name, riid_expr, body_expr, fields_expr=None, status=None):
    parts = ['  "review_item_id": "{{ ' + riid_expr + ' }}"',
             '  "body": {{ ' + body_expr + ' }}']
    if fields_expr:
        parts.append('  "fields": {{ ' + fields_expr + ' }}')
    if status:
        parts.append('  "status": "' + status + '"')
    return hook_post(name, "/update", "={\n" + ",\n".join(parts) + "\n}")


def final(name, riid_expr, body_expr, status, push=True):
    return hook_post(name, "/final", (
        "={\n"
        '  "review_item_id": "{{ ' + riid_expr + ' }}",\n'
        '  "body": {{ ' + body_expr + ' }},\n'
        '  "status": "' + status + '",\n'
        '  "push": ' + ("true" if push else "false") + '\n'
        "}"
    ))


def ask(name, riid_expr, body_expr, candidates_expr="[]", format_hint="null",
        push=True):
    """Question resuming to the sweep-decision webhook (?item=<id>).
    Retry questions are silent — the answering manager is on the card."""
    # {{ null }} interpolates to EMPTY TEXT in n8n — emit a literal null.
    hint = "null" if format_hint == "null" else "{{ " + format_hint + " }}"
    return hook_post(name, "/question", (
        "={\n"
        '  "review_item_id": "{{ ' + riid_expr + ' }}",\n'
        '  "body": {{ ' + body_expr + ' }},\n'
        '  "candidates": {{ ' + candidates_expr + ' }},\n'
        '  "resume_url": "{{ $(\'Sweep Config\').item.json.decision_webhook_url }}?item={{ ' + riid_expr + ' }}",\n'
        '  "allowed_freeform": true,\n'
        '  "format_hint": ' + hint + ',\n'
        '  "push": ' + ("true" if push else "false") + '\n'
        "}"
    ))


def notify(name, title_expr, body_expr):
    return hook_post(name, "/notify", (
        "={\n"
        '  "title": {{ ' + title_expr + ' }},\n'
        '  "body": {{ ' + body_expr + ' }},\n'
        '  "url": "/payments"\n'
        "}"
    ))


# ===========================================================================
# SCHEDULE FLOW
# ===========================================================================
port("Hourly Sweep Trigger")

new(CONFIG, "n8n-nodes-base.set", 3.4, {
    "assignments": {"assignments": [
        {"id": "s1", "name": "console_base_url", "type": "string",
         "value": "PASTE_CONSOLE_BASE_URL"},
        {"id": "s2", "name": "pilot_hook_secret", "type": "string",
         "value": "PASTE_PILOT_HOOK_SECRET"},
        {"id": "s3", "name": "bridge_base_url", "type": "string",
         "value": "https://bridge.emgcheckbot.us"},
        # SANDBOX realm only — never a production company here.
        {"id": "s4", "name": "qb_company_url", "type": "string",
         "value": "https://sandbox-quickbooks.api.intuit.com/v3/company/9341457257917923"},
        {"id": "s5", "name": "moraware_job_base", "type": "string",
         "value": "https://granite-marble-tops.moraware.net/sys/job/"},
        {"id": "s6", "name": "lookback_days", "type": "number", "value": 3},
        {"id": "s7", "name": "register_days", "type": "number", "value": 45},
        # This workflow's OWN second webhook (production URL, filled on import).
        {"id": "s8", "name": "decision_webhook_url", "type": "string",
         "value": "https://alexemg.app.n8n.cloud/webhook/sweep-decision"},
    ]},
    "includeOtherFields": True,
    "options": {},
})

port("QB Payment Methods")
port("QB Recent Payments")
port("Normalize Payments")
port("QB Invoice Numbers")

hook_get("Fetch Console Rows",
         "/list?days={{ $('Sweep Config').item.json.register_days }}")

code("Rows As Register", """\
// Dress console rows in the Airtable row shape Build Work Items expects.
const items = ($json.items || []);
const out = items.map(r => ({ json: { id: r.review_item_id, fields: {
  QBPaymentId: r.qb_payment_id || '',
  CheckNumber: r.check_number || '',
  InvoiceNumber: r.invoice_number || '',
  Amount: r.amount || '',
  Status: r.status || ''
}}}));
// Never return [] — downstream must still run on an empty register.
if (!out.length) out.push({ json: { id: null, fields: null } });
return out;
""")

port("Build Work Items")
port("Route Action")
port("Loop Over Payments")

# --- per-payment: dedup -> match tiers -> record ---------------------------
hook_get("Live Dedup Lookup",
         "/find?qb_payment_id={{ encodeURIComponent($json.qbPaymentId || '0') }}")

code("Dedup Decide", """\
// The original Airtable dedup formula, verbatim in JS: live qb_payment_id
// check first, then the register snapshot for check#/invoice+amount matches.
const p = $('Loop Over Payments').item.json;
const digits = s => String(s == null ? '' : s).replace(/[^0-9]/g, '');
const money = v => { const n = parseFloat(v); return isNaN(n) ? null : Math.round(n * 100) / 100; };
const amt = money(p.amount);
const live = ($json.items || []).filter(r => r.status !== 'superseded_split');
if (live.length) return { json: Object.assign({}, p, { _dupId: live[0].review_item_id }) };
const rows = $('Rows As Register').all().map(it => it.json)
  .filter(r => r && r.id && r.fields)
  .filter(r => String(r.fields.Status || '') !== 'superseded_split');
const invD = digits(p.invoiceNumber), refD = digits(p.refNum);
const amtEq = r => { const a = money(r.fields.Amount); return a !== null && amt !== null && Math.abs(a - amt) < 0.005; };
let hit = null;
if (p.isSplit) {
  hit = rows.find(r => String(r.fields.QBPaymentId || '') === String(p.qbPaymentId) && digits(r.fields.InvoiceNumber) === invD)
     || (invD ? rows.find(r => !(r.fields.QBPaymentId) && digits(r.fields.InvoiceNumber) === invD && amtEq(r)) : null);
} else {
  hit = rows.find(r => String(r.fields.QBPaymentId || '') === String(p.qbPaymentId))
     || (refD ? rows.find(r => digits(r.fields.CheckNumber) === refD && ((invD && digits(r.fields.InvoiceNumber) === invD) || amtEq(r))) : null)
     || (invD ? rows.find(r => !(r.fields.QBPaymentId) && digits(r.fields.InvoiceNumber) === invD && amtEq(r)) : null);
}
return { json: Object.assign({}, p, { _dupId: hit ? hit.id : '' }) };
""")

if_node("Already Recorded?", "($json._dupId || '') !== ''")

update("Adopt: Stamp QB Id",
       "$json._dupId",
       "JSON.stringify('Payment sweep: found this payment in QuickBooks (' + ($json.method || 'payment') + (" "$json.refNum ? (' #' + $json.refNum) : '') + ') \\u2014 QB id stamped.')".replace('" "', ''),
       "JSON.stringify(Object.fromEntries(Object.entries({qb_payment_id: $json.qbPaymentId, payment_method: ($json.method || '').toLowerCase() || null}).filter(function(e){return e[1];})))")

port("Job By Invoice")
port("Tier1 or Fallback?")
port("Build Tier1")
port("Get QB Customer")
port("Parse QB Address")
port("Smart Search")
port("Pick Best Match")
if_node("Match Found?", "$json.found === true", tv=2.3)
port("Build Tier2")
port("NF Candidates")
port("NF Find Job")
port("NF Collect")
port("NF Found?")
port("Build Tier3")
port("Selected Payment")

SEL = "$('Selected Payment').item.json"
hook_post("Create Item", "/items", (
    "={\n"
    '  "amount": {{ JSON.stringify(String(' + SEL + '.Amount ?? "")) }},\n'
    '  "payer_name": {{ JSON.stringify(' + SEL + '.PayerName || "") }},\n'
    '  "payment_method": {{ JSON.stringify((' + SEL + '.PaymentMethod || "").toLowerCase()) }},\n'
    '  "payment_type": {{ JSON.stringify(' + SEL + '.PaymentType || "") }},\n'
    '  "invoice_number": {{ JSON.stringify(' + SEL + '.InvoiceNumber || "") }},\n'
    '  "check_number": {{ JSON.stringify(' + SEL + '.CheckNumber || "") }},\n'
    '  "txn_date": {{ JSON.stringify(' + SEL + '.PaymentDate || "") }},\n'
    '  "qb_payment_id": {{ JSON.stringify(' + SEL + '.QBPaymentId || "") }},\n'
    '  "status": "{{ ' + SEL + ".Status === 'confirmed' ? 'confirmed' : 'needs_job' }}\",\n"
    '  "body": {{ JSON.stringify("Found in QuickBooks by the payment sweep: $" + (' + SEL + '.Amount ?? "?") + " \\u00b7 " + (' + SEL + '.PaymentMethod || "payment") + " \\u00b7 invoice #" + (' + SEL + '.InvoiceNumber || "?") + (' + SEL + '.SplitGroup ? " (part of a split payment)" : "") + ".") }}\n'
    "}"
))

new("Tier Router", "n8n-nodes-base.switch", 3.4, {
    "rules": {"values": [
        {"conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                    "typeValidation": "loose", "version": 3},
                        "conditions": [{"id": "t1", "leftValue": "={{ " + SEL + ".Status }}",
                                        "rightValue": "confirmed",
                                        "operator": {"type": "string", "operation": "equals"}}],
                        "combinator": "and"},
         "renameOutput": True, "outputKey": "Tier1Auto"},
        {"conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                    "typeValidation": "loose", "version": 3},
                        "conditions": [{"id": "t2", "leftValue": "={{ " + SEL + ".Status }}",
                                        "rightValue": "queued",
                                        "operator": {"type": "string", "operation": "equals"}}],
                        "combinator": "and"},
         "renameOutput": True, "outputKey": "Tier2Confirm"},
    ]},
    "options": {"fallbackOutput": "extra"},
})

RIID = "$('Create Item').item.json.review_item_id"
update("Update: Job Fields",
       RIID,
       "JSON.stringify('Matched to ' + (" + SEL + ".JobName || 'job') + ' by invoice #' + (" + SEL + ".InvoiceNumber || '?') + '.')",
       "JSON.stringify(Object.fromEntries(Object.entries({matched_job_id: String(" + SEL + ".JobId || ''), matched_job_name: " + SEL + ".JobName, moraware_url: " + SEL + ".MorawareURL}).filter(function(e){return e[1];})))")

port("Post Moraware Note")

# push=False: the ONE run-summary push announces auto-records — a per-item
# push here made every payment look like two (owner feedback 2026-07-21).
final("Final: Recorded", RIID,
      "JSON.stringify(" + SEL + ".messageText)", "confirmed", push=False)

ask("Ask: Confirm Match (Sweep)", RIID,
    "JSON.stringify('New payment from QuickBooks: $' + (" + SEL + ".Amount ?? '?') + ' \\u00b7 ' + (" + SEL + ".PaymentMethod || 'payment') + ' \\u00b7 invoice #' + (" + SEL + ".InvoiceNumber || '?') + '. I matched it to a Moraware job by ' + ((" + SEL + "._candStrategy || '').indexOf('fallback:') === 0 ? 'address/name lookup' : 'address') + ', NOT by invoice \\u2014 is this the right job?')",
    "JSON.stringify([{label: " + SEL + "._candJobName || 'Matched job', sublabel: 'Matched by address/name \\u2014 double-check before confirming', job_id: String(" + SEL + "._candJobId || ''), moraware_url: " + SEL + "._candLeadUrl || null}])",
    "JSON.stringify('correct job name or 4-digit invoice #')")

ask("Ask: Which Job (Sweep)", RIID,
    "JSON.stringify('New payment from QuickBooks needs a job: $' + (" + SEL + ".Amount ?? '?') + ' \\u00b7 ' + (" + SEL + ".PaymentMethod || 'payment') + ' \\u00b7 invoice #' + (" + SEL + ".InvoiceNumber || '?') + ' \\u00b7 customer \\u201c' + (" + SEL + ".PayerName || 'unknown') + '\\u201d. No Moraware job matched by invoice, address, or name \\u2014 type the job name.')",
    "[]",
    "JSON.stringify('job name or 4-digit invoice #')")

# --- after the loop: run summary + split digests ---------------------------
code("Run Summary", """\
let sel = [];
try { sel = $('Selected Payment').all().map(it => it.json); } catch (e) { sel = []; }
let adopted = 0;
try { adopted = $('Dedup Decide').all().filter(it => it.json && it.json._dupId).length; } catch (e) { adopted = 0; }
const recorded = sel.filter(s => s.Status === 'confirmed').length;
const confirm = sel.filter(s => s.Status === 'queued').length;
const needsJob = sel.filter(s => s.Status === 'needs_job').length;
const groups = {};
for (const s of sel) { if (s.SplitGroup) (groups[s.SplitGroup] = groups[s.SplitGroup] || []).push(s); }
const parts = [];
if (recorded) parts.push(recorded + ' recorded automatically');
if (confirm) parts.push(confirm + ' waiting for your confirmation');
if (needsJob) parts.push(needsJob + ' need a job');
if (adopted) parts.push(adopted + ' matched to existing rows');
return [{ json: {
  total: sel.length + adopted,
  // Questions push their own deep links; the summary only announces the
  // SILENT work (auto-records, adoptions, splits) so nothing pushes twice.
  silent_total: recorded + adopted,
  summary: parts.join(' \\u00b7 ') || 'nothing new',
  splitGroups: Object.keys(groups).map(g => {
    const list = groups[g];
    let total = 0; for (const s of list) { const n = parseFloat(s.Amount); if (!isNaN(n)) total += n; }
    return { group: g, count: list.length, total: Math.round(total * 100) / 100,
             method: list[0].PaymentMethod || 'payment', check: list[0].CheckNumber || '',
             payer: list[0].PayerName || '' };
  })
}}];
""")

if_node("Anything To Report?", "($json.silent_total || 0) > 0")
no_op("Quiet Run")

notify("Notify: Sweep Summary",
       "JSON.stringify('Payment sweep: ' + $json.summary)",
       "JSON.stringify('Tap to open the payments board.')")

code("Split Digest Items", """\
const groups = $('Run Summary').first().json.splitGroups || [];
return groups.map(g => ({ json: g }));
""")

notify("Notify: Split Digest",
       "JSON.stringify('One ' + ($json.method || 'payment') + ($json.check ? (' #' + $json.check) : '') + ' covered ' + $json.count + ' invoices')",
       "JSON.stringify('$' + $json.total + ' total \\u00b7 ' + ($json.payer || 'unknown payer') + ' \\u2014 each invoice got its own card on the board.')")

# --- backfill branch -------------------------------------------------------
port("Supersede Parent?")

update("Update: Supersede Parent",
       "$json.airtableRowId",
       "JSON.stringify('Payment sweep: this ' + ($json.method || 'payment') + ($json.refNum ? (' #' + $json.refNum) : '') + ' actually covered multiple invoices \\u2014 this full-amount row is superseded by per-invoice rows.')",
       "JSON.stringify(Object.fromEntries(Object.entries({qb_payment_id: $json.qbPaymentId, payment_method: ($json.method || '').toLowerCase() || null}).filter(function(e){return e[1];})))",
       status="superseded_split")

update("Update: Backfill",
       "$json.airtableRowId",
       "JSON.stringify('Payment sweep: verified in QuickBooks (' + ($json.method || 'payment') + ', ' + ($json.txnDate || '') + ') \\u2014 QB payment id stamped.')",
       "JSON.stringify(Object.fromEntries(Object.entries({qb_payment_id: $json.qbPaymentId, payment_method: ($json.method || '').toLowerCase() || null}).filter(function(e){return e[1];})))")

# ===========================================================================
# DECISION FLOW (webhook resume for tier-2/3 questions)
# ===========================================================================
new("Sweep Decision Webhook", "n8n-nodes-base.webhook", 2.1,
    {"httpMethod": "POST", "path": "sweep-decision", "options": {}},
    webhook_id=True)

# The decision flow is a SEPARATE execution — it needs its own config copy.
_cfg_copy = None
for _n in nodes:
    if _n["name"] == CONFIG:
        _cfg_copy = copy.deepcopy(_n["parameters"])
new("Sweep Config D", "n8n-nodes-base.set", 3.4, _cfg_copy)

code("Parse Sweep Decision", """\
// Console decision endpoint POSTs {secret, choice|text} to
// resume_url = .../sweep-decision?item=<review_item_id>.
const b = $('Sweep Decision Webhook').first().json.body || {};
const q = $('Sweep Decision Webhook').first().json.query || {};
const ok = String(b.secret || '') === $('Sweep Config D').first().json.pilot_hook_secret;
const itemId = String(q.item || '');
const choice = b.choice || null;
const text = (b.text != null) ? String(b.text).trim() : '';
const label = choice ? String(choice.label || '') : '';
const jobId = (choice && choice.job_id != null && String(choice.job_id).trim() !== '')
  ? String(choice.job_id).trim() : null;
const invoiceText = (!choice && /^#?\\s*\\d{3,6}\\s*$/.test(text))
  ? text.replace(/[^0-9]/g, '') : null;
return { json: { valid: ok && itemId !== '', item_id: itemId, kind: choice ? 'choice' : 'text',
  label: label, job_id: jobId, text: text, invoice_text: invoiceText } };
""")

if_node("SD Valid?", "$json.valid === true")
no_op("Drop (Bad SD Resume)")

new("SD Router", "n8n-nodes-base.switch", 3.4, {
    "rules": {"values": [
        {"conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                    "typeValidation": "loose", "version": 3},
                        "conditions": [{"id": "sd1", "leftValue": "={{ $json.kind === 'choice' && $json.job_id !== null }}",
                                        "rightValue": "={{ true }}",
                                        "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
                        "combinator": "and"},
         "renameOutput": True, "outputKey": "ChoseJob"},
        {"conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                    "typeValidation": "loose", "version": 3},
                        "conditions": [{"id": "sd2", "leftValue": "={{ $json.invoice_text || '' }}",
                                        "rightValue": "",
                                        "operator": {"type": "string", "operation": "notEmpty", "singleValue": True}}],
                        "combinator": "and"},
         "renameOutput": True, "outputKey": "InvoiceText"},
    ]},
    "options": {"fallbackOutput": "extra"},
})

new("SD Job By Invoice", "n8n-nodes-base.httpRequest", 4.4, {
    "method": "POST",
    "url": "={{ $('Sweep Config').item.json.bridge_base_url }}/api/checkbot/job-by-invoice",
    "sendHeaders": True,
    "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
    "sendBody": True,
    "specifyBody": "json",
    "jsonBody": "={\n  \"invoiceNumber\": {{ JSON.stringify($('Parse Sweep Decision').item.json.invoice_text || '') }}\n}",
    "options": {},
})
if_node("SD Invoice Found?", "$json.Found === true")

new("SD Find By Name", "n8n-nodes-base.httpRequest", 4.4, {
    "method": "POST",
    "url": "={{ $('Sweep Config').item.json.bridge_base_url }}/api/checkbot/find-job-by-name",
    "sendHeaders": True,
    "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
    "sendBody": True,
    "specifyBody": "json",
    "jsonBody": "={\n  \"job_name\": {{ JSON.stringify($('Parse Sweep Decision').item.json.text || '') }},\n  \"limit\": 5\n}",
    "options": {},
})

code("SD Name Pick", """\
const r = $json || {};
const ms = Array.isArray(r.Matches) ? r.Matches : [];
const strong = ms.filter(m => m && m.NameMatchStrength === 'strong');
const chosen = strong.length ? strong[0] : (ms.length ? ms[0] : null);
return { json: { found: !!chosen,
  JobId: chosen ? chosen.JobId : null,
  CustomerName: chosen ? chosen.CustomerName : '',
  LeadUrl: chosen ? (chosen.LeadUrl || '') : '' } };
""")
if_node("SD Name Found?", "$json.found === true")

SDID = "$('Parse Sweep Decision').item.json.item_id"
ask("Ask: Still Not Found (Sweep)", SDID,
    "JSON.stringify('Still couldn\\u2019t find a job for \\u201c' + ($('Parse Sweep Decision').item.json.text || '?') + '\\u201d. Type the EXACT Moraware job name or a 4-digit invoice #.')",
    "[]",
    "JSON.stringify('exact job name / invoice #')", push=False)

code("SD Select Job", """\
const d = $('Parse Sweep Decision').item.json;
let job_id = null, job_name = '', lead_url = '';
if (d.kind === 'choice' && d.job_id) { job_id = d.job_id; job_name = d.label; }
else {
  let s = null;
  try { const t = $('SD Job By Invoice').item.json; if (t && t.Found === true) s = { id: t.JobId, name: t.CustomerName, url: t.LeadUrl }; } catch (e) {}
  if (!s) { try { const n = $('SD Name Pick').item.json; if (n && n.found) s = { id: n.JobId, name: n.CustomerName, url: n.LeadUrl }; } catch (e) {} }
  if (s) { job_id = String(s.id); job_name = s.name || ''; lead_url = s.url || ''; }
}
if (!lead_url && job_id && /^\\d+$/.test(job_id)) {
  lead_url = $('Sweep Config').first().json.moraware_job_base + job_id;
}
return { json: { job_id: job_id, job_name: job_name, lead_url: lead_url } };
""")

hook_get("SD Fetch Rows", "/list?days={{ $('Sweep Config').item.json.register_days }}")

code("SD Row", """\
// The answered item's payment facts, for the Moraware note + final line.
const want = $('Parse Sweep Decision').first().json.item_id;
const rows = ($json.items || []);
const r = rows.find(x => x.review_item_id === want) || {};
return { json: { amount: r.amount || '?', invoice: r.invoice_number || '',
  method: r.payment_method || 'payment', payer: r.payer_name || '',
  check: r.check_number || '', txn_date: r.txn_date || '' } };
""")

update("SD Update Job", SDID,
       "JSON.stringify('Job set: ' + ($('SD Select Job').item.json.job_name || ('job #' + $('SD Select Job').item.json.job_id)) + '.')",
       "JSON.stringify(Object.fromEntries(Object.entries({matched_job_id: String($('SD Select Job').item.json.job_id || ''), matched_job_name: $('SD Select Job').item.json.job_name, moraware_url: $('SD Select Job').item.json.lead_url}).filter(function(e){return e[1];})))")

new("SD Post Note", "n8n-nodes-base.httpRequest", 4.4, {
    "method": "POST",
    "url": "={{ $('Sweep Config').item.json.bridge_base_url }}/api/checkbot/create-activity",
    "sendHeaders": True,
    "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
    "sendBody": True,
    "specifyBody": "json",
    "jsonBody": ("={\n"
                 "  \"jobId\": {{ $('SD Select Job').item.json.job_id }},\n"
                 "  \"notes\": {{ JSON.stringify('Payment recorded \\u2014 ' + ($('SD Row').item.json.payer || 'customer') + ' paid $' + $('SD Row').item.json.amount + ' (' + $('SD Row').item.json.method + ')' + ($('SD Row').item.json.invoice ? (' \\u00b7 invoice #' + $('SD Row').item.json.invoice) : '') + '. Confirmed by a manager via the console; logged by the payment sweep.') }},\n"
                 "  \"activityTypeId\": 17\n"
                 "}"),
    "options": {},
})

# push=False: the deciding manager is on the card; broadcasting their click
# to the whole pool was the "3-4 pushes later" confusion.
final("SD Final", SDID,
      "JSON.stringify('\\u2713 $' + $('SD Row').item.json.amount + ' \\u2192 ' + ($('SD Select Job').item.json.job_name || 'job') + ($('SD Row').item.json.invoice ? (' \\u00b7 invoice #' + $('SD Row').item.json.invoice) : '') + ' \\u2014 recorded, Moraware note added.' + ($('SD Select Job').item.json.lead_url ? ('\\n' + $('SD Select Job').item.json.lead_url) : ''))",
      "confirmed", push=False)

# ===========================================================================
# CONNECTIONS
# ===========================================================================
wire("Hourly Sweep Trigger", CONFIG)
wire(CONFIG, "QB Payment Methods")
wire("QB Payment Methods", "QB Recent Payments")
wire("QB Recent Payments", "Normalize Payments")
wire("Normalize Payments", "QB Invoice Numbers")
wire("QB Invoice Numbers", "Fetch Console Rows")
wire("Fetch Console Rows", "Rows As Register")
wire("Rows As Register", "Build Work Items")
wire("Build Work Items", "Route Action")
wire("Route Action", "Loop Over Payments", 0)
wire("Route Action", "Supersede Parent?", 1)

# loop: output 0 = done, output 1 = each item
wire("Loop Over Payments", "Run Summary", 0)
wire("Loop Over Payments", "Live Dedup Lookup", 1)
wire("Live Dedup Lookup", "Dedup Decide")
wire("Dedup Decide", "Already Recorded?")
wire("Already Recorded?", "Adopt: Stamp QB Id", 0)
wire("Already Recorded?", "Job By Invoice", 1)
wire("Adopt: Stamp QB Id", "Loop Over Payments")
wire("Job By Invoice", "Tier1 or Fallback?")
wire("Tier1 or Fallback?", "Build Tier1", 0)
wire("Tier1 or Fallback?", "Get QB Customer", 1)
wire("Get QB Customer", "Parse QB Address")
wire("Parse QB Address", "Smart Search")
wire("Smart Search", "Pick Best Match")
wire("Pick Best Match", "Match Found?")
wire("Match Found?", "Build Tier2", 0)
wire("Match Found?", "NF Candidates", 1)
wire("NF Candidates", "NF Find Job")
wire("NF Find Job", "NF Collect")
wire("NF Collect", "NF Found?")
wire("NF Found?", "Build Tier2", 0)
wire("NF Found?", "Build Tier3", 1)
wire("Build Tier1", "Selected Payment")
wire("Build Tier2", "Selected Payment")
wire("Build Tier3", "Selected Payment")
wire("Selected Payment", "Create Item")
wire("Create Item", "Tier Router")
wire("Tier Router", "Update: Job Fields", 0)
wire("Tier Router", "Ask: Confirm Match (Sweep)", 1)
wire("Tier Router", "Ask: Which Job (Sweep)", 2)
wire("Update: Job Fields", "Post Moraware Note")
wire("Post Moraware Note", "Final: Recorded")
wire("Final: Recorded", "Loop Over Payments")
wire("Ask: Confirm Match (Sweep)", "Loop Over Payments")
wire("Ask: Which Job (Sweep)", "Loop Over Payments")

wire("Run Summary", "Anything To Report?")
wire("Anything To Report?", "Notify: Sweep Summary", 0)
wire("Anything To Report?", "Quiet Run", 1)
wire("Notify: Sweep Summary", "Split Digest Items")
wire("Split Digest Items", "Notify: Split Digest")

wire("Supersede Parent?", "Update: Supersede Parent", 0)
wire("Supersede Parent?", "Update: Backfill", 1)

wire("Sweep Decision Webhook", "Sweep Config D")
wire("Sweep Config D", "Parse Sweep Decision")
wire("Parse Sweep Decision", "SD Valid?")
wire("SD Valid?", "SD Router", 0)
wire("SD Valid?", "Drop (Bad SD Resume)", 1)
wire("SD Router", "SD Select Job", 0)
wire("SD Router", "SD Job By Invoice", 1)
wire("SD Router", "SD Find By Name", 2)
wire("SD Job By Invoice", "SD Invoice Found?")
wire("SD Invoice Found?", "SD Select Job", 0)
wire("SD Invoice Found?", "Ask: Still Not Found (Sweep)", 1)
wire("SD Find By Name", "SD Name Pick")
wire("SD Name Pick", "SD Name Found?")
wire("SD Name Found?", "SD Select Job", 0)
wire("SD Name Found?", "Ask: Still Not Found (Sweep)", 1)
wire("SD Select Job", "SD Fetch Rows")
wire("SD Fetch Rows", "SD Row")
wire("SD Row", "SD Update Job")
wire("SD Update Job", "SD Post Note")
wire("SD Post Note", "SD Final")

# ===========================================================================
# DECISION-FLOW CONFIG REFERENCES + LAYOUT
# ===========================================================================
DECISION_NODES = {"Parse Sweep Decision", "SD Valid?", "Drop (Bad SD Resume)",
                  "SD Router", "SD Job By Invoice", "SD Invoice Found?",
                  "SD Find By Name", "SD Name Pick", "SD Name Found?",
                  "Ask: Still Not Found (Sweep)", "SD Select Job",
                  "SD Fetch Rows", "SD Row", "SD Update Job", "SD Post Note",
                  "SD Final"}
for n in nodes:
    if n["name"] in DECISION_NODES:
        n["parameters"] = walk_swap(n["parameters"],
                                    [("$('Sweep Config')", "$('Sweep Config D')")])

from collections import deque

depth = {"Hourly Sweep Trigger": 0, "Sweep Decision Webhook": 0}
q = deque(["Hourly Sweep Trigger", "Sweep Decision Webhook"])
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
    y_base = 0 if not n["name"].startswith(("SD", "Sweep Decision", "Sweep Config D", "Parse Sweep", "Drop (Bad SD", "Ask: Still Not Found")) else 900
    n["position"] = [d * 260, y_base + lane * 170]

workflow = {
    "name": "PILOT CONSOLE PAYMENT-SWEEP",
    "nodes": nodes,
    "connections": connections,
    "active": False,
    "settings": {"executionOrder": "v1"},
    "pinData": {},
}

# ===========================================================================
# VALIDATION
# ===========================================================================
errors = []
report = []
names = [n["name"] for n in nodes]
name_set = set(names)
if len(name_set) != len(names):
    errors.append("duplicate names")
if len({n["id"] for n in nodes}) != len(nodes):
    errors.append("duplicate ids")
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

for bad, why in [
    ("000000000000000", "prod QB company"),
    ("bot.emgcheckbot.us", "WhatsApp endpoint"),
    ("checkbot_9k3m", "Evolution key"),
    ("appXXXXXXXXXXXXXX", "Airtable base"),
    ("120363000000000000", "WhatsApp group JID in live config"),
]:
    # the group JID constant survives inside transplanted tier builders (unused
    # output fields) — flag only the others
    if bad in blob and why != "WhatsApp group JID in live config":
        errors.append(f"forbidden: {why}")

for n in nodes:
    if n["type"] == "n8n-nodes-base.airtable":
        errors.append(f"airtable leaked: {n['name']}")
    if n["type"] == "n8n-nodes-base.httpRequest":
        url = str(n["parameters"].get("url", ""))
        if "/api/hooks/pilot" in url and "X-Pilot-Secret" not in json.dumps(n["parameters"].get("headerParameters", {})):
            errors.append(f"hook without secret: {n['name']}")
for n in nodes:
    if n["name"].startswith("Ask:"):
        if "sweep-decision" not in str(n["parameters"].get("jsonBody", "")) and "decision_webhook_url" not in str(n["parameters"].get("jsonBody", "")):
            errors.append(f"question without webhook resume: {n['name']}")

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
sched_set = reach("Hourly Sweep Trigger")
dec_set = reach("Sweep Decision Webhook")
for n in nodes:
    blob_n = json.dumps(n["parameters"])
    for ref in set(re.findall(r"\$\('([^']+)'\)", blob_n)):
        ok_sched = n["name"] in sched_set and ref in sched_set
        ok_dec = n["name"] in dec_set and ref in dec_set
        if not (ok_sched or ok_dec):
            errors.append(f"cross-execution reference: {n['name']} -> {ref}")

ported = [n for n in nodes if n["name"] in src_by_name]
drift = [n["name"] for n in ported if n["typeVersion"] != src_by_name[n["name"]].get("typeVersion")]
if drift:
    errors.append(f"typeVersion drift: {drift}")

types = {}
for n in nodes:
    t = n["type"].split(".")[-1]
    types[t] = types.get(t, 0) + 1
report.append(f"nodes: {len(nodes)} ({len(ported)} transplanted, {len(nodes) - len(ported)} new)")
report.append("types: " + ", ".join(f"{t}={c}" for t, c in sorted(types.items())))
report.append(f"connections: {sum(len(b) for o in connections.values() for b in o['main'])}")
report.append("triggers: hourly schedule + webhook sweep-decision | active: false")

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
