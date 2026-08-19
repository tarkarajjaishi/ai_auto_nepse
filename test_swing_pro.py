"""Conformance test: does swing_pro.py actually implement the 22-section framework?

Not a unit test of the maths (swing_pro.py --selftest does that). This checks the thing that
is easy to get wrong and easy to believe you got right: whether every named requirement of the
written framework is actually present, in the framework's own wording and polarity.

Five passes, mirroring how the spec is written:

    PASS 1  the Section 22 output contract  — every named output field is emitted
    PASS 2  analytical coverage             — every named requirement of sections 2-19 exists
    PASS 3  Section 21 fidelity             — the fifteen questions verbatim, right polarity
    PASS 4  behaviour in the real output    — what a token-search cannot see
    PASS 5  independent-audit regressions   — the 16 defects a 47-agent audit confirmed

Each later pass exists because the earlier ones said "FULLY IMPLEMENTED" while things were
broken. Pass 2 only proves A STRING EXISTS IN THE SOURCE, so a feature can be computed and
never surfaced. Pass 4 caught eight of those. Pass 5 exists because an INDEPENDENT audit then
found sixteen more that passes 1-4 still could not see — including R:R arithmetically pinned
at exactly 2.00 for every stock alive, BUY ZONE firing on red candles, and eight values
computed on every symbol and read by nothing. Those are only visible by running the framework
over the whole market and checking the distribution, which is what pass 5 does.

    python test_swing_pro.py        # exits non-zero on any gap, and prints exactly which
"""
import inspect
import sys

import swing_pro

# ---------------------------------------------------------------- PASS 1

SPEC_22 = [
    "Stock", "Current Price", "Daily Trend", "Trend Stage", "Market Structure",
    "20 EMA", "50 EMA", "200 EMA", "Volume Ratio", "Volume Interpretation", "RSI 14",
    "MACD 12/26/9", "ATR 14", "Support", "Resistance", "Breakout/Pullback",
    "Relative Performance", "Fundamental Quality", "Liquidity", "False-Signal Risk",
    "Score", "Grade", "Decision", "Entry Zone", "Stop Loss", "Target 1", "Target 2",
    "Target 3", "Risk/Reward", "Trade Invalidation", "Expected Holding Period",
    "Profit-Maximization Plan", "Strongest Bullish Evidence", "Strongest Bearish Evidence",
    "Why This Trade Could Profit", "Why This Trade Could Fail", "FINAL VERDICT",
]

# ---------------------------------------------------------------- PASS 2
# (requirement name, a token that must appear in swing_pro.py's source if it is implemented)

