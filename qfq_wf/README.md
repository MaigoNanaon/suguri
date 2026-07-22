# qfq_wf —— 前复权独立工作流

基于已验证的「issue#39 累积法 + 两处修正（bug1/bug2）」，将前复权处理拆分为三个独立步骤。
前复权锚点 = **最新交易日**（最新日 `offset=0, scale=1`，历史价向前折算）。

## 三步职责

| 步骤 | 模块 | 输入 | 输出 | 增量？ |
|---|---|---|---|---|
| ① raw | `step1_raw.py` | sh/sz 的 lday 文件夹 | `data/daily/<code>.csv`（不复权） | ✅ 支持 `--incremental` |
| ② factors | `step2_factors.py` | gbbq + key.rs + ①的交易日历 | `data/qfq_events.csv`、`data/factors/<code>.csv` | ❌ 必须全量 |
| ③ qfq | `step3_qfq.py` | ① raw + ② 因子 | `data/qfq/<code>.csv`（前复权） | ❌ 必须全量 |
| ④ resample | `step4_resample.py` | ③ 前复权日线 | `data/resample/<code>_<tag>.csv` | ❌ 整目录重算 |
| ⑤ drawline | `step5_drawline.py` | ③ 前复权日线 | `data/drawline/<code>.csv` | ❌ 整目录重算 |

> ② 的因子递推依赖整段交易日历（从 `n-1` 倒序到 `0`），gbbq 一旦更新必须全量重算；
> ③ 的因子整体随 ② 变化，也必须全量重算。只有 ① 的 `.day` 解析可增量。
> ④ ⑤ 均由 ③ 的前复权日线派生，依赖 ③ 的最新结果，须在其后执行。

### 第 ④ 步：resample（周/月/45日/季/年 K 线）

对前复权日线做周期重采样，输出 5 种周期：

- 周 / 月 / 季 / 年：按【自然日历】分组（pandas Period）。周以周一为起点、周日为界（W-SUN）；月/季/年末为各自期间边界。
- 45日：按【交易天数】连续切片——第 1..45 个交易日为第 1 根，第 46..90 为第 2 根，依此类推（末尾不足 45 日也成一根）。

每根 K 线 OHLC：`open`=期间首个交易日开盘，`close`=期间末交易日收盘，`high`=期间最高，`low`=期间最低；`date`=期间末个交易日的真实日期（便于与日线对齐）。仅含 OHLC，不含量/额。

### 第 ⑤ 步：drawline（划线集）

由前复权日线生成【划线集】：

- **跨年价**：某年【最后一个交易日收盘价】与【来年（次年）第一个交易日开盘价】的较小值。仅当相邻两年 `Y` 与 `Y+1` 在数据中都有交易记录时成立（若 `Y+1` 整年无数据，则该年不产生跨年价）。
- **划线集**：该股票历史以来的所有跨年价，外加【上市第一个交易日的开盘价】。

输出列：`kind`（ipo=上市首开 / cross_year=跨年价）、`year`、`date_a / price_a`（ipo→上市首日开盘；cross_year→年 Y 末交易日收盘）、`date_b / price_b`（cross_year→年 Y+1 首交易日开盘，ipo 留空）、`price`（划线价：ipo→首日开盘，cross_year→min(price_a, price_b)）。

## 复权因子

每行附带一对仿射因子：

```
前复权价 = (raw + adj_offset) / adj_scale
还原 raw  = 前复权价 * adj_scale - adj_offset
```

单因子（纯乘法）无法表达现金分红的「位移」，故必须 `(offset, scale)` 两参数。
两个因子仅由 gbbq 事件序列 + 交易日历决定，**不依赖 OHLC 价格**。

## 用法

```powershell
# 日常全量（①增量 + ②全量 + ③全量）
python -m qfq_wf all

# 首次或强制全量重解析 lday
python -m qfq_wf all --full

# 只跑某几只
python -m qfq_wf all --only 600009,600000

# 单步
python -m qfq_wf raw --incremental
python -m qfq_wf factors
python -m qfq_wf qfq
python -m qfq_wf resample        # ④ 全部股票重采样
python -m qfq_wf drawline        # ⑤ 全部股票划集合
python -m qfq_wf resample --only 600009
python -m qfq_wf drawline --only 600009

# 用 600000 真实数据校验锚点（容差 0.015）
python -m qfq_wf self-test
```

路径默认值见 `config.py`（Windows 环境），均可用 `--sh-dir / --sz-dir / --gbbq / --key-rs / --out` 覆盖。

## 目录产出

```
data/
├── daily/      <code>.csv      # ① 不复权日线（价格已为元，/100）
├── factors/    <code>.csv      # ② 每日因子（date, adj_offset, adj_scale）
├── qfq/        <code>.csv      # ③ 前复权 OHLC（+ 因子列）
├── resample/   <code>_W.csv    # ④ 周线  （date, open, high, low, close）
│               <code>_M.csv    #    月线
│               <code>_Q.csv    #    季线
│               <code>_Y.csv    #    年线
│               <code>_45D.csv  #    45日线（按交易天数切片）
├── drawline/   <code>.csv      # ⑤ 划线集（kind,year,date_a,price_a,date_b,price_b,price）
└── qfq_events.csv              # ② 解密出的权息事件表（code,date,fh,pgj,sg,pg）
```
