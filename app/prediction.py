"""Statistical price trend analysis and a sell/hold recommendation.

No ML model - this is deliberately a transparent, multi-factor statistical
approach rather than a single "trend is negative -> sell" rule. It weighs
several independent signals and only recommends selling/holding when they
line up, explaining exactly which factors drove the call:

- medium-term trend (~30 days): the primary direction signal
- short-term trend (~7 days): momentum - is it accelerating, or reversing
  against the medium-term trend?
- percentile rank: where the current price sits within the recent price
  range (robust to the fat-tailed, non-normal swings real market data has -
  a plain z-score assumes a normal distribution that skin prices don't follow)
- volume trend: rising volume into a price drop suggests real selling
  pressure; falling volume into a drop is thinner and less trustworthy

Each factor casts a small vote; only a clear majority produces a strong
recommendation. When signals conflict (e.g. long-term down but short-term
recovering), that's surfaced explicitly instead of averaged away.
"""
import time

MIN_POINTS_FOR_TREND = 3

# Official Steam price history can span many years. Using the entire history
# to compute trend/mean/std dilutes any real signal into near-zero noise -
# a decade-long straight-line fit barely moves, and a decade-wide price range
# inflates the standard deviation so today's price never looks unusual next
# to it. We instead look at recent windows and only widen them if there
# isn't enough data yet.
MEDIUM_WINDOW_CANDIDATES_DAYS = [30, 90, 365]
SHORT_WINDOW_CANDIDATES_DAYS = [7, 14, 30]

TREND_FLAT_THRESHOLD_PCT = 0.15   # %/day below this magnitude counts as "flat"
PERCENTILE_HIGH = 0.8
PERCENTILE_LOW = 0.2
SELL_SCORE_THRESHOLD = 2
HOLD_SCORE_THRESHOLD = -2


