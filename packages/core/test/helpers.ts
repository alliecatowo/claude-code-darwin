import { mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { paths, readEnv, type Paths } from "../src/index.ts"

/** A throwaway DARWIN_HOME with the Paths derived from it. */
export function tempHome(): { dir: string; p: Paths; cleanup: () => void } {
  const dir = mkdtempSync(join(tmpdir(), "darwin-test-"))
  const p = paths({ ...readEnv(), home: dir, db: join(dir, "darwin.db") })
  return { dir, p, cleanup: () => rmSync(dir, { recursive: true, force: true }) }
}
