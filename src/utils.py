def ms_to_time_str(ms):
    """Converteix mil·lisegons a format HH:MM:SS o MM:SS."""
    sign = ""
    if ms < 0:
        sign = "-"
        ms = abs(ms)
    
    seconds = int(ms // 1000)
    minutes = int(seconds // 60)
    hours = int(minutes // 60)
    
    sec_rem = seconds % 60
    min_rem = minutes % 60
    
    if hours > 0:
        return f"{sign}{hours:02d}:{min_rem:02d}:{sec_rem:02d}"
    else:
        return f"{sign}{min_rem:02d}:{sec_rem:02d}"


def srt_time_to_ms(time_str):
    """Converteix el temps en format SRT (00:00:00,000) a mil·lisegons."""
    try:
        time_str = time_str.replace(',', '.')
        parts = time_str.split(':')
        hours = int(parts[0])
        minutes = int(parts[1])
        sec_parts = parts[2].split('.')
        seconds = int(sec_parts[0])
        ms = int(sec_parts[1])
        return (hours * 3600 + minutes * 60 + seconds) * 1000 + ms
    except Exception:
        return 0
