#!/usr/bin/env bash
# demo.sh — LINGSHU 沙盒实验舱 · macOS / Linux 一键启动
#
#   bash demo.sh                 # 检查 Node → 建沙盒 profile → 预置「阿福」记忆 → 进入对话
#   bash demo.sh --clean         # 只删除沙盒 profile 与 ./.lingshu-demo-data
#   bash demo.sh --help          # 全部参数与隔离口径
#
# 设计边界：
#   · 本脚本只做"找到 Node 并把参数交给 node demo/bootstrap.mjs"，业务逻辑在 Node 里只有一份；
#   · demo.bat 是它的 Windows 对等实现，两边行为一致；
#   · 不读任何密钥、不写 $DSH_HOME 里的真实数据（唯一例外是可抛弃 profile lingshu-demo-profile）。
set -eu

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$SCRIPT_DIR"

find_node() {
  # 1) 显式指定
  if [ -n "${LINGSHU_NODE:-}" ] && [ -x "${LINGSHU_NODE}" ]; then
    printf '%s\n' "$LINGSHU_NODE"
    return 0
  fi
  # 2) PATH
  if command -v node >/dev/null 2>&1; then
    command -v node
    return 0
  fi
  # 3) DSH 自带运行时（与 DSH_HOME 口径一致）
  local dsh_home="${DSH_HOME:-$HOME/.dsh}"
  local cand
  for cand in "$dsh_home"/dsh-runtimes/*/dependencies/node/bin/node \
              "$dsh_home"/dsh-runtimes/*/dependencies/node/bin/node.exe; do
    if [ -x "$cand" ]; then
      printf '%s\n' "$cand"
      return 0
    fi
  done
  # 4) 常见安装位置
  for cand in /usr/local/bin/node /opt/homebrew/bin/node /usr/bin/node; do
    if [ -x "$cand" ]; then
      printf '%s\n' "$cand"
      return 0
    fi
  done
  return 1
}

NODE_EXE="$(find_node || true)"
if [ -z "${NODE_EXE:-}" ]; then
  echo "[demo] 找不到 Node（需要 >= 20.10）。" >&2
  echo "       安装：https://nodejs.org/（或用 nvm/fnm）；已装但不在 PATH 时：" >&2
  echo "         LINGSHU_NODE=/abs/path/to/node bash demo.sh" >&2
  exit 2
fi

if ! "$NODE_EXE" -e 'const [m,n]=process.versions.node.split(".").map(Number); process.exit((m>20)||(m===20&&n>=10)?0:1)'; then
  echo "[demo] Node 版本过低：$("$NODE_EXE" --version)（需要 >= 20.10，建议 22/24 LTS）。" >&2
  exit 2
fi

exec "$NODE_EXE" "$SCRIPT_DIR/demo/bootstrap.mjs" "$@"
