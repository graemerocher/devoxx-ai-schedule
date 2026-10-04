#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["tzdata"]
# ///
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Fetches the official Devoxx Belgium schedule from the CFP API.

Writes the normalized dataset to assets/devoxx-be-2026.json. It only needs
the standard library plus the IANA time zone database (``tzdata``) on platforms
that do not ship one:

    uv run --no-project --script scripts/fetch_schedule.py [event-slug]
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TARGET = Path(__file__).resolve().parent.parent / "assets" / "devoxx-be-2026.json"


def fetch_json(url: str):
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "dvxaisched"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def to_local(instant: str, zone: ZoneInfo) -> datetime:
    return datetime.fromisoformat(instant.replace("Z", "+00:00")).astimezone(zone)


def main(slug: str) -> None:
    base_url = f"https://{slug}.cfp.dev/api/public"

    print(f"Fetching talks catalog from {base_url}/talks...")
    talk_map = {int(t["id"]): t for t in fetch_json(f"{base_url}/talks")}
    print(f"Fetched {len(talk_map)} talks from catalog.")

    print(f"Fetching schedules from {base_url}/schedules...")
    schedules = fetch_json(f"{base_url}/schedules")
    days = [link["href"].rstrip("/").split("/")[-1] for link in schedules["links"]]
    print(f"Found schedule days: {', '.join(days)}")

    scheduled_talks = []
    for day in days:
        print(f"Fetching schedule for {day}...")
        day_count = 0
        for slot in fetch_json(f"{base_url}/schedules/{day}"):
            proposal = slot.get("proposal")
            if proposal is None:
                continue
            talk_id = int(proposal["id"])
            talk = talk_map.get(talk_id, {})

            zone = ZoneInfo(slot.get("timezone") or "Europe/Brussels")
            start = to_local(slot["fromDate"], zone)
            end = to_local(slot["toDate"], zone)

            speakers = []
            for s in talk.get("speakers") or slot.get("speakers") or []:
                name = s.get("fullName") or f"{s.get('firstName') or ''} {s.get('lastName') or ''}".strip()
                speakers.append({
                    "id": int(s["id"]) if s.get("id") is not None else 0,
                    "name": name,
                    "company": s.get("company") or "",
                    "bio": s.get("bio") or "",
                })

            title = (talk.get("title") or proposal.get("title") or "").strip()
            title_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            url = f"https://m.devoxx.com/events/{slug}/talks/{talk_id}" + (f"/{title_slug}" if title_slug else "")

            scheduled_talks.append({
                "id": talk_id,
                "day": day.lower(),
                "date": start.date().isoformat(),
                "startTime": start.strftime("%H:%M"),
                "endTime": end.strftime("%H:%M"),
                "room": (slot.get("room") or {}).get("name") or "TBA",
                "title": title,
                "summary": talk.get("summary") or proposal.get("summary") or "",
                "track": (talk.get("track") or {}).get("name") or (slot.get("track") or {}).get("name") or "",
                "sessionType": (talk.get("sessionType") or {}).get("name") or (slot.get("sessionType") or {}).get("name") or "",
                "totalFavourites": int(talk.get("totalFavourites") or slot.get("totalFavourites") or 0),
                "speakers": speakers,
                "description": talk.get("description") or "",
                "url": url,
            })
            day_count += 1
        print(f"  -> {day}: {day_count} talks scheduled")

    scheduled_talks.sort(key=lambda t: (t["date"], t["startTime"], t["room"], t["id"]))
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with TARGET.open("w", encoding="utf-8") as out:
        out.write(json.dumps(scheduled_talks, indent=4, ensure_ascii=False) + "\n")
    print(f"Successfully installed {len(scheduled_talks)} scheduled talks into {TARGET}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dvbe26")
