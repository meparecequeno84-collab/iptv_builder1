import os
import re
import sys
import json
import time
import threading
import urllib.parse
import requests
import concurrent.futures
from datetime import datetime

os.environ['KIVY_LOG_LEVEL'] = 'warning'

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.spinner import Spinner
from kivy.uix.checkbox import CheckBox
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.clock import Clock
from kivy.core.window import Window

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

Window.clearcolor = (0.1, 0.1, 0.15, 1)
Window.fullscreen = False
Window.size = (1024, 600)


USER_AGENTS = [
    "VLC/3.0.20 LibVLC/3.0.20",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "curl/7.68.0",
]


def parse_m3u(content):
    channels = []
    lines = content.strip().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('#EXTINF:'):
            name = line.split(',', 1)[1].strip() if ',' in line else 'Sin nombre'
            group = ''
            if 'group-title=' in line:
                group = line.split('group-title="')[1].split('"')[0]
            logo = ''
            if 'tvg-logo=' in line:
                logo = line.split('tvg-logo="')[1].split('"')[0]
            i += 1
            if i < len(lines):
                url = lines[i].strip()
                if url and not url.startswith('#'):
                    channels.append({
                        'name': name,
                        'url': url,
                        'group': group,
                        'logo': logo,
                        'status': 'PENDIENTE',
                        'codec': '?',
                        'latency_ms': 0,
                    })
        i += 1
    return channels


def test_channel(channel, timeout=5, retries=2):
    url = channel['url']
    attempt = 0
    last_exc = None

    while attempt <= retries:
        try:
            start = time.time()
            headers = {
                'User-Agent': USER_AGENTS[attempt % len(USER_AGENTS)],
                'Icy-Meta': '1',
                'Accept': '*/*',
            }
            resp = requests.get(url, timeout=timeout, stream=True, headers=headers)
            latency = round((time.time() - start) * 1000)

            if resp.status_code == 200:
                chunk_iter = resp.iter_content(chunk_size=1024)
                data = b''
                for _ in range(3):
                    part = next(chunk_iter, None)
                    if not part:
                        break
                    data += part

                if data and len(data) > 0:
                    is_ts = data[:4] == b'G\x00\x00' or b'\x47' in data[:188]
                    is_m3u8 = b'#EXTM3U' in data or b'#EXTINF' in data

                    if is_ts or is_m3u8 or len(data) > 100:
                        codec = 'MPEG-TS' if is_ts else 'HLS' if is_m3u8 else 'Stream'
                        return {
                            **channel,
                            'status': 'ACTIVO',
                            'latency_ms': latency,
                            'codec': codec,
                        }

            return {**channel, 'status': 'SIN RESPUESTA', 'latency_ms': 0, 'codec': '?'}

        except requests.exceptions.Timeout:
            attempt += 1
            if attempt > retries:
                return {**channel, 'status': 'TIMEOUT', 'latency_ms': timeout * 1000, 'codec': '?'}
            time.sleep(0.5 * attempt)
        except requests.exceptions.ConnectionError:
            attempt += 1
            if attempt > retries:
                return {**channel, 'status': 'ERROR CONEXION', 'latency_ms': 0, 'codec': '?'}
            time.sleep(0.5 * attempt)
        except Exception as e:
            return {**channel, 'status': f'ERROR: {str(e)[:25]}', 'latency_ms': 0, 'codec': '?'}

    return {**channel, 'status': 'FALLIDO', 'latency_ms': 0, 'codec': '?'}


