class TranscriptorTelo:
    """Classe base abstracta per als transcriptors de TELÓ."""

    def __init__(self, config=None):
        self.config = config or {}
        self.is_listening = False

        # Flag unificat per a tots els motors
        gen_cfg = self.config.get("general", {})
        self.show_info = gen_cfg.get("show_info", self.config.get("show_info", False))

    def inicialitzar_motor(self):
        raise NotImplementedError

    def iniciar_escolta(self, callback_resultat):
        raise NotImplementedError

    def aturar_escolta(self):
        self.is_listening = False
