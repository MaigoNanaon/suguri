import pandas as pd, numpy as np

code = "sh600009"
for tag in ["W", "M", "Q", "Y", "45D"]:
    d = pd.read_csv(f"data/resample/{code}_{tag}.csv")
    print(f"{tag:4s} rows={len(d):4d}  first={int(d.date.iloc[0])}  last={int(d.date.iloc[-1])}  cols={list(d.columns)}")

print()
day = pd.read_csv(f"data/qfq/{code}.csv")

# 对账1：月线某根 close == 日线该月最后一个交易日 close
m = pd.read_csv(f"data/resample/{code}_M.csv")
t = m.iloc[0]
ym = int(str(int(t.date))[:6])
lo, hi = int(f"{ym}01"), int(f"{ym}31")
sub = day[(day.date >= lo) & (day.date <= hi)]
print(f"月线 {int(t.date)} close={t.close:.4f} | 日线{ym}最后交易日 {int(sub.date.iloc[-1])} close={sub.close.iloc[-1]:.4f}")

# 对账2：45日第1根 open/close == 日线第1/第45根
r45 = pd.read_csv(f"data/resample/{code}_45D.csv").iloc[0]
print(f"45日 首根 date={int(r45.date)} O={r45.open:.4f} C={r45.close:.4f} | 日线 第1根O={day.open.iloc[0]:.4f} 第45根C={day.close.iloc[44]:.4f}")

# 对账3：周线 high/low == 该周(末周起始)日线极值（粗略取末10日）
w = pd.read_csv(f"data/resample/{code}_W.csv").iloc[0]
wk = day[day.date <= w.date.iloc[0]].tail(10)
print(f"周线 首根 date={int(w.date.iloc[0])} H={w.high:.4f} L={w.low:.4f} | 末10日 Hmax={wk.high.max():.4f} Lmin={wk.low.min():.4f}")
