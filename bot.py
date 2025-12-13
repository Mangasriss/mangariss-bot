import logging
import json
import os
import sys  # <--- Ajoute ça
import scraper
import storage

# --- CONFIG LOGS ---
if not os.path.exists('logs'): os.makedirs('logs')

# Configuration spéciale pour Windows (Support des emojis)
# On redirige la sortie standard vers l'UTF-8
sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/bot.log", encoding='utf-8'), # UTF-8 pour le fichier
        logging.StreamHandler(sys.stdout) # UTF-8 forcé pour l'écran
    ]
)
logger = logging.getLogger()

# --- CONFIG B2 CLUSTER ---
# ⚠️ METS ICI TON CLUSTER B2 EXACT (f002, f004, s001...)
B2_CLUSTER = "f003" 

def load_config():
    with open('config.json', 'r') as f: return json.load(f)
def load_mangas():
    if not os.path.exists('mangas.txt'): return []
    with open('mangas.txt', 'r') as f: return [line.strip() for line in f if line.strip()]

def main():
    logger.info("🤖 --- DÉMARRAGE BOT (Mode Retention) ---")
    config = load_config()
    tracked_names = load_mangas()
    bot_scraper = scraper.MangaScraper(config)
    BUCKET = storage.creds['bucket_name']
    MAX_CHAPS = config.get('max_chapters', 0) # 0 = Illimité

    # 1. Gestion des Covers (Inchangé)
    # ... (Garde ton code de covers ici) ...
    # (Je résume pour pas que le message soit trop long, ne change rien au début)
    covers_url_map = {}
    for m in tracked_names:
        url = storage.upload_cover(m)
        if url: covers_url_map[m] = url
        else: covers_url_map[m] = f"https://{B2_CLUSTER}.backblazeb2.com/file/{BUCKET}/mangas/{m}/cover.jpg"

    # 2. Scan Site Source
    logger.info("📡 Scan des nouveautés...")
    found_chapters = bot_scraper.get_latest_chapters_from_feed(config['pages_to_scan'], tracked_names)

    # 3. Base de données
    db_store = {}
    for m in tracked_names:
        db_store[m] = {
            "title": m,
            "author": "Inconnu",
            "cover": covers_url_map.get(m, ""),
            "chapters": []
        }

    # 4. TÉLÉCHARGEMENT ET UPLOAD (D'abord on ajoute tout le nouveau)
    for chap in found_chapters:
        m_name = chap['manga_name']
        c_num = chap['chapter_num']
        
        if chap['author'] != "Inconnu": db_store[m_name]['author'] = chap['author']

        # Vérif et Upload (Code existant)
        existing_files = storage.list_files_in_chapter(m_name, c_num)
        
        # On lance le téléchargement seulement si on ne l'a pas déjà fait
        # (Ou si on veut compléter)
        if len(existing_files) < 1: # On suppose qu'il est vide ou absent
             logger.info(f"🔎 Traitement : {m_name} {c_num}")
             for filename, content in bot_scraper.download_images_generator(chap['scan_id']):
                if filename not in existing_files:
                    storage.upload_image(m_name, c_num, filename, content)
                    logger.info(f"      ☁️ UP : {filename}")

    # 5. NETTOYAGE ET CONSTRUCTION DU JSON (C'est ici que ça change)
    logger.info("🧹 Vérification des quotas...")

    for m_name in tracked_names:
        # A. On récupère TOUT ce qui est sur B2 pour ce manga
        b2_chapters = storage.list_chapters_on_b2(m_name)
        
        # B. Fonction de tri malin
        def sort_key(chap_str):
            # Sécurité absolue : si c'est cover.jpg, on le met à l'infini pour ne jamais le supprimer
            if "cover" in str(chap_str): return 999999.0 
            try: return float(chap_str)
            except: return 0.0
            
        b2_chapters.sort(key=sort_key) # Tri croissant (1, 2, ..., 1168)

        # C. LOGIQUE DE SUPPRESSION
        if MAX_CHAPS > 0 and len(b2_chapters) > MAX_CHAPS:
            # On calcule combien il faut en tuer
            nb_to_delete = len(b2_chapters) - MAX_CHAPS
            # On prend les 'nb' premiers (les plus vieux)
            chaps_to_kill = b2_chapters[:nb_to_delete]
            # On garde les autres
            chaps_to_keep = b2_chapters[nb_to_delete:]
            
            logger.info(f"   ⚠️ {m_name} : {len(b2_chapters)} chapitres trouvés. Limite: {MAX_CHAPS}.")
            
            for old_chap in chaps_to_kill:
                storage.delete_chapter_folder(m_name, old_chap)
        else:
            chaps_to_keep = b2_chapters

        # D. Construction du JSON avec SEULEMENT ce qu'on a gardé
        for c_num in chaps_to_keep:
            # On recrée l'objet chapitre pour le JSON
            # (Note: On perd le titre exact du scan si on ne l'a pas dans le feed récent, 
            # mais pour un lecteur perso ce n'est pas grave, on met le numéro)
            
            # On doit recompter les pages pour le JSON (rapide car c'est juste un listing)
            files = storage.list_files_in_chapter(m_name, c_num)
            
            folder_url = f"https://{B2_CLUSTER}.backblazeb2.com/file/{BUCKET}/mangas/{m_name}/{c_num}/"
            
            db_store[m_name]['chapters'].append({
                "number": c_num,
                "title": f"Chapitre {c_num}", # Titre générique pour les anciens
                "folder_url": folder_url,
                "pages_count": len(files)
            })

    # 6. GÉNÉRATION DES FICHIERS JSON (Inchangé)
    if not os.path.exists('api/details'): os.makedirs('api/details')

    api_list = []
    for m_name, data in db_store.items():
        slug = m_name.lower().replace(' ', '-')
        api_list.append({ "id": slug, "title": m_name, "cover": data['cover'] })
        
        # Inverser l'ordre pour le JSON (Le plus récent en haut)
        data['chapters'].sort(key=lambda x: sort_key(x['number']), reverse=True)
        
        with open(f'api/details/{slug}.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    with open('api/mangas.json', 'w', encoding='utf-8') as f:
        json.dump(api_list, f, indent=2, ensure_ascii=False)

    logger.info("✅ API JSON et Nettoyage terminés !")

if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()