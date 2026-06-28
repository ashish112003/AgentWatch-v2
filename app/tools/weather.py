"""
app/tools/weather.py
─────────────────────
LangChain Weather Tool — mock weather service.

This is a mock implementation because:
  1. We don't want real API keys (e.g. OpenWeatherMap) as a hard
     dependency for running and testing AgentWatch.
  2. The governance and observability features we're building are
     about HOW tools are called, not WHAT they return.
  3. Mock responses are deterministic — tests don't flake due to
     network conditions or API rate limits.

Mock design:
  • 12 major cities have realistic hardcoded weather data.
  • All other cities get procedurally generated data derived from
    a hash of the city name, so the same city always returns the
    same weather (deterministic), but different cities vary.
  • Responses mimic the structure of a real weather API, including
    temperature, humidity, wind speed, UV index, and a description,
    so the LLM can reason about and explain the data naturally.

In a real production system:
  Replace _fetch_weather() with an actual HTTP call to:
    https://api.openweathermap.org/data/2.5/weather?q={city}&appid={key}
  and parse the response into the same WeatherData structure.

The @tool decorator registers this function as a LangChain tool,
making it available for tool-calling LLMs to invoke automatically.
"""

# import hashlib
# from dataclasses import dataclass
# from langchain_core.tools import tool


# # ── Weather data model ────────────────────────────────────────────────────────

# @dataclass
# class WeatherData:
#     """Structured weather observation for a city."""
#     city:          str
#     country:       str
#     temperature_c: float    # Celsius
#     feels_like_c:  float    # Apparent temperature
#     humidity_pct:  int      # 0–100 %
#     wind_kmh:      float    # km/h
#     description:   str      # Human-readable condition
#     uv_index:      float    # 0–11+ scale
#     visibility_km: float    # km


# def _format_weather(data: WeatherData) -> str:
#     """
#     Format WeatherData into a structured natural-language string.

#     The format is intentionally verbose so the LLM has rich context
#     to answer follow-up questions like "should I bring an umbrella?"
#     """
#     temp_f = round(data.temperature_c * 9 / 5 + 32, 1)
#     feels_f = round(data.feels_like_c * 9 / 5 + 32, 1)

#     return (
#         f"Weather in {data.city}, {data.country}:\n"
#         f"  Condition:   {data.description}\n"
#         f"  Temperature: {data.temperature_c}°C ({temp_f}°F)\n"
#         f"  Feels like:  {data.feels_like_c}°C ({feels_f}°F)\n"
#         f"  Humidity:    {data.humidity_pct}%\n"
#         f"  Wind:        {data.wind_kmh} km/h\n"
#         f"  UV Index:    {data.uv_index}\n"
#         f"  Visibility:  {data.visibility_km} km"
#     )


# # ── Hardcoded city data ───────────────────────────────────────────────────────
# # Representative data for major cities based on typical seasonal averages.
# # Keys are lowercase for case-insensitive lookup.

