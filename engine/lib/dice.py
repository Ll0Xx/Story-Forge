"""Dice roller.

CLI:     python3 -m dice <expr> [--dc N] [--label TEXT] [--times N]
Module:  from dice import roll, check

Expressions: 1d20+5, 2d6+1d4-1, d100, 4d6kh3 (keep highest 3), 2d20kl1 (keep lowest 1).
Output is one JSON object, e.g.
  {"expr": "1d20+5", "rolls": [[14]], "total": 19, "natural": 14, "dc": 15, "success": true, "margin": 4}
"natural" is present when the roll has exactly one kept d20 (useful for crits).
"""
import argparse
import json
import random
import re
import sys

_TERM = re.compile(r"([+-]?)(?:(\d*)d(\d+)(?:(kh|kl)(\d+))?|(\d+))", re.IGNORECASE)


def roll(expr: str) -> dict:
    text = expr.replace(" ", "").lower()
    if not text:
        raise ValueError("empty dice expression")
    pos, total, rolls, kept_d20 = 0, 0, [], []
    while pos < len(text):
        m = _TERM.match(text, pos)
        if not m or m.end() == pos:
            raise ValueError(f"cannot parse dice expression at: {text[pos:]!r}")
        sign = -1 if m.group(1) == "-" else 1
        if m.group(6) is not None:
            total += sign * int(m.group(6))
        else:
            count = int(m.group(2) or 1)
            sides = int(m.group(3))
            if not (1 <= count <= 1000 and sides >= 1):
                raise ValueError(f"bad dice term: {m.group(0)}")
            dice = [random.randint(1, sides) for _ in range(count)]
            kept = dice
            if m.group(4):
                n = int(m.group(5))
                kept = sorted(dice, reverse=m.group(4) == "kh")[:n]
            rolls.append(dice)
            total += sign * sum(kept)
            if sides == 20:
                kept_d20.extend(kept)
        pos = m.end()
    result = {"expr": expr, "rolls": rolls, "total": total}
    if len(kept_d20) == 1:
        result["natural"] = kept_d20[0]
    return result


def check(expr: str, dc: int) -> dict:
    result = roll(expr)
    result.update(dc=dc, success=result["total"] >= dc, margin=result["total"] - dc)
    return result


def main(argv=None):
    p = argparse.ArgumentParser(prog="dice", description="Roll dice; prints JSON.")
    p.add_argument("expr")
    p.add_argument("--dc", type=int, help="difficulty to meet or beat")
    p.add_argument("--label", help="what the roll is for")
    p.add_argument("--times", type=int, default=1, help="roll several times")
    args = p.parse_args(argv)
    try:
        results = []
        for _ in range(max(1, args.times)):
            r = check(args.expr, args.dc) if args.dc is not None else roll(args.expr)
            if args.label:
                r["label"] = args.label
            results.append(r)
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
    print(json.dumps(results[0] if len(results) == 1 else results, ensure_ascii=False))


if __name__ == "__main__":
    main()
