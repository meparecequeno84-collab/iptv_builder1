import os
import re
import sys
import json
import time
import shutil
import subprocess
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
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition
from kivy.uix.video import Video
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle

try:
    from plyer import filechooser
    HAS_FILECHOOSER = True
except ImportError:
    HAS_FILECHOOSER = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

Window.clearcolor = (0.08, 0.08, 0.12, 1)
Window.fullscreen = False
Window.size = (1024, 600)

FOCUS_COLOR = (0.3, 0.6, 1.0, 1)
UNFOCUS_COLOR = None

def _apply_focus_style(widget, focused):
    if focused:
        widget._saved_bg = widget.background_color
        widget.background_color = FOCUS_COLOR
        widget.bold = True
    else:
        if hasattr(widget, '_saved_bg') and widget._saved_bg:
            widget.background_color = widget._saved_bg
        widget.bold = False

def _redraw_div(inst):
    inst.canvas.clear()
    with inst.canvas:
        Color(0.2, 0.5, 0.8, 1)
        Rectangle(pos=inst.pos, size=inst.size)


DATA_DIR = os.path.join(os.path.expanduser('~'), '.iptv_finder')
PLAYLISTS_FILE = os.path.join(DATA_DIR, 'playlists.json')

USER_AGENTS = [
    "VLC/3.0.20 LibVLC/3.0.20",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "curl/7.68.0",
]

