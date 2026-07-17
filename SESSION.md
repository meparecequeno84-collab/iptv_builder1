# IPTV Finder v2.1 - Sesión de Desarrollo

## Estado del proyecto
- **Repo**: https://github.com/meparecequeno84-collab/iptv_builder1
- **Archivo principal**: main.py (~1270 líneas)
- **Build**: GitHub Actions → APK ARM
- **Último build exitoso**: 17/07/2026

## Funcionalidades implementadas

### Interfaz 1 - Playlist Manager
- Lista de playlists guardadas con nombre, URL, cantidad de canales
- Botones por playlist: CARGAR / EDITAR / BORRAR
- Botón +AGREGAR LISTA (dialogo con nombre + URL + EXPLORAR archivos)
- Botón BUSCAR WEB (scraping Google + extracción de links M3U)
- Botón FUENTES (15 fuentes IPTV públicas predefinidas)
- Persistencia en ~/.iptv_finder/playlists.json

### Interfaz 2 - Player / Visor IPTV
- Sidebar de categorías con conteo de canales
- Lista de canales con: Estado, Nombre, Grupo, Codec, Latencia, Estado
- Barra de búsqueda en tiempo real
- Botón ▶ por canal para reproducir (Video embebido)
- Botón TESTEAR CANALES (multi-hilo con progreso)
- Botón EXPORTAR (M3U + TXT a ~/IPTV_Exports/)
- Botón RECARGAR playlist
- Player con Pause/Play y Fullscreen toggle

### Control Remoto (D-pad / Android TV)
- Flechas Arriba/Abajo navegan entre botones con foco azul
- El scroll se mueve automáticamente para mostrar el botón enfocado
- Enter/OK activa el botón seleccionado
- Back vuelve del Player al Playlist Manager
- android.leanback = 1 en buildozer.spec

### Build Android
- **Arquitectura**: armeabi (ARM puro, sin v7 ni v8)
- **NDK**: 21e
- **API**: 28 (min 21)
- **Dependencias**: kivy, requests, beautifulsoup4, ffpyplayer, plyer

## Archivos clave
- `main.py` — Toda la app
- `buildozer.spec` — Config build Android ARM
- `.github/workflows/build.yml` — CI/CD GitHub Actions

## Pendiente / Mejoras futuras
- [ ] El VideoPlayer embebido no muestra video en PC (ffpyplayer no funciona bien) — en Android sí con player nativo
- [ ] Agregar EPG (Electronic Program Guide)
- [ ] Favoritos de canales
- [ ] Historial de canales vistos
- [ ] Subtítulos
- [ ] Mejorar performance con listas de 10K+ canales (virtualización de lista)
- [ ] soporte m3u8 HLS embebido
- [ ] Widget de pantalla completa con controles overlay en Android TV
- [ ] Remapeo de teclas del control remoto para play/pause/volumen