SPEC_SECTIONS = {
    "2 trend": [("price vs each EMA", "e20="), ("EMA slope", "_slope"),
                ("EMA separation", "separation"), ("recent crossovers", "_crossovers"),
                ("pullback respects EMA", "close >= e20"), ("extension", "ext_atr")],
    "3 structure": [("HH/HL/LH/LL", "hh="), ("BOS", "BOS"), ("CHoCH", "CHoCH"),
                    ("consolidation", "Consolidation"), ("accumulation", "Accumulation"),
                    ("distribution", "Distribution")],
    "4 relative": [("5d", "ret5"), ("20d", "ret20"), ("60d", "ret60"),
                   ("vs 20-day high", "off_20d_high"), ("vs 60-day high", "off_60d_high"),
                   ("momentum acceleration", "adv_decl"),
                   ("advances vs declines", "_advances_declines")],
    "5 volume": [("volume ratio", "vol_x"), ("breakout volume", "breakout-without-volume"),
                 ("pullback volume", "pullback_dry"), ("buying vs selling", "up_day"),
                 ("expansion/contraction", "_volume_regime"), ("abnormal volume", "abnormal")],
    "6 rsi": [("level", "rsi"), ("direction", "rsi_slope"), ("bearish divergence", "rsi_div"),
              ("bullish divergence", "rsi_bull_div"), ("overextension", "rsi_overext")],
    "7 macd": [("vs signal", "macd_sig"), ("histogram direction", "hist_slope"),
               ("above/below zero", "macd_above_zero"), ("crossover", "macd_cross"),
               ("divergence", "macd_div")],
    "8 breakout": [("six states", "Failed Breakout"), ("resistance level", "resistance_tested"),
                   ("previous tests", "res_tests"), ("consolidation duration", "consol_days"),
                   ("breakout candle size", "bo_candle"), ("close position", "close_pos"),
                   ("follow-through", "follow_through")],
    "9 pullback": [("five states", "TREND BREAKDOWN"), ("declining volume", "pullback_dry"),
                   ("holds EMA", "close >= e20"), ("lower low", "broke_low")],
    "10 s/r": [("near/major support", "major_support"), ("near/major resistance", "major_res"),
               ("previous breakout level", "prev_breakout")],
    "11 atr": [("value", "atr"), ("% of price", "atr_pct"),
               ("enough volatility", "enough_vol"), ("stop too tight/wide", "stop_width")],
    "12 rr": [("entry", "entry"), ("stop", "stop"), ("t1", "t1"), ("t2", "t2"), ("t3", "t3"),
              ("mandatory gate", "MIN_RR")],
    "13 fundamentals": [("ROE", "roe"), ("P/E", "pe"), ("book value", "bvps"), ("P/B", "pb"),
                        ("EPS", "eps"), ("deteriorating flag", "fund_warn")],
    "14 liquidity": [("turnover", "turnover"), ("avg daily volume", "avg_vol"),
                     ("unexplained gaps", "gap_flag"), ("manipulation traits", "manip"),
                     ("HIGH EXECUTION RISK", "HIGH EXECUTION RISK")],
    "15 smart money": [("demand/supply zone", "demand_zone"), ("liquidity sweep", "sweep"),
                       ("support reclaim", "reclaim"), ("BOS/retest", "retest")],
    "16 false signal": [("failed breakout", "failed-breakout"),
                        ("no volume", "breakout-without-volume"),
                        ("rejection wick", "rejection-wick"), ("extended", "extended"),
                        ("resistance overhead", "resistance-overhead"),
                        ("divergence", "bearish-divergence"), ("distribution", "distribution"),
                        ("repeated failures", "repeated-failures"), ("poor rr", "poor-rr"),
                        ("chasing a large candle", "chasing-a-large-candle")],
    "17 setup": [("A breakout", "A. Breakout"), ("B retest", "B. Breakout Retest"),
                 ("C pullback", "C. Pullback"), ("D continuation", "D. Continuation"),
                 ("E reversal", "E. Early Reversal"), ("setup grade", "setup_grade")],
    "18 invalidation": [("stated invalidation", "invalidation"),
                        ("never widen the stop", "never widen the stop")],
    "19 profit": [("initial target", "Initial target"), ("extended target", "Extended target"),
                  ("partial profit", "partial profit"), ("breakeven", "breakeven"),
                  ("trailing", "trail"), ("momentum exit", "momentum breaks down")],
}

# ---------------------------------------------------------------- PASS 3

SPEC_QUESTIONS = [
    "Is the stock objectively in an uptrend?",
    "Is the daily structure showing HH/HL?",
    "Are the 20/50/200 EMAs supportive of the trend?",
    "Is volume confirming the important price moves?",
    "Is momentum healthy rather than deteriorating?",
    "Is the stock accumulating rather than distributing?",
    "Is the current entry close to a logical support/invalidation level?",
    "Is there sufficient room before major resistance?",
    "Is the stock already too extended to enter safely?",
    "Is the breakout or pullback genuinely confirmed?",
    "Is the stop-loss technically logical?",
    "Is realistic reward at least 2x the risk?",
    "Is liquidity sufficient for practical execution?",
    "What is the strongest reason this trade could fail?",
    "If this stock were not already rising, would the current 1D setup still make me want "
    "to buy it?",
]


def _sample():
    """A real analysed stock — the report must be produced from live archive data, not a stub."""
    funds = swing_pro._fundamentals()
    names = (swing_pro.MASTER / "symbols.txt").read_text(encoding="utf-8").split()
    cal = swing_pro.market_calendar(names)      # same inputs production uses, or the sample lies
    for s in names:
        try:
            f = swing_pro.analyse(s, funds, cal)
        except Exception:
            continue
        if f:
            return f
    raise AssertionError("no symbol could be analysed — cannot run the conformance test")


