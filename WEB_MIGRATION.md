# Bili23 Web 服务器版

当前分支新增了独立的 FastAPI 后端和 Vue 3 前端，用于把桌面 Qt 客户端迁移为可在服务器运行的网页管理台。

## 环境

- Python 3.12 可用；当前机器没有 `conda`，系统 Python 也缺少 `venv/ensurepip`。
- Node.js 22 和 npm 10 可用。
- 当前验证方式是把 Python 依赖安装到 `backend/.deps`，不污染系统 Python。
- 当前机器未检测到 `ffmpeg`。下载可以进行，但音视频自动合并需要安装 `ffmpeg` 或关闭“合并音视频”。

## 安装

```bash
cd Bili23-Downloader
python3 -m pip install -r backend/requirements.txt --target backend/.deps --index-url https://pypi.org/simple
cd frontend
npm install
npm run build
```

## 启动

后端：

```bash
cd Bili23-Downloader
PYTHONPATH=backend/.deps:backend python3 backend/run.py
```

前端开发服务：

```bash
cd Bili23-Downloader/frontend
npm run dev
```

访问：

- 后端 API：`http://127.0.0.1:8233`
- 前端开发页：`http://127.0.0.1:5173`

首次打开前端时会要求初始化管理员账号。初始化后可以扫码登录 B 站、解析 BV 视频、创建下载任务、查看进度、暂停/继续/取消任务、修改设置、查看下载文件和日志。

## 已覆盖的 Qt 客户端能力映射

- Web 管理台账号初始化与登录。
- B 站账号状态、扫码登录。
- BV 视频解析、分 P 列表展示、批量选择。
- 服务端“全部下载”批量入口。
- 服务端下载队列、并发控制、暂停、继续、取消。
- 下载目录、画质、命名模板、附加文件等设置。
- 封面、弹幕 XML、元数据 JSON 下载。
- 下载文件列表与后台日志。

## 当前限制

- 已实现 BV/普通视频主链路；番剧、课程、收藏夹、稍后再看、历史记录等 Qt 客户端入口还需要继续按同一服务层扩展。
- 当前未引入 WebSocket，前端用轮询刷新任务进度。
- 当前机器没有 `ffmpeg`，所以实测只验证了视频流写入文件；安装 `ffmpeg` 后即可执行音视频合并。
