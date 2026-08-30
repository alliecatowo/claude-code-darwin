import { existsSync, openSync, closeSync, writeSync, readFileSync, writeFileSync, renameSync, unlinkSync, statSync } from "node:fs"
import type { Paths } from "./env.ts"
import { log } from "./env.ts"

export type Task = {
  id: string
  name: string
  atMs?: number // one-shot fire time
  everyMs?: number // recurring period
  lastRunMs?: number
  fired?: boolean
  payload?: Record<string, unknown>
}

type TaskFile = { version: 1; tasks: Task[] }

/**
 * Durable scheduler — design ported from MiMo-Code cron/: file-backed tasks,
 * cross-process lock, missed one-shot catch-up on start. Fires only while a
 * darwin host process is alive; schedule OS-level spawns for fire-when-dead.
 */
export class Scheduler {
  private timer: ReturnType<typeof setInterval> | undefined
  private running = false
  private readonly p: Paths
  private readonly fire: (task: Task) => Promise<void>
  private readonly intervalMs: number

  constructor(p: Paths, fire: (task: Task) => Promise<void>, intervalMs = 30_000) {
    this.p = p
    this.fire = fire
    this.intervalMs = intervalMs
  }

  start() {
    if (this.timer) return
    // catch-up pass immediately: missed one-shots fire on start
    this.timer = setInterval(() => void this.tick(), this.intervalMs)
    setTimeout(() => void this.tick(), 1_000)
  }

  stop() {
    if (this.timer) clearInterval(this.timer)
    this.timer = undefined
  }

  list(): Task[] {
    return this.read().tasks
  }

  add(task: Task): void {
    const file = this.read()
    const i = file.tasks.findIndex((t) => t.id === task.id)
    if (i >= 0) file.tasks[i] = task
    else file.tasks.push(task)
    this.write(file)
  }

  remove(id: string): boolean {
    const file = this.read()
    const before = file.tasks.length
    file.tasks = file.tasks.filter((t) => t.id !== id)
    this.write(file)
    return file.tasks.length < before
  }

  private async tick() {
    if (this.running) return
    this.running = true
    try {
      const lock = Lock.acquire(this.p.lock)
      if (!lock) return
      const file = this.read()
      const now = Date.now()
      let dirty = false
      for (const t of file.tasks) {
        if (t.atMs !== undefined && !t.fired && t.atMs <= now) {
          t.fired = true
          dirty = true
          await this.safeFire(t)
        } else if (t.everyMs !== undefined && (t.lastRunMs ?? 0) + t.everyMs <= now) {
          t.lastRunMs = now
          dirty = true
          await this.safeFire(t)
        }
      }
      if (dirty) this.write(file)
      lock.release()
    } finally {
      this.running = false
    }
  }

  private async safeFire(t: Task) {
    try {
      await this.fire(t)
    } catch (err) {
      log.error("scheduler task failed", t.name, err)
    }
  }

  private read(): TaskFile {
    try {
      if (!existsSync(this.p.tasks)) return { version: 1, tasks: [] }
      return JSON.parse(readFileSync(this.p.tasks, "utf8")) as TaskFile
    } catch {
      return { version: 1, tasks: [] }
    }
  }

  private write(file: TaskFile) {
    const tmp = this.p.tasks + ".tmp"
    writeFileSync(tmp, JSON.stringify(file, null, 2))
    renameSync(tmp, this.p.tasks)
  }
}

/** Cross-process lock: O_EXCL create, stale after 60s, pid+timestamp inside. */
export class Lock {
  readonly path: string
  private constructor(path: string) {
    this.path = path
  }
  static acquire(path: string, staleMs = 60_000): Lock | null {
    try {
      const fd = openSync(path, "wx")
      writeSync(fd, `${process.pid} ${Date.now()}`)
      closeSync(fd)
      return new Lock(path)
    } catch {
      try {
        const st = statSync(path)
        if (Date.now() - st.mtimeMs > staleMs) {
          unlinkSync(path)
          return Lock.acquire(path, staleMs)
        }
      } catch {
        /* raced away */
      }
      return null
    }
  }
  release() {
    try {
      unlinkSync(this.path)
    } catch {
      /* already gone */
    }
  }
}
