/** @typedef {'lamp'|'table'} TileKind */
/**
 * @typedef {Object} TileDef
 * @property {string} id
 * @property {TileKind} kind
 * @property {string} title
 * @property {string} [variant]
 */

export const state = {
  /** @type {TileDef[]} */
  registry: [],
  /** @type {{ overall: string, checks: object[], ok: boolean, stale: boolean, detail: string } | null} */
  status: null,
  /** @type {{ overall: string, checks: object[], ok: boolean, stale: boolean, detail: string } | null} */
  gitStatus: null,
  /** @type {{ rows: { domain: string, url: string, status: string, checked_at: string }[], stale: boolean } | null} */
  sites: null,
};
