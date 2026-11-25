"""Weather for here, or for anywhere else.

Three things used to go wrong, and all three are fixed below.

1. NOTHING CAME BACK. Neither HTTP call passed a timeout, so Python's default
   applied — which is no timeout at all. A stalled TLS handshake to the
   geocoder (reproducible from this machine; it hung past 20s on the first
   connection and succeeded on the next) left the call blocked forever. That
   call is awaited inside main.py's receive loop, so SIVA went silent until
   the user gave up. Asking a second time opened a fresh connection and
   worked. Every request now carries a timeout, retries once, and the whole
   lookup runs under a hard overall budget.

2. A COUNTRY GAVE YOU A JUNGLE. "Weather in Malaysia" geocoded to 2.5N 112.5E
   — the country's centroid, in the middle of Borneo. Regions were worse:
   the geocoder holds no record for US states or UK nations at all, so
   "Scotland" resolved to a village of 1,395 people in Pennsylvania. Country
   and region names are now resolved through the verified tables below.

3. IT ASKED YOU TO ASK AGAIN. The no-city path fired off a location helper
   and returned "please ask again in a moment" without ever waiting for it.
   That is the "had to ask twice" — it was by design. Now a known location is
   used at once and refreshed in the background, and a first-ever run waits
   briefly for a real fix instead of punting.
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

_BASE      = os.path.dirname(os.path.abspath(__file__))
_MEMORY    = os.path.join(_BASE, "..", "memory")
_LOC_CACHE = os.path.join(_MEMORY, "location_cache.json")
_GEO_CACHE = os.path.join(_MEMORY, "geocode_cache.json")

# Nothing here may outlive the receive loop's patience. One lookup gets this
# many seconds in total, spread across however many requests it needs.
_TOTAL_BUDGET = 11.0
# Short and often, not long and twice. A healthy call to either service takes
# 0.7-0.9s from here; the failure mode is a TLS handshake that hangs rather
# than one that is merely slow, and what clears it is a fresh connection. So
# give up early and try again — three quick attempts beat two patient ones.
_PER_REQUEST  = 2.5
_TRIES        = 3
_RETRY_PAUSE  = 0.3

# A known position is reused for this long before a fresh fix is fetched. Only
# a machine that has never had a fix waits on the sensor.
_LOCATION_FRESH_S = 900.0
_LOCATION_WAIT_S  = 8.0

_UA = {"User-Agent": "SIVA-Assistant"}

# WMO Weather interpretation codes (WW)
WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow fall", 73: "Moderate snow fall", 75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}

# Country code -> (capital, lat, lon). A country's own geocoder record points
# at its centroid, which for most countries is farmland. Asked about a
# country, people mean its capital. Every coordinate below came back from the
# geocoder itself — none were typed by hand.
COUNTRY_CAPITALS = {
    "AD": ("Andorra la Vella", 42.5078, 1.5211),
    "AE": ("Abu Dhabi", 24.4512, 54.397),
    "AF": ("Kabul", 34.5281, 69.1723),
    "AG": ("St John's", 17.121, -61.8433),
    "AI": ("The Valley", 18.217, -63.0578),
    "AL": ("Tirana", 41.3274, 19.8187),
    "AM": ("Yerevan", 40.1776, 44.5126),
    "AO": ("Luanda", -8.8368, 13.2343),
    "AR": ("Buenos Aires", -34.6131, -58.3772),
    "AT": ("Vienna", 48.2085, 16.3721),
    "AU": ("Canberra", -35.2835, 149.1281),
    "AW": ("Oranjestad", 12.524, -70.027),
    "AZ": ("Baku", 40.3777, 49.892),
    "BA": ("Sarajevo", 43.8486, 18.3564),
    "BB": ("Bridgetown", 13.1073, -59.6202),
    "BD": ("Dhaka", 23.7104, 90.4074),
    "BE": ("Brussels", 50.8505, 4.3488),
    "BF": ("Ouagadougou", 12.3657, -1.5339),
    "BG": ("Sofia", 42.6975, 23.3241),
    "BH": ("Manama", 26.2279, 50.5857),
    "BI": ("Gitega", -3.4271, 29.9246),
    "BJ": ("Porto-Novo", 6.4965, 2.6036),
    "BM": ("Hamilton", 32.2949, -64.783),
    "BN": ("Bandar Seri Begawan", 4.8903, 114.9401),
    "BO": ("La Paz", -16.5, -68.15),
    "BR": ("Brasilia", -15.7797, -47.9297),
    "BS": ("Nassau", 25.0582, -77.3431),
    "BT": ("Thimphu", 27.4661, 89.6419),
    "BW": ("Gaborone", -24.6545, 25.9086),
    "BY": ("Minsk", 53.9002, 27.5665),
    "BZ": ("Belmopan", 17.2538, -88.764),
    "CA": ("Ottawa", 45.4112, -75.6981),
    "CD": ("Kinshasa", -4.3276, 15.3136),
    "CF": ("Bangui", 4.3612, 18.555),
    "CG": ("Brazzaville", -4.2661, 15.2832),
    "CH": ("Bern", 46.9481, 7.4474),
    "CI": ("Yamoussoukro", 6.8205, -5.2767),
    "CL": ("Santiago", -33.4569, -70.6483),
    "CM": ("Yaounde", 3.8667, 11.5167),
    "CN": ("Beijing", 39.9075, 116.3972),
    "CO": ("Bogota", 4.6097, -74.0817),
    "CR": ("San Jose", 9.9339, -84.0849),
    "CU": ("Havana", 23.133, -82.383),
    "CV": ("Praia", 14.9315, -23.5125),
    "CW": ("Willemstad", 12.1225, -68.8864),
    "CY": ("Nicosia", 35.1728, 33.354),
    "CZ": ("Prague", 50.088, 14.4208),
    "DE": ("Berlin", 52.5244, 13.4105),
    "DJ": ("Djibouti", 11.589, 43.145),
    "DK": ("Copenhagen", 55.6759, 12.5655),
    "DM": ("Roseau", 15.3017, -61.3881),
    "DO": ("Santo Domingo", 18.4719, -69.8923),
    "DZ": ("Algiers", 36.7323, 3.0875),
    "EC": ("Quito", -0.2298, -78.525),
    "EE": ("Tallinn", 59.437, 24.7535),
    "EG": ("Cairo", 30.0626, 31.2497),
    "EH": ("Laayoune", 27.1418, -13.188),
    "ER": ("Asmara", 15.3381, 38.9318),
    "ES": ("Madrid", 40.4165, -3.7026),
    "ET": ("Addis Ababa", 9.025, 38.7469),
    "FI": ("Helsinki", 60.1695, 24.9354),
    "FJ": ("Suva", -18.1368, 178.4253),
    "FK": ("Stanley", -51.6938, -57.857),
    "FM": ("Palikir", 6.9248, 158.1611),
    "FO": ("Torshavn", 62.0097, -6.7716),
    "FR": ("Paris", 48.8534, 2.3488),
    "GA": ("Libreville", 0.3924, 9.4536),
    "GB": ("London", 51.5085, -0.1257),
    "GD": ("St. George's", 12.0529, -61.7523),
    "GE": ("Tbilisi", 41.6914, 44.8341),
    "GF": ("Cayenne", 4.9381, -52.3346),
    "GG": ("St Peter Port", 49.4598, -2.5353),
    "GH": ("Accra", 5.556, -0.1969),
    "GI": ("Gibraltar", 36.1447, -5.3526),
    "GL": ("Nuuk", 64.1835, -51.7216),
    "GM": ("Banjul", 13.4527, -16.578),
    "GN": ("Conakry", 9.538, -13.6773),
    "GP": ("Basse-Terre", 15.9971, -61.7321),
    "GQ": ("Malabo", 3.7558, 8.7817),
    "GR": ("Athens", 37.9838, 23.7278),
    "GT": ("Guatemala City", 14.6407, -90.5133),
    "GU": ("Hagatna", 13.4757, 144.7489),
    "GW": ("Bissau", 11.8636, -15.5977),
    "GY": ("Georgetown", 6.8045, -58.1553),
    "HK": ("Hong Kong", 22.2783, 114.1747),
    "HN": ("Tegucigalpa", 14.0818, -87.2068),
    "HR": ("Zagreb", 45.8144, 15.978),
    "HT": ("Port-au-Prince", 18.5435, -72.3388),
    "HU": ("Budapest", 47.4984, 19.0404),
    "ID": ("Jakarta", -6.2146, 106.8451),
    "IE": ("Dublin", 53.3331, -6.2489),
    "IL": ("Jerusalem", 31.769, 35.2163),
    "IM": ("Douglas", 54.15, -4.4833),
    "IN": ("New Delhi", 28.6214, 77.2148),
    "IQ": ("Baghdad", 33.3406, 44.4009),
    "IR": ("Tehran", 35.6944, 51.4215),
    "IS": ("Reykjavik", 64.1355, -21.8954),
    "IT": ("Rome", 41.8919, 12.5113),
    "JE": ("Saint Helier", 49.188, -2.1049),
    "JM": ("Kingston", 17.997, -76.7936),
    "JO": ("Amman", 31.9552, 35.945),
    "JP": ("Tokyo", 35.6895, 139.6917),
    "KE": ("Nairobi", -1.2833, 36.8167),
    "KG": ("Bishkek", 42.87, 74.59),
    "KH": ("Phnom Penh", 11.5625, 104.916),
    "KI": ("Tarawa", 1.3278, 172.977),
    "KM": ("Moroni", -11.7022, 43.2551),
    "KN": ("Basseterre", 17.2955, -62.725),
    "KP": ("Pyongyang", 39.0339, 125.7543),
    "KR": ("Seoul", 37.566, 126.9784),
    "KW": ("Kuwait City", 29.367, 47.9743),
    "KY": ("George Town", 19.2866, -81.3744),
    "KZ": ("Astana", 51.1801, 71.446),
    "LA": ("Vientiane", 17.9667, 102.6),
    "LB": ("Beirut", 33.8933, 35.5016),
    "LC": ("Castries", 13.9957, -61.0061),
    "LI": ("Vaduz", 47.1415, 9.5215),
    "LK": ("Colombo", 6.9355, 79.8487),
    "LR": ("Monrovia", 6.3005, -10.7969),
    "LS": ("Maseru", -29.3167, 27.4833),
    "LT": ("Vilnius", 54.6892, 25.2798),
    "LU": ("Luxembourg", 49.6098, 6.1327),
    "LV": ("Riga", 56.946, 24.1059),
    "LY": ("Tripoli", 32.8874, 13.1873),
    "MA": ("Rabat", 34.0132, -6.8326),
    "MC": ("Monaco", 43.7372, 7.4215),
    "MD": ("Chisinau", 47.009, 28.8594),
    "ME": ("Podgorica", 42.4412, 19.2631),
    "MG": ("Antananarivo", -18.9137, 47.5361),
    "MH": ("Majuro", 7.0897, 171.3803),
    "MK": ("Skopje", 41.9965, 21.4314),
    "ML": ("Bamako", 12.6091, -7.9752),
    "MM": ("Naypyidaw", 19.7366, 96.2072),
    "MN": ("Ulaanbaatar", 47.9077, 106.8832),
    "MO": ("Macao", 22.2006, 113.5461),
    "MQ": ("Fort-de-France", 14.6037, -61.0742),
    "MR": ("Nouakchott", 18.0858, -15.9785),
    "MS": ("Brades", 16.7918, -62.2106),
    "MT": ("Valletta", 35.8997, 14.5148),
    "MU": ("Port Louis", -20.1619, 57.4989),
    "MV": ("Male", 4.1752, 73.5092),
    "MW": ("Lilongwe", -13.9669, 33.7873),
    "MX": ("Mexico City", 19.4285, -99.1277),
    "MY": ("Kuala Lumpur", 3.1412, 101.6865),
    "MZ": ("Maputo", -25.9655, 32.5832),
    "NA": ("Windhoek", -22.5594, 17.0832),
    "NC": ("Noumea", -22.2741, 166.4488),
    "NE": ("Niamey", 13.5137, 2.1098),
    "NG": ("Abuja", 9.0579, 7.4951),
    "NI": ("Managua", 12.1328, -86.2504),
    "NL": ("Amsterdam", 52.374, 4.8897),
    "NO": ("Oslo", 59.9127, 10.7461),
    "NP": ("Kathmandu", 27.7017, 85.3206),
    "NR": ("Yaren District", -0.5508, 166.9252),
    "NZ": ("Wellington", -41.2866, 174.7756),
    "OM": ("Muscat", 23.5841, 58.4078),
    "PA": ("Panama City", 8.9936, -79.5197),
    "PE": ("Lima", -12.0432, -77.0282),
    "PF": ("Papeete", -17.5347, -149.5684),
    "PG": ("Port Moresby", -9.4772, 147.1509),
    "PH": ("Manila", 14.6042, 120.9822),
    "PK": ("Islamabad", 33.7215, 73.0433),
    "PL": ("Warsaw", 52.2298, 21.0118),
    "PR": ("San Juan", 18.4663, -66.1057),
    "PS": ("Ramallah", 31.8996, 35.2042),
    "PT": ("Lisbon", 38.7251, -9.1498),
    "PW": ("Ngerulmud", 7.5008, 134.6238),
    "PY": ("Asuncion", -25.2865, -57.647),
    "QA": ("Doha", 25.2855, 51.531),
    "RE": ("Saint-Denis", -20.8823, 55.4504),
    "RO": ("Bucharest", 44.4323, 26.1063),
    "RS": ("Belgrade", 44.804, 20.4651),
    "RU": ("Moscow", 55.752, 37.6178),
    "RW": ("Kigali", -1.95, 30.0588),
    "SA": ("Riyadh", 24.6877, 46.7219),
    "SB": ("Honiara", -9.4333, 159.95),
    "SC": ("Victoria", -4.62, 55.455),
    "SD": ("Khartoum", 15.5518, 32.5324),
    "SE": ("Stockholm", 59.3294, 18.0687),
    "SG": ("Singapore", 1.2897, 103.8501),
    "SI": ("Ljubljana", 46.0511, 14.5051),
    "SK": ("Bratislava", 48.1482, 17.1067),
    "SL": ("Freetown", 8.4871, -13.2356),
    "SM": ("San Marino", 43.9367, 12.4464),
    "SN": ("Dakar", 14.6937, -17.4441),
    "SO": ("Mogadishu", 2.0371, 45.3438),
    "SR": ("Paramaribo", 5.8664, -55.1668),
    "SS": ("Juba", 4.8517, 31.5825),
    "ST": ("Sao Tome", 0.3376, 6.7299),
    "SV": ("San Salvador", 13.6893, -89.1872),
    "SY": ("Damascus", 33.5102, 36.2913),
    "SZ": ("Mbabane", -26.3167, 31.1333),
    "TC": ("Cockburn Town", 21.4612, -71.1419),
    "TD": ("N'Djamena", 12.1067, 15.0444),
    "TG": ("Lome", 6.1287, 1.2215),
    "TH": ("Bangkok", 13.754, 100.5014),
    "TJ": ("Dushanbe", 38.5358, 68.779),
    "TL": ("Dili", -8.5586, 125.5736),
    "TM": ("Ashgabat", 37.95, 58.3833),
    "TN": ("Tunis", 36.819, 10.1658),
    "TO": ("Nuku'alofa", -21.1368, -175.2011),
    "TR": ("Ankara", 39.9199, 32.8543),
    "TT": ("Port of Spain", 10.6667, -61.5189),
    "TV": ("Funafuti", -8.5243, 179.1942),
    "TW": ("Taipei", 25.0531, 121.5264),
    "TZ": ("Dodoma", -6.1722, 35.7395),
    "UA": ("Kyiv", 50.4547, 30.5238),
    "UG": ("Kampala", 0.3163, 32.5822),
    "US": ("Washington D.C.", 38.8951, -77.0364),
    "UY": ("Montevideo", -34.9033, -56.1882),
    "UZ": ("Tashkent", 41.2647, 69.2163),
    "VA": ("Vatican City", 41.9027, 12.4541),
    "VC": ("Kingstown", 13.1553, -61.2274),
    "VE": ("Caracas", 10.488, -66.8792),
    "VG": ("Road Town", 18.4269, -64.6208),
    "VI": ("Charlotte Amalie", 18.3419, -64.9307),
    "VN": ("Hanoi", 21.0245, 105.8412),
    "VU": ("Port Vila", -17.7365, 168.3137),
    "WS": ("Apia", -13.8333, -171.7667),
    "YE": ("Sanaa", 15.3545, 44.2065),
    "YT": ("Mamoudzou", -12.7823, 45.2288),
    "ZA": ("Pretoria", -25.7449, 28.1878),
    "ZM": ("Lusaka", -15.4067, 28.2871),
    "ZW": ("Harare", -17.8277, 31.0534),
}

# Region -> (principal city, lat, lon). The geocoder has no record for these
# names at all, so without this table "Texas" is a hamlet in Mexico and
# "Scotland" is a village in Pennsylvania. Largest city rather than capital,
# because that is the place people picture. "Georgia" and "Washington" are
# deliberately absent — each means something else first (the country, and the
# capital), and the geocoder already gets those right.
REGION_CITIES = {
    "alabama": ("Birmingham, Alabama", 33.5207, -86.8025),
    "alaska": ("Anchorage, Alaska", 61.2181, -149.9003),
    "alberta": ("Calgary, Alberta", 51.0501, -114.0853),
    "arizona": ("Phoenix, Arizona", 33.4484, -112.074),
    "arkansas": ("Little Rock, Arkansas", 34.7465, -92.2896),
    "british columbia": ("Vancouver, British Columbia", 49.2497, -123.1193),
    "california": ("Los Angeles, California", 34.0522, -118.2437),
    "colorado": ("Denver, Colorado", 39.7392, -104.9847),
    "connecticut": ("Bridgeport, Connecticut", 41.1792, -73.1894),
    "delaware": ("Wilmington, Delaware", 39.746, -75.5466),
    "england": ("London, England", 51.5085, -0.1257),
    "florida": ("Jacksonville, Florida", 30.3322, -81.6556),
    "hawaii": ("Honolulu, Hawaii", 21.3069, -157.8583),
    "idaho": ("Boise, Idaho", 43.6135, -116.2035),
    "illinois": ("Chicago, Illinois", 41.85, -87.65),
    "indiana": ("Indianapolis, Indiana", 39.7684, -86.158),
    "iowa": ("Des Moines, Iowa", 41.6005, -93.6091),
    "kansas": ("Wichita, Kansas", 37.6922, -97.3375),
    "kentucky": ("Louisville, Kentucky", 38.2542, -85.7594),
    "louisiana": ("New Orleans, Louisiana", 29.9547, -90.0751),
    "maine": ("Portland, Maine", 43.6574, -70.2589),
    "manitoba": ("Winnipeg, Manitoba", 49.8844, -97.147),
    "maryland": ("Baltimore, Maryland", 39.2904, -76.6122),
    "massachusetts": ("Boston, Massachusetts", 42.3584, -71.0598),
    "michigan": ("Detroit, Michigan", 42.3314, -83.0457),
    "minnesota": ("Minneapolis, Minnesota", 44.98, -93.2638),
    "mississippi": ("Jackson, Mississippi", 32.2988, -90.1848),
    "missouri": ("Kansas City, Missouri", 39.0997, -94.5786),
    "montana": ("Billings, Montana", 45.7833, -108.5007),
    "nebraska": ("Omaha, Nebraska", 41.2563, -95.9404),
    "nevada": ("Las Vegas, Nevada", 36.175, -115.1372),
    "new brunswick": ("Moncton, New Brunswick", 46.0945, -64.7965),
    "new hampshire": ("Manchester, New Hampshire", 42.9956, -71.4548),
    "new jersey": ("Newark, New Jersey", 40.7357, -74.1724),
    "new mexico": ("Albuquerque, New Mexico", 35.0845, -106.6511),
    "new south wales": ("Sydney, New South Wales", -33.8678, 151.2073),
    "new york state": ("New York, New York", 40.7143, -74.006),
    "newfoundland": ("St. John's, Newfoundland and Labrador", 47.5649, -52.7093),
    "north carolina": ("Charlotte, North Carolina", 35.2271, -80.8431),
    "north dakota": ("Fargo, North Dakota", 46.8772, -96.7898),
    "northern ireland": ("Belfast, Northern Ireland", 54.5968, -5.9254),
    "northern territory": ("Darwin, Northern Territory", -12.4611, 130.8418),
    "nova scotia": ("Halifax, Nova Scotia", 44.6427, -63.5769),
    "ohio": ("Columbus, Ohio", 39.9612, -82.9988),
    "oklahoma": ("Oklahoma City, Oklahoma", 35.4676, -97.5164),
    "ontario": ("Toronto, Ontario", 43.7064, -79.3986),
    "oregon": ("Portland, Oregon", 45.5234, -122.6762),
    "pennsylvania": ("Philadelphia, Pennsylvania", 39.9524, -75.1636),
    "prince edward island": ("Charlottetown, Prince Edward Island", 46.2346, -63.1256),
    "quebec": ("Montreal, Quebec", 45.5088, -73.5878),
    "queensland": ("Brisbane, Queensland", -27.4679, 153.0281),
    "rhode island": ("Providence, Rhode Island", 41.824, -71.4128),
    "saskatchewan": ("Saskatoon, Saskatchewan", 52.1324, -106.6689),
    "scotland": ("Edinburgh, Scotland", 55.9521, -3.1965),
    "south australia": ("Adelaide, South Australia", -34.9287, 138.5986),
    "south carolina": ("Charleston, South Carolina", 32.7763, -79.9327),
    "south dakota": ("Sioux Falls, South Dakota", 43.5437, -96.728),
    "tasmania": ("Hobart, Tasmania", -42.8794, 147.3294),
    "tennessee": ("Nashville, Tennessee", 36.1659, -86.7844),
    "texas": ("Houston, Texas", 29.7633, -95.3633),
    "utah": ("Salt Lake City, Utah", 40.7608, -111.8911),
    "vermont": ("Burlington, Vermont", 44.4759, -73.2121),
    "victoria": ("Melbourne, Victoria", -37.814, 144.9633),
    "virginia": ("Virginia Beach, Virginia", 36.8529, -75.978),
    "wales": ("Cardiff, Wales", 51.48, -3.18),
    "west virginia": ("Charleston, West Virginia", 38.3498, -81.6326),
    "western australia": ("Perth, Western Australia", -31.9522, 115.8614),
    "wisconsin": ("Milwaukee, Wisconsin", 43.0389, -87.9065),
    "wyoming": ("Cheyenne, Wyoming", 41.14, -104.8203),
    # Malaysia and India in full — this install's own region, and the one
    # its user is most likely to ask after — plus the European and Asian
    # regions people name in place of a city.
    "andalusia": ("Seville, Andalusia", 37.3828, -5.9732),
    "andhra pradesh": ("Visakhapatnam, Andhra Pradesh", 17.6801, 83.2016),
    "arunachal pradesh": ("Itanagar, India", 27.0869, 93.6099),
    "assam": ("Guwahati, Assam", 26.1844, 91.7458),
    "basque country": ("Bilbao, Basque Country", 43.2627, -2.9253),
    "bavaria": ("Munich, Bavaria", 48.1374, 11.5755),
    "bihar": ("Patna, Bihar", 25.5941, 85.1356),
    "borneo": ("Kuching, Sarawak", 1.55, 110.3333),
    "brittany": ("Rennes, Brittany Region", 48.1111, -1.6743),
    "catalonia": ("Barcelona, Catalonia", 41.3888, 2.159),
    "chhattisgarh": ("Raipur, Chhattisgarh", 21.2333, 81.6333),
    "corsica": ("Ajaccio, Corsica", 41.9189, 8.7381),
    "crete": ("Heraklion, Crete", 35.3279, 25.1434),
    "flanders": ("Antwerp, Flanders", 51.2205, 4.4003),
    "galicia": ("Vigo, Galicia", 42.2328, -8.7226),
    "goa": ("Vasco da Gama, Goa", 15.3959, 73.8157),
    "gujarat": ("Ahmedabad, Gujarat", 23.0258, 72.5873),
    "haryana": ("Faridabad, Haryana", 28.4112, 77.3132),
    "himachal pradesh": ("Shimla, Himachal Pradesh", 31.1044, 77.1666),
    "hokkaido": ("Sapporo, Hokkaido", 43.0667, 141.35),
    "jammu and kashmir": ("Srinagar, Jammu and Kashmir", 34.0857, 74.8055),
    "java": ("Surabaya, East Java", -7.2492, 112.7508),
    "jharkhand": ("Ranchi, Jharkhand", 23.3432, 85.3094),
    "johor": ("Johor Bahru, Johor", 1.4655, 103.7578),
    "karnataka": ("Bengaluru, Karnataka", 12.9719, 77.5937),
    "kashmir": ("Srinagar, Jammu and Kashmir", 34.0857, 74.8055),
    "kedah": ("Alor Star, Kedah", 6.121, 100.3601),
    "kelantan": ("Kota Bharu, Kelantan", 6.1236, 102.2433),
    "kerala": ("Thiruvananthapuram, Kerala", 8.4855, 76.9492),
    "kyushu": ("Fukuoka, Fukuoka", 33.6, 130.4167),
    "labuan": ("Labuan, Labuan", 5.2803, 115.2475),
    "lombardy": ("Milan, Lombardy", 45.4643, 9.1895),
    "madhya pradesh": ("Indore, Madhya Pradesh", 22.7179, 75.8333),
    "maharashtra": ("Mumbai, Maharashtra", 19.0728, 72.8826),
    "malacca": ("Malacca, Melaka", 2.196, 102.2405),
    "manipur": ("Imphal, Manipur", 24.8081, 93.9442),
    "meghalaya": ("Shillong, Meghalaya", 25.5689, 91.8831),
    "melaka": ("Malacca, Melaka", 2.196, 102.2405),
    "mizoram": ("Aizawl, Mizoram", 23.7289, 92.7179),
    "nagaland": ("Kohima, Nagaland", 25.6747, 94.111),
    "negeri sembilan": ("Seremban, Negeri Sembilan", 2.7297, 101.9381),
    "normandy": ("Le Havre, Normandy", 49.4935, 0.1079),
    "odisha": ("Bhubaneswar, Odisha", 20.2724, 85.8338),
    "okinawa": ("Naha, Okinawa", 26.213, 127.6785),
    "pahang": ("Kuantan, Pahang", 3.8077, 103.326),
    "patagonia": ("Comodoro Rivadavia, Chubut", -45.8626, -67.494),
    "penang": ("George Town, Penang", 5.4112, 100.3354),
    "perak": ("Ipoh, Perak", 4.5841, 101.0829),
    "perlis": ("Kangar, Perlis", 6.4414, 100.1986),
    "phuket": ("Phuket, Phuket", 7.8906, 98.3981),
    "pondicherry": ("Puducherry, Puducherry", 11.9338, 79.8298),
    "provence": ("Marseille, Provence-Alpes-Côte d'Azur", 43.297, 5.3811),
    "puducherry": ("Puducherry, Puducherry", 11.9338, 79.8298),
    "pulau pinang": ("George Town, Penang", 5.4112, 100.3354),
    "punjab": ("Ludhiana, Punjab", 30.912, 75.8538),
    "putrajaya": ("Putrajaya, Putrajaya", 2.9353, 101.6911),
    "rajasthan": ("Jaipur, Rajasthan", 26.9196, 75.7878),
    "sabah": ("Kota Kinabalu, Sabah", 5.9749, 116.0724),
    "santorini": ("Fira, South Aegean", 36.4211, 25.4309),
    "sarawak": ("Kuching, Sarawak", 1.55, 110.3333),
    "sardinia": ("Cagliari, Sardinia", 39.2305, 9.1192),
    "saxony": ("Leipzig, Saxony", 51.3396, 12.3713),
    "selangor": ("Shah Alam, Selangor", 3.0851, 101.5328),
    "siberia": ("Novosibirsk, Novosibirsk Oblast", 55.0226, 82.9317),
    "sicilia": ("Palermo, Sicily", 38.1166, 13.3636),
    "sicily": ("Palermo, Sicily", 38.1166, 13.3636),
    "sikkim": ("Gangtok, Sikkim", 27.3257, 88.6122),
    "sumatra": ("Medan, North Sumatra", 3.5833, 98.6667),
    "tamil nadu": ("Chennai, Tamil Nadu", 13.0878, 80.2785),
    "telangana": ("Hyderabad, Telangana", 17.384, 78.4564),
    "terengganu": ("Kuala Terengganu, Terengganu", 5.3302, 103.1408),
    "tibet": ("Lhasa, Tibet", 29.65, 91.1),
    "tripura": ("Agartala, Tripura", 23.8361, 91.2794),
    "tuscany": ("Florence, Tuscany", 43.7792, 11.2463),
    "tyrol": ("Innsbruck, Tyrol", 47.2627, 11.3945),
    "uttar pradesh": ("Lucknow, Uttar Pradesh", 26.8393, 80.9231),
    "uttarakhand": ("Dehradun, Uttarakhand", 30.3244, 78.0339),
    "west bengal": ("Kolkata, West Bengal", 22.5626, 88.363),
}

# Spoken shorthand the geocoder returns nothing for. "the united states" is a
# real example: zero results, so the old code said it could not find it.
_ALIASES = {
    "us": "United States", "usa": "United States", "u.s.": "United States",
    "u.s.a.": "United States", "united states of america": "United States",
    "america": "United States", "the states": "United States",
    "uk": "United Kingdom", "u.k.": "United Kingdom", "britain": "United Kingdom",
    "great britain": "United Kingdom", "gb": "United Kingdom",
    "uae": "United Arab Emirates", "emirates": "United Arab Emirates",
    # Countries the geocoder files under a name nobody says out loud. Without
    # these, "Turkey" is a town in North Carolina and "the Netherlands" is a
    # place in Missouri — both real answers to the wrong question. Pointed at
    # the capital directly, so they do not depend on the official spelling.
    "turkey": "Ankara", "turkiye": "Ankara",
    "netherlands": "Amsterdam", "holland": "Amsterdam", "the netherlands": "Amsterdam",
    "burma": "Myanmar", "czechia": "Czech Republic", "ivory coast": "Cote d'Ivoire",
    "south korea": "Seoul", "north korea": "Pyongyang",
    "nyc": "New York", "new york city": "New York", "la": "Los Angeles",
    "sf": "San Francisco", "dc": "Washington D.C.", "kl": "Kuala Lumpur",
    "bombay": "Mumbai", "calcutta": "Kolkata", "madras": "Chennai",
    "saigon": "Ho Chi Minh City", "peking": "Beijing",
}


class _Budget:
    """One lookup's total time allowance, shared by every request it makes."""

    def __init__(self, seconds: float):
        self._deadline = time.monotonic() + seconds

    def left(self) -> float:
        return self._deadline - time.monotonic()

    def slice(self, cap: float = _PER_REQUEST) -> float:
        return min(cap, self.left())


