import cloudscraper
from bs4 import BeautifulSoup
import time
import random
import logging
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

class MangaScraper:
    def __init__(self, config):
        self.base_url = config['source_url']
        # Configuration Anti-Bot robuste
        self.scraper = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
        )
        self.headers = {
            'User-Agent': config['user_agent'],
            'Referer': self.base_url
        }

    def _sleep(self):
        time.sleep(random.uniform(0.5, 1.5))

    def get_latest_chapters_from_feed(self, pages_to_scan, tracked_mangas_names):
        found_chapters = []
        
        for page_num in range(1, pages_to_scan + 1):
            url = f"{self.base_url}/?p={page_num}"
            logger.info(f"🔍 Scan page : {url}")
            
            try:
                response = self.scraper.get(url, headers=self.headers)
                if response.status_code != 200: break
                
                soup = BeautifulSoup(response.text, 'html.parser')
                links = soup.select('a[href^="?scan="]')
                
                if not links: break

                for link in links:
                    try:
                        # 1. Extraction MÉTADONNÉES
                        fig_p = link.select_one('figure figcaption p')
                        if not fig_p: continue
                        
                        # Auteur
                        author_tag = fig_p.find('span')
                        author = author_tag.text.strip() if author_tag else "Inconnu"
                        
                        # Nom Manga
                        if author_tag: author_tag.extract()
                        manga_name = fig_p.text.strip()
                        
                        # Filtrage
                        if manga_name not in tracked_mangas_names: continue

                        # Infos Chapitre
                        scan_id = link['href'].split('=')[-1]
                        footer = link.select_one('.sortiefooter')
                        num_tag = footer.find('h3')
                        title_tag = footer.find('p')
                        
                        chapter_num = num_tag.text.replace('#', '').strip()
                        chapter_title = title_tag.text.strip() if title_tag else ""
                        
                        found_chapters.append({
                            'manga_name': manga_name,
                            'author': author,
                            'scan_id': scan_id,
                            'chapter_num': chapter_num,
                            'chapter_title': chapter_title
                        })
                        
                    except Exception as e:
                        logger.error(f"❌ Erreur parsing item : {e}")
                        continue
            except Exception as e:
                logger.error(f"❌ Erreur connexion : {e}")
                break
            
            self._sleep()
            
        return found_chapters

    def download_images_generator(self, scan_id):
        """ 
        Générateur qui envoie les images une par une dès qu'elles sont téléchargées.
        Permet le traitement en temps réel.
        """
        logger.info(f"📥 Démarrage analyse pour ID: {scan_id}")
        page_num = 1
        base_img_url = f"{self.base_url}/files/scans/{scan_id}/"

        while True:
            filename = f"{page_num:02d}.png"
            img_url = urljoin(base_img_url, filename)
            
            try:
                # Timeout court pour être réactif
                r = self.scraper.get(img_url, headers=self.headers, timeout=10)
                
                if r.status_code == 200:
                    # ✅ SUCCÈS : On envoie l'image tout de suite au bot
                    yield (filename, r.content)
                    
                    page_num += 1
                else:
                    # Fallback 1.png si c'est la page 1
                    if page_num == 1:
                        alt_filename = "1.png"
                        r_alt = self.scraper.get(urljoin(base_img_url, alt_filename), headers=self.headers)
                        if r_alt.status_code == 200:
                            yield ("01.png", r_alt.content)
                            page_num += 1
                            continue
                    
                    # 404 = Fin du chapitre
                    break 
            except Exception as e:
                logger.warning(f"      ⚠️ Erreur téléchargement page {page_num} : {e}")
                break
            
            if page_num > 200: break # Sécurité