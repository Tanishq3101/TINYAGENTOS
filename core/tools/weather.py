# core/tools/weather.py

import requests


class WeatherTool:
    def run(self, city: str) -> str:
        if not city:
            return "No city provided"

        city = city.strip()

        url = f"https://wttr.in/{city}?format=3"  # ✅ FIXED

        try:
            res = requests.get(url, timeout=5)

            if res.status_code == 200:
                return res.text.strip()

            return "Weather service error"

        except Exception:
            return "Weather service unavailable"
