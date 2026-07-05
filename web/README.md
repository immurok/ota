# 官网固件分发 + 遥测

官网是 **Cloudflare Pages**（`website/` 根目录即发布内容，`website/functions/` 是
Pages Functions）。固件分发与遥测都随 `wrangler pages deploy` 一起上线。

## 一条龙发布（推荐）
```bash
ota/deploy-fw.sh --notes "Fix occasional unlock failure"
```
它会：编译 release 固件 → 生成 manifest → 拷进 `website/fw/` →
`npx wrangler pages deploy . --project-name=immurok` → curl 校验线上版本。
先 `--dry-run` 预览、`--no-build` 复用现有产物。详见脚本头部注释。

## /fw/ 静态目录
`ota/release-web.sh` 生成 `ota/web-dist/`（manifest.json + 2 个 .imfw）；
`deploy-fw.sh` 负责拷到 `website/fw/`。`website/_headers` 已给
`/fw/manifest.json` 设 `Cache-Control: max-age=300`（App 每 24h 查一次，
但发布后希望 5 分钟内生效）。固件二进制经 `website/.gitignore` 排除，不入版本库。

## /api/t 遥测（Cloudflare Pages Function）
端点是 `website/functions/api/t.js`（`onRequestPost` 形式，与 `functions/api/like.js`
同机制），随站点部署自动路由到 `https://immurok.com/api/t`。**不是**独立 Worker。

配置 GA4 密钥（Pages 项目的环境变量 / Secrets）：
1. GA4 后台创建 Measurement Protocol api_secret（管理 → 数据流 → MP API 密钥）
2. ```bash
   cd website
   npx wrangler pages secret put GA_MEASUREMENT_ID --project-name=immurok
   npx wrangler pages secret put GA_API_SECRET --project-name=immurok
   ```
   （未配密钥时端点仍返回 204，只是不转发给 GA——不影响 App。）
3. 部署：`cd website && npx wrangler pages deploy . --project-name=immurok`
4. 验证：
   ```bash
   curl -X POST https://immurok.com/api/t -H 'Content-Type: application/json' \
     -d '{"client_id":"test-1","events":[{"name":"fw_check","params":{"device_version":"1.3.11","latest_version":"1.6.1","update_available":true,"app_version":"1.20"}}]}'
   ```
   期待 204；GA4 DebugView / 实时报表可见 fw_check。
