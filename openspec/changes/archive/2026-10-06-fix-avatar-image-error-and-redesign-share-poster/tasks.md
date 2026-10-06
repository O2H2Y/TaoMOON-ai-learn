# Tasks

## 1. 后端：头像上传与数据清洗

- [x] 1.1 新增 `POST /api/v1/user/avatar`（multipart、需登录）：校验文件类型与大小（`AVATAR_MAX_SIZE_MB`），复用 `cos_service` 上传到 `{COS_UPLOAD_PREFIX}avatars/{user_id}/{uuid}.{ext}`，更新 `users.avatar_url` 并返回 URL；pytest 覆盖成功 / 非图片 / 超限 / 未登录四种情况并通过
- [x] 1.2 在 `PUT /api/v1/user/profile` 为 `avatar_url` 增加校验：拒绝 `http://tmp/...`、`https://tmp/...`、`wxfile://...` 等临时路径并返回明确错误；pytest 覆盖「拒绝临时路径」与「接受 https 地址」两个用例并通过
- [x] 1.3 新增 `backend/scripts/clean_temp_avatar_urls.py`：把匹配临时路径模式的 `avatar_url` 置空，先计数、再执行、再复核，可重复执行；在测试库连续执行两次，验证第二次更新行数为 0

## 2. 前端：头像上传与渲染兜底

- [x] 2.1 `src/services/api.ts` 新增 `uploadAvatar(filePath)`（`Taro.uploadFile` + 统一响应解析）与 `isPersistableImageUrl(url)`；用三组样例（`http://tmp/x.jpeg`、`wxfile://x`、`https://cos.example.com/x.png`）验证判定结果为 假 / 假 / 真
- [x] 2.2 `src/pages/profile/index.tsx` 的 `handleChooseAvatar` 改为「先上传、成功后刷新资料」，失败时提示且不改本地状态；在开发者工具中选择一次真实微信头像，验证请求走 `/user/avatar` 且资料中的地址为 HTTPS
- [x] 2.3 `src/pages/profile/index.tsx` 与 `src/pages/index/index.tsx` 渲染前用 `isPersistableImageUrl` 过滤，非法地址回退文字头像；把本地缓存 `userInfo.avatar_url` 手工改成 `http://tmp/x.jpeg` 后重启小程序，验证显示为文字头像且控制台不再出现 `__tmp__` 请求
- [x] 2.4 更新 `.env.example` 与 `docs/保姆级本地运行指南.md`，补充头像上传接口、`AVATAR_MAX_SIZE_MB` 与清洗脚本用法；按文档原样执行一遍，确认步骤可复现

## 3. 后端：小程序码与公开分享接口

- [x] 3.1 新增 `app/services/wechat_qrcode_service.py`：access_token 获取与提前过期缓存、调用 `getwxacodeunlimit`（`scene=quiz_id`、`page=pages/share/index`、`check_path=false`、`env_version` 可配）、区分二进制 PNG 与 JSON 错误体、成功结果上传 COS 并返回 URL；按官方文档核对参数名与错误码，pytest（mock httpx）覆盖成功 / 微信错误码 / 网络异常三条路径并通过
- [x] 3.2 新增 `GET /api/v1/share/poster-qrcode?quiz_id=`：quiz_id 不存在时返回明确业务错误，存在时返回 `{ qrcode_url }`；pytest 覆盖存在 / 不存在两条路径并通过
- [x] 3.3 新增免登录只读接口 `GET /api/v1/share/quizzes/{quiz_id}`：仅返回白名单字段（title / summary / questions / answer_records / report / created_at）；pytest 断言响应键集合、断言不含 user_id / nickname / avatar_url，且 quiz_id 不存在时返回未找到
- [x] 3.4 `app/core/config.py` 增加 `wechat_miniprogram_env_version`（默认 release）、`share_qrcode_width`（默认 430）、`avatar_max_size_mb`（默认 5）；验证不设置这些变量时服务仍以默认值启动且既有 pytest 全绿
- [x] 3.5 在 `app/main.py` 注册 share 路由，在 `app/repositories/quiz_repository.py` 增加按 quiz_id 的公开只读查询；用不带认证头的 pytest 用例验证公开接口可访问、私有接口仍返回未认证

## 4. 前端：海报重排与小程序码绘制

