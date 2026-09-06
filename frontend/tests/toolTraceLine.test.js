import { describe, expect, it } from 'vitest'
import { toolTraceLine } from '../src/toolTraceLine.js'

describe('toolTraceLine', () => {
  it('formats a select with a single argument', () => {
    const record = {
      name: 'source_flights_select', label: 'Flight records', arguments: { values: ['VY3003'] },
      result: 'city\nParis\n', rows: 1, error: false,
    }

    expect(toolTraceLine(record)).toBe('Searched Flight records for "VY3003" · 1 row')
  })

  it('formats a select with two arguments, plural rows', () => {
    const record = {
      name: 'source_flights_select', label: 'Flight records', arguments: { values: ['VY3003', '2026-06-01'] },
      result: 'code,date\nVY3003,2026-06-01\nVY3003,2026-06-02\n', rows: 2, error: false,
    }

    expect(toolTraceLine(record)).toBe('Searched Flight records for "VY3003", "2026-06-01" · 2 rows')
  })

  it('formats an update from its own fields, never the row-filter values', () => {
    const record = {
      name: 'source_booking_update', label: 'Booking', arguments: { values: ['ABC123'], fields: { pnr: 'ABC123' } },
      result: '1 row updated', rows: 0, error: false,
    }

    expect(toolTraceLine(record)).toBe('Updated Booking for pnr="ABC123" · 0 rows')
  })

  it('reports zero rows for an empty result', () => {
    const record = {
      name: 'source_flights_select', label: 'Flight records', arguments: { values: ['nope'] },
      result: '', rows: 0, error: false,
    }

    expect(toolTraceLine(record)).toBe('Searched Flight records for "nope" · 0 rows')
  })

  it('reports failed instead of a row count on error', () => {
    const record = {
      name: 'source_flights_select', label: 'Flight records', arguments: { values: ['x'] },
      result: 'error: response too long.', rows: 0, error: true,
    }

    expect(toolTraceLine(record)).toBe('Searched Flight records for "x" · failed')
  })

  it('falls back to the raw tool name when there is no label', () => {
    const record = { name: 'source_flights_select', label: null, arguments: { values: [] }, result: '', rows: 0, error: false }

    expect(toolTraceLine(record)).toBe('Searched source_flights_select · 0 rows')
  })
})