def _linreg(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Least-squares slope & intercept, no numpy dependency needed for this size."""
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return 0.0, mean_y
    slope = num / den
    intercept = mean_y - slope * mean_x
    return slope, intercept


def _select_window(points: list[dict], candidates_days: list[int]) -> list[dict]:
    latest_ts = points[-1]["ts"]
    for window_days in candidates_days:
        candidate = [p for p in points if latest_ts - p["ts"] <= window_days * 86400]
        if len(candidate) >= MIN_POINTS_FOR_TREND:
            return candidate
    return points


def _trend_stats(points: list[dict]) -> dict:
    t0 = points[0]["ts"]
    xs = [(p["ts"] - t0) / 86400.0 for p in points]
    ys = [p["price"] for p in points]

    slope, intercept = _linreg(xs, ys)
    mean_price = sum(ys) / len(ys)
    variance = sum((y - mean_price) ** 2 for y in ys) / len(ys)
    std_dev = variance ** 0.5
    trend_pct_per_day = (slope / mean_price * 100) if mean_price > 0 else 0.0

    return {
        "slope": slope,
        "intercept": intercept,
        "mean_price": mean_price,
        "std_dev": std_dev,
        "trend_pct_per_day": trend_pct_per_day,
        "last_x": xs[-1],
        "span_days": xs[-1],
    }


def _percentile_rank(value: float, values: list[float]) -> float:
    n = len(values)
    if n == 0:
        return 0.5
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return (below + 0.5 * equal) / n


def _volume_trend(points: list[dict]) -> str | None:
    """Compares average volume in the first vs second half of the window."""
    vols = [p["volume"] for p in points if p.get("volume") is not None]
    if len(vols) < 4:
        return None
    mid = len(vols) // 2
    first_half, second_half = vols[:mid], vols[mid:]
    avg_first = sum(first_half) / len(first_half)
    avg_second = sum(second_half) / len(second_half)
    if avg_first == 0:
        return None
    change = (avg_second - avg_first) / avg_first
    if change >= 0.2:
        return "rising"
    if change <= -0.2:
        return "falling"
    return "flat"


def analyze(history: list[dict], current_price: float | None, current_volume: int | None) -> dict:
    """history: list of {ts, price, volume} sorted ascending by ts."""
    if current_price is None:
        return {
            "status": "no_price",
            "message": "לא נמצא מחיר נוכחי עבור הפריט.",
        }

    points = [h for h in history if h.get("price") is not None]

    if len(points) < MIN_POINTS_FOR_TREND:
        return {
            "status": "insufficient_history",
            "current_price": current_price,
            "data_points": len(points),
            "recommendation": "המתן",
            "confidence": "low",
            "message": (
                f"נאספו רק {len(points)} נקודות מחיר היסטוריות עד כה. "
                "האפליקציה בונה היסטוריה משלה בכל בדיקה - חזור לבדוק את הפריט הזה "
                "מדי כמה ימים כדי לקבל תחזית וסטטיסטיקה מהימנות יותר."
            ),
        }

    medium_points = _select_window(points, MEDIUM_WINDOW_CANDIDATES_DAYS)
    short_points = _select_window(points, SHORT_WINDOW_CANDIDATES_DAYS)

    medium = _trend_stats(medium_points)
    short = _trend_stats(short_points) if len(short_points) >= MIN_POINTS_FOR_TREND else None

    medium_prices = [p["price"] for p in medium_points]
    recent_window = medium_prices[-min(10, len(medium_prices)):]
    moving_avg = sum(recent_window) / len(recent_window)
    z_score = (current_price - moving_avg) / medium["std_dev"] if medium["std_dev"] > 0 else 0.0
    percentile = _percentile_rank(current_price, medium_prices)

    predicted_7d = medium["intercept"] + medium["slope"] * (medium["last_x"] + 7)
    predicted_7d = max(predicted_7d, 0.0)

    volume_trend = _volume_trend(medium_points)
    low_liquidity = current_volume is not None and current_volume < 5

    # ---- Multi-factor scoring: each signal casts a small vote. A single
    # factor never decides the call on its own. ----
    score = 0.0
    factors: list[str] = []

    if medium["trend_pct_per_day"] <= -TREND_FLAT_THRESHOLD_PCT:
        score += 1
        factors.append(f"מגמה ארוכה (~{round(medium['span_days'])} יום) יורדת ({medium['trend_pct_per_day']:+.2f}% ליום)")
    elif medium["trend_pct_per_day"] >= TREND_FLAT_THRESHOLD_PCT:
        score -= 1
        factors.append(f"מגמה ארוכה (~{round(medium['span_days'])} יום) עולה ({medium['trend_pct_per_day']:+.2f}% ליום)")

    medium_down = medium["trend_pct_per_day"] <= -TREND_FLAT_THRESHOLD_PCT
    medium_up = medium["trend_pct_per_day"] >= TREND_FLAT_THRESHOLD_PCT

    short_conflicts_medium = False
    if short is not None:
        short_down = short["trend_pct_per_day"] <= -TREND_FLAT_THRESHOLD_PCT
        short_up = short["trend_pct_per_day"] >= TREND_FLAT_THRESHOLD_PCT
        short_conflicts_medium = (medium_down and short_up) or (medium_up and short_down)

        if short_down:
            score += 1
            if medium_up:
                factors.append(f"מגמה קצרה (~{round(short['span_days'])} ימים) מתהפכת כלפי מטה ({short['trend_pct_per_day']:+.2f}% ליום) - ייתכן סימן להיחלשות העלייה")
            else:
                factors.append(f"מגמה קצרה (~{round(short['span_days'])} ימים) ממשיכה לרדת ({short['trend_pct_per_day']:+.2f}% ליום)")
        elif short_up:
            score -= 1
            if medium_down:
                factors.append(f"מגמה קצרה (~{round(short['span_days'])} ימים) מתהפכת כלפי מעלה ({short['trend_pct_per_day']:+.2f}% ליום) - ייתכן סימן להתאוששות")
            else:
                factors.append(f"מגמה קצרה (~{round(short['span_days'])} ימים) ממשיכה לעלות ({short['trend_pct_per_day']:+.2f}% ליום)")

    if percentile >= PERCENTILE_HIGH:
        score += 1
        factors.append(f"המחיר הנוכחי נמצא באחוזון {round(percentile * 100)} מהטווח האחרון - יחסית גבוה")
    elif percentile <= PERCENTILE_LOW:
        score -= 1
        factors.append(f"המחיר הנוכחי נמצא באחוזון {round(percentile * 100)} מהטווח האחרון - יחסית נמוך")

    if volume_trend == "rising" and medium["trend_pct_per_day"] < 0:
        score += 1
        factors.append("נפח המסחר עולה תוך כדי ירידת המחיר - סימן למכירה אמיתית, לא רעש")
    elif volume_trend == "falling" and medium["trend_pct_per_day"] < 0:
        score -= 0.5
        factors.append("נפח המסחר יורד - ירידת המחיר פחות אמינה בגלל נזילות נמוכה יותר")

    if score >= SELL_SCORE_THRESHOLD:
        recommendation = "מכור"
    elif score <= HOLD_SCORE_THRESHOLD:
        recommendation = "החזק"
    else:
        recommendation = "המתן"

    warnings: list[str] = []
    if short_conflicts_medium:
        warnings.append("המגמה הקצרה סותרת את הארוכה, כך שהאות פחות חד-משמעי.")

    confidence = "medium" if len(medium_points) < 20 else "high"
    if low_liquidity:
        confidence = "low"
        warnings.append("נפח המסחר בפריט זה נמוך, מה שהופך כל תחזית לפחות אמינה.")
    if short_conflicts_medium and confidence == "high":
        confidence = "medium"

    if factors:
        reason = "; ".join(factors) + "."
    else:
        reason = "אין איתות ברור באף אחד מהגורמים שנבדקו - המחיר והמגמה קרובים לנורמה עבור הפריט."
    if warnings:
        reason += " שימו לב: " + " ".join(warnings)

    return {
        "status": "ok",
        "current_price": round(current_price, 2),
        "data_points": len(medium_points),
        "moving_average": round(moving_avg, 2),
        "std_dev": round(medium["std_dev"], 2),
        "z_score": round(z_score, 2),
        "percentile_rank": round(percentile * 100),
        "trend_per_day": round(medium["slope"], 4),
        "trend_pct_per_day": round(medium["trend_pct_per_day"], 3),
        "short_trend_pct_per_day": round(short["trend_pct_per_day"], 3) if short else None,
        "volume_trend": volume_trend,
        "score": score,
        "predicted_price_7d": round(predicted_7d, 2),
        "volume": current_volume,
        "low_liquidity": low_liquidity,
        "recommendation": recommendation,
        "confidence": confidence,
        "message": reason,
        "factors": factors,
        "warnings": warnings,
        "history_span_days": round(medium["span_days"], 1),
        "generated_at": int(time.time()),
    }