- [x] 4.1 `src/pages/report/poster.ts` 实现 `wrapTextByWidth`（宽度测量、行首 / 行尾禁则、省略号、不拆英文词）；用「长中文金句结尾带句号」「中英混排」「超长文本」三组样例生成海报，确认标点不落行首、单词不被拆断、末行带省略号
- [x] 4.2 按设计版式重画海报（要点卡 / 数据卡含小程序码位 / 经验值 / 金句区 / 固定页脚）并导出 1200×1800；确认标题下方无大块空白、五个区块互不重叠、页脚完整可见
- [x] 4.3 `src/pages/report/index.tsx` 生成流程接入小程序码：先请求 `/share/poster-qrcode` → `Taro.downloadFile` → `context.drawImage`；把接口置为失败验证降级路径，确认海报仍可生成、可保存、可分享并出现提示
- [ ] 4.4 真机验证：用微信扫描海报上的小程序码，确认可打开完整版报告页且二维码清晰可识别（`WECHAT_MINIPROGRAM_ENV_VERSION` 设为 `develop` 或 `trial` 时可扫开）

## 5. 前端：完整版报告分享页

- [x] 5.1 新增 `src/pages/share/index.tsx` / `index.scss` / `index.config.ts` 并在 `src/app.config.ts` 注册；支持 `scene` 与 `quizId` 两种入口解析，分别用「扫码进入」与「带 quizId 的页面跳转」验证都能加载数据
- [x] 5.2 分享页调用公开只读接口渲染完整报告（含题目回顾）并实现「保存完整海报」（full 模式 600×1440）；清除本地 token 模拟未登录后刷新，验证仍可查看与保存
- [x] 5.3 补齐空态与错误态：quiz_id 不存在或接口失败时展示「报告不存在或已失效」并可返回首页；用一个不存在的 quiz_id 验证文案与跳转

## 6. 集成验收

- [ ] 6.1 端到端走查：启动小程序（控制台无 `__tmp__` / CORS 报错）→ 登录 → 生成闯关 → 答题 → 报告 → 生成海报（含小程序码）→ 保存到相册 → 扫码 → 完整版页 → 保存完整海报；每步留截图或录屏
- [ ] 6.2 回归检查既有流程：出题（含 AI 配图与知识库出题）、答题判定、报告生成、历史列表与详情、我的页统计与头像展示；确认外部接口契约未变
- [x] 6.3 逐条复核本变更 spec 中的需求与场景是否都已实现并被验证，将结论回写到本任务清单
---

## 7. 用户反馈修复（2026-10-06 截图）

- [x] 7.1 报告页底部按钮「生成海报」更名为「分享海报」，生成过程中显示「生成中...」；源码断言与 `dist/pages/report/index.js` 构建产物校验通过
- [x] 7.2 海报弹窗去掉标题「分享海报」与底部「关闭」文字链接；第二个按钮改为「关闭图片」并用于关闭弹窗；构建产物中已无 `poster-dialog-title` / `poster-close`
- [x] 7.3 修复海报内「答错 N 题」与「扫码看完整报告」文字遮挡：统计列与小程序码列之间增加分隔线、答错统计左移、说明文字按实际宽度在二维码下方居中；Node 包围盒断言通过（答错 [240,361]，说明 [400,540]，无交集）
- [x] 7.4 补齐分享海报弹窗样式（此前 `.poster-mask` / `.poster-dialog` / `.poster-image` / `.poster-dialog-actions` 均无样式，弹层以内联块方式展示）：遮罩固定定位并阻止穿透滚动、弹窗居中、图片固定尺寸、按钮等分排布；`dist/pages/report/index.wxss` 已包含新样式
## 验证结论（对应任务 6.3，复核于 2026-10-06）

复核方式：源码走查 + 自动化测试 + 本地端到端冒烟（真实 MySQL / 腾讯云 COS / 微信 getwxacodeunlimit 接口）。

### 已自动验证通过

| 能力 | 场景要点 | 证据 |
| --- | --- | --- |
| user-avatar | 上传后保存持久化地址 | `tests/test_avatar_service.py`(9) + `tests/test_avatar_api.py`(4)；端到端实测 `POST /user/avatar` 返回 COS https 地址 |
| user-avatar | 拒绝设备临时路径 | `tests/test_profile_avatar_validation.py`(4)：临时路径返回 400 / code 4002，且不写库 |
| user-avatar | 渲染层兜底 | `isPersistableImageUrl` 判定 4 例通过；首页与「我的」页渲染前均已过滤（源码断言） |
| user-avatar | 历史数据清洗 | `scripts/clean_temp_avatar_urls.py` 真实执行两次：匹配 1 条 → 0 条，第二次更新 0 行，幂等 |
| report-share | 版面完整、无重叠 | Node 版式验证：要点卡填满标题下方空白；正文与页脚最小间距 64px；全部文字在 900px 画布内 |
| report-share | 中文折行规则 | Node 验证：标点不落行首、左括号不落行尾、英文单词不拆断、超长截断补省略号 |
| report-share | 小程序码生成 | 真实调用微信接口生成成功并上传 COS；实测返回字节为 JPEG，已按文件头自适应扩展名与 Content-Type |
| report-share | 小程序码失败降级 | `tests/test_wechat_qrcode_service.py`(8) 覆盖微信错误码 / 网络异常 / token 过期自动重试一次；海报无码时仍可生成 |
| report-share | 免登录完整报告 | 端到端无 token 请求返回 200；响应仅含 7 个白名单字段；quiz 不存在返回 4004；私有接口未登录仍 401 |
| report-share | 分享入口解析 | 从页面源码抽取 `resolveQuizId` 实测：`?quizId=`、编码 / 未编码 / 带前缀的 `scene` 均正确，空参数触发空态 |
| 回归 | 既有契约不变 | 后端全量 `pytest`：193 passed（含出题、AI 配图、知识库、报告、历史）；前端 `tsc --noEmit` 与 `taro build --type weapp` 均通过 |

