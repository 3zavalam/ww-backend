import os
from supabase import create_client, Client
from dotenv import load_dotenv

# Carga las variables de entorno desde un archivo .env
load_dotenv()

# Obtiene la URL y la clave de Supabase desde las variables de entorno
url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")

# Valida que las variables de entorno estén presentes
if not url or not key:
    raise EnvironmentError("Las variables de entorno SUPABASE_URL y SUPABASE_KEY son obligatorias.")

# Crea una única instancia del cliente de Supabase para ser usada en toda la aplicación
supabase: Client = create_client(url, key) 