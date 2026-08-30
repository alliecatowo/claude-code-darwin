/**
 * Ambient types for Bun's built-in `bun:sqlite` — enough for Store/History.
 * The real implementation is Bun's SQLite; this just satisfies `tsc` under Node.
 */
declare module "bun:sqlite" {
  export class Database {
    constructor(filename: string, options?: { readonly?: boolean; readwrite?: boolean; create?: boolean })
    exec(query: string): void
    query<Row = unknown>(query: string): {
      get(...args: unknown[]): Row | undefined
      all(...args: unknown[]): Row[]
      run(...args: unknown[]): { lastInsertRowid: number | bigint; changes: number }
    }
    prepare<Row = unknown>(query: string): {
      get(...args: unknown[]): Row | undefined
      all(...args: unknown[]): Row[]
      run(...args: unknown[]): { lastInsertRowid: number | bigint; changes: number }
    }
    close(): void
  }
}

declare global {
  interface ImportMeta {
    dir: string
    url: string
  }
}
