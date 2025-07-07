import os
import json
import logging
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo # pip install zoneinfo si no está
# geopy y pyproj se eliminan temporalmente para depuración
# from geopy.geocoders import Nominatim
# from pyproj import Transformer
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- CONFIG ---
LAYER_URL = ("https://services7.arcgis.com/ZCqVt1fRXwwK6GF4/arcgis/rest/services/"
             "ACTUACIONS_URGENTS_online_PRO_AMB_FASE_VIEW/FeatureServer/0")
MIN_DOTACIONS = 3 # Puedes ajustar este umbral si quieres filtrar para la web
API_KEY = os.getenv("ARCGIS_API_KEY", "") # Para ArcGIS, si tienes una clave
# GEOCODER_USER_AGENT se elimina si no usamos geopy
# GEOCODER_USER_AGENT = os.getenv("GEOCODER_USER_AGENT", "bombers_web_app")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

# Configuración de reintentos para requests
retries = Retry(total=3, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
session = requests.Session()
session.mount('https://', HTTPAdapter(max_retries=retries))

# TRANSFORM se elimina si no usamos pyproj
# TRANSFORM = Transformer.from_crs(25831, 4326, always_xy=True) # Para transformar coordenadas

# --- CONSULTA ARCGIS ---
def fetch_features(limit=100):
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
        return 3 # Default to urbà

def classify(a):
    return {1: "forestal", 2: "agrícola", 3: "urbà"}[tipo_val(a)]

# utm_to_latlon se elimina si no usamos pyproj
# def utm_to_latlon(x, y):
#     lon, lat = TRANSFORM.transform(x, y)
#     return lat, lon

# get_address_components_from_coords se simplifica/elimina si no usamos geopy
def get_address_components_from_coords(geom):
    """
    Versión simplificada sin geopy. Solo devuelve datos básicos si existen.
    """
    # En esta versión, no podemos geocodificar, así que devolvemos vacío
    # o intentamos inferir algo del nombre del municipio si vino de ArcGIS
    return {"street": "", "municipality": ""}


def format_incident_data(feature):
    a = feature["attributes"]
    geom = feature.get("geometry")

    municipio_arcgis = a.get("MUN_NOM_MUNICIPI")
    _municipio_from_arcgis_success = a.get("_municipio_from_arcgis_success", False)

    # get_address_components_from_coords ya no usa geopy
    address_components = get_address_components_from_coords(geom) # Esto devolverá vacío
    calle_final = "" # No podemos obtener calle sin geocodificador
    municipio_geocoded = "" # No podemos obtener municipio por geocodificador

    municipio_final = "ubicació desconeguda"
    
    # Lógica de prioridad: ArcGIS > Desconocido (sin geocodificador)
    if _municipio_from_arcgis_success and municipio_arcgis:
        municipio_final = municipio_arcgis
    # El `elif municipio_geocoded:` se elimina o pasa a ser un else con "desconocida"

    location_str = ""
    # Si tenemos municipio de ArcGIS, lo usamos, la calle será vacía
    if municipio_final != "ubicació desconeguda":
        location_str = municipio_final
    else: # Si ni siquiera ArcGIS nos dio el municipio
        location_str = "ubicació desconocida"


    hora = datetime.fromtimestamp(a["ACT_DAT_ACTUACIO"]/1000, tz=timezone.utc)\
               .astimezone(ZoneInfo("Europe/Madrid")).strftime("%H:%M")
    
    return {
        "id": a.get("ESRI_OID"),
        "tipo": classify(a),
        "ubicacion": location_str, # Esto será solo el municipio o "ubicación desconocida"
        "hora": hora,
        "dotaciones": a.get("ACT_NUM_VEH"),
        "fase": a.get("COM_FASE", "Desconeguda"),
        "lat": geom["y"] if geom else None, 
        "lon": geom["x"] if geom else None,
        # lat_lon convertido se elimina si no usamos pyproj
        "lat_lon": None # No podemos convertir a lat/lon sin pyproj
    }

# --- FUNCIÓN SERVERLESS PRINCIPAL ---
def handler(request):
    logging.info("Iniciando solicitud a la función serverless get_incidents (VERSION DEBUG SIN GEOPY/PYPROJ).")
    
    feats = fetch_features(limit=50)

    if not feats:
        logging.info("No se encontraron features en ArcGIS.")
        return json.dumps({"error": "No data available", "incidents": []}), 200, {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}

    incidents_data = []
    for feature in feats:
        # Puedes añadir filtros aquí si es necesario
        incidents_data.append(format_incident_data(feature))

    # Ordenar los incidentes (siempre por la hora, ya que no tenemos fecha completa para ordenar)
    # Mejor ordenar por ID si no hay un buen campo de tiempo
    incidents_data.sort(key=lambda x: x["id"], reverse=True)


    logging.info(f"Se encontraron {len(incidents_data)} incidentes para la web.")
    
    return json.dumps({"incidents": incidents_data}), 200, {
        'Content-Type': 'application/json', 
        'Access-Control-Allow-Origin': '*' # Permitir acceso desde cualquier dominio para el frontend
    }

# No es necesario el bloque if __name__ == "__main__": en un serverless
