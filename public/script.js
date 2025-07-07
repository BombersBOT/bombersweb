document.addEventListener('DOMContentLoaded', async () => {
    const incidentsListDiv = document.getElementById('incidents-list');
    const mapDiv = document.getElementById('map');
    let map; // Variable para el mapa de Leaflet

    // Función para inicializar y mostrar el mapa
    function initMap(incidents) {
        // Coordenadas de Barcelona como centro por defecto
        const defaultLat = 41.3851;
        const defaultLon = 2.1734;

        if (!map) { // Inicializar el mapa solo una vez
            map = L.map(mapDiv).setView([defaultLat, defaultLon], 8); // Zoom inicial
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            }).addTo(map);
        } else {
            // Limpiar marcadores existentes si el mapa ya está inicializado
            map.eachLayer(layer => {
                if (layer instanceof L.Marker) {
                    map.removeLayer(layer);
                }
            });
        }

        // Añadir marcadores al mapa
        incidents.forEach(incident => {
            if (incident.lat_lon && incident.lat_lon.length === 2) {
                const [lat, lon] = incident.lat_lon;
                const marker = L.marker([lat, lon]).addTo(map);
                marker.bindPopup(`<b>${incident.tipo.toUpperCase()}</b><br>${incident.ubicacion}<br>🕒 ${incident.hora} | 🚒 ${incident.dotaciones} dot.`);
            }
        });
        // Ajustar el mapa para que muestre todos los marcadores
        if (incidents.length > 0) {
            const latLngs = incidents.filter(i => i.lat_lon).map(i => L.latLng(i.lat_lon[0], i.lat_lon[1]));
            if (latLngs.length > 0) {
                const bounds = L.latLngBounds(latLngs);
                map.fitBounds(bounds, { padding: [50, 50] }); // Añadir padding
            }
        }
    }


    async function fetchAndDisplayIncidents() {
        incidentsListDiv.innerHTML = '<p>Cargando avisos...</p>';
        try {
            // Llama a tu función serverless (en desarrollo, será /api/get_incidents)
            const response = await fetch('/api/get_incidents');
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            const data = await response.json();

            if (data.error) {
                incidentsListDiv.innerHTML = `<p>Error al cargar los avisos: ${data.error}</p>`;
                return;
            }

            if (data.incidents && data.incidents.length > 0) {
                incidentsListDiv.innerHTML = ''; // Limpiar "Cargando..."
                
                // Ordenar por dotaciones (más relevante) y luego por hora (más reciente)
                // O según quieras priorizar para la lista
                data.incidents.sort((a, b) => {
                    if (b.dotaciones !== a.dotaciones) {
                        return b.dotaciones - a.dotaciones; // Más dotaciones primero
                    }
                    // Si las dotaciones son iguales, ordenar por hora (más reciente primero)
                    const [hA, mA] = a.hora.split(':').map(Number);
                    const [hB, mB] = b.hora.split(':').map(Number);
                    const timeA = hA * 60 + mA;
                    const timeB = hB * 60 + mB;
                    return timeB - timeA;
                });

                data.incidents.forEach(incident => {
                    const card = document.createElement('div');
                    card.className = 'incident-card';
                    card.innerHTML = `
                        <h2>🔥 ${incident.tipo.toUpperCase()}</h2>
                        <p class="ubicacion">${incident.ubicacion}</p>
                        <p>🕒 ${incident.hora} | <span class="dotaciones">🚒 ${incident.dotaciones} dot.</span></p>
                        <p>Fase: ${incident.fase}</p>
                    `;
                    incidentsListDiv.appendChild(card);
                });
                initMap(data.incidents); // Inicializar/actualizar el mapa
            } else {
                incidentsListDiv.innerHTML = '<p>No se encontraron avisos recientes.</p>';
                initMap([]); // Inicializar mapa vacío si no hay incidentes
            }
        } catch (error) {
            console.error('Error fetching incidents:', error);
            incidentsListDiv.innerHTML = `<p>Error al cargar los avisos: ${error.message}. Inténtalo de nuevo más tarde.</p>`;
            initMap([]); // Inicializar mapa vacío si hay error
        }
    }

    fetchAndDisplayIncidents(); // Cargar al iniciar la página
    // Opcional: actualizar cada cierto tiempo
    // setInterval(fetchAndDisplayIncidents, 5 * 60 * 1000); // Actualizar cada 5 minutos
});
