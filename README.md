# joko-image 生图服务

`joko-image` 是一个基于 React + FastAPI 的生图网站项目。前端负责生图、改图、历史、任务列表、账单、系统设置等页面；后端负责用户会话、配置保存、异步生图任务、图片落盘、账单记录和对接 sub2api。

当前对接的是 sub2api 的 OpenAI 兼容接口，默认模型为：

```text
gpt-image-2
```

## 功能概览

- 文生图：调用 sub2api `/v1/images/generations`
- 改图：调用 sub2api `/v1/images/edits`
- 多参考图改图：改图接口支持上传多张参考图
- 异步任务：提交后进入任务中心，页面刷新后仍可查看任务状态
- 历史记录：成功和失败任务都会保存
- 游客模式：使用浏览器 Cookie 隔离游客历史和配置
- 用户模式：注册、登录、托管 API Key、余额和用量查询走部署者自己的 sub2api
- 案例库：从 GitHub README 案例源同步，也支持管理员配置多个案例源
- 账单明细：系统托管 Key 尝试读取 sub2api 实际扣费；手动 Key 使用本地价格估算并标记“估算”

## 服务架构

推荐生产部署使用 Docker Compose 跑 image 服务本身，宿主机 Nginx 只负责公网 HTTPS 入口。

```text
用户浏览器
  |
  v
Cloudflare / DNS
  |
  v
宿主机 Nginx :443
/etc/nginx/sites-enabled/image.get-money.locker.conf
  |
  v
127.0.0.1:18080
  |
  v
Docker web 容器 Nginx
deploy/docker-nginx.conf
  |
  ├─ /          -> React 静态页面
  ├─ /api/*     -> Docker backend 容器 FastAPI :8000
  └─ /storage/* -> Docker backend 容器 FastAPI :8000
```

如果你的服务器没有其它站点，也可以让 Docker web 容器直接占用 `80/443`。当前服务器已有多个站点，所以保留宿主机 Nginx 作为统一入口。

## 对接 sub2api

部署前需要先准备好你自己的 sub2api 服务，并确认：

- sub2api 可以从 image 后端访问
- sub2api 已支持 `gpt-image-2`
- 用户注册、登录接口可用
- API Key 分组可用
- OpenAI 兼容接口可用

image 后端使用两类 sub2api 地址：

```text
SUB2API_BASE_URL       OpenAI 兼容接口地址，默认用于生图、改图、余额
SUB2API_AUTH_BASE_URL  sub2api 管理接口地址，默认用于登录、注册、Key、用量明细
```

如果 sub2api 在宿主机上通过 `9878:8080` 暴露，Docker 部署时保持默认即可：

```env
SUB2API_BASE_URL=http://host.docker.internal:9878/v1
SUB2API_AUTH_BASE_URL=http://host.docker.internal:9878
```

如果 image 和 sub2api 在同一个 Docker 网络里，可以改成 sub2api 的服务名，例如：

```env
SUB2API_BASE_URL=http://sub2api:8080/v1
SUB2API_AUTH_BASE_URL=http://sub2api:8080
```

如果不是 Docker 部署，而是本机进程直接运行，可以使用：

```env
SUB2API_BASE_URL=http://127.0.0.1:9878/v1
SUB2API_AUTH_BASE_URL=http://127.0.0.1:9878
```

### 实际请求流程

文生图：

```text
前端 POST /api/images/generate
  -> FastAPI 创建异步任务
  -> 后台任务调用 SUB2API_BASE_URL + /images/generations
  -> 图片保存到 backend/storage/images
  -> 历史和账单写入 SQLite
```

改图：

```text
前端 POST /api/images/edit
  -> multipart 上传一张或多张 image
  -> FastAPI 保存上传图到 backend/storage/uploads
  -> 后台任务调用 SUB2API_BASE_URL + /images/edits
  -> 图片保存到 backend/storage/images
  -> 历史和账单写入 SQLite
```

用户注册登录：

```text
前端 POST /api/auth/register 或 /api/auth/login
  -> FastAPI 调用 SUB2API_AUTH_BASE_URL + /api/v1/auth/*
  -> 登录成功后读取或创建用户 API Key
  -> 绑定到当前 image 用户
```

余额和账单：

