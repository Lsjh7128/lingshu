# LINGSHU 演示沙盒镜像（可抛弃容器）
#
# ⚠️ 未在本机验证：开发机没有 Docker。本文件是按运行时契约写出的构建配方，
#    首次真实构建请对照 docs/demo-sandbox.md §10「Docker 路径」逐条核对。
#
# 目标：只从公共 npm registry（@deepseek-ai/dsh 的 publishConfig access=public）
#      与本仓库源码构建，不依赖宿主已装 DSH，也不读写宿主的 $DSH_HOME。
FROM node:24-slim

# DSH CLI 版本可覆盖：docker build --build-arg DSH_VERSION=<ver> ...
ARG DSH_VERSION=0.2.1-rc.2

# 中文输出 / Python 子进程编码（slim 镜像默认 POSIX locale，会让中文变 ?）
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONIOENCODING=utf-8 \
    npm_config_store_dir=/home/demo/.pnpm-store

# DSH CLI 与 pnpm 必须全局可用：plugin add 依赖 pnpm 在 PATH。
RUN npm install --no-fund --no-audit -g \
        "@deepseek-ai/dsh@${DSH_VERSION}" \
        pnpm \
    && npm cache clean --force \
    && node --version \
    && (command -v dsh || true)

# 非 root 运行；容器内的 DSH_HOME 与宿主的 C:\Users\...\.dsh 完全无关。
RUN useradd --create-home --shell /bin/bash demo
ENV DSH_HOME=/home/demo/.dsh
# 固定指向全局安装的 CLI 入口（不依赖 npm bin 链接名，也不依赖宿主 DSH_BIN）
ENV DSH_BIN=/usr/local/lib/node_modules/@deepseek-ai/dsh/lib/bin.js

WORKDIR /app

# 只复制演示所需内容（构建上下文由仓库根 .dockerignore 白名单裁剪）。
# 不复制 plugin/node_modules：Windows junction 无法进镜像，
# 插件运行时的依赖链接由 demo bootstrap 在容器内自行补建。
COPY demo/ /app/demo/
COPY plugin/ /app/plugin/
COPY demo.sh demo.bat /app/

RUN chown -R demo:demo /app

USER demo

# 交互式演示入口；stdin/tty 由 docker compose run（stdin_open/tty）提供。
CMD ["bash", "demo.sh"]