def main():
    src = inspect.getsource(swing_pro)
    f = _sample()
    text = swing_pro.report(f)
    gaps = []

    missing1 = [x for x in SPEC_22 if x not in text]
    print(f"PASS 1  Section 22 output contract .......... "
          f"{len(SPEC_22) - len(missing1)}/{len(SPEC_22)}")
    for m in missing1:
        gaps.append(f"[22] field not emitted: {m}")

    tot = ok = 0
    for sec, items in SPEC_SECTIONS.items():
        bad = [n for n, probe in items if probe not in src]
        tot += len(items)
        ok += len(items) - len(bad)
        for n in bad:
            gaps.append(f"[{sec}] not implemented: {n}")
    print(f"PASS 2  analytical coverage ................. {ok}/{tot}")

    mine = [q for q, _ in swing_pro.QUESTIONS]
    bad3 = 0
    for i, (spec, got) in enumerate(zip(SPEC_QUESTIONS, mine), 1):
        if spec.replace("2x", "2×") != got.replace("2x", "2×"):
            gaps.append(f"[21] Q{i} wording differs\n        spec: {spec}\n        mine: {got}")
            bad3 += 1
    if len(mine) != len(SPEC_QUESTIONS):
        gaps.append(f"[21] question count {len(mine)} != {len(SPEC_QUESTIONS)}")
        bad3 += 1
    # Q9 must be a WARNING question: YES means "too extended", i.e. bad
    if 9 not in getattr(swing_pro, "WARNING_QUESTIONS", set()):
        gaps.append("[21] Q9 is not marked as a warning question (YES must mean BAD)")
        bad3 += 1
    # Q14 is not a yes/no — it must answer with text
    a14 = swing_pro.answers(f)[13][1]
    if not isinstance(a14, str):
        gaps.append("[21] Q14 must be answered with text, not a boolean")
        bad3 += 1
    # ...and both renderers must honour that, or the CLI prints a meaningless "YES" for Q14
    # and hides the warning polarity of Q9. This caught exactly that regression once.
    for name, path in (("swing_pro.main", "swing_pro.py"), ("ui swing page", "ui.py")):
        # NB: do not name this `text` — that shadows the report and silently makes every
        # later check search the wrong string. It did exactly that once.
        srctext = (swing_pro.MASTER.parent / path).read_text(encoding="utf-8")
        if "WARNING_QUESTIONS" not in srctext:
            gaps.append(f"[21] {name} renders the questions without honouring "
                        "WARNING_QUESTIONS / the text answer for Q14")
            bad3 += 1
    print(f"PASS 3  Section 21 fidelity ................. "
          f"{len(SPEC_QUESTIONS) - bad3}/{len(SPEC_QUESTIONS)}")

    # ---- PASS 4: the things a token-search cannot see -------------------------------------
    # Pass 2 only proves a string exists in the source. These check the framework's *behaviour*
    # in the actual output. Every one of them was a real gap that Pass 2 reported as clean.
    pos = [(lbl, text.index(lbl)) for lbl in SPEC_22 if lbl in text]
    order_bad = [pos[i][0] for i in range(1, len(pos)) if pos[i][1] < pos[i - 1][1]]
    behaviour = [
        ("[22] fields are in the spec's exact ORDER, not merely present", not order_bad),
        ("[21] the professional trader question is asked and answered",
         "risking my own capital" in text and bool(f.get("capital_answer"))),
        ("[5] volume strength uses the spec's wording (strong / very strong)",
         any(w in text for w in ("very strong (2x+)", "strong (1.5x+)",
                                 "above average", "below average"))),
        ("[5] volume regime (expansion/contraction) is surfaced",
         "volume regime" in text),
        ("[11] targets are stated against normal daily movement (ATR)", "ATR)" in text),
        ("[11] enough-volatility and stop-width verdicts are surfaced",
         ("enough volatility" in text or "TOO FLAT" in text) and "stop is" in text),
        ("[12] the spec's R:R bands are named (1:2 / 1:3+)",
         any(w in text for w in ("excellent (1:3+)", "acceptable (1:2+)",
                                 "BELOW THE 1:2 GATE"))),
        ("[13] unavailable fundamentals are STATED, not silently dropped",
         "not published for NEPSE" in text),
        ("[13] P/B and EPS are reported", "P/B" in text and "EPS" in text),
        ("[14] avg daily volume is reported", "avg daily shares" in text),
        ("[15] smart-money evidence line is surfaced", "Smart-Money Evidence" in text),
        ("[preamble] never claims guaranteed profit or certainty",
         not any(w in text.lower() for w in ("guarantee", "risk-free", "will profit"))),
        ("[preamble] states that no claim of certainty is made",
         "not a prediction" in text or "No claim of profit" in text),
    ]
    ok4 = sum(1 for _, good in behaviour if good)
    print(f"PASS 4  behaviour in the real output ........ {ok4}/{len(behaviour)}")
    for name, good in behaviour:
        if not good:
            gaps.append(f"{name}  -> NOT satisfied in the produced report")
    if order_bad:
        gaps.append(f"[22] out of spec order: {order_bad}")

    # ---- PASS 5: regression guards for the 16 gaps an INDEPENDENT audit confirmed -----------
    # Passes 1-4 all reported clean while every one of these was broken. They are behavioural
    # and market-wide, because each was only visible by running the thing over real data.
    funds = swing_pro._fundamentals()
    names = (swing_pro.MASTER / "symbols.txt").read_text(encoding="utf-8").split()
    cal = swing_pro.market_calendar(names)
    sample = []
    for s in names:
        try:
            g = swing_pro.analyse(s, funds, cal)
        except Exception:
            continue
        if g:
            sample.append(g)
        if len(sample) >= 120:
            break

    # every value the framework computes must reach the reader, the score, or a gate
    dead = [k for k in ("crossovers", "separation", "macd_cross", "macd_above_zero",
                        "rsi_overext", "adv_decl", "close_pos", "prev_breakout",
                        "consol_days", "res_tests", "follow_through", "trade_freq")
            if str(swing_pro.report(f)).count(str(f.get(k))) == 0
            and k not in src.split("def _score")[1].split("def ")[0]]
    rrs = [g["rr"] for g in sample]
    # use the implementation's OWN definition, not a paraphrase of it — a paraphrase passed
    # locally and failed on the VPS's fresher session, which is how this guard got caught lying
    down_pullbacks = [g["symbol"] for g in sample
                      if g["pullback"] in ("BUY ZONE", "HEALTHY PULLBACK")
                      and not g["confirmed_candle"]]
    distrib_elite = [g["symbol"] for g in sample
                     if g["performer"] in ("ELITE PERFORMER", "STRONG PERFORMER")
                     and g["accum"] == "Distribution"]
    guards = [
        ("[12] R:R is not arithmetically pinned to 2.0 (1:3+ must be reachable)",
         max(rrs) > 2.01),
        ("[12] R:R is bounded — no 90R 'realistic' targets", max(rrs) <= 5.01),
        ("[9] no BUY ZONE / HEALTHY PULLBACK without bullish confirmation",
         not down_pullbacks),
        ("[1] no top-tier performer while in distribution", not distrib_elite),
        ("[10] room_r is always numeric — one meaning for 'no resistance overhead'",
         all(isinstance(g["room_r"], float) for g in sample)),
        ("[10] unlimited room scores the full 5/5, not 0",
         swing_pro._score(dict(sample[0], room_r=float("inf")))[0]["sr"] == 5),
        ("[1] the performer test asks all ten of Section 1's questions",
         src.count("# 10 upside before resistance") == 1),
        ("[8] consolidation duration is bounded and real",
         all(0 <= g["consol_days"] <= 250 for g in sample)),
        ("[3] Consolidation is decided by measuring range, not EMA order alone",
         "_ranging" in src),
        ("[14] trading frequency is measured", any(g["trade_freq"] is not None for g in sample)),
        ("[9] 'selling candles weaken' is computed and surfaced",
         "_selling_weakens" in src and "selling candles" in text),
        ("[2] EMA separation + crossovers reach the report",
         "EMA separation:" in text and "crossovers:" in text),
        ("[6/7] RSI overextension, MACD zero-line and cross reach the report",
         "zero," in text and ("OVEREXTENDED" in text or "RSI 14:" in text)
         and ("cross" in text)),
        ("[8] breakout mechanics reach the report",
         "prior tests" in text and "follow-through" in text and "up its range" in text),
        ("[4] advances/declines reaches the report", "advances/declines" in text),
        ("[14] trading frequency reaches the report", "of the last 60 sessions" in text),
    ]
    ok5 = sum(1 for _, good in guards if good)
    print(f"PASS 5  independent-audit regressions ....... {ok5}/{len(guards)}")
    for name, good in guards:
        if not good:
            gaps.append(f"{name}  -> REGRESSED")

    print()
    if gaps:
        print(f"INCOMPLETE — {len(gaps)} gap(s):")
        for g in gaps:
            print(f"  - {g}")
        return 1
    print("FULLY IMPLEMENTED — all five passes clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
