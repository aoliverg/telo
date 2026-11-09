"""
sincronitzador.py
Mòdul de concordança en temps real entre transcripció de veu (ASR)
i les línies del guió oficial en múltiples idiomes.
"""

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None


class Sincronitzador:
    def __init__(
        self,
        score_threshold=72,
        no_offset_threshold=58,
        offset_penalty=2,
        base_window=4,
        max_window=15,
        desync_threshold=2,
        config=None,
    ):
        """
        Permet passar valors individuals o extreure'ls d'un diccionari 'config'.
        """
        cfg = config or {}

        self.score_threshold = cfg.get("score_threshold", score_threshold)
        self.no_offset_threshold = cfg.get("no_offset_threshold", no_offset_threshold)
        self.offset_penalty = cfg.get("offset_penalty", offset_penalty)
        self.base_window = cfg.get("base_window", base_window)
        self.max_window = cfg.get("max_window", max_window)
        self.desync_threshold = cfg.get("desync_threshold", desync_threshold)

        self.current_window = self.base_window
        self.desync_count = 0
        self.previous_eval = ""

    def reiniciar_a_index(self, index=0):
        """Reinicia la finestra i comptadors quan l'operador salta manualment."""
        self.current_window = self.base_window
        self.desync_count = 0
        self.previous_eval = ""

    def avaluar(self, text_asr, current_index, subtitles, lang_guia="CAT"):
        """
        Compara el text de veu (ASR) amb les frases del guió dins la finestra activa.

        Retorna:
            (status, payload, nou_index, score)
            - status: 'MATCH' o 'FALLBACK'
            - payload: diccionari del subtítol coincident o text pla
            - nou_index: índex de la línia seleccionada
            - score: percentatge de similitud calculat
        """
        if fuzz is None or not text_asr:
            return ("FALLBACK", text_asr, current_index, 0)

        eval_text = text_asr.strip()
        if len(eval_text.split()) < 2:
            return ("FALLBACK", text_asr, current_index, 0)

        total_subs = len(subtitles)
        if current_index < 0:
            current_index = 0
        if current_index >= total_subs:
            return ("FALLBACK", text_asr, current_index, 0)

        limit = min(current_index + self.current_window, total_subs)
        candidats = [
            subtitles[i]["texts"].get(lang_guia, "") for i in range(current_index, limit)
        ]

        millor_score = 0
        millor_offset = -1
        eval_lower = eval_text.lower()

        for offset, target_line in enumerate(candidats):
            anchor = " ".join(target_line.lower().split())
            if not anchor:
                continue

            score = fuzz.partial_ratio(anchor, eval_lower)

            if offset == 0:
                dynamic_threshold = self.no_offset_threshold
            else:
                dynamic_threshold = self.score_threshold + (offset * self.offset_penalty)
                if dynamic_threshold > 90:
                    dynamic_threshold = 90

            if score >= dynamic_threshold and score > millor_score:
                millor_score = score
                millor_offset = offset

        if millor_offset != -1:
            nou_index = current_index + millor_offset
            self.current_window = self.base_window
            self.desync_count = 0
            self.previous_eval = eval_text
            return ("MATCH", subtitles[nou_index], nou_index, millor_score)
        else:
            self.desync_count += 1
            if self.desync_count >= self.desync_threshold:
                self.current_window = min(self.current_window + 3, self.max_window)
            return ("FALLBACK", text_asr, current_index, 0)
