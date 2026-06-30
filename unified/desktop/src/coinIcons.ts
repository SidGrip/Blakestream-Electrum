// Per-coin icon images keyed by ticker. Vite eagerly globs assets/coins/<TICKER>.png, so any coin
// whose icon is present is bundled automatically — there is no per-coin import to keep in sync.
// Each glob value is the bundled asset URL (import: 'default').
const modules = import.meta.glob('./assets/coins/*.png', {
  eager: true,
  import: 'default',
}) as Record<string, string>

export const COIN_ICONS: Record<string, string> = Object.fromEntries(
  Object.entries(modules).map(([path, url]) => [
    path.split('/').pop()!.replace(/\.png$/, ''),
    url,
  ]),
)
