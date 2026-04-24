# Hyperliquid 监控跟单系统

无前端，纯后端 WebSocket 事件驱动服务。

## 功能覆盖

- 监控指定地址的仓位变化：开仓、加仓、减仓、反手、清仓
- 每次信号推送飞书：币种、方向、全仓/逐仓、杠杆、开单本金、本金占总余额比例
- 使用 Hyperliquid API 进行市价跟单（IOC 模拟市价）
- 通过 Hyperliquid WebSocket 订阅 leader 账户事件，低延迟触发跟单
- 跟单固定本金：单币种每次按 `20U`（可配置）本金执行
- 跟随 leader 的杠杆和保证金模式（全仓/逐仓）
- leader 清仓时，follower 无条件清仓并推送平仓盈亏（若可查询到）
- 事件去抖 + 去重，避免短时间重复触发导致重复下单
- 风控拦截：最大持仓币种数、总本金上限、最小下单面额、是否允许做空
- 系统启动通知与周期心跳通知
- 高并发保护：事件处理重入时自动合并为下一次 tick

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
- `FOLLOWER_PRIVATE_KEY`: 跟单账号私钥
- `FOLLOWER_ADDRESS`: 跟单账号地址
- `FEISHU_WEBHOOK`: 飞书机器人 webhook
- `FIXED_MARGIN_USD`: 固定跟单本金，默认 `20`
- `WS_RECONNECT_SECONDS`: WebSocket 断线重连间隔秒数
- `EVENT_DEBOUNCE_SECONDS`: 同币种事件最小处理间隔
- `DUPLICATE_TTL_SECONDS`: 相同事件去重窗口
- `MAX_OPEN_COINS`: 最大持仓币种数
- `MAX_TOTAL_PRINCIPAL_USD`: 总本金上限
- `MIN_NOTIONAL_USD`: 最小下单面额
- `ALLOW_SHORT`: 是否允许空单
- `HEARTBEAT_SECONDS`: 飞书心跳间隔（<=0 关闭）
- `DRY_RUN`: 建议先 `true` 联调，确认无误后改 `false`

## 3. 启动

```bash
python3 main.py
```

启动后会立刻推送一条飞书“系统启动”快照，用于重启自检：

- 监控地址与跟单地址
- 两个地址的账户净值
- 两个地址的已用本金（估算）
- 两个地址的持仓币种数
- 两个地址的逐币种仓位明细（方向、全仓/逐仓、杠杆、面额、本金、本金占比、当前盈亏、爆仓价）
- 当前运行模式（DRY_RUN/LIVE）

## 4. 飞书消息示例

监控信号示例：

```text
[监控信号] 开仓
时间: 2026-04-24 13:40:00
币种/方向: BTC / 做空
仓位模式/杠杆: 全仓 / 20.00x
开单本金/仓位面额: 750.7424 U / 15014.8488 U
本金占比/当前盈亏: 31.48% / -12.3400 U
爆仓价: 103245.120000
```

跟单结果示例：

```text
[跟单结果] 开仓
时间: 2026-04-24 13:40:01
币种/方向: BTC / 做空
仓位模式/杠杆: 全仓 / 20.00x
跟单本金/下单面额: 20.0000 U / 400.0000 U
模式: LIVE
```

平仓结果示例：

```text
[跟单结果] 清仓
时间: 2026-04-24 15:12:08
币种/方向: BTC / 做空
仓位模式/杠杆: 全仓 / 20.00x
跟单本金/下单面额: 20.0000 U / 398.2300 U
模式: LIVE
平仓盈亏: 3.1200 U
```

## 5. 跟单规则（实现细节）

- 开仓/加仓：按 `固定本金 * 当前杠杆` 计算下单面额
- 减仓：按 `固定本金 * 当前杠杆` 做 reduce-only 市价减仓（不超过当前仓位）
- 清仓：对该币种 follower 当前仓位做 reduce-only 市价全平
- 反手：先平旧方向，再按新方向开仓

## 6. 安全建议

- 首次务必 `DRY_RUN=true` 观察飞书消息和行为
- 使用专用小资金跟单地址，避免与主账户混用
- 根据网络和流动性适当调整 `MARKET_SLIPPAGE`
- 实盘建议设置 `MAX_TOTAL_PRINCIPAL_USD`，避免突发连续信号导致风险敞口过高
- 若只做多，设置 `ALLOW_SHORT=false`
