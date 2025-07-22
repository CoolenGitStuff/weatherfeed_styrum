#!/usr/bin/env python3
"""
Generate an iCalendar feed with hourly weather data from Open‑Meteo for a specified
location and forecast horizon. Each calendar event covers one hour and the
summary contains temperature, an icon representing the weather condition,
precipitation probability, precipitation amount and UV index.

Usage (example):
    python generate_weather_calendar.py --lat 51.45126 --lon 6.86418 \
        --days 4 --timezone Europe/Berlin --out styrum_weather.ics

This script fetches data from the free Open‑Meteo API:contentReference[oaicite:0]{index=0},
so no API key is required. It is designed for non‑commercial use and
returns up‑to‑date hourly forecasts:contentReference[oaicite:1]{index=1}.
"""
import argparse
import datetime as dt
import json
import sys
from typing import Dict, List
import urllib.parse
import urllib.request


def fetch_hourly_weather(lat: float, lon: float, days: int, timezone: str) -> Dict[str, List]:
    """Fetch hourly weather data from Open‑Meteo.

    Returns a dictionary containing time, temperature, weathercode,
    precipitation_probability, precipitation and uv_index arrays.
    """
    # Build the API URL. We request a few days of forecast and adjust timezone.
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

    hourly = data.get("hourly", {})
    return hourly


def map_weather_code_to_icon(code: int) -> str:
    """Map WMO weather code to a representative Unicode icon.

    The mapping is derived from the WMO weather interpretation codes table:contentReference[oaicite:2]{index=2}.
    """
    # Consolidate codes into categories for icons.
    if code == 0:
        return "☀"  # Clear sky
    elif code in {1, 2, 3}:
        return "⛅"  # Mainly clear, partly cloudy, overcast
    elif code in {45, 48}:
        return "🌫"  # Fog
    elif code in {51, 53, 55, 56, 57}:  # drizzle and freezing drizzle
        return "🌦"  # light rain drizzle icon
    elif code in {61, 63, 65, 80, 81, 82}:  # rain and rain showers
        return "🌧"  # Rain
    elif code in {66, 67}:  # freezing rain
        return "🌧"  # Represent as rain
    elif code in {71, 73, 75, 77, 85, 86}:  # snow and snow showers
        return "🌨"  # Snow
    elif code == 95:
        return "⛈"  # Thunderstorm
    elif code in {96, 99}:
        return "🌩"  # Thunderstorm with hail
    else:
        return "❔"  # Unknown/other


def build_calendar(hourly: Dict[str, List], timezone: str, hours_ahead: int = 24) -> str:
    """Construct iCalendar content from the hourly weather data.

    Only include events starting within the next ``hours_ahead`` hours from now.  A
    new feed should therefore only contain the next day's worth of events when
    regenerated each hour, as requested by the user.
    """
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    codes = hourly.get("weathercode", [])
    probs = hourly.get("precipitation_probability", [])
    precs = hourly.get("precipitation", [])
    uvs = hourly.get("uv_index", [])

    # Determine the cutoff time for inclusion. We use the local timezone the API
    # returned (``timezone``). Because the times strings are already in this
    # timezone, we compare them directly using naive datetime objects.  The
    # current time is taken from the system clock.
    now_local = dt.datetime.now()
    cutoff = now_local + dt.timedelta(hours=hours_ahead)

    # Build the calendar header with refresh hints. According to RFC 7986 and
    # widely used iCalendar extensions, REFRESH-INTERVAL and X-PUBLISHED-TTL
    # suggest how often clients should update:contentReference[oaicite:3]{index=3}.
    cal_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "PRODID:-//Weather Calendar//OpenMeteo//DE",
        # Suggest hourly refresh; actual behaviour depends on the client.
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]

    for idx, iso_time in enumerate(times):
        try:
            start_dt = dt.datetime.fromisoformat(iso_time)
        except ValueError:
            # Skip malformed timestamps
            continue
        # Only include events starting between now_local and cutoff
        if start_dt < now_local or start_dt > cutoff:
            continue
        end_dt = start_dt + dt.timedelta(hours=1)

        # Format times for iCalendar. Use DATE-TIME with TZID to keep local time.
        dtstart = start_dt.strftime("%Y%m%dT%H%M%S")
        dtend = end_dt.strftime("%Y%m%dT%H%M%S")

        # Extract weather parameters; fallback to None if missing.
        temp = temps[idx] if idx < len(temps) else None
        code = codes[idx] if idx < len(codes) else None
        prob = probs[idx] if idx < len(probs) else None
        prec = precs[idx] if idx < len(precs) else None
        uv = uvs[idx] if idx < len(uvs) else None

        # Map code to icon.
        icon = map_weather_code_to_icon(code) if code is not None else "❔"

        # Format pieces with appropriate units. Use integers where possible.
        temp_str = f"{temp:.1f}".rstrip("0").rstrip(".") if isinstance(temp, (int, float)) else "NA"
        prob_str = f"{int(round(prob))}" if isinstance(prob, (int, float)) else "NA"
        # Precipitation is in mm and can be zero; keep one decimal if needed.
        prec_str = f"{prec:.1f}".rstrip("0").rstrip(".") if isinstance(prec, (int, float)) else "NA"
        uv_str = f"{uv:.1f}".rstrip("0").rstrip(".") if isinstance(uv, (int, float)) else "NA"

        summary_parts = [
            f"{temp_str}°C",
            icon,
            f"{prob_str}%",
            f"{prec_str}mm",
            f"UV {uv_str}",
        ]
        summary = " ".join(summary_parts)

        # Build VEVENT section
        event_lines = [
            "BEGIN:VEVENT",
            f"UID:{dtstart}-{idx}@open-meteo",  # Unique ID for each event
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
    parser = argparse.ArgumentParser(description="Generate an iCalendar feed with hourly weather data from Open‑Meteo.")
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