def _get_json(url: str, budget: _Budget, tries: int = _TRIES):
    """Fetch and parse, never blocking longer than the budget allows.

    The retry is the important part: the failure that made SIVA go quiet was a
    TLS handshake that stalled on a cold connection and then succeeded
    immediately on a second one. One retry turns that from a dead turn into a
    half-second hiccup.
    """
    last = None
    for attempt in range(tries):
        window = budget.slice()
        if window <= 0.2:
            break
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=window) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            # 4xx will say the same thing next time; only retry server faults.
            last = e
            if e.code < 500:
                break
        except Exception as e:
            last = e
        if attempt + 1 < tries and budget.left() > _RETRY_PAUSE + 0.5:
            time.sleep(_RETRY_PAUSE)
    if last:
        raise last
    raise TimeoutError("out of time")


def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path: str, data) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


_LEADING_NOISE = ("the ", "in ", "at ", "for ")
_TRAILING_NOISE = (" today", " right now", " now", " weather", " currently")


def _normalise(name: str) -> str:
    """Fold a spoken place name down to something lookupable.

    Stripped repeatedly, not once: "in the UK" needs both words gone, and a
    single pass left "the UK" — which matches nothing at all.
    """
    text = " ".join(name.strip().split()).strip(" ,.?!")
    changed = True
    while changed and text:
        changed = False
        low = text.casefold()
        for lead in _LEADING_NOISE:
            if low.startswith(lead):
                text, changed = text[len(lead):].strip(" ,"), True
                break
        low = text.casefold()
        for tail in _TRAILING_NOISE:
            if low.endswith(tail):
                text, changed = text[: -len(tail)].strip(" ,"), True
                break
    return text