# Curated list of public playlists
KNOWN_SOURCES = [
    {"name": "iptv-org (13K+ canales mundiales)", "url": "https://iptv-org.github.io/iptv/index.m3u"},
    {"name": "Free-TV (canales verificados)", "url": "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8"},
    {"name": "iptv-org - Argentina", "url": "https://iptv-org.github.io/iptv/countries/ar.m3u"},
    {"name": "iptv-org - Brazil", "url": "https://iptv-org.github.io/iptv/countries/br.m3u"},
    {"name": "iptv-org - Mexico", "url": "https://iptv-org.github.io/iptv/countries/mx.m3u"},
    {"name": "iptv-org - España", "url": "https://iptv-org.github.io/iptv/countries/es.m3u"},
    {"name": "iptv-org - Colombia", "url": "https://iptv-org.github.io/iptv/countries/co.m3u"},
    {"name": "iptv-org - Chile", "url": "https://iptv-org.github.io/iptv/countries/cl.m3u"},
    {"name": "iptv-org - Peru", "url": "https://iptv-org.github.io/iptv/countries/pe.m3u"},
    {"name": "iptv-org - USA", "url": "https://iptv-org.github.io/iptv/countries/us.m3u"},
    {"name": "iptv-org - Deportes", "url": "https://iptv-org.github.io/iptv/categories/sports.m3u"},
    {"name": "iptv-org - Noticias", "url": "https://iptv-org.github.io/iptv/categories/news.m3u"},
    {"name": "iptv-org - Peliculas", "url": "https://iptv-org.github.io/iptv/categories/movies.m3u"},
    {"name": "iptv-org - Infantil", "url": "https://iptv-org.github.io/iptv/categories/kids.m3u"},
    {"name": "iptv-org - Musica", "url": "https://iptv-org.github.io/iptv/categories/music.m3u"},
]


def search_iptv_web(query, pages=1, allowed_domains=None):
    results = []
    if not HAS_BS4:
        return results

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        resp = requests.get(
            "https://www.google.com/search",
            params={"q": query, "num": pages * 10},
            timeout=10,
            headers=headers,
        )
        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.select("a[href]"):
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if not href.startswith("http"):
                continue
            if "google.com" in href or "youtube.com/results" in href:
                continue
            try:
                domain = urllib.parse.urlparse(href).netloc.lower().lstrip("www.")
            except Exception:
                domain = ""
            if allowed_domains and domain not in allowed_domains:
                continue
            if title and len(title) > 5:
                results.append({"title": title, "url": href, "domain": domain})

    except Exception:
        pass

    if not results:
        for source in KNOWN_SOURCES:
            results.append({
                "title": source["name"],
                "url": source["url"],
                "domain": "github.io",
            })

    return results


