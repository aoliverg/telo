# src/i18n_manager.py
import os

class I18nManager:
    def __init__(self, modul="manual", lang="ca", base_dir=None):
        if base_dir and os.path.isdir(base_dir):
            self.base_dir = os.path.abspath(base_dir)
        else:
            # Si no s'especifica ruta, busca la carpeta i18n a l'arrel del projecte
            dir_pare = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.base_dir = os.path.join(dir_pare, "i18n")

        self.modul = modul
        self.lang = str(lang).lower().strip()
        self.translations = {}  # Inicialitzem sempre el diccionari
        self.carregar_traduccions()

    def carregar_traduccions(self):
        fitxer = os.path.join(self.base_dir, f"{self.modul}_{self.lang}.properties")
        
        # Fallback si no troba el fitxer de l'idioma sol·licitat
        if not os.path.isfile(fitxer):
            fitxer = os.path.join(self.base_dir, f"{self.modul}_ca.properties")

        if os.path.isfile(fitxer):
            try:
                with open(fitxer, 'r', encoding='utf-8') as f:
                    for line in f:
                        linea = line.strip()
                        # Ignorem línies buides o comentaris que comencin amb # o !
                        if linea and not linea.startswith('#') and not linea.startswith('!') and '=' in linea:
                            k, v = linea.split('=', 1)
                            self.translations[k.strip()] = v.strip().replace(r'\n', '\n')
            except Exception as e:
                print(f"⚠️ [i18n] Error llegint {fitxer}: {e}")
        else:
            print(f"⚠️ [i18n] No s'ha trobat cap fitxer de traducció a: {fitxer}")

    def t(self, key, default=None, **kwargs):
        """Retorna la traducció de la clau o el valor per defecte."""
        text = self.translations.get(key, default if default is not None else key)
        if kwargs:
            try:
                text = text.format(**kwargs)
            except Exception:
                pass
        return text
