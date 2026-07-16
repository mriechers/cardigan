/**
 * Remote-preview flag accessor.
 *
 * On the hardened Tailscale-Funnel preview vhost, nginx injects
 * `<script>window.__CARDIGAN_PREVIEW__=true</script>` into index.html so the
 * frontend can hide the config/Settings surface for remote editors. In the
 * normal LAN build the flag is absent (undefined) and every check below is
 * false, so the app behaves byte-identically to today.
 *
 * This is cosmetic defense-in-depth only — the nginx config write-block is the
 * real enforcement gate. The flag just avoids showing remote editors a
 * visibly-broken Settings surface.
 */

declare global {
  interface Window {
    __CARDIGAN_PREVIEW__?: boolean
  }
}

/**
 * True only when the app is served through the hardened remote preview vhost.
 * False/undefined (and therefore false) in the normal LAN build.
 */
export function isRemotePreview(): boolean {
  return typeof window !== 'undefined' && window.__CARDIGAN_PREVIEW__ === true
}
