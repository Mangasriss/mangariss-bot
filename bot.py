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

# Fonction de tri robuste (déjà vue)
def sort_key(chap_str):
    if "cover" in str(chap_str).lower(): return 999999.0
    try: return float(chap_str)
    except: return 0.0

def main():
    logger.info("🤖 --- DÉMARRAGE BOT (Smart Filter) ---")
    config = load_config()
    tracked_names = load_mangas()
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

    # 3. Scan Site Source
    logger.info("📡 Scan des nouveautés...")
    found_chapters = bot_scraper.get_latest_chapters_from_feed(config['pages_to_scan'], tracked_names)

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

    logger.info("✅ Terminé avec succès !")

if __name__ == "__main__":
    main()