# _CITY_DATA: dict[str, WeatherData] = {
#     "new york": WeatherData(
#         city="New York", country="US",
#         temperature_c=18.5, feels_like_c=17.2, humidity_pct=62,
#         wind_kmh=14.4, description="Partly cloudy", uv_index=5.2, visibility_km=16.0
#     ),
#     "london": WeatherData(
#         city="London", country="GB",
#         temperature_c=13.2, feels_like_c=11.8, humidity_pct=78,
#         wind_kmh=19.8, description="Overcast with light drizzle", uv_index=2.1, visibility_km=9.5
#     ),
#     "tokyo": WeatherData(
#         city="Tokyo", country="JP",
#         temperature_c=22.0, feels_like_c=23.1, humidity_pct=70,
#         wind_kmh=10.8, description="Clear sky", uv_index=7.4, visibility_km=20.0
#     ),
#     "sydney": WeatherData(
#         city="Sydney", country="AU",
#         temperature_c=20.5, feels_like_c=19.8, humidity_pct=65,
#         wind_kmh=22.0, description="Sunny with a sea breeze", uv_index=8.0, visibility_km=18.0
#     ),
#     "paris": WeatherData(
#         city="Paris", country="FR",
#         temperature_c=15.8, feels_like_c=14.5, humidity_pct=72,
#         wind_kmh=16.2, description="Mostly cloudy", uv_index=3.5, visibility_km=12.0
#     ),
#     "dubai": WeatherData(
#         city="Dubai", country="AE",
#         temperature_c=38.5, feels_like_c=42.0, humidity_pct=45,
#         wind_kmh=18.0, description="Hazy sunshine", uv_index=10.8, visibility_km=7.0
#     ),
#     "mumbai": WeatherData(
#         city="Mumbai", country="IN",
#         temperature_c=30.2, feels_like_c=36.5, humidity_pct=88,
#         wind_kmh=12.6, description="Humid with thunderstorm risk", uv_index=9.2, visibility_km=5.0
#     ),
#     "hyderabad": WeatherData(
#         city="Hyderabad", country="IN",
#         temperature_c=32.0, feels_like_c=35.4, humidity_pct=58,
#         wind_kmh=14.0, description="Hot and sunny", uv_index=9.8, visibility_km=15.0
#     ),
#     "toronto": WeatherData(
#         city="Toronto", country="CA",
#         temperature_c=10.4, feels_like_c=8.1, humidity_pct=55,
#         wind_kmh=24.5, description="Windy and cool", uv_index=4.0, visibility_km=20.0
#     ),
#     "berlin": WeatherData(
#         city="Berlin", country="DE",
#         temperature_c=12.0, feels_like_c=10.2, humidity_pct=68,
#         wind_kmh=17.3, description="Light rain", uv_index=2.8, visibility_km=10.5
#     ),
#     "singapore": WeatherData(
#         city="Singapore", country="SG",
#         temperature_c=29.5, feels_like_c=34.0, humidity_pct=82,
#         wind_kmh=8.0, description="Tropical showers likely", uv_index=9.5, visibility_km=11.0
#     ),
#     "cape town": WeatherData(
#         city="Cape Town", country="ZA",
#         temperature_c=17.3, feels_like_c=16.0, humidity_pct=60,
#         wind_kmh=28.0, description="Strong Cape Doctor winds", uv_index=6.5, visibility_km=22.0
#     ),
# }


# # ── Procedural fallback ───────────────────────────────────────────────────────

# def _generate_city_weather(city: str) -> WeatherData:
#     """
#     Procedurally generate weather data for unknown cities.

#     Uses a SHA-256 hash of the city name to derive deterministic values,
#     so the same city always gets the same weather while different cities
#     get different (but realistic) values.  This makes the tool useful
#     in demos without needing a real API.

#     Args:
#         city: City name (already lowercased by the caller).

#     Returns:
#         WeatherData with procedurally generated values.
#     """
#     # Hash the city name to get a deterministic seed.
#     seed = int(hashlib.sha256(city.encode()).hexdigest(), 16)

#     def _rng(mn: float, mx: float, bits: int = 16) -> float:
#         """Extract a float in [mn, mx] from the seed using bit slicing."""
#         chunk = (seed >> bits) & 0xFFFF
#         return mn + (chunk / 0xFFFF) * (mx - mn)

#     # Derive values from different bit slices of the hash.
#     temp          = round(_rng(-5, 40, 0),  1)
#     feels_like    = round(temp + _rng(-4, 4, 16), 1)
#     humidity      = int(_rng(20, 95, 32))
#     wind          = round(_rng(2, 50, 48), 1)
#     uv            = round(_rng(0, 11, 64), 1)
#     visibility    = round(_rng(2, 25, 80), 1)

#     conditions = [
#         "Clear sky", "Partly cloudy", "Mostly cloudy",
#         "Overcast", "Light rain", "Heavy rain",
#         "Thunderstorms", "Foggy", "Snowy", "Windy",
#     ]
#     condition_idx = int(_rng(0, len(conditions), 96)) % len(conditions)

#     return WeatherData(
#         city=city.title(),
#         country="??",    # Unknown country for procedural cities
#         temperature_c=temp,
#         feels_like_c=feels_like,
#         humidity_pct=humidity,
#         wind_kmh=wind,
#         description=conditions[condition_idx],
#         uv_index=uv,
#         visibility_km=visibility,
#     )


