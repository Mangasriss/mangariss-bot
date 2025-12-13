import b2sdk.v2 as b2
import json
import os

def get_credentials():
    # Supporte Local ET GitHub Actions
    if os.path.exists('credentials.json'):
        with open('credentials.json') as f: return json.load(f)
    return {
        "application_key_id": os.environ.get("B2_KEY_ID"),
        "application_key": os.environ.get("B2_APP_KEY"),
        "bucket_name": os.environ.get("B2_BUCKET")
    }

creds = get_credentials()
info = b2.InMemoryAccountInfo()
b2_api = b2.B2Api(info)

try:
    b2_api.authorize_account("production", creds['application_key_id'], creds['application_key'])
    bucket = b2_api.get_bucket_by_name(creds['bucket_name'])
except Exception as e:
    print(f"ERREUR B2 CRITIQUE: {e}")
    exit(1)

def upload_image(manga_name, chapter_num, filename, image_bytes):
    """ Upload : mangas/One Piece/1147/01.png """
    b2_path = f"mangas/{manga_name}/{chapter_num}/{filename}"
    try:
        bucket.upload_bytes(data_bytes=image_bytes, file_name=b2_path)
        return b2_path
    except:
        return None

def upload_cover(manga_name):
    """ Cherche une image dans le dossier local 'covers/' et l'upload """
    for ext in ['.jpg', '.png', '.jpeg']:
        local_path = f"covers/{manga_name}{ext}"
        if os.path.exists(local_path):
            print(f"🖼️ Cover trouvée pour {manga_name}")
            b2_path = f"mangas/{manga_name}/cover.jpg"
            bucket.upload_local_file(local_file=local_path, file_name=b2_path)
            # URL Publique (A adapter selon ton cluster f002/f004)
            return f"https://f003.backblazeb2.com/file/{creds['bucket_name']}/{b2_path}"
    return None

def list_chapters_on_b2(manga_name):
    """ Liste quels numéros de chapitres existent déjà (Ignore cover.jpg) """
    prefix = f"mangas/{manga_name}/"
    chapters = set()
    # Listing récursif
    for file_version, _ in bucket.ls(folder_to_list=prefix, recursive=True):
        # file_name = mangas/One Piece/1147/01.png
        parts = file_version.file_name.split('/')
        if len(parts) >= 3:
            chap_name = parts[2]
            # 🛑 SÉCURITÉ : On ignore le fichier cover.jpg pour ne pas le supprimer
            if chap_name == "cover.jpg":
                continue
            chapters.add(chap_name)
    return list(chapters)

def list_files_in_chapter(manga_name, chapter_num):
    """ 
    Retourne la liste des fichiers (ex: ['01.png', '02.png']) présents sur B2 
    pour un chapitre donné.
    """
    prefix = f"mangas/{manga_name}/{chapter_num}/"
    files = set()
    # On liste tout ce qui est dans ce dossier précis
    try:
        for file_version, _ in bucket.ls(folder_to_list=prefix):
            # file_name complet : mangas/One Piece/1147/01.png
            # On veut juste : 01.png
            clean_name = file_version.file_name.split('/')[-1]
            files.add(clean_name)
    except Exception:
        # Si le dossier n'existe pas encore, c'est pas grave
        pass
    return files

def delete_chapter_folder(manga_name, chapter_num):
    """ Supprime tous les fichiers d'un chapitre spécifique sur B2 """
    prefix = f"mangas/{manga_name}/{chapter_num}/"
    print(f"🗑️ NETTOYAGE : Suppression du vieux chapitre {manga_name} {chapter_num}...")
    
    # CORRECTION : On utilise latest_only=False au lieu de show_versions=True
    for file_version, _ in bucket.ls(folder_to_list=prefix, recursive=True, latest_only=False):
        try:
            bucket.delete_file_version(file_version.id_, file_version.file_name)
        except Exception as e:
            print(f"   ⚠️ Erreur suppression fichier {file_version.file_name}: {e}")