def _rank_key(index: int, result: dict, query: str):
    """Best match first: exact name, then population, then the API's own order.

    Population is what stops "Bali" landing on a village in West Bengal that
    the geocoder happens to rank first, and it is why a country's own record
    (tens of millions) beats the hamlets that share its name.
    """
    name = str(result.get("name") or "")
    exact = name.casefold() != query.casefold()
    return (exact, -(result.get("population") or 0), index)


def _geocode(query: str, budget: _Budget):
    """(label, lat, lon) for a place name, or None if there is no such place."""
    url = ("https://geocoding-api.open-meteo.com/v1/search?name="
           + urllib.parse.quote_plus(query) + "&count=10&language=en")
    results = (_get_json(url, budget) or {}).get("results") or []
    if not results:
        return None

    best = sorted(enumerate(results), key=lambda t: _rank_key(t[0], t[1], query))[0][1]

    # A country record points at the centroid, so swap in the capital.
    code = str(best.get("country_code") or "").upper()
    if str(best.get("feature_code") or "").startswith("PCL") and code in COUNTRY_CAPITALS:
        capital, lat, lon = COUNTRY_CAPITALS[code]
        return f"{capital}, {best.get('country') or code}", lat, lon

    parts = [best.get("name")]
    # Region for somewhere inside your own country, country for anywhere else
    # — enough to make "Taiping" unambiguous without reciting the whole address.
    if best.get("country"):
        parts.append(best["country"])
    label = ", ".join(p for p in parts if p)
    return label, float(best["latitude"]), float(best["longitude"])


