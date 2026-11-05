# 随机图片 HTTP 服务（Render 部署版）

一个“每次请求都随机返回一张图片”的 Flask 服务，已改造成可以直接把 GitHub 仓库连接到
[Render](https://render.com) 完成构建与部署的版本。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 随机返回 `images/` 中的一张图片，响应头 `X-Image-Name` 带文件名 |
| GET | `/healthz` | 健康检查，返回 `{"status":"ok","image_count":18}`，Render 用它判断服务是否就绪 |

支持格式：`.jpg` `.jpeg` `.png` `.gif` `.webp` `.bmp`。

## 设计思路（代码讲解）

### 1. 把“自己 fork 进程”换成“交给 gunicorn”

旧版主进程先 `bind(8000)`，再 `fork` 出 N 个 worker 共享这个 socket。这个做法适合演示
多进程共享端口，但放到 Render 上会失败：

- Render 通过 `$PORT` 环境变量动态分配端口，应用必须监听 `$PORT`，写死 8000 收不到流量；
- 平台的健康检查、日志、重启、扩缩容都以“启动命令拉起的进程组”为单位，应用自己再
  fork 会与平台的管理逻辑重叠；
- 多进程和崩溃重启本来就可以由 gunicorn 完成，重复实现只会增加故障点。

因此新版只做一件事：在模块级导出 WSGI 应用对象 `app`，由 gunicorn 启动：

```bash
gunicorn server:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
```

本地没有 gunicorn（Windows 不支持）时，`python server.py` 用 Flask 内置服务器跑，读取
`PORT`（默认 8000）和 `HOST`（默认 0.0.0.0）。这样同一份代码既能本地调试，也能云端部署。

### 2. 图片目录定位

用 `Path(__file__).resolve().parent` 定位项目根目录，默认图片目录是 `images/`。可以通过
`IMAGE_DIR` 环境变量覆盖；相对路径按项目根目录解析，绝对路径直接使用。Render 的代码目录
是 `/opt/render/project/src`，用这种基于文件位置的写法，不会因为启动时的工作目录不同而
找不到图片。

### 3. 扫描、过滤与缓存

启动后首次请求时扫描目录，按扩展名过滤、按文件名排序，把结果缓存到内存中，之后每个请求
直接复用。缓存为空时（例如部署时目录还没准备好、或者图片被清空）会重新扫描，所以补图后
不需要重启服务。随机选中的文件若已被删除，会从缓存中移除并换一张重试。

### 4. 随机返回与防缓存

用 `random.choice` 选图，`send_file` 以流式方式返回，不会把整张图读进内存，适合并发请求。
浏览器默认会缓存图片，导致刷新时看起来“总是同一张”。所以响应显式加上：

```
Cache-Control: no-store, max-age=0
```

保证每次刷新都重新请求服务，随机效果才真实可见。`X-Image-Name` 响应头方便在浏览器
开发者工具或 Postman 中确认本次返回的是哪张图。

### 5. 健康检查与跨域

`/healthz` 返回 200 和图片数量，Render 的 `healthCheckPath: /healthz` 会周期性检查，部署
成功与否一目了然。全局 `after_request` 添加 `Access-Control-Allow-Origin: *`，前端页面
可以跨域直接引用这个图片接口。

### 6. 异常处理

图片目录不存在、为空或不可读时，接口返回 **404 + JSON**（包含目录路径和提示），而不是
500；目录不可读只在日志中告警。这样异常场景也有明确的、可演示的响应。

## 目录结构

```
random_image/
├─ server.py          # HTTP 服务（WSGI 入口 app）
├─ requirements.txt   # Python 依赖（Flask + gunicorn）
├─ render.yaml        # Render Blueprint 部署配置
├─ images/            # 图片目录（示例 18 张 jpg，随仓库提交）
├─ README.md
└─ .gitignore
```

## 开发与运行环境

- Python 3.11+（Render 构建默认使用 3.13.x，`render.yaml` 中显式指定 3.13.5）
- Flask 3.1.2、gunicorn 23.0.0（gunicorn 只在 Linux/Render 上运行）
- 本地：Windows / macOS / Linux 均可
- 部署：GitHub 仓库 + Render Web Service（免费套餐即可）

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 启动（默认 0.0.0.0:8000，可设 PORT / IMAGE_DIR）
python server.py
```

访问 <http://127.0.0.1:8000/>，每次刷新都会换一张图；健康检查：
<http://127.0.0.1:8000/healthz>。

用 PowerShell 验证响应头：

```powershell
(Invoke-WebRequest http://127.0.0.1:8000/ -Method Head).Headers
```

## 部署到 Render（通过 GitHub）

### 第 0 步：把代码推送到 GitHub

Render 只认仓库里的代码，所以先把 `server.py`、`requirements.txt`、`render.yaml` 和
`images/` 全部提交（`images/` 没有被 `.gitignore` 忽略，会正常提交）：

```bash
git add -A
git commit -m "feat: 适配 Render 部署"
git push origin master
```

### 方式一：Blueprint（推荐，配置已写好）

1. 登录 Render，点击 **New + → Blueprint**；
2. 选择 GitHub 仓库 `random_image`（首次使用需要授权 Render 访问该仓库）；
3. Render 自动读取 `render.yaml`，确认服务 `random-image`、分支 `master`；
4. 点击 **Apply**，等待 Build 和 Deploy 完成；
5. 访问控制台给出的地址：`https://random-image-xxxx.onrender.com/`。

`render.yaml` 中已经固定了以下配置，不需要手工填写：

| 配置项 | 值 |
| --- | --- |
| Runtime | `python` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn server:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120 --access-logfile -` |
| Health Check Path | `/healthz` |
| Plan / Region | `free` / `singapore` |

### 方式二：手动创建 Web Service

不想要 Blueprint 时，也可以在 Render 控制台手动创建：

1. **New + → Web Service**，连接 GitHub 仓库 `random_image`；
2. Language 选择 **Python 3**，Branch 选择 `master`；
3. Build Command 填 `pip install -r requirements.txt`；
4. Start Command 填
   `gunicorn server:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120 --access-logfile -`；
5. Health Check Path 填 `/healthz`；
6. 可选：环境变量 `PYTHON_VERSION=3.13.5`、`IMAGE_DIR=images`；
7. 点击 **Create Web Service**。

### 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | 8000 | Render 自动注入，**不要手动设置** |
| `IMAGE_DIR` | `images` | 图片目录，相对项目根目录或绝对路径 |
| `PYTHON_VERSION` | 3.13.5 | Render 构建使用的 Python 版本 |
| `HOST` | 0.0.0.0 | 仅 `python server.py` 本地运行时使用 |

### 自动部署

`render.yaml` 里 `autoDeploy: true`。之后每次 `git push` 到 `master`，Render 都会自动重新
构建并发布，替换 `images/` 里的图片后 push 即可生效。

> 关于“打包”：本方案不需要在本地打 wheel/zip。Render 以 Git 仓库为构建源，在云端执行
> `pip install -r requirements.txt`，构建产物由平台管理；`requirements.txt` 就是依赖清单。

## 部署后验证

### 浏览器

打开 `https://<服务名>.onrender.com/`，每次刷新都会显示不同图片；F12 打开 Network，
可以看到响应头 `X-Image-Name` 在变化、`Cache-Control: no-store`。

### Postman（正常场景）

1. 新建请求，方法 `GET`，URL 填 `https://<服务名>.onrender.com/`；
2. 点击 **Send**，状态码 200，`Content-Type: image/jpeg`；
3. Body 里选择 **Preview** 查看图片，或 **Send and Download** 保存文件。

### curl（正常场景）

```bash
curl -i https://<服务名>.onrender.com/
```

```
HTTP/1.1 200 OK
Content-Type: image/jpeg
Content-Length: 479374
Cache-Control: no-store, max-age=0
X-Image-Name: beauty01.jpg
```

健康检查：

```bash
curl -s https://<服务名>.onrender.com/healthz
# {"status":"ok","image_count":18}
```

### 异常场景（目录中没有图片）

本地模拟：把 `IMAGE_DIR` 指向一个空目录再启动。

```powershell
New-Item -ItemType Directory -Force images_empty | Out-Null
$env:IMAGE_DIR = "images_empty"
python server.py
```

浏览器或 Postman 请求 `http://127.0.0.1:8000/`：

```
HTTP/1.1 404 NOT FOUND
Content-Type: application/json

{
  "error": "no images available",
  "image_dir": "D:\\...\\random_image\\images_empty",
  "hint": "把图片放进 IMAGE_DIR 目录，或调整 IMAGE_DIR 环境变量"
}
```

线上模拟：在 Render 的环境变量里把 `IMAGE_DIR` 改成一个不存在的目录并重新部署，再请求
`/`，即可得到同样的 404。

## 与旧版对比

| 项目 | 旧版 server.py | 新版 |
| --- | --- | --- |
| 监听端口 | 写死 8000 | `$PORT`（Render 注入） |
| 多进程 | 手工 fork + 传递 socket | gunicorn `--workers` |
| 崩溃重启 | 主进程轮询重启 | gunicorn / Render 负责 |
| 部署方式 | 仅本地运行 | GitHub → Render 自动构建 |
| 浏览器缓存 | 无控制 | `no-store`，刷新必换图 |
| 健康检查 | 无 | `/healthz` |
| 路径基准 | 相对当前工作目录 | 相对 `server.py` 所在目录 |

## 常见问题

- **构建失败，提示 Python 版本不可用**：把 `render.yaml` 里的 `PYTHON_VERSION` 改成
  Render 支持的版本（见 <https://render.com/docs/python-version>），或删除该变量使用默认版本。
- **Blueprint 提示找不到 master 分支**：如果仓库默认分支是 `main`，把 `render.yaml` 里的
  `branch: master` 改成 `branch: main` 再同步一次。
- **访问 `/` 返回 404 且 `image_count` 为 0**：`images/` 没有提交到 GitHub，或
  `IMAGE_DIR` 指向了错误的目录。
- **免费实例第一次访问很慢**：免费套餐 15 分钟无请求会休眠，冷启动大约 30–60 秒。
- **本地 `pip install gunicorn` 成功但跑不起来**：gunicorn 不支持 Windows，本地请用
  `python server.py`；gunicorn 只在 Linux 的 Render 上使用。
- **想确认线上跑的是哪张图**：看响应头 `X-Image-Name`，或浏览器 Network 面板。

## 附：原始实验要求

> 编写一个 HTTP 服务，该服务随机返回服务器中某目录的图片。目的：模拟拍照后返回图像
> 场景。应用：将开发完成服务布置到云服务器上，后续实训实验可以调用该服务。测试：浏览器
> 或 Postman。考核文档：简易说明设计思路（代码讲解，不是代码拷贝）、开发与运行环境、
> 打包与部署操作步骤、浏览器或 Postman 调用截图，正常 + 异常场景各一张。
