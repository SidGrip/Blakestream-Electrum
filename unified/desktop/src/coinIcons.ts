// Per-coin icon images (the 25.2 coin renders, downscaled to 64px), keyed by
// ticker. Vite bundles each import and hands back the asset URL.
import BLC from './assets/coins/BLC.png'
import BBTC from './assets/coins/BBTC.png'
import ELT from './assets/coins/ELT.png'
import LIT from './assets/coins/LIT.png'
import PHO from './assets/coins/PHO.png'
import UMO from './assets/coins/UMO.png'

export const COIN_ICONS: Record<string, string> = { BLC, BBTC, ELT, LIT, PHO, UMO }