def _search_terms(query: str):
    """The query, then shorter versions of it, best first.

    The geocoder matches place names, not addresses: "Sydney Australia" finds
    nothing while "Sydney" finds five million people. Speech gives us the
    long form often enough to be worth peeling back — at most three attempts,
    so a nonsense word cannot spend the whole budget.
    """
    terms = [query]

    head = query.split(",")[0].strip()
    if head and head != query:
        terms.append(head)

    words = head.split()
    if len(words) > 1:
        shorter = " ".join(words[:-1])
        if shorter not in terms:
            terms.append(shorter)

    return terms[:3]


def _resolve_place(city: str, budget: _Budget):
    """Turn whatever the user said into (label, lat, lon).

    Tables first, then the on-disk cache, then the network — so a repeat
    question costs nothing and works even while the geocoder is unreachable.
    """
    query = _normalise(city)
    if not query:
        return None
    key = query.casefold()

    query = _ALIASES.get(key, query)
    key = query.casefold()

    if key in REGION_CITIES:
        label, lat, lon = REGION_CITIES[key]
        return label, lat, lon

    cache = _read_json(_GEO_CACHE) or {}
    hit = cache.get(key)
    if isinstance(hit, dict) and "lat" in hit:
        return hit.get("label") or query, float(hit["lat"]), float(hit["lon"])

    resolved = None
    for attempt in _search_terms(query):
        resolved = _geocode(attempt, budget)
        if resolved or budget.left() < 1.5:
            break
    if not resolved:
        return None

    label, lat, lon = resolved
    # Coordinates of a named place do not change, so this never needs expiry.
    # Capped so a long-running install cannot grow the file without bound.
    if len(cache) > 400:
        cache = {}
    cache[key] = {"label": label, "lat": lat, "lon": lon}
    _write_json(_GEO_CACHE, cache)
    return label, lat, lon