# def _fetch_weather(city: str) -> WeatherData:
#     """
#     Return weather data for a city, from hardcoded data or procedural generation.

#     Args:
#         city: City name (any case).

#     Returns:
#         WeatherData instance.
#     """
#     key = city.strip().lower()
#     if key in _CITY_DATA:
#         return _CITY_DATA[key]
#     return _generate_city_weather(key)


# # ── LangChain Tool ────────────────────────────────────────────────────────────

# @tool
# def weather(city: str) -> str:
#     """
#     Get the current weather conditions for a city.

#     Returns temperature (Celsius and Fahrenheit), humidity, wind speed,
#     weather description, UV index, and visibility for the requested city.

#     Args:
#         city: Name of the city to get weather for (e.g. "London", "Tokyo").

#     Returns:
#         A formatted weather report string for the given city.
#     """
#     if not city or not city.strip():
#         return "Error: Please provide a city name."

#     city = city.strip()
#     if len(city) > 100:
#         return "Error: City name is too long."

#     data = _fetch_weather(city)
#     return _format_weather(data)

"""
app/tools/weather.py
─────────────────────
LangChain Weather Tool — live data from OpenWeatherMap free tier.

API used: OpenWeatherMap Current Weather
Endpoint: https://api.openweathermap.org/data/2.5/weather
Free tier: 1,000 calls/day, no credit card required.
Sign up:   https://openweathermap.org/api

Falls back to a helpful error message if the API key is missing
or the city is not found — never crashes the agent run.
"""

import os
import urllib.request
import urllib.parse
import json
from langchain_core.tools import tool


def _fetch_live_weather(city: str) -> str:
    """
    Call OpenWeatherMap API and return a formatted weather string.

    Args:
        city: City name as provided by the LLM.

    Returns:
        Formatted weather string or an error message.
    """
    api_key = os.getenv("OPENWEATHER_API_KEY", "")

    if not api_key:
        return (
            "Weather service is not configured. "
            "Set OPENWEATHER_API_KEY in environment variables."
        )

    encoded_city = urllib.parse.quote(city)
    url = (
        f"https://api.openweathermap.org/data/2.5/weather"
        f"?q={encoded_city}&appid={api_key}&units=metric"
    )

    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            data = json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return f"City '{city}' not found. Please check the spelling."
        if e.code == 401:
            return "Weather API key is invalid. Please check OPENWEATHER_API_KEY."
        return f"Weather API error: HTTP {e.code}."
    except Exception as e:
        return f"Could not fetch weather: {e}"

    # Parse response
    city_name   = data.get("name", city)
    country     = data.get("sys", {}).get("country", "??")
    temp_c      = data["main"]["temp"]
    feels_c     = data["main"]["feels_like"]
    humidity    = data["main"]["humidity"]
    wind_kmh    = round(data["wind"]["speed"] * 3.6, 1)
    description = data["weather"][0]["description"].capitalize()
    visibility  = round(data.get("visibility", 0) / 1000, 1)
    temp_f      = round(temp_c * 9 / 5 + 32, 1)
    feels_f     = round(feels_c * 9 / 5 + 32, 1)

    return (
        f"Weather in {city_name}, {country}:\n"
        f"  Condition:   {description}\n"
        f"  Temperature: {temp_c}°C ({temp_f}°F)\n"
        f"  Feels like:  {feels_c}°C ({feels_f}°F)\n"
        f"  Humidity:    {humidity}%\n"
        f"  Wind:        {wind_kmh} km/h\n"
        f"  Visibility:  {visibility} km"
    )


@tool
def weather(city: str) -> str:
    """
    Get the current live weather conditions for any city in the world.

    Returns temperature, humidity, wind speed, and weather description
    using real-time data from OpenWeatherMap.

    Args:
        city: Name of the city (e.g. "London", "Tokyo", "Hyderabad").

    Returns:
        A formatted live weather report for the given city.
    """
    if not city or not city.strip():
        return "Error: Please provide a city name."
    if len(city) > 100:
        return "Error: City name is too long."

    return _fetch_live_weather(city.strip())
