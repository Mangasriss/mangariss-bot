import logging
import json
import os
import sys
import scraper
import storage

# --- CONFIG LOGS ---
if not os.path.exists('logs'): os.makedirs('logs')
sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/bot.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger()

# ⚠️ Cluster Backblaze (Vérifie bien que c'est f003 ou ton cluster actuel)
B2_CLUSTER = "f003" 

def load_config():
    with open('config.json', 'r') as f: return json.load(f)
def load_mangas():
    if not os.path.exists('mangas.txt'): return []
    with open('mangas.txt', 'r') as f: return [line.strip() for line in f if line.strip()]

# Filtre optionnel via variable d'environnement (ex: "One Piece" ou "One Piece, Naruto")
def normalize_name(name):
    if name is None: return ""
    n = str(name).strip()
    if n.startswith("@"): n = n[1:]
    n = n.strip().strip('"').strip("'")
    n = n.replace("’", "'")
    n = " ".join(n.split())
    return n.lower()

def parse_trigger_mangas():
    raw = os.getenv("TRIGGER_MANGA", "").strip()
    if not raw: return []
    if raw[0] in "[{":
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and "manga" in data: data = data["manga"]
            if isinstance(data, str): return [normalize_name(data)] if data else []
            if isinstance(data, list): return [normalize_name(x) for x in data if str(x).strip()]
        except Exception:
            pass
    parts = [p for p in raw.split(",") if p.strip()]
    return [normalize_name(p) for p in parts]

# Scan IDs envoyés par le webhook (ex: OP1164, LDS91, ou URL ?scan=OP1164)
def parse_trigger_scans():
    raw = os.getenv("TRIGGER_SCAN", "").strip()
    if not raw: return []
    # Si on reçoit une URL complète
    if "?scan=" in raw:
        raw = raw.split("?scan=")[-1]
    # Support JSON simple
    if raw and raw[0] in "[{":
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and "scan" in data: data = data["scan"]
            if isinstance(data, str): return [data.strip()] if data.strip() else []
            if isinstance(data, list): return [str(x).strip() for x in data if str(x).strip()]
        except Exception:
            pass
    parts = [p for p in raw.split(",") if p.strip()]
    return [p.strip() for p in parts]

SCAN_PREFIX = {
    "One Piece": "OP",
    "L'Atelier des Sorciers": "LDS",
}

# Prochain chapitre basé sur l'existant (entiers uniquement)
def next_chapter_number(existing):
    nums = []
    for c in existing:
        s = str(c)
        if "cover" in s.lower():
            continue
        try:
            nums.append(int(float(s)))
        except Exception:
            continue
    return (max(nums) if nums else 0) + 1

# Fonction de tri robuste (déjà vue)
def sort_key(chap_str):
    if "cover" in str(chap_str).lower(): return 999999.0
    try: return float(chap_str)
    except: return 0.0

