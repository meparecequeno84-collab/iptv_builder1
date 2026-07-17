[app]

title = IPTV Finder
package.name = iptvfinder
package.domain = org.iptvfinder

source.dir = .
source.include_exts = py,png,jpg,kv,atlas
source.include_patterns = assets/*

version = 2.1

requirements = python3,kivy,certifi,charset-normalizer,cffi,urllib3,requests,idna,packaging,beautifulsoup4,soupsieve,plyer

orientation = landscape

fullscreen = 0

android.permissions = INTERNET,ACCESS_NETWORK_STATE,BLUETOOTH,BLUETOOTH_CONNECT

android.api = 28
android.minapi = 21
android.ndk = 28c
android.accept_sdk_license = True

android.archs = armeabi-v7a

android.log_level = 2

android.preserve_data = 1

# Android TV / TV Box support
android.leanback = 1
android.add_activity = android:name=org.kivy.android.PythonActivity,android:configChanges=keyboard|keyboardHidden|orientation|screenSize