```text
余额       -> SUB2API_BASE_URL + /v1/usage
实际扣费   -> SUB2API_AUTH_BASE_URL + /api/v1/usage
本地估算   -> IMAGE_PRICE_1K_USD / IMAGE_PRICE_2K_USD / IMAGE_PRICE_4K_USD
```

## Docker 部署

### 1. 准备配置

复制部署环境变量示例：

```bash
cp deploy/joko-image.env.example .env
```

编辑 `.env`，至少确认这些值：

```env
SUB2API_BASE_URL=http://host.docker.internal:9878/v1
SUB2API_AUTH_BASE_URL=http://host.docker.internal:9878
CORS_ORIGINS=https://image.get-money.locker
COOKIE_SECURE=true
```

如果你的域名不是 `image.get-money.locker`，需要同时改：

- `.env` 里的 `CORS_ORIGINS`
- 宿主机 Nginx 配置里的 `server_name`
- 如有 Cloudflare，确认 DNS 指向当前服务器

### 2. 启动容器

```bash
docker compose up -d --build
docker compose ps
```

正常状态应类似：

```text
backend   Up healthy
web       127.0.0.1:18080->80
```

### 3. 配置宿主机 Nginx

项目内提供了宿主机 Nginx 示例：

```text
deploy/nginx-image.get-money.locker.conf
```

复制到 Nginx 站点目录：

```bash
cp deploy/nginx-image.get-money.locker.conf /etc/nginx/sites-enabled/image.get-money.locker.conf
nginx -t
systemctl reload nginx
```

这个配置只做一件事：把公网域名流量转发给 Docker web 容器。

```nginx
location / {
    proxy_pass http://127.0.0.1:18080;
}
```

### 4. 验证

```bash
curl -k https://image.get-money.locker/api/health
curl -k -I https://image.get-money.locker/
docker compose logs --tail=100 backend web
```

`/api/health` 返回以下内容说明后端正常：

```json
{"ok":"true"}
```

## 环境变量

### sub2api 对接

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SUB2API_BASE_URL` | `http://host.docker.internal:9878/v1` | OpenAI 兼容接口地址，生图、改图、余额使用 |
| `SUB2API_AUTH_BASE_URL` | `http://host.docker.internal:9878` | sub2api 管理接口地址，注册、登录、Key、用量明细使用 |
| `SUB2API_USAGE_PATH` | `/v1/usage` | 余额查询路径 |

### 生图配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `IMAGE_MODEL` | `gpt-image-2` | 默认模型 |
| `IMAGE_SIZE` | `2K` | 默认尺寸档位 |
| `IMAGE_QUALITY` | `auto` | 默认质量 |
| `PROVIDER_TIMEOUT_SECONDS` | `300` | 请求 sub2api 的超时时间 |

### 本地估算价格

| 变量 | 默认值 | 说明 |
|---|---|---|
| `IMAGE_PRICE_1K_USD` | `0.134` | 1K 本地估算价格 |
| `IMAGE_PRICE_2K_USD` | `0.201` | 2K 本地估算价格 |
| `IMAGE_PRICE_4K_USD` | `0.268` | 4K 本地估算价格 |

说明：

- 登录用户使用系统托管 Key 时，优先读取 sub2api `/api/v1/usage` 的 `actual_cost`
- 登录用户手动填写 Key、游客手动填写 Key 时，无法确定真实扣费，页面显示本地估算并标记“估算”

### 数据和存储

| 变量 | Docker 默认值 | 说明 |
|---|---|---|
| `DATABASE_PATH` | `/data/app.sqlite3` | SQLite 数据库路径 |
| `STORAGE_DIR` | `/storage` | 图片和上传文件存储目录 |

Docker Compose 默认挂载：

```text
./backend/data    -> /data
./backend/storage -> /storage
```

不要删除这两个宿主机目录，否则历史、账单、图片文件会丢失。