KNOWN_SOURCES = [
    {"name": "iptv-org (13K+ canales mundiales)", "url": "https://iptv-org.github.io/iptv/index.m3u"},
    {"name": "Free-TV (canales verificados)", "url": "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8"},
    {"name": "iptv-org - Argentina", "url": "https://iptv-org.github.io/iptv/countries/ar.m3u"},
    {"name": "iptv-org - Brazil", "url": "https://iptv-org.github.io/iptv/countries/br.m3u"},
    {"name": "iptv-org - Mexico", "url": "https://iptv-org.github.io/iptv/countries/mx.m3u"},
    {"name": "iptv-org - Espana", "url": "https://iptv-org.github.io/iptv/countries/es.m3u"},
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


# ─── Persistencia ───────────────────────────────────────────────────────────────

def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_playlists():
    ensure_data_dir()
    if os.path.exists(PLAYLISTS_FILE):
        try:
            with open(PLAYLISTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_playlists(playlists):
    ensure_data_dir()
    with open(PLAYLISTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(playlists, f, ensure_ascii=False, indent=2)


# ─── M3U Parsing ────────────────────────────────────────────────────────────────

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
                        'name': name, 'url': url, 'group': group,
                        'logo': logo, 'status': 'PENDIENTE',
                        'codec': '?', 'latency_ms': 0,
                    })
        i += 1
    return channels


# ─── Canal Testing ───────────────────────────────────────────────────────────────

def test_channel(channel, timeout=5, retries=2):
    url = channel['url']
    attempt = 0
    while attempt <= retries:
        try:
            start = time.time()
            headers = {
                'User-Agent': USER_AGENTS[attempt % len(USER_AGENTS)],
                'Icy-Meta': '1', 'Accept': '*/*',
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
                        return {**channel, 'status': 'ACTIVO', 'latency_ms': latency, 'codec': codec}
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


# ─── Web Search ──────────────────────────────────────────────────────────────────

def search_iptv_web(query, pages=1, allowed_domains=None):
    results = []
    if not HAS_BS4:
        return results
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = requests.get(
            "https://www.google.com/search",
            params={"q": query, "num": pages * 10},
            timeout=10, headers=headers,
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
            results.append({"title": source["name"], "url": source["url"], "domain": "github.io"})
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


# ═════════════════════════════════════════════════════════════════════════════════
#  APLICACION PRINCIPAL
# ═════════════════════════════════════════════════════════════════════════════════

class IPTVFinderApp(App):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.title = 'IPTV Finder'
        self.playlists = load_playlists()
        self.channels = []
        self.active_channels = []
        self.testing = False
        self.all_groups = []
        self.group_counts = {}
        self.current_playlist_id = None
        self.current_group = 'Todos'
        self._test_start_time = 0
        self._editing_id = None
        self._popup = None
        self._load_popup = None
        self._load_label = None
        self._load_progress = None
        self._load_pct = None

    def build(self):
        self.sm = ScreenManager(transition=SlideTransition(direction='left'))
        self.sm.add_widget(self._build_playlist_manager())
        self.sm.add_widget(self._build_player())
        self._focusable_widgets = []
        self._focus_index = 0
        Window.bind(on_keyboard=self._on_keyboard)
        Clock.schedule_once(self._collect_focusables, 0.5)
        return self.sm

    def _collect_focusables(self, dt):
        self._focusable_widgets = []
        for child in self.pm_list.children:
            if isinstance(child, BoxLayout):
                for w in child.children:
                    if isinstance(w, Button):
                        w.focused = False
                        self._focusable_widgets.append(w)
        if self._focusable_widgets:
            self._focus_index = 0
            self._focus_widget(0)

    def _focus_widget(self, idx):
        if not self._focusable_widgets:
            return
        for w in self._focusable_widgets:
            _apply_focus_style(w, False)
        idx = idx % len(self._focusable_widgets)
        self._focus_index = idx
        w = self._focusable_widgets[idx]
        _apply_focus_style(w, True)
        self._focused_widget = w
        self._scroll_to_widget(w)

    def _scroll_to_widget(self, widget):
        scroll = None
        if self.sm.current == 'playlist_manager':
            scroll = self.pm_scroll
        elif self.sm.current == 'player':
            parent = widget.parent
            while parent:
                if isinstance(parent, ScrollView):
                    scroll = parent
                    break
                parent = parent.parent
        if scroll and widget.parent:
            widget_y = widget.y
            scroll_y = scroll.scroll_y
            scroll_h = scroll.height
            container_h = widget.parent.height
            if container_h <= scroll_h:
                return
            visible_bottom = (1 - scroll_y) * (container_h - scroll_h)
            visible_top = visible_bottom + scroll_h
            if widget_y < visible_bottom:
                scroll.scroll_y = 1 - (widget_y / (container_h - scroll_h))
            elif widget_y + widget.height > visible_top:
                scroll.scroll_y = 1 - ((widget_y + widget.height - scroll_h) / (container_h - scroll_h))

    def _on_keyboard(self, window, key, scancode, codepoint, modifiers):
        if key == 27:
            if self.sm.current == 'player':
                self.sm.current = 'playlist_manager'
                return True
        if key == 13 or key == 32:
            if hasattr(self, '_focused_widget') and self._focused_widget:
                self._focused_widget.dispatch('on_press')
                return True
        if key == 273:
            self._focus_widget(self._focus_index + 1)
            return True
        if key == 274:
            self._focus_widget(self._focus_index - 1)
            return True
        if key == 275 or key == 276:
            return True
        return False

    # ─── Color Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def status_color(status):
        return {
            'PENDIENTE': (0.5, 0.5, 0.5),
            'ACTIVO': (0.2, 1.0, 0.4),
            'INACTIVO': (1.0, 0.2, 0.2),
            'TIMEOUT': (1.0, 0.8, 0.0),
            'SIN RESPUESTA': (1.0, 0.4, 0.2),
            'ERROR CONEXION': (1.0, 0.2, 0.2),
            'FALLIDO': (0.8, 0.2, 0.2),
        }.get(status, (0.5, 0.5, 0.5))

    @staticmethod
    def status_color_hex(status):
        return {
            'PENDIENTE': '808080', 'ACTIVO': '33ff66', 'INACTIVO': 'ff3333',
            'TIMEOUT': 'ffcc00', 'SIN RESPUESTA': 'ff6633',
            'ERROR CONEXION': 'ff3333', 'FALLIDO': 'cc3333',
        }.get(status, '808080')

    @staticmethod
    def format_time(seconds):
        if seconds < 60:
            return f'{seconds:.0f}s'
        elif seconds < 3600:
            m, s = int(seconds // 60), int(seconds % 60)
            return f'{m}m {s}s'
        else:
            h, m = int(seconds // 3600), int((seconds % 3600) // 60)
            return f'{h}h {m}m'

    @staticmethod
    def close_popup(_=None):
        pass

    def _dismiss_popup(self, _=None):
        if self._popup:
            self._popup.dismiss()
            self._popup = None

    def _show_popup(self, title, message):
        content = BoxLayout(orientation='vertical', padding=12, spacing=10)
        content.add_widget(Label(text=message, font_size=15, size_hint_y=0.7, halign='left',
                                 text_size=(500, None), valign='top'))
        btn = Button(text='Cerrar', font_size=15, size_hint=(0.3, 0.25),
                     pos_hint={'center_x': 0.5}, background_color=(0.3, 0.3, 0.38, 1))
        content.add_widget(btn)
        popup = Popup(title=title, content=content, size_hint=(0.7, 0.45), auto_dismiss=False,
                      background_color=(0.12, 0.12, 0.16, 1))
        btn.bind(on_press=popup.dismiss)
        popup.open()

    def _show_popup_with_content(self, title, content_widget, size=(0.75, 0.65)):
        popup = Popup(title=title, content=content_widget, size_hint=size, auto_dismiss=False,
                      background_color=(0.12, 0.12, 0.16, 1))
        content_widget.popup_ref = popup
        self._popup = popup
        popup.open()

    # ═══════════════════════════════════════════════════════════════════════════
    #  INTERFAZ 1 — PLAYLIST MANAGER
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_playlist_manager(self):
        screen = Screen(name='playlist_manager')
        root = BoxLayout(orientation='vertical', padding=10, spacing=8)

        # Header
        header = BoxLayout(size_hint_y=None, height=56, spacing=10)
        header.add_widget(Label(
            text='[color=33ccff][b]IPTV FINDER[/b][/color]',
            markup=True, font_size=28, size_hint_x=0.6, halign='left',
            text_size=(400, None)
        ))
        header.add_widget(Label(
            text='[color=606060]v2.0 — Gestion de Listas[/color]',
            markup=True, font_size=14, size_hint_x=0.4, halign='right',
            text_size=(300, None)
        ))
        root.add_widget(header)

        # Divider
        from kivy.uix.widget import Widget as DividerWidget
        div = DividerWidget(size_hint_y=None, height=2)
        with div.canvas:
            Color(0.2, 0.5, 0.8, 1)
            Rectangle(pos=div.pos, size=div.size)
        div.bind(pos=lambda inst, val: _redraw_div(inst), size=lambda inst, val: _redraw_div(inst))
        root.add_widget(div)

        # Playlist list (scrollable)
        self.pm_scroll = ScrollView(size_hint_y=1)
        self.pm_list = BoxLayout(orientation='vertical', size_hint_y=None, spacing=6, padding=[0, 6])
        self.pm_list.bind(minimum_height=self.pm_list.setter('height'))
        self.pm_scroll.add_widget(self.pm_list)
        root.add_widget(self.pm_scroll)
        self._refresh_playlist_list()

        # Bottom buttons
        bottom = BoxLayout(size_hint_y=None, height=60, spacing=10, padding=[0, 6, 0, 0])

        add_btn = Button(
            text='[b]+ AGREGAR LISTA[/b]', markup=True, font_size=17,
            background_color=(0.15, 0.55, 0.35, 1), color=(1, 1, 1, 1)
        )
        add_btn.bind(on_press=self._show_add_dialog)
        bottom.add_widget(add_btn)

        web_btn = Button(
            text='[b]BUSCAR WEB[/b]', markup=True, font_size=17,
            background_color=(0.55, 0.25, 0.75, 1), color=(1, 1, 1, 1)
        )
        web_btn.bind(on_press=self._web_search)
        bottom.add_widget(web_btn)

        sources_btn = Button(
            text='[b]FUENTES[/b]', markup=True, font_size=17,
            background_color=(0.75, 0.25, 0.4, 1), color=(1, 1, 1, 1)
        )
        sources_btn.bind(on_press=self._show_sources)
        bottom.add_widget(sources_btn)

        root.add_widget(bottom)
        screen.add_widget(root)
        return screen

    def _refresh_playlist_list(self):
        self.pm_list.clear_widgets()
        if not self.playlists:
            self.pm_list.add_widget(Label(
                text='[color=505050]No hay listas guardadas.\nPresione + AGREGAR LISTA para comenzar.[/color]',
                markup=True, font_size=16, size_hint_y=None, height=100
            ))
            return

        for pl in self.playlists:
            row = BoxLayout(
                size_hint_y=None, height=80,
                padding=[10, 6], spacing=8
            )

            # Info column
            info = BoxLayout(orientation='vertical', size_hint_x=0.45, spacing=2)
            info.add_widget(Label(
                text=f'[b]{pl["name"]}[/b]', markup=True, font_size=16,
                halign='left', text_size=(350, None), valign='bottom',
                size_hint_y=0.5
            ))
            ch_count = len(pl.get('channels', []))
            url_short = pl['url'][:50] + ('...' if len(pl['url']) > 50 else '')
            info.add_widget(Label(
                text=f'[color=707070]{ch_count} canales  |  {url_short}[/color]',
                markup=True, font_size=11, halign='left',
                text_size=(350, None), valign='top', size_hint_y=0.5
            ))
            row.add_widget(info)

            # Buttons
            btn_specs = [
                ('CARGAR', (0.15, 0.5, 0.85, 1), lambda i, pid=pl['id']: self._load_playlist_for_viewing(pid)),
                ('EDITAR', (0.35, 0.35, 0.42, 1), lambda i, pid=pl['id']: self._show_edit_dialog(pid)),
                ('BORRAR', (0.7, 0.2, 0.2, 1), lambda i, pid=pl['id']: self._show_delete_confirm(pid)),
            ]
            for text, color, handler in btn_specs:
                btn = Button(
                    text=f'[b]{text}[/b]', markup=True, font_size=12,
                    size_hint_x=0.17, background_color=color, color=(1, 1, 1, 1)
                )
                btn.bind(on_press=handler)
                row.add_widget(btn)

            self.pm_list.add_widget(row)

    # ─── Add / Edit / Delete dialogs ─────────────────────────────────────────

    def _show_add_dialog(self, _=None):
        self._editing_id = None
        content = BoxLayout(orientation='vertical', padding=14, spacing=10)

        content.add_widget(Label(
            text='[b]Nombre de la lista:[/b]', markup=True, font_size=14,
            size_hint_y=None, height=22, halign='left', text_size=(400, None)
        ))
        self._dlg_name = TextInput(
            hint_text='Mi Lista IPTV', font_size=15, multiline=False,
            size_hint_y=None, height=40,
            background_color=(0.2, 0.2, 0.25, 1), foreground_color=(1, 1, 1, 1),
            hint_text_color=(0.5, 0.5, 0.5, 1)
        )
        content.add_widget(self._dlg_name)

        content.add_widget(Label(
            text='[b]URL o ruta del archivo M3U:[/b]', markup=True, font_size=14,
            size_hint_y=None, height=22, halign='left', text_size=(400, None)
        ))
        url_row = BoxLayout(size_hint_y=None, height=40, spacing=6)
        self._dlg_url = TextInput(
            hint_text='https://ejemplo.com/playlist.m3u', font_size=15, multiline=False,
            size_hint_x=0.7,
            background_color=(0.2, 0.2, 0.25, 1), foreground_color=(1, 1, 1, 1),
            hint_text_color=(0.5, 0.5, 0.5, 1)
        )
        url_row.add_widget(self._dlg_url)
        explore_btn = Button(
            text='[b]EXPLORAR[/b]', markup=True, font_size=13,
            size_hint_x=0.3, background_color=(0.3, 0.45, 0.65, 1), color=(1, 1, 1, 1)
        )
        explore_btn.bind(on_press=self._browse_file)
        url_row.add_widget(explore_btn)
        content.add_widget(url_row)

        btns = BoxLayout(size_hint_y=None, height=48, spacing=10, padding=[0, 8, 0, 0])
        cancel = Button(text='Cancelar', font_size=15, background_color=(0.35, 0.35, 0.4, 1))
        cancel.bind(on_press=self._dismiss_popup)
        btns.add_widget(cancel)
        save = Button(text='[b]GUARDAR[/b]', markup=True, font_size=15,
                      background_color=(0.15, 0.55, 0.35, 1))
        save.bind(on_press=self._handle_save_playlist)
        btns.add_widget(save)
        content.add_widget(btns)

        self._show_popup_with_content('Nueva Lista', content)

    def _show_edit_dialog(self, playlist_id):
        pl = next((p for p in self.playlists if p['id'] == playlist_id), None)
        if not pl:
            return
        self._editing_id = playlist_id

        content = BoxLayout(orientation='vertical', padding=14, spacing=10)

        content.add_widget(Label(
            text='[b]Nombre de la lista:[/b]', markup=True, font_size=14,
            size_hint_y=None, height=22, halign='left', text_size=(400, None)
        ))
        self._dlg_name = TextInput(
            text=pl['name'], font_size=15, multiline=False,
            size_hint_y=None, height=40,
            background_color=(0.2, 0.2, 0.25, 1), foreground_color=(1, 1, 1, 1),
            hint_text_color=(0.5, 0.5, 0.5, 1)
        )
        content.add_widget(self._dlg_name)

        content.add_widget(Label(
            text='[b]URL o ruta del archivo M3U:[/b]', markup=True, font_size=14,
            size_hint_y=None, height=22, halign='left', text_size=(400, None)
        ))
        url_row2 = BoxLayout(size_hint_y=None, height=40, spacing=6)
        self._dlg_url = TextInput(
            text=pl['url'], font_size=15, multiline=False,
            size_hint_x=0.7,
            background_color=(0.2, 0.2, 0.25, 1), foreground_color=(1, 1, 1, 1),
            hint_text_color=(0.5, 0.5, 0.5, 1)
        )
        url_row2.add_widget(self._dlg_url)
        explore_btn2 = Button(
            text='[b]EXPLORAR[/b]', markup=True, font_size=13,
            size_hint_x=0.3, background_color=(0.3, 0.45, 0.65, 1), color=(1, 1, 1, 1)
        )
        explore_btn2.bind(on_press=self._browse_file)
        url_row2.add_widget(explore_btn2)
        content.add_widget(url_row2)

        btns = BoxLayout(size_hint_y=None, height=48, spacing=10, padding=[0, 8, 0, 0])
        cancel = Button(text='Cancelar', font_size=15, background_color=(0.35, 0.35, 0.4, 1))
        cancel.bind(on_press=self._dismiss_popup)
        btns.add_widget(cancel)
        save = Button(text='[b]ACTUALIZAR[/b]', markup=True, font_size=15,
                      background_color=(0.15, 0.5, 0.85, 1))
        save.bind(on_press=self._handle_save_playlist)
        btns.add_widget(save)
        content.add_widget(btns)

        self._show_popup_with_content('Editar Lista', content)

    def _handle_save_playlist(self, _=None):
        name = self._dlg_name.text.strip()
        url = self._dlg_url.text.strip()
        if not name or not url:
            self._show_popup('Error', 'Complete todos los campos')
            return
        self._dismiss_popup()

        if self._editing_id:
            for pl in self.playlists:
                if pl['id'] == self._editing_id:
                    pl['name'] = name
                    pl['url'] = url
                    pl['channels'] = []
                    pl['updated'] = datetime.now().isoformat()
                    break
            save_playlists(self.playlists)
            self._refresh_playlist_list()
            self._load_channels_for_playlist(self._editing_id)
            self._editing_id = None
        else:
            new_pl = {
                'id': str(int(time.time() * 1000)),
                'name': name, 'url': url,
                'channels': [], 'created': datetime.now().isoformat(),
                'updated': datetime.now().isoformat(),
            }
            self.playlists.append(new_pl)
            save_playlists(self.playlists)
            self._refresh_playlist_list()
            self._load_channels_for_playlist(new_pl['id'])

    def _browse_file(self, _=None):
        if HAS_FILECHOOSER:
            try:
                filechooser.open_file(
                    on_selection=self._on_file_selected,
                    filters=[("M3U Files", "*.m3u", "*.m3u8"), ("Text Files", "*.txt"), ("All Files", "*")]
                )
            except Exception as e:
                self._show_popup('Error', f'No se pudo abrir el explorador:\n{str(e)[:60]}')
        else:
            self._show_popup('Info', 'Escriba la ruta del archivo M3U\nen el campo URL.')

    def _on_file_selected(self, selection):
        if selection and len(selection) > 0:
            self._dlg_url.text = selection[0]

    def _show_delete_confirm(self, playlist_id):
        pl = next((p for p in self.playlists if p['id'] == playlist_id), None)
        if not pl:
            return

        content = BoxLayout(orientation='vertical', padding=14, spacing=10)
        content.add_widget(Label(
            text=f'[color=ff6666]Eliminar la lista\n\n[b]{pl["name"]}[/b]\n\nEsta accion no se puede deshacer.[/color]',
            markup=True, font_size=15, halign='center', text_size=(400, None)
        ))

        btns = BoxLayout(size_hint_y=None, height=48, spacing=10, padding=[0, 8, 0, 0])
        cancel = Button(text='Cancelar', font_size=15, background_color=(0.35, 0.35, 0.4, 1))
        cancel.bind(on_press=self._dismiss_popup)
        btns.add_widget(cancel)
        delete = Button(text='[b]ELIMINAR[/b]', markup=True, font_size=15,
                        background_color=(0.75, 0.15, 0.15, 1))
        delete.bind(on_press=lambda i, pid=playlist_id: self._delete_playlist(pid))
        btns.add_widget(delete)
        content.add_widget(btns)

        self._show_popup_with_content('Confirmar Eliminacion', content, size=(0.6, 0.4))

    def _delete_playlist(self, playlist_id):
        self._dismiss_popup()
        self.playlists = [p for p in self.playlists if p['id'] != playlist_id]
        save_playlists(self.playlists)
        self._refresh_playlist_list()

    def _load_channels_for_playlist(self, playlist_id):
        pl = next((p for p in self.playlists if p['id'] == playlist_id), None)
        if not pl:
            return
        content = BoxLayout(orientation='vertical', padding=14, spacing=10)
        self._load_label = Label(
            text=f'Cargando canales de\n[b]{pl["name"]}[/b]...',
            markup=True, font_size=15, size_hint_y=0.4, halign='center'
        )
        content.add_widget(self._load_label)
        self._load_progress = ProgressBar(max=100, size_hint_y=0.2)
        content.add_widget(self._load_progress)
        self._load_pct = Label(text='0%', font_size=13, size_hint_y=0.2)
        content.add_widget(self._load_pct)
        self._load_popup = Popup(
            title='Cargando', content=content,
            size_hint=(0.6, 0.4), auto_dismiss=False,
            background_color=(0.12, 0.12, 0.16, 1)
        )
        self._load_popup.open()
        threading.Thread(target=self._load_channels_thread, args=(playlist_id,), daemon=True).start()

    def _update_load_popup(self, pct, msg):
        self._load_progress.value = pct
        self._load_pct.text = f'{pct:.0f}%'
        if msg:
            self._load_label.text = msg

    def _load_channels_thread(self, playlist_id):
        pl = next((p for p in self.playlists if p['id'] == playlist_id), None)
        if not pl:
            return
        try:
            url = pl['url']
            Clock.schedule_once(lambda dt: self._update_load_popup(
                10, f'Descargando playlist...'))
            if url.startswith('http'):
                resp = requests.get(url, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
                content = resp.text
            else:
                with open(url, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            Clock.schedule_once(lambda dt: self._update_load_popup(
                50, 'Parseando canales...'))
            channels = parse_m3u(content)
            Clock.schedule_once(lambda dt: self._update_load_popup(
                80, f'Guardando {len(channels)} canales...'))
            pl['channels'] = channels
            pl['updated'] = datetime.now().isoformat()
            save_playlists(self.playlists)
            Clock.schedule_once(lambda dt: self._update_load_popup(
                100, f'Listo: {len(channels)} canales'))
            Clock.schedule_once(lambda dt, ch=channels, pid=playlist_id:
                                self._on_channels_loaded(ch, pid), 0.5)
        except Exception as e:
            Clock.schedule_once(lambda dt, msg=str(e)[:80]:
                                self._on_channels_load_error(msg))

    def _on_channels_loaded(self, channels, playlist_id):
        if self._load_popup:
            self._load_popup.dismiss()
            self._load_popup = None
        self.current_playlist_id = playlist_id
        self._open_player(playlist_id)

    def _on_channels_load_error(self, msg):
        if self._load_popup:
            self._load_popup.dismiss()
            self._load_popup = None
        self._show_popup('Error', f'Error al cargar:\n{msg}')

    def _load_playlist_for_viewing(self, playlist_id):
        pl = next((p for p in self.playlists if p['id'] == playlist_id), None)
        if not pl:
            return
        if not pl.get('channels'):
            self._load_channels_for_playlist(playlist_id)
        else:
            self.current_playlist_id = playlist_id
            self._open_player(playlist_id)

    def _open_player(self, playlist_id):
        pl = next((p for p in self.playlists if p['id'] == playlist_id), None)
        if not pl:
            return
        self.channels = pl['channels']
        self.current_playlist_id = playlist_id
        self._build_groups()
        self._show_player_channels()
        self.player_title.text = f'[color=33ccff][b]{pl["name"]}[/b][/color]  [color=606060]({len(self.channels)} canales)[/color]'
        self.sm.current = 'player'
        Clock.schedule_once(self._collect_player_focusables, 0.3)

    def _collect_player_focusables(self, dt):
        self._focusable_widgets = []
        for child in self.sidebar_list.children:
            if isinstance(child, Button):
                child.focused = False
                self._focusable_widgets.append(child)
        for child in self.channel_grid.children:
            if isinstance(child, BoxLayout):
                for w in child.children:
                    if isinstance(w, Button):
                        w.focused = False
                        self._focusable_widgets.append(w)
        if self._focusable_widgets:
            self._focus_index = 0
            self._focus_widget(0)

    # ─── Fuentes / Web Search (Playlist Manager) ─────────────────────────────

    def _show_sources(self, _=None):
        content = BoxLayout(orientation='vertical', padding=10, spacing=8)

        header_lbl = Label(
            text='[color=33ccff][b]Fuentes IPTV Publicas[/b][/color]',
            markup=True, font_size=18, size_hint_y=None, height=36
        )
        content.add_widget(header_lbl)

        scroll = ScrollView(size_hint_y=0.8)
        btn_list = BoxLayout(orientation='vertical', size_hint_y=None, spacing=4)
        btn_list.bind(minimum_height=btn_list.setter('height'))
        for source in KNOWN_SOURCES:
            btn = Button(
                text=f'  {source["name"]}', font_size=14, size_hint_y=None, height=40,
                background_color=(0.15, 0.18, 0.25, 1), halign='left',
                text_size=(600, None), valign='middle'
            )
            btn.url = source["url"]
            btn.name = source["name"]
            btn.bind(on_press=self._on_source_selected)
            btn_list.add_widget(btn)
        scroll.add_widget(btn_list)
        content.add_widget(scroll)

        close_btn = Button(text='Cerrar', font_size=15, size_hint_y=0.12, size_hint_x=0.25,
                           pos_hint={'center_x': 0.5}, background_color=(0.35, 0.35, 0.4, 1))
        close_btn.bind(on_press=self._dismiss_popup)
        content.add_widget(close_btn)

        self._show_popup_with_content('Fuentes', content, size=(0.85, 0.8))

    def _on_source_selected(self, btn):
        self._dismiss_popup()
        url = btn.url
        name = btn.name

        new_pl = {
            'id': str(int(time.time() * 1000)),
            'name': name, 'url': url,
            'channels': [], 'created': datetime.now().isoformat(),
            'updated': datetime.now().isoformat(),
        }
        self.playlists.append(new_pl)
        save_playlists(self.playlists)
        self._refresh_playlist_list()
        self._load_channels_for_playlist(new_pl['id'])

    def _web_search(self, _=None):
        if not HAS_BS4:
            self._show_popup('Error', 'Instale beautifulsoup4:\npip install beautifulsoup4')
            return
        self._show_popup('Busqueda', 'Buscando listas en la web...')
        threading.Thread(target=self._web_search_thread, daemon=True).start()

    def _web_search_thread(self):
        try:
            results = search_iptv_web("iptv m3u playlist free", pages=2)
            m3u_links = []
            seen = set()
            for r in results:
                url = r["url"]
                if url in seen:
                    continue
                seen.add(url)
                if any(ext in url.lower() for ext in (".m3u", ".m3u8")):
                    m3u_links.append(url)

            for r in results[:8]:
                links = extract_m3u_links(r["url"], limit=3)
                for l in links:
                    if l not in seen:
                        seen.add(l)
                        m3u_links.append(l)

            if m3u_links:
                all_channels = []
                for link in m3u_links[:10]:
                    try:
                        resp = requests.get(link, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
                        if resp.status_code == 200 and ('#EXTM3U' in resp.text or '#EXTINF' in resp.text):
                            chs = parse_m3u(resp.text)
                            all_channels.extend(chs)
                    except Exception:
                        pass

                if all_channels:
                    new_pl = {
                        'id': str(int(time.time() * 1000)),
                        'name': f'Web Search ({len(all_channels)} canales)',
                        'url': m3u_links[0], 'channels': all_channels,
                        'created': datetime.now().isoformat(),
                        'updated': datetime.now().isoformat(),
                    }
                    self.playlists.append(new_pl)
                    save_playlists(self.playlists)
                    Clock.schedule_once(lambda dt: self._refresh_playlist_list())
                    Clock.schedule_once(lambda dt, c=len(all_channels), l=len(m3u_links):
                                        self._show_popup('Busqueda Web',
                                                         f'Se encontraron {c} canales desde {l} playlists'))
                else:
                    Clock.schedule_once(lambda dt: self._show_popup(
                        'Busqueda Web', 'No se pudieron cargar canales'))
            else:
                Clock.schedule_once(lambda dt: self._show_popup(
                    'Busqueda Web', 'No se encontraron links M3U.\nIntente con FUENTES para listas conocidas.'))
        except Exception as e:
            Clock.schedule_once(lambda dt, msg=str(e)[:80]:
                                self._show_popup('Error', f'Error en busqueda:\n{msg}'))

    # ═══════════════════════════════════════════════════════════════════════════
    #  INTERFAZ 2 — PLAYER / VISOR IPTV
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_player(self):
        screen = Screen(name='player')
        root = BoxLayout(orientation='vertical', padding=6, spacing=6)

        # ── Header ──
        header = BoxLayout(size_hint_y=None, height=50, spacing=10)
        back_btn = Button(
            text='[b]< ATRAS[/b]', markup=True, font_size=15,
            size_hint_x=0.12, background_color=(0.35, 0.35, 0.4, 1)
        )
        back_btn.bind(on_press=self._go_back)
        header.add_widget(back_btn)

        self.player_title = Label(
            text='[color=33ccff][b]IPTV Viewer[/b][/color]',
            markup=True, font_size=22, size_hint_x=0.4, halign='left',
            text_size=(400, None)
        )
        header.add_widget(self.player_title)

        # Search
        self.player_search = TextInput(
            hint_text='Buscar canal...', font_size=14, multiline=False,
            size_hint_x=0.22, height=36,
            background_color=(0.18, 0.18, 0.22, 1), foreground_color=(1, 1, 1, 1),
            hint_text_color=(0.45, 0.45, 0.45, 1)
        )
        self.player_search.bind(text=self._on_search_text)
        header.add_widget(self.player_search)

        self.player_status = Label(
            text='[color=606060]Listo[/color]', markup=True, font_size=13,
            size_hint_x=0.22
        )
        header.add_widget(self.player_status)
        root.add_widget(header)

        # ── Body (sidebar + channel list) ──
        body = BoxLayout(spacing=6)

        # Sidebar — categories
        sidebar_outer = BoxLayout(orientation='vertical', size_hint_x=0.18, spacing=4)
        sidebar_label = Label(
            text='[color=33ccff][b]CATEGORIAS[/b][/color]',
            markup=True, font_size=13, size_hint_y=None, height=26
        )
        sidebar_outer.add_widget(sidebar_label)

        self.sidebar_scroll = ScrollView(size_hint_y=1)
        self.sidebar_list = BoxLayout(orientation='vertical', size_hint_y=None, spacing=3)
        self.sidebar_list.bind(minimum_height=self.sidebar_list.setter('height'))
        self.sidebar_scroll.add_widget(self.sidebar_list)
        sidebar_outer.add_widget(self.sidebar_scroll)
        body.add_widget(sidebar_outer)

        # Channel list area
        channel_area = BoxLayout(orientation='vertical', spacing=4)

        # Channel list header
        ch_header = BoxLayout(size_hint_y=None, height=28, spacing=6, padding=[8, 0])
        for text, sx in [('Est', 0.05), ('Canal', 0.40), ('Grupo', 0.15),
                         ('Codec', 0.10), ('Latencia', 0.12), ('Estado', 0.18)]:
            ch_header.add_widget(Label(
                text=f'[b]{text}[/b]', markup=True, font_size=12,
                color=(0.5, 0.7, 1, 1), size_hint_x=sx, halign='left',
                text_size=(200, None)
            ))
        channel_area.add_widget(ch_header)

        self.channel_scroll = ScrollView(size_hint_y=1)
        self.channel_grid = GridLayout(cols=1, spacing=2, size_hint_y=None, padding=[4, 4])
        self.channel_grid.bind(minimum_height=self.channel_grid.setter('height'))
        self.channel_scroll.add_widget(self.channel_grid)
        channel_area.add_widget(self.channel_scroll)
        body.add_widget(channel_area)

        root.add_widget(body)

        # ── Stats bar ──
        stats = BoxLayout(size_hint_y=None, height=28, spacing=15, padding=[8, 2])
        self.p_total = Label(text='Total: 0', font_size=13, color=(0.6, 0.6, 0.6, 1))
        stats.add_widget(self.p_total)
        self.p_active = Label(text='Activos: 0', font_size=13, color=(0.2, 0.9, 0.4, 1))
        stats.add_widget(self.p_active)
        self.p_inactive = Label(text='Inactivos: 0', font_size=13, color=(0.9, 0.3, 0.3, 1))
        stats.add_widget(self.p_inactive)
        self.p_codecs = Label(text='Codecs: -', font_size=13, color=(0.5, 0.7, 1, 1))
        stats.add_widget(self.p_codecs)
        root.add_widget(stats)

        # ── Progress ──
        progress_row = BoxLayout(size_hint_y=None, height=24, spacing=6)
        self.p_progress = ProgressBar(max=100, size_hint_x=0.6)
        progress_row.add_widget(self.p_progress)
        self.p_pct = Label(text='0%', font_size=13, bold=True, size_hint_x=0.08)
        progress_row.add_widget(self.p_pct)
        self.p_eta = Label(text='', font_size=12, color=(0.6, 0.6, 0.6, 1), size_hint_x=0.32)
        progress_row.add_widget(self.p_eta)
        root.add_widget(progress_row)

        # ── Bottom buttons ──
        bottom = BoxLayout(size_hint_y=None, height=48, spacing=8, padding=[0, 4, 0, 0])

        test_btn = Button(
            text='[b]TESTEAR CANALES[/b]', markup=True, font_size=15,
            background_color=(0.2, 0.7, 0.35, 1)
        )
        test_btn.bind(on_press=self._start_testing)
        bottom.add_widget(test_btn)

        export_btn = Button(
            text='[b]EXPORTAR[/b]', markup=True, font_size=15,
            background_color=(0.85, 0.55, 0.15, 1)
        )
        export_btn.bind(on_press=self._export_from_player)
        bottom.add_widget(export_btn)

        refresh_btn = Button(
            text='[b]RECARGAR[/b]', markup=True, font_size=15,
            background_color=(0.2, 0.5, 0.8, 1)
        )
        refresh_btn.bind(on_press=self._reload_current)
        bottom.add_widget(refresh_btn)

        root.add_widget(bottom)
        screen.add_widget(root)
        return screen

    def _go_back(self, _=None):
        self.sm.current = 'playlist_manager'

    def _build_groups(self):
        groups = sorted(set(c['group'] for c in self.channels if c['group']))
        self.all_groups = groups
        self.group_counts = {}
        for g in groups:
            self.group_counts[g] = sum(1 for c in self.channels if c['group'] == g)
        self.group_counts['Todos'] = len(self.channels)
        self.current_group = 'Todos'
        self._refresh_sidebar()

    def _refresh_sidebar(self):
        self.sidebar_list.clear_widgets()
        groups = ['Todos'] + self.all_groups

        for group in groups:
            count = self.group_counts.get(group, 0)
            display_name = group if group != 'Todos' else 'TODOS'
            btn = Button(
                text=f'{display_name}\n[color=808080][size=11]{count} canales[/size][/color]',
                markup=True, font_size=13, size_hint_y=None, height=50,
                halign='left', text_size=(180, None), valign='middle',
                background_color=(0.12, 0.15, 0.22, 1),
                padding=[10, 0]
            )
            btn.group_name = group
            btn.bind(on_press=self._on_group_click)
            self.sidebar_list.add_widget(btn)

    def _on_group_click(self, btn):
        self.current_group = btn.group_name
        self._show_player_channels()

    def _show_player_channels(self):
        self.channel_grid.clear_widgets()

        if self.current_group == 'Todos':
            display = list(self.channels)
        else:
            display = [c for c in self.channels if c['group'] == self.current_group]

        search = self.player_search.text.strip().lower()
        if search:
            display = [c for c in display if search in c['name'].lower() or search in c.get('group', '').lower()]

        active_count = sum(1 for c in display if c['status'] == 'ACTIVO')
        self.p_total.text = f'Total: {len(display)}'
        self.p_active.text = f'Activos: {active_count}'
        self.p_inactive.text = f'Inactivos: {len(display) - active_count}'

        codecs_found = {}
        for c in display:
            if c['status'] == 'ACTIVO':
                codec = c.get('codec', '?')
                codecs_found[codec] = codecs_found.get(codec, 0) + 1
        self.p_codecs.text = f'Codecs: {", ".join(f"{k}:{v}" for k, v in sorted(codecs_found.items())) or "-"}'

        for ch in display:
            row = BoxLayout(size_hint_y=None, height=34, spacing=6, padding=[8, 1])

            sc = self.status_color_hex(ch['status'])
            row.add_widget(Label(text=f'[{ch["status"][0]}]', font_size=13, bold=True,
                                 color=(1, 1, 1, 1), size_hint_x=0.04))
            row.add_widget(Label(text=ch['name'][:40], font_size=12, color=(1, 1, 1, 1),
                                 size_hint_x=0.35, halign='left', text_size=(350, None)))
            row.add_widget(Label(text=ch['group'][:15] if ch['group'] else '-',
                                 font_size=11, color=(0.5, 0.5, 0.5, 1),
                                 size_hint_x=0.13, halign='left', text_size=(150, None)))
            row.add_widget(Label(text=ch.get('codec', '?'), font_size=11,
                                 color=(0.4, 0.7, 1, 1), size_hint_x=0.08))
            row.add_widget(Label(text=f'{ch.get("latency_ms", 0)}ms', font_size=11,
                                 color=(0.6, 0.6, 0.6, 1), size_hint_x=0.10))
            row.add_widget(Label(text=f'[color={sc}]{ch["status"]}[/color]', markup=True,
                                 font_size=11, size_hint_x=0.15))

            play_btn = Button(
                text='[b]▶[/b]', markup=True, font_size=14,
                size_hint_x=0.07, size_hint_y=None, height=30,
                background_color=(0.15, 0.5, 0.85, 1), color=(1, 1, 1, 1)
            )
            play_btn.bind(on_press=lambda inst, c=ch: self._play_channel(c))
            row.add_widget(play_btn)

            self.channel_grid.add_widget(row)

    def _on_search_text(self, instance, text):
        self._show_player_channels()

    def _play_channel(self, channel):
        content = BoxLayout(orientation='vertical', spacing=4)

        header = BoxLayout(size_hint_y=None, height=40, spacing=8, padding=[8, 4])
        header.add_widget(Label(
            text=f'[b]{channel["name"]}[/b]', markup=True, font_size=15,
            size_hint_x=0.6, halign='left', text_size=(500, None)
        ))
        close_btn = Button(
            text='[b]X  CERRAR[/b]', markup=True, font_size=14,
            size_hint_x=0.2, background_color=(0.7, 0.2, 0.2, 1),
            color=(1, 1, 1, 1)
        )
        header.add_widget(close_btn)
        content.add_widget(header)

        video = Video(
            source=channel['url'],
            state='play',
            size_hint_y=0.82,
            allow_stretch=True,
            keep_ratio=True,
        )
        content.add_widget(video)

        controls = BoxLayout(size_hint_y=None, height=36, spacing=8, padding=[8, 2])
        play_btn = Button(text='[b]▶ Pause[/b]', markup=True, font_size=13,
                          size_hint_x=0.2, background_color=(0.2, 0.5, 0.8, 1))

        def toggle_play(_):
            if video.state == 'play':
                video.state = 'pause'
                play_btn.text = '[b]▶ Play[/b]'
            else:
                video.state = 'play'
                play_btn.text = '[b]▶ Pause[/b]'

        play_btn.bind(on_press=toggle_play)
        controls.add_widget(play_btn)

        fs_btn = Button(text='[b]⛶ Full[/b]', markup=True, font_size=13,
                        size_hint_x=0.15, background_color=(0.3, 0.3, 0.4, 1))

        original_size = (0.92, 0.88)
        is_fullscreen = [False]

        def toggle_fullscreen(_):
            if is_fullscreen[0]:
                popup_ref[0].size_hint = original_size
                popup_ref[0].pos_hint = {}
                popup_ref[0].pos = (0, 0)
                fs_btn.text = '[b]⛶ Full[/b]'
                is_fullscreen[0] = False
            else:
                popup_ref[0].size_hint = (None, None)
                popup_ref[0].size = Window.size
                popup_ref[0].pos = (0, 0)
                fs_btn.text = '[b]⛶ Normal[/b]'
                is_fullscreen[0] = True

        fs_btn.bind(on_press=toggle_fullscreen)
        controls.add_widget(fs_btn)

        controls.add_widget(Label(text='', size_hint_x=0.3))
        controls.add_widget(Label(
            text=f'Codec: {channel.get("codec", "?")} | {channel.get("latency_ms", 0)}ms',
            font_size=11, color=(0.6, 0.6, 0.6, 1), size_hint_x=0.2
        ))
        content.add_widget(controls)

        popup_ref = [None]

        def do_close(_):
            video.state = 'stop'
            if popup_ref[0]:
                popup_ref[0].dismiss()

        popup = Popup(
            title=f'Reproduciendo: {channel["name"][:40]}',
            content=content,
            size_hint=(0.92, 0.88),
            auto_dismiss=False,
            background_color=(0.05, 0.05, 0.08, 1)
        )
        popup_ref[0] = popup

        close_btn.bind(on_release=do_close)
        popup.open()

    def _reload_current(self, _=None):
        if self.current_playlist_id:
            self._load_channels_for_playlist(self.current_playlist_id)

    # ─── Testing (from Player) ────────────────────────────────────────────────

    def _start_testing(self, _=None):
        if not self.channels:
            self._show_popup('Error', 'No hay canales para testear')
            return
        if self.testing:
            return

        self.testing = True
        self._test_start_time = time.time()
        self._show_player_channels()

        threading.Thread(target=self._test_channels_thread, daemon=True).start()

    def _test_channels_thread(self):
        self.active_channels = []
        tested = [0]
        total = len(self.channels)
        timeout = 5
        retries = 2
        threads = 20

        def test_one(ch):
            return test_channel(ch, timeout=timeout, retries=retries)

        with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
            futures = {executor.submit(test_one, ch): ch for ch in self.channels}
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                tested[0] += 1
                if result['status'] == 'ACTIVO':
                    self.active_channels.append(result)

                idx = next(i for i, ch in enumerate(self.channels)
                           if ch['url'] == result['url'])
                self.channels[idx] = result

                p = (tested[0] / total) * 100
                elapsed = time.time() - self._test_start_time
                avg_per = elapsed / tested[0] if tested[0] > 0 else 0
                remaining = avg_per * (total - tested[0])
                eta_str = self.format_time(remaining)

                if tested[0] % 5 == 0 or tested[0] == total:
                    Clock.schedule_once(lambda dt, pp=p, tt=tested[0], ee=eta_str:
                                        self._update_player_progress(pp, tt, total, ee))

        total_time = self.format_time(time.time() - self._test_start_time)
        Clock.schedule_once(lambda dt, tt=total_time, n=len(self.active_channels), t=total:
                            self._finish_player_testing(tt, n, t))

    def _update_player_progress(self, progress, tested, total, eta):
        self.p_progress.value = progress
        self.p_pct.text = f'{progress:.0f}%'
        self.p_eta.text = f'{tested}/{total} — Quedan {eta}'
        self.player_status.text = f'[color=ffcc00]{tested}/{total} | {progress:.0f}%[/color]'

    def _finish_player_testing(self, total_time, active_count, total):
        self.testing = False
        self.p_progress.value = 100
        self.p_pct.text = '100%'
        self.p_eta.text = f'Completado en {total_time}'
        self.player_status.text = f'[color=33ff66]Activos: {active_count}/{total} | {total_time}[/color]'

        # Update playlist channels cache
        if self.current_playlist_id:
            pl = next((p for p in self.playlists if p['id'] == self.current_playlist_id), None)
            if pl:
                pl['channels'] = list(self.channels)
                save_playlists(self.playlists)

        self._show_player_channels()

    # ─── Export (from Player) ─────────────────────────────────────────────────

    def _export_from_player(self, _=None):
        active = [c for c in self.channels if c['status'] == 'ACTIVO']
        if not active:
            self._show_popup('Exportar', 'No hay canales activos para exportar')
            return

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        data_dir = os.path.join(os.path.expanduser('~'), 'IPTV_Exports')
        os.makedirs(data_dir, exist_ok=True)

        m3u_path = os.path.join(data_dir, f'iptv_activos_{ts}.m3u')
        with open(m3u_path, 'w', encoding='utf-8') as f:
            f.write('#EXTM3U\n')
            for ch in active:
                logo = f' tvg-logo="{ch["logo"]}"' if ch.get('logo') else ''
                group = f' group-title="{ch["group"]}"' if ch.get('group') else ''
                f.write(f'#EXTINF:-1{logo}{group},{ch["name"]}\n')
                f.write(f'{ch["url"]}\n')

        txt_path = os.path.join(data_dir, f'iptv_activos_{ts}.txt')
        with open(txt_path, 'w', encoding='utf-8') as f:
            for ch in active:
                f.write(f'{ch["name"]} | {ch.get("codec", "?")} | '
                        f'{ch.get("latency_ms", 0)}ms | {ch["url"]}\n')

        self._show_popup('Exportar',
                         f'Guardados en IPTV_Exports/\n\n'
                         f'{os.path.basename(m3u_path)}\n{os.path.basename(txt_path)}\n\n'
                         f'{len(active)} canales activos')


if __name__ == '__main__':
    IPTVFinderApp().run()
