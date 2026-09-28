# 蹬点 · Tibo 重置信号哨兵

定时读取 [AIHOT Tibo 重置监控](https://aihot.news/codex-reset)的公开 API，发现新的重置或发卡预告、确认后，通过飞书群自定义机器人通知。普通互动不会推送。仅需 Python 3.12 标准库；无需 X 账号或 AIHOT API Key。

项目的交互式介绍见 [demo/index.html](demo/index.html)。飞书通知已验证；钉钉和企业微信是后续可扩展渠道，当前代码尚未发送到它们。

数据来源：[数字生命卡兹克](https://aihot.news/about)的 [AIHOT API](https://aihot.news/agent)。


## 当前行为

- 首次运行把现有相关帖子保存为基线，不发送历史消息。
- 后续按帖子 ID 去重；同一事件的预告与确认是两条不同通知。
- 发送失败保留在 SQLite 队列中重试；调用 AIHOT 时使用 ETag 条件请求。
- 消息包含中文内容、北京时间、Tibo 原帖和 AIHOT 页面链接。发重置卡与额度重置分开标注。
- 默认每 5 分钟检查，可通过 `POLL_MINUTES` 调整。通知延迟还受 AIHOT 自身采集周期影响。

## 在 Windows 电脑运行

1. 在飞书目标群的「群机器人」中添加**自定义机器人**，复制 Webhook 地址。若开启了签名校验，再复制签名密钥。[飞书操作说明](https://www.feishu.cn/content/7271149634339422210)
2. 在项目目录把 `.env.example` 复制为 `.env`，把地址填到 `FEISHU_WEBHOOK_URL=` 后面。若机器人设置了自定义关键词，把**准确的关键词**填到 `FEISHU_KEYWORD=`；使用签名时也填 `FEISHU_SIGN_SECRET=`。`.env` 已加入 `.gitignore`，不要公开。
3. 在项目目录中打开 PowerShell，运行：

   ```powershell
   python .\tibo_feishu.py preview  # 查看最近活动，不写数据库、不发消息
   python .\tibo_feishu.py init     # 记录当前历史基线，不发消息
   python .\tibo_feishu.py test-webhook # 向飞书发送一条连接测试消息
   python .\tibo_feishu.py once     # 检查一次并发送基线之后的新动态
   python .\tibo_feishu.py run      # 持续运行；Ctrl+C 停止
   ```

`init` 不需要飞书 Webhook，可以先运行。若直接运行 `run` 且数据库为空，程序也会自动建立基线，不会补发历史。`status` 可查看初始化状态、上次成功检查时间、待发送和已发送数量：

```powershell
python .\tibo_feishu.py status
```

持续通知可以使用 `run` 长驻进程。Windows 上更适合安装计划任务：它在当前用户登录期间每 5 分钟执行一次 `once`，每次完成即退出；安装脚本会立即先运行一轮。若修改 `.env` 中的 `POLL_MINUTES`，重新运行安装脚本以更新计划间隔：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-windows-task.ps1
Get-ScheduledTask -TaskName TiboFeishuNotifier
# 以后如需移除：
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-windows-task.ps1 -Remove
```

这个任务需要当前用户保持登录；退出登录时不会继续检查。程序不需要开放入站端口。数据库默认位于项目目录下的 `data/tibo.sqlite3`，日志写到 `data/tibo.log`，最多保留约 15 MB。

## Docker Compose 部署

在装有 Docker Compose 的机器上克隆仓库、创建 `.env`，把飞书 Webhook 填入 `FEISHU_WEBHOOK_URL`，然后执行：

```bash
git clone https://github.com/CurtisJiang3/deng-dian.git
cd deng-dian
cp .env.example .env
# 编辑 .env，填入自己的飞书 Webhook
docker compose up --build -d
docker compose logs -f tibo-feishu
```

首次启动自动记录历史基线。数据库保存在名为 `tibo-data` 的 Docker volume 中；重建镜像不会清空去重状态。容器只发起 HTTPS 出站请求，不暴露端口。停止与查看状态：

```bash
docker compose stop
docker compose run --rm --no-deps tibo-feishu python tibo_feishu.py status
```

若需要迁移到另一台机器，应同时迁移 Docker volume 或本地 SQLite 数据库。若故意重新建立基线，需先备份后清除数据库；不要仅为了升级而删除 volume。

## 故障排查

| 现象 | 检查 |
| --- | --- |
| 启动时提示缺少 Webhook | `.env` 中填写 `FEISHU_WEBHOOK_URL`；`init`、`preview`、`status` 无需填写 |
| 飞书返回 `19024 Key Words Not Found` | 机器人启用了关键词规则；把准确关键词填入 `.env` 的 `FEISHU_KEYWORD`，随后重新测试 |
| 飞书返回其他非零错误码 | 检查 Webhook、关键词规则与签名密钥 |
| 没有通知 | 首次运行只建立基线；用 `status` 看待发送数，用日志看最近轮询 |
| AIHOT 临时不可用 | 程序保留待发送队列，并在下个周期重试 |
| 可能重复 | 飞书收到消息但 HTTP 确认丢失时，重试可能造成一次重复；原帖链接可用于识别 |

AIHOT 的[公开 API](https://aihot.news/agent)免费用于个人非商业和组织内部使用；其他用途需遵守[其使用规则](https://aihot.news/terms)。个人额度与重置卡余额仍以 Codex 内显示为准。
