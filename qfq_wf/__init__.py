"""前复权独立工作流。

三步拆分（详见各 stepN 模块）：
  ① step1_raw.py      : 解析 sh/sz 的 lday -> 不复权日线 CSV（支持增量更新）
  ② step2_factors.py  : 解密 gbbq -> 权息事件；以最新交易日为锚点倒序递推
                         d_acc/b_acc/g_acc/e_acc，得到每日 (adj_offset, adj_scale)
  ③ step3_qfq.py      : 由 raw 行情 + 两个因子，推算每日前复权 OHLC -> CSV

运行：python -m qfq_wf <raw|factors|qfq|all|self-test> [选项]
"""
