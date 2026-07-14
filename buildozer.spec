[app]

title = IPTV Finder
package.name = iptvfinder
package.domain = org.iptvfinder

source.dir = .
source.include_exts = py,png,jpg,kv,atlas
source.include_patterns = assets/*

version = 2.0

requirements = python3,kivy,certifi,charset-normalizer,cffi,urllib3,requests,idna,packaging,beautifulsoup4,soupsieve

orientation = landscape

fullscreen = 0

android.permissions = INTERNET,ACCESS_NETWORK_STATE

android.api = 33
android.minapi = 24
android.ndk = 26b
android.accept_sdk_license = True

android.archs = arm64-v8a

android.log_level = 2
