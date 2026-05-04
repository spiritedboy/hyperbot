# Hyperliquid 监控跟单系统

无前端，纯后端 WebSocket 事件驱动服务。

## 功能覆盖

- 监控指定地址的仓位变化：开仓、加仓、减仓、反手、清仓
- 每次信号推送钉钉：币种、方向、全仓/逐仓、杠杆、开单本金、本金占总余额比例
- 使用 Hyperliquid API 进行市价跟单（IOC 模拟市价）
- 通过 Hyperliquid WebSocket 订阅 leader 账户事件，低延迟触发跟单
- 跟单固定本金：单币种每次按 `20U`（可配置）本金执行
- 跟随 leader 的杠杆和保证金模式（全仓/逐仓）
- leader 清仓时，follower 无条件清仓并推送平仓盈亏（若可查询到）
- 事件去抖 + 去重，避免短时间重复触发导致重复下单
- 风控拦截：最大持仓币种数、总本金上限、最小下单面额、是否允许做空
- 系统启动通知与周期心跳通知
- 高并发保护：事件处理重入时自动合并为下一次 tick
- 统一账户余额口径：账户净值 = 合约净值 + 现货余额（稳定币）

## 1. 安装

```bash
cd /home/yyf/hyperbot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`：

- `LEADER_ADDRESS`: 被监控地址
- `FOLLOWER_PRIVATE_KEY`: API 钱包私钥（用于签名下单）
- `FOLLOWER_ADDRESS`: Hyper 主地址（资金和仓位所在账户地址，不是 API 钱包地址）
- `DINGTALK_WEBHOOK`: 钉钉自定义机器人 webhook
- `FIXED_MARGIN_USD`: 固定跟单本金，默认 `20`
- `WS_RECONNECT_SECONDS`: WebSocket 断线重连间隔秒数
- `EVENT_DEBOUNCE_SECONDS`: 同币种事件最小处理间隔
- `DUPLICATE_TTL_SECONDS`: 相同事件去重窗口
- `MAX_OPEN_COINS`: 最大持仓币种数
- `MAX_TOTAL_PRINCIPAL_USD`: 总本金上限
- `MIN_NOTIONAL_USD`: 最小下单面额
- `ALLOW_SHORT`: 是否允许空单
- `HEARTBEAT_SECONDS`: 心跳推送间隔（<=0 关闭）
- `DRY_RUN`: 建议先 `true` 联调，确认无误后改 `false`

地址填写规则（务必确认）：

- Hyper 主地址：有资产和仓位的主账户地址，用于查询净值和持仓
- API 钱包地址：代理钱包地址，通常不承载资产
- API 钱包私钥：仅用于交易签名

对应到配置：

- `FOLLOWER_PRIVATE_KEY` 填 API 钱包私钥
- `FOLLOWER_ADDRESS` 填 Hyper 主地址

常见错误：

- 如果把 `FOLLOWER_ADDRESS` 误填成 API 钱包地址，系统可能显示余额为 `0`
- 程序启动时已内置该错误的自动告警（控制台 + 钉钉）

## 3. 启动

```bash
python3 main.py
```

后台长期运行（推荐）：

```bash
nohup python3 main.py > 1.txt 2>&1 &
```

说明：

- `2>&1` 表示将错误输出合并到日志文件
- 若不需要日志可用：`nohup python3 main.py > /dev/null 2>&1 &`

常用命令：

```bash
# 实时查看日志
tail -f 1.txt

# 查进程（任选其一）
pgrep -fa "python3 main.py"
ps -ef | grep "python3 main.py" | grep -v grep

# 停止进程（先查 PID 再 kill）
kill <PID>
```

启动后会立刻推送一条钉钉"系统启动"快照（ActionCard 格式，附"查看 Leader 仓位"按钮），用于重启自检：

- 监控地址与跟单地址
- 两个地址的合约净值
- 两个地址的现货余额
- 两个地址的账户净值（统一账户 = 合约 + 现货）
- 两个地址的已用本金（估算）
- 两个地址的持仓币种数
- 两个地址的逐币种仓位明细（方向、全仓/逐仓、杠杆、面额、本金、本金占比、当前盈亏、爆仓价）
- 当前运行模式（DRY_RUN/LIVE）

统一账户说明：

- 当跟单地址“合约净值”为 `0` 但“现货余额”大于 `0`，表示资金在现货侧，未作为合约保证金使用
- 系统展示的“账户净值(统一账户)”会将这两部分合并显示

## 4. 钉钉消息示例

消息类型说明：

- **监控信号 / 跟单结果 / 风控拦截 / 系统错误** → Markdown 类型（支持标题、加粗、分隔线、引用块、链接）
- **系统启动 / 心跳** → ActionCard 类型（含"查看 Leader 仓位"按钮）

监控信号（Markdown）：

```
## 📈 监控信号 · 开仓

---

**BTC**   🔴 做空   `全仓 20x`   _13:40:00_

> 本金 **750.74 U** ｜ 面额 15014.85 U
> 本金占比 31.48% ｜ 浮盈 **-12.3400 U**
> 爆仓价 103245.1200
```

跟单结果（Markdown）：

```
## ✅ 跟单结果 · 开仓

---

**BTC**   🔴 做空   `全仓 20x`   🔴 **LIVE**   _13:40:01_

> 本金 **20.00 U** ｜ 面额 400.00 U
```

平仓结果（Markdown）：

```
## ✅ 跟单结果 · 清仓

---

**BTC**   🔴 做空   `全仓 20x`   🔴 **LIVE**   _15:12:08_

> 本金 **20.00 U** ｜ 面额 398.23 U
> 平仓盈亏 **+3.1200 U**
```

## 5. 跟单规则（实现细节）

- 开仓/加仓：按 `固定本金 * 当前杠杆` 计算下单面额
- 减仓：按 `固定本金 * 当前杠杆` 做 reduce-only 市价减仓（不超过当前仓位）
- 清仓：对该币种 follower 当前仓位做 reduce-only 市价全平
- 反手：先平旧方向，再按新方向开仓

## 6. 安全建议

- 首次务必 `DRY_RUN=true` 观察钉钉消息和行为
- 使用专用小资金跟单地址，避免与主账户混用
- 根据网络和流动性适当调整 `MARKET_SLIPPAGE`
- 实盘建议设置 `MAX_TOTAL_PRINCIPAL_USD`，避免突发连续信号导致风险敞口过高
- 若只做多，设置 `ALLOW_SHORT=false`
