#!/usr/bin/env python3
import argparse
import datetime as dt
import json
from typing import Dict, List
import urllib.parse
import urllib.request

def fetch_hourly_weather(lat: float, lon: float, days: int, timezone: str) -> Dict[str, List]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,weathercode,precipitation_probability,precipitation,uv_index",
        "forecast_days": days,
        "timezone": timezone,
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url) as response:
        data = json.loads(response.read().decode())
    return data.get("hourly", {})

def map_weather_code_to_icon(code: int) -> str:
    if code == 0:
        return "🌞"  # sonnig
    elif code in {1, 2, 3}:
        return "⛅"  # leicht bewölkt
    elif code in {45, 48}:
        return "🌁"  # neblig
    elif code in {51, 53, 55, 56, 57}:
        return "🌧️"  # Nieselregen
    elif code in {61, 63, 65, 80, 81, 82}:
        return "🌧️"  # regnerisch
    elif code in {66, 67}:
        return "🌧️❄️"  # gefrierender Regen (ersatzweise)
    elif code in {71, 73, 75, 77, 85, 86}:
        return "🌨️"  # schneit
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

def build_calendar(hourly: Dict[str, List], timezone: str, hours_ahead: int = 24) -> str:
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    codes = hourly.get("weathercode", [])
    probs = hourly.get("precipitation_probability", [])
    precs = hourly.get("precipitation", [])
    uvs = hourly.get("uv_index", [])

    now_local = dt.datetime.now()
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
        try:
            start_dt = dt.datetime.fromisoformat(iso_time)
        except ValueError:
            continue
        if start_dt < now_local or start_dt > cutoff:
            continue

        end_dt = start_dt + dt.timedelta(hours=1)
        dtstart = start_dt.strftime("%Y%m%dT%H%M%S")
        dtend = end_dt.strftime("%Y%m%dT%H%M%S")

        temp = temps[idx] if idx < len(temps) else None
        code = codes[idx] if idx < len(codes) else None
        prob = probs[idx] if idx < len(probs) else None
        prec = precs[idx] if idx < len(precs) else None
        uv = uvs[idx] if idx < len(uvs) else None

        icon = map_weather_code_to_icon(code) if code is not None else "❔"
        desc = map_weather_code_to_description(code) if code is not None else "unbekannt"

        temp_str = f"{temp:.1f}".rstrip("0").rstrip(".") if isinstance(temp, (int, float)) else "NA"
        prob_str = f"{int(round(prob))}" if isinstance(prob, (int, float)) else "NA"
        prec_str = f"{prec:.1f}".rstrip("0").rstrip(".") if isinstance(prec, (int, float)) else "NA"
        uv_str = f"{uv:.1f}".rstrip("0").rstrip(".") if isinstance(uv, (int, float)) else "NA"

        summary = (
            f"{icon}: {desc} -🌡️{temp_str}°C - ☔ {prob_str}% / {prec_str}mm - ⛱️ {uv_str} UV"
        )

        event_lines = [
            "BEGIN:VEVENT",
            f"UID:{dtstart}-{idx}@open-meteo",
            f"DTSTAMP:{dt.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART;TZID={timezone}:{dtstart}",
            f"DTEND;TZID={timezone}:{dtend}",
            f"SUMMARY:{summary}",
            "END:VEVENT",
        ]
        cal_lines.extend(event_lines)

    cal_lines.append("END:VCALENDAR")
    return "\r\n".join(cal_lines)

def main():
    parser = argparse.ArgumentParser(description="Generate an iCalendar feed with hourly weather data from Open-Meteo.")
    parser.add_argument("--lat", type=float, required=True, help="Latitude of the location")
    parser.add_argument("--lon", type=float, required=True, help="Longitude of the location")
    parser.add_argument("--days", type=int, default=4, help="Number of forecast days (max 16)")
    parser.add_argument("--timezone", type=str, default="Europe/Berlin", help="Timezone for event times")
    parser.add_argument("--out", type=str, default="weather.ics", help="Output .ics file path")
    parser.add_argument("--hours", type=int, default=24, help="Number of hours ahead to include in the feed (default 24)")

    args = parser.parse_args()

    hourly = fetch_hourly_weather(args.lat, args.lon, args.days, args.timezone)
    cal_content = build_calendar(hourly, args.timezone, hours_ahead=args.hours)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(cal_content)
    print(f"Calendar file written to {args.out}")

if __name__ == "__main__":
    main()
