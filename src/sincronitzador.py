"""
sincronitzador.py
Mòdul de concordança en temps real optimitzat per a subtítols de teatre,
amb buffer rodant d'ASR, tolerància de retrocés configurable (back_window)
i filtratge temporal.
"""

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None


class Sincronitzador:
    def __init__(
        self,
        score_threshold=55,
        no_offset_threshold=48,
        offset_penalty=1.5,
        base_window=10,
        max_window=25,
        back_window=1,
        desync_threshold=2,
        max_time_distance_ms=25000,
        time_penalty_per_sec=0.8,
        max_buffer_words=25,
        config=None,
    ):
        cfg = config or {}

        self.score_threshold = cfg.get("score_threshold", score_threshold)
        self.no_offset_threshold = cfg.get("no_offset_threshold", no_offset_threshold)
        self.offset_penalty = cfg.get("offset_penalty", offset_penalty)
        self.base_window = cfg.get("base_window", base_window)
        self.max_window = cfg.get("max_window", max_window)
        self.back_window = cfg.get("back_window", back_window)
        self.desync_threshold = cfg.get("desync_threshold", desync_threshold)

        self.max_time_distance_ms = cfg.get("max_time_distance_ms", max_time_distance_ms)
        self.time_penalty_per_sec = cfg.get("time_penalty_per_sec", time_penalty_per_sec)
        self.max_buffer_words = cfg.get("max_buffer_words", max_buffer_words)

        self.current_window = self.base_window
        self.desync_count = 0
        self.asr_word_buffer = []

    def reiniciar_a_index(self, index=0):
        """Reinicia la finestra de cerca, comptadors i buida el buffer d'ASR."""
        self.current_window = self.base_window
        self.desync_count = 0
        if hasattr(self, "asr_word_buffer"):
            self.asr_word_buffer.clear()

    def reiniciar(self, index=0):
        """Àlies per compatibilitat amb versions prèvies."""
        self.reiniciar_a_index(index)

    def afegir_text_asr(self, text_nou):
        """Afegeix paraules al buffer rodant i manté el límit màxim."""
        if not text_nou:
            return
        paraules = text_nou.strip().split()
        if not paraules:
            return

        for p in paraules:
            self.asr_word_buffer.append(p)

        if len(self.asr_word_buffer) > self.max_buffer_words:
            self.asr_word_buffer = self.asr_word_buffer[-self.max_buffer_words:]

    def avaluar(self, text_asr, current_index, subtitles, lang_guia="CAT", current_playhead_ms=None):
        if fuzz is None or not text_asr:
            return ("FALLBACK", text_asr, current_index, 0)

        self.afegir_text_asr(text_asr)
        if not self.asr_word_buffer:
            return ("FALLBACK", text_asr, current_index, 0)

        total_subs = len(subtitles)
        if current_index < 0:
            current_index = 0
        if current_index >= total_subs:
            return ("FALLBACK", text_asr, current_index, 0)

        start_idx = max(0, current_index - self.back_window)
        limit = min(current_index + self.current_window, total_subs)

        ref_time_ms = (
            current_playhead_ms
            if current_playhead_ms is not None
            else subtitles[current_index].get("start_ms", 0)
        )

        millor_score = 0
        millor_idx = -1

        for idx in range(start_idx, limit):
            sub = subtitles[idx]
            offset = idx - current_index
            target_line = sub["texts"].get(lang_guia, "").strip()
            target_words = target_line.split()
            n_target = len(target_words)
            if n_target == 0:
                continue

            candidat_time_ms = sub.get("start_ms", 0)
            diff_ms = abs(candidat_time_ms - ref_time_ms)
            if diff_ms > self.max_time_distance_ms:
                continue

            finestra_mida = max(n_target, min(len(self.asr_word_buffer), n_target + 2))
            fragment_veu = " ".join(self.asr_word_buffer[-finestra_mida:]).lower()
            target_clean = " ".join(target_words).lower()

            raw_score = fuzz.ratio(target_clean, fragment_veu)
            if raw_score < 70:
                raw_score = max(raw_score, fuzz.token_sort_ratio(target_clean, fragment_veu))

            penalitzacio_pos = abs(offset) * self.offset_penalty
            penalitzacio_temps = (diff_ms / 1000.0) * self.time_penalty_per_sec
            score_final = raw_score - (penalitzacio_pos + penalitzacio_temps)

            if offset == 0:
                dynamic_threshold = self.no_offset_threshold
            elif offset < 0:
                dynamic_threshold = max(68, self.score_threshold + abs(offset) * 3.0)
            else:
                dynamic_threshold = min(85, self.score_threshold + (offset * 2.0))

            if score_final >= dynamic_threshold and score_final > millor_score:
                millor_score = score_final
                millor_idx = idx

        if millor_idx != -1:
            self.current_window = self.base_window
            self.desync_count = 0
            if millor_idx > current_index:
                self.asr_word_buffer.clear()
            return ("MATCH", subtitles[millor_idx], millor_idx, int(millor_score))
        else:
            self.desync_count += 1
            if self.desync_count >= self.desync_threshold:
                self.current_window = min(self.current_window + 2, self.max_window)
            return ("FALLBACK", text_asr, current_index, 0)
