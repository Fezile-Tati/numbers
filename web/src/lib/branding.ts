/**
 * Numbers branding accessor.
 *
 * The server injects `window.__NUMBERS_BRANDING__` into index.html (see
 * `hermes_cli/web_server.py::_numbers_branding_json`). Reading it here — in a
 * Numbers-owned module upstream never ships — keeps every brand string/URL as
 * server config: the SPA components only reference `getBranding()`, so future
 * upstream merges never conflict on branding, and a deploy can rebrand via
 * `NUMBERS_BRAND_*` env vars without rebuilding the bundle.
 */

export interface NumbersDocs {
  /** "pdf" → render a Download card served by `url`; "iframe" → external docs iframe. */
  mode: "pdf" | "iframe";
  url: string;
}

export interface NumbersBranding {
  /** Short product name. */
  name: string;
  /** Sidebar / header wordmark, e.g. "NUMBERS 21:4-9". */
  wordmark: string;
  /** Footer organisation label. */
  org: string;
  /** Footer organisation link target. */
  orgUrl: string;
  docs: NumbersDocs;
}

declare global {
  interface Window {
    __NUMBERS_BRANDING__?: Partial<NumbersBranding> & {
      docs?: Partial<NumbersDocs>;
    };
  }
}

/** Upstream-safe defaults, used when the server injection is absent (e.g. a
 *  dev build served without the bootstrap script). */
const DEFAULTS: NumbersBranding = {
  name: "Numbers",
  wordmark: "NUMBERS 21:4-9",
  org: "Intersession",
  orgUrl: "https://172.20.10.12:3000/",
  docs: { mode: "pdf", url: "/api/numbers/docs.pdf" },
};

export function getBranding(): NumbersBranding {
  const b = (typeof window !== "undefined" && window.__NUMBERS_BRANDING__) || {};
  return {
    name: b.name ?? DEFAULTS.name,
    wordmark: b.wordmark ?? DEFAULTS.wordmark,
    org: b.org ?? DEFAULTS.org,
    orgUrl: b.orgUrl ?? DEFAULTS.orgUrl,
    docs: {
      mode: b.docs?.mode ?? DEFAULTS.docs.mode,
      url: b.docs?.url ?? DEFAULTS.docs.url,
    },
  };
}
