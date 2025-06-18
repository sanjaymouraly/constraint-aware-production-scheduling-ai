import { spawn, spawnSync } from 'node:child_process'
import { existsSync, rmSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '..', '..')
const backend = path.join(root, 'backend')
const venvPython = process.platform === 'win32'
  ? path.join(root, '.venv', 'Scripts', 'python.exe')
  : path.join(root, '.venv', 'bin', 'python')
const python = existsSync(venvPython) ? venvPython : process.env.E2E_PYTHON ?? 'python3'
const port = process.env.E2E_API_PORT ?? '8000'
// The backend resolves relative SQLite paths against the repository root, so
// the E2E database is addressed with an absolute path to avoid ending up with
// two different files.
const dbPath = path.join(root, 'data', 'e2e.db')
const databaseUrl = `sqlite:///${dbPath.split(path.sep).join('/')}`
const environment = {
  ...process.env,
  DATABASE_URL: databaseUrl,
  ENVIRONMENT: 'e2e',
  SECRET_KEY: 'e2e-insecure-key-with-more-than-32-bytes',
  BOOTSTRAP_ADMIN_USERNAME: 'admin',
  BOOTSTRAP_ADMIN_PASSWORD: 'admin',
  CORS_ORIGINS: `http://127.0.0.1:${process.env.E2E_WEB_PORT ?? '5173'}`,
  RATE_LIMIT_ENABLED: 'false',
  AI_ENABLED: 'true',
  AI_DEFAULT_PROVIDER: 'mock',
  PYTHONPATH: backend,
}

// Reset before the API process creates an engine/opens SQLite. Deleting a DB
// file after startup would leave that process attached to the unlinked inode.
for (const suffix of ['', '-shm', '-wal', '-journal']) {
  rmSync(`${dbPath}${suffix}`, { force: true })
}

const reset = spawnSync(python, ['-m', 'app.cli', 'reset', '--scale', 'small'], {
  cwd: backend,
  env: environment,
  stdio: 'inherit',
})
if (reset.error) throw reset.error
if (reset.status !== 0) process.exit(reset.status ?? 1)

const server = spawn(
  python,
  ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', port],
  { cwd: backend, env: environment, stdio: 'inherit' },
)
server.on('error', (error) => {
  console.error('Could not start the E2E API server:', error)
  process.exit(1)
})

const stop = () => server.kill('SIGTERM')
process.on('SIGINT', stop)
process.on('SIGTERM', stop)
server.on('exit', (code, signal) => {
  if (signal) process.kill(process.pid, signal)
  process.exit(code ?? 0)
})
