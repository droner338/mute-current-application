# Mute Current Application

Zwei einstellbare globale Hotkeys steuern die Lautstärke des Programms im
gerade aktiven (fokussierten) Fenster – etwa um ein Spiel per Tastendruck
stummzuschalten.

- **Toggle-Mute-Taste** (Standard `F1`): schaltet Ton der aktiven Anwendung stumm/an
- **Fixwert-Taste** (Standard `F2`): wechselt die Lautstärke der aktiven Anwendung
  zwischen einem festen Prozentwert (Standard 20 %) und 100 %

Beide Tasten und der Prozentwert sind über das Tray-Icon → *Einstellungen...*
änderbar. Die Konfiguration liegt in `mute_config.json` neben der exe.

## Installation

Fertigen Installer von der [Releases-Seite](../../releases) herunterladen
und ausführen.

## Aus dem Quellcode bauen

```bash
pip install -r requirements.txt
python mute_app.py
```

Zu einer eigenständigen exe kompilieren:

```bash
pyinstaller --onefile --noconsole --uac-admin --icon icon.ico --name mute_current_application mute_app.py
```

## Hinweis zu Administratorrechten

Die Anwendung fordert beim Start Administratorrechte an. Das ist nötig,
damit die globalen Hotkeys auch funktionieren, wenn ein mit erhöhten
Rechten laufendes Spiel (z. B. wegen Anti-Cheat) im Vordergrund ist.
