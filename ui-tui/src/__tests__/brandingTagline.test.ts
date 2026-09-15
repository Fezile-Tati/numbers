import { describe, expect, it } from 'vitest'

import { BANNER_TAGLINES, ORG_LABEL, taglineFor } from '../components/branding.js'

describe('Banner tagline', () => {
  it('carries no upstream product branding', () => {
    const all = [...Object.values(BANNER_TAGLINES), ORG_LABEL].join(' | ')
    expect(all).not.toMatch(/Nous Research|Hermes|Digital Gods/)
  })

  it('names the verses, not a product', () => {
    expect(BANNER_TAGLINES.full).toContain('Matthew 22:37')
    expect(BANNER_TAGLINES.mid).toContain('Matthew 22:39')
    expect(BANNER_TAGLINES.tiny).toMatch(/Matthew 22:37/)
  })

  it('picks the longest verse that fits without an ellipsis', () => {
    // Pure tiering: the full text is ~110 columns, so a 80-column terminal has
    // to fall back to the shorter verse rather than print a clipped one.
    expect(taglineFor(200)).toBe(BANNER_TAGLINES.full)
    expect(taglineFor(80)).toBe(BANNER_TAGLINES.mid)
    expect(taglineFor(40)).toBe(BANNER_TAGLINES.tiny)
  })

  it('never returns text wider than the columns it was given', () => {
    for (const cols of [34, 46, 57, 58, 64, 72, 90, 112, 120, 200]) {
      expect(taglineFor(cols).length).toBeLessThanOrEqual(Math.max(0, cols - 4))
    }
  })

  it('names the organisation, not the upstream one', () => {
    expect(ORG_LABEL).toBe('Intersession')
  })
})
