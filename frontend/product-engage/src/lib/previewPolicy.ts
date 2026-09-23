/**
 * The `Content-Security-Policy` prepended to a campaign's HTML before it is put in a sandboxed
 * preview frame (`routes/CampaignPreview.tsx` explains why any of this is needed).
 *
 * Nothing loads except the markup's own inline styles and, only when the person previewing has
 * turned the switch on, images over `https:`. A fixed string per switch position — the test pins
 * both exactly, so a loosened policy fails a test rather than shipping.
 */
export function framePolicy(remoteImages: boolean): string {
  const images = remoteImages ? "https:" : "'none'";
  return `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${images}; style-src 'unsafe-inline'">`;
}
