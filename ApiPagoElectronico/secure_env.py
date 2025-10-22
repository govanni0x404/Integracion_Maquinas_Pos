import os
import base64
import hashlib
import platform
from pathlib import Path
from cryptography.fernet import Fernet

# Verificar si cryptography está disponible
try:
    from cryptography.fernet import Fernet
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


class SecureEnvHandler:
    """Maneja archivos .env cifrados"""
    
    def __init__(self, key_source="machine"):
        """
        key_source puede ser:
        - "machine": Genera clave basada en ID único de la máquina
        - "hardcoded": Usa una clave embebida en el código (menos seguro pero portátil)
        - Una clave específica (bytes)
        """
        if not CRYPTO_AVAILABLE:
            raise RuntimeError("cryptography no instalado: pip install cryptography")
        
        if key_source == "machine":
            self.key = self._generate_machine_key()
        elif key_source == "hardcoded":
            # Clave hardcodeada (cámbiala por una única tuya)
            # Genera una con: Fernet.generate_key()
            self.key = b'TU_CLAVE_SECRETA_AQUI_44_CARACTERES_BASE64='
        else:
            self.key = key_source
        
        self.fernet = Fernet(self.key)
    
    def _generate_machine_key(self):
        """
        Genera una clave única basada en identificadores de la máquina.
        Ventaja: Cada máquina tiene su propia clave.
        Desventaja: Si cambias de hardware, pierdes acceso.
        """
        # Combina varios identificadores de la máquina
        machine_id = f"{platform.node()}-{platform.machine()}-{platform.processor()}"
        
        # Genera hash SHA256
        hash_obj = hashlib.sha256(machine_id.encode())
        
        # Convierte a clave Fernet (32 bytes base64)
        key = base64.urlsafe_b64encode(hash_obj.digest())
        return key
    
    def encrypt_env_file(self, input_file=".env", output_file=".env.encrypted"):
        """Cifra un archivo .env"""
        try:
            with open(input_file, "rb") as f:
                data = f.read()
            
            encrypted = self.fernet.encrypt(data)
            
            with open(output_file, "wb") as f:
                f.write(encrypted)
            
            print(f"✅ Archivo cifrado guardado en: {output_file}")
            print(f"⚠️  Elimina el archivo original: {input_file}")
            return True
            
        except Exception as e:
            print(f"❌ Error cifrando: {e}")
            return False
    
    def decrypt_env_file(self, encrypted_file=".env.encrypted", output_file=".env"):
        """Descifra un archivo .env.encrypted"""
        try:
            with open(encrypted_file, "rb") as f:
                encrypted_data = f.read()
            
            decrypted = self.fernet.decrypt(encrypted_data)
            
            with open(output_file, "wb") as f:
                f.write(decrypted)
            
            print(f"✅ Archivo descifrado: {output_file}")
            return True
            
        except Exception as e:
            print(f"❌ Error descifrando: {e}")
            return False
    
    def load_encrypted_env(self, encrypted_file=".env.encrypted"):
        """
        Carga variables de entorno desde archivo cifrado SIN crear archivo temporal.
        RECOMENDADO: Más seguro porque no deja rastro en disco.
        """
        try:
            with open(encrypted_file, "rb") as f:
                encrypted_data = f.read()
            
            decrypted = self.fernet.decrypt(encrypted_data)
            
            # Parsea el contenido y carga en os.environ
            for line in decrypted.decode("utf-8").splitlines():
                line = line.strip()
                
                # Ignora comentarios y líneas vacías
                if not line or line.startswith("#"):
                    continue
                
                # Parsea KEY=VALUE
                if "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip()
            
            print(f"✅ Variables cargadas desde {encrypted_file}")
            return True
            
        except Exception as e:
            print(f"❌ Error cargando env cifrado: {e}")
            return False

