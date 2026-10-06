# Design

## Context

现状（来自代码阅读，非猜测）：

- 海报由 `src/pages/report/index.tsx` 用 `Taro.createCanvasContext(POSTER_CANVAS_ID)` + `drawPoster()`（`src/pages/report/poster.ts`）在 600×900 画布上绘制，`context.draw(false, cb)` 回调内调用 `Taro.canvasToTempFilePath`（`fileType: 'png'`，导出 1200×1800），再用 `<Image src={posterPath}>` 预览并支持保存 / 分享。
- Taro 官方文档：`Taro.canvasToTempFilePath` 必须在 `draw()` 回调里调用；旧版画布传 `canvasId`，Canvas 2D 传 `canvas` 节点实例（https://docs.taro.zone/docs/apis/canvas/canvasToTempFilePath ，对应微信 https://developers.weixin.qq.com/miniprogram/dev/api/canvas/wx.canvasToTempFilePath.html ）。
- 现有海报的具体缺陷：标题基线 y=180/242，数据卡从 y=330 开始，中间留下约 90px 空档；`wrapCanvasText` 按固定字符数切分，长金句最后一行的句号被挤成独立一行（y=826），并与页脚（y=850）重叠。
- 头像链路：`src/pages/profile/index.tsx:78-86` 把 `chooseAvatar` 返回的 `e.detail.avatarUrl`（设备临时路径）直接提交 `PUT /user/profile`；`backend/app/repositories/user_repository.py:59-79` 原样写库；`src/pages/index/index.tsx:18-19,77-78`、`src/pages/profile/index.tsx:105-106` 直接把它当作 `<Image src>`。启动时登录返回该地址 → 渲染层请求 `http://tmp/xxx.jpeg` 被开发者工具代理为 `http://127.0.0.1:<port>/__tmp__/xxx.jpeg` → CORS 报错。
- 后端已有能力：统一响应 `ApiResponse`、JWT 鉴权 `get_current_user` / `get_optional_user`、腾讯云 COS 上传（`app/services/cos_service.py`，公开 URL 由 `COS_DOMAIN` 或 bucket 域名拼接）、手写 SQL + `CREATE TABLE IF NOT EXISTS`（`app/core/db.py`），`quiz_sessions` / `answer_records` / `reports` 均以 `quiz_id` 为键。
- 微信服务端接口：`/wxa/getwxacodeunlimit`（获取不限制的小程序码），接口清单见 https://developers.weixin.qq.com/miniprogram/dev/server/API/qrcode-link/ ；appid / secret 已由 `WECHAT_APP_ID` / `WECHAT_APP_SECRET` 配置（登录链路已在使用）。
- 约束：小程序尚未发布；开发者工具渲染层对 `http://tmp/...` 图片报 CORS；海报使用旧版 canvas API；前端无测试框架（验证以开发者工具与真机观察为主），后端使用 pytest。

## Goals / Non-Goals

**Goals:**
- 头像只保存与渲染可持久化地址，启动时不再出现 `__tmp__` / CORS 报错。
- 海报版面完整（无大块空白、无重叠），中文排版正确，并带可扫描的小程序码。
- 扫码可在小程序内免登录查看完整版报告，并支持保存完整版长海报。
- 不改变既有出题 / 答题 / 报告 / 知识库接口的外部契约。

**Non-Goals:**
- 不做 H5 分享页，不做普通二维码方案。
- 不迁移 Canvas 2D，不重做报告页整体 UI。
- 不做头像裁剪 / 压缩 / 审核，不做分享数据统计与埋点。

## Decisions

### D1 头像走「上传文件 → 对象存储 → 存 HTTPS 地址」
- 新增 `POST /api/v1/user/avatar`（multipart、需登录），复用 `cos_service` 上传，key 形如 `{COS_UPLOAD_PREFIX}avatars/{user_id}/{uuid}.{ext}`，成功后 `UPDATE users.avatar_url` 并返回该 URL。
- 不把图片 base64 存库：`users.avatar_url` 字段上限 500 字符，DB 体积与图片渲染性能都不可接受。
- `PUT /api/v1/user/profile` 保留，但对 `avatar_url` 增加地址校验：只接受可持久化的 `http(s)` 地址，拒绝 `http://tmp/...`、`https://tmp/...`、`wxfile://...` 等临时路径并返回明确错误——防止旧客户端继续写入脏数据。

### D2 前端渲染层防御 + 一次性数据清洗（双保险）
- `src/services/api.ts` 增加 `isPersistableImageUrl(url)`；`profile` 页与首页渲染头像前过滤，非法地址回退文字头像。
- 新增 `backend/scripts/clean_temp_avatar_urls.py`：把匹配 `http://tmp/%`、`https://tmp/%`、`wxfile://%` 的 `avatar_url` 置空，执行前 SELECT 计数、执行后复核，可重复执行。
- 两端都做的原因：服务端清洗只能修数据库历史值，客户端 `Taro.getStorageSync('userInfo')` 缓存里仍可能残留脏值，必须由渲染层兜底。

