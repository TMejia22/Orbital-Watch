"""
Fetches current position/distance data for deep-space objects (Voyager 1,
Voyager 2, JWST) from NASA JPL Horizons and writes it to deep-space.json.

Runs server-side (via GitHub Actions), so there's no browser CORS restriction —
the website just reads the resulting static JSON file instead of calling
Horizons directly.

Uses the VECTORS ephemeris type (heliocentric X/Y/Z position) rather than the
OBSERVER type, because its CSV rows are simpler and far less error-prone to
parse: one date field followed by exactly X, Y, Z (and a trailing empty
field). This also gives us real heliocentric coordinates, which we use both
to compute distances and to plot each object's position on a solar-system
diagram.
"""
import json
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

AU_KM = 149597870.7
LIGHT_SPEED_KMS = 299792.458

TARGETS = {
    "v1": {"command": "-31", "name": "Voyager 1"},
    "v2": {"command": "-32", "name": "Voyager 2"},
    "jwst": {"command": "-170", "name": "James Webb (JWST)"},
}
EARTH_COMMAND = "399"


def format_distance(km):
    if km > 1e8:
        return f"{km / 1e6:.1f} M km"
    return f"{km:,.0f} km"


def format_light_time(km):
    sec = km / LIGHT_SPEED_KMS
    if sec > 3600:
        return f"{sec / 3600:.2f} hr"
    if sec > 60:
        return f"{sec / 60:.1f} min"
    return f"{sec:.1f} s"


def is_float(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def fetch_heliocentric_xyz(command):
    """Returns (x_km, y_km, z_km) heliocentric ecliptic position for a body."""
    now = datetime.now(timezone.utc)
    stop = now + timedelta(days=1)
    params = {
        "format": "json",
        "COMMAND": f"'{command}'",
        "OBJ_DATA": "'NO'",
        "MAKE_EPHEM": "'YES'",
        "EPHEM_TYPE": "'VECTORS'",
        "CENTER": "'500@10'",       # heliocentric (Sun-centered)
        "REF_PLANE": "'ECLIPTIC'",
        "VEC_TABLE": "'1'",          # position only: X, Y, Z
        "CSV_FORMAT": "'YES'",
        "START_TIME": f"'{now.strftime('%Y-%m-%d')}'",
        "STOP_TIME": f"'{stop.strftime('%Y-%m-%d')}'",
        "STEP_SIZE": "'1d'",
    }
    url = "https://ssd.jpl.nasa.gov/api/horizons.api?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "orbital-watch-fetcher/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    result = data.get("result", "")
    if "$$SOE" not in result or "$$EOE" not in result:
        raise ValueError("Unexpected Horizons response format (no $$SOE/$$EOE markers)")

    soe = result.split("$$SOE")[1]
    line = soe.split("$$EOE")[0].strip().split("\n")[0]

    # CSV-split (NOT regex-over-the-whole-line — the earlier version broke
    # here because it scanned for numbers anywhere in the row, including
    # inside the date text). Each comma-delimited field is either the
    # date/JD or a clean numeric value.
    fields = [f.strip() for f in line.split(",")]
    nums = [float(f) for f in fields if f != "" and is_float(f)]

    if len(nums) < 3:
        raise ValueError(f"Could not parse X/Y/Z from row: {line!r}")

    # Last 3 numeric fields are always X, Y, Z for VEC_TABLE=1, regardless
    # of whether a leading JD column is present.
    x, y, z = nums[-3], nums[-2], nums[-1]
    return x, y, z


def main():
    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "objects": {},
    }

    try:
        earth_xyz = fetch_heliocentric_xyz(EARTH_COMMAND)
    except Exception as e:
        earth_xyz = None
        output["earthError"] = str(e)

    if earth_xyz:
        output["earth"] = {
            "xAU": earth_xyz[0] / AU_KM,
            "yAU": earth_xyz[1] / AU_KM,
        }

    for key, cfg in TARGETS.items():
        try:
            x, y, z = fetch_heliocentric_xyz(cfg["command"])
            sun_km = (x**2 + y**2 + z**2) ** 0.5

            if earth_xyz:
                dx, dy, dz = x - earth_xyz[0], y - earth_xyz[1], z - earth_xyz[2]
                earth_km = (dx**2 + dy**2 + dz**2) ** 0.5
            else:
                earth_km = None

            entry = {
                "name": cfg["name"],
                "xAU": x / AU_KM,
                "yAU": y / AU_KM,
                "distanceFromSunKm": sun_km,
                "distanceFromSunDisplay": format_distance(sun_km),
            }
            if earth_km is not None:
                entry["distanceFromEarthKm"] = earth_km
                entry["distanceFromEarthDisplay"] = format_distance(earth_km)
                entry["lightTimeDisplay"] = format_light_time(earth_km)
            else:
                entry["distanceFromEarthDisplay"] = "N/A"
                entry["lightTimeDisplay"] = "N/A"

            output["objects"][key] = entry
        except Exception as e:
            output["objects"][key] = {"name": cfg["name"], "error": str(e)}

    with open("deep-space.json", "w") as f:
        json.dump(output, f, indent=2)

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()


