import yaml


class TranscriptorTelo:
    """Classe base abstracta per als transcriptors de TELÓ."""

    def __init__(self, config_path="configTranscriptorTelo.yaml"):
        self.config_path = config_path
        self.config = self._carregar_configuracio()
        self.is_listening = False

        # Flag unificat per a tots els motors
        gen_cfg = self.config.get("general", {})
        self.show_info = gen_cfg.get("show_info", False)

    def _carregar_configuracio(self):
        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def inicialitzar_motor(self):
        raise NotImplementedError

    def iniciar_escolta(self, callback_resultat):
        raise NotImplementedError

    def aturar_escolta(self):
        self.is_listening = False
