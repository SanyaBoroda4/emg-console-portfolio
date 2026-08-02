"""Materials catalog (slab scans chapter): the stone-name typeahead and its
intake doors. Search mirrors the jobs typeahead (local table, ~ms).

Feeds: delivery slips (slab_hooks upserts on every slip), manual adds from
the picker, and n8n bulk feeds (Airtable daily / stock Excel weekly /
supplier sites weekly) through the X-Pilot-Secret hook.
"""

import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth import require_role
from app.config import get_settings
from app.db import get_db
from app.models import MaterialCatalog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/materials", tags=["materials"])

MIN_QUERY_LEN = 2
LIMIT = 8

# --- base-name normalization (owner 2026-07-24) ---------------------------
# The dropdown shows ONE clean stone name: supplier + finish + grade + size +
# category variants all collapse to the base. Computed at query time, so no
# data is lost (full names stay in the catalog) and it's fully reversible.
_SIZE_RE = re.compile(r"^\d+(\.\d+)?CM$")
_FORMAT = {"FF", "DF", "LF", "TEK", "VC", "V/C", "NC", "HASHTAG"}
_SOFT = {  # trailing finish / grade / category words
    "POLISHED", "POL", "HONED", "HON", "LEATHER", "LEATHERED", "LEATH",
    "SATIN", "BRUSHED", "MATTE", "DUAL", "ANTIQUE", "ANTIQUED", "SUEDE",
    "FLAMED", "CAVE", "MOON", "DEEP", "SANDBLASTED",
    "EXTRA", "PREMIUM", "CLASSIC", "SELECT", "SUPREME", "SPECIAL", "LIMITED",
    "JUMBO", "PRIME", "SUPERIOR", "RESERVE", "PLUS", "ORIGINAL",
    "QUARTZITE", "QUARTZ", "MARBLE", "DOLOMITE", "GRANITE", "ONYX",
    "SOAPSTONE", "PORCELAIN", "TRAVERTINE", "SLATE",
}
_COLORS = {  # never let a name collapse down to a bare color
    "BLUE", "WHITE", "BLACK", "GREY", "GRAY", "GREEN", "RED", "GOLD", "BROWN",
    "BEIGE", "CREAM", "SILVER", "PINK", "YELLOW", "IVORY", "TAUPE",
}


def base_name(name: str) -> str:
    toks = [t for t in re.split(r"\s+", str(name or "").strip().upper()) if t]
    while toks and (_SIZE_RE.match(toks[-1]) or toks[-1] in _FORMAT):
        toks.pop()  # size + format codes always go
    while len(toks) >= 2 and toks[-1] in _SOFT:
        if len(toks) == 2 and toks[0] in _COLORS:
            break  # protects "Blue Onyx", "Green Onyx", ...
        toks.pop()
    return " ".join(t.capitalize() for t in toks) or str(name).strip()


def upsert_materials(
    db: Session,
    names: list[tuple[str, str | None]],
    source: str,
) -> int:
    """Insert-or-refresh catalog rows. names = [(name, supplier)]. Returns
    how many were new. Callers commit."""
    now = datetime.now(timezone.utc)
    added = 0
    seen_keys: set[str] = set()
    for raw_name, supplier in names:
        name = " ".join(str(raw_name or "").split())
        key = name.lower()
        if len(name) < 2 or key in seen_keys:
            continue
        seen_keys.add(key)
        row = db.get(MaterialCatalog, key)
        if row is None:
            db.add(MaterialCatalog(name_key=key, name=name, supplier=supplier,
                                   source=source, last_seen=now))
            added += 1
        else:
            row.last_seen = now
            if supplier and not row.supplier:
                row.supplier = supplier
    return added


@router.get("/search", dependencies=[Depends(require_role("admin", "manager", "yard"))])
def search_materials(
    q: str = Query(min_length=MIN_QUERY_LEN, max_length=80),
    db: Session = Depends(get_db),
) -> dict:
    tokens = [t for t in q.strip().lower().split() if t]
    if not tokens:
        return {"materials": []}

    # Match against the full stored name, then collapse every hit to its base
    # name and dedup — the dropdown shows one clean name per stone, no
    # supplier, no finish/size/category variants (owner 2026-07-24).
    clauses = [MaterialCatalog.name.ilike(f"%{t}%") for t in tokens]
    hits = db.scalars(
        select(MaterialCatalog).where(or_(*clauses)).limit(600)
    ).all()

    bases: dict[str, str] = {}  # key -> display base name
    for m in hits:
        if not all(t in m.name.lower() for t in tokens):
            continue
        b = base_name(m.name)
        bases.setdefault(b.lower(), b)

    def rank(display: str) -> tuple:
        low = display.lower()
        words = low.split()
        score = 0
        for t in tokens:
            if any(w.startswith(t) for w in words):
                continue
            if t in low:
                score += 1  # substring but not word-prefix
            else:
                score += 2  # token was a size/category we stripped away
        return (score, len(display), low)

    ranked = sorted(bases.values(), key=rank)[:LIMIT]
    return {"materials": [{"name": n} for n in ranked]}


class MaterialIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    supplier: str | None = None


@router.post("", dependencies=[Depends(require_role("admin", "manager", "yard"))])
def add_material(payload: MaterialIn, db: Session = Depends(get_db)) -> dict:
    added = upsert_materials(db, [(payload.name, payload.supplier)], "manual")
    db.commit()
    return {"ok": True, "added": added}


class BulkMaterialsIn(BaseModel):
    source: str = "website"  # 'airtable' | 'excel' | 'website'
    materials: list[MaterialIn]


@router.post("/upsert")
def bulk_upsert(
    payload: BulkMaterialsIn,
    x_pilot_secret: str = Header(default=""),
    db: Session = Depends(get_db),
) -> dict:
    """n8n intake: daily Airtable scan, weekly Excel scan, weekly supplier
    pulls all land here."""
    if x_pilot_secret != get_settings().pilot_hook_secret:
        raise HTTPException(401, detail={"error": "unauthorized"})
    if payload.source not in ("airtable", "excel", "website", "delivery", "manual"):
        raise HTTPException(422, detail={"error": "invalid_source"})
    added = upsert_materials(
        db,
        [(m.name, m.supplier) for m in payload.materials],
        payload.source,
    )
    db.commit()
    return {"ok": True, "added": added, "received": len(payload.materials)}
