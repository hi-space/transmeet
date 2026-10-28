import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const root = fileURLToPath(new URL('../../', import.meta.url))

// Run real local modules, replacing only explicitly supplied external boundaries.
export function loadTypeScript(relativePath, dependencies = {}) {
  const cache = new Map()
  function load(filename) {
    if (cache.has(filename)) return cache.get(filename)
    if (filename.endsWith('.json')) return JSON.parse(readFileSync(filename, 'utf8'))
    const exports = {}
    cache.set(filename, exports)
    const { outputText } = ts.transpileModule(readFileSync(filename, 'utf8'), {
      fileName: filename,
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        esModuleInterop: true,
      },
    })
    const localRequire = (name) => {
      if (Object.hasOwn(dependencies, name)) return dependencies[name]
      if (name.startsWith('.') || name.startsWith('@/')) {
        const target = name.startsWith('@/')
          ? path.join(root, 'src', name.slice(2))
          : path.resolve(path.dirname(filename), name)
        const resolved = [target, `${target}.ts`, `${target}.tsx`].find(
          (candidate) => existsSync(candidate)
        )
        if (resolved) return load(resolved)
      }
      throw new Error(`Unexpected dependency in test: ${name}`)
    }
    new Function('require', 'exports', outputText)(localRequire, exports)
    return exports
  }
  return load(path.join(root, relativePath))
}
