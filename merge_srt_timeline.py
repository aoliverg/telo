#!/usr/bin/env python3
"""
merge_srt_timeline.py
---------------------
Fusiona arxius SRT en diferents idiomes combinant tots els intervals temporals.
Si un idioma té dos subtítols en el temps que un altre en té un (o viceversa),
es generen els talls corresponents i, si un idioma segueix actiu o no canvia,
es manté el text corresponent a aquell moment (o es repeteix l'anterior).

Ús:
    python3 merge_srt_timeline.py -o sortida_multi.srt FRA=frances.srt CAT=catala.srt SPA=castella.srt
"""

import sys
import re
import argparse

def srt_time_to_ms(t_str):
    t_str = t_str.strip().replace('.', ',')
    parts = t_str.split(':')
    if len(parts) != 3:
        return 0
    h = int(parts[0])
    m = int(parts[1])
    s, ms = parts[2].split(',')
    return (h * 3600 + m * 60 + int(s)) * 1000 + int(ms)

def ms_to_srt_time(ms):
    if ms < 0:
        ms = 0
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def parse_single_srt(path):
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read().replace('\r\n', '\n').replace('\r', '\n')

    blocks = re.split(r'\n\s*\n', content.strip())
    subtitles = []

    for block in blocks:
        lines = [l.strip() for l in block.split('\n') if l.strip()]
        if len(lines) >= 2:
            sub_id = lines[0]
            time_idx = 1
            if '-->' not in lines[time_idx] and len(lines) > 2 and '-->' in lines[2]:
                time_idx = 2
            
            if '-->' in lines[time_idx]:
                time_str = lines[time_idx]
                times = time_str.split('-->')
                start_ms = srt_time_to_ms(times[0])
                end_ms = srt_time_to_ms(times[1])

                text_lines = lines[time_idx + 1:]
                clean_text = "\n".join(text_lines)
                subtitles.append({
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "text": clean_text
                })
    return subtitles

def get_text_at(subs, start, end, last_known_text=""):
    """
    Retorna el text que correspon a l'interval [start, end].
    Si hi ha un subtítol actiu en aquest interval, el retorna.
    Si cap subtítol està actiu directament però l'interval cau dins d'una frase vigent,
    o volem mantenir el text previ si encara no s'ha esvaït, s'utilitza.
    """
    candidates = []
    for s in subs:
        overlap = max(0, min(end, s["end_ms"]) - max(start, s["start_ms"]))
        if overlap > 0:
            candidates.append((overlap, s["text"]))
    
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    
    # Si no hi ha solapament directe, busquem si estem en una petita transició
    # o si l'usuari vol mantenir l'anterior:
    return ""

