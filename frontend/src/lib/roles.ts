// The role matrix (STAGE2_BUILD_PLAN.md §3) — single source of truth for
// what the UI shows. The backend enforces the same matrix independently.

export type Area = 'payments' | 'slabs' | 'supply' | 'leads' | 'followups'

const MATRIX: Record<Area, string[]> = {
  payments: ['admin', 'manager'],
  slabs: ['admin', 'manager', 'yard'],
  supply: ['admin', 'manager', 'yard'],
  leads: ['admin', 'manager'],
  followups: ['admin'],
}

export function canSee(area: Area, role: string | undefined | null): boolean {
  return !!role && MATRIX[area].includes(role)
}

export const PAYMENTS_ROLES = MATRIX.payments
export const SLAB_SCAN_ROLES = MATRIX.slabs