def _spawn_location_helper():
    """Run the Windows Location Services helper without flashing a console."""
    script = os.path.join(_BASE, "..", "scripts", "get_native_location.py")
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return subprocess.Popen(
        [sys.executable, script],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs
    )


def _refresh_location_in_background() -> None:
    """Kick off a new fix, unless one was attempted very recently.

    Without the throttle, a machine with location switched off would start a
    helper process on every single weather question — each one destined to
    fail — while the answer comes from cache regardless.
    """
    try:
        if time.time() - os.path.getmtime(_LOC_CACHE) < 300:
            return
    except OSError:
        pass

    def run():
        try:
            _spawn_location_helper().wait(timeout=30)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


def _here_label(cached: dict) -> str:
    """What to call the user's own position out loud.

    The helper writes "your current location" as a placeholder until the
    reverse geocode names the place. Spoken back inside "the weather in ..."
    that reads like a form field, so it becomes "your area" instead.
    """
    city = str(cached.get("city") or "").strip()
    if not city or city.casefold() in ("your current location", "unknown"):
        return "your area"
    return city


def _current_location(budget: _Budget):
    """(label, lat, lon) for where the user is, or None with a reason.

    Returns (result, problem). A usable position is returned the moment one
    exists — the old code always answered "ask again in a moment", which is
    exactly the second ask the user should never have had to make.
    """
    cached = _read_json(_LOC_CACHE) or {}
    have_fix = cached.get("lat") is not None and cached.get("lon") is not None
    age = time.time() - float(cached.get("timestamp") or 0)

    if have_fix and age < _LOCATION_FRESH_S:
        # Good enough to answer with right now; no waiting, no subprocess.
        return (_here_label(cached),
                float(cached["lat"]), float(cached["lon"])), None

    if have_fix:
        # Stale but usable. Answer from it and refresh behind the user's back,
        # so the next question is current without this one paying for it.
        _refresh_location_in_background()
        return (_here_label(cached),
                float(cached["lat"]), float(cached["lon"])), None

    # Nothing on file at all — the only path that waits, and in practice only
    # reachable by asking within seconds of launch, since main.py starts a fix
    # at startup. Poll the file rather than wait on the process: the helper
    # publishes coordinates before it stops to look up the place name, so this
    # returns as soon as there is something to answer with.
    try:
        proc = _spawn_location_helper()
    except Exception as e:
        return None, f"Sir, I could not start the location service: {e}"

    deadline = time.monotonic() + min(_LOCATION_WAIT_S, max(budget.left() - 2.5, 1.0))
    fresh = {}
    while time.monotonic() < deadline:
        fresh = _read_json(_LOC_CACHE) or {}
        if fresh.get("lat") is not None and fresh.get("lon") is not None:
            break
        if proc.poll() is not None:
            # Helper finished without producing a fix; nothing more is coming.
            fresh = _read_json(_LOC_CACHE) or {}
            break
        time.sleep(0.2)

    if fresh.get("lat") is not None and fresh.get("lon") is not None:
        return (_here_label(fresh),
                float(fresh["lat"]), float(fresh["lon"])), None

    if fresh.get("error") == "denied":
        return None, ("Sir, Windows Location Services is switched off for desktop apps. "
                      "Enable 'Let desktop apps access your location' in Windows Settings, "
                      "or simply name the place you want.")
    if fresh.get("error") == "unsupported":
        return None, ("Sir, this machine has no location provider available. "
                      "Name the place and I will report on it directly.")
    return None, ("Sir, I could not fix your position just now. "
                  "Name the place and I will report on it directly.")


