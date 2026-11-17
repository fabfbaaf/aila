// 本机测试版：npm run pack:local → dist/local/win-unpacked/艾拉.exe，双击就开，不用开发服务器。
//
// 为什么这样做：正式安装包（把 Python 和 AI 服务打进去，别的电脑也能装）是任务 06，还没做完。
// 测试版只打桌面端，打包时把这个仓库的 aila-ai 目录记进 resources/local-service.json，
// 打出来的程序用它启动 AI 服务（还是 aila-ai\.venv 里的 Python），所以只能在这台电脑上用。
import { execSync } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const aiDir = resolve(root, '..', 'aila-ai')
const python = join(aiDir, '.venv', 'Scripts', 'python.exe')

if (!existsSync(python)) {
  console.error(`找不到 ${python}。先在 aila-ai 里建虚拟环境：py -3.12 -m venv .venv，再装依赖。`)
  process.exit(1)
}

const recordDir = join(root, 'build-local')
mkdirSync(recordDir, { recursive: true })
writeFileSync(join(recordDir, 'local-service.json'), JSON.stringify({ aiDir }, null, 2) + '\n', 'utf8')

const run = (command) => execSync(command, { cwd: root, stdio: 'inherit', shell: true })
run('npm run build')
run('npx electron-builder --config electron-builder.local.yml --win dir --x64 --publish never')
console.log(`\n打好了：${join(root, 'dist', 'local', 'win-unpacked')}\n双击里面的「艾拉.exe」。AI 服务用的是 ${aiDir}`)
