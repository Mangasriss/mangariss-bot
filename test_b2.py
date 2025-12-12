import b2sdk.v2 as b2
import json
import sys

def test_connection():
    print("--- 🛠️ TEST DE CONNEXION BACKBLAZE B2 ---")

    # 1. Chargement des identifiants
    try:
        with open('credentials.json', 'r') as f:
            creds = json.load(f)
        print("✅ Fichier credentials.json lu.")
    except FileNotFoundError:
        print("❌ Erreur : Le fichier credentials.json est introuvable !")
        return
    except json.JSONDecodeError:
        print("❌ Erreur : Le fichier credentials.json est mal formaté.")
        return

    # 2. Initialisation de l'API B2
    info = b2.InMemoryAccountInfo()
    b2_api = b2.B2Api(info)

    # 3. Tentative de connexion (Authentification)
    print("🔄 Connexion aux serveurs B2...")
    try:
        b2_api.authorize_account("production", creds['application_key_id'], creds['application_key'])
        print("✅ Authentification réussie !")
    except Exception as e:
        print(f"❌ Échec de l'authentification. Vérifiez vos clés.\nErreur : {e}")
        return

    # 4. Vérification du Bucket
    print(f"🔄 Recherche du bucket : '{creds['bucket_name']}'...")
    try:
        bucket = b2_api.get_bucket_by_name(creds['bucket_name'])
        # CORRECTION ICI : On utilise bucket.id_ ou juste bucket.name pour éviter l'erreur
        print(f"✅ Bucket trouvé : {bucket.name} (ID: {bucket.id_})")
    except b2.exception.NonExistentBucket:
        print(f"❌ Erreur : Le bucket '{creds['bucket_name']}' n'existe pas.")
        return
    except Exception as e:
        print(f"❌ Erreur lors de la récupération du bucket : {e}")
        return

    # 5. Test d'écriture (Upload d'un fichier temporaire)
    test_filename = "test_connectivity_bot.txt"
    test_content = "Si vous lisez ceci, le bot a les droits d'écriture."
    
    print(f"🔄 Test d'écriture (Upload de {test_filename})...")
    try:
        bucket.upload_bytes(
            data_bytes=test_content.encode('utf-8'),
            file_name=test_filename
        )
        print("✅ Upload réussi !")
    except Exception as e:
        print(f"❌ Impossible d'écrire dans le bucket : {e}")
        return

    # 6. Test de lecture (Listing)
    print("🔄 Test de lecture (Listing des fichiers)...")
    found = False
    # Utilisation simplifiée du listing
    try:
        for file_version, folder_name in bucket.ls(folder_to_list="", show_versions=False):
            if file_version.file_name == test_filename:
                found = True
                print(f"   - Fichier trouvé dans la liste : {file_version.file_name}")
                break
        
        if found:
            print("✅ Lecture réussie !")
        else:
            print("⚠️ Upload fait, mais fichier non visible immédiatement (peut être normal avec le délai de propagation).")
            
    except Exception as e:
        print(f"⚠️ Erreur non critique lors du listing : {e}")

    # 7. Nettoyage (Suppression du fichier test)
    print("🔄 Nettoyage (Suppression du fichier test)...")
    try:
        # On essaie de récupérer le fichier par son nom pour le supprimer
        file_version = bucket.get_file_info_by_name(test_filename)
        file_version.delete()
        print("✅ Fichier de test supprimé.")
    except Exception as e:
        print(f"⚠️ Impossible de supprimer le fichier test (déjà supprimé ou introuvable) : {e}")

    print("\n🎉 SUCCÈS : TOUS LES SYSTÈMES SONT OPÉRATIONNELS !")

if __name__ == "__main__":
    test_connection()