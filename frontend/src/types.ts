// Shapes returned by the backend (see backend/app/schemas.py).
// Dates are ISO strings; `amount` is an exact decimal *string* (e.g. "4850.50")
// so money never rides through floating point on the wire.

export interface PaymentDetails {
  amount: string | null
  payment_method: string | null
  payment_type: string | null
  payer_name: string | null
  invoice_number: string | null
  txn_date: string | null
  check_number: string | null
  caption_name: string | null
  date_received: string | null
  qb_invoice: string | null
  qb_payment_id: string | null
}

// --- decision flow ---

export interface Candidate {
  label: string
  sublabel: string | null
  job_id: string | null
  moraware_url: string | null
}

export interface ItemEvent {
  id: string
  kind: 'system' | 'bot_update' | 'bot_question' | 'decision' | 'comment'
  body: string
  payload: {
    candidates?: Candidate[]
    allowed_freeform?: boolean
    format_hint?: string | null
    choice?: { label: string; job_id: string | null }
    text?: string
    fields?: string[]
  } | null
  actor_email: string | null
  /** kind='decision' only: id of the bot_question event this answers. */
  answers_event_id: string | null
  created_at: string
}

export interface ItemCard {
  item: ReviewItem
  events: ItemEvent[]
}

export interface ReviewItem {
  id: string
  item_type: string
  status: string
  source: string
  airtable_id: string | null
  photo_drive_url: string | null
  photo_path: string | null
  matched_job_id: string | null
  matched_job_name: string | null
  moraware_url: string | null
  match_method: string | null
  created_at: string
  updated_at: string
  last_edited_at: string | null
  last_edited_by: string | null
  payment_details: PaymentDetails | null
  delivery_details?: DeliveryDetails | null
  scan_details?: ScanDetails | null
}

export interface AuditEntry {
  id: string
  review_item_id: string
  item_label: string
  actor_email: string
  action: string
  field: string | null
  old_value: string | null
  new_value: string | null
  created_at: string
}

export interface AuditList {
  entries: AuditEntry[]
  total: number
}

export interface ReviewItemList {
  items: ReviewItem[]
  total: number
}

export interface Stats {
  by_status: Record<string, number>
  total: number
}

export interface AuthUser {
  email: string
  display_name: string | null
  role: string
}

// --- slab deliveries ---

export interface DeliveryMaterial {
  material: string
  finish: string | null
  thickness: string | null
  area: string | null
  slab_count: number | null
  total_sf: number | null
  serials: string | null
  barcodes: string | null
  lot: string | null
  unit_price: number | null
  extended_price: number | null
  stock: boolean
  job_id: string | null
  job_name: string | null
  moraware_url: string | null
  assigned_by?: string | null
}

export interface DeliveryDetails {
  supplier: string | null
  supplier_confidence: string | null
  document_number: string | null
  order_date: string | null
  subtotal: string | null
  tax: string | null
  total: string | null
  slab_count: number | null
  hand_notes: string | null
  validation_note: string | null
  validation_ok: boolean | null
  assignment_mode: 'one' | 'split' | null
  materials: DeliveryMaterial[] | null
  drive_url: string | null
}

export interface JobHit {
  job_id: number
  customer_name: string
  lead_url: string | null
  creation_date: string | null
}

/** Slab scans chapter: one scanned label = one slab ID. */
export interface ScanSlab {
  id: string
  source: 'qr' | 'ocr' | 'manual'
  material?: string | null
}

export interface ScanDetails {
  slab_ids: ScanSlab[] | null
  scanned_date: string | null
}