### D3 海报重排：宽度测量折行 + 固定分区骨架
- 新增 `wrapTextByWidth(ctx, text, maxWidth, maxLines)`：逐字符 `measureText` 累加折行；英文 / 数字按整词聚合，不拆词；行首禁则（`。，、；：！？）》」』%` 等不落行首）、行尾禁则（`（《「『` 不落行尾）；超出行数上限时以 `…` 结尾。
- 新版式（画布 600×900，导出 1200×1800）：
  - 0–24 顶部色条；y=88 品牌行。
  - 标题：y=176 起，48px，最多 2 行。
  - **本节要点卡**：y=270–386，白色卡片 + 左侧色条，内容取 `mastered_points` 前 2 条；缺失时降级为 `three_line_summary[0]`，再缺失则用固定鼓励文案。
  - 数据卡：y=416–650；左列为正确率（88px）+「掌握度」+ 答对 / 答错；右列为 132×132 小程序码 + 「扫码看完整报告」。
  - 经验值行：y=700；金句区：y=748 起，最多 2 行 × 44px；页脚固定 y=862。
  - 金句区行数上限与页脚固定基线共同保证不重叠（即使输入更长，也只截断加省略号）。
- 完整版长海报（full 模式）：600×1440，依次绘制要点卡、数据卡（含小程序码）、经验值、金句、知识总结、薄弱点、建议，超出按行数截断加省略号。

### D4 小程序码：后端生成 → COS → 前端下载后绘制
- 新增 `app/services/wechat_qrcode_service.py`：
  - `access_token` 进程内缓存（`https://api.weixin.qq.com/cgi-bin/token`，按 `expires_in` 提前 300s 过期）。
  - `POST https://api.weixin.qq.com/wxa/getwxacodeunlimit`，参数：`scene` 传该次闯关的 `quiz_id`（`quiz_` + 12 位 hex = 17 字符，满足官方 scene 长度上限）、`page=pages/share/index`、`check_path=false`（页面尚未发布也能生成）、`env_version` 由 `WECHAT_MINIPROGRAM_ENV_VERSION` 控制（默认 `release`；开发与体验环境可设 `develop` / `trial`）、`width` 由 `SHARE_QRCODE_WIDTH` 控制。
  - 返回体按 `Content-Type` 区分：二进制为 PNG 图片，JSON 为错误体；成功则上传 COS 并返回 URL。
  - 参数名与错误码在实现时按官方文档逐项核对：https://developers.weixin.qq.com/miniprogram/dev/server/API/qrcode-link/qr-code/api_getunlimitedqrcode
- 新增 `GET /api/v1/share/poster-qrcode?quiz_id=...`：校验该 quiz_id 已保存于 `quiz_sessions` → 返回 `{ qrcode_url }`；不存在返回业务错误，前端降级为「无码海报 + 提示」，不阻断海报生成。
- 前端绘制：`Taro.downloadFile({ url })` 得到本地临时路径（官方文档确认返回本地临时文件路径）→ `context.drawImage(path, x, y, w, h)` → 最后统一 `draw()`。
- 不采用前端生成普通二维码：已确认走方案 A（小程序码），普通二维码无法直接进入小程序页面。

### D5 完整版分享页（小程序内，免登录只读）
- 新增页面 `pages/share/index`，入口两种：小程序码 `scene`（`useRouter().params.scene`，需 `decodeURIComponent`）与 query `?quizId=`（便于开发调试）。
- 数据来自新增的免登录只读接口 `GET /api/v1/share/quizzes/{quiz_id}`，响应字段白名单：`quiz_id`、`title`、`summary`、`questions`、`answer_records`、`report`、`created_at`；不含 `user_id`、`openid`、`nickname`、`avatar_url`、`total_xp`。
- 页面提供「保存完整海报」：复用 poster 模块 full 模式，canvas 600×1440。
- 隐私取舍：以随机 `quiz_id`（12 位 hex）作为分享凭证，不额外发放分享 token；隐私边界由接口字段白名单保证。

### D6 配置与降级
- 新增配置：`WECHAT_MINIPROGRAM_ENV_VERSION`（默认 `release`）、`SHARE_QRCODE_WIDTH`（默认 430）、`AVATAR_MAX_SIZE_MB`（默认 5），并同步 `.env.example`。
- 外部依赖失败一律降级而不阻断主流程：小程序码失败 → 跳过二维码并提示；头像上传失败 → 保持原头像并提示。

## Risks / Trade-offs

- 小程序未发布前，小程序码只能被开发者 / 体验者扫开：通过 `env_version` 在开发期验证，上线后切 `release`。
- `access_token` 进程内缓存：多实例部署会各自获取，存在触发频率限制的风险；当前单实例部署可接受，记录为后续优化。
- 公开只读接口以 `quiz_id` 作为唯一凭证：12 位 hex 不可枚举，但链接一旦外传即对持有者可见——这是"分享即公开"的产品语义，接口层已剥离身份字段。
- `measureText` 在不同机型存在字体回退差异：折行按保守行宽计算，并在真机上抽查至少 2 台设备。
- 清洗脚本会清空历史临时头像，用户需重新选择一次头像：属预期行为，头像为空时展示文字头像，不再报错。

## Open Questions

无。二维码方案已确认为「小程序码 + 小程序内完整报告页」；实现阶段需按官方文档核对 `getwxacodeunlimit` 的参数名与错误码。