# The WMO names are nouns for a table, not for a sentence: "35°C with
# overcast" is not English. Only the cloud-cover codes need the help.
_SPOKEN_CONDITIONS = {
    0: "clear skies",
    1: "mainly clear skies",
    2: "partly cloudy skies",
    3: "overcast skies",
}


def _describe(code) -> str:
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "unknown conditions"
    if code in _SPOKEN_CONDITIONS:
        return _SPOKEN_CONDITIONS[code]
    return WEATHER_CODES.get(code, "unknown conditions").lower()


def _round(value):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _fetch_weather(lat: float, lon: float, budget: _Budget) -> dict:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat:.4f}&longitude={lon:.4f}"
        "&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
        "weather_code,wind_speed_10m,is_day"
        "&daily=temperature_2m_max,temperature_2m_min"
        "&forecast_days=1&timezone=auto"
    )
    data = _get_json(url, budget)
    if not data or "current" not in data:
        raise ValueError("the forecast service returned no current conditions")
    return data


def _compose(label: str, data: dict, away: bool) -> str:
    current = data.get("current") or {}
    daily = data.get("daily") or {}

    temp = _round(current.get("temperature_2m"))
    feels = _round(current.get("apparent_temperature"))
    desc = _describe(current.get("weather_code"))
    wind = _round(current.get("wind_speed_10m"))

    def first(seq):
        try:
            return _round(seq[0])
        except (TypeError, IndexError, KeyError):
            return None

    high = first(daily.get("temperature_2m_max"))
    low = first(daily.get("temperature_2m_min"))

    if temp is None:
        return f"Sir, the forecast for {label} came back incomplete."

    lines = [f"The current weather in {label} is {temp}°C with {desc}."]

    # Only worth saying when it actually disagrees with the thermometer.
    if feels is not None and abs(feels - temp) >= 3:
        lines.append(f"It feels closer to {feels}°C.")
    if high is not None and low is not None:
        lines.append(f"Today's range is {low} to {high}°C.")
    if wind is not None and wind >= 15:
        lines.append(f"Wind is {wind} kilometres per hour.")

    # Somewhere else in the world is often somewhere else in the day, and that
    # is usually the point of asking.
    if away:
        stamp = str(current.get("time") or "")
        if "T" in stamp:
            clock = stamp.split("T", 1)[1][:5]
            night = "" if current.get("is_day") else ", after dark"
            lines.append(f"Local time there is {clock}{night}.")

    return " ".join(lines)


