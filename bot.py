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
    logger.info("🤖 --- DÉMARRAGE BOT API ---")
    config = load_config()
    tracked_names = load_mangas()
    bot_scraper = scraper.MangaScraper(config)
    BUCKET = storage.creds['bucket_name']

    # 1. Gestion des Covers
    logger.info("🖼️ Vérification des covers...")
    covers_url_map = {}
    for m in tracked_names:
        url = storage.upload_cover(m)
        # Si pas de cover uploadée, on génère l'URL supposée
        if url: 
            covers_url_map[m] = url
        else:
            # URL si la cover existe déjà sur le cloud
            covers_url_map[m] = f"https://{B2_CLUSTER}.backblazeb2.com/file/{BUCKET}/mangas/{m}/cover.jpg"

    # 2. Scan Site Source
    logger.info("📡 Scan des nouveautés...")
    found_chapters = bot_scraper.get_latest_chapters_from_feed(config['pages_to_scan'], tracked_names)

    # 3. Base de données en mémoire
    # On va construire la structure de notre API
    db_store = {} 

    # On initialise la DB avec les mangas trackés
    for m in tracked_names:
        db_store[m] = {
            "title": m,
            "author": "Inconnu", # Sera mis à jour si trouvé dans le feed
            "cover": covers_url_map.get(m, ""),
            "chapters": []
        }

    # 4. Traitement des chapitres
    for chap in found_chapters:
        m_name = chap['manga_name']
        c_num = chap['chapter_num']
        
        # Mise à jour auteur
        if chap['author'] != "Inconnu":
            db_store[m_name]['author'] = chap['author']

        logger.info(f"🔎 Analyse : {m_name} {c_num}")

        # 1. On récupère la liste des fichiers DÉJÀ sur B2 pour ce chapitre
        # Cela nous permet de savoir si le chapitre est complet ou partiel
        existing_files = storage.list_files_in_chapter(m_name, c_num)
        
        # URL du dossier B2 (pour le JSON plus tard)
        folder_url = f"https://{B2_CLUSTER}.backblazeb2.com/file/{BUCKET}/mangas/{m_name}/{c_num}/"
        
        # Compteur de pages (On part de ce qu'on a déjà)
        total_pages_count = len(existing_files)

        # 2. On lance le scraper en mode "Flux Tendu"
        # Le scraper va tester 01.png, 02.png...
        for filename, content in bot_scraper.download_images_generator(chap['scan_id']):
            
            # VÉRIFICATION : Est-ce qu'on a déjà cette image ?
            if filename in existing_files:
                # Si oui, on ne l'upload pas, mais on logue pour rassurer (optionnel, on peut commenter pour alléger)
                # logger.info(f"      ⏩ Ignoré (Déjà présent) : {filename}")
                pass
            else:
                # Si non, on l'upload IMMÉDIATEMENT
                size_ko = len(content) / 1024
                storage.upload_image(m_name, c_num, filename, content)
                logger.info(f"      ☁️ UPLOADÉ : {filename} ({size_ko:.1f} Ko)")
                
                # On l'ajoute à notre liste locale pour que le compte soit bon
                existing_files.add(filename)

        # À la fin de la boucle, le chapitre est forcément complet (ou au max possible)
        total_pages_count = len(existing_files)
        logger.info(f"   ✅ Chapitre traité ({total_pages_count} pages au total)")

        # Ajout au JSON
        db_store[m_name]['chapters'].append({
            "number": c_num,
            "title": chap['chapter_title'],
            "folder_url": folder_url,
            "pages_count": total_pages_count
        })

    # 5. GÉNÉRATION DES FICHIERS API (JSON)
    if not os.path.exists('api/details'): os.makedirs('api/details')

    # A. mangas.json (Liste globale)
    api_list = []
    for m_name, data in db_store.items():
        slug = m_name.lower().replace(' ', '-')
        api_list.append({
            "id": slug,
            "title": m_name,
            "cover": data['cover']
        })
    
    with open('api/mangas.json', 'w', encoding='utf-8') as f:
        json.dump(api_list, f, indent=2, ensure_ascii=False)

    # B. details/{slug}.json (Détail par manga)
    for m_name, data in db_store.items():
        slug = m_name.lower().replace(' ', '-')
        with open(f'api/details/{slug}.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info("✅ API JSON générée avec succès !")

if __name__ == "__main__":
    main()