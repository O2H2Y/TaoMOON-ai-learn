# Proposal

## Why

两个用户可见的问题需要一起解决：其一，用户更换微信头像后，客户端把 `chooseAvatar` 返回的**设备临时文件路径**（开发者工具中形如 `http://tmp/xxx.jpeg`）当作永久地址提交并入库，小程序一启动就把它渲染成图片，触发渲染层 `__tmp__` / CORS 报错；该地址在其它设备或缓存清理后必然失效，属于真实缺陷。其二，通关报告海报中部存在大块空白，金句换行把句号挤到下一行并与页脚文字重叠，同时海报缺少二次传播入口（没有可扫描的码，也没有可查看的完整版）。

## What Changes

- 头像改为"先上传文件、再保存服务端地址"：新增后端头像上传接口（文件落到对象存储 COS），前端在 `chooseAvatar` 后先上传文件再更新资料；对历史脏数据做一次性清洗，并在渲染层做防御性过滤（回退文字头像）。
- 重新设计海报版式：标题与数据卡之间用「本节要点」填充，金句按实际渲染宽度折行并禁止标点落在行首，各区块与页脚不再重叠。
- 海报内新增小程序码入口：后端生成小程序码（微信 `getwxacodeunlimit`），前端下载后绘制进海报；扫码进入小程序内的「完整版报告」页。
- 新增「完整版报告」分享页与免登录只读接口：任何人扫码即可查看完整报告内容，并支持保存完整版长海报。
- 明确不做：不迁移到 Canvas 2D、不新增 H5 分享页、不改动既有出题 / 答题 / 知识库行为。

## Capabilities

### New Capabilities
- `user-avatar`: 用户头像的更新与展示契约——头像必须以上传文件的方式提交并由服务端返回可持久化的 HTTPS 地址，任何设备临时路径都不得被保存或渲染。
- `report-share`: 通关报告分享能力——海报的内容与版式规则、海报内小程序码，以及扫码进入的完整版报告页（免登录只读、按 quiz_id、不含用户隐私）。

### Modified Capabilities
<!-- 现有 knowledge-base-documents / knowledge-base-quiz-generation 的需求不变 -->

## Impact

- **后端新增**：`app/api/v1/routes/share.py`、`app/services/wechat_qrcode_service.py`、`app/services/avatar_service.py`（或复用 `cos_service`）、`app/models/share.py`、`backend/scripts/clean_temp_avatar_urls.py`。
- **后端修改**：`app/api/v1/routes/user.py`（新增 `POST /user/avatar`）、`app/services/user_service.py`（头像地址校验）、`app/core/config.py`（小程序码 env_version 等新配置）、`app/main.py`（注册 share 路由）、`app/repositories/quiz_repository.py`（按 quiz_id 的公开只读查询）、`.env.example`。
- **前端修改**：`src/services/api.ts`、`src/pages/profile/index.tsx`、`src/pages/index/index.tsx`、`src/pages/report/index.tsx`、`src/pages/report/poster.ts`、`src/app.config.ts`。
- **前端新增**：`src/pages/share/index.tsx`、`src/pages/share/index.scss`、`src/pages/share/index.config.ts`。
- **接口**：新增 `POST /api/v1/user/avatar`、`GET /api/v1/share/poster-qrcode`、`GET /api/v1/share/quizzes/{quiz_id}`；既有接口的请求 / 响应结构与行为不变。
- **数据**：一次性清洗 `users.avatar_url` 中形如 `http://tmp/%`、`https://tmp/%`、`wxfile://%` 的历史值。
- **依赖**：无新增第三方依赖（小程序码生成走已有 `httpx`，对象存储复用已有 COS 能力）。
- **已知限制**：小程序未发布前，小程序码只能被开发者 / 体验者扫开（上线后使用 release 版本码）。