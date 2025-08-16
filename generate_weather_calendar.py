#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import datetime as dt
import json
from typing import Dict, List, Optional
import urllib.parse
import urllib.request

try:
    # Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # Backfall: wir behandeln Zeiten dann wie naive lokale Zeiten


# ------------------------------
# Helpers
# ------------------------------

def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def safe_round_to_str(x: Optional[float], ndigits: int = 1) -> str:
    if isinstance(x, (int, float)):
        s = f"{x:.{ndigits}f}"
        return s.rstrip("0").rstrip(".")
    return "NA"


# ------------------------------
# Open-Meteo Fetch
# ------------------------------

def fetch_hourly_weather(lat: float, lon: float, days: int, timezone: str) -> Dict[str, List]:
    """
    Holt stündliche Wetterdaten für die Score-Berechnung und Anzeige.
    """
    hourly_vars = [
        "temperature_2m",
        "apparent_temperature",
        "weathercode",
        "precipitation_probability",
        "precipitation",
        "uv_index",
        "wind_speed_10m",
        "wind_gusts_10m",
        "relative_humidity_2m",
        "dew_point_2m",
        "visibility",
        "cloud_cover",
    ]
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(hourly_vars),
        "forecast_days": days,
        "timezone": timezone,
        "windspeed_unit": "kmh",   # für Wind-Scoring
        "precipitation_unit": "mm" # klar definieren
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url) as response:
        data = json.loads(response.read().decode())
    return data.get("hourly", {})


# ------------------------------
# Weathercode → Icon/Description
# ------------------------------

def map_weather_code_to_icon(code: int) -> str:
    if code == 0:
        return "🌞"  # sonnig
    elif code in {1, 2, 3}:
        return "⛅"  # (teilweise) bewölkt
    elif code in {45, 48}:
        return "🌁"  # neblig
    elif code in {51, 53, 55, 56, 57}:
        return "🌧️"  # Niesel/gefrierender Niesel
    elif code in {61, 63, 65, 80, 81, 82}:
        return "🌧️"  # Regen
    elif code in {66, 67}:
        return "🌧️❄️"  # gefrierender Regen
    elif code in {71, 73, 75, 77, 85, 86}:
        return "🌨️"  # Schnee
    elif code == 95:
        return "⛈️"  # Gewitter
    elif code in {96, 99}:
        return "🌩️❄️"  # Gewitter mit Hagel
    else:
        return "❔"  # unbekannt


def map_weather_code_to_description(code: int) -> str:
    if code == 0:
        return "sonnig"
    elif code in {1, 2, 3}:
        return "bewölkt"
    elif code in {45, 48}:
        return "neblig"
    elif code in {51, 53, 55, 56, 57}:
        return "Nieselregen"
    elif code in {61, 63, 65, 80, 81, 82}:
        return "regnerisch"
    elif code in {66, 67}:
        return "gefrierender Regen"
    elif code in {71, 73, 75, 77, 85, 86}:
        return "schneit"
    elif code == 95:
        return "Gewitter"
    elif code in {96, 99}:
        return "Gewitter mit Hagel"
    else:
        return "unbekannt"


# ------------------------------
# Scoring
# ------------------------------

def therm_comfort_score(apparent_temp: Optional[float]) -> float:
    """
    Thermalkomfort: Ideal 8–20 °C -> 10 Punkte.
    Linearer Abfall bis 0 bei ≤ -4 °C oder ≥ 32 °C.
    """
    if apparent_temp is None:
        return 5.0  # neutral, wenn kein Wert
    Ta = float(apparent_temp)
    # Formel: T = clamp(1 - max(0, |Ta-14| - 6) / 12, 0, 1) * 10
    val = 1.0 - max(0.0, abs(Ta - 14.0) - 6.0) / 12.0
    return clamp(val, 0.0, 1.0) * 10.0


def precip_score(precip_mm: Optional[float], precip_prob: Optional[float], weathercode: Optional[int]) -> float:
    """
    Niederschlag: Erwartete Rate E = Menge * (Wahrscheinlichkeit/100).
    10 Punkte bei E=0, linear zu 0 bei E >= 2 mm/h.
    Gefrierender Regen & Gewitter -> harte Abwertung (0).
    """
    if weathercode in {66, 67, 95, 96, 99}:
        return 0.0
    if precip_mm is None or precip_prob is None:
        # Wenn keine Angaben: neutral bis leicht vorsichtig
        return 6.0
    E = float(precip_mm) * float(precip_prob) / 100.0
    # 0 -> 10 Punkte, 2+ -> 0 Punkte
    if E <= 0:
        return 10.0
    if E >= 2.0:
        return 0.0
    return 10.0 * (1.0 - (E / 2.0))


def wind_score(wind_speed: Optional[float], wind_gust: Optional[float]) -> float:
    """
    Wind: 10 Punkte bei <= 10 km/h, 0 Punkte bei >= 40 km/h (linear).
    Zusätzlicher Malus -3 bei Böen > 60 km/h oder (Gust - Mean) > 20 km/h.
    """
    if wind_speed is None:
        base = 6.0
    else:
        ws = float(wind_speed)
        if ws <= 10.0:
            base = 10.0
        elif ws >= 40.0:
            base = 0.0
        else:
            # linear zwischen 10 und 40 km/h
            base = 10.0 * (1.0 - (ws - 10.0) / 30.0)

    if wind_gust is None:
        return clamp(base, 0.0, 10.0)

    gust = float(wind_gust)
    malus = 0.0
    if gust > 60.0:
        malus += 3.0
    if wind_speed is not None and (gust - float(wind_speed)) > 20.0:
        malus += 3.0

    return clamp(base - malus, 0.0, 10.0)


def uv_score(uv_index: Optional[float]) -> float:
    """
    UV ≤3: 10 | 4–5: 7 | 6–7: 4 | 8–9: 2 | ≥10: 0
    """
    if uv_index is None:
        return 6.0
    uv = float(uv_index)
    if uv <= 3:
        return 10.0
    elif uv <= 5:
        return 7.0
    elif uv <= 7:
        return 4.0
    elif uv <= 9:
        return 2.0
    else:
        return 0.0


def visibility_score(visibility_m: Optional[float], weathercode: Optional[int]) -> float:
    """
    Sicht: 10 Punkte bei ≥ 8 km, linear auf 0 bei ≤ 1 km.
    Nebelcodes 45/48: Deckel leicht (max 8).
    """
    if visibility_m is None:
        return 6.0
    km = float(visibility_m) / 1000.0
    if km >= 8.0:
        base = 10.0
    elif km <= 1.0:
        base = 0.0
    else:
        base = 10.0 * (km - 1.0) / (8.0 - 1.0)  # linear 1→8 km
    if weathercode in {45, 48}:
        base = min(base, 8.0)
    return clamp(base, 0.0, 10.0)


def humidity_dewpoint_score(dew_point_c: Optional[float]) -> float:
    """
    Taupunkt: 10 Punkte bei 7–13 °C.
    Linear auf 0 bei ≥ 22 °C (schwül) bzw. ≤ -10 °C (sehr trocken).
    """
    if dew_point_c is None:
        return 6.0
    dp = float(dew_point_c)
    if 7.0 <= dp <= 13.0:
        return 10.0
    if dp > 13.0:
        if dp >= 22.0:
            return 0.0
        # 13 ->10, 22 ->0
        return 10.0 * (1.0 - (dp - 13.0) / (22.0 - 13.0))
    else:
        if dp <= -10.0:
            return 0.0
        # -10 ->0, 7 ->10
        return 10.0 * ((dp - (-10.0)) / (7.0 - (-10.0)))


def code_baseline_score(weathercode: Optional[int]) -> float:
    """
    Grober Komfort/Traktion je nach Wettercode.
    """
    if weathercode is None:
        return 6.0
    c = int(weathercode)
    if c == 0:
        return 10.0
    if c in {1, 2, 3}:
        return 9.0
    if c in {45, 48}:
        return 5.0
    if c in {51, 53, 55, 61, 80}:  # leichtes Nass
        return 6.0
    if c in {56, 57, 63, 65, 81, 82, 71, 73, 75, 77, 85, 86}:
        return 2.0
    if c in {66, 67}:
        return 0.5
    if c in {95, 96, 99}:
        return 0.0
    return 5.0


def apply_safety_caps(score_raw: float,
                      weathercode: Optional[int],
                      temp_c: Optional[float],
                      precip_mm: Optional[float]) -> float:
    """
    Sicherheitskappen: Gewitter/Eisglätte etc.
    """
    c = weathercode
    s = score_raw
    # Gewitter -> max 2/10
    if c in {95, 96, 99}:
        s = min(s, 2.0)
    # Eisglätte (sehr kalt + Niederschlag) oder gefrierender Regen -> max 2/10
    if (temp_c is not None and precip_mm is not None and float(temp_c) <= -2.0 and float(precip_mm) > 0.0) or (c in {66, 67}):
        s = min(s, 2.0)
    return s


def rain_cap(score_after_caps: float,
             precip_mm: Optional[float],
             precip_prob: Optional[float]) -> float:
    """
    Regen-Cap: Sobald es nicht trocken ist (precip_mm > 0)
    ODER Regenwahrscheinlichkeit > 33 %, darf der Gesamtscore nicht über 5/10 liegen.
    """
    wet = False
    if precip_mm is not None and float(precip_mm) > 0.0:
        wet = True
    if precip_prob is not None and float(precip_prob) > 33.0:
        wet = True
    return min(score_after_caps, 5.0) if wet else score_after_caps


def compute_sport_score(
    apparent_temp: Optional[float],
    precip_mm: Optional[float],
    precip_prob: Optional[float],
    wind_speed: Optional[float],
    wind_gust: Optional[float],
    uv_index: Optional[float],
    visibility_m: Optional[float],
    dew_point_c: Optional[float],
    weathercode: Optional[int],
    air_temp: Optional[float],
) -> int:
    """
    Endscore 1..10 (integer). Gewichtungen nach Vorgabe:
    T 25%, R 35%, W 10%, U 15%, V 5%, H 5%, C 5%.
    Mit Sicherheits-Caps und Regen-Cap.
    """
    T = therm_comfort_score(apparent_temp)
    R = precip_score(precip_mm, precip_prob, weathercode)
    W = wind_score(wind_speed, wind_gust)
    U = uv_score(uv_index)
    V = visibility_score(visibility_m, weathercode)
    H = humidity_dewpoint_score(dew_point_c)
    C = code_baseline_score(weathercode)

    score_raw = (
        0.25 * T +
        0.35 * R +
        0.10 * W +
        0.15 * U +
        0.05 * V +
        0.05 * H +
        0.05 * C
    )

    # Sicherheitskappen (Gewitter/Eisglätte)
    score_capped = apply_safety_caps(score_raw, weathercode, air_temp, precip_mm)
    # Regen-Cap (nass oder prob>33%)
    score_capped = rain_cap(score_capped, precip_mm, precip_prob)

    # finaler Score 1..10
    return int(clamp(round(score_capped), 1.0, 10.0))


def score_to_rank_emoji(score: int) -> str:
    """
    Emoji-Mapping:
    🔝 10–8/10
    ↗️  7–6/10
    ➡️  5–4/10
    ↘️  3–2/10
    ⬇️  1–0/10
    """
    if score >= 8:
        return "🔝"
    elif score >= 6:
        return "↗️"
    elif score >= 4:
        return "➡️"
    elif score >= 2:
        return "↘️"
    else:
        return "⬇️"


# ------------------------------
# Calendar Builder
# ------------------------------