def extract_m3u_links(page_url, timeout=8, limit=10):
    found = []
    try:
        resp = requests.get(page_url, timeout=timeout,
                            headers={"User-Agent": "Mozilla/5.0 (compatible; IPTV-Checker/1.0)"})
        text = resp.text

        if HAS_BS4:
            soup = BeautifulSoup(text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("/"):
                    href = urllib.parse.urljoin(page_url, href)
                if any(ext in href.lower() for ext in (".m3u", ".m3u8", "pastebin.com/raw",
                                                        "raw.githubusercontent.com", "gist.githubusercontent.com")):
                    if href not in found:
                        found.append(href)
                        if len(found) >= limit:
                            return found

        for m in re.findall(r"https?://[^\s'\"]+\.(?:m3u8?|txt)\b", text, flags=re.IGNORECASE):
            if m not in found:
                found.append(m)
                if len(found) >= limit:
                    return found

    except Exception:
        pass
    return found


class IPTVFinderApp(App):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.channels = []
        self.active_channels = []
        self.testing = False
        self.all_groups = []
        self._test_start_time = 0

    def build(self):
        self.title = 'IPTV Finder v2.0'

        main_layout = BoxLayout(orientation='vertical', padding=8, spacing=8)

        header = BoxLayout(orientation='horizontal', size_hint_y=None, height=50)
        title_lbl = Label(
            text='[color=33ccff][b]IPTV FINDER v2.0[/b][/color]',
            markup=True, font_size=28, size_hint_x=0.5
        )
        header.add_widget(title_lbl)
        self.status_label = Label(
            text='[color=808080]Listo[/color]',
            markup=True, font_size=16, size_hint_x=0.5
        )
        header.add_widget(self.status_label)
        main_layout.add_widget(header)

        row1 = BoxLayout(orientation='horizontal', size_hint_y=None, height=44, spacing=8)
        self.url_input = TextInput(
            hint_text='URL M3U o ruta local...',
            font_size=16, multiline=False, size_hint_x=0.5,
            background_color=(0.2, 0.2, 0.25, 1),
            foreground_color=(1, 1, 1, 1),
            hint_text_color=(0.5, 0.5, 0.5, 1)
        )
        row1.add_widget(self.url_input)

        self.load_btn = Button(text='CARGAR', font_size=16, bold=True, size_hint_x=0.12,
                               background_color=(0.2, 0.6, 0.9, 1))
        self.load_btn.bind(on_press=self.load_playlist)
        row1.add_widget(self.load_btn)

        self.test_btn = Button(text='TESTEAR', font_size=16, bold=True, size_hint_x=0.12,
                               background_color=(0.2, 0.8, 0.4, 1))
        self.test_btn.bind(on_press=self.start_testing)
        row1.add_widget(self.test_btn)

        self.export_btn = Button(text='EXPORTAR', font_size=16, bold=True, size_hint_x=0.12,
                                 background_color=(0.9, 0.6, 0.2, 1))
        self.export_btn.bind(on_press=self.export_active)
        row1.add_widget(self.export_btn)

        self.search_btn = Button(text='BUSCAR WEB', font_size=16, bold=True, size_hint_x=0.12,
                                 background_color=(0.7, 0.3, 0.9, 1))
        self.search_btn.bind(on_press=self.web_search)
        row1.add_widget(self.search_btn)

        self.sources_btn = Button(text='FUENTES', font_size=16, bold=True, size_hint_x=0.1,
                                  background_color=(0.9, 0.3, 0.5, 1))
        self.sources_btn.bind(on_press=self.show_sources)
        row1.add_widget(self.sources_btn)

        main_layout.add_widget(row1)

        row2 = BoxLayout(orientation='horizontal', size_hint_y=None, height=40, spacing=8)

        row2.add_widget(Label(text='Grupo:', font_size=14, size_hint_x=0.06, color=(0.7, 0.7, 0.7, 1)))
        self.group_spinner = Spinner(
            text='Todos', font_size=14, size_hint_x=0.18,
            background_color=(0.25, 0.25, 0.3, 1)
        )
        row2.add_widget(self.group_spinner)

        row2.add_widget(Label(text='Timeout:', font_size=14, size_hint_x=0.07, color=(0.7, 0.7, 0.7, 1)))
        self.timeout_input = TextInput(
            text='5', font_size=14, multiline=False, size_hint_x=0.06,
            background_color=(0.25, 0.25, 0.3, 1), foreground_color=(1, 1, 1, 1)
        )
        row2.add_widget(self.timeout_input)

        row2.add_widget(Label(text='Reintentos:', font_size=14, size_hint_x=0.09, color=(0.7, 0.7, 0.7, 1)))
        self.retries_input = TextInput(
            text='2', font_size=14, multiline=False, size_hint_x=0.06,
            background_color=(0.25, 0.25, 0.3, 1), foreground_color=(1, 1, 1, 1)
        )
        row2.add_widget(self.retries_input)

        row2.add_widget(Label(text='Hilos:', font_size=14, size_hint_x=0.05, color=(0.7, 0.7, 0.7, 1)))
        self.threads_input = TextInput(
            text='20', font_size=14, multiline=False, size_hint_x=0.06,
            background_color=(0.25, 0.25, 0.3, 1), foreground_color=(1, 1, 1, 1)
        )
        row2.add_widget(self.threads_input)

        self.only_active_cb = CheckBox(size_hint_x=0.03)
        self.only_active_cb.active = True
        row2.add_widget(self.only_active_cb)
        row2.add_widget(Label(text='Solo activos', font_size=13, size_hint_x=0.1, color=(0.6, 0.6, 0.6, 1)))

        self.fetch_links_cb = CheckBox(size_hint_x=0.03)
        row2.add_widget(self.fetch_links_cb)
        row2.add_widget(Label(text='Extraer links', font_size=13, size_hint_x=0.11, color=(0.6, 0.6, 0.6, 1)))

        main_layout.add_widget(row2)

        progress_row = BoxLayout(orientation='horizontal', size_hint_y=None, height=28, spacing=6)
        self.progress_bar = ProgressBar(max=100, size_hint_x=0.55)
        progress_row.add_widget(self.progress_bar)
        self.pct_label = Label(text='0%', font_size=14, bold=True, color=(1, 1, 1, 1), size_hint_x=0.08)
        progress_row.add_widget(self.pct_label)
        self.eta_label = Label(text='', font_size=13, color=(0.7, 0.7, 0.7, 1), size_hint_x=0.22)
        progress_row.add_widget(self.eta_label)
        self.activity_label = Label(text='', font_size=13, color=(0.6, 0.8, 1, 1), size_hint_x=0.15)
        progress_row.add_widget(self.activity_label)
        main_layout.add_widget(progress_row)

        stats = BoxLayout(orientation='horizontal', size_hint_y=None, height=32, spacing=15)
        self.total_label = Label(text='Total: 0', font_size=15, color=(0.7, 0.7, 0.7, 1))
        stats.add_widget(self.total_label)
        self.active_label = Label(text='Activos: 0', font_size=15, color=(0.2, 0.9, 0.4, 1))
        stats.add_widget(self.active_label)
        self.inactive_label = Label(text='Inactivos: 0', font_size=15, color=(0.9, 0.3, 0.3, 1))
        stats.add_widget(self.inactive_label)
        self.codec_label = Label(text='Codecs: -', font_size=15, color=(0.6, 0.8, 1, 1))
        stats.add_widget(self.codec_label)
        main_layout.add_widget(stats)

        self.scroll = ScrollView(size_hint_y=1)
        self.channel_grid = GridLayout(cols=1, spacing=3, size_hint_y=None)
        self.channel_grid.bind(minimum_height=self.channel_grid.setter('height'))
        self.scroll.add_widget(self.channel_grid)
        main_layout.add_widget(self.scroll)

        return main_layout

    def load_playlist(self, instance):
        url = self.url_input.text.strip()
        if not url:
            self.show_popup('Error', 'Ingrese una URL o ruta de playlist')
            return
        self.status_label.text = '[color=ffcc00]Cargando...[/color]'
        self.load_btn.disabled = True
        self.progress_bar.value = 0
        self.pct_label.text = '0%'
        self.activity_label.text = 'Cargando'
        threading.Thread(target=self._load_playlist_thread, args=(url,), daemon=True).start()

    def _load_playlist_thread(self, url):
        try:
            Clock.schedule_once(lambda dt: self._update_load_progress(30, 'Descargando...'))
            if url.startswith('http'):
                resp = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
                content = resp.text
            else:
                with open(url, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()

            channels = parse_m3u(content)
            Clock.schedule_once(lambda dt: self._finish_load(channels))
        except Exception as e:
            Clock.schedule_once(lambda dt: self._finish_load_error(str(e)[:80]))

    def _update_load_progress(self, pct, msg):
        self.progress_bar.value = pct
        self.pct_label.text = f'{pct}%'
        self.status_label.text = f'[color=ffcc00]{msg}[/color]'

    def _finish_load(self, channels):
        self.channels = channels
        self._update_group_spinner()
        total = len(self.channels)
        self.status_label.text = f'[color=33ccff]Cargados: {total}[/color]'
        self.total_label.text = f'Total: {total}'
        self.active_label.text = 'Activos: 0'
        self.inactive_label.text = f'Inactivos: {total}'
        self.codec_label.text = 'Codecs: -'
        self.progress_bar.value = 100
        self.pct_label.text = '100%'
        self.eta_label.text = f'{total} canales'
        self.activity_label.text = 'Listo'
        self.load_btn.disabled = False
        self._show_channels(self.channels)

    def _finish_load_error(self, msg):
        self.show_popup('Error', f'Error al cargar: {msg}')
        self.status_label.text = '[color=ff3333]Error[/color]'
        self.load_btn.disabled = False

    def _update_group_spinner(self):
        groups = sorted(set(c['group'] for c in self.channels if c['group']))
        self.all_groups = groups
        values = ['Todos'] + groups
        self.group_spinner.values = values
        self.group_spinner.text = 'Todos'
        self.group_spinner.bind(on_select=self._on_group_select)

    def _on_group_select(self, spinner, text):
        if not self.channels:
            return
        if text == 'Todos':
            filtered = self.channels
        else:
            filtered = [c for c in self.channels if c['group'] == text]
        self._show_channels(filtered)

    def _show_channels(self, channels):
        self.channel_grid.clear_widgets()
        display = channels if not self.only_active_cb.active else [c for c in channels if c['status'] == 'ACTIVO']

        for ch in display:
            row = BoxLayout(orientation='horizontal', size_hint_y=None, height=36, spacing=8, padding=[8, 1])

            sc = {
                'PENDIENTE': '808080', 'ACTIVO': '33ff66', 'INACTIVO': 'ff3333',
                'TIMEOUT': 'ffcc00', 'SIN RESPUESTA': 'ff6633', 'ERROR CONEXION': 'ff3333',
                'FALLIDO': 'cc3333',
            }.get(ch['status'], '808080')

            row.add_widget(Label(text=f'[{ch["status"][0]}]', font_size=14, bold=True,
                                 color=(1, 1, 1, 1), size_hint_x=0.04))
            row.add_widget(Label(text=ch['name'][:38], font_size=13, color=(1, 1, 1, 1),
                                 size_hint_x=0.36, halign='left', text_size=(350, None)))
            row.add_widget(Label(text=ch['group'][:12] if ch['group'] else '-', font_size=12,
                                 color=(0.5, 0.5, 0.5, 1), size_hint_x=0.14))
            row.add_widget(Label(text=ch.get('codec', '?'), font_size=12,
                                 color=(0.4, 0.7, 1, 1), size_hint_x=0.1))
            row.add_widget(Label(text=f'{ch.get("latency_ms", 0)}ms', font_size=12,
                                 color=(0.6, 0.6, 0.6, 1), size_hint_x=0.1))
            row.add_widget(Label(text=f'[color={sc}]{ch["status"]}[/color]', markup=True,
                                 font_size=12, size_hint_x=0.16))

            self.channel_grid.add_widget(row)

    def start_testing(self, instance):
        if not self.channels:
            self.show_popup('Error', 'Cargue un playlist primero')
            return
        if self.testing:
            return

        self.testing = True
        self.test_btn.disabled = True
        self.status_label.text = '[color=ffcc00]Testeando...[/color]'
        self.progress_bar.value = 0
        self.pct_label.text = '0%'
        self.eta_label.text = 'Calculando...'
        self.activity_label.text = 'Testeando'
        self._test_start_time = time.time()

        try:
            self._timeout = int(self.timeout_input.text)
            self._retries = int(self.retries_input.text)
            self._threads = int(self.threads_input.text)
        except ValueError:
            self._timeout = 5
            self._retries = 2
            self._threads = 20

        threading.Thread(target=self._test_channels_thread, daemon=True).start()

    def _test_channels_thread(self):
        self.active_channels = []
        tested = [0]
        total = len(self.channels)
        codecs_found = {}
        lock = threading.Lock()

        def test_one(ch):
            return test_channel(ch, timeout=self._timeout, retries=self._retries)

        with concurrent.futures.ThreadPoolExecutor(max_workers=self._threads) as executor:
            futures = {executor.submit(test_one, ch): ch for ch in self.channels}
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                with lock:
                    tested[0] += 1
                    if result['status'] == 'ACTIVO':
                        self.active_channels.append(result)
                        codec = result.get('codec', '?')
                        codecs_found[codec] = codecs_found.get(codec, 0) + 1
                    p = (tested[0] / total) * 100
                    t = tested[0]
                    a = len(self.active_channels)
                    cf = ', '.join(f'{k}:{v}' for k, v in sorted(codecs_found.items()))
                    elapsed = time.time() - self._test_start_time
                    avg_per = elapsed / t if t > 0 else 0
                    remaining = avg_per * (total - t)
                    eta_str = self._format_time(remaining)
                    Clock.schedule_once(lambda dt, pp=p, tt=t, aa=a, cc=cf, ee=eta_str:
                                        self._update_progress(pp, tt, aa, total, cc, ee))

        total_time = self._format_time(time.time() - self._test_start_time)
        Clock.schedule_once(lambda dt, tt=total_time, n=len(self.active_channels), t=total:
                            self._finish_testing(tt, n, t))

    def _update_progress(self, progress, tested, active, total, codecs, eta):
        self.progress_bar.value = progress
        self.pct_label.text = f'{progress:.0f}%'
        self.eta_label.text = f'Quedan {eta} ({tested}/{total})'
        self.status_label.text = f'[color=ffcc00]{tested}/{total} | Activos: {active} | {progress:.0f}%[/color]'
        self.codec_label.text = f'Codecs: {codecs}'

    def _finish_testing(self, total_time, active_count, total):
        self.testing = False
        self.test_btn.disabled = False
        self.status_label.text = f'[color=33ff66]Activos: {active_count}/{total} | Tiempo: {total_time}[/color]'
        self.active_label.text = f'Activos: {active_count}'
        self.inactive_label.text = f'Inactivos: {total - active_count}'
        self.pct_label.text = '100%'
        self.eta_label.text = f'Completado en {total_time}'
        self.activity_label.text = 'Listo'
        self._show_channels(self.channels)

    def _format_time(self, seconds):
        if seconds < 60:
            return f'{seconds:.0f}s'
        elif seconds < 3600:
            m = int(seconds // 60)
            s = int(seconds % 60)
            return f'{m}m {s}s'
        else:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            return f'{h}h {m}m'

    def export_active(self, instance):
        if not self.active_channels:
            self.show_popup('Exportar', 'No hay canales activos para exportar')
            return

        filters = [
            ('M3U files', '*.m3u'),
            ('Text files', '*.txt'),
        ]

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')

        m3u_file = f'iptv_activos_{ts}.m3u'
        with open(m3u_file, 'w', encoding='utf-8') as f:
            f.write('#EXTM3U\n')
            for ch in self.active_channels:
                logo = f' tvg-logo="{ch["logo"]}"' if ch.get('logo') else ''
                group = f' group-title="{ch["group"]}"' if ch.get('group') else ''
                f.write(f'#EXTINF:-1{logo}{group},{ch["name"]}\n')
                f.write(f'{ch["url"]}\n')

        txt_file = f'iptv_activos_{ts}.txt'
        with open(txt_file, 'w', encoding='utf-8') as f:
            for ch in self.active_channels:
                f.write(f'{ch["name"]} | {ch.get("codec", "?")} | {ch.get("latency_ms", 0)}ms | {ch["url"]}\n')

        self.show_popup('Exportar', f'Guardados:\n{m3u_file}\n{txt_file}')

    def web_search(self, instance):
        if not HAS_BS4:
            self.show_popup('Error', 'Instale beautifulsoup4:\npip install beautifulsoup4')
            return
        self.search_btn.disabled = True
        self.status_label.text = '[color=ff66cc]Buscando web...[/color]'
        self.progress_bar.value = 0
        self.pct_label.text = '0%'
        self.activity_label.text = 'Buscando'
        self._test_start_time = time.time()
        threading.Thread(target=self._web_search_thread, daemon=True).start()

    def _web_search_thread(self):
        try:
            Clock.schedule_once(lambda dt: self._update_search_status('[color=ff66cc]Buscando en web...[/color]', 10))
            results = search_iptv_web("iptv m3u playlist free", pages=2)
            Clock.schedule_once(lambda dt: self._update_search_status('[color=ff66cc]Procesando resultados...[/color]', 25))

            m3u_links = []
            seen = set()

            for r in results:
                url = r["url"]
                if url in seen:
                    continue
                seen.add(url)
                if any(ext in url.lower() for ext in (".m3u", ".m3u8")):
                    m3u_links.append(url)

            if self.fetch_links_cb.active and results:
                total_pages = min(10, len(results))
                for idx, r in enumerate(results[:total_pages]):
                    p = 25 + (idx / total_pages) * 30
                    Clock.schedule_once(lambda dt, pp=p, ii=idx, tt=total_pages:
                                        self._update_search_status(f'[color=ff66cc]Extrayendo {ii+1}/{tt}...[/color]', pp))
                    links = extract_m3u_links(r["url"], limit=5)
                    for l in links:
                        if l not in seen:
                            seen.add(l)
                            m3u_links.append(l)

            if m3u_links:
                all_channels = []
                for idx, link in enumerate(m3u_links[:10]):
                    pct = 55 + (idx / min(len(m3u_links), 10)) * 40
                    Clock.schedule_once(lambda dt, pp=pct, ii=idx, tt=len(m3u_links):
                                        self._update_search_status(f'[color=ff66cc]Descargando {ii+1}/{min(tt,10)}...[/color]', pp))
                    try:
                        resp = requests.get(link, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
                        if resp.status_code == 200 and ('#EXTM3U' in resp.text or '#EXTINF' in resp.text):
                            chs = parse_m3u(resp.text)
                            all_channels.extend(chs)
                    except Exception:
                        pass

                if all_channels:
                    elapsed = self._format_time(time.time() - self._test_start_time)
                    Clock.schedule_once(lambda dt, chs=all_channels, ml=len(m3u_links), el=elapsed:
                                        self._finish_web_search(chs, ml, el))
                else:
                    Clock.schedule_once(lambda dt, r=len(results), m=len(m3u_links):
                                        self._finish_web_search_empty(r, m))
            else:
                Clock.schedule_once(lambda dt, r=len(results):
                                    self._finish_web_search_no_links(r))

        except Exception as e:
            Clock.schedule_once(lambda dt, msg=str(e)[:60]:
                                self._finish_web_search_error(msg))

        Clock.schedule_once(lambda dt: setattr(self, 'search_btn_disabled', False))

    def _update_search_status(self, text, pct):
        self.status_label.text = text
        self.progress_bar.value = pct
        self.pct_label.text = f'{pct:.0f}%'

    def _finish_web_search(self, channels, m3u_count, elapsed):
        self.channels = channels
        self._update_group_spinner()
        total = len(self.channels)
        self.status_label.text = f'[color=33ccff]Encontrados: {total} canales | {elapsed}[/color]'
        self.total_label.text = f'Total: {total}'
        self.active_label.text = 'Activos: 0'
        self.inactive_label.text = f'Inactivos: {total}'
        self.progress_bar.value = 100
        self.pct_label.text = '100%'
        self.eta_label.text = f'Completado en {elapsed}'
        self.activity_label.text = 'Listo'
        self.search_btn.disabled = False
        self._show_channels(self.channels)
        self.show_popup('Busqueda Web', f'Se cargaron {total} canales desde {m3u_count} playlists')

    def _finish_web_search_empty(self, results_count, links_count):
        msg = f'Se encontro {results_count} resultados, {links_count} links M3U\nPero no se pudieron cargar canales'
        self.show_popup('Busqueda Web', msg)
        self.status_label.text = f'[color=ffcc00]{results_count} resultados web[/color]'
        self.search_btn.disabled = False

    def _finish_web_search_no_links(self, results_count):
        msg = f'Se encontro {results_count} resultados.\nMarque "Extraer links" y busque de nuevo,\nO use el boton FUENTES para listas conocidas.'
        self.show_popup('Busqueda Web', msg)
        self.status_label.text = f'[color=ffcc00]{results_count} resultados web[/color]'
        self.search_btn.disabled = False

    def _finish_web_search_error(self, msg):
        self.show_popup('Error', f'Error en busqueda: {msg}')
        self.status_label.text = '[color=ff3333]Error busqueda[/color]'
        self.search_btn.disabled = False

    def show_sources(self, instance):
        content = BoxLayout(orientation='vertical', padding=10, spacing=8)

        scroll = ScrollView(size_hint_y=0.85)
        btn_list = BoxLayout(orientation='vertical', size_hint_y=None, spacing=4)
        btn_list.bind(minimum_height=btn_list.setter('height'))

        for source in KNOWN_SOURCES:
            btn = Button(
                text=source["name"],
                font_size=14,
                size_hint_y=None,
                height=38,
                background_color=(0.2, 0.25, 0.35, 1),
                halign='left',
                padding=[12, 0],
            )
            btn.url = source["url"]
            btn.bind(on_press=self._load_source)
            btn_list.add_widget(btn)

        scroll.add_widget(btn_list)
        content.add_widget(scroll)

        close_btn = Button(text='Cerrar', font_size=16, size_hint_y=0.15, size_hint_x=0.3,
                           pos_hint={'center_x': 0.5})
        content.add_widget(close_btn)

        self._sources_popup = Popup(title='Fuentes IPTV Publicas', content=content,
                                     size_hint=(0.85, 0.8), auto_dismiss=False)
        close_btn.bind(on_press=self._sources_popup.dismiss)
        self._sources_popup.open()

    def _load_source(self, instance):
        url = instance.url
        self._sources_popup.dismiss()
        self.url_input.text = url
        self.load_playlist(None)

    def show_popup(self, title, message):
        content = BoxLayout(orientation='vertical', padding=10, spacing=10)
        content.add_widget(Label(text=message, font_size=16, size_hint_y=0.7))
        close_btn = Button(text='Cerrar', font_size=16, size_hint_y=0.3, size_hint_x=0.4,
                           pos_hint={'center_x': 0.5})
        content.add_widget(close_btn)
        popup = Popup(title=title, content=content, size_hint=(0.7, 0.4), auto_dismiss=False)
        close_btn.bind(on_press=popup.dismiss)
        popup.open()


if __name__ == '__main__':
    IPTVFinderApp().run()