### 会话和跨域

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CORS_ORIGINS` | `https://image.get-money.locker,http://127.0.0.1:18080` | 允许访问 API 的前端来源 |
| `COOKIE_SECURE` | `true` | HTTPS 部署必须为 `true` |
| `SESSION_COOKIE_NAME` | `cybergen_session` | 登录用户 Cookie 名 |
| `GUEST_COOKIE_NAME` | `cybergen_guest` | 游客 Cookie 名 |
| `SESSION_TTL_SECONDS` | `2592000` | 登录会话有效期，默认 30 天 |
| `GUEST_TTL_SECONDS` | `31536000` | 游客身份有效期，默认 365 天 |

### 案例源同步

| 变量 | 默认值 | 说明 |
|---|---|---|
| `INSPIRATION_SOURCE_URLS` | 两个默认 GitHub 案例源 | 案例源列表，逗号分隔 |
| `INSPIRATION_SYNC_INTERVAL_SECONDS` | `21600` | 自动同步间隔，默认 6 小时 |
| `INSPIRATION_SYNC_ON_STARTUP` | `true` | 启动时是否同步案例 |

默认案例源：

```text
https://raw.githubusercontent.com/EvoLinkAI/awesome-gpt-image-2-prompts/main/README.md
https://raw.githubusercontent.com/YouMind-OpenLab/awesome-gpt-image-2/main/README.md
```

管理员也可以在系统设置里配置案例源，一行一个。

## 尺寸和比例

前端让用户选择尺寸档位和比例，后端实际传给 sub2api 的是 `WIDTHxHEIGHT`。

当前规则：

- 宽高必须都能被 16 整除
- 小于上游最低像素预算会被拒绝
- 4K 正方形暂不开放，因为上游对超大正方形限制较多

常用映射：

| 档位 | 16:9 | 9:16 | 1:1 |
|---|---|---|---|
| `1K` | `2048x1152` | `1152x2048` | `1088x1088` |
| `2K` | `2560x1440` | `1440x2560` | `1440x1440` |
| `4K` | `3840x2160` | `2160x3840` | 暂不支持 |

## 本地开发

```bash
npm install
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
npm run backend
npm run dev
```

本地开发地址：

```text
http://127.0.0.1:3000
```

Vite 会把 `/api` 和 `/storage` 代理到本地 FastAPI：

```text
http://127.0.0.1:8000
```

本地开发可以使用 `.env.example` 里的默认值：

```env
SUB2API_BASE_URL=http://127.0.0.1:9878/v1
SUB2API_AUTH_BASE_URL=http://127.0.0.1:9878
COOKIE_SECURE=false
```

## 常用运维命令

```bash
docker compose ps
docker compose logs -f
docker compose logs -f backend
docker compose restart
docker compose up -d --build
docker compose down
```

查看宿主机 Nginx：

```bash
nginx -t
systemctl reload nginx
nginx -T | grep -n "image.get-money.locker" -A20
```

查看端口：

```bash
ss -ltnp | grep -E ':80|:443|:18080'
```

## 重要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/auth/public-settings` | 读取 sub2api 公开设置 |
| `GET` | `/api/auth/session` | 当前登录/游客状态 |
| `POST` | `/api/auth/register` | 注册 |
| `POST` | `/api/auth/login` | 登录 |
| `POST` | `/api/auth/logout` | 退出 |
| `GET` | `/api/account` | 个人系统 |
| `GET` | `/api/balance` | 余额 |
| `GET` | `/api/history` | 历史记录 |
| `POST` | `/api/images/generate` | 提交文生图任务 |
| `POST` | `/api/images/edit` | 提交改图任务 |
| `GET` | `/api/tasks` | 任务列表 |
| `GET` | `/api/tasks/{task_id}` | 任务详情 |
| `GET` | `/api/inspirations` | 案例列表 |
| `POST` | `/api/inspirations/sync` | 手动同步案例 |
| `PUT` | `/api/config` | 用户配置 |
| `GET` | `/storage/*` | 生成图片、上传图、案例缓存图 |

## 测试

```bash
PYTHONPATH=backend pytest backend/tests
npm run lint
npm run build
docker compose build
```

## 开源自部署注意事项

部署者至少修改：

- `SUB2API_BASE_URL`
- `SUB2API_AUTH_BASE_URL`
- `CORS_ORIGINS`
- 宿主机 Nginx `server_name`
- 站点公告、充值链接、联系方式
- 品牌名和 Logo

不要提交真实 `.env`、API Key、GitHub Token、数据库和图片存储目录。