### 待你在微信开发者工具 / 真机上确认（任务 4.4、6.1、6.2）

1. 扫码前先确认 `backend/.env` 的 `WECHAT_MINIPROGRAM_ENV_VERSION` 与手头版本一致：开发版填 `develop`、体验版填 `trial`、已发布版本用 `release`（默认）。
2. 任务 4.4 + 6.1：启动小程序（控制台应不再出现 `__tmp__` / CORS 报错）→ 更换一次头像 → 生成海报（含小程序码）→ 保存到相册 → 用微信扫描小程序码 → 完整版报告页 → 保存完整海报，并保留截图。
3. 任务 6.2：在开发者工具里回归首页 / 我的页 / 知识库 / 闯关页的展示表现（接口契约层已由上面的自动化测试覆盖）。

### 冒烟测试产生的测试数据

- COS 上新增了 2 个测试对象：`quiz-images/avatars/999999/...png`（假用户 999999）与 `quiz-images/share-qrcodes/quiz_b9cf9a996f91/...jpg`（真实 quiz，用于验证小程序码），确认后可手动删除。

## 8. 用户反馈修复（第二轮，手机预览实测反馈）

- [x] 8.1 首页与全局默认导航标题缩短为「智能闯关挑战」，手机预览不再显示省略号；`dist/app.json` 与 `dist/pages/index/index.json` 已验证
- [x] 8.2 首页与「我的」页默认头像统一为「昵称第一个字」（无数据时回退「学」，与后端默认昵称「学习者」一致）；源码断言通过（两处均为 `nickname?.[0] || '学'`）
- [x] 8.3 头像 / 昵称更新失败时给出可见提示（网络类错误映射为「网络异常，头像上传失败」，昵称失败提示「昵称更新失败，请检查网络后重试」）；`tsc --noEmit` 与 Taro 构建通过
### 人工验证记录（2026-10-06 用户反馈）

- 启动与运行：控制台无 `__tmp__` / CORS 报错，页面显示正常（对应 6.1 的启动项与 6.2 的展示项）。
- 海报保存与扫码：海报可正常生成/保存；用微信扫描海报上的小程序码时，微信能识别该码并发起打开，但提示「小程序尚未发布」——原因是该码为正式版（release）小程序码，而小程序尚未发布，属 design.md 已记录的已知限制，非代码缺陷。
- 处理：已在 `backend/.env` 增加 `WECHAT_MINIPROGRAM_ENV_VERSION=develop`（重启后端后生效），可生成开发版小程序码，用开发者微信扫码打开完整版报告页完成 4.4 验证；上线前改回 `release`。
- 小程序码中心图形：该位置固定为「小程序头像」，接口无参数可自定义；当前显示灰色默认人像，说明该 AppID 在微信公众平台尚未设置小程序头像，设置并生效后重新生成海报即可显示。
- 手机预览时账号相关功能（微信昵称 / 头像、统计、历史）均无数据或无反应，根因是开发环境 `API_BASE_URL=http://localhost:8000` 在手机上指向手机自身；建议用「远程调试」预览，或把开发地址改为电脑局域网 IP（当前候选 10.250.125.105）。本轮已为头像 / 昵称更新失败添加可见提示。
- 本地调试构建模式：`npm run build:weapp` 为 production 模式，会把 `dist` 的接口地址切到 `config/prod.ts` 的线上域名（当前为占位 `your-domain.com`，请求返回 403 Tengine「Request forbidden by administrative rules」，表现为头像 / 昵称更新失败）；本地验证应使用 development 模式（`npm run dev:weapp`，或一次性 `taro build --type weapp --mode development`）。本次已用 development 模式重建，`dist` 指向 `http://localhost:8000/api/v1`。