def build_calendar(hourly: Dict[str, List], timezone: str, hours_ahead: int = 24) -> str:
    """
    Baut einen iCalendar-Feed mit stündlichen Events:
    - Zeitfenster: ab jetzt bis hours_ahead
    - Bewertung/Score nur für Stunden zwischen 05:00 und 22:00 (lokal).
      Stunden außerhalb dieses Fensters werden nicht ausgegeben.
    - SUMMARY beginnt mit <Emoji><Score>/10 ...
    """
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    apps = hourly.get("apparent_temperature", [])
    codes = hourly.get("weathercode", [])
    probs = hourly.get("precipitation_probability", [])
    precs = hourly.get("precipitation", [])
    uvs = hourly.get("uv_index", [])
    winds = hourly.get("wind_speed_10m", [])
    gusts = hourly.get("wind_gusts_10m", [])
    hums = hourly.get("relative_humidity_2m", [])
    dews = hourly.get("dew_point_2m", [])
    vis = hourly.get("visibility", [])
    clouds = hourly.get("cloud_cover", [])

    # Zeitzone vorbereiten
    tzinfo = ZoneInfo(timezone) if ZoneInfo is not None else None
    now_local = dt.datetime.now(tzinfo) if tzinfo else dt.datetime.now()
    cutoff = now_local + dt.timedelta(hours=hours_ahead)

    cal_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "PRODID:-//Weather Calendar//OpenMeteo//DE",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]

    for idx, iso_time in enumerate(times):
        # Zeit parsen; Open-Meteo liefert lokale Zeit ohne Offset
        try:
            start_dt = dt.datetime.fromisoformat(iso_time)
        except ValueError:
            continue

        # lokal machen
        if tzinfo is not None and start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=tzinfo)

        # Filter: nur in [now, cutoff]
        if start_dt < now_local or start_dt > cutoff:
            continue

        # Bewertung nur zwischen 05:00 und 22:00 lokaler Zeit
        hour_local = start_dt.hour if start_dt.tzinfo else start_dt.hour
        if hour_local < 5 or hour_local > 22:
            continue

        end_dt = start_dt + dt.timedelta(hours=1)

        # Werte holen (Bounds prüfen)
        def g(arr: List, i: int) -> Optional[float]:
            return arr[i] if i < len(arr) else None

        temp = g(temps, idx)
        app = g(apps, idx)
        code = g(codes, idx)
        prob = g(probs, idx)
        prec = g(precs, idx)
        uv = g(uvs, idx)
        wind = g(winds, idx)
        gust = g(gusts, idx)
        # hum = g(hums, idx)  # aktuell nicht direkt im Score gebraucht
        dew = g(dews, idx)
        vis_m = g(vis, idx)
        # cloud = g(clouds, idx)  # derzeit nicht genutzt

        # Score berechnen
        score = compute_sport_score(
            apparent_temp=app,
            precip_mm=prec,
            precip_prob=prob,
            wind_speed=wind,
            wind_gust=gust,
            uv_index=uv,
            visibility_m=vis_m,
            dew_point_c=dew,
            weathercode=int(code) if code is not None else None,
            air_temp=temp
        )
        rank_emoji = score_to_rank_emoji(score)

        # Anzeigeelemente
        icon = map_weather_code_to_icon(int(code)) if code is not None else "❔"
        desc = map_weather_code_to_description(int(code)) if code is not None else "unbekannt"

        temp_str = safe_round_to_str(temp, 1)
        prob_str = f"{int(round(prob))}" if isinstance(prob, (int, float)) else "NA"
        prec_str = safe_round_to_str(prec, 1)
        uv_str = safe_round_to_str(uv, 1)

        # SUMMARY: Ranking-Emoji + numerischer Score zuerst
        summary = (
            f"{rank_emoji}{score}/10 "
            f"{icon}: {desc} - 🌡️{temp_str}°C - ☔ {prob_str}% / {prec_str}mm - ⛱️ {uv_str} UV"
        )

        # ICS-Zeiten formattieren (mit TZID)
        dtstart = start_dt.strftime("%Y%m%dT%H%M%S")
        dtend = end_dt.strftime("%Y%m%dT%H%M%S")
        tzid = timezone

        event_lines = [
            "BEGIN:VEVENT",
            f"UID:{dtstart}-{idx}@open-meteo",
            f"DTSTAMP:{dt.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART;TZID={tzid}:{dtstart}",
            f"DTEND;TZID={tzid}:{dtend}",
            f"SUMMARY:{summary}",
            "END:VEVENT",
        ]
        cal_lines.extend(event_lines)

    cal_lines.append("END:VCALENDAR")
    return "\r\n".join(cal_lines)


# ------------------------------
# Main
# ------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate an iCalendar feed with hourly weather and a sport suitability score (1–10)."
    )
    parser.add_argument("--lat", type=float, required=True, help="Latitude of the location")
    parser.add_argument("--lon", type=float, required=True, help="Longitude of the location")
    parser.add_argument("--days", type=int, default=4, help="Number of forecast days (max 16)")
    parser.add_argument("--timezone", type=str, default="Europe/Berlin", help="Timezone for event times (IANA name)")
    parser.add_argument("--out", type=str, default="weather.ics", help="Output .ics file path")
    parser.add_argument("--hours", type=int, default=24, help="Number of hours ahead to include (default 24)")

    args = parser.parse_args()

    hourly = fetch_hourly_weather(args.lat, args.lon, args.days, args.timezone)
    cal_content = build_calendar(hourly, args.timezone, hours_ahead=args.hours)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(cal_content)

    print(f"Calendar file written to {args.out}")


if __name__ == "__main__":
    main()