def main():
    logger.info("🤖 --- DÉMARRAGE BOT (Smart Filter) ---")
    config = load_config()
    tracked_names = load_mangas()
    trigger = parse_trigger_mangas()
    trigger_scans = parse_trigger_scans()
    if trigger:
        name_map = {normalize_name(n): n for n in tracked_names}
        wanted = [name_map[t] for t in trigger if t in name_map]
        if not wanted:
            logger.info("⚠️ Aucun manga correspondant au trigger; arrêt.")
            return
        logger.info(f"🔔 Trigger: {', '.join(wanted)}")
        tracked_names = wanted
    bot_scraper = scraper.MangaScraper(config)
    BUCKET = storage.creds['bucket_name']
    MAX_CHAPS = config.get('max_chapters', 0)

    # 1. Gestion des Covers
    logger.info("🖼️ Vérification des covers...")
    covers_url_map = {}
    for m in tracked_names:
        url = storage.upload_cover(m)
        if url: covers_url_map[m] = url
        else: covers_url_map[m] = f"https://{B2_CLUSTER}.backblazeb2.com/file/{BUCKET}/mangas/{m}/cover.jpg"

    # 2. ANALYSE DE L'ÉTAT ACTUEL (Le point crucial)
    # On regarde ce qu'on a DÉJÀ sur B2 pour définir ce qu'on refuse de télécharger
    logger.info("📊 Analyse de l'existant sur Backblaze...")
    manga_state = {}

    for m_name in tracked_names:
        existing = storage.list_chapters_on_b2(m_name)
        existing.sort(key=sort_key) # Tri du plus vieux au plus récent (ex: 10, 11, 12)
        
        cutoff_value = -1.0
        
        # Si on a déjà atteint ou dépassé la limite (ex: 10 chapitres)
        if MAX_CHAPS > 0 and len(existing) >= MAX_CHAPS:
            # On définit la limite : tout ce qui est plus vieux que le 10ème en partant de la fin est IGNORÉ.
            # ex: on a [1, 2... 40, 41, ... 50]. On garde les 10 derniers (41-50).
            # Le cutoff devient 41. Tout ce qui est < 41 sur le site sera ignoré.
            keep_list = existing[-MAX_CHAPS:] # Les 10 derniers
            cutoff_value = sort_key(keep_list[0]) # Le plus petit des "gardés"
            
        manga_state[m_name] = {
            "existing_chapters": set(existing),
            "cutoff": cutoff_value
        }
        if cutoff_value > 0:
            logger.info(f"   🛡️ {m_name} : Filtre activé. On ignore tout ce qui est < Chapitre {cutoff_value}")

    # 3. Scan Site Source (ou mode direct via scan ID)
    found_chapters = []
    if trigger_scans:
        logger.info("📡 Mode direct via scan ID...")
        # Associe scans ↔ mangas (si un seul manga, on lui applique tous les scans)
        if len(tracked_names) == 1:
            for scan_id in trigger_scans:
                c_num = "".join([c for c in scan_id if c.isdigit()]) or "0"
                found_chapters.append({
                    "manga_name": tracked_names[0],
                    "author": "Inconnu",
                    "scan_id": scan_id,
                    "chapter_num": c_num,
                    "chapter_title": ""
                })
        else:
            for idx, scan_id in enumerate(trigger_scans):
                manga_name = tracked_names[min(idx, len(tracked_names)-1)]
                c_num = "".join([c for c in scan_id if c.isdigit()]) or "0"
                found_chapters.append({
                    "manga_name": manga_name,
                    "author": "Inconnu",
                    "scan_id": scan_id,
                    "chapter_num": c_num,
                    "chapter_title": ""
                })
    else:
        logger.info("📡 Ping du prochain chapitre...")
        for m_name in tracked_names:
            prefix = SCAN_PREFIX.get(m_name)
            if not prefix:
                logger.warning(f"⚠️ Prefix scan manquant pour {m_name}")
                continue
            current_state = manga_state.get(m_name, {})
            existing = current_state.get("existing_chapters", [])
            next_num = next_chapter_number(existing)
            scan_id = f"{prefix}{next_num}"
            if bot_scraper.scan_exists(scan_id):
                found_chapters.append({
                    "manga_name": m_name,
                    "author": "Inconnu",
                    "scan_id": scan_id,
                    "chapter_num": str(next_num),
                    "chapter_title": ""
                })
            else:
                logger.info(f"⏭️ {m_name} {next_num} pas dispo")

    # 4. Base de données pour le JSON final
    db_store = {}
    for m in tracked_names:
        db_store[m] = { "title": m, "author": "Inconnu", "cover": covers_url_map.get(m, ""), "chapters": [] }

    # 5. TRAITEMENT INTELLIGENT
    for chap in found_chapters:
        m_name = chap['manga_name']
        c_num = chap['chapter_num']
        c_val = sort_key(c_num)
        
        if chap['author'] != "Inconnu": db_store[m_name]['author'] = chap['author']

        # --- LE FILTRE ANTI-BOUCLE EST ICI ---
        current_state = manga_state.get(m_name)
        
        # 1. Si le chapitre est trop vieux (inférieur au cutoff), ON ZAPPE IMMÉDIATEMENT
        # Cela économise les transactions "Class C" car on ne vérifie même pas les fichiers
        if current_state and current_state['cutoff'] > 0:
            if c_val < current_state['cutoff']:
                # logger.info(f"   ⛔ Trop vieux : {m_name} {c_num} (Ignoré)") # Décommenter pour debug
                continue

        # 2. Si le chapitre est valide, on vérifie s'il faut le télécharger
        existing_files = storage.list_files_in_chapter(m_name, c_num)
        
        if len(existing_files) < 1:
            logger.info(f"🔎 Traitement : {m_name} {c_num}")
            for filename, content in bot_scraper.download_images_generator(chap['scan_id']):
                if filename not in existing_files:
                    storage.upload_image(m_name, c_num, filename, content)
                    logger.info(f"      ☁️ UP : {filename}")
        
        # On met à jour l'état local pour le nettoyage final
        if current_state:
            current_state['existing_chapters'].add(str(c_num))

    # 6. NETTOYAGE FINAL (Retention Policy)
    logger.info("🧹 Nettoyage final...")
    for m_name in tracked_names:
        # On reliste ce qu'on a maintenant (avec les nouveaux ajouts potentiels)
        # Note: On utilise notre set en mémoire pour éviter un appel API B2 coûteux
        all_chaps = list(manga_state[m_name]['existing_chapters'])
        all_chaps.sort(key=sort_key)

        # Logique de suppression
        if MAX_CHAPS > 0 and len(all_chaps) > MAX_CHAPS:
            nb_to_delete = len(all_chaps) - MAX_CHAPS
            chaps_to_kill = all_chaps[:nb_to_delete] # Les plus vieux
            chaps_to_keep = all_chaps[nb_to_delete:] # Les récents
            
            for old_chap in chaps_to_kill:
                # Sécurité cover
                if "cover" in str(old_chap).lower(): continue
                
                storage.delete_chapter_folder(m_name, old_chap)
        else:
            chaps_to_keep = all_chaps

        # Construction JSON
        for c_num in chaps_to_keep:
            if "cover" in str(c_num).lower(): continue
            
            # Pour économiser les transactions, on n'appelle list_files que pour compter les pages
            # Astuce : Si on vient de le scanner, on pourrait stocker le compte, 
            # mais ici on fait un appel "Class C" léger pour être juste.
            files = storage.list_files_in_chapter(m_name, c_num)
            
            folder_url = f"https://{B2_CLUSTER}.backblazeb2.com/file/{BUCKET}/mangas/{m_name}/{c_num}/"
            db_store[m_name]['chapters'].append({
                "number": c_num,
                "title": f"Chapitre {c_num}",
                "folder_url": folder_url,
                "pages_count": len(files)
            })

    # 7. GÉNÉRATION JSON
    if not os.path.exists('api/details'): os.makedirs('api/details')

    api_list = []
    for m_name, data in db_store.items():
        slug = m_name.lower().replace(' ', '-')
        api_list.append({ "id": slug, "title": m_name, "cover": data['cover'] })
        
        # Tri décroissant (récent en haut)
        data['chapters'].sort(key=lambda x: sort_key(x['number']), reverse=True)
        
        with open(f'api/details/{slug}.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    with open('api/mangas.json', 'w', encoding='utf-8') as f:
        json.dump(api_list, f, indent=2, ensure_ascii=False)

    logger.info("✅ Terminé avec succès ! ")

if __name__ == "__main__":
    main()
    
