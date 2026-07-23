import time
import gc

from config import config
from metro_api_common import MetroApiUtils


class MetroApiBus:
    def __init__(self):
        self._gtfs_incidents_disabled_due_to_memory = False

    def fetch_bus_predictions(
        self, wifi, bus_stops, walking_times, bus_lines, show_incidents
    ) -> list[dict]:
        if len(walking_times) == 0:
            walking_times = [0] * len(bus_stops)
        bus_combos = list(zip(bus_stops, walking_times))
        retries = config["metro_api_retries"]

        start_time = time.monotonic()
        found_buses = []
        for bus_stop, walking_time in bus_combos:
            for attempt in range(retries + 1):
                try:
                    found_buses.extend(
                        self._fetch_bus_predictions(
                            wifi, bus_stop, walking_time, bus_lines
                        )
                    )
                    break
                except Exception as e:
                    MetroApiUtils.maybe_retry(attempt, retries, str(e))
        found_buses = sorted(found_buses, key=lambda b: b["int_arrival"])
        print(f"Buses found: {found_buses}")

        if len(bus_lines) == 0:
            bus_lines = set([b["loc"] for b in found_buses])

        incidents = []
        if show_incidents:
            print("Fetching bus incidents...")
            use_standard_api = (
                not config["use_gtfs_rt_for_bus_incidents"]
                or self._gtfs_incidents_disabled_due_to_memory
            )
            if not use_standard_api:
                bus_directions = {}
                for i, b in enumerate(found_buses):
                    direction = b["destination"][-1]
                    bus_directions.setdefault(b["loc"], set()).add(direction)
                    if (
                        i == 2
                    ):  # only show incidents involving the 3 buses that will show on the board
                        break
                for attempt in range(retries + 1):
                    try:
                        incidents = self._fetch_bus_incidents_gtfs_rt(
                            wifi, bus_directions
                        )
                        break
                    except MemoryError:
                        print("GTFS bus incidents exceeded available memory; using standard API")
                        self._gtfs_incidents_disabled_due_to_memory = True
                        gc.collect()
                        use_standard_api = True
                        break
                    except Exception as e:
                        MetroApiUtils.maybe_retry(attempt, retries, str(e))

            if use_standard_api:
                for route in bus_lines:
                    for attempt in range(retries + 1):
                        try:
                            incidents.extend(self._fetch_bus_incidents(wifi, route))
                            break
                        except Exception as e:
                            MetroApiUtils.maybe_retry(attempt, retries, str(e))
                incidents = [{"description": i} for i in set(incidents)]
            print(f"Bus incidents found: {len(incidents)}")
        duration = time.monotonic() - start_time
        print(f"Update took: {duration:.2f}s")
        return found_buses, incidents

    def _fetch_bus_predictions(
        self, wifi, bus_stop, walking_time, bus_lines
    ) -> list[dict]:
        print(f"Fetching buses for bus stop {bus_stop}...")
        start_time = time.monotonic()
        api_url = config["wmata_api_bus_url"] + str(bus_stop)
        data = MetroApiUtils.query_api(wifi, api_url)
        print("Received bus response from WMATA api...")
        time_buffer = time.monotonic() - start_time
        time_buffer = int(round(time_buffer / 60)) + 1

        buses = [bus for bus in data["Predictions"]]
        buses = [self._normalize_bus_response(bus, time_buffer) for bus in buses]

        if len(bus_lines) > 0:
            dropped_bus_lines = set(
                [bus["loc"] for bus in buses if bus["loc"] not in bus_lines]
            )
            buses = [bus for bus in buses if bus["loc"] in bus_lines]
            if len(dropped_bus_lines) > 0:
                print(
                    f"Dropped bus lines: {dropped_bus_lines}. Consider adding these to your config page."
                )

        if walking_time > 0:
            filtered_buses = list(
                filter(lambda b: b["int_arrival"] - walking_time >= 0, buses)
            )
            if len(filtered_buses) > 0 or not config["show_all_if_none_walking"]:
                buses = filtered_buses

        return buses

    def _fetch_bus_incidents(self, wifi, bus_route):
        api_url = config["wmata_api_bus_incident_url"] + str(bus_route)
        data = MetroApiUtils.query_api(wifi, api_url)
        print("Received bus incident response from WMATA api...")
        incidents = []
        for i in data["BusIncidents"]:
            incident = self._clean_incident(i["Description"])
            incidents.append(
                f"==={str(bus_route)}=== {incident} ==={str(bus_route)}==="
            )
        return incidents

    def _fetch_bus_incidents_gtfs_rt(self, wifi, bus_directions):
        api_url = config["wmata_api_gtfs_bus_incident_url"]
        data = MetroApiUtils.query_api(wifi, api_url)
        print("Received bus incident response from WMATA api...")

        bus_lines = set(bus_directions.keys())

        incident_to_route_map = {}
        for incident in data:
            for entity in incident.get("entities", []):
                alert = entity.get("alert", {})
                lines_affected = {
                    info.get("routeId")
                    for info in alert.get("informedEntities", [])
                    if info.get("routeId")
                }

                matched_lines = lines_affected & bus_lines
                if not matched_lines:
                    continue

                translations = alert.get("descriptionText", {}).get("translations", [])
                description = next(
                    (
                        t.get("text", "")
                        for t in translations
                        if t.get("language") == "en"
                    )
                )
                if not description or not self._incident_in_correct_direction(
                    description.lower(), matched_lines, bus_directions
                ):
                    continue
                description = self._clean_incident(description)
                if description in incident_to_route_map:
                    incident_to_route_map[description].update(matched_lines)
                else:
                    incident_to_route_map[description] = matched_lines

        filtered_incidents = []
        for incident, lines in incident_to_route_map.items():
            lines_affected = ", ".join(sorted(lines))
            description = f"==={lines_affected}=== {incident} ==={lines_affected}==="
            filtered_incidents.append({"description": description})
        return sorted(filtered_incidents, key=lambda x: x["description"])

    def _incident_in_correct_direction(
        self, incident: str, matched_lines: set, bus_directions: dict
    ):
        directions = {
            "N": ["northbound", "southbound"],
            "S": ["southbound", "northbound"],
            "E": ["eastbound", "westbound"],
            "W": ["westbound", "eastbound"],
        }
        for line in matched_lines:
            for direction in bus_directions.get(line, []):
                if (
                    directions[direction][0] in incident
                    or directions[direction][1] not in incident
                ):
                    return True
        return False

    def _clean_incident(self, incident: str):
        incident = incident.replace("\n", "")
        sentences = []
        start_idx = 0
        for i in range(len(incident) - 2):
            if (
                incident[i] == "."
                and incident[i + 1] == " "
                and incident[i + 2].isupper()
            ):
                sentences.append(incident[start_idx : i + 1].strip())
                start_idx = i + 2
        if start_idx < len(incident):
            sentences.append(incident[start_idx:].strip())
        clean = ". ".join([s for s in sentences if ".com" not in s])
        clean = clean.replace("..", ".")
        return clean

    def _remove_vowels(self, text: str):
        vowels = "aeiouAEIOU"
        return "".join(
            [
                char
                for i, char in enumerate(text)
                if char not in vowels or i == 0 or text[i - 1] == " "
            ]
        )

    def _normalize_bus_response(self, bus: dict, buff: int):
        dest = f"{bus['RouteID']} - {bus['DirectionText'].split(' ')[0][0]}"  # C51 - N
        # Note changing to one of the options below would mess up incident direction filtering
        # dest = f"{bus['RouteID']}-{bus['DirectionText'].split(" ")[0]}" # C51 - North
        # dest = self._remove_vowels(f"{bus['DirectionText'].split(" to ")[1].strip()}") # Tnlytwn

        arrival = str(bus["Minutes"])
        if arrival.isdigit():
            int_arrival = int(arrival)
        else:
            int_arrival = 100
        ret = {
            "line_color": 0x000000,
            "destination": dest,
            "text_arrival": arrival,
            "int_arrival": int_arrival,
            "loc": bus["RouteID"].strip(),
        }
        return ret