def weather_action(parameters: dict, player=None, session_memory=None) -> str:
    parameters = parameters or {}
    city = parameters.get("city")
    if city is not None and not isinstance(city, str):
        city = str(city)

    budget = _Budget(_TOTAL_BUDGET)
    asked_for_somewhere_else = bool(city and city.strip())

    try:
        if asked_for_somewhere_else:
            place = _resolve_place(city, budget)
            if not place:
                msg = (f"Sir, I could not find anywhere called '{city.strip()}'. "
                       "Try naming a nearby city.")
                _log(msg, player)
                return msg
            label, lat, lon = place
        else:
            here, problem = _current_location(budget)
            if not here:
                _log(problem, player)
                return problem
            label, lat, lon = here

        data = _fetch_weather(lat, lon, budget)
        msg = _compose(label, data, away=asked_for_somewhere_else)
        _log(msg, player)
        return msg

    except (TimeoutError, urllib.error.URLError) as e:
        # Said plainly, because the model will read this out and the user can
        # act on it: it is the network, and asking again is worth a try.
        reason = getattr(e, "reason", e)
        msg = (f"Sir, the weather service did not respond in time ({reason}). "
               "The connection may be down — shall I try once more?")
        _log(msg, player)
        return msg
    except Exception as e:
        msg = f"Sir, I encountered an error fetching the weather: {e}"
        _log(msg, player)
        return msg


def web_weather_url() -> str:
    """Forecast page for wherever the user is, for the Weather (Web) button.

    Deliberately cache-only. This runs on the Qt thread when the button is
    clicked, so it must never touch the network — a known location makes the
    link specific, and without one a plain weather search is still correct.
    """
    cached = _read_json(_LOC_CACHE) or {}
    place = _here_label(cached)
    # _here_label falls back to "your area" when the place has no name yet,
    # which is fine spoken aloud but nonsense in a search box.
    query = f"weather in {place}" if place != "your area" else "weather"
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)


def _log(message: str, player=None) -> None:
    try:
        print(f"[Weather] {message}")
    except UnicodeEncodeError:
        # Place names carry accents and the Windows console is often cp1252.
        # Losing a log line to that would be absurd; losing the report to it
        # would be worse, and this is called from inside the success path.
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        safe = f"[Weather] {message}".encode(encoding, "replace").decode(encoding, "replace")
        print(safe)
    if player:
        try:
            player.write_log(f"SIVA: {message}")
        except Exception:
            pass