def load_secure_env():
    """
    Carga configuración desde .env.encrypted (funciona en .exe y .py)
    """
    import sys
    
    # Detecta si estamos en un .exe empaquetado
    if getattr(sys, 'frozen', False):
        # Estamos en un .exe de PyInstaller
        # sys._MEIPASS es la carpeta temporal donde PyInstaller descomprime archivos
        base_path = Path(sys._MEIPASS)
        print(f"🔧 Ejecutando desde .exe (carpeta temporal: {base_path})")
    else:
        # Estamos ejecutando el script .py normal
        base_path = Path(__file__).parent
        print(f"🔧 Ejecutando desde script .py (carpeta: {base_path})")
    
    # Busca .env.encrypted
    encrypted_path = base_path / ".env.encrypted"
    
    print(f"🔍 Buscando: {encrypted_path}")
    print(f"   ¿Existe? {encrypted_path.exists()}")
    
    if encrypted_path.exists():
        if not CRYPTO_AVAILABLE:
            print("❌ cryptography no instalado, no se puede usar .env.encrypted")
            return False
        
        handler = SecureEnvHandler(key_source="machine")
        if handler.load_encrypted_env(str(encrypted_path)):
            print("✅ Configuración cargada exitosamente")
            # Verifica que se cargaron las credenciales
            if os.environ.get("API_AUTH_USER") and os.environ.get("API_AUTH_PASS"):
                print(f"   Usuario: {os.environ.get('API_AUTH_USER')}")
                return True
            else:
                print("⚠️  Variables no se cargaron correctamente")
                return False
        else:
            print("❌ Error al cargar .env.encrypted")
            return False
    
    # Fallback: intenta .env sin cifrar
    env_path = base_path / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(dotenv_path=str(env_path))
            print("⚠️  Cargando .env SIN CIFRAR (modo desarrollo)")
            return True
        except ImportError:
            print("❌ python-dotenv no instalado")
            return False
    
    print("❌ No se encontró .env ni .env.encrypted")
    return False

def edit_encrypted_env():
    """Descifra .env.encrypted, permite editarlo y vuelve a cifrar"""
    if not CRYPTO_AVAILABLE:
        print("❌ Necesitas: pip install cryptography")
        return
    
    if not Path(".env.encrypted").exists():
        print("❌ No existe .env.encrypted")
        return
    
    handler = SecureEnvHandler(key_source="machine")
    
    # 1. Descifra temporalmente
    print("Descifrando .env.encrypted...")
    if not handler.decrypt_env_file():
        print("❌ No se pudo descifrar")
        return
    
    print("✅ Archivo .env creado temporalmente")
    print("\n1. Edita el archivo .env con un editor de texto")
    print("2. Guarda los cambios")
    print("3. Presiona Enter cuando termines...")
    
    input()
    
    # 2. Vuelve a cifrar
    print("\nCifrando de nuevo...")
    if handler.encrypt_env_file():
        # 3. Elimina el .env temporal
        try:
            Path(".env").unlink()
            print("✅ .env.encrypted actualizado")
            print("✅ Archivo .env temporal eliminado")
        except Exception as e:
            print(f"⚠️  No se pudo eliminar .env temporal: {e}")
    else:
        print("❌ Error al cifrar")


# HERRAMIENTA DE LÍNEA DE COMANDOS
def main():
    """Utilidad para cifrar/descifrar archivos .env"""
    import sys
    
    if len(sys.argv) < 2:
        print("Uso:")
        print("  python secure_env.py encrypt   # Cifra .env -> .env.encrypted")
        print("  python secure_env.py decrypt   # Descifra .env.encrypted -> .env")
        print("  python secure_env.py test      # Prueba carga de .env.encrypted")
        print("  python secure_env.py edit      # Edita .env.encrypted")
        return
    
    if not CRYPTO_AVAILABLE:
        print("❌ cryptography no instalado: pip install cryptography")
        return
    
    handler = SecureEnvHandler(key_source="machine")
    comando = sys.argv[1]
    
    if comando == "encrypt":
        if handler.encrypt_env_file():
            print("\n⚠️  IMPORTANTE:")
            print("1. Elimina el archivo .env original")
            print("2. Usa solo .env.encrypted en producción")
    
    elif comando == "decrypt":
        handler.decrypt_env_file()
        print("\n⚠️  RECUERDA: Elimina el .env después de verificar")
    
    elif comando == "test":
        if handler.load_encrypted_env():
            print("\nVariables cargadas:")
            print(f"  API_AUTH_USER = {os.environ.get('API_AUTH_USER', 'NO ENCONTRADO')}")
            print(f"  API_AUTH_PASS = {'*' * 20}")
    
    elif comando == "edit":
        edit_encrypted_env()
    
    else:
        print(f"❌ Comando desconocido: {comando}")


if __name__ == "__main__":
    main()

# Para encriptar: python secure_env.py encrypt
# Para editar: python secure_env.py edit
# Para descifrar: python secure_env.py decrypt