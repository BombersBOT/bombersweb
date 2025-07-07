import os
import json
import logging
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from geopy.geocoders import Nominatim
from pyproj import Transformer
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- CONFIG ---
LAYER_URL = ("https://services7.arcgis.com/ZCqVt1fRXwwK6GF4/arcgis/rest/services/"
             "ACTUACIONS_URGENTS_online_PRO_AMB_FASE_VIEW/FeatureServer/0")
# Puedes ajustar este umbral para filtrar cuántas dotaciones se muestran en la web
MIN_DOTACIONS_DISPLAY = 1 # Mostrar todos los incendios con 1 o más dotaciones
API_KEY = os.getenv("ARCGIS_API_KEY", "") 
GEOCODER_USER_AGENT = os.getenv("GEOCODER_USER_AGENT", "bombers_web_app")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

retries = Retry(total=3, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
session = requests.Session()
session.mount('https://', HTTPAdapter(max_retries=retries))

TRANSFORM = Transformer.from_crs(25831, 4326, always_xy=True)

# --- CONSULTA ARCGIS ---
def fetch_features(limit=50): # Obtener más incidentes para la web
    params = {
        "f": "json",
        "where": "1=1",
        "outFields": (
            "ESRI_OID,ACT_NUM_VEH,COM_FASE,ACT_DAT_ACTUACIO,"
            "TAL_DESC_ALARMA1,TAL_DESC_ALARMA2,MUN_NOM_MUNICIPI" # Intentamos obtener el municipio de ArcGIS
        ),
        "orderByFields": "ACT_DAT_ACTUACIO DESC",
        "resultRecordCount": limit,
        "returnGeometry": "true",
        "cacheHint": "true",
    }
    if API_KEY:
        params["token"] = API_KEY
    
    try:
        r = session.get(f"{LAYER_URL}/query", params=params, timeout=30)
        r.raise_for_status() 
    except requests.exceptions.Timeout:
        logging.error("Timeout al consultar ArcGIS. Servidor no respondió a tiempo.")
        return []
    except requests.exceptions.RequestException as e:
        logging.error(f"Error de conexión al consultar ArcGIS: {e}")
        if "400" in str(e) and "Invalid query parameters" in str(e):
            logging.warning("Error 400 al obtener MUN_NOM_MUNICIPI. Intentando sin él.")
            params["outFields"] = ("ACT_NUM_VEH,COM_FASE,ESRI_OID,ACT_DAT_ACTUACIO,"
                                   "TAL_DESC_ALARMA1,TAL_DESC_ALARMA2")
            try:
                r = session.get(f"{LAYER_URL}/query", params=params, timeout=30)
                r.raise_for_status()
                data = r.json()
                for feature in data.get("features", []):
                    feature["attributes"]["_municipio_from_arcgis_success"] = False
                return data.get("features", [])
            except requests.exceptions.RequestException as e_fallback:
                logging.error(f"Error en fallback de ArcGIS: {e_fallback}")
                return []
        return []

    data = r.json()
    if "error" in data:
        logging.error("ArcGIS devolvió un error en los datos: %s", data["error"]["message"])
        if data["error"]["code"] == 400 and "Invalid query parameters" in data["error"]["message"]:
            logging.warning("Error 400 al obtener MUN_NOM_MUNICIPI. Intentando sin él. (Post-JSON parse)")
            params["outFields"] = ("ACT_NUM_VEH,COM_FASE,ESRI_OID,ACT_DAT_ACTUACIO,"
                                   "TAL_DESC_ALARMA1,TAL_DESC_ALARMA2")
            try:
                r = session.get(f"{LAYER_URL}/query", params=params, timeout=30)
                r.raise_for_status()
                data = r.json()
                for feature in data.get("features", []):
                    feature["attributes"]["_municipio_from_arcgis_success"] = False
                return data.get("features", [])
            except requests.exceptions.RequestException as e_fallback:
                logging.error(f"Error en fallback de ArcGIS: {e_fallback}")
                return []
        return []
    
    for feature in data.get("features", []):
        feature["attributes"]["_municipio_from_arcgis_success"] = True
    return data.get("features", [])

# --- UTILIDADES ---
def tipo_val(a):
    d = (a.get("TAL_DESC_ALARMA1","")+" "+a.get("TAL_DESC_ALARMA2","")).lower()
    
    if "urbà" in d or "urbana" in d:
        return 3
    elif "agrí" in d:
        return 2
    elif "forestal" in d or "vegetació" in d:
        return 1
    else:
        return 3

def classify(a):
    return {1: "forestal", 2: "agrícola", 3: "urbà"}[tipo_val(a)]

def utm_to_latlon(x, y):
    lon, lat = TRANSFORM.transform(x, y)
    return lat, lon

def get_address_components_from_coords(geom):
    street = ""
    municipality = ""
    
    if geom and geom["x"] is not None and geom["y"] is not None: 
        lat, lon = TRANSFORM.transform(geom["x"], geom["y"])
        try:
            loc = GEOCODER.reverse((lat, lon), exactly_one=True, timeout=15, language="ca")
            if loc and loc.raw:
                address_parts = loc.raw.get('address', {})
                street = address_parts.get('road', '') or address_parts.get('building', '') or address_parts.get('amenity', '')
                municipality = address_parts.get('city', '') or \
                               address_parts.get('town', '') or \
                               address_parts.get('village', '') or \
                               address_parts.get('county', '')

                if not municipality and loc.address:
                    parts = [p.strip() for p in loc.address.split(',')]
                    for p in reversed(parts):
                        if not any(char.isdigit() for char in p) and len(p) > 2 and p.lower() not in ["catalunya", "españa"]:
                            municipality = p
                            break

        except Exception as e:
            logging.debug(f"Error al geocodificar: {e}")
            pass
    
    return {"street": street, "municipality": municipality}


def format_incident_data(feature):
    a = feature["attributes"]
    geom = feature.get("geometry")

    municipio_arcgis = a.get("MUN_NOM_MUNICIPI")
    _municipio_from_arcgis_success = a.get("_municipio_from_arcgis_success", False)

    address_components = get_address_components_from_coords(geom)
    calle_final = address_components["street"] if address_components["street"] else ""
    municipio_geocoded = address_components["municipality"] if address_components["municipality"] else ""

    municipio_final = "ubicació desconeguda"
    
    if _municipio_from_arcgis_success and municipio_arcgis:
        municipio_final = municipio_arcgis
    elif municipio_geocoded:
        municipio_final = municipio_geocoded
    
    location_str = ""
    if calle_final and municipio_final != "ubicació desconeguda":
        location_str = f"{calle_final}, {municipio_final}"
    elif municipio_final != "ubicació desconeguda":
        location_str = municipio_final
    elif calle_final:
        location_str = calle_final
    else:
        location_str = "ubicació desconeguda"

    hora_timestamp = a.get("ACT_DAT_ACTUACIO")
    hora_formato = ""
    if hora_timestamp:
        hora = datetime.fromtimestamp(hora_timestamp / 1000, tz=timezone.utc)\
                   .astimezone(ZoneInfo("Europe/Madrid")).strftime("%H:%M")
        hora_formato = hora
    
    lat_lon_coords = None
    if geom and geom["x"] is not None and geom["y"] is not None:
        lat_lon_coords = utm_to_latlon(geom["x"], geom["y"])

    return {
        "id": a.get("ESRI_OID"),
        "tipo": classify(a),
        "ubicacion": location_str,
        "hora": hora_formato,
        "dotaciones": a.get("ACT_NUM_VEH", 0),
        "fase": a.get("COM_FASE", "Desconeguda"),
        "lat": lat_lon_coords[0] if lat_lon_coords else None,
        "lon": lat_lon_coords[1] if lat_lon_coords else None,
        "timestamp": hora_timestamp # Para ordenar correctamente por fecha/hora
    }

# --- FUNCIÓN SERVERLESS PRINCIPAL ---
def handler(request):
    """
    Función serverless para obtener los datos de los incidentes y devolverlos como JSON.
    """
    logging.info("Iniciando solicitud a la función serverless get_incidents.")
    
    feats = fetch_features(limit=50) 

    if not feats:
        logging.info("No se encontraron features en ArcGIS.")
        return json.dumps({"error": "No data available", "incidents": []}), 200, {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}

    incidents_data = []
    for feature in feats:
        if feature["attributes"].get("ACT_NUM_VEH", 0) >= MIN_DOTACIONS_DISPLAY:
           incidents_data.append(format_incident_data(feature))

    # Ordenar los incidentes por timestamp original (más reciente primero)
    incidents_data.sort(key=lambda x: x.get("timestamp", 0), reverse=True) 

    logging.info(f"Se encontraron {len(incidents_data)} incidentes para la web.")
    
    return json.dumps({"incidents": incidents_data}), 200, {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}

# Para probar localmente (no necesario para Vercel/Netlify)
if __name__ == "__main__":
    # Simula una llamada a la función handler para ver la salida
    # Configura variables de entorno para pruebas locales si necesitas geocodificador o API_KEY
    # os.environ["ARCGIS_API_KEY"] = "tu_api_key_arcgis"
    # os.environ["GEOCODER_USER_AGENT"] = "test_local_web_app"
    
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import urllib.parse

    # Clase simple para simular una petición HTTP
    class MockRequest:
        def __init__(self, path):
            self.path = path
            self.query = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
            self.method = "GET"
            self.headers = {}
            self.body = b""

    # Servidor HTTP simple para probar la función handler
    class SimpleHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            mock_request = MockRequest(self.path)
            response_body_str, status_code, headers_dict = handler(mock_request)
            
            self.send_response(status_code)
            for key, value in headers_dict.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response_body_str.encode('utf-8'))

    # Para ejecutar el servidor de prueba:
    # Asegúrate de tener `requests`, `geopy`, `pyproj`, `zoneinfo` instalados en tu entorno local.
    # Luego ejecuta este script. Podrás acceder a http://localhost:8000/api/get_incidents
    # para ver el JSON.
    try:
        server_address = ('', 8000)
        httpd = HTTPServer(server_address, SimpleHandler)
        print("Servidor de prueba iniciado en http://localhost:8000/")
        print("Accede a http://localhost:8000/api/get_incidents para ver los datos.")
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor de prueba detenido.")
        httpd.server_close()