def merge_srt_timeline(files_dict, output_path, min_duration_ms=400):
    parsed = {lang: parse_single_srt(path) for lang, path in files_dict.items()}
    langs = list(files_dict.keys())

    # 1. Recollir tots els punts temporals d'inici i final
    # Per evitar micro-intervals (p. ex. diferències de 50ms entre fitxers),
    # agrupem els punts que estiguin a menys de 150ms.
    raw_points = set()
    for lang, subs in parsed.items():
        for s in subs:
            raw_points.add(s["start_ms"])
            raw_points.add(s["end_ms"])

    sorted_raw = sorted(list(raw_points))
    if not sorted_raw:
        print("No s'han trobat subtítols.")
        return

    # Agrupació de talls propers per evitar parpelleigs
    clean_points = [sorted_raw[0]]
    for pt in sorted_raw[1:]:
        if pt - clean_points[-1] >= 120:
            clean_points.append(pt)

    # 2. Construir intervals
    intervals = []
    for i in range(len(clean_points) - 1):
        t_start = clean_points[i]
        t_end = clean_points[i + 1]
        if t_end - t_start >= min_duration_ms or (i == len(clean_points) - 2):
            intervals.append((t_start, t_end))

    # 3. Mapejar textos per a cada interval
    blocks = []
    last_text = {lang: "" for lang in langs}

    # Per saber quan un idioma ha acabat formalment el seu subtítol i no arrossegar-lo eternament
    def is_lang_active_at(subs, t_mid):
        for s in subs:
            if s["start_ms"] <= t_mid <= s["end_ms"]:
                return s["text"]
        return None

    # Mètode alternatiu més pràctic i fidel a la proposta:
    # Fem servir els blocs basats en esdeveniments on ALMENYS una llengua tingui text nou o actiu.
    sub_count = 1
    
    # Anem a recórrer cada interval i determinar el text de cada llengua
    current_block = None

    for t_start, t_end in intervals:
        t_mid = (t_start + t_end) // 2
        
        interval_texts = {}
        has_any_text = False

        for lang in langs:
            active_t = is_lang_active_at(parsed[lang], t_mid)
            if active_t is not None:
                interval_texts[lang] = active_t
                last_text[lang] = active_t
                has_any_text = True
            else:
                # Comprova si algun altre idioma té text actiu;
                # si un altre idioma té text, però aquest ha callat,
                # comprovem si l'últim subtítol d'aquest idioma ha acabat fa molt poc (< 1.5s)
                # per mantenir-lo (repetir l'anterior) mentre dura la frase de l'altre
                interval_texts[lang] = ""

        # Si almenys un idioma està parlant:
        if has_any_text:
            # Per als idiomes que no tenen text en aquest bocí exacte,
            # però estan dins d'un bloc compartit, decidim si repetir l'anterior
            for lang in langs:
                if not interval_texts[lang]:
                    # Buscar si l'últim subtítol d'aquest idioma cobria la frase
                    # Si volem repetir l'anterior mentre l'altre parla:
                    pass

            # Si aquest interval té exactament el mateix text que l'anterior, allarguem l'anterior
            if current_block and all(current_block["texts"][lang] == interval_texts[lang] for lang in langs):
                current_block["end_ms"] = t_end
            else:
                if current_block:
                    blocks.append(current_block)
                current_block = {
                    "start_ms": t_start,
                    "end_ms": t_end,
                    "texts": interval_texts
                }
        else:
            if current_block:
                blocks.append(current_block)
                current_block = None

    if current_block:
        blocks.append(current_block)

    # 4. Fussionar els que siguin un sol subtítol que s'ha dividit quan un altre idioma en tenia dos:
    # Ex: FRA té frase llarga. CAT en té dues (A i B).
    # Resultat:
    # Bloc 1 (temps A): FRA=frase llarga, CAT=A, SPA=...
    # Bloc 2 (temps B): FRA=frase llarga (repetida!), CAT=B, SPA=...
    # Aquesta és exactament la repetició desitjada!
    
    # Ara apliquem la regla de repetir l'anterior per als idiomes buits si la frase de referència segueix:
    for b in blocks:
        # Si un idioma ha quedat buit però estava parlant just abans o en el context:
        for lang in langs:
            if not b["texts"][lang]:
                # Cerquem el subtítol d'aquest idioma més proper
                # que estigui a menys de 1 segon
                for s in parsed[lang]:
                    if abs(s["start_ms"] - b["start_ms"]) <= 800 or (s["start_ms"] <= b["start_ms"] and b["end_ms"] <= s["end_ms"] + 800):
                        b["texts"][lang] = s["text"]
                        break

    # 5. Escriptura de l'SRT final
    final_srt_blocks = []
    idx = 1
    for b in blocks:
        # Assegurar que hi ha text en almenys un idioma
        if not any(b["texts"][l].strip() for l in langs):
            continue
        
        time_line = f"{ms_to_srt_time(b['start_ms'])} --> {ms_to_srt_time(b['end_ms'])}"
        lines = [str(idx), time_line]
        for lang in langs:
            t = b["texts"][lang].strip()
            lines.append(f"[{lang}] {t}")
        
        final_srt_blocks.append("\n".join(lines))
        idx += 1

    content = "\n\n".join(final_srt_blocks) + "\n"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"✅ S'ha generat correctament: {output_path} ({len(final_srt_blocks)} subtítols generats)